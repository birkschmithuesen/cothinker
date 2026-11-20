# Vendored: Silero VAD (ONNX)

## Source

- Project: https://github.com/snakers4/silero-vad
- Package: PyPI `silero-vad` 6.2.3
- File inside the wheel: `silero_vad/data/silero_vad.onnx`
- Size: 2 327 524 bytes
- sha256: `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`
- Date copied: 2026-09-24

## License

MIT, "Copyright (c) 2020-present Silero Team". Full text in `LICENSE`
in this directory.

## Why vendored rather than depended on

The PyPI package `silero-vad` hard-requires `torch`, which is gigabytes
and is not installed on the vServer or on the recording PC. The model
itself needs none of it: it runs on plain `onnxruntime` (~50 MB), and
that stays an OPTIONAL dependency -- only `--vad silero` imports it, and
without it the server says so at startup instead of running on.

## Measured interface (2026-09-24)

Inputs: `input` float32 `[1, 576]` (64 context samples carried over from
the previous window + 512 new samples at 16 kHz), `state` float32
`[2,1,128]` (zeros at the start), `sr` int64 scalar `16000`.
Outputs: `output` `[1,1]` speech probability, `stateN` (the next state).
Cost: 0.136 ms per 32 ms window measured on the vServer CPU on
2026-09-24 (the plan had stated 0.464 ms).

## Updating

1. `pip download --no-deps silero-vad==<new version>`, unpack with
   `python3 -m zipfile -e`.
2. Replace the file, update size, sha256 and date above.
3. Update `MODEL_SHA256` / `MODEL_BYTES` in
   `fundusapps/stt_server/tests/test_vad_silero.py` -- that test exists
   so an unannounced model swap cannot pass as a green run.
4. Re-run the detector tests and write down the new deviations.
