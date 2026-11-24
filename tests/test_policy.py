# CoThinker — tests of the level-2 policy table
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""blocks/decisions_tasks_questions/policy.py: signal x mode x threshold -> action, no model call.

Every rule is a function returning bool, so the mutants at the end can
swap a broken variant in and prove that the named rule check goes red
(the "Mutant" pattern of tests/test_bench.py). Fixture text is invented.

    python3 -m pytest -q tests/test_policy.py
"""
from __future__ import annotations

import contextlib
import io
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))

import pytest  # noqa: E402
from script_checks import check, collected, done  # noqa: E402
import blocks.decisions_tasks_questions.policy as p  # noqa: E402
from shared import minutes_format as pd  # noqa: E402
import blocks.decisions_tasks_questions.questions as q  # noqa: E402


def ans(**ps):
    """Answer entries in decide.py's shape, one per given question id."""
    return [{"question_id": k, "p": v, "confidence": 0.9, "ms": 1.0}
            for k, v in ps.items()]


def kinds(actions):
    return [(a.kind, a.autonomous) for a in actions]


# ------------------------------------------------------------ the table
print("the table is data, keyed by the real modes and question ids")
check("POLICY covers exactly the three modes",
      set(p.POLICY) == set(pd.MODES), str(sorted(p.POLICY)))
noul = {f.id for f in q.QUESTIONS if f.kind == "noul"}
for mode, row in p.POLICY.items():
    check(f"{mode}: every signal is a level-1 noul id", set(row) <= noul,
          str(set(row) - noul))
    for sig, pair in row.items():
        check(f"{mode}/{sig}: (act_at, ask_at) tuple of None|0..1",
              isinstance(pair, tuple) and len(pair) == 2 and all(
                  v is None or (isinstance(v, float) and 0 <= v <= 1)
                  for v in pair), repr(pair))
        act_at, ask_at = pair
        if act_at is not None and ask_at is not None:
            check(f"{mode}/{sig}: ask band below act", ask_at < act_at)
    check(f"{mode}: SIGNAL_ACTIONS only names known actions",
          all(k in p.ACTIONS for ks in p.SIGNAL_ACTIONS[mode].values()
              for k in ks))

# §3 start values, cell by cell (docs/entwurf-meeting-modi.md)
check("§3 Dokument sitzung (None, 0.8)",
      p.POLICY["sitzung"]["dokument_kandidat"] == (None, 0.8))
check("§3 Dokument arbeit (0.8, 0.5)",
      p.POLICY["arbeit"]["dokument_kandidat"] == (0.8, 0.5))
check("§3 Dokument folge (None, 0.8)",
      p.POLICY["folge"]["dokument_kandidat"] == (None, 0.8))
check("§3 Recherche sitzung never",
      p.POLICY["sitzung"]["recherche_kandidat"] == (None, None))
check("§3 Recherche arbeit (0.8, 0.5)",
      p.POLICY["arbeit"]["recherche_kandidat"] == (0.8, 0.5))
check("§3 Recherche folge ask from 0.5 (ANNAHME: monotone)",
      p.POLICY["folge"]["recherche_kandidat"] == (None, 0.5))
for mode in pd.MODES:
    check(f"§3 Beschluss {mode} act 0.7",
          p.POLICY[mode]["beschluss"] == (0.7, None))
    check(f"§3 Zuruf {mode} act 0.9",
          p.POLICY[mode]["zuruf_robo"] == (0.9, None))
    check(f"§3 Zuruf {mode} -> wake",
          p.SIGNAL_ACTIONS[mode]["zuruf_robo"] == ("wake",))
check("§3 Beschluss sitzung -> stage_decision (tag via protocol step)",
      p.SIGNAL_ACTIONS["sitzung"]["beschluss"] == ("stage_decision",))
check("§3 Beschluss arbeit -> tag only",
      p.SIGNAL_ACTIONS["arbeit"]["beschluss"] == ("tag_entry",))
check("§3 Beschluss folge -> stage_decision",
      p.SIGNAL_ACTIONS["folge"]["beschluss"] == ("stage_decision",))


# ------------------------------------------------------------ the rules
def rule_act_boundary() -> bool:
    """p exactly at act_at acts (>=, not >)."""
    a = p.route(ans(beschluss=0.7), "sitzung", p.PolicyState())
    return kinds(a) == [("stage_decision", True)]


def rule_below_nothing() -> bool:
    a = p.route(ans(beschluss=0.69, dokument_kandidat=0.79),
                "sitzung", p.PolicyState())
    return a == []


