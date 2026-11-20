# STT Server

Speech-to-text server with two pluggable backends — Vosk and faster-whisper —
plus an operator display and the existing botland callback contract.

## Backends

- **Vosk** (default): low-latency, streaming partials, modest accuracy on German.
  Runs on Linux and Windows.
- **Whisper** (`large-v3-turbo` via `faster-whisper`): segment-based, high
  accuracy on German, requires CUDA. Targets the Windows host with the RTX 3080.

Switching is a CLI flag — see `scripts/run_stt.ps1` (`-Whisper`) and
`scripts/run_stt.sh` (`--whisper`).

## Quick start (Windows, Whisper)

```powershell
.\venv\Scripts\Activate.ps1
pip install -r requirements/requirements.txt
# If CUDA libs aren't auto-installed:
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12

# One-time model download (~3 GB):
python -c "from faster_whisper import WhisperModel; WhisperModel('large-v3-turbo', device='cuda', compute_type='float16')"

.\scripts\run_stt.ps1 -Whisper
```

Operator UI: `http://localhost:5051/operator` (or `http://<lan-ip>:5051/operator`).

## CLI flags

Top-level (both backends):

| Flag                  | Default                       | Notes                                  |
|-----------------------|-------------------------------|----------------------------------------|
| `--audio_devices`     | from `SST_AUDIO_DEVICES`      | space-separated device-name substrings |
| `--sample_rates`      | from `STT_AUDIO_DEVICES_SR`   | one per device, must be the device's native rate |
| `--channels`          | none                          | one per recognizer; `left`/`right`/`regie`/`audience`. Lives on the backend subparser — pass AFTER the backend subcommand keyword. |
| `--asio-channels`     | from `STT_ASIO_CHANNELS`      | 1-indexed ASIO inputs (e.g. `13 14`) for the single configured device. Backend-subparser flag. Requires `SD_ENABLE_ASIO=1`. |
| `--audio_logging`     | off                           | enables WAV logging of incoming audio  |
| `--debug_audio`       | off                           | saves post-resample audio under `audio/stt_rec/` (per-frame for vosk, per-chunk for whisper) |
| `--settings-file`     | `settings/stt_runtime.json` (or `STT_SETTINGS_FILE`) | JSON file that keeps the thresholds measured on site across a restart — see below |

Whisper subparser:

| Flag                     | Default            | Notes                              |
|--------------------------|--------------------|------------------------------------|
| `--model`                | `large-v3-turbo`   | any faster-whisper model id        |
| `--device`               | `cuda`             | or `cpu`                           |
| `--compute_type`         | `float16`          | `int8_float16`/`int8`/`float32`    |
| `--language`             | `de`               | ISO 639-1                          |
| `--vad_min_silence_ms`   | `500`              | end-of-phrase silence              |
| `--vad_min_speech_ms`    | `250`              | minimum buffered speech to submit  |
| `--vad_energy_threshold` | `0.01`             | RMS threshold for "speech present". Only a *typed* value counts as set — see the settings file below. |

## Settings that survive a restart

Four values are measured on site, at the real room and the real microphone, and
would otherwise be gone after a crash:

`vad_energy_threshold`, `mic_gate_threshold`, `mic_gate_release_ratio`,
`mic_gate_debounce_ms`.

They are written to `--settings-file` (default `settings/stt_runtime.json`,
git-ignored) on every accepted `POST /vad_threshold` and `POST /mic_gate`, and
read back at the next start. Device, language and backend are deliberately *not*
stored — those belong to the setup and stay on the command line.

Precedence at startup, highest first:

1. a value **typed on the command line** — `--vad_energy_threshold 0.005` means
   what it says, and a stored file must never override it;
2. the stored value;
3. the matching `.env` variable (`STT_MIC_GATE_*`);
4. the built-in default.

The start log prints one line naming the source of each value, so the file can
never quietly win an argument you didn't know it was having.

Robustness: a missing file is the normal first run. Broken JSON makes the whole
file void; a single out-of-range value only voids itself. Either way the server
logs loudly and starts on the defaults — it never refuses to start. Writing goes
through a temp file plus `os.replace`, and a failed write leaves the runtime
change in force and reports `persisted: false` in the HTTP response (the operator
page shows „nicht gespeichert: …" next to the slider).

## Tuning VAD with `--debug_audio`

When `--debug_audio` is set, every chunk submitted to Whisper is saved to
`audio/stt_rec/{recognizer_id}_{ts}_{idx}.wav`, and a log line is emitted
listing the path, duration, RMS, and resulting text. Replay these in any
audio player to tune `--vad_*` defaults to your room/microphone.

## Audio device discovery

```bash
python -c "import sounddevice as sd; print(sd.query_devices())"
```

`--audio_devices` matches device-name substrings, not indices.
