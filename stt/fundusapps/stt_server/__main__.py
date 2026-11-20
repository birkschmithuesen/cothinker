import sys
import logging
from pathlib import Path
import os

from fundusapps.stt_server.args import args, config  # Arguments are imported here
from fundusapps.stt_server.app  import app
from fundusapps.stt_server.sr   import STTServer, _CHANNEL_IDX, sd
from fundusapps.stt_server.botland_callbacks import send_message_to_botland
from fundusapps.stt_server.events import EventBus
from fundusapps.stt_server.backends import get_backend_class
from fundusapps.stt_server.interview_signal import make_interview_signal_callback
from fundusapps.stt_server.settings import (
    RuntimeSettings, log_resolved_settings, resolve_runtime_settings,
)
from fundusapps.stt_server.thought_end import build_tap


log_folder = Path(args.log_folder)
log_folder.mkdir(parents=True, exist_ok=True)

# Hardcode audio logging folder
audio_log_folder = Path("logs/audio")
audio_log_folder.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s [%(filename)s:%(lineno)s]',
    handlers=[
        logging.FileHandler(os.path.join(log_folder, 'sst_server.log')),
        logging.StreamHandler()
    ]
)

# Die vor Ort eingemessenen Schwellen, gelesen NACH dem Parsen der Argumente.
# Muss vor jedem Zugriff auf args.vad_energy_threshold / args.mic_gate_* stehen:
# diese Attribute existieren bis hierher nicht (default=SUPPRESS in args.py, so
# erkennt die Rangfolge einen getippten Wert). Danach tragen sie garantiert
# einen geprueften Zahlenwert.
settings_store = RuntimeSettings(args.settings_file)
log_resolved_settings(resolve_runtime_settings(args, settings_store.load(), env=config))
# Sofort an die App, nicht erst in main(): ein Prozess, der dieses Modul
# geladen hat, soll speichern koennen: ein stillschweigend nicht speichernder
# Server waere genau der Zustand, den dieses Feature abschaffen soll.
app.settings_store = settings_store


