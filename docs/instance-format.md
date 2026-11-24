# Instance format

CoThinker's core (gateway, stage, the agent and STT seams) is instance-agnostic
— it never hard-codes a deployment's name, content or secrets. The companion
seam that makes that possible is the **instance seam**, implemented in
`shared/instance.py`. This document describes the on-disk format an instance
directory must follow and the Python interface the core reads it through. It
assumes no prior familiarity with the repository.

An instance is **configuration and content, never code and never live run
data**. Audio, transcripts and other session output stay in a deployment's
own, separately backed-up data folders — they are never checked into an
instance directory.

## Directory layout

```
instances/<name>/
  instance.toml
  content/
  profiles/
  deploy/
  acceptance/
  scripts/
  tests/
  docs/
  look/
```

- `instance.toml` — the manifest (format below). The only file every
  instance must have.
- `content/` — the deployment's own text material: question pools, role
  descriptions, app strings, anything a session reads that is specific to
  this deployment rather than to the core.
- `profiles/<role>/` — one directory per agent role this deployment uses,
  each with whatever profile material (a `SOUL.md`, skill configuration)
  the agent-side code expects. `shared/instance.py` only resolves the
  path; it does not interpret what is inside.
- `deploy/`, `acceptance/`, `scripts/`, `tests/`, `docs/` — deployment-side
  equivalents of the repository's own top-level folders: unit templates
  and render helpers, this deployment's acceptance scenarios, one-off
  scripts, deployment-specific tests, and deployment-facing documentation.
- `look/` — this deployment's design-token override, `tokens.yaml` in the
  same shape as `surfaces/design/tokens.yaml` (ADR 0023). `python -m
  surfaces.design.build --instance <name>` merges it over the default and
  writes `look/tokens.css` / `look/tokens.json`. A deployment changes its
  look here, never by editing surface code.

`instances/README.md` is the short version of this rule; `instances/example/`
is a complete, fully invented instance that exercises every part of this
format without depending on any real deployment's content.

## `instance.toml`

```toml
[instance]
name = "example"
timezone = "UTC"
languages = ["de", "en"]
app_format = "example-app/1"
poster_format = "example-poster/1"

[secrets]
hub_secret = "EXAMPLE_HUB_SECRET"
app_token = "EXAMPLE_APP_TOKEN"

[hosts]
wall = "wall.example.org"

[modules]
enabled = ["example_echo"]
```

- `[instance]` — the required keys are `name`, `timezone`, `languages`,
  `app_format` and `poster_format`. `shared.instance.validate()` reports
  every missing one by name rather than failing on the first.
- `[secrets]` — each value must be the **name** of an environment variable
  that holds the actual secret at run time, never the secret's value
  itself. `validate()` checks the shape of the name (uppercase, starts
  with a letter) and rejects anything that looks like a literal value
  instead of a variable name.
- `[hosts]` — host names this deployment's surfaces talk to. Optional.
- `[modules]` — `enabled` lists the module names (see `docs/module-format.md`)
  this deployment loads, by name only; where those modules actually live on
  disk is a separate search-path concern, not part of the manifest.

## Loading an instance

```python
from shared.instance import load

cfg = load("instances/example")   # or: load() reads COTHINKER_INSTANCE_DIR
```

`load()` resolves an instance in this order: an explicit path argument, then
the `COTHINKER_INSTANCE_DIR` environment variable, then `InstanceError` — a
process that needs an instance and finds neither simply refuses to start
rather than falling back to a default deployment.

The returned `InstanceConfig` is a frozen dataclass with:

- `content(rel)`, `profile(role)`, `acceptance(rel)` — each resolves a
  relative name to an absolute path under the right subdirectory and raises
  `InstanceError` if nothing exists there. Calling code never builds these
  paths itself.
- `name`, `timezone`, `languages`, `app_format`, `poster_format` — the
  manifest's required fields, already parsed.
- `secret_env`, `hosts`, `modules` — the optional `[secrets]`, `[hosts]`
  and `[modules].enabled` tables, as plain mappings/tuples.

From the command line, the same validation `load()` performs is available
without writing any code:

```bash
python3 -m shared.instance check instances/example
```

This prints `ok` and exits 0 when the manifest is well-formed, or prints
every problem it found and exits 1 otherwise.

## What this seam deliberately does not do

- It does not read or interpret `content/`, `profiles/` or `acceptance/`
  files itself — it only resolves their paths. What those files contain,
  and what reads them, is decided by the calling code (agent profiles,
  question pools, the acceptance runner), not by `shared/instance.py`.
- It does not hold or validate the actual value of a secret, only the name
  of the environment variable that should hold it.
- It does not decide which instance directory is "the" running one beyond
  the resolution order above — a deployment chooses that for itself,
  explicitly, every time it starts.

These are deliberately left open so the same loader works for a minimal,
invented instance like `instances/example/` and for a full deployment with
its own agent roles, content pools and acceptance scenarios.
