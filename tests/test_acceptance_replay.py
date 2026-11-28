"""Level-1 tests for the offline transcript replay (real consumer, fake STT)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from demo.acceptance.catalogue import load_all  # noqa: E402
from demo.acceptance.replay import render_finals, run  # noqa: E402

SCEN = load_all(ROOT / "demo" / "acceptance" / "scenarios")


def test_render_finals_scenario_a_keeps_the_pause():
    finals = render_finals(SCEN["a"])
    assert finals[0]["text"] == "Hey Robo."
    assert finals[0]["type"] == "final" and finals[0]["speaker"] == "Alex"
    gap = finals[1]["t_start"] - finals[0]["t_end"]
    assert gap == pytest.approx(4.0)
    # consecutive say steps abut
    assert finals[2]["t_start"] == pytest.approx(finals[1]["t_end"])
    assert finals[0]["t_end"] - finals[0]["t_start"] == pytest.approx(2 / 2.5)


def test_render_finals_scenario_g_overlap_starts_early():
    finals = render_finals(SCEN["g"])
    assert finals[1]["t_start"] < finals[0]["t_end"]
    assert finals[1]["t_start"] == pytest.approx(finals[0]["t_end"] - 1.5)
    assert finals[1]["speaker"] == "Sam"


def test_render_finals_rejects_clip_steps():
    with pytest.raises(ValueError, match="clip"):
        render_finals(SCEN["c"])


def test_replay_scenario_h_makes_exactly_one_lane_call(tmp_path):
    obs = run(SCEN["h"], repo=ROOT, workdir=tmp_path, speed=4.0, timeout_s=50)
    assert len(obs.lane_calls) == 1
    assert "fass" in obs.lane_calls[0].lower()
    assert any("fass" in text.lower() for _, text in obs.transcript_lines)


def test_replay_scenario_g_makes_exactly_one_lane_call(tmp_path):
    obs = run(SCEN["g"], repo=ROOT, workdir=tmp_path, speed=4.0, timeout_s=50)
    assert len(obs.lane_calls) == SCEN["g"].expect["lane_calls"]["count"]
    assert "planung" in obs.lane_calls[0].lower()


def test_every_ordering_scenario_has_a_say_step_with_the_wake_phrase():
    from shared.wake_word import CLAUSE_LEAD, WAKE_NOT, WAKE_PHRASE_RE
    for sid, s in SCEN.items():
        if not ({"lane_calls", "answer"} & set(s.expect)):
            continue
        woken = []
        for st in s.timeline:
            if "say" not in st:
                continue
            m = WAKE_PHRASE_RE.match(CLAUSE_LEAD.sub("", st["say"]))
            woken.append(bool(m) and not WAKE_NOT.match(m.group("name")))
        assert any(woken), f"scenario {sid} never says the wake phrase"


_PROMPT = (
    "## Agenda\n- Budget\n\n"
    "## Verlauf\nAlex: Wir sprechen ueber Infrastruktur und Server.\n"
    "Robo (Zuruf): Robo: Zusammenfassung läuft …\n\n"
    "## Auftrag\nThema: x\nBudget note\nRegeln\nbitte kurz antworten\n"
    "{order}")


def test_order_text_is_the_last_line_of_the_auftrag_section():
    from demo.acceptance.replay import order_text
    prompt = _PROMPT.format(order="Fass bitte alles zusammen.")
    assert order_text(prompt) == "Fass bitte alles zusammen."


def test_order_text_excludes_verlauf_and_ack():
    from demo.acceptance.replay import order_text
    out = order_text(_PROMPT.format(order="Robo, Ende."))
    assert "Infrastruktur" not in out and "Zusammenfassung" not in out


def test_order_text_keeps_a_merged_block():
    from demo.acceptance.replay import order_text
    sys.path.insert(0, str(ROOT / "gateway"))
    from gateway.call_lane import CALL_MERGED_HEADER
    block = CALL_MERGED_HEADER + "\n1. eins\n2. zwei"
    assert order_text(_PROMPT.format(order=block)) == block


def test_order_text_without_the_section_is_returned_unchanged():
    from demo.acceptance.replay import order_text
    assert order_text("plain") == "plain"


def test_merged_mark_matches_the_gateway_header():
    sys.path.insert(0, str(ROOT / "gateway"))
    from gateway.call_lane import CALL_MERGED_HEADER
    from demo.acceptance.replay import _MERGED_MARK
    assert CALL_MERGED_HEADER.startswith(_MERGED_MARK)
