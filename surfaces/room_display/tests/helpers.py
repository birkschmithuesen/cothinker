"""Shared helpers of the room display tests."""
from __future__ import annotations

from surfaces.contract.models import StageMain, StageStateSnapshot

VIEW_KEYS = ["live", "topics", "results", "queue", "share", "memory"]


def empty_snapshot() -> StageStateSnapshot:
    """The smallest valid snapshot: every optional field at its default."""
    return StageStateSnapshot(
        version=0, updated="", mirror="", main=StageMain(id="", markdown="", set_at=""),
        parked=0, age_visible_after_s=0, follow_up_after_s=0,
    )


def open_view(page, key: str) -> None:
    """Choose a view the way a person does: M opens the menu, a digit picks."""
    page.keyboard.press("m")
    page.keyboard.press(str(VIEW_KEYS.index(key) + 1))
