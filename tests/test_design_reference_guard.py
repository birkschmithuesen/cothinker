# CoThinker — design reference-image gate: Design-Signoff guard (issue #61)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""The coder must never update a reference PNG themselves -- only a commit
carrying its own `Design-Signoff: #<issue>` trailer line may.
reference_guard.py's pure `violating_commit()` checks one commit's own
(changed_paths, message) against the manifest's protected paths;
`scan_commits_since()` wraps it with real git subprocess calls, exercised
here against this branch's own real history.

    python3 -m pytest -q tests/test_design_reference_guard.py
"""
from __future__ import annotations

from pathlib import Path

from tools.design_gate import reference_diff, reference_guard

REPO = reference_guard.REPO


def test_violating_commit_and_trailer_own_line_matching():
    protected = {"surfaces/widget/reference/default.png", "surfaces/widget/reference/hover.png"}

    assert reference_guard.violating_commit(
        {"workshop/static/table.js"}, "fix: something\n", protected) is None

    assert reference_guard.violating_commit(
        {"surfaces/widget/reference/default.png"},
        "feat(design-signoff): approve new default capture\n\n"
        "Design-Signoff: #61\n", protected) is None

    violation = reference_guard.violating_commit(
        {"surfaces/widget/reference/default.png", "workshop/static/table.js"},
        "fix: tweak the default reference a bit\n", protected)
    assert violation == {"surfaces/widget/reference/default.png"}

    assert reference_guard.violating_commit(
        {"surfaces/widget/reference/default.png"},
        "fix: this commit message happens to mention Design-Signoff in prose, "
        "no real trailer though\n", protected) is not None

    assert reference_guard.violating_commit(
        {"surfaces/widget/reference/default.png", "surfaces/widget/reference/hover.png"},
        "fix: both\n", protected) == protected


def test_manifest_reference_paths_and_scan_commits_since_real_history(tmp_path):
    surface_dir = tmp_path / "surfaces" / "widget"
    (surface_dir / "reference").mkdir(parents=True)
    (surface_dir / "states.toml").write_text('states = ["default"]\n', encoding="utf-8")
    ref_png = surface_dir / "reference" / "default.png"
    ref_png.write_bytes(b"not a real png, path only matters here")
    manifest_path = surface_dir / "reference" / "manifest.toml"
    manifest_path.write_text(
        "[[reference]]\n"
        'id = "widget-default"\n'
        'state = "default"\n'
        'device = "phone"\n'
        f'reference = "{ref_png}"\n'
        'design_source_commit = "0000000 (test fixture)"\n'
        "masks = []\n",
        encoding="utf-8")
    entries = reference_diff.load_manifest(manifest_path)

    protected = reference_guard.manifest_reference_paths(entries, repo=tmp_path)
    assert protected == {str(ref_png.relative_to(tmp_path))}

    real_entries = []
    real_protected = reference_guard.manifest_reference_paths(real_entries)
    violations = reference_guard.scan_commits_since("main", real_protected)
    assert violations == []


def test_resolve_ref_falls_back_to_fetching_from_origin_when_nothing_local():
    """A shallow CI checkout (`actions/checkout`'s default, no
    `fetch-depth: 0`) has neither a local `main` branch nor an
    `origin/main` remote-tracking ref -- only the PR branch itself was
    fetched. `main` alone must not raise `fatal: bad revision`; the lookup
    must fall back all the way to fetching it from `origin`.
    """
    import subprocess

    local_exists = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", "main"],
        cwd=REPO, capture_output=True, text=True).returncode == 0
    if local_exists:
        import pytest
        pytest.skip("this worktree has a local 'main' branch (shares .git with "
                     "the main checkout); the fallback path is not exercised here "
                     "-- see test_resolve_ref_rejects_unknown_ref for the pure case")

    resolved = reference_guard._resolve_ref("main", repo=REPO)
    assert resolved in ("origin/main", "FETCH_HEAD")


def test_resolve_ref_rejects_unknown_ref():
    import pytest

    with pytest.raises(ValueError, match="cannot resolve base ref"):
        reference_guard._resolve_ref("no-such-branch-anywhere", repo=REPO)