def main():
    app.sst_server = None

    device_indexes = [STTServer.get_audio_device_index(d) for d in args.audio_devices]

    # Validate ASIO configuration before starting any threads.
    if args.asio_channels:
        if len(args.audio_devices) != 1:
            logging.error(
                "--asio-channels requires exactly one --audio_devices entry "
                f"(got {len(args.audio_devices)}: {args.audio_devices})"
            )
            sys.exit(2)
        if sd is None:
            logging.error("--asio-channels needs PortAudio, which is not installed here.")
            sys.exit(2)
        hostapis = [h['name'] for h in sd.query_hostapis()]
        if 'ASIO' not in hostapis:
            logging.error(
                "--asio-channels was passed but no ASIO host API is available in PortAudio. "
                "Set the environment variable SD_ENABLE_ASIO=1 before launching this process "
                "so sounddevice loads its ASIO-enabled PortAudio DLL. "
                f"Currently available host APIs: {hostapis}"
            )
            sys.exit(2)
        if not args.channels or len(args.channels) != len(args.asio_channels):
            logging.error(
                f"--asio-channels has {len(args.asio_channels)} entries but --channels has "
                f"{len(args.channels) if args.channels else 0}; they must match."
            )
            sys.exit(2)
        # Reject duplicate resolved channel indices (e.g. `--channels regie left`
        # both map to index 0 via _CHANNEL_IDX and would silently produce two
        # recognizers reading the same demuxed channel).
        resolved = [_CHANNEL_IDX.get(c) for c in args.channels]
        if len(set(resolved)) != len(resolved):
            logging.error(
                "--channels values resolve to duplicate channel indices "
                f"({list(zip(args.channels, resolved))}); choose distinct labels."
            )
            sys.exit(2)

    debug_audio_folder = Path("audio/stt_rec") if args.debug_audio else None
    if debug_audio_folder is not None:
        debug_audio_folder.mkdir(parents=True, exist_ok=True)

    bus = EventBus()
    backend_cls = get_backend_class(args.backend)
    backend = backend_cls.from_args(event_bus=bus, args=args)

    # Off unless asked for. Audio stays on this machine: the tap hands the
    # window to a local child process and publishes the number only.
    thought_end_tap = build_tap(getattr(args, "thought_end", "off"),
                                bus.publish)

    # The threshold is passed for EVERY backend: resolve_runtime_settings() has
    # filled it in above no matter which subcommand was used, and a backend that
    # builds no chunker simply ignores it. Restricting it to the two --vad_*
    # backends would mean a value the operator measured and saved gets dropped
    # the moment someone starts a different backend.
    vad_kwargs = dict(vad_energy_threshold=args.vad_energy_threshold)
    # The other two knobs only exist on these two subparsers. Both backends
    # buffer per utterance (wants_streaming() is False), so both need them
    # passed through. Leaving "infomaniak-whisper" out here would silently
    # ignore its --vad_* options and fall back to the defaults in
    # STTRecognizer.__init__ (500/250) — a config that looks applied but isn't.
    if args.backend in ("whisper", "infomaniak-whisper"):
        vad_kwargs.update(
            vad_min_silence_ms=args.vad_min_silence_ms,
            vad_min_speech_ms=args.vad_min_speech_ms,
            # Only these two subparsers carry --vad: the streaming
            # backends build no local chunker, so there is nothing for a
            # detector to decide there.
            vad_kind=args.vad,
            silero_threshold=args.silero_threshold,
        )

    # Reject a hysteresis-free gate at startup rather than at the first switch.
    # release_ratio 1.0 turns the gate into a bare comparison, and a bare
    # comparison flutters at the threshold: dozens of interview start/stop
    # signals a minute, all of them believed by the receiving side.
    # Only a value typed on the command line can still get here — one that came
    # from the settings file or the .env has already been through
    # validate_release_ratio() and was dropped there if it was out of range.
    if args.mic_gate and not (0.0 < args.mic_gate_release_ratio < 1.0):
        logging.error(
            f"--mic-gate-release-ratio must be greater than 0 and below 1.0 "
            f"(got {args.mic_gate_release_ratio}); 1.0 would remove the hysteresis."
        )
        sys.exit(2)

    interview_signal = make_interview_signal_callback(args.kg_url)
    if args.mic_gate and interview_signal is None:
        logging.info(
            "Microphone gate is on but --kg-url is empty: recognition will follow "
            "the switch and the operator page will show it, but no interview "
            "signal is sent anywhere."
        )

    with STTServer(
        audio_device_indexes=device_indexes,
        backend=backend,
        event_bus=bus,
        sample_rates=args.sample_rates,
        channels=args.channels,
        asio_channels=args.asio_channels,
        enable_audio_logging=args.audio_logging,
        debug_audio_folder=debug_audio_folder,
        external_record_callback=None,
        external_stop_callback=None,
        mic_gate_enabled=args.mic_gate,
        mic_gate_threshold=args.mic_gate_threshold,
        mic_gate_debounce_ms=args.mic_gate_debounce_ms,
        mic_gate_release_ratio=args.mic_gate_release_ratio,
        interview_signal_callback=interview_signal,
        thought_end_tap=thought_end_tap,
        **vad_kwargs,
    ) as stt_server:

        app.sst_server = stt_server
        app.event_bus = bus

        if args.audio_logging:
            logging.info(f"Audio logging is ENABLED and saving to: {audio_log_folder}")
        if debug_audio_folder is not None:
            logging.info(f"Debug audio recording ENABLED, saving to: {debug_audio_folder}")

        try:
            logging.info(
                f"Starting Flask server on {args.host}:{args.port} with debug={args.debug_flask}"
            )
            display_host = "localhost" if args.host in ("0.0.0.0", "::") else args.host
            logging.info(f"Operator UI: http://{display_host}:{args.port}/operator")
            if args.host in ("0.0.0.0", "::"):
                logging.info(f"  (also reachable on LAN at http://<this-machine-ip>:{args.port}/operator)")
            app.run(host=args.host, port=args.port, debug=args.debug_flask)
        except Exception as e:
            logging.error(f"Error running Flask server: {e}")
            sys.exit(1)
        finally:
            if thought_end_tap is not None:
                thought_end_tap.close()

if __name__ == "__main__":
    main()
