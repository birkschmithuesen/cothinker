import argparse
import os

from dotenv import dotenv_values, load_dotenv

from fundusapps.stt_server.settings import DEFAULT_SETTINGS_FILE

#: Only names with one of these prefixes are read out of the process
#: environment. Not the whole environment: `_redacted_config()` prints the
#: key list on every start, and `SST_` (sic) is a historical typo that the
#: recording PC's .env and args.py both still use -- renaming it would
#: silently change which audio device the server opens.
CONFIG_PREFIXES = ("STT_", "SST_", "BOTSERVER_")


def build_config(environ, dotenv):
    """The server's configuration, `.env` over environment.

    `.env` wins, unconditionally: on the recording PC it is the only
    source, it is hand-maintained, and a stale shell variable must never
    override it. The environment only FILLS GAPS -- which is what makes
    the test suite run on a machine that has no `.env` at all, using the
    defaults `tests/conftest.py` sets (it always set them; until
    2026-09-24 this module simply never looked).
    """
    merged = {name: value for name, value in environ.items()
              if name.startswith(CONFIG_PREFIXES)}
    merged.update(dotenv)
    return merged


load_dotenv(".env")
config = build_config(os.environ, dotenv_values(".env"))


def _redacted_config(cfg):
    """The .env as a printable dict, with secret VALUES masked.

    This used to be a plain `print(config)`, which wrote every value in the
    .env to stdout on every start — including ELEVENLABS_API_KEY and any other
    credential. Those lines end up in logs/sst_server/sst_server.log (already
    megabytes large) and in any terminal scrollback or screen recording, i.e.
    in places nobody treats as secret storage.

    Keys are still listed, because "which settings did this process actually
    read" is genuinely useful when a wrong .env is picked up — only the values
    of secret-looking names are replaced. Matching is on the NAME, so a new
    credential added later is masked automatically as long as it is called
    *_KEY / *_TOKEN / *_SECRET / *_PASSWORD.
    """
    geheim = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL")
    out = {}
    for name, wert in cfg.items():
        if any(g in name.upper() for g in geheim) and wert:
            out[name] = f"<{len(wert)} Zeichen, maskiert>"
        else:
            out[name] = wert
    return out


print(_redacted_config(config))


def _parse_asio_channels_env():
    raw = config.get('STT_ASIO_CHANNELS', '').strip()
    if not raw:
        return None
    return [int(x.strip()) for x in raw.split(',') if x.strip()]


parser = argparse.ArgumentParser(description='Speech-to-Text Server')
parser.add_argument('--language', type=str, default='en', help='Language code for STT (e.g. en, es, fr)')
parser.add_argument('--debug_flask', action='store_true', help='Run Flask in debug mode')
parser.add_argument('--host', type=str, help='Host to run the server on', default=config['STT_HOST'])
parser.add_argument('--port', type=int, help='Port to run the server on', default=config['STT_PORT'])
parser.add_argument('--audio_devices', type=str, nargs='+', default=config['SST_AUDIO_DEVICES'].split(','), help='Audio device to use for STT')
parser.add_argument('--sample_rates', type=int,  nargs='+', default=[int(sr) for sr in config['STT_AUDIO_DEVICES_SR'].split(',')], help='Sample rate for audio recording')
parser.add_argument('--log_folder'    , type=str, default="logs/sst_server")
parser.add_argument('--sid', type=str, default=None, help='Session ID to use when sending messages to botland')
parser.add_argument('--settings-file', dest='settings_file', type=str,
                    default=(config.get('STT_SETTINGS_FILE') or '').strip() or DEFAULT_SETTINGS_FILE,
                    help='JSON file next to the log that keeps the values measured on site '
                         '(VAD threshold, microphone gate) across a restart. Written on every '
                         'accepted POST /vad_threshold and /mic_gate, read at startup. '
                         'Deliberately not the .env: that one is hand-maintained and holds keys.')

parser.add_argument('--audio_logging', action='store_true', help='Enable logging of audio to WAV files')
parser.add_argument('--debug_audio', action='store_true', help='Save post-resampled audio fed to Vosk as WAV files under audio/stt_rec/')
parser.add_argument('--thought-end', dest='thought_end',
                    choices=['off', 'smart_turn'], default='off',
                    help='Score every final with smart-turn next to the audio '
                         'and publish a thought_end event (numbers only, never '
                         'audio). Off by default. Needs '
                         'COTHINKER_SMART_TURN_PYTHON and '
                         'COTHINKER_SMART_TURN_MODEL in the environment.')