def rule_ask_band() -> bool:
    """arbeit/dokument: 0.5..0.8 asks, >= 0.8 acts autonomously."""
    mid = p.route(ans(dokument_kandidat=0.6), "arbeit", p.PolicyState())
    hi = p.route(ans(dokument_kandidat=0.9), "arbeit", p.PolicyState())
    return (kinds(mid) == [("ask", False)]
            and mid[0].about.kind == "document"
            and mid[0].about.autonomous is False
            and kinds(hi) == [("document", True)])


def rule_sitzung_document_asks() -> bool:
    a = p.route(ans(dokument_kandidat=0.95), "sitzung", p.PolicyState())
    return kinds(a) == [("ask", False)] and a[0].about.kind == "document"


def rule_document_autonomous_only_in_arbeit() -> bool:
    """MM-G: the topic page (action `document`) is created autonomously
    ONLY in `arbeit`; in `sitzung` and `folge` even p = 1.0 asks. Read off
    the existing table -- the wiki layer re-encodes nothing."""
    for mode in p.MODES:
        a = p.route(ans(dokument_kandidat=1.0), mode, p.PolicyState())
        if mode == "arbeit":
            if kinds(a) != [("document", True)]:
                return False
        elif not (kinds(a) == [("ask", False)]
                  and a[0].about.kind == "document"):
            return False
    return set(p.MODES) == {"sitzung", "arbeit", "folge"}


def rule_one_ask_open() -> bool:
    st = p.PolicyState(ask=p.OpenAsk(action=p.Action(
        "document", "dokument_kandidat", 0.9, False)))
    a = p.route(ans(dokument_kandidat=0.6), "arbeit", st)
    st2 = p.PolicyState(stage_question_open=True)
    b = p.route(ans(dokument_kandidat=0.6), "arbeit", st2)
    return a == [] and b == []


def rule_one_ask_per_block() -> bool:
    a = p.route(ans(dokument_kandidat=0.6, recherche_kandidat=0.7),
                "arbeit", p.PolicyState())
    asks = [x for x in a if x.kind == "ask"]
    return len(asks) == 1 and asks[0].signal == "recherche_kandidat"


def rule_brake_drops_topic() -> bool:
    braked = p.route(ans(neues_thema=0.95, aufgabe=0.9), "sitzung",
                     p.PolicyState(brake="too soon"))
    free = p.route(ans(neues_thema=0.95), "sitzung", p.PolicyState())
    return (kinds(braked) == [("tag_entry", True)]
            and kinds(free) == [("topic_block", True)])


def rule_wake_passes_brake() -> bool:
    a = p.route(ans(zuruf_robo=0.95), "arbeit",
                p.PolicyState(brake="too many"))
    return kinds(a) == [("wake", True)]


def rule_gap_is_not_no() -> bool:
    """A gap entry produces nothing -- and is not read as 0.0 either."""
    entries = [{"question_id": "beschluss", "error": "missing", "ms": 1.0}]
    return p.route(entries, "sitzung", p.PolicyState()) == []


def rule_unknown_mode_is_sitzung() -> bool:
    a = p.route(ans(dokument_kandidat=0.9), "quatsch", p.PolicyState())
    return kinds(a) == [("ask", False)]


RULES = [rule_act_boundary, rule_below_nothing, rule_ask_band,
         rule_sitzung_document_asks,
         rule_document_autonomous_only_in_arbeit, rule_one_ask_open,
         rule_one_ask_per_block, rule_brake_drops_topic,
         rule_wake_passes_brake, rule_gap_is_not_no,
         rule_unknown_mode_is_sitzung]
print("\nrouting rules")
for r in RULES:
    check(r.__name__, r())

check("route accepts a Result-like object (.answers)",
      kinds(p.route(type("R", (), {"answers": ans(aufgabe=0.8)})(),
                    "sitzung", p.PolicyState())) == [("tag_entry", True)])
check("tag of an aufgabe entry is 'aufgabe'",
      p.route(ans(aufgabe=0.8), "sitzung", p.PolicyState())[0].tag
      == "aufgabe")
check("offene_frage files tag 'frage'",
      p.route(ans(offene_frage=0.85), "sitzung", p.PolicyState())[0].tag
      == "frage")
check("idee only in arbeit",
      p.route(ans(idee=0.9), "sitzung", p.PolicyState()) == []
      and kinds(p.route(ans(idee=0.9), "arbeit", p.PolicyState()))
      == [("tag_entry", True)])
