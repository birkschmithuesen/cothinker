# CoThinker — tests of block building (segmenter)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Tests of block building — with a simulated clock, run in milliseconds.

    python3 -m pytest -q tests/test_segmenter.py
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gateway.blocks import BlockBuilder  # noqa: E402


class Clock:
    """Controllable time — no sleep in tests."""
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


def build(**kw):
    clock = Clock()
    emitted: list = []
    bb = BlockBuilder(output=emitted.append, now=clock, **kw)
    return clock, emitted, bb


# 1 — pause cuts
def _pause_cut():
    clock, emitted, bb = build(pause_s=2.5)
    bb.sentence("Also ich wuerde vorschlagen wir fangen mit dem Budget an.")
    clock.advance(0.8)
    bb.sentence("Wir haben insgesamt zwoelftausend Euro zur Verfuegung.")
    clock.advance(3.0)          # pause
    bb.tick()
    return emitted


def test_pause_cuts() -> None:
    assert len(_pause_cut()) == 1


def test_both_sentences_in_the_block() -> None:
    emitted = _pause_cut()
    assert emitted and "Budget" in emitted[0].text and "zwoelftausend" in emitted[0].text


def test_reason_is_pause() -> None:
    emitted = _pause_cut()
    assert emitted and emitted[0].reason == "pause"


# 2 — short pauses do NOT cut
def _short_pauses():
    clock, emitted, bb = build(pause_s=2.5)
    for s in ["Erster Satz mit genug Zeichen drin.", "Zweiter Satz auch lang genug.",
              "Dritter Satz ebenfalls ausreichend."]:
        bb.sentence(s)
        clock.advance(0.7)
    bb.tick()
    return emitted, bb


def test_short_pauses_dont_cut() -> None:
    assert len(_short_pauses()[0]) == 0


def test_everything_open() -> None:
    assert _short_pauses()[1].open_chars > 80


# 3 — wake word cuts IMMEDIATELY and marks
def _wake_cut():
    clock, emitted, bb = build(pause_s=2.5)
    bb.sentence("Wir sollten das nochmal genauer durchrechnen.")
    clock.advance(0.5)
    bb.sentence("Robo, nimm dir die Use Cases nochmal alle vor.")
    return emitted


def test_wake_word_cuts_immediately() -> None:
    assert len(_wake_cut()) == 1


def test_wake_word_marks() -> None:
    emitted = _wake_cut()
    assert emitted and emitted[0].wake_word


def test_reason_is_wake() -> None:
    emitted = _wake_cut()
    assert emitted and emitted[0].reason == "wake"


def test_prior_sentence_carried_into_the_block() -> None:
    emitted = _wake_cut()
    assert emitted and "durchrechnen" in emitted[0].text


# 4 — wake word only on a word boundary (no false alarm)
def test_no_false_alarm_on_robotik_robert() -> None:
    clock, emitted, bb = build(pause_s=2.5)
    bb.sentence("Das Thema Robotik interessiert mich sehr an dieser Stelle.")
    clock.advance(0.3)
    bb.sentence("Robert hat dazu auch schon etwas gesagt gehabt.")
    assert len(emitted) == 0


# 5 — length limit kicks in without a pause
def _length_cut():
    clock, emitted, bb = build(pause_s=2.5, max_s=90)
    for i in range(40):
        bb.sentence(f"Das ist Satz Nummer {i} in einem sehr langen Monolog ohne Pause.")
        clock.advance(2.4)                   # always just under the pause threshold
    return emitted


def test_length_cuts() -> None:
    assert len(_length_cut()) >= 1


def test_reason_is_length() -> None:
    emitted = _length_cut()
    assert emitted and emitted[0].reason == "length"


def test_block_not_overlong() -> None:
    emitted = _length_cut()
    assert emitted and (emitted[0].end - emitted[0].start) <= 92


# 6 — short interjections are appended, not sent alone
def test_short_interjection_doesnt_go_out() -> None:
    clock, emitted, bb = build(pause_s=2.5, min_chars=25)
    bb.sentence("Genau.")
    clock.advance(3.0)
    bb.tick()
    assert len(emitted) == 0


def test_interjection_attaches_to_the_next_block() -> None:
    clock, emitted, bb = build(pause_s=2.5, min_chars=25)
    bb.sentence("Genau.")
    clock.advance(3.0)
    bb.tick()
    bb.sentence("Und deshalb sollten wir den Antrag frueher einreichen als geplant.")
    clock.advance(3.0)
    bb.tick()
    assert len(emitted) == 1 and "Genau." in emitted[0].text


