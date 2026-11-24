# Contract tests

A contract test is level 2 of ADR 0022: it proves that one implementation of
a core API keeps that API's contract. It is written so that it takes the
implementation as a parameter -- an outside implementation runs the **same**
test, not a copy of it (ADR 0022 rule 3, as ADR 0019 does for surfaces). The
behaviour each test checks is specified in `docs/architecture/target.md` §2;
the tests cite the section, they do not restate it.

The tests live in `tests/contract/<api>/` and run on every commit with the
rest of the suite. They never touch the network: a guard in
`tests/contract/conftest.py` refuses every socket connection that does not go
to the loopback interface, so a test may start a fake server on `127.0.0.1`
and nothing else.

## Running a contract against your own implementation

Every contract test module runs once per built-in implementation. To add
yours, name it in one environment variable and run the API's directory:

```bash
COTHINKER_CONTRACT_<API>=<subject> python3 -m pytest -q tests/contract/<api>
```

Your run shows up with the test id `outside`; add `-k outside` to run only
yours. The built-in runs stay in, so a red run is easy to compare with a
green one. Every assertion message starts with a rule id (`AGENT-TERMINAL`,
`MOD-PROFILE`, `INST-VALIDATE`, ...): a red run names the rule that broke.
Each API's test module also carries one deliberately broken fake and proves
that the contract fails it for exactly the rule that fake breaks.

## Agent API (`tests/contract/agent/`, target.md §2.2)

`COTHINKER_CONTRACT_AGENT=package.module:make`. `make(scenario)` is a
callable that returns a context manager yielding an `AgentPort` whose next
`send_turn` plays the named scenario: `reply` (a plain answer), `tool` (one
tool call, then the answer), `failed` (the backend reports a failed run),
`failure_marker` (the backend reports success, but the reply text carries one
of `shared.agent.FAILURE_MARKERS`) and `cancelled` (the backend reports the
run cancelled). The contract: every event kind is one of `EVENT_KINDS`;
exactly one terminal event (`done` or `failed`) and it comes last;
`tool_started`/`tool_finished` and `subagent_started`/`subagent_finished`
pair up by tool name, with nothing left open at `done`; a reply carrying a
failure marker ends `failed`, never `done`; `stop()` is safe while idle, in the
middle of a turn and twice, and the turn still ends with one terminal event.
Your module has to be importable: put its directory on `PYTHONPATH`, next to
the repository root. The built-ins are `FakeAgentPort` and
`HermesApiAgentPort` against a fake Hermes API server on `127.0.0.1`.

## Module API (`tests/contract/module/`, target.md §2.4, `docs/module-format.md`)

`COTHINKER_CONTRACT_MODULE=<path to the module directory>`. The contract
covers the format as it is declared today (`api_version = 1`): the manifest
keys and their shapes; the module loads and reports the core's
`api_version`; a copy of it with another `api_version` is refused by
`load_module`; `tools()` returns valid tool specs (unique names, a
description, a callable, a JSON schema as `parameters`); `phases()` returns
phases with an id and titles; `profile_dir()` is the declared profile
directory and exists; on every core event kind the module writes only the
files its `[state].files` declares, only into its own state root, and nothing
into its own directory. The `StateStore` it is handed keeps two roots apart
and keeps the old file when a write fails. `provides` and `api_version = 2`
(target.md §2.4) come with Module API v2. The built-in is
`modules/example_echo`.

## Instance API (`tests/contract/instance/`, target.md §2.5, `docs/instance-format.md`)

`COTHINKER_CONTRACT_INSTANCE=<path to the instance directory>`. The contract:
`shared.instance.validate()` reports nothing; `load()` reads the manifest's
name and a non-empty language list; `python3 -m shared.instance check <dir>`
prints `ok` and exits 0; a content, profile or acceptance path that does not
exist is an `InstanceError`, never a guessed path; the directory holds no
audio. The built-ins are `instances/example`, `instances/example-workshop`
and `instances/example-production`.

## Surface API (`tests/contract/surface/`, target.md §2.3, `docs/surface-contract.md`)

`COTHINKER_CONTRACT_SURFACE=<path to the surface's index.html>`. This is the
conformance check of ADR 0019 (it moved here from
`tests/test_surface_conformance.py`): the surface is served next to the
contract server, the `meeting` fixture is replayed into it, and it must pass
every check of `surfaces/contract/check.py`. The same check runs on its own,
with a report and screenshots, as
`python -m surfaces.contract.check --surface <path or URL>`. The built-in is
`surfaces/contract/examples/minimal/`; the broken fake is
`tests/contract/surface/broken_surface/`.

## STT API (target.md §2.1)

The STT contract follows once the STT API (`shared/stt.py`, `open_stt`) is on
`main`; it is tracked as its own issue, linked from the issue that added this
page. It will take `COTHINKER_CONTRACT_STT=package.module:make` in the same
way as the agent contract.
