# CoThinker

> **TL;DR** — CoThinker sits in a meeting, in person or remote, and listens.
> It writes the minutes live and shows everyone a **website** with what
> matters right now: the current topic, what has been decided, what is
> still open. Everyone opens the link on their own device. Nobody has to
> share a screen, and nobody has to operate the tool: you just keep
> talking.

The name: a **co-thinker**. It does not just take notes, it thinks *with*
the group. It keeps track of what holds, points out contradictions it
notices, and feeds the current state back into the conversation, so the
group can see where it stands and keep its head free for the actual
thinking.

Design principle behind every choice in this project: reduce to the
essentials, keep it clear, and leave room to think. Empty space on the
page is not unused space — a full page takes the thinking away from the
group by filling it with reading.

## The idea in three sentences

1. **Listen without being in the way.** Speech becomes text, text becomes
   minutes. Nobody has to press a button; everything spoken is just
   conversation unless a short wake phrase addresses the tool directly.
2. **Show little, but the right thing.** The website is not a transcript.
   It is a small, curated surface that follows the conversation on its
   own, with the complete minutes next to it for anyone who wants detail.
3. **Code decides, the model only proposes.** A language model is slow
   (seconds to minutes per turn) and sometimes wrong. So the model only
   *proposes*; fixed rules in code decide what actually appears, how much
   of it, and when the page is allowed to change.

## What happens where: the data path

| Station | What it does |
|---|---|
| **Listen** (`stt/`) | Turns speech into text, sentence by sentence. |
| **Understand & decide** (`gateway/`) | Writes every line into the minutes immediately, cuts speech into blocks, and hands each block to the agent. The agent proposes a new topic, a decision, a question; fixed code checks every proposal against rules before it can reach the page. |
| **Store** (a session folder) | The minutes file holds what lasts. A separate state file holds only what the current surface shows *right now* — and has exactly one writer, so its rules always hold. |
| **Show** (`stage/`) | A small web server that only reads. The browser polls every few seconds and swaps only what changed. |

The agent is a separate process from the rest of the pipeline: if it
crashes, the minutes keep running. How CoThinker talks to its agent (and
to its speech-to-text engine) without hard-coding either one is described
in [`docs/architecture.md`](docs/architecture.md).

## What CoThinker does not do yet

- **Not a hosted, multi-tenant service.** Today you run your own instance
  against your own configuration; there is no sign-up flow or shared
  deployment.
- **Not yet fully agent-framework-independent.** The agent seam
  (`docs/architecture.md`, §1) is the interface that makes swapping agent
  frameworks possible; only one adapter exists behind it today.
- **Not a finished standalone package.** The core currently assumes it
  runs next to the one agent framework its first adapter talks to; turning
  that into a package anyone can run with any adapter is ongoing work, not
  a finished feature.

## Quickstart

Clone the repository, then run the instance loader's format check against
`instances/example/` — a fully invented instance that exists for exactly
this purpose, with no real deployment's content in it:

```bash
python3 - <<'PY'
import tomllib
from pathlib import Path

instance_dir = Path("instances/example")
config = tomllib.loads((instance_dir / "instance.toml").read_text())["instance"]
print(f"instance: {config['name']}")
print(f"languages: {', '.join(config['languages'])}")

content_files = sorted(p.name for p in (instance_dir / "content").glob("*.json"))
print(f"content files ({len(content_files)}): {', '.join(content_files)}")
PY
```

Only Python 3 and the standard library are needed (tested on 3.11): no
microphone, no agent, no network, no accounts. This reads the same
`instance.toml` format every real deployment uses — see
[`docs/instance-format.md`](docs/instance-format.md) for the full format,
and [`instances/README.md`](instances/README.md) and
[`instances/example/README.md`](instances/example/README.md) for what the
example instance contains and why.

## Where to go next

| You want to… | Read |
|---|---|
| understand how CoThinker talks to its agent and to speech-to-text | [`docs/architecture.md`](docs/architecture.md) |
| understand the instance directory format (configuration and content for one deployment) | [`docs/instance-format.md`](docs/instance-format.md) |
| understand the module format (optional, pluggable session behaviour) | [`docs/module-format.md`](docs/module-format.md) |
| see a complete, invented instance | [`instances/example/README.md`](instances/example/README.md) |
| build a surface (screen, room display, phone view) | [`docs/contributing-surfaces.md`](docs/contributing-surfaces.md) |
| follow what is being built, in plain words | [progress reports](https://cothinker.eu/dev/) |

## Repo map

```
gateway/     speech-block building, agent orchestration, the stage's state rules
stage/       the website: server + HTML/CSS/JS
graph/       builds a note graph from a project's markdown notes
stt/         the bundled speech-to-text server
shared/      the instance and module seams, and other shared building blocks
modules/     optional, pluggable units of session behaviour
instances/   one directory per deployment: configuration and content, no code
tools/       repo maintenance tooling (inventory classifier, publish checks)
tests/       python3 tests/all.py -- standard library only
```

## License and contributing

**AGPL-3.0-only** (see the `LICENSE` file). The AGPL closes the so-called
SaaS loophole: anyone who modifies CoThinker and offers it as a network
service must publish their changes (§13). Anyone who runs it themselves
is not affected and can use and adapt it freely.

Before your first commit, install the guard:

```bash
cp scripts/pre-commit-leak-guard.sh .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

It blocks internal addresses, keys, audio recordings and protocol files.
**Material from real meetings never goes into this repository** — use the
invented example instance or the synthetic fixtures in `tests/` instead.