# 7 — wake word beats the minimum length
def test_short_order_still_goes_out() -> None:
    clock, emitted, bb = build(pause_s=2.5, min_chars=25)
    bb.sentence("Robo, stopp.")
    assert len(emitted) == 1 and emitted[0].wake_word


# 8 — finish() emits the remainder
def test_finish_emits_the_remainder() -> None:
    clock, emitted, bb = build(pause_s=2.5, min_chars=25)
    bb.sentence("Kurz.")
    bb.finish()
    assert len(emitted) == 1


# 9 — empty and whitespace-only sentences are ignored
def test_empty_sentences_ignored() -> None:
    clock, emitted, bb = build()
    bb.sentence("")
    bb.sentence("   ")
    bb.finish()
    assert len(emitted) == 0


# 10 — realistic conversation: numbers survive
def _conversation():
    clock, emitted, bb = build(pause_s=2.5)
    gespraech = [
        ("Also ich wuerde vorschlagen wir fangen mit dem Budget an.", 0.9),
        ("Wir haben insgesamt zwoelftausend Euro zur Verfuegung.", 3.5),
        ("Nina uebernimmt die Pressetexte bis zum fuenfzehnten Oktober.", 0.8),
        ("Die Frist fuer den Antrag ist der sechste Oktober, nicht der fuenfte.", 4.0),
        ("Robo, trag das bitte als Entscheidung ein.", 0.5),
        ("Offen bleibt, ob wir das ueber den Verein oder die GbR abrechnen.", 3.0),
    ]
    for text, pause in gespraech:
        bb.sentence(text)
        clock.advance(pause)
        bb.tick()
    bb.finish()
    return gespraech, emitted, " ".join(b.text for b in emitted)


def test_conversation_several_blocks() -> None:
    assert len(_conversation()[1]) >= 3


def test_conversation_number_survives() -> None:
    assert "zwoelftausend" in _conversation()[2]


def test_conversation_deadline_survives() -> None:
    assert "sechste Oktober" in _conversation()[2]


def test_conversation_order_marked() -> None:
    assert any(b.wake_word for b in _conversation()[1])


def test_conversation_nothing_lost() -> None:
    gespraech, _, volltext = _conversation()
    assert all(t.split(",")[0][:30] in volltext for t, _ in gespraech)


# 11 — length cut also releases a short block
# Otherwise a "Yes." would hang around indefinitely and stick to a topic
# discussed half an hour later.
def _short_forced():
    clock, emitted, bb = build(pause_s=2.5, max_s=90, min_chars=25)
    bb.sentence("Ja.")
    clock.advance(120)
    bb.tick()
    return emitted


def test_short_block_still_goes_out_after_max_s() -> None:
    assert len(_short_forced()) == 1


def test_reason_is_length_on_a_forced_cut() -> None:
    emitted = _short_forced()
    assert emitted and emitted[0].reason == "length"


# 12 — an empty wake word must not turn every sentence into an order
def test_empty_wake_word_doesnt_trigger() -> None:
    clock, emitted, bb = build(pause_s=2.5, wake_word="")
    bb.sentence("Ein ganz gewoehnlicher Satz ohne jeden Auftrag darin.")
    assert len(emitted) == 0


def test_whitespace_wake_word_doesnt_trigger() -> None:
    clock, emitted, bb = build(pause_s=2.5, wake_word="   ")
    bb.sentence("Auch dieser Satz ist kein Auftrag an irgendwen.")
    assert len(emitted) == 0


# 13 — wake word in quotes, repeated, at the end of a sentence
def test_wake_word_behind_a_typographic_quote() -> None:
    clock, emitted, bb = build(pause_s=2.5)
    bb.sentence("„Robo, mach das bitte.")
    assert len(emitted) == 1


def test_several_wake_words_give_exactly_one_block() -> None:
    clock, emitted, bb = build(pause_s=2.5)
    bb.sentence("Robo, und dann Robo nochmal, hoerst du, Robo?")
    assert len(emitted) == 1


def test_several_wake_words_text_complete() -> None:
    clock, emitted, bb = build(pause_s=2.5)
    bb.sentence("Robo, und dann Robo nochmal, hoerst du, Robo?")
    assert emitted and emitted[0].text.count("Robo") == 3


def test_wake_word_at_end_of_sentence_lowercase() -> None:
    clock, emitted, bb = build(pause_s=2.5)
    bb.sentence("Das machen wir spaeter, robo.")
    assert len(emitted) == 1


