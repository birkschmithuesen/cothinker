# import torch
import sys
import json
import queue
from flask import Flask, request, jsonify, render_template, Response
from fundusapps.stt_server.args import args, config
from fundusapps.stt_server.botland_callbacks import send_message_to_botland
import logging
import os


app = Flask(__name__)
setattr(app, 'sst_server', None)
# settings.RuntimeSettings, gesetzt von __main__. None heisst: dieser Prozess
# speichert nicht (Tests, Einbindung als Bibliothek) — dann meldet jede
# Aenderung ehrlich `persisted: false`, statt zu behaupten, sie ueberlebe.
setattr(app, 'settings_store', None)


def _persist(**values):
    """Die gerade angewandte Aenderung in die Einstellungsdatei schreiben.

    Reihenfolge ist Absicht: erst validieren, dann anwenden, dann schreiben.
    Ein Schreibfehler macht die Laufzeitaenderung NICHT rueckgaengig — eine
    nicht beschreibbare Platte darf die Vorstellung nicht anhalten. Er wird
    laut geloggt und als `persisted: false` gemeldet, damit der Operator es
    neben dem Regler sieht.
    """
    store = getattr(app, 'settings_store', None)
    if store is None:
        return False, 'keine Einstellungsdatei konfiguriert'
    return store.update(**values)



@app.route('/status', methods=['GET'])
def get_status():
    return jsonify({
        'status': app.sst_server.status if app.sst_server else 'not initialized',
        'audio_logging_enabled': app.sst_server.enable_audio_logging if app.sst_server else False,

    })

@app.route('/listen', methods=['POST'])
def listen():
    data = request.get_json()
    sid = data.get('session') if data else None
    users = data.get('users') if data else None

    if sid:
        logging.info(f"Session ID: {sid}")
        if users:
            logging.info(f"Users: {users}")
        else:
            logging.warning("No users provided in /listen request. Using default user display name.")
    else:
        logging.warning("No session ID provided in /listen request. Using default callback without session.")

    def _user_name(i):
        if users:
            try:
                return users[i]
            except IndexError:
                pass
        return f"User {i}"

    # turn_id -> cumulative text already POSTed for that turn. Whisper-
    # streaming partials are GROWING prefixes ("Das ist ein" → "Das ist ein
    # Test" → …). To avoid sending the same words twice, we POST only the
    # NEW tail (the suffix beyond what was already POSTed). The botserver
    # concatenates messages in order — the result must read as one clean
    # sentence with no duplications.
    posted_so_far: dict[str, str] = {}

    def _suffix(text: str, turn_id: str) -> str:
        prev = posted_so_far.get(turn_id, "")
        if prev and text.startswith(prev):
            return text[len(prev):].lstrip()
        if prev:
            # Defensive: LocalAgreement-2 should guarantee monotonic prefix
            # growth. If we ever see a non-extending update, log and post
            # the full text so nothing is silently dropped.
            logging.warning(
                f"[partial] turn={turn_id} text is not an extension of "
                f"already-posted prefix; posting full text. "
                f"prev={prev!r} new={text!r}"
            )
        return text

    def _record_cb(i, text, turn_id):
        # Final: send only the tail beyond what partials already covered.
        suffix = _suffix(text, turn_id) if text else ""
        posted_so_far.pop(turn_id, None)  # turn done — clean up
        if not suffix:
            return
        send_message_to_botland(
            [{"role": "user", "user_display_name": _user_name(i), "content": suffix}],
            sid=sid,
            turn_id=turn_id,
        )

    def _partial_cb(i, text, turn_id, partial_seq):
        if not text:
            return
        suffix = _suffix(text, turn_id)
        if len(suffix.split()) < 3:
            return  # gate on ≥3 NEW words so we don't spam per-word commits
        posted_so_far[turn_id] = text
        send_message_to_botland(
            [{"role": "user", "user_display_name": _user_name(i), "content": suffix}],
            sid=sid,
            turn_id=turn_id,
        )

    app.sst_server.set_external_record_callback(_record_cb)
    app.sst_server.set_external_partial_callback(_partial_cb)

    return jsonify({'status': 'listening started'})

@app.route('/pause', methods=['POST'])
def pause():
    app.sst_server.pause()
    return jsonify({'status': 'listening paused'})

@app.route('/resume', methods=['POST'])
def resume():
    app.sst_server.resume()
    return jsonify({'status': 'listening resumed'})

@app.route('/levels', methods=['GET'])
def levels():
    """Input level per capture source, polled by the operator page.

    Levels keep updating while the server is paused — pause only mutes the
    recognizer threads, the stream stays open — which is exactly when the
    operator wants to check whether the microphone delivers anything.
    """
    if app.sst_server is None:
        return jsonify({'error': 'not initialized'}), 503
    return jsonify({
        'status': app.sst_server.status,
        'sources': app.sst_server.describe_sources(),
        # The threshold the VAD actually compares against — RMS, same 0..1
        # scale as each source's `level_rms` (NOT as `level`, which is peak).
        # The operator page reads it back from here after every change rather
        # than trusting its own slider: the server is the truth.
        'vad_energy_threshold': app.sst_server.vad_energy_threshold,
        # Second, independent threshold: microphone switched on vs. off. Also
        # RMS, also 0..1, so the operator page can draw it into the same bar in
        # a different colour. {"enabled": false} when the gate is not running.
        'mic_gate': app.sst_server.describe_mic_gate(),
    })