# --channels and --asio-channels are shared across all backends. They are defined
# in a parent parser and added via parents= to each subparser so that argparse
# places them AFTER the subcommand name on the CLI. This is necessary because
# nargs='*' / nargs='+' on the main parser would greedily consume the subcommand
# token (e.g. 'vosk') as a value, preventing subparser dispatch.
#
# Correct CLI usage:
#   python -m fundusapps.stt_server ... vosk --channels regie audience --model M
#   python -m fundusapps.stt_server ... vosk --channels left right --asio-channels 13 14 --model M
_channel_parent = argparse.ArgumentParser(add_help=False)
_channel_parent.add_argument('--channels', type=str, nargs='*',
                    choices=['left', 'right', 'regie', 'audience'], default=None,
                    help='Per-recognizer channel selector: left/right (legacy stereo demux) '
                         'or regie/audience (Fireface ASIO input 13/14). One value per recognizer.')
_channel_parent.add_argument('--asio-channels', dest='asio_channels', type=int, nargs='+',
                    default=_parse_asio_channels_env(),
                    help='1-indexed ASIO input channels for the single configured device. '
                         'Maps to AsioSettings(channel_selectors=[ch-1, ...]). '
                         'Number of values must equal number of --channels. '
                         'Requires SD_ENABLE_ASIO=1 in the environment.')

# --- Mikrofonschalter als Interview-Signal -----------------------------------
# Off by default, and that is the important part: with the gate on, the INPUT
# LEVEL decides whether recognition runs. None of the existing productions has
# a microphone with a physical switch, so this must never arrive by surprise.
_channel_parent.add_argument('--mic-gate', dest='mic_gate', action='store_true',
                    default=config.get('STT_MIC_GATE', '').strip() in ('1', 'true', 'yes'),
                    help='Watch the input level for the physical switch on the microphone: '
                         'off pauses recognition and sends an interview stop, on resumes '
                         'and sends an interview start.')
# --- die vier einstellbaren Schwellen: default=SUPPRESS, kein echter Default ---
# Diese vier Werte werden vor Ort eingemessen und in der Einstellungsdatei
# gespeichert (settings.py). Damit "auf der Kommandozeile getippt" den
# gespeicherten Wert schlagen kann, muss argparse verraten, ob getippt wurde --
# und das tut es von sich aus nicht. Mit `default=argparse.SUPPRESS` FEHLT das
# Attribut, wenn nichts getippt wurde; `settings.resolve_runtime_settings()`
# setzt danach den geltenden Wert ein (gespeichert > .env > Vorgabe).
#
# Achtung beim Aendern: bis dieser Aufruf gelaufen ist, existieren
# args.mic_gate_threshold & Co. NICHT. Das ist Absicht -- ein fehlendes Attribut
# faellt sofort auf, ein durchgereichtes `None` erst im Betrieb, wenn der VAD
# nicht mehr arbeitet. Die frueheren Defaults stehen jetzt in
# settings.BUILT_IN_DEFAULTS bzw. settings.ENV_NAMES.
_channel_parent.add_argument('--mic-gate-threshold', dest='mic_gate_threshold', type=float,
                    default=argparse.SUPPRESS,
                    help='RMS level (0..1) above which the microphone counts as switched on. '
                         'Default 0.002: a converter noise floor sits below it, the quietest '
                         'speech well above. Read it off the RMS bar on the operator page with '
                         'the switch actually off — the right value is room- and gain-dependent.')
_channel_parent.add_argument('--mic-gate-release-ratio', dest='mic_gate_release_ratio', type=float,
                    default=argparse.SUPPRESS,
                    help='Hysteresis. Switch-off happens below threshold*ratio, not below '
                         'threshold. Default 0.5. Must stay below 1.0 — at 1.0 the gate '
                         'chatters around the threshold and produces dozens of interview '
                         'boundaries a minute.')
_channel_parent.add_argument('--mic-gate-debounce-ms', dest='mic_gate_debounce_ms', type=int,
                    default=argparse.SUPPRESS,
                    help='How long a state must hold before it counts as a change. '
                         'Default 1500 ms, i.e. a short dip is not a stop.')
_channel_parent.add_argument('--kg-url', dest='kg_url', type=str,
                    default=(config.get('KG_URL') or '').strip(),
                    help='Base URL of an external interview-signal receiver, e.g. http://192.168.0.50:8800. '
                         'Empty (the default) means the interview signal is not sent anywhere: '
                         'the gate still switches recognition and shows its state, it just has '
                         'no receiver.')


subparsers = parser.add_subparsers(dest='backend', required=True)