# Misheard variants of the wake word at utterance start (live meeting,
# 2026-10-20: STT transcribed "Robo" as "Grobo", "Wo, wo" and "Wobei", so the
# instruction landed in the minutes instead of being handled as an order).
@pytest.mark.parametrize("text", [
    "Grobo, starte die Vorstellungsrunde.",
    "Wo, wo, naechster Punkt.",
    "Wobei, fass das mal zusammen.",
])
def test_wake_variant_fires(text: str) -> None:
    clock, emitted, bb = build(pause_s=2.5)
    bb.sentence(text)
    assert emitted and emitted[0].wake_word and emitted[0].reason == "wake"


@pytest.mark.parametrize("text", [
    "Wobei ich finde, dass das schwierig ist.",
    "Wo wohnst du eigentlich?",
    "Wobei das schwierig ist.",
    "Wo, wo war das nochmal?",
    "Der Grobolt kommt gleich vorbei.",
])
def test_no_wake_variant_fires(text: str) -> None:
    clock, emitted, bb = build(pause_s=2.5)
    bb.sentence(text)
    # No clock advance, no length cut possible here -- an immediate
    # emission can only mean the wake word fired.
    assert len(emitted) == 0


# 14 — Unicode and very long sentences
def _very_long():
    clock, emitted, bb = build(pause_s=2.5)
    lang = "Emojis 🎧 und Umlaute äöüß und Kyrillisch привет. " * 400   # ~20k chars
    bb.sentence(lang)
    clock.advance(3.0)
    bb.tick()
    return lang, emitted


def test_very_long_sentence_goes_through() -> None:
    assert len(_very_long()[1]) == 1


def test_unicode_unchanged() -> None:
    emitted = _very_long()[1]
    assert emitted and "привет" in emitted[0].text and "🎧" in emitted[0].text


def test_character_count_matches() -> None:
    lang, emitted = _very_long()
    assert emitted and emitted[0].chars == len(lang.strip())


# 15 — non-text from the STT server doesn't kill block building
def test_non_text_doesnt_crash() -> None:
    clock, emitted, bb = build(pause_s=2.5)
    bb.sentence(None)          # type: ignore[arg-type]
    bb.sentence(42)            # type: ignore[arg-type]
    bb.sentence(["a", "b"])    # type: ignore[arg-type]


# 16 — concurrency: sentence() and tick() from two threads lose nothing
class RealClock:
    """A clock that advances by one tick per sentence — deterministic
    enough that tick() keeps cutting and the threads collide with each
    other."""
    def __init__(self):
        self.t = 1000.0
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            self.t += 0.05
            return self.t


def _concurrent():
    clock2 = RealClock()
    gesehen: list = []
    sperre = threading.Lock()

    def sammle(b):
        with sperre:
            gesehen.append(b)

    bb2 = BlockBuilder(output=sammle, pause_s=0.2, max_s=1.0, min_chars=1,
                       now=clock2)
    n = 3000
    fertig = threading.Event()

    def spricht():
        for i in range(n):
            bb2.sentence(f"Satz Nummer {i:05d} mit genug Text.")
        fertig.set()

    def tickt():
        while not fertig.is_set():
            bb2.tick()

    schalter = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)          # force thread switching
    try:
        t1 = threading.Thread(target=spricht)
        t2 = threading.Thread(target=tickt)
        t1.start()
        t2.start()
        t1.join()
        t2.join()
    finally:
        sys.setswitchinterval(schalter)
    bb2.finish()
    return n, " ".join(b.text for b in gesehen)


def test_concurrent_no_sentence_lost_or_duplicated() -> None:
    n, volltext2 = _concurrent()
    verloren = [i for i in range(n) if f"Satz Nummer {i:05d}" not in volltext2]
    assert not verloren, f"{len(verloren)} lost, e.g. {verloren[:5]}"
    doppelt = [i for i in range(n) if volltext2.count(f"Satz Nummer {i:05d}") > 1]
    assert not doppelt, f"{len(doppelt)} duplicated, e.g. {doppelt[:5]}"


# 18 — misheard wake word at utterance start (rehearsal 2026-10-20:
# "Bobo, trag in ..." was filed as a topic instead of an order)
@pytest.mark.parametrize("heard", [
    "Bobo, trag in status/offene-punkte.md oben ein.",
    "Hobo, was steht in den offenen Punkten?",
    "Probo, fasse zusammen.",
    "Rubo, fass zusammen.",
    "Roboter, recherchiere das bitte.",
    "Robo-Vorstellungsrunde fertig.",
    "„Bobo, mach das bitte.",
    "Bobo - trag das ein.",
])
def test_misheard_wake_at_start_fires(heard: str) -> None:
    clock, emitted, bb = build(pause_s=2.5)
    bb.sentence(heard)
    assert len(emitted) == 1 and emitted[0].wake_word


