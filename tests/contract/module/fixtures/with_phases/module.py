"""Contract-test fixture: a module whose `phases()` is non-empty.

`modules/example_echo` (the only other built-in subject) always returns
`[]` from `phases()`, so the MOD-PHASES loop in
`test_its_phases_have_an_id_and_titles` never runs its body for that
subject. This fixture exists only to exercise that rule against a real
`phases()` return value -- it is not a real module.
"""
from __future__ import annotations

from pathlib import Path

from shared.modules import CoreEvent, ModuleContext, Phase, ToolSpec


class WithPhasesModule:
    def __init__(self, *, name: str, api_version: int, module_dir: Path, profile_dir: Path) -> None:
        self.name = name
        self.api_version = api_version
        self._profile_dir = profile_dir

    def tools(self) -> list[ToolSpec]:
        return []

    def phases(self) -> list[Phase]:
        return [Phase(id="intro", titles={"en": "Intro"})]

    def profile_dir(self) -> Path:
        return self._profile_dir

    def on_event(self, ev: CoreEvent, ctx: ModuleContext) -> None:
        pass