sp = vosk_parser = subparsers.add_parser('vosk', help='Use Vosk for speech recognition',
                                         parents=[_channel_parent])
sp.add_argument('--model', type=str, default='models/vosk-model-en-us-0.22', help='Vosk model to use')

sp_whisper = subparsers.add_parser('whisper', help='Use faster-whisper for STT',
                                   parents=[_channel_parent])
sp_whisper.add_argument('--model', default='large-v3-turbo')
sp_whisper.add_argument('--compute_type', default='float16',
                        choices=['float16', 'int8_float16', 'int8', 'float32'])
sp_whisper.add_argument('--device', default='cuda', choices=['cuda', 'cpu'])
sp_whisper.add_argument('--language', default='de')
sp_whisper.add_argument('--vad_min_silence_ms', type=int, default=500)
sp_whisper.add_argument('--vad_min_speech_ms', type=int, default=250)
# default=SUPPRESS: siehe den Block bei --mic-gate-threshold oben. Der frueher
# hier stehende 0.01 lebt jetzt in settings.BUILT_IN_DEFAULTS.
sp_whisper.add_argument('--vad_energy_threshold', type=float, default=argparse.SUPPRESS)
sp_whisper.add_argument('--vad', dest='vad', default='rms',
                        choices=['rms', 'silero'],
                        help='Which detector decides speech vs. silence. rms is '
                             'the energy threshold this server has always used; '
                             'silero is a small neural VAD, better in noise, '
                             'needs the onnxruntime package. See docs/stt.md.')
sp_whisper.add_argument('--silero-threshold', dest='silero_threshold',
                        type=float, default=0.5,
                        help='Speech PROBABILITY at or above which a 32 ms '
                             'window counts as speech. Only used with '
                             '--vad silero; /vad_threshold does not move it.')

sp_ws = subparsers.add_parser('whisper-streaming', help='Streaming faster-whisper with LocalAgreement-2 partials',
                               parents=[_channel_parent])
sp_ws.add_argument('--model', default='large-v3-turbo')
sp_ws.add_argument('--compute_type', default='float16',
                   choices=['float16', 'int8_float16', 'int8', 'float32'])
sp_ws.add_argument('--device', default='cuda', choices=['cuda', 'cpu'])
sp_ws.add_argument('--language', default='de')
sp_ws.add_argument('--min-chunk-size', dest='min_chunk_size', type=float, default=1.0,
                   help='Seconds between re-inferences over the growing audio buffer.')

sp_el = subparsers.add_parser('elevenlabs-scribe',
                              help='ElevenLabs Scribe v2 Realtime streaming STT',
                              parents=[_channel_parent])
sp_el.add_argument('--model', default='scribe_v2_realtime',
                   help='ElevenLabs realtime model id')
sp_el.add_argument('--language', default='de',
                   help='Language code passed as language_code to ElevenLabs')
sp_el.add_argument('--commit-strategy', dest='commit_strategy',
                   default='vad', choices=['vad', 'manual'],
                   help='vad: server VAD fires committed_transcript; '
                        'manual: backend never sends commit; turn boundaries '
                        'come purely from --silence-timeout.')
sp_el.add_argument('--silence-timeout', dest='silence_timeout',
                   type=float, default=0.7,
                   help='Wall-clock silence (s) that closes an utterance.')
sp_el.add_argument('--api-key-env', dest='api_key_env',
                   default='ELEVENLABS_API_KEY',
                   help='Env var name to read API key from.')
sp_el.add_argument('--timestamps', dest='timestamps', action='store_true',
                   default=True,
                   help='Ask ElevenLabs for word timestamps (include_timestamps) '
                        'and turn them into t_start/t_end on every final. On by '
                        'default since 2026-09-24. See docs/stt.md.')
sp_el.add_argument('--no-timestamps', dest='timestamps', action='store_false',
                   help='Do not ask for word timestamps. Finals then carry no '
                        't_start/t_end, which is the documented '
                        'backward-compatible case.')

# Infomaniak Whisper (Geneva, Switzerland) — batched, not streaming.
# Unlike the streaming backends this one buffers each utterance via VAD and
# submits it as one request, so the three --vad_* options below are the knobs
# that decide latency: longer chunks are cheaper per second of audio but make
# the text appear later. Measured 2026-08-31: overhead is a near-constant ~3 s
# regardless of chunk length (5.3 s audio -> 2.9 s e2e, 32 s audio -> 4.7 s).
sp_ik = subparsers.add_parser('infomaniak-whisper',
                              help='Infomaniak Whisper, batched via VAD chunks',
                              parents=[_channel_parent])