check("research in arbeit is autonomous with tag recherche",
      [(a.kind, a.autonomous, a.tag) for a in p.route(
          ans(recherche_kandidat=0.85), "arbeit", p.PolicyState())]
      == [("research", True, "recherche")])
check("all p=1.0 in every mode: never more than one ask",
      all(sum(1 for a in p.route(ans(**{s: 1.0 for s in noul}), m,
                                 p.PolicyState()) if a.kind == "ask") <= 1
          for m in pd.MODES))


# -------------------------------------- NEVER autonomous (acceptance 4)
def never_delete() -> bool:
    a = p.Action("tag_entry", "beschluss", 1.0, True,
                 effects=frozenset({"append", "delete"}))
    ask = p.Action("ask", "beschluss", 1.0, False, about=replace(
        a, autonomous=False))
    return p.enforce(a) is None and p.enforce(ask) is None


def never_reorder() -> bool:
    a = p.Action("topic_block", "neues_thema", 1.0, True,
                 effects=frozenset({"reorder"}))
    return p.enforce(a) is None


def never_write_outside(tmp=Path("/tmp/cothinker-policy-test")) -> bool:
    session = tmp / "sessions" / "2026-09-26-probe"
    doc = p.Action("document", "dokument_kandidat", 1.0, True,
                   effects=frozenset(p.ACTION_EFFECTS["document"]))
    inside = p.enforce(p.with_target(doc, session / "dokument-1.md",
                                     (session,)))
    outside = p.enforce(p.with_target(doc, tmp / "projekt.md", (session,)))
    escape = p.enforce(p.with_target(doc, session / ".." / "x.md",
                                     (session,)))
    return (inside is not None and inside.kind == "document"
            and inside.autonomous
            and outside is not None and outside.kind == "ask"
            and outside.about.kind == "document"
            and escape is not None and escape.kind == "ask")


def never_copy_speech() -> bool:
    a = p.Action("document", "dokument_kandidat", 1.0, True,
                 effects=frozenset({"write_session", "copy_speech"}))
    out = p.enforce(a)
    return out is not None and out.kind == "ask" and not out.autonomous


def never_via_route() -> bool:
    """Whatever maps a signal to a forbidden effect, route() refuses or
    asks -- the guard sits inside route(), not only at the call sites."""
    saved = dict(p.ACTION_EFFECTS)
    try:
        p.ACTION_EFFECTS["tag_entry"] = frozenset({"delete"})
        p.ACTION_EFFECTS["topic_block"] = frozenset({"copy_speech"})
        out = p.route(ans(aufgabe=1.0, neues_thema=1.0), "arbeit",
                      p.PolicyState())
        return (not any(a.kind == "tag_entry" for a in out)
                and not any(a.autonomous for a in out)
                and [a.kind for a in out] == ["ask"])
    finally:
        p.ACTION_EFFECTS.clear()
        p.ACTION_EFFECTS.update(saved)


NEVER = [never_delete, never_reorder, never_write_outside,
         never_copy_speech, never_via_route]
print("\nnever autonomous, whatever p is: always ask or refuse")
for r in NEVER:
    check(r.__name__, r())
check("no normal action carries a forbidden effect",
      not any(set(e) & (p.FORBIDDEN_REFUSE | p.FORBIDDEN_ASK)
              for e in p.ACTION_EFFECTS.values()))


# ------------------------------------------------------------- texts
print("\nstage texts: German, yes/no, answerable in three words")
samples = [p.Action("ask", "x", 0.6, False, about=p.Action(
    k, "x", 0.6, False, tag=t, effects=frozenset(e)))
    for k, t, e in [("document", "", ()), ("research", "recherche", ()),
                    ("topic_block", "", ()), ("stage_decision", "", ()),
                    ("tag_entry", "beschluss", ()), ("tag_entry", "aufgabe", ()),
                    ("tag_entry", "frage", ()), ("tag_entry", "idee", ()),
                    ("document", "", ("write_outside",)),
                    ("document", "", ("copy_speech",))]]
for s in samples:
    t = p.ask_text(s)
    check(f"ask text {t!r}: ends with '?', <= 5 words, no 'oder'",
          t.endswith("?") and len(t.split()) <= 5 and " oder " not in t)
check("write_outside gets its own question",
      p.ask_text(samples[8]) != p.ask_text(samples[0]))
for kind in p.ACTIONS:
    pk, label = p.PENDING[kind]
    check(f"pending {kind}: known stage kind, label <= 60 chars",
          pk in {"topic", "main", "research", "document", "question",
                 "decision", "entry"} and 0 < len(label) <= 60)
