# Module format

CoThinker's core (gateway, stage, workshop) is instance-agnostic — see
`shared/instance.py` and the instance seam. Modules are the companion seam:
optional, pluggable units of behaviour that react to what happens in a
session (an utterance, a finished block, a phase change, a cue) without the
core needing to know they exist. The implementation lives in
`shared/modules.py`.

This document describes the on-disk format and the Python contract a module
must meet. It assumes no prior familiarity with the repository.

## Directory layout

A module is a directory with a manifest and a Python entry point:

```
modules/example_echo/
  module.toml
  module.py
  profile/
    SOUL.md
    skills/...
```

The directory name must match `[module].name` when the module is loaded
through an instance: `load_modules` locates a module by trying
`<search-dir>/<name>/module.toml` for each `name` an instance lists, and
raises `ModuleError` if the manifest found there declares a different
name. (`load_module(path)` on its own takes any directory.)

## `module.toml`

```toml
[module]
name = "example_echo"
version = "0.1.0"
api_version = 1
entry = "module:EchoModule"
profile = "profile"
tests = "tests"

[state]
files = ["echo.json"]
```

- `name` — the module's identifier. This is the name an instance's
  `[modules] enabled = [...]` list uses to request the module.
- `version` — the module's own version string. Not interpreted by the
  loader; for humans and changelogs.
- `api_version` — must equal `shared.modules.CORE_API_VERSION` (currently
  `1`). A mismatch is a hard load error (`ModuleError`), not a warning —
  the core and the module must agree on the shape of `CoreEvent`,
  `ModuleContext` and the rest of the seam before anything runs.
- `entry` — `"<file>:<ClassName>"`. `<file>.py` is a Python source file in
  the module directory (for `module:EchoModule` that is `module.py`); the
  loader imports it directly from disk (it does not need to be on
  `sys.path` or be part of an installed package) and looks up `ClassName`
  in it.
- `profile` — the directory, relative to the module directory, holding the
  module's own profile material (a `SOUL.md`, skills, etc.) in whatever
  shape the profile-consuming code expects. The loader resolves this to an
  absolute path and passes it to the entry point as `profile_dir`; it does
  not interpret the profile's contents itself.
- `tests` — optional; the directory, relative to the module directory,
  holding the module's own tests (ADR 0022 level 1: tests live with the
  code). When present, `load_module` checks that the directory exists and
  raises `ModuleError` if it does not — a module that declares a tests
  directory must actually ship it. Omitting the key skips this check.
- `[state].files` — the list of file names the module intends to read and
  write through its `StateStore` (see below). This is documentation for
  humans and tooling, not enforced by the loader: a module is free to call
  `ctx.state.read(name)` / `write(name, data)` with any name, and nothing
  in `shared/modules.py` cross-checks it against this list.

## The entry point class

The class named by `entry` is instantiated by `load_module` with four
keyword arguments:

```python
cls(name=name, api_version=api_version, module_dir=module_dir, profile_dir=profile_dir)
```

- `name`, `api_version` — the values read from `module.toml`, verbatim.
- `module_dir` — the absolute path to the module's own directory (where
  `module.toml` lives).
- `profile_dir` — the absolute path to the module's profile directory
  (`module_dir / profile`).

The resulting instance must satisfy the `Module` protocol in
`shared/modules.py`:

```python
class Module(Protocol):
    name: str
    api_version: int
    def tools(self) -> list[ToolSpec]: ...
    def phases(self) -> list[Phase]: ...
    def profile_dir(self) -> Path: ...
    def on_event(self, ev: CoreEvent, ctx: ModuleContext) -> None: ...
```

This is a `typing.Protocol` — structural, not a base class. A module's
entry point class does not need to import or subclass anything from
`shared/modules.py` to satisfy it; it only needs to have these attributes
and methods with these signatures. A minimal, well-behaved implementation
stores its constructor arguments and returns them from the matching
methods:

```python
class EchoModule:
    def __init__(self, *, name, api_version, module_dir, profile_dir):
        self.name = name
        self.api_version = api_version
        self._profile_dir = profile_dir

    def tools(self) -> list[ToolSpec]:
        return []

    def phases(self) -> list[Phase]:
        return []

    def profile_dir(self) -> Path:
        return self._profile_dir

    def on_event(self, ev: CoreEvent, ctx: ModuleContext) -> None:
        pass
```

### `tools()`