@pytest.mark.parametrize("normal", [
    "Der Roboter, den wir gebaut haben, steht im Keller.",
    "Wir haben einen Roboter, der Kaffee kocht.",
    "Roboter sind ein Thema in dieser Runde.",
    "Bobo ist der Name von meinem Hund.",
    "Das war wie bei Bobo, dem Clown.",
    "Ich sag mal Hobo, Probo und Rubo zum Spass.",
])
def test_no_wake_for_normal_speech(normal: str) -> None:
    clock, emitted, bb = build(pause_s=2.5)
    bb.sentence(normal)
    assert not any(b.wake_word for b in emitted)


# allow_wake=False: a self-introduction during the intro round names Robo
# but is not an order (dress rehearsal 2026-10-20)
def test_allow_wake_false_intro_line_cuts_no_wake_block() -> None:
    clock, emitted, bb = build(pause_s=2.5)
    bb.sentence("Ich bin Max und will herausfinden, ob der Robo mich erkennt.",
                allow_wake=False)
    assert not any(b.wake_word for b in emitted)


def test_allow_wake_default_next_real_order_still_cuts() -> None:
    clock, emitted, bb = build(pause_s=2.5)
    bb.sentence("Ich bin Max und will herausfinden, ob der Robo mich erkennt.",
                allow_wake=False)
    bb.sentence("Robo, was steht in den offenen Punkten?")
    assert any(b.wake_word for b in emitted)


# 19 — bare wake word opens the call block for the NEXT utterance(s),
# never cuts a block ending at the PREVIOUS one (live finding, 2026-10-26:
# a lone "Robo" cut a block from the preceding explanation; the real order
# in the following utterance was never taken as the call's content).
def _bare_wake():
    clock, emitted, bb = build(pause_s=2.5)
    bb.sentence("Wir haben das Budget schon letztes Mal besprochen und sollten "
                "das jetzt nicht wieder aufmachen.")          # explanation
    clock.advance(1.0)
    bb.sentence("Robo")                                       # bare wake word
    clock.advance(1.0)
    bb.sentence("Fass das bitte nochmal zusammen.")            # the real order
    clock.advance(3.0)
    bb.tick()
    return emitted


def test_bare_wake_word_exactly_two_blocks() -> None:
    assert len(_bare_wake()) == 2


def test_bare_wake_word_explanation_block_carries_no_order() -> None:
    e = _bare_wake()
    assert len(e) == 2 and not e[0].wake_word and "Budget" in e[0].text


def test_bare_wake_word_explanation_block_not_reason_wake() -> None:
    e = _bare_wake()
    assert len(e) == 2 and e[0].reason != "wake"


def test_bare_wake_word_order_lands_in_the_call_block() -> None:
    e = _bare_wake()
    assert len(e) == 2 and e[1].wake_word and "zusammen" in e[1].text


def test_bare_wake_word_call_block_does_not_carry_the_explanation() -> None:
    e = _bare_wake()
    assert len(e) == 2 and "Budget" not in e[1].text


def test_bare_wake_word_itself_is_not_echoed_as_block_text() -> None:
    e = _bare_wake()
    assert len(e) == 2 and "Robo" not in e[0].text and e[1].text.strip() != "Robo"


# 20 — wake word welded onto a bare activation at the END of the SAME
# STT final (a sentence like "Dann koennten wir Robo dazu auch mal fragen.
# Robo Aktiv." arriving as ONE final, no pause for the STT server to split
# it in two). Must not fire a call: no order anywhere in it.
# The real order that follows 11s later must still fire its own call.
def _welded():
    clock, emitted, bb = build(pause_s=2.5)
    bb.sentence("Dann koennten wir Robo dazu auch mal fragen. Robo Aktiv.")
    clock.advance(11.0)
    bb.sentence("Robo. Gib uns mal bitte aus der Ablage eine Uebersicht aller "
                "Kontaktadressen, die wir notiert haben.")
    clock.advance(3.0)
    bb.tick()
    return emitted


def test_welded_bare_wake_exactly_two_blocks() -> None:
    assert len(_welded()) == 2


def test_welded_bare_wake_no_order_in_the_first_block() -> None:
    e = _welded()
    assert len(e) == 2 and not e[0].wake_word


def test_welded_bare_wake_explanation_text_is_kept() -> None:
    e = _welded()
    assert len(e) == 2 and "fragen" in e[0].text