# ------------------------------------------------- pending labels (MM-L)
# Fixtures are invented: a garden club planning a bake sale. No session
# content, per AGENTS.md.
print("\npending labels: prefix + <= 3 content words, never the full block")
check("content_words drops articles and conjunctions",
      pd.content_words("Der Gartenverein und die Kuchenbasar Planung")
      == ["Gartenverein", "Kuchenbasar", "Planung"])
check("content_words drops the wake word 'robo' but keeps stopwords out",
      pd.content_words("Robo kann der Gartenverein das Datum für den "
                       "Kuchenbasar klären")
      == ["Gartenverein", "Datum", "Kuchenbasar"])
check("content_words keeps 'Robotik' -- not the wake word (blocks._WAKE_NOT)",
      pd.content_words("Robotik und Gartenverein planen zusammen")[:1]
      == ["Robotik"])
check("content_words keeps 'Robert' -- not the wake word",
      pd.content_words("Robert bringt den Kuchen mit")[:1] == ["Robert"])
check("content_words respects n",
      pd.content_words("Gartenverein plant Kuchenbasar Flohmarkt", n=2)
      == ["Gartenverein", "plant"])
check("content_words strips surrounding punctuation, keeps inner hyphen",
      pd.content_words("Der Kuchenbasar-Termin, endlich!")
      == ["Kuchenbasar-Termin", "endlich"])
check("content_words skips tokens with no letter or digit at all "
      "(bare --, em dash, en dash, ...)",
      pd.content_words("Also -- der Termin nächste Woche")
      == ["Termin", "nächste", "Woche"]
      and pd.content_words("Der Termin -- äh -- ... — – steht")
      == ["Termin", "steht"])
check("pending_label never puts a bare dash/ellipsis into the label",
      pd.pending_label("topic", "Also -- der Termin nächste Woche")
      == "Thema: Termin nächste Woche")

block = "Der Gartenverein plant einen Kuchenbasar für Samstag"
label = pd.pending_label("topic", block)
check("topic label: prefix + <= 3 content words, not the full block",
      label == "Thema: Gartenverein plant Kuchenbasar" and label != block)

label = pd.pending_label(
    "decision", "Wir haben beschlossen den Kuchenbasar zu verschieben")
check("decision label",
      label == "Beschluss: beschlossen Kuchenbasar verschieben")

label = pd.pending_label(
    "research", "Ob wir für den Kuchenbasar eine Standgenehmigung brauchen")
check("research label",
      label == "Recherche: Kuchenbasar Standgenehmigung brauchen")

label = pd.pending_label(
    "main", "Robo, sollen wir den Kuchenbasar oder den Flohmarkt bewerben")
check("main (wake) label: 'Robo' itself never shows up",
      label == "Zuruf: Kuchenbasar Flohmarkt bewerben")

label = pd.pending_label(
    "question", "Sollen wir den Kuchenbasar verschieben?")
check("question label keeps the trailing '?' on the last word",
      label == "Rückfrage: Kuchenbasar verschieben?")

label = pd.pending_label(
    "document",
    "Der Vorstand hat heute lange über die Finanzen des Kuchenbasars "
    "gesprochen",
    title="Kuchenbasar Finanzen")
check("document label: title beats block, block text never leaks in",
      label == "Dokument: Kuchenbasar Finanzen"
      and "Vorstand" not in label and "gesprochen" not in label)

for tag, prefix in pd.TAG_PENDING_PREFIX.items():
    label = pd.pending_label("entry", "Kuchen für den Basar bestellen",
                            tag=tag)
    check(f"entry label uses the tag prefix for tag={tag!r}",
          label.startswith(prefix))
label = pd.pending_label("entry", "Kuchen für den Basar bestellen",
                        tag="notiz")
check("entry label falls back to 'Notiert:' for an unknown/absent tag",
      label.startswith("Notiert:"))

check("empty block (only stopwords) falls back to the given fallback",
      pd.pending_label("topic", "der die und", fallback="Neues Thema erkannt")
      == "Neues Thema erkannt")
check("empty block without a fallback returns the bare prefix",
      pd.pending_label("topic", "der die und") == "Thema")

check("contractions and fillers (am, im, bis, nicht) do not take a slot",
      pd.pending_label("decision", "Das Fest findet am Samstag im Garten statt")
      == "Beschluss: Fest findet Samstag")
