"""Level-4 scenario catalogue: loader, validation and threshold resolution.

Scenarios live as one YAML file each under ``demo/acceptance/scenarios/``;
measured numbers live in ``demo/acceptance/baseline.yaml``. This module only
loads and validates them; it never plays audio or talks to a deployment.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

REQUEST_CLASSES = (
    "order-with-pause",
    "answer-from-files",
    "natural-segmentation",
    "wake-never-topic",
    "structure-a-list",
    "interruption",
    "overlap",
    "order-one-breath",
)

BASELINE_PREFIX = "baseline:"

_REQUIRED = ("id", "title", "request_class", "speakers", "timeline", "expect", "timeouts")
_OPTIONAL = ("regression_only", "gaps", "judge")

_EXPECT_KEYS = {
    "lane_calls": {"count", "keywords_all"},
    "answer": {"must_not", "keywords_all", "agenda_line"},
    "topic": {"must_not_contain"},
    "transcript": {"mid_sentence_max", "speakers_all", "keywords_all"},
}
_STEP_KINDS = ("say", "pause", "clip")
_STEP_KEYS = {"speaker", "say", "pause", "clip", "overlap_prev_s"}


class CatalogueError(ValueError):
    """The scenario catalogue or baseline file is malformed."""


class MissingThreshold(KeyError):
    """A ``baseline:<key>`` reference has neither a baseline value nor an override."""

    def __init__(self, key: str):
        super().__init__(key)
        self.key = key

    def __str__(self) -> str:
        return f"no baseline value or override for threshold {self.key!r}"


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    request_class: str
    regression_only: bool
    speakers: tuple
    timeline: tuple
    gaps: dict | None
    expect: dict
    judge: dict | None
    timeouts: dict


def _fail(where: str, msg: str) -> CatalogueError:
    return CatalogueError(f"{where}: {msg}")


def _check_keys(where: str, data: dict, allowed: Iterable[str], required: Iterable[str] = ()) -> None:
    allowed = set(allowed)
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise _fail(where, f"unknown key(s) {unknown}")
    missing = [k for k in required if k not in data]
    if missing:
        raise _fail(where, f"missing required key(s) {missing}")


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _parse(where: str, data: Any) -> Scenario:
    if not isinstance(data, dict):
        raise _fail(where, "scenario must be a mapping")
    _check_keys(where, data, _REQUIRED + _OPTIONAL, _REQUIRED)
    if not isinstance(data["id"], str) or not data["id"]:
        raise _fail(where, "id must be a non-empty string")
    if data["request_class"] not in REQUEST_CLASSES:
        raise _fail(where, f"unknown request_class {data['request_class']!r}")
    regression = data.get("regression_only", False)
    if not isinstance(regression, bool):
        raise _fail(where, "regression_only must be a boolean")

    speakers = data["speakers"]
    if not isinstance(speakers, list) or not speakers:
        raise _fail(where, "speakers must be a non-empty list")
    names = set()
    for sp in speakers:
        if not isinstance(sp, dict):
            raise _fail(where, "each speaker must be a mapping")
        _check_keys(where + " speaker", sp, {"name", "voice"}, {"name", "voice"})
        names.add(sp["name"])

    timeline = data["timeline"]
    if not isinstance(timeline, list) or not timeline:
        raise _fail(where, "timeline must be a non-empty list")
    for i, step in enumerate(timeline):
        sw = f"{where} timeline[{i}]"
        if not isinstance(step, dict):
            raise _fail(sw, "step must be a mapping")
        _check_keys(sw, step, _STEP_KEYS)
        kinds = [k for k in _STEP_KINDS if k in step]
        if len(kinds) != 1:
            raise _fail(sw, "step needs exactly one of say / pause / clip")
        if kinds[0] == "pause":
            if not _is_num(step["pause"]) or step["pause"] < 0:
                raise _fail(sw, "pause must be a non-negative number")
            if "speaker" in step or "overlap_prev_s" in step:
                raise _fail(sw, "a pause step takes no speaker or overlap_prev_s")
        else:
            if step.get("speaker") not in names:
                raise _fail(sw, f"speaker {step.get('speaker')!r} is not declared in speakers")
            if "overlap_prev_s" in step and (not _is_num(step["overlap_prev_s"]) or step["overlap_prev_s"] < 0):
                raise _fail(sw, "overlap_prev_s must be a non-negative number")

    gaps = data.get("gaps")
    if gaps is not None:
        if not isinstance(gaps, dict):
            raise _fail(where, "gaps must be a mapping or null")
        _check_keys(where + " gaps", gaps, {"profile", "seed"}, {"profile"})

    expect = data["expect"]
    if not isinstance(expect, dict) or not expect:
        raise _fail(where, "expect must be a non-empty mapping")
    for group, body in expect.items():
        if group not in _EXPECT_KEYS:
            raise _fail(where, f"unknown expect group {group!r}")
        if not isinstance(body, dict) or not body:
            raise _fail(where, f"expect.{group} must be a non-empty mapping")
        _check_keys(f"{where} expect.{group}", body, _EXPECT_KEYS[group])

    judge = data.get("judge")
    if judge is not None:
        if not isinstance(judge, dict):
            raise _fail(where, "judge must be a mapping or null")
        _check_keys(where + " judge", judge, {"question"}, {"question"})
        if not isinstance(judge["question"], str) or not judge["question"].strip():
            raise _fail(where, "judge.question must be a non-empty string")

    timeouts = data["timeouts"]
    if not isinstance(timeouts, dict):
        raise _fail(where, "timeouts must be a mapping")
    _check_keys(where + " timeouts", timeouts, {"answer_s", "scenario_s"}, {"answer_s", "scenario_s"})
    if not all(_is_num(v) and v > 0 for v in timeouts.values()):
        raise _fail(where, "timeouts must be positive numbers")

    return Scenario(
        id=data["id"],
        title=data["title"],
        request_class=data["request_class"],
        regression_only=regression,
        speakers=tuple(speakers),
        timeline=tuple(timeline),
        gaps=gaps,
        expect=expect,
        judge=judge,
        timeouts=timeouts,
    )


def load_all(dir: Path) -> dict[str, Scenario]:
    """Load every ``*.yaml`` scenario in ``dir``, keyed by scenario id."""
    out: dict[str, Scenario] = {}
    for path in sorted(Path(dir).glob("*.yaml")):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise CatalogueError(f"{path.name}: invalid YAML: {exc}") from exc
        scen = _parse(path.name, data)
        if scen.id in out:
            raise CatalogueError(f"{path.name}: duplicate scenario id {scen.id!r}")
        out[scen.id] = scen
    return out


def load_baseline(path: Path) -> dict:
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CatalogueError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise CatalogueError(f"{path}: baseline must be a mapping")
    return data


def resolve(value: Any, baseline: dict, overrides: dict[str, float]) -> Any:
    """Resolve ``"baseline:<key>"`` against ``baseline["thresholds"]``, then ``overrides``.

    Any other value passes through unchanged.
    """
    if not isinstance(value, str) or not value.startswith(BASELINE_PREFIX):
        return value
    key = value[len(BASELINE_PREFIX):]
    entry = (baseline.get("thresholds") or {}).get(key)
    if entry is not None:
        return entry["value"] if isinstance(entry, dict) and "value" in entry else entry
    if key in overrides:
        return overrides[key]
    raise MissingThreshold(key)


def _walk(node: Any) -> Iterable[str]:
    if isinstance(node, str):
        if node.startswith(BASELINE_PREFIX):
            yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, (list, tuple)):
        for v in node:
            yield from _walk(v)


def required_thresholds(scenarios: Iterable[Scenario]) -> set[str]:
    """Every ``"baseline:<key>"`` string anywhere in the scenarios' expect blocks."""
    found: set[str] = set()
    for s in scenarios:
        found.update(_walk(s.expect))
    return found
