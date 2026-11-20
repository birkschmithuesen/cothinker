# Vendored: ufal/whisper_streaming `whisper_online.py`

## Source

- Project: https://github.com/ufal/whisper_streaming
- File: https://raw.githubusercontent.com/ufal/whisper_streaming/main/whisper_online.py
- Commit (HEAD of `main` at time of vendoring): `6da90b44b7e50d79695e68166d2a2c7609c75abb`
- Date copied: 2026-05-18

## License

MIT License, Copyright (c) 2023 UFAL. Full license text is preserved in
`LICENSE` in this directory.

## Why vendored

The pip package `whisper-streaming` has transitive deps that do not build
on Windows:

- `pyalsaaudio` (Linux-only, ALSA headers)
- `opus-fast-mosestokenizer` (build failure under MSVC)

Our `whisper-streaming` STT backend only needs two classes — `FasterWhisperASR`
and `OnlineASRProcessor` — neither of which transitively imports any of the
unbuildable deps at module-load time. Vendoring the single `whisper_online.py`
file is enough; the heavy Linux-only deps stay out of the import graph.

## Files vendored

- `whisper_online.py` — upstream file, copied verbatim. No patches applied.
- `__init__.py` — re-exports `FasterWhisperASR`, `OnlineASRProcessor`.
- `LICENSE` — upstream MIT license.
- `UPSTREAM.md` — this file.

## Patches applied

None. Project-local imports in upstream that could fail on Windows are all
already lazy (inside functions / class methods):

- `silero_vad_iterator` — imported inside `VACOnlineASRProcessor.__init__`,
  never invoked by our backend.
- `mosestokenizer`, `wtpsplit`, `tokenize_uk` — imported inside
  `create_tokenizer`, never invoked by our backend (we don't use the
  "sentence" buffer trimming mode).
- `whisper`, `whisper_timestamped`, `mlx_whisper`, `openai` — imported
  inside non-FasterWhisper ASR class `load_model` methods we never call.

Module-level imports (`numpy`, `librosa`, `soundfile`) are dependencies of
`faster-whisper` and are already installed alongside it.

## Updating

To refresh from upstream:

1. Fetch the new `whisper_online.py` from the repo.
2. Diff against the vendored copy; re-apply patches if any.
3. Update the commit SHA and date above.
4. Run the smoketest:
   ```powershell
   & .\venv\Scripts\python.exe -c "from fundusapps.stt_server.backends._vendored_whisper_streaming.whisper_online import FasterWhisperASR, OnlineASRProcessor; print('OK')"
   ```
5. Run the test suite to make sure nothing else regressed.
