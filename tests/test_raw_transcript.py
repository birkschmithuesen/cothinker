"""Session host's minutes file: raw appends and spliced block sections."""
from __future__ import annotations

import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gateway.block_host_wiring import (  # noqa: E402
    build_session_blocks, feed_utterance)
from gateway.raw_transcript import MinutesFile  # noqa: E402
from shared.blocks import BLOCK_API_VERSION  # noqa: E402

FRONT = "# Titel\n\n## Verlauf\n\n## Entscheidungen\n\n"
RAW_HEAD = "## Rohtranskript\n"


def _make(tmp_path: Path, front: str = FRONT) -> MinutesFile:
    path = tmp_path / "protokoll.md"
    path.write_bytes((front + RAW_HEAD).encode("utf-8"))
    return MinutesFile(path, threading.RLock())


def test_raw_append_survives_block_crash(tmp_path):
    mf = _make(tmp_path)

    class Crashing:
        id = "capture-bullets"
        api_version = BLOCK_API_VERSION

        def start(self, ctx, config):
            pass

        def on_event(self, ev):
            raise RuntimeError("block crashed")

        def stop(self):
            pass

    logs: list[str] = []
    blocks = build_session_blocks(
        session_dir=tmp_path / "sess", sink=lambda intent: None,
        log=logs.append, registry={"capture-bullets": Crashing},
        clock=lambda: 0.0)
    feed_utterance(mf, blocks, "Wir nehmen Option B", "Anna", {})
    data = mf.path.read_text(encoding="utf-8")
    assert "*Anna:* Wir nehmen Option B" in data
    assert data.index(RAW_HEAD) < data.index("Wir nehmen Option B")
    assert any("block crashed" in line for line in logs), logs


def test_sections_spliced_before_raw(tmp_path):
    mf = _make(tmp_path)
    mf.append_raw("eins", None)
    mf.write_sections("# Titel\n\n## Verlauf\n\n- 10:00 neu\n")
    data = mf.path.read_text(encoding="utf-8")
    assert data.startswith("# Titel\n\n## Verlauf\n\n- 10:00 neu\n\n## Rohtranskript\n")
    assert data.count(RAW_HEAD) == 1
    assert "## Entscheidungen" not in data
    assert data.endswith(" eins\n")


def test_raw_section_byte_identical_after_section_write(tmp_path):
    mf = _make(tmp_path)
    mf.append_raw("zeile eins", "Ben")
    mf.append_raw("zeile zwei", None)
    # Stray bytes and CRLF inside the raw section must survive untouched.
    with open(mf.path, "ab") as f:
        f.write(b"- **12:00** crlf line\r\n- raw \xff\xfe bytes\n")

    def raw_of(blob: bytes) -> bytes:
        return blob[blob.index(RAW_HEAD.encode()):]

    before = raw_of(mf.path.read_bytes())
    mf.write_sections("# Titel\n\n## Verlauf\n\n- 10:01 a\n")
    mf.write_sections("# Anderer Titel\r\n\r\n## Aufgaben\n\n- x\n")
    after = raw_of(mf.path.read_bytes())
    assert after == before
    assert not list(tmp_path.glob(".*.tmp"))