check("wake word in a later sentence: main label names the order",
      pd.pending_label("main", "Der Verein plant die Satzung lange. "
                              "Hey Robo, zeig uns die Beetpaten.")
      == "Zuruf: zeig Beetpaten")
check("main label without a wake word uses the whole text",
      pd.pending_label("main", "Satzung Beetpaten Liste") ==
      "Zuruf: Satzung Beetpaten Liste")
check("Robotik is not the wake word for main",
      pd.pending_label("main", "Robotik im Garten ist Thema")
      == "Zuruf: Robotik Garten Thema")
check("'?' is not put on a word that was cut off",
      pd.pending_label("question", "Eigenes Dokument dazu anlegen?")
      == "Rückfrage: Eigenes Dokument dazu")
check("'?' is not appended when the label was cut after three words",
      not pd.pending_label("question",
                          "Plant Verein Kuchenbasar Samstag Gemeindesaal?"
                          ).endswith("?"))

long_word = "Gartenvereinskuchenbasarplanungsausschussvorsitzendenwahl" * 2
check("label is cut to MAX_PENDING_LABEL even with one very long word",
      len(pd.pending_label("topic", long_word)) <= pd.MAX_PENDING_LABEL)

known_prefixes = set(pd.PENDING_PREFIX.values()) | set(
    pd.TAG_PENDING_PREFIX.values())
for pkind, _ in p.PENDING.values():
    label = pd.pending_label(pkind, "Gartenverein plant den Kuchenbasar bald")
    check(f"every stage pending kind has a prefix: {pkind!r}",
          label.split(" ", 1)[0] in known_prefixes, label)

check("marker autonomous", pd.marker(True) == "*(autonom)*")
check("marker confirmed", pd.marker(False, "14:05") == "*(bestätigt 14:05)*")
lang = "wort " * 200
check("with_marker keeps the marker inside the limit",
      pd.with_marker(lang, "*(autonom)*").endswith("*(autonom)*")
      and len(pd.with_marker(lang, "*(autonom)*")) <= 500)
check("with_marker without marker is the text",
      pd.with_marker("Ein Satz.", "") == "Ein Satz.")
check("anchor: first 8 words, no quotes/backticks",
      p.anchor('Wir "machen" `das` so und dann noch weiter bis morgen')
      == "Wir machen das so und dann noch weiter")
for kind in p.ACTIONS:
    if kind == "ask":
        continue
    a = p.Action(kind, "x", 0.9, True, tag="aufgabe")
    t = p.order_text(a, "A7", "Wir machen das", target="/s/d.md", stem="d")
    check(f"order {kind}: carries its id, is ASCII, never says 'entscheide'",
          "A7" in t and t.isascii() and "entscheide" not in t.lower())
note = p.orders_note(["AUFTRAG A1: x"], "/s")
check("orders_note: header, the line, the rules with the folder",
      "AUFTRAEGE" in note and "AUFTRAG A1: x" in note and "/s" in note
      and "nichts loeschen" in note and note.isascii())
check("orders_note of nothing is empty", p.orders_note([], "/s") == "")


# ------------------------------------------------------------- canary
print("\ncanary: KANARIENVOGEL must never leak into stdout/trace/error")
CANARY = "KANARIENVOGEL"


def _run_captured(fn, *a, **kw):
    """Runs fn with stdout/stderr captured. Returns (result, captured,
    exc) -- exc is the raised exception, or None. Never lets an
    exception propagate: the canary check needs the captured output
    either way, and a caller decides for itself whether exc is a
    failure."""
    buf_out, buf_err = io.StringIO(), io.StringIO()
    result, exc = None, None
    try:
        with contextlib.redirect_stdout(buf_out):
            with contextlib.redirect_stderr(buf_err):
                result = fn(*a, **kw)
    except Exception as e:                        # noqa: BLE001
        exc = e
    return result, buf_out.getvalue() + buf_err.getvalue(), exc


# Block text carriers: the anchor is meant to reach the agent's prompt,
# so anchor()/order_text()/with_marker()/orders_note() MAY show the
# canary -- they never print it, and it never raises.
anchor_out, cap, exc = _run_captured(
    p.anchor, f"Wir reden ueber {CANARY} und dann noch weiter")
check("canary: anchor() raises nothing", exc is None, repr(exc))
check("canary: anchor() prints nothing", CANARY not in cap)
check("canary: anchor() output carries it (by design)", CANARY in anchor_out)

marker_out, cap, exc = _run_captured(
    pd.with_marker, f"Satz mit {CANARY} drin.", "*(autonom)*")