@app.route('/vad_threshold', methods=['POST'])
def set_vad_threshold():
    """Move the VAD's speech/silence threshold at runtime. Body: {"value": 0.012}.

    Takes effect on the running chunkers immediately, is remembered on the
    server (so a recognizer rebuilt later keeps it) and is written to the
    settings file (so a restart keeps it). `persisted` says whether the last
    part worked; `persist_error` carries the reason when it did not.
    """
    if app.sst_server is None:
        return jsonify({'error': 'not initialized'}), 503
    data = request.get_json(silent=True) or {}
    if 'value' not in data:
        return jsonify({'status': 'error', 'error': 'no value given'}), 400
    try:
        value = app.sst_server.set_vad_energy_threshold(data['value'])
    except ValueError as e:
        return jsonify({'status': 'error', 'error': str(e)}), 400
    persisted, reason = _persist(vad_energy_threshold=value)
    body = {'status': 'ok', 'vad_energy_threshold': value, 'persisted': persisted}
    if not persisted:
        body['persist_error'] = reason
    return jsonify(body)


@app.route('/mic_gate', methods=['POST'])
def set_mic_gate():
    """Retune the microphone-switch gate. Body: any of
    {"value": 0.002, "debounce_ms": 1500, "release_ratio": 0.5}.

    Separate from /vad_threshold on purpose: the VAD separates speech from
    pauses, this separates a switched-on microphone from a switched-off one.
    A rejected field changes nothing at all — the gate is never left half
    adjusted.
    """
    if app.sst_server is None:
        return jsonify({'error': 'not initialized'}), 503
    data = request.get_json(silent=True) or {}
    try:
        gate = app.sst_server.configure_mic_gate(
            value=data.get('value'),
            debounce_ms=data.get('debounce_ms'),
            release_ratio=data.get('release_ratio'),
        )
    except ValueError as e:
        return jsonify({'status': 'error', 'error': str(e)}), 400
    # Alle drei Werte, nicht nur der geaenderte: nach einem erfolgreichen Aufruf
    # sind alle drei gueltig, und die Datei beschreibt dann das ganze Gate statt
    # eines Ausschnitts davon.
    persisted, reason = _persist(
        mic_gate_threshold=gate['threshold'],
        mic_gate_release_ratio=gate['release_ratio'],
        mic_gate_debounce_ms=gate['debounce_ms'],
    )
    body = {'status': 'ok', 'mic_gate': gate, 'persisted': persisted}
    if not persisted:
        body['persist_error'] = reason
    return jsonify(body)


@app.route('/audio_devices', methods=['GET'])
def audio_devices():
    if app.sst_server is None:
        return jsonify({'error': 'not initialized'}), 503
    return jsonify({'devices': app.sst_server.list_input_devices()})


@app.route('/audio_device', methods=['POST'])
def set_audio_device():
    """Switch the capture device at runtime. Body: {"device": <index|name>,
    "sample_rate": <optional int>}.

    Any failure leaves the previous device running (see
    STTServer.set_audio_device) and is reported as 400 rather than silently
    dropping recognition.
    """
    if app.sst_server is None:
        return jsonify({'error': 'not initialized'}), 503
    data = request.get_json(silent=True) or {}
    device = data.get('device', data.get('index', data.get('name')))
    if device is None:
        return jsonify({'status': 'error', 'error': 'no device given'}), 400
    try:
        source = app.sst_server.set_audio_device(device, data.get('sample_rate'))
    except Exception as e:
        logging.error(f"Audio device switch to {device!r} failed: {e}")
        return jsonify({'status': 'error', 'error': str(e)}), 400
    return jsonify({'status': 'ok', 'source': source})


@app.route('/audio_logs', methods=['GET'])
def audio_logs():
    """Get information about audio logging status and log files."""
    if not hasattr(app.sst_server, 'enable_audio_logging'):
        return jsonify({'enabled': False})

    response = {
        'enabled': app.sst_server.enable_audio_logging,
        'log_folder': app.sst_server.audio_log_folder if app.sst_server.enable_audio_logging else None,
    }

    # Optionally list recent log files
    if app.sst_server.enable_audio_logging and os.path.exists(app.sst_server.audio_log_folder):
        try:
            log_files = sorted(os.listdir(app.sst_server.audio_log_folder), reverse=True)[:10]  # List 10 most recent files
            response['recent_logs'] = log_files
        except Exception as e:
            response['error'] = str(e)

    return jsonify(response)


@app.route('/events', methods=['GET'])
def events():
    bus = getattr(app, 'event_bus', None)
    if bus is None:
        return Response('event bus not initialized', status=503)

    sub = bus.subscribe()

    def stream():
        try:
            while True:
                try:
                    ev = sub.get(timeout=15.0)
                    yield f"data: {json.dumps(ev.to_dict())}\n\n"
                except queue.Empty:
                    # SSE keep-alive comment so proxies don't close the connection
                    yield ": keep-alive\n\n"
        except GeneratorExit:
            pass
        finally:
            bus.unsubscribe(sub)

    return Response(stream(), mimetype='text/event-stream')

@app.route("/operator", methods=["GET"])
def operator():
    backend_name = "unknown"
    recognizer_ids = ["left", "right"]  # safe default if server not yet initialized
    if app.sst_server is not None:
        if getattr(app.sst_server, "backend", None) is not None:
            backend_name = type(app.sst_server.backend).__name__.replace("Backend", "").lower()
        threads = getattr(app.sst_server, "threads", None)
        if threads:
            recognizer_ids = [t.recognizer_id for t in threads]
    return render_template("operator.html", backend=backend_name, recognizer_ids=recognizer_ids)