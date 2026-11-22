"""Tests for target.md A.7: Network seams paragraph (§1) and ADR 0017 row (§10)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET_MD = ROOT / "docs" / "architecture" / "target.md"


def _text():
    return TARGET_MD.read_text(encoding="utf-8")


def test_layers_section_names_fastapi_as_the_network_seam():
    text = _text()
    assert text.count("FastAPI") >= 1


def test_adr_0017_is_referenced_at_least_twice():
    text = _text()
    assert text.count("0017") >= 2


def test_adr_table_has_a_row_for_0017():
    text = _text()
    assert "[0017](../adr/0017-network-seams-fastapi-blocks-in-process.md)" in text
    assert "| §1 |" in text.splitlines()[
        [i for i, line in enumerate(text.splitlines()) if "0017" in line and "../adr/" in line][0]
    ]


def test_network_seams_paragraph_sits_in_layers_section():
    text = _text()
    layers_start = text.index("## 1. Layers (ADR 0001)")
    next_section = text.index("## 2. The five core APIs (ADR 0002)")
    layers_section = text[layers_start:next_section]
    assert "Network seams" in layers_section
    assert "FastAPI" in layers_section
    assert "0017" in layers_section