check("canary: with_marker() raises nothing", exc is None, repr(exc))
check("canary: with_marker() prints nothing", CANARY not in cap)
check("canary: with_marker() output carries it (by design)",
      CANARY in marker_out)

_order_action = p.Action("tag_entry", "aufgabe", 0.9, True, tag="aufgabe")
order_out, cap, exc = _run_captured(
    p.order_text, _order_action, "A1", f"Block mit {CANARY} drin",
    target="/s/d.md", stem="d")
check("canary: order_text() raises nothing", exc is None, repr(exc))
check("canary: order_text() prints nothing", CANARY not in cap)
check("canary: order_text() output carries it (by design)",
      CANARY in order_out)

note_out, cap, exc = _run_captured(
    p.orders_note, [f"AUFTRAG A1: {CANARY}"], "/s")
check("canary: orders_note() raises nothing", exc is None, repr(exc))
check("canary: orders_note() prints nothing", CANARY not in cap)
check("canary: orders_note() output carries it (by design)",
      CANARY in note_out)

# Answers whose question_id/extra keys carry the canary: route() and
# enforce() must never let it reach an Action field, and ask_text()
# must never let it reach the stage text.
poisoned = [{"question_id": "beschluss", "p": 0.9, "raw_text": CANARY,
             f"note_{CANARY}": CANARY}]
acts, cap, exc = _run_captured(p.route, poisoned, "sitzung", p.PolicyState())
check("canary: route() with poisoned extra keys raises nothing",
      exc is None, repr(exc))
check("canary: route() with poisoned extra keys prints nothing",
      CANARY not in cap)
check("canary: route() actions never carry it", CANARY not in repr(acts))

id_poisoned = [{"question_id": CANARY, "p": 1.0}]
acts2, cap, exc = _run_captured(
    p.route, id_poisoned, "sitzung", p.PolicyState())
check("canary: route() with canary question_id raises nothing",
      exc is None, repr(exc))
check("canary: route() with canary question_id prints nothing",
      CANARY not in cap)
check("canary: route() with canary question_id makes no action",
      acts2 == [])

for _act in list(acts) + list(acts2):
    enforced, cap, exc = _run_captured(p.enforce, _act)
    check(f"canary: enforce() of {_act.kind} raises nothing",
          exc is None, repr(exc))
    check(f"canary: enforce() of {_act.kind} prints nothing",
          CANARY not in cap)
    check(f"canary: enforce() of {_act.kind} keeps it out of the Action",
          CANARY not in repr(enforced))
    if enforced is not None:
        txt, cap, exc = _run_captured(p.ask_text, enforced)
        check(f"canary: ask_text() of {_act.kind} raises nothing",
              exc is None, repr(exc))
        check(f"canary: ask_text() of {_act.kind} prints nothing",
              CANARY not in cap)
        check(f"canary: ask_text() of {_act.kind} never carries it",
              CANARY not in (txt or ""))

# ask_text() must fall back safely even when kind/tag are themselves
# the canary (a hypothetical corrupted Action) -- its lookup tables
# must never echo an unknown key back into the stage text.
weird = p.Action("ask", CANARY, 0.6, False,
                 about=p.Action(CANARY, CANARY, 0.6, False, tag=CANARY))
weird_txt, cap, exc = _run_captured(p.ask_text, weird)
check("canary: ask_text() of a corrupted Action raises nothing",
      exc is None, repr(exc))
check("canary: ask_text() of a corrupted Action prints nothing",
      CANARY not in cap)
check("canary: ask_text() of a corrupted Action falls back, no leak",
      CANARY not in weird_txt)

# Garbage entries: None, a non-dict, and a dict that only has the
# canary as its question_id -- route() must shrug, not choke.
garbage = [None, "not a dict", {"question_id": CANARY, "p": 1.0}]
gout, cap, exc = _run_captured(p.route, garbage, "sitzung", p.PolicyState())
check("canary: route() over garbage raises nothing", exc is None, repr(exc))
check("canary: route() over garbage prints nothing", CANARY not in cap)
check("canary: route() over garbage makes no action", gout == [])


# ------------------------------------------------------------ mutants
print("\nmutants: each broken rule must turn its named check red")


def _mutant(name, attr, broken, rule):
    saved = getattr(p, attr)
    setattr(p, attr, broken)
    try:
        red = not rule()
    except Exception:                       # noqa: BLE001
        red = True
    finally:
        setattr(p, attr, saved)
    check(f"mutant {name} is caught by {rule.__name__}", red)