sp_ik.add_argument('--model', default='whisper')
sp_ik.add_argument('--language', default='de')
sp_ik.add_argument('--base-url', dest='base_url',
                   default='https://api.infomaniak.com')
sp_ik.add_argument('--product-id', dest='product_id', default='110416')
sp_ik.add_argument('--api-key-env', dest='api_key_env',
                   default='INFOMANIAK_API_KEY',
                   help='Env var name to read the API key from.')
sp_ik.add_argument('--request-timeout', dest='request_timeout',
                   type=float, default=60.0)
sp_ik.add_argument('--word-times', dest='word_times', action='store_true',
                   help='Ask for word timestamps and pass them on as a '
                        '`words` list ([{start, end}], seconds from the '
                        'utterance start) on each final -- used by the '
                        'consumer\'s --speaker-split. Off by default: the '
                        'request and the events stay exactly as before.')
sp_ik.add_argument('--vad_min_silence_ms', type=int, default=700)
sp_ik.add_argument('--vad_min_speech_ms', type=int, default=500)
sp_ik.add_argument('--vad_energy_threshold', type=float, default=argparse.SUPPRESS)
sp_ik.add_argument('--vad', dest='vad', default='rms',
                   choices=['rms', 'silero'],
                   help='Which detector decides speech vs. silence. rms is the '
                        'energy threshold this server has always used; silero '
                        'is a small neural VAD, better in noise, needs the '
                        'onnxruntime package. See docs/stt.md.')
sp_ik.add_argument('--silero-threshold', dest='silero_threshold',
                   type=float, default=0.5,
                   help='Speech PROBABILITY at or above which a 32 ms window '
                        'counts as speech. Only used with --vad silero; '
                        '/vad_threshold does not move it.')

# Speechmatics EU realtime (2026-10-26) -- wraps workshop/stt_speechmatics.py.
# Streaming with diarization; the speaker label rides in recognizer_id.
sp_sm = subparsers.add_parser('speechmatics-rt',
                              help='Speechmatics EU realtime streaming STT with diarization',
                              parents=[_channel_parent])
sp_sm.add_argument('--language', default='de')
sp_sm.add_argument('--max-delay', dest='max_delay', type=float, default=2.0,
                   help='Speechmatics max_delay (s, 0.7..4): how long a final may wait. '
                        'Partials are not affected. 2.0 measured best on meeting audio.')
sp_sm.add_argument('--max-speakers', dest='max_speakers', type=int, default=10,
                   help='Diarization max_speakers: upper bound of distinct speaker '
                        'labels (Speechmatics allows 2..100).')
sp_sm.add_argument('--prefer-current-speaker', dest='prefer_current_speaker',
                   action='store_true', default=False,
                   help='Diarization prefer_current_speaker (off: measured fewer '
                        'speaker confusions on meeting audio).')
sp_sm.add_argument('--eou-silence', dest='eou_silence', type=float, default=0.5,
                   help='end_of_utterance_silence_trigger (s): silence that closes '
                        'an utterance and releases the collected final.')
sp_sm.add_argument('--max-utt', dest='max_utt', type=float, default=20.0,
                   help='Publish a final after this many seconds of one speaker '
                        'even without an end of utterance.')
sp_sm.add_argument('--vocab', dest='vocab', action='append', default=None,
                   metavar='WORD[:SOUNDS_LIKE,...]',
                   help='Custom-dictionary entry (Speechmatics additional_vocab), '
                        'repeatable. "Robo:Robbo,Roboh" adds the word Robo with two '
                        'sounds_like hints. Default: the wake name Robo.')
sp_sm.add_argument('--url', default='',
                   help='Override the realtime URL (tests only); default is the EU endpoint.')
sp_sm.add_argument('--api-key-env', dest='api_key_env',
                   default='SPEECHMATICS_API_KEY',
                   help='Env var name to read the API key from.')
sp_sm.add_argument('--per-track-sidecar', dest='per_track_sidecar', default='',
                   help='Path to a bbb_tap --per-track run\'s own *-tracks.json '
                        'sidecar (fundusapps.bbb_tap.__main__.PerTrack). When set, '
                        'fundusapps.stt_server.track_speaker_resolver.build_speaker_resolver '
                        'builds a resolver from it (issue #128): the Speechmatics '
                        'diarization label (S1/S2/...) is overridden with the '
                        'participant whose track actually carried a word\'s energy. '
                        'Empty (the default) leaves today\'s diarization-label '
                        'behaviour untouched; scripts/stt-vserver.sh sets this only '
                        'when its own --per-track tap flag is also set.')


args = parser.parse_args()
