"""Tests for tools/publish_check.py's image-source rule.

The text-only scan (`scan()` over `history_lines`/`tree_lines`) cannot see
text baked into a raster image, so a committed PNG under `docs/` with no
text source next to it (`.html`, `.svg`, `.py`, `.md`) can carry names the
publish check will never catch. `missing_image_sources()` is a separate,
structural check over the tracked file list: a raster image under `docs/`
is a hit unless a sibling file with the same stem and one of the text
extensions is tracked next to it.
"""
import subprocess

from publish_check import missing_image_sources


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _init_repo(repo):
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "-c", "user.email=t@example.org", "-c", "user.name=t",
         "config", "user.email", "t@example.org")
    _git(repo, "config", "user.name", "t")


def test_png_under_docs_without_sibling_source_is_flagged(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "docs").mkdir()
    (repo / "docs" / "orphan.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "add orphan png")

    hits = missing_image_sources(repo)

    assert "docs/orphan.png" in hits


def test_png_under_docs_with_sibling_html_is_not_flagged(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "docs").mkdir()
    (repo / "docs" / "diagram.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (repo / "docs" / "diagram.html").write_text("<html></html>")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "add diagram png with html source")

    hits = missing_image_sources(repo)

    assert "docs/diagram.png" not in hits


def test_png_outside_docs_without_sibling_source_is_not_flagged(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "tests" / "golden").mkdir(parents=True)
    (repo / "tests" / "golden" / "fixture.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "add test fixture png")

    hits = missing_image_sources(repo)

    assert hits == []