_zone = p.zone
_route = p.route
_enforce = p.enforce
_within = p.within
_mutant("zone uses > instead of >=", "zone",
        lambda x, act, ask: ("act" if act is not None and x > act else
                             "ask" if ask is not None and x > ask else
                             "none"), rule_act_boundary)
_mutant("zone ignores act_at (asks above the band)", "zone",
        lambda x, act, ask: ("ask" if ask is not None and x >= ask else
                             "act" if act is not None and x >= act else
                             "none"), rule_ask_band)
_mutant("route ignores the open ask", "route",
        lambda a, m, s: _route(a, m, p.PolicyState(brake=s.brake)),
        rule_one_ask_open)
_mutant("route ignores the brake", "route",
        lambda a, m, s: _route(a, m, p.PolicyState(ask=s.ask)),
        rule_brake_drops_topic)
_mutant("enforce lets delete through", "enforce",
        lambda a: a, never_delete)
_mutant("enforce lets reorder through", "enforce",
        lambda a: a, never_reorder)
_mutant("enforce lets copy_speech act", "enforce",
        lambda a: None if a is None or (a.about or a).effects
        & p.FORBIDDEN_REFUSE else a, never_copy_speech)
_mutant("within() always true", "within",
        lambda t, r: True, never_write_outside)
_mutant("route skips enforce", "enforce",
        lambda a: a, never_via_route)


# ------------------------------------------------------ ask lifecycle
print("\nthe open question: yes / no / no answer in two blocks")
DOC = p.Action("document", "dokument_kandidat", 0.9, False)


def _open():
    return p.PolicyState(ask=p.OpenAsk(action=DOC, stage_id="Q1"))


def rule_confirm() -> bool:
    st = _open()
    out, ask = p.answer(st, 0.9, 0.8)
    return out == "confirmed" and ask.action == DOC and st.ask is None


def rule_confirm_needs_address() -> bool:
    """A 'ja' that is not addressed to Robo does not confirm."""
    st = _open()
    out, _ = p.answer(st, 0.95, 0.1)
    return out == "waiting" and st.ask is not None


def rule_reject() -> bool:
    st = _open()
    out, ask = p.answer(st, 0.1, 0.8)
    return out == "rejected" and ask.action == DOC and st.ask is None


def rule_expire_after_two() -> bool:
    st = _open()
    a, _ = p.answer(st, None, None)
    b, ask = p.answer(st, 0.5, 0.9)
    return a == "waiting" and b == "expired" and st.ask is None \
        and ask.stage_id == "Q1"


def rule_gap_is_no_answer() -> bool:
    st = _open()
    out, _ = p.answer(st, None, 0.9)
    return out == "waiting" and st.ask.blocks == 1


def rule_no_reask_after_no() -> bool:
    """A no (or silence) settles the ask: nothing new in that block, and
    the same signal stays silent for ASK_COOLDOWN_BLOCKS more blocks. A
    different signal may ask again from the next block on."""
    for antwort in ((0.1, 0.8), (None, None)):          # rejected, expired
        st = p.PolicyState(block=5, ask=p.OpenAsk(
            action=p.Action("document", "dokument_kandidat", 0.6, False),
            stage_id="Q1", blocks=p.ASK_MAX_BLOCKS - 1))
        out, _ = p.answer(st, *antwort)
        if out not in ("rejected", "expired"):
            return False
        if p.route(ans(dokument_kandidat=0.6, recherche_kandidat=0.6),
                   "arbeit", st) != []:
            return False                                 # settling block
        for b in range(6, 6 + p.ASK_COOLDOWN_BLOCKS):
            st.block = b
            if p.route(ans(dokument_kandidat=0.6), "arbeit", st) != []:
                return False
        st.block = 6
        andere = p.route(ans(recherche_kandidat=0.6), "arbeit", st)
        if kinds(andere) != [("ask", False)]:
            return False
        st.block = 6 + p.ASK_COOLDOWN_BLOCKS
        if kinds(p.route(ans(dokument_kandidat=0.6), "arbeit", st)) \
                != [("ask", False)]:
            return False
    return True


