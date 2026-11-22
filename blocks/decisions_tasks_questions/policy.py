#!/usr/bin/env python3
# CoThinker — level 2: signal x mode x threshold -> action
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software under the GNU Affero General Public
# License, version 3. It comes WITHOUT ANY WARRANTY. See LICENSE.
"""docs/entwurf-meeting-modi.md section 3, "Ebene 2 -- Handeln": the code,
not a model, decides what a level-1 answer vector (gateway/decide.py) sets
in motion. Confidence-gated routing: the model answers narrow questions
with a probability, THIS table says from which probability on something
happens.

    route(answers, mode, state) -> list[Action]

    p >= act_at            act at once, marked *(autonom)*
    ask_at <= p (< act_at) ONE yes/no question on the stage
    otherwise              nothing

PURE. No I/O (except `within()` resolving symlinks, read-only), no
clock, no thread, no model call. `answers` are the finished entries of
`decide.decide()` -- this module never talks to a backend (the
JEV-SITEs of level 2 live in gateway/policy_runner.py).
Everything German that level 2 puts in front of the round or into the
agent's prompt lives here too, so one file shows what the room sees.

NEVER AUTONOMOUS, whatever p is: deleting, reordering, writing outside
the session folder (and the wiki layer, once it exists), copying other
people's speech into other documents. `enforce()` refuses the first two
and turns the last two into a question -- and `route()` runs every
action through it, so a future table row cannot bypass it.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional, Sequence

from shared.minutes_format import DEFAULT_MODE, MODES

ACTIONS = ("topic_block", "tag_entry", "stage_decision", "ask", "research",
           "document", "wake")

# --- the table ------------------------------------------------------------
# (act_at, ask_at). None = never. Order = level-1 question order, which is
# also the tie order when two asks compete in one block.
# The rows the design doc names (§3 table) are start values, to be set by
# LOSO (bench/loso.py `policy`) once there is data -- see docs/bench.md.
POLICY: dict = {
    "sitzung": {
        # ANNAHME: low-risk, and the brake guards the surface anyway.
        "neues_thema": (0.7, None),
        # ANNAHME: v2 is append-only; there is no block to "continue".
        "rueckkehr_thema": (None, None),
        "beschluss": (0.7, None),                       # §3
        # ANNAHME: a task is an append, same weight as a decision.
        "aufgabe": (0.7, None),
        # ANNAHME: open questions are frequent -- higher bar.
        "offene_frage": (0.8, None),
        # ANNAHME: the idea tag belongs to the work meeting (§2).
        "idee": (None, None),
        "recherche_kandidat": (None, None),             # §3
        "dokument_kandidat": (None, 0.8),               # §3
        "zuruf_robo": (0.9, None),                      # §3
        # ANNAHME: numbers are the formulating agent's job, not an action.
        "zahl_frist_betrag": (None, None),
    },
    "arbeit": {
        # ANNAHME: low-risk, and the brake guards the surface anyway.
        "neues_thema": (0.7, None),
        # ANNAHME: v2 is append-only; there is no block to "continue".
        "rueckkehr_thema": (None, None),
        "beschluss": (0.7, None),                       # §3
        # ANNAHME: a task is an append, same weight as a decision.
        "aufgabe": (0.7, None),
        # ANNAHME: open questions are frequent -- higher bar.
        "offene_frage": (0.8, None),
        # ANNAHME: the idea tag belongs to the work meeting (§2).
        "idee": (0.7, None),
        "recherche_kandidat": (0.8, 0.5),               # §3
        "dokument_kandidat": (0.8, 0.5),                # §3
        "zuruf_robo": (0.9, None),                      # §3
        # ANNAHME: numbers are the formulating agent's job, not an action.
        "zahl_frist_betrag": (None, None),
    },
    "folge": {
        # ANNAHME: low-risk, and the brake guards the surface anyway.
        "neues_thema": (0.7, None),
        # ANNAHME: v2 is append-only; there is no block to "continue".
        "rueckkehr_thema": (None, None),
        "beschluss": (0.7, None),                       # §3
        # ANNAHME: a task is an append, same weight as a decision.
        "aufgabe": (0.7, None),
        # ANNAHME: open questions are frequent -- higher bar.
        "offene_frage": (0.8, None),
        # ANNAHME: the idea tag belongs to the work meeting (§2).
        "idee": (None, None),
        # ANNAHME (deviates from §3): the table says "nichts" at p >= 0.8
        # and "Rückfrage" at 0.5-0.8. A stronger signal must never get a
        # weaker reaction than a weaker one, and (act_at, ask_at) cannot
        # express a hole above the band -- so p >= 0.8 asks too.
        "recherche_kandidat": (None, 0.5),
        "dokument_kandidat": (None, 0.8),               # §3
        "zuruf_robo": (0.9, None),                      # §3
        # ANNAHME: numbers are the formulating agent's job, not an action.
        "zahl_frist_betrag": (None, None),
    },
}

_COMMON_ACTIONS = {
    "neues_thema": ("topic_block",),
    "aufgabe": ("tag_entry",),
    "offene_frage": ("tag_entry",),
    "idee": ("tag_entry",),
    "recherche_kandidat": ("research",),
    "dokument_kandidat": ("document",),
    "zuruf_robo": ("wake",),
}
# Which action(s) a signal sets off, per mode. "Tag + Bühne decision" is
# ONE action: an accepted `decision` already files the `beschluss` entry
# (StageWriter._protocol_step), so ordering a tag as well would file it
# twice. The work meeting only tags (§3).
SIGNAL_ACTIONS: dict = {
    "sitzung": {**_COMMON_ACTIONS, "beschluss": ("stage_decision",)},
    "arbeit": {**_COMMON_ACTIONS, "beschluss": ("tag_entry",)},
    "folge": {**_COMMON_ACTIONS, "beschluss": ("stage_decision",)},
}
SIGNAL_TAGS = {"beschluss": "beschluss", "aufgabe": "aufgabe",
               "offene_frage": "frage", "idee": "idee",
               "recherche_kandidat": "recherche"}

# What an action does to files and surfaces. Consulted by `enforce()`.
ACTION_EFFECTS: dict = {
    "topic_block": frozenset({"append"}),
    "tag_entry": frozenset({"append"}),
    "stage_decision": frozenset({"append"}),
    "research": frozenset({"append"}),
    "document": frozenset({"write_session"}),
    "wake": frozenset(),
    "ask": frozenset(),
}
FORBIDDEN_REFUSE = frozenset({"delete", "reorder"})
FORBIDDEN_ASK = frozenset({"write_outside", "copy_speech"})
# What replaces the main surface and therefore waits for the brake.
# `wake` does not: an order waits for nothing (StageState._main).
STAGE_MOVING = frozenset({"topic_block"})


@dataclass(frozen=True)
class Action:
    kind: str                 # one of ACTIONS
    signal: str               # the level-1 question id that set it off
    p: float
    autonomous: bool          # True: act now, *(autonom)*; False: asked/confirmed
    tag: str = ""             # tag_entry / research: the protocol tag
    effects: frozenset = frozenset()
    about: Optional["Action"] = None   # ask: what a yes would run


@dataclass
class OpenAsk:
    """The one question level 2 has on the stage, and what a yes runs."""
    action: Action
    stage_id: str = ""        # the Q-id the stage assigned
    blocks: int = 0           # blocks heard since it was asked
    # The text of the block that raised the question. A yes comes blocks
    # later ("Ja, mach das") -- the order has to name what was ASKED
    # about, not the answer. In memory only, never traced.
    block: str = ""


@dataclass
class PolicyState:
    """What route() needs to know beyond the answers. Held by the runner."""
    ask: Optional[OpenAsk] = None
    stage_question_open: bool = False   # any open question on the stage
    brake: str = ""                     # StageState.brake_check(), "" = free
    block: int = 0                      # blocks heard (the runner counts)
    settled_at: int = -1                # block in which an ask last ended
    # signal -> last block in which it may not ask again (see
    # ASK_COOLDOWN_BLOCKS). Set by settle() on every ending of an ask.
    cooldown: dict = field(default_factory=dict)


def may_ask(state: PolicyState, signal: str) -> bool:
    """May `signal` put a question on the stage in the current block?

    Not in the block that just settled an ask (the round has only just
    answered), and not while `signal` is cooling down after a no or a
    silence. Says nothing about the slot -- see `route()`.
    """
    if state.settled_at == state.block:
        return False
    return state.cooldown.get(signal, -1) < state.block


def _p(entry) -> Optional[float]:
    if not isinstance(entry, dict) or "error" in entry:
        return None
    v = entry.get("p")
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    if v != v or v < 0.0 or v > 1.0:
        return None
    return v


def probabilities(answers) -> dict:
    """question id -> p for every answered noul; a gap is simply absent."""
    entries = getattr(answers, "answers", answers) or ()
    out: dict = {}
    for e in entries:
        if isinstance(e, dict) and isinstance(e.get("question_id"), str):
            v = _p(e)
            if v is not None:
                out[e["question_id"]] = v
    return out


def zone(p: float, act_at, ask_at) -> str:
    if act_at is not None and p >= act_at:
        return "act"
    if ask_at is not None and p >= ask_at:
        return "ask"
    return "none"


def _action(kind: str, signal: str, p: float, autonomous: bool) -> Action:
    return Action(kind=kind, signal=signal, p=p, autonomous=autonomous,
                  tag=(SIGNAL_TAGS.get(signal, "")
                       if kind in ("tag_entry", "research") else ""),
                  effects=frozenset(ACTION_EFFECTS.get(kind, frozenset())))


def enforce(action: Optional[Action]) -> Optional[Action]:
    """The never-autonomous guard. None = refused outright."""
    if action is None:
        return None
    inner = action.about if action.kind == "ask" and action.about else action
    if inner.effects & FORBIDDEN_REFUSE:
        return None
    if (action.kind != "ask" and action.autonomous
            and action.effects & FORBIDDEN_ASK):
        return Action("ask", action.signal, action.p, False,
                      about=replace(action, autonomous=False))
    return action


def route(answers, mode: str, state: PolicyState) -> list:
    """The actions this block sets off, in table order, ask last."""
    mode = mode if mode in POLICY else DEFAULT_MODE
    table, wirkung = POLICY[mode], SIGNAL_ACTIONS[mode]
    ps = probabilities(answers)
    out: list = []
    fragen: list = []
    for signal, (act_at, ask_at) in table.items():
        p = ps.get(signal)
        if p is None:
            continue
        z = zone(p, act_at, ask_at)
        if z == "none":
            continue
        for kind in wirkung.get(signal, ()):
            a = _action(kind, signal, p, z == "act")
            if z == "ask":
                a = Action("ask", signal, p, False, about=a)
            a = enforce(a)
            if a is None:
                continue
            if a.kind == "ask":
                fragen.append(a)
            elif a.kind in STAGE_MOVING and state.brake:
                continue
            else:
                out.append(a)
    fragen = [a for a in fragen if may_ask(state, a.signal)]
    if fragen and state.ask is None and not state.stage_question_open:
        out.append(max(fragen, key=lambda a: a.p))
    return out


def within(target, roots) -> bool:
    """True if `target` resolves inside one of `roots`.

    Uses `Path.resolve()` -- read-only filesystem stats, not a lexical
    check -- because the vault is reached through symlinks and a purely
    lexical comparison could be escaped through one.
    """
    t = Path(target).resolve()
    for r in roots:
        try:
            t.relative_to(Path(r).resolve())
            return True
        except ValueError:
            continue
    return False


def with_target(action: Action, target, roots) -> Action:
    """Mark a file-writing action `write_outside` when `target` escapes
    every allowed root (the session folder; later the wiki layer)."""
    if within(target, roots):
        return action
    return replace(action, effects=action.effects | {"write_outside"})


# --- German: what the round sees ----------------------------------------
ASK_TEXTS = {
    "document": "Eigenes Dokument dazu anlegen?",
    "research": "Soll Robo das nachschlagen?",
    "topic_block": "Neues Thema anlegen?",
    "stage_decision": "Als Beschluss festhalten?",
    "wake": "War das an Robo gerichtet?",
}
TAG_ASK_TEXTS = {
    "beschluss": "Als Beschluss festhalten?",
    "aufgabe": "Als Aufgabe festhalten?",
    "frage": "Als offene Frage festhalten?",
    "idee": "Als Idee festhalten?",
    "recherche": "Soll Robo das nachschlagen?",
    "notiz": "Als Notiz festhalten?",
}
EFFECT_ASK_TEXTS = {
    "write_outside": "Außerhalb der Sitzung speichern?",
    "copy_speech": "Wortlaut ins Dokument übernehmen?",
}


def ask_text(action: Action) -> str:
    """The stage question: yes/no, answerable in three words."""
    inner = action.about or action
    for eff in ("write_outside", "copy_speech"):
        if eff in inner.effects:
            return EFFECT_ASK_TEXTS[eff]
    if inner.kind == "tag_entry":
        return TAG_ASK_TEXTS.get(inner.tag, "Festhalten?")
    return ASK_TEXTS.get(inner.kind, "Festhalten?")


# action kind -> (StageState pending kind, German label <= 60 chars)
PENDING = {
    "topic_block": ("topic", "Neues Thema erkannt"),
    "tag_entry": ("entry", "Eintrag wird notiert"),
    "stage_decision": ("decision", "Beschluss wird notiert"),
    "ask": ("question", "Rückfrage kommt"),
    "research": ("research", "Robo recherchiert"),
    "document": ("document", "Dokument wird angelegt"),
    "wake": ("main", "Zuruf erkannt"),
}


def anchor(text, words: int = 8) -> str:
    """The first words of a block: how an order names it to the agent."""
    clean = re.sub(r'["`\\]', "", " ".join(str(text).split()))
    return " ".join(clean.split()[:words])


# --- German (ASCII, like the rest of the prompt): the finished orders ----
ORDER_TEXTS = {
    "topic_block": (
        'AUFTRAG {id}: Mit dem Redeblock ab "{anker}" beginnt ein neues '
        "Thema. Schlage genau eine `topic`-Aktion vor: `title` (hoechstens "
        "6 Woerter) und `markdown` mit 1-3 Stichpunkten. Setze "
        '"order": "{id}".'),
    "tag_entry": (
        'AUFTRAG {id}: Fasse den Redeblock ab "{anker}" als 1-3 Eintraege '
        "mit Tag `{tag}` (je eine `tag_entry`-Aktion, ein Satz). Setze bei "
        'jedem "order": "{id}".'),
    "stage_decision": (
        'AUFTRAG {id}: Im Redeblock ab "{anker}" wurde etwas beschlossen. '
        "Schlage genau eine `decision`-Aktion vor (ein Satz: was "
        'beschlossen wurde). Setze "order": "{id}".'),
    "research": (
        'AUFTRAG {id}: Im Redeblock ab "{anker}" faellt etwas, das man '
        "nachschlagen kann. Schlag es kurz nach und lege das Ergebnis als "
        "genau eine `tag_entry`-Aktion mit Tag `recherche` ab (hoechstens "
        'zwei Saetze, Quelle nennen). Setze "order": "{id}".'),
    # MM-G D6: the consumer files the page's backlink itself -- the agent
    # must not file a second `notiz` entry for it.
    "document": (
        "AUFTRAG {id}: Die Themenseite `{ziel}` ist schon angelegt, der "
        "Verweis [[{stamm}]] steht schon im Protokoll. Ergaenze die Seite "
        "unterhalb ihrer ersten Zeilen (die bleiben stehen) um 3-7 "
        'Stichpunkte zum Thema des Redeblocks ab "{anker}", in eigenen '
        "Worten. Lege dafuer KEINEN Protokolleintrag und keine "
        "`tag_entry`-Aktion an."),
    "wake": (
        'AUFTRAG {id}: Der Redeblock ab "{anker}" war ein Zuruf an Robo, '
        "auch ohne Weckwort. Behandle ihn wie einen Auftrag mit Weckwort."),
}
ORDER_HEAD = ("\nAUFTRAEGE VON ROBO (fertig entschieden -- nur formulieren, "
              "nicht neu abwaegen):\n")
ORDER_RULES = ("  Fuer alle Auftraege: nichts loeschen, nichts umsortieren, "
               "keine woertlichen Zitate anderer in andere Dokumente "
               "kopieren, keine Datei ausserhalb von {ordner} schreiben.\n")


def order_text(action: Action, order_id: str, anchor_text: str, *,
               target: str = "", stem: str = "") -> str:
    return ORDER_TEXTS[action.kind].format(
        id=order_id, anker=anchor_text, tag=action.tag, ziel=target,
        stamm=stem)


def orders_note(lines: Sequence[str], folder) -> str:
    if not lines:
        return ""
    return (ORDER_HEAD + "".join(f"  {l}\n" for l in lines)
            + ORDER_RULES.format(ordner=folder))


# --- the open question: yes / no / no answer -----------------------------
# ANNAHME (start values, like the table): a yes counts only when the block
# is addressed to Robo (level-1 `zuruf_robo`) -- a "ja" among the round
# itself is not an answer to the stage.
CONFIRM_AT = 0.7
REJECT_BELOW = 0.3
ZURUF_AT = 0.5
ASK_MAX_BLOCKS = 2
# ANNAHME: a question the round just answered, yes or no, must not come
# straight back. After a yes, a no or no answer, the same signal stays
# silent for this many blocks after the one that settled it (a start value,
# like the table). A yes counts too: the next blocks usually still talk
# about the very thing the round just said yes to.
ASK_COOLDOWN_BLOCKS = 3


def answer(state: PolicyState, p_confirm, p_zuruf) -> tuple:
    """One more block heard while a question is open.

    (outcome, ask): "none" (nothing open), "waiting", "confirmed",
    "rejected", "expired". On every outcome but "waiting"/"none" the ask
    leaves `state` and is returned so the caller can act on it. A gap
    (None) is no answer, never a no.

    Ending an ask marks the block (`settled_at`: no new ask in it) and puts
    its signal on cooldown for the next ASK_COOLDOWN_BLOCKS blocks, on
    every ending alike -- `route()` reads both via `may_ask()`.
    """
    ask = state.ask
    if ask is None:
        return "none", None
    ask.blocks += 1
    angesprochen = p_zuruf is not None and p_zuruf >= ZURUF_AT
    outcome = "waiting"
    if p_confirm is not None and angesprochen:
        if p_confirm >= CONFIRM_AT:
            outcome = "confirmed"
        elif p_confirm < REJECT_BELOW:
            outcome = "rejected"
    if outcome == "waiting" and ask.blocks >= ASK_MAX_BLOCKS:
        outcome = "expired"
    if outcome == "waiting":
        return outcome, ask
    state.ask = None
    settle(state, ask)
    return outcome, ask


def settle(state: PolicyState, ask: OpenAsk) -> None:
    """Mark the current block as the one that ended `ask` and cool its
    signal down -- whoever ended it (`answer()`, or the stage closing the
    question under level 2's feet)."""
    state.settled_at = state.block
    state.cooldown[ask.action.signal] = state.block + ASK_COOLDOWN_BLOCKS


# --- level 0: the mode counter-check -------------------------------------
MODE_CHECK_S = 300.0
MODE_MIN_VOTES = 3
MODE_NAMES = {"sitzung": "eine Sitzung", "arbeit": "ein Arbeitsmeeting",
              "folge": "einen Folgetermin"}


def mode_check(votes, set_mode: str, *,
               min_votes: int = MODE_MIN_VOTES) -> Optional[str]:
    """The mode the votes name by strict majority, if it is not
    `set_mode`; else None. Too few answered votes: None."""
    gezaehlt = [v for v in votes if v in MODES]
    if len(gezaehlt) < min_votes:
        return None
    oben, n = Counter(gezaehlt).most_common(1)[0]
    if n * 2 <= len(gezaehlt):
        return None
    return oben if oben != set_mode else None


def mode_mirror_text(mode: str) -> str:
    return f"Robo hört {MODE_NAMES[mode]} — umschalten?"
