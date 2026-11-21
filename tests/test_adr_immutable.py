"""Tests for scripts/check_adr_immutable.py (accepted ADRs are superseded, never rewritten)."""
import subprocess
from pathlib import Path

from check_adr_immutable import check_repo, is_accepted, violations

ROOT = Path(__file__).resolve().parent.parent
ADR_DIR = ROOT / "docs" / "adr"

BASE = """# ADR 0099: Example

- **Status:** accepted
- **Date:** 2026-11-20

## Decision

Use only EU-hosted providers.
"""


def test_status_line_change_is_allowed():
    new = BASE.replace("- **Status:** accepted", "- **Status:** superseded by ADR 0100")
    assert violations("0099.md", BASE, new) == []


def test_rewriting_the_decision_is_a_violation():
    new = BASE.replace("Use only EU-hosted providers.", "Use EU or Swiss-hosted providers.")
    found = violations("0099.md", BASE, new)
    assert len(found) == 2  # one removed + one added line
    assert all("accepted ADR" in f for f in found)


def test_added_clarification_is_allowed():
    new = BASE + "\nClarification: 'EU' includes the EEA.\n"
    assert violations("0099.md", BASE, new) == []


def test_added_consequence_is_allowed():
    new = BASE + "\nConsequence (added 2026-11-21): vision needs its own provider.\n"
    assert violations("0099.md", BASE, new) == []


def test_other_added_paragraph_is_a_violation():
    new = BASE + "\nAlso Swiss providers are fine.\n"
    assert violations("0099.md", BASE, new) != []


def test_proposed_adr_may_be_edited():
    base = BASE.replace("accepted", "proposed")
    new = base.replace("Use only EU-hosted providers.", "Anything goes.")
    assert violations("0099.md", base, new) == []


def test_deleting_accepted_adr_is_a_violation():
    assert violations("0099.md", BASE, None) != []


def test_is_accepted():
    assert is_accepted(BASE)
    assert not is_accepted(BASE.replace("accepted", "proposed"))
    assert is_accepted(BASE.replace("accepted", "superseded by ADR 0100"))


def test_adr_0007_stays_proposed_and_is_editable():
    path = ADR_DIR / "0007-coupling-rule-blocks-surfaces.md"
    text = path.read_text(encoding="utf-8")
    assert not is_accepted(text)


def test_adr_0001_is_accepted():
    path = ADR_DIR / "0001-instance-core-module-layering.md"
    text = path.read_text(encoding="utf-8")
    assert is_accepted(text)


def test_check_repo_base_without_adr_dir_does_not_crash():
    """A base revision that predates docs/adr/ (e.g. the public export's first
    commit) has nothing to protect yet -- check_repo() must not crash on the
    git ls-tree failure, it must treat it as an empty ADR set."""
    merge_base = subprocess.run(
        ["git", "rev-list", "--max-parents=0", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip().splitlines()[-1]
    assert check_repo(merge_base) == []
