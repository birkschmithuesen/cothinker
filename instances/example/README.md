# instances/example — invented example instance

A second, fully invented instance directory. Every string under this directory (instance
name, hosts, role names, question text, dossier facts, poster style, profile content) is
placeholder content written for this card. None of it is real session or operator material,
and it carries no real instance or operator identifiers (checked by
`tests/test_instance_example.py`).

Purpose: give generic code and generic tests (the instance loader, question/role content
checks, profile loading) something valid to run against without depending on the real
content of any other, non-invented instance directory in this repo. Follow-up cards extend
this with a shared test helper and move instance-specific assertions out of the generic
test suite.

## Shape

- `instance.toml` — required keys only (`name`, `timezone`, `languages`, `app_format`,
  `poster_format`), illustrative `[secrets]` env-var names and `[hosts]` entries (all
  `*.example.org`).
- `content/` — bilingual (`de`/`en`) question and role pools, app strings, object icons,
  one dossier topic, a cue sheet, a poster style note, and an STT vocabulary file
  (`stt_vocab.txt`, docs/instance-format.md). All content is bilingual
  DE/EN, matching `languages = ["de", "en"]`.
- `profiles/{main,writer,lektor,stoffentwickler,check}/` — minimal neutral `config.yaml`
  (+ `SOUL.md` for every role but `check`, matching the structural asymmetry of the
  instance this layout is modeled on) per role. Skill wiring for role profiles does not
  exist generically yet (plan Task 5.1), so these are placeholder shells, not a working
  agent setup.

## IP/host convention

Any host string in this directory is `*.example.org`; any IPv4 address, if one ever appears,
stays within `192.0.2.0/24` (RFC 5737 documentation range).