def test_welded_bare_wake_the_real_order_still_fires() -> None:
    e = _welded()
    assert len(e) == 2 and e[1].wake_word and "Uebersicht" in e[1].text


# 21 — a trailing ADDRESS by name keeps its order
def test_trailing_address_keeps_its_order() -> None:
    clock, emitted, bb = build(pause_s=2.5)
    bb.sentence("Mach das bitte, Robo.")
    clock.advance(3.0)
    bb.tick()
    assert len(emitted) == 1 and emitted[0].wake_word


# 22 — a lone "Robo Aktiv." with nothing before it to flush is bare wake too
def _lone_aktiv():
    clock, emitted, bb = build(pause_s=2.5)
    bb.sentence("Robo Aktiv.")
    clock.advance(1.0)
    bb.sentence("Fass das bitte nochmal zusammen.")
    clock.advance(3.0)
    bb.tick()
    return emitted


def test_lone_robo_aktiv_exactly_one_block() -> None:
    assert len(_lone_aktiv()) == 1


def test_lone_robo_aktiv_order_lands_in_the_call_block() -> None:
    e = _lone_aktiv()
    assert len(e) == 1 and e[0].wake_word and "zusammen" in e[0].text


def test_lone_robo_aktiv_itself_is_not_echoed_as_block_text() -> None:
    e = _lone_aktiv()
    assert len(e) == 1 and "Aktiv" not in e[0].text


# 23 — replay of a synthetic incident window (invented filler, same wake-word
# structure and timing as a live incident). A bare-wake-tail hit
# ("... Robo dazu fragen. Robo Aktiv.") and a second, unrelated one
# ("Ja, das passt schon. Erika. Robo." -- Erika is a participant's name,
# not an order) must not let the filler that follows ride out as a false
# call. Production defaults: pause_s=2.5, max_s=90.0, min_chars=25
# (default), wake_word="robo".
_INCIDENT = [
    (0.0,   "Und die Liste? Ich glaube, die Tabelle zum Budget lag noch im Ordner dort"),
    (6.0,   "Dann könnten wir Robo dazu auch mal fragen. Robo Aktiv."),
    (10.0,  "Ja, gut. Dann probier es mal!"),
    (17.0,  "Robo. Gib uns mal bitte aus der Ablage eine Uebersicht aller "
            "Kontaktadressen, die wir notiert haben."),
    (22.0,  "Das ist im Archiv abgelegt. Es könnte auch sein, dass wir sie."),
    (25.0,  "In dem alten Ordner abgelegt haben."),
    (27.0,  "Schau dort bitte auch mal nach."),
    (36.0,  "Richtig."),
    (42.0,  "Das folgt gleich."),
    (46.0,  "Prima."),
    (49.0,  "Ja, das passt schon. Erika. Robo."),
    (51.0,  "Hier. Und."),
    (54.0,  "Und weiter geht es, oder?"),
    (55.0,  "Wir waren dabei."),
    (58.0,  "Irgendwie."),
    (58.1,  "Okay."),
    (62.0,  "Einfach zum nächsten Thema gehen. Zum Thema Planung."),
    (64.0,  "Wir haben nicht mehr so viel Zeit."),
    (70.0,  "Du musst. Um 18:00 los, glaube ich, oder?"),
    (70.1,  "Ja, richtig. Punkt."),
    (71.0,  "Ja. Fein."),
    (75.0,  "Also. Ja. Gut. Mit dem Thema Planung. Wie machen wir weiter?"),
]


def _replay():
    clock, emitted, bb = build(pause_s=2.5, max_s=90.0, min_chars=25, wake_word="robo")
    t0 = clock.t
    for offset, text in _INCIDENT:
        clock.t = t0 + offset
        bb.sentence(text)
    clock.t = t0 + _INCIDENT[-1][0] + 3.0
    bb.tick()
    bb.finish()
    return emitted


def test_replay_exactly_one_call() -> None:
    orders = [b for b in _replay() if b.wake_word]
    assert len(orders) == 1


def test_replay_the_one_call_is_the_real_order() -> None:
    orders = [b for b in _replay() if b.wake_word]
    assert len(orders) == 1 and "Kontaktadressen" in orders[0].text


def test_replay_name_drop_produces_no_call() -> None:
    assert not any("Erika" in b.text and b.wake_word for b in _replay())


def test_replay_filler_after_first_bare_wake_tail_is_not_its_own_call() -> None:
    assert not any(b.text.strip() == "Ja, gut. Dann probier es mal!" and b.wake_word
                   for b in _replay())