Returns a list of `ToolSpec(name, description, parameters, fn)` the module
offers. `parameters` is a JSON schema describing `fn`'s keyword arguments;
`fn` is a plain callable that returns a `dict`. How these are wired into an
agent's tool list is decided by the code that calls `tools()`, not by
`shared/modules.py` — this seam only describes the module's own shape.

### `phases()`

Returns a list of `Phase(id, titles)` the module contributes to the
session's phase sequence. `titles` maps a language code (matching the
instance's `languages`, e.g. `"de"`, `"en"`) to the phase's display title in
that language — see the naming convention in `AGENTS.md`: a title shown on
a stage surface is German; the `id` itself is an internal token and stays
English.

### `profile_dir()`

Returns the module's profile directory as resolved by the loader (see
above). Calling code reads whatever profile material it expects
(`SOUL.md`, skills, …) from this path; `shared/modules.py` does not look
inside it.

### `on_event(ev, ctx)`

Called once per `CoreEvent` the module is interested in.

- `ev.kind` is one of `"utterance"`, `"block"`, `"phase"`, `"cue"`; `ev.payload`
  is a plain mapping whose shape depends on `kind` and is defined by
  whatever part of the core emits the event (not by `shared/modules.py`).
- `ctx.instance` is the current `InstanceConfig` (`shared/instance.py`) —
  the same object the rest of the core reads instance data through.
- `ctx.state` is a `StateStore` scoped to this module (see below). A
  module reads and writes its own state through it; nothing else writes to
  that directory.
- `ctx.agent`, when present, is a `shared.agent.AgentPort` a module can use
  to talk to the session's agent. It is `None` when no agent is available
  in the calling context — a module must handle that case rather than
  assume it is always set.

`on_event` has no return value. Side effects (state writes, agent calls)
are how a module acts.

## `StateStore`

```python
class StateStore:
    def __init__(self, root: Path): ...
    def read(self, name: str) -> dict          # {} when absent
    def write(self, name: str, data: dict) -> None
```

A thin JSON-file store scoped to one directory (`root`), normally one
directory per module per session. `read(name)` returns `{}` if `root/name`
does not exist yet — a module never needs to special-case "first run".
`write(name, data)` is atomic: it writes to a temporary file in `root` and
`os.replace`s it onto `root/name`, so a reader never sees a half-written
file and an interrupted write never leaves a stray temp file behind.

Who constructs the `StateStore` a module receives via `ModuleContext.state`,
and which directory it points at, is decided by the calling code (the hub
or consumer wiring a module into a running session) — not by
`shared/modules.py` itself.

## Loading modules

```python
def load_module(path: Path) -> Module
def load_modules(cfg: InstanceConfig, search: Sequence[Path]) -> list[Module]
```

- `load_module(path)` reads `<path>/module.toml`, checks `api_version`
  against `CORE_API_VERSION`, imports the entry file and instantiates the
  entry class as described above. A missing manifest, an unreadable
  manifest, an `api_version` mismatch, a declared `tests` directory that
  does not exist, a malformed `entry` string, a missing entry file or
  class, or any exception raised while importing the entry file (a syntax
  error, a failing import) all raise `ModuleError`
  with a message naming the module and the problem. The entry file is
  registered in `sys.modules` as `cothinker_module_<name>` while it runs,
  so ordinary Python inside it (dataclasses, postponed annotations,
  pickling) works as in any imported module.
- `load_modules(cfg, search)` loads every module name in `cfg.modules`
  (`InstanceConfig.modules`, populated from an instance's
  `[modules] enabled = [...]`). For each name, it tries each directory in
  `search`, in order, and uses the first one containing
  `<name>/module.toml`. A name not found in any `search` directory raises
  `ModuleError` naming the module; so does a manifest there whose `name`
  differs from the requested one. This lets an instance's modules come
  from more than one directory (e.g. a shared `modules/` tree plus an
  instance-local override directory) without `shared/modules.py` needing
  to know about either.

## What this seam deliberately does not do

- It does not run a module's code against live sessions, wire it into the
  hub, or decide which events fire when — that is the calling code's job
  (see the proof module and its hub wiring, a separate piece of work).
- It does not validate `[state].files` against what a module actually
  reads or writes.
- It does not enforce anything about what a module puts in its profile
  directory.

These are deliberately left open so the seam stays small and the modules
built on top of it can differ in how they use it.