def rule_yes_has_cooldown() -> bool:
    """A yes settles too (no ask in that block) AND puts its signal on
    cooldown like a no: a question the round just answered must not come
    straight back. A different signal may ask from the next block on."""
    st = p.PolicyState(block=2, ask=p.OpenAsk(action=DOC, stage_id="Q1"))
    out, _ = p.answer(st, 0.9, 0.8)
    if out != "confirmed":
        return False
    if p.route(ans(dokument_kandidat=0.6), "arbeit", st) != []:
        return False                                     # settling block
    for b in range(3, 3 + p.ASK_COOLDOWN_BLOCKS):
        st.block = b
        if p.route(ans(dokument_kandidat=0.6), "arbeit", st) != []:
            return False
    st.block = 3
    if kinds(p.route(ans(recherche_kandidat=0.6), "arbeit", st)) \
            != [("ask", False)]:
        return False
    st.block = 3 + p.ASK_COOLDOWN_BLOCKS
    return kinds(p.route(ans(dokument_kandidat=0.6), "arbeit", st)) \
        == [("ask", False)]


LIFE = [rule_confirm, rule_confirm_needs_address, rule_reject,
        rule_expire_after_two, rule_gap_is_no_answer,
        rule_no_reask_after_no, rule_yes_has_cooldown]
for r in LIFE:
    check(r.__name__, r())
check("answer() without an open ask", p.answer(p.PolicyState(), 0.9, 0.9)
      == ("none", None))


# --------------------------------------------------------- mode check
print("\nmode counter-check over the first five minutes")


def rule_mode_mismatch() -> bool:
    return p.mode_check(["arbeit", "arbeit", "sitzung", "arbeit"],
                        "sitzung") == "arbeit"


def rule_mode_needs_votes() -> bool:
    return p.mode_check(["arbeit", "arbeit"], "sitzung") is None


def rule_mode_needs_majority() -> bool:
    return p.mode_check(["arbeit", "folge", "sitzung", "arbeit", "folge"],
                        "sitzung") is None


def rule_mode_agrees() -> bool:
    return p.mode_check(["sitzung"] * 5, "sitzung") is None


MODE = [rule_mode_mismatch, rule_mode_needs_votes, rule_mode_needs_majority,
        rule_mode_agrees]
for r in MODE:
    check(r.__name__, r())
check("junk votes are ignored",
      p.mode_check(["arbeit", None, "x", "arbeit", "arbeit"], "folge")
      == "arbeit")
check("mirror text", p.mode_mirror_text("arbeit")
      == "Robo hört ein Arbeitsmeeting — umschalten?")
check("mirror text for every mode", all(
    p.mode_mirror_text(m).endswith("umschalten?") for m in pd.MODES))

print("\nmutants of the lifecycle and the mode check")
_answer = p.answer
_mode_check = p.mode_check


def _answer_no_address(st, pc, pz):
    return _answer(st, pc, 1.0)


def _answer_three_blocks(st, pc, pz):
    saved = p.ASK_MAX_BLOCKS
    p.ASK_MAX_BLOCKS = 3
    try:
        return _answer(st, pc, pz)
    finally:
        p.ASK_MAX_BLOCKS = saved


_mutant("answer ignores the address", "answer", _answer_no_address,
        rule_confirm_needs_address)
_mutant("answer waits three blocks", "answer", _answer_three_blocks,
        rule_expire_after_two)
_mutant("route ignores settle block and cooldown", "route",
        lambda a, m, s: _route(a, m, p.PolicyState(
            ask=s.ask, stage_question_open=s.stage_question_open,
            brake=s.brake)), rule_no_reask_after_no)
_mutant("may_ask always true", "may_ask", lambda s, sig: True,
        rule_no_reask_after_no)


def _answer_no_cooldown(st, pc, pz):
    out = _answer(st, pc, pz)
    st.cooldown.clear()
    return out


_mutant("answer sets no cooldown", "answer", _answer_no_cooldown,
        rule_no_reask_after_no)
_mutant("answer sets no cooldown", "answer", _answer_no_cooldown,
        rule_yes_has_cooldown)


def _answer_no_cooldown_on_yes(st, pc, pz):
    # The old behaviour: a yes settles the block but cools nothing down.
    out = _answer(st, pc, pz)
    if out[0] == "confirmed":
        st.cooldown.clear()
    return out


_mutant("answer cools down a no but not a yes", "answer",
        _answer_no_cooldown_on_yes, rule_yes_has_cooldown)
_mutant("mode_check without majority", "mode_check",
        lambda v, s, **k: next((x for x in v if x != s), None),
        rule_mode_needs_majority)
_mutant("mode_check without minimum", "mode_check",
        lambda v, s, **k: _mode_check(v, s, min_votes=1),
        rule_mode_needs_votes)

done()


@pytest.mark.parametrize("name,ok,info", collected())
def test_check(name, ok, info):
    assert ok, info
