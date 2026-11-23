import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "demo" / "acceptance"))

import score as sc  # noqa: E402

SCRIPT = {"events": [
    {"speaker": "Alex", "keywords": ["Apfelbaum"]},
    {"speaker": "Sam", "keywords": ["Fahrrad"]},
]}


def L(spk, text):
    return "- **16:28** *%s:* %s" % (spk, text)


def test_parse_line_sample():
    assert sc.parse_line(
        "- **16:28** *Alex:* Guten. Tag. Ich bin Alex.") == (
        "Alex", "Guten. Tag. Ich bin Alex.")
    assert sc.parse_line("## Rohtranskript") is None


def test_all_correct():
    r = sc.score([L("Alex", "Der Apfelbaum blüht."),
                  L("Sam", "Das Fahrrad ist kaputt.")], SCRIPT)
    assert (r["matched"], r["correct"], r["ratio"]) == (2, 2, 1.0)


def test_lines_without_keyword_are_ignored():
    r = sc.score([L("Alex", "Der Apfelbaum."), L("S1", "Hallo zusammen."),
                  "kein Transkript"], SCRIPT)
    assert r["matched"] == 1 and r["correct"] == 1 and r["lines"] == 2


def test_wrong_attribution_counted():
    r = sc.score([L("S1", "Der Apfelbaum."), L("Alex", "Das Fahrrad")], SCRIPT)
    assert r["matched"] == 2 and r["correct"] == 0 and r["ratio"] == 0.0
    # ASR word splits still match the keyword
    assert sc.score([L("Sam", "Fahr rad")], SCRIPT)["correct"] == 1


def test_90_percent_boundary():
    good = [L("Alex", "Apfelbaum")] * 9
    nine = sc.score(good + [L("Sam", "Apfelbaum")], SCRIPT)
    assert nine["ratio"] == 0.9 and sc.passes(nine)
    eight = sc.score(good[:8] + [L("Sam", "Apfelbaum")] * 2, SCRIPT)
    assert eight["ratio"] == 0.8 and not sc.passes(eight)
    assert not sc.passes(sc.score([], SCRIPT))


def test_raw_section_only():
    text = "# P\n## Rohtranskript\n" + L("Alex", "Apfelbaum") + "\n## Rest\n" + \
        L("Sam", "Apfelbaum") + "\n"
    assert len(sc.raw_transcript_lines(text)) == 1
