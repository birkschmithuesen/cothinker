# instances

One directory per instance. An instance is configuration and content, never code and never run data
(audio, transcripts, results stay in the backed-up data folders).

- `instance.toml` — format in docs/superpowers/specs/2026-11-12-repo-split-design.md §5; secrets are
  given as environment variable NAMES, never values.
- `content/`, `profiles/`, `deploy/`, `acceptance/`, `scripts/`, `tests/`, `docs/`.
- Check: `python3 -m shared.instance check instances/<name>`.

Only `example/` (invented data) goes public. Every other directory here moves into its own private repo.
