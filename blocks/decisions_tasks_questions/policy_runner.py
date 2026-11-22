#!/usr/bin/env python3
# CoThinker — level 2 at runtime: from answers to pendings and orders
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software under the GNU Affero General Public
# License, version 3. It comes WITHOUT ANY WARRANTY. See LICENSE.
"""Runs gateway/policy.py against the live stage. Called by the decider
thread (gateway/decider.py `on_result`) once per block, and by the agent
thread around each turn (take_turn / end_turn), and by the writer when a
proposal carries an order id (marker_for / delivered).

THE PROMISE of level 2 (docs/entwurf-meeting-modi.md §3/§4): every action
announces a pending AT ONCE, and whatever needs words goes to the language
agent as a FINISHED order -- it formulates, it never decides. The order
rides along in the next turn's prompt.

NEVER RAISES out of on_result(): a failing level 2 costs nothing but
itself. Text-free trace: tokens and numbers only; block text reaches the
prompt (via the order's anchor) and nothing else -- no log, no trace.

LOCKS. Own `_lock` is a leaf for the order book and for every
check-and-swap of `policy.ask` (decider thread: `pol.answer`, `_refresh`,
`_ask`; agent thread: the Zuruf's `_supersede_document_ask`; `close`). Stage changes go through
`StageWriter` (writer lock, then state lock -- the documented order); this
module never holds its own lock while calling the writer.

CALLERS MUST NOT HOLD `StageState._lock` when they call into this module
(`on_result`, `delivered`, `end_turn`). Every one of them may write the
stage, and writing takes `StageWriter._lock` FIRST: a caller that already
holds the state lock and then waits for the writer lock is the reverse
order, and a deadlock against the ticker. Holding `StageWriter._lock`
(an RLock) is fine -- `StageWriter.ingest` may call `delivered()`.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import decide as d
from . import policy as pol
from shared.events import is_token
from shared.minutes_format import marker, pending_label
from shared.wiki_names import page_path, page_slug
from .questions import CONFIRM_ID, CONFIRM_QUESTIONS, MODE_ID, MODE_QUESTIONS

# The signal token of a page ordered by Zuruf (MM-G D7), for the trace.
ZURUF_SIGNAL = "zuruf_seite"


@dataclass
class Order:
    id: str
    action: pol.Action
    text: str
    pending_id: str
    mark: str
    spoken: str = ""
    delivered: bool = False


@dataclass
class TurnOrders:
    note: str = ""        # orders_note(...) for build_prompt's order_note
    spoken: str = ""      # texts of blocks a `wake` order refers to
    wake: bool = False    # at least one wake order


def _token(e: BaseException) -> str:
    t = getattr(e, "token", None)
    if is_token(t):
        return t
    n = type(e).__name__
    return n if is_token(n) else "exception"


class PolicyRunner:
    def __init__(self, writer, *, mode: str, session_dir, backend,
                 decide_fn: Optional[Callable] = None,
                 log: Callable = print, now: Callable = time.monotonic,
                 clock: Callable = datetime.now,
                 allowed_roots=None, project_dir=None) -> None:
        self.writer = writer
        self.state = writer.state
        self.mode = mode if mode in pol.POLICY else pol.DEFAULT_MODE
        self.session_dir = Path(session_dir)
        # The round's documents folder: the topic page's `sources` path is
        # relative to it. Without one, the session folder stands in.
        self.project_dir = Path(project_dir if project_dir is not None
                                else session_dir)
        # MM-G D4: the topic page lives in `<session>/entwuerfe/`, inside
        # the session folder -- still the only allowed root.
        self.allowed_roots = tuple(Path(r) for r in
                                   (allowed_roots or (self.session_dir,)))
        self.backend = backend
        self.decide_fn = decide_fn or d.decide
        self.log = log
        self._now = now
        self._clock = clock
        self.policy = pol.PolicyState()
        self._lock = threading.Lock()
        self._queued: list = []
        # The orders of the turn that is running: moved here by
        # take_turn(), emptied by end_turn(). Only these ids are honoured
        # by marker_for() / delivered() -- an agent reusing an order id
        # from an earlier turn of its conversation history must not stamp
        # an unrelated entry with a marker.
        self._sent: list = []
        self._closed = False
        self._counter = 0
        self._t0 = now()
        self._votes: list = []
        self._mode_done = False
        # callable(tag, text, topic_id): told about every protocol entry
        # this runner files itself (the topic page's backlink). The block
        # turns it into its `entry` output; None: nobody listens.
        self.on_entry: Optional[Callable] = None

    # ------------------------------------------------ decider thread
    def on_result(self, result, snap, reason: str = "") -> None:
        try:
            self._on_result(result, snap, reason)
        except Exception as e:                # noqa: BLE001 -- see docstring
            self._trace("policy_error", reason=_token(e))
            self._say("policy skipped:", _token(e))

    def _on_result(self, result, snap, reason: str) -> None:
        if self._closed or self.writer.protocol is None:
            # Shut down (close()), or the protocol stopped being v2 mid-run
            # and the writer let go of it: level 2 files into a v2
            # protocol only, so from here on it does nothing at all.
            return
        self.policy.block += 1        # the cooldown clock of policy.py
        self._refresh()
        by = pol.probabilities(result)
        extra = []
        if self.policy.ask is not None:
            extra += list(CONFIRM_QUESTIONS)
        if not self._mode_done:
            extra += list(MODE_QUESTIONS)
        extra_by: dict = {}
        if extra:
            try:
                # JEV-SITE: level 2's own questions -- "does `neu` say yes
                # to the open stage question?" and "which kind of meeting
                # is this?" -- in ONE extra decide() call, separate from
                # the level-1 set so its hash stays comparable.
                r = self.decide_fn(snap, tuple(extra), backend=self.backend)
                extra_by = r.by_id()
            except Exception as e:            # noqa: BLE001
                self._trace("policy_error", reason=_token(e))
        if self.policy.ask is not None:
            # JEV-SITE: the confirm answer is a noul p (gap -> None, i.e.
            # no answer, never a no); `zuruf_robo` from level 1 says the
            # block was addressed to Robo.
            p_ja = pol.probabilities([extra_by.get(CONFIRM_ID)]).get(CONFIRM_ID)
            # Under the lock: the agent thread's Zuruf may end the same ask
            # (_supersede_document_ask) -- it is settled by one of them.
            with self._lock:
                outcome, ask = pol.answer(self.policy, p_ja,
                                          by.get("zuruf_robo"))
            self._settle_ask(outcome, ask)
        if not self._mode_done:
            # JEV-SITE: the mode vote is one of the question's own choices
            # (decide.interpret guarantees it) or absent.
            wahl = (extra_by.get(MODE_ID) or {}).get("choice")
            if wahl:
                self._votes.append(wahl)
            if self._now() - self._t0 >= pol.MODE_CHECK_S:
                self._mode_done = True
                anders = pol.mode_check(self._votes, self.mode)
                if anders:
                    self._apply({"action": "mirror",
                                 "text": pol.mode_mirror_text(anders)})
                    self._trace("mode_mismatch", mode=anders,
                                count=len(self._votes))
        self._refresh()
        # JEV-SITE: level-1 p values are interpreted here -- route() turns them into actions by table
        for action in pol.route(result, self.mode, self.policy):
            if action.kind == "wake" and reason == "wake":
                continue          # the wake word already made it an order
            self._execute(action, marker(True), snap.block)

    def _refresh(self) -> None:
        """Read the stage's question slot and brake into PolicyState."""
        frage = self.state.question
        offen = frage is not None and frage.state == "open"
        beendet = None
        with self._lock:                  # check-and-swap, see _supersede_…
            ask = self.policy.ask
            if ask is not None and not (offen and frage.id == ask.stage_id):
                # Resolved or replaced by someone else (the agent, a
                # timeout). Ended all the same: no new ask in this block,
                # and the same cooldown as an answered one -- else it comes
                # straight back.
                self.policy.ask = None
                pol.settle(self.policy, ask)
                beendet, ask = ask, None
        if beendet is not None:
            self._trace("ask_discarded", id=beendet.stage_id,
                        reason="stage_closed")
        self.policy.stage_question_open = offen and ask is None
        self.policy.brake = self.state.brake_check()

    def _settle_ask(self, outcome: str, ask) -> None:
        if outcome in ("none", "waiting") or ask is None:
            return
        # `answer()` already took the ask out of PolicyState: whatever
        # raises below, the stage question must not stay open without an
        # owner, so it is resolved in `finally`.
        text = "Ja" if outcome == "confirmed" else "verworfen"
        try:
            if outcome == "confirmed":
                self._trace("ask_confirmed", id=ask.stage_id,
                            blocks=ask.blocks)
            else:
                self._trace("ask_discarded", id=ask.stage_id,
                            reason="rejected" if outcome == "rejected"
                            else "no_answer", blocks=ask.blocks)
        finally:
            self._resolve(ask.stage_id, text)
        if outcome == "confirmed":
            self._confirmed(ask)

    def _confirmed(self, ask) -> None:
        """Run what the round said yes to -- anchored to the block that
        raised the question, not to the "Ja" that answered it."""
        action = ask.action
        if action.kind in pol.STAGE_MOVING:
            # A yes answers a question asked blocks ago, and the surface
            # may have moved since: the same brake as an autonomous one.
            bremse = self.state.brake_check()
            if bremse:
                self._trace("policy_refused", kind=action.kind,
                            reason=bremse.replace(" ", "_"))
                return
        stamp = self._clock().strftime("%H:%M")
        self._execute(action, marker(False, stamp), ask.block)

    def _execute(self, action: pol.Action, mark: str,
                 block: str) -> Optional[Path]:
        """Run one action. Returns the topic page a `document` action
        created, else None."""
        if self._closed:
            return
        if action.kind == "ask":
            self._ask(action, block)
            return
        ziel = stem = titel = ""
        pfad = None
        if action.kind == "document":
            # MM-G D4: the target is the topic page in `<session>/entwuerfe/`
            # -- inside the session folder, so the `write_outside` branch
            # never fires today. It is the guard for future targets -- keep
            # it. The title is read ONCE: guard and page use the same one --
            # the protocol's current topic block (writer.page_title()).
            titel = self.writer.page_title()
            jetzt = self._clock()
            geplant = page_path(self.session_dir,
                               page_slug(titel, jetzt))
            geprueft = pol.enforce(pol.with_target(
                action, geplant, self.allowed_roots))
            if geprueft is None:
                self._trace("policy_refused", kind=action.kind)
                return
            if geprueft.kind == "ask":
                # No question about a page the block already has (the
                # Zuruf got there first). Only a read of the registry --
                # the one-per-block rule itself is `file_topic_page`'s.
                if self.writer.topic_page() is not None:
                    self._trace("policy_refused", kind="document",
                                reason="page_exists")
                    return
                self._ask(geprueft, block)
                return
            # Created HERE, with its marker and links, whatever the agent
            # writes below it -- and its backlink filed by the consumer
            # (D6), not by the agent.
            pfad = self.writer.file_topic_page(
                titel, session_dir=self.session_dir,
                project_dir=self.project_dir, mark=mark, created=jetzt,
                report=lambda grund: self._trace(
                    "policy_refused", kind="document", reason=grund))
            if pfad is None:
                return
            ziel, stem = str(pfad), pfad.stem
            self._entry("notiz", f"Themenseite [[{stem}]]")
        # MM-L: the label names the WHAT -- a prefix plus <= 3 content
        # words (for a page: its title). It reaches buehne.json only; the
        # trace's pending_start carries id and kind, never the label.
        pkind, fallback = pol.PENDING[action.kind]
        label = pending_label(pkind, block, tag=action.tag, title=titel,
                                  fallback=fallback)
        pending_id = self.writer.announce(pkind, label)
        with self._lock:
            spaet = self._closed
            if not spaet:
                self._counter += 1
                oid = f"A{self._counter}"
                o = Order(id=oid, action=action, pending_id=pending_id,
                          mark=mark,
                          text=pol.order_text(action, oid, pol.anchor(block),
                                              target=ziel, stem=stem),
                          spoken=block if action.kind == "wake" else "")
                self._queued.append(o)
        if spaet:
            # close() ran while this action was on its way: no agent will
            # ever take the order, so its pending must not stand.
            if pending_id and self.writer.settle(pending_id, "shutdown"):
                self._flush()
            return pfad
        self._trace("policy_action", id=oid, kind=action.kind,
                    signal=action.signal, autonomous=action.autonomous,
                    mode=self.mode)
        return pfad

    def _ask(self, action: pol.Action, block: str = "") -> None:
        if self._closed:
            return
        self._refresh()
        if (self.policy.ask is not None or self.policy.stage_question_open
                or not pol.may_ask(self.policy, action.signal)):
            return
        # ONE critical section for "is the slot free?" and "put the
        # question up": the agent's writer thread can land a question in
        # between, and applying ours then would PARK Robo's own
        # meta-question. Writer lock first, then state lock (both RLocks);
        # the pending is announced only once the slot is known free.
        pending_id = ""
        with self.writer._lock:              # noqa: SLF001 -- lock order
            with self.state._lock:           # noqa: SLF001
                frage = self.state.question
                if frage is not None and frage.state == "open":
                    grund, frage = "question open", None
                else:
                    pkind, fallback = pol.PENDING["ask"]
                    pending_id = self.writer.announce(pkind, pending_label(
                        pkind, pol.ask_text(action), fallback=fallback))
                    grund = self.state.apply({"action": "question",
                                              "text": pol.ask_text(action)})
                    frage = self.state.question
        self._flush()
        if grund or frage is None:
            if pending_id:
                self.writer.settle(pending_id, "refused")
                self._flush()
            self._trace("policy_refused", kind="ask",
                        reason=(grund or "no_question").replace(" ", "_"))
            return
        with self._lock:
            self.policy.ask = pol.OpenAsk(action=action.about or action,
                                          stage_id=frage.id, block=block)
        self._trace("ask_opened", id=frage.id, signal=action.signal,
                    mode=self.mode)

    def _apply(self, action: dict) -> str:
        with self.writer._lock:              # noqa: SLF001 -- lock order
            grund = self.state.apply(action)
        self._flush()
        return grund

    def _resolve(self, stage_id: str, text: str) -> None:
        if stage_id:
            with self.writer._lock:          # noqa: SLF001 -- lock order
                self.state.resolve(stage_id, text)
            self._flush()

    # ------------------------------------------------ agent thread
    def page_order(self, block: str) -> Optional[Path]:
        """The Zuruf "Robo, leg dafuer eine Seite an" (MM-G D7): the SAME
        page path as the `document` action, in every mode -- an explicit
        order is a confirmation, not an autonomous act, so the marker is
        `*(bestätigt HH:MM)*` and no question is asked. Returns the page,
        None when none was created. Never raises.

        Level 2's own open question about a document ("Eigenes Dokument
        dazu anlegen?") is answered by the Zuruf: it is closed first, with
        the same settle (cooldown) as any other ending, so a later "Ja"
        cannot create a second page.

        The caller must NOT hold `StageState._lock` or `StageWriter._lock`
        (the page's backlink takes `MinutesPort.lock`)."""
        try:
            if self._closed or self.writer.protocol is None:
                return None
            self._supersede_document_ask()
            # The effects come from the table, not from here.
            action = pol.Action(
                "document", ZURUF_SIGNAL, 1.0, False,
                effects=frozenset(pol.ACTION_EFFECTS["document"]))
            stamp = self._clock().strftime("%H:%M")
            return self._execute(action, marker(False, stamp), block)
        except Exception as e:                # noqa: BLE001
            self._trace("policy_error", reason=_token(e))
            return None

    def _supersede_document_ask(self) -> None:
        """End level 2's open ask if it is about a `document`: the round
        ordered the page outright. Settled like every other ending
        (`pol.settle`: no new ask in this block, cooldown for the signal);
        the stage question, if still ours and open, is resolved "Ja" --
        the round did say yes, by Zuruf.

        Agent thread, while the decider may be in `pol.answer` / `_refresh`
        on the same ask: the check-and-swap runs under `self._lock` (a leaf
        -- nothing below takes another lock), so exactly one of them ends
        it. Tracing and resolving happen after the lock is released
        (writer lock, then state lock, as documented above)."""
        with self._lock:
            ask = self.policy.ask
            if ask is None or ask.action.kind != "document":
                return
            self.policy.ask = None
            pol.settle(self.policy, ask)
        frage = self.state.question
        noch_offen = (frage is not None and frage.state == "open"
                      and frage.id == ask.stage_id)
        try:
            self._trace("ask_discarded", id=ask.stage_id,
                        reason="superseded", blocks=ask.blocks)
        finally:
            if noch_offen:
                self._resolve(ask.stage_id, "Ja")

    def open_ask_id(self) -> str:
        """The stage id of level 2's own open question, "" if none.

        For the agent's prompt: that question is Robo's to evaluate, so
        the agent must neither resolve it nor act on the room's answer.
        Thread-safe: one read of the `ask` reference (atomic), then only
        that snapshot is used -- the decider thread may swap it any time."""
        ask = self.policy.ask
        return ask.stage_id if ask is not None else ""

    def take_turn(self) -> TurnOrders:
        with self._lock:
            jetzt, self._queued = self._queued, []
            self._sent.extend(jetzt)
        if not jetzt:
            return TurnOrders()
        return TurnOrders(
            note=pol.orders_note([o.text for o in jetzt], self.session_dir),
            spoken=" ".join(o.spoken for o in jetzt if o.spoken),
            wake=any(o.action.kind == "wake" for o in jetzt))

    def _current(self, order_id) -> Optional[Order]:
        """The order `order_id` if it was SENT IN THE RUNNING TURN (taken
        by the latest take_turn(), not yet ended by end_turn()); None for
        every other id, however it looks. Caller holds `self._lock`."""
        if not is_token(order_id):
            return None
        for o in self._sent:
            if o.id == order_id:
                return o
        return None

    def marker_for(self, order_id) -> str:
        with self._lock:
            o = self._current(order_id)
            return o.mark if o is not None else ""

    def delivered(self, order_id) -> None:
        """The agent's proposal for `order_id` arrived: settle its pending.

        The caller must NOT hold `StageState._lock` -- this writes the
        stage, which takes `StageWriter._lock` first. Holding the writer
        lock is fine (RLock; `StageWriter.ingest` calls this)."""
        with self._lock:
            o = self._current(order_id)
            if o is None or o.delivered:
                return
            o.delivered = True
        # False when the state already settled it (a topic arrives as
        # `topic` through StageState.apply) -- harmless.
        if o.pending_id and self.writer.settle(o.pending_id, "arrived"):
            self._flush()

    def end_turn(self, ok: bool, answered: bool = False) -> None:
        """Settle the pendings of every order of this turn that brought
        nothing: `unanswered` (ok) or `failed`.

        `answered`: the turn put a new main surface up for a call-out
        (consumer.answered_the_call). A wake order has no proposal of its
        own to be delivered by, so that is its arrival.

        The caller must NOT hold `StageState._lock` -- this writes the
        stage, which takes `StageWriter._lock` first."""
        with self._lock:
            offen = [o for o in self._sent if not o.delivered]
            self._sent = []
            for o in offen:
                o.delivered = True
        settled = False
        for o in offen:
            if o.pending_id:
                if answered and o.action.kind == "wake":
                    ausgang = "arrived"
                elif ok and o.action.kind == "document":
                    # The page and its backlink exist since _execute(); the
                    # agent fills it in place and sends no proposal (D6), so
                    # a turn that went through is its arrival.
                    ausgang = "arrived"
                else:
                    ausgang = "unanswered" if ok else "failed"
                settled |= self.writer.settle(o.pending_id, ausgang)
        if settled:
            self._flush()

    def close(self) -> None:
        """Shutdown: level 2 stops acting for good. on_result() becomes a
        no-op, and the orders no agent will ever take any more are settled
        so their pendings do not freeze into the final stage file. An open
        question of level 2's own is resolved "verworfen" for the same
        reason: nobody will evaluate the answer any more.

        Main thread, after the agent has stopped. Must NOT hold
        `StageState._lock` (it writes the stage)."""
        with self._lock:
            self._closed = True
            rest = self._queued + [o for o in self._sent if not o.delivered]
            self._queued, self._sent = [], []
            for o in rest:
                o.delivered = True
            ask, self.policy.ask = self.policy.ask, None
        settled = False
        for o in rest:
            if o.pending_id:
                settled |= self.writer.settle(o.pending_id, "shutdown")
        if settled:
            self._flush()
        if ask is not None:
            frage = self.state.question
            noch_offen = (frage is not None and frage.state == "open"
                          and frage.id == ask.stage_id)
            try:
                self._trace("ask_discarded", id=ask.stage_id,
                            reason="shutdown" if noch_offen
                            else "stage_closed", blocks=ask.blocks)
            finally:
                if noch_offen:
                    self._resolve(ask.stage_id, "verworfen")

    # ------------------------------------------------ helpers
    def _record_ticks(self) -> None:
        """Drain the state's queued events (pending_end, question_cleared)
        into the trace -- `settle()` and `resolve()` only queue them."""
        if self.writer.events is not None:
            self.writer.events.record_all(self.state.tick())

    def _flush(self) -> None:
        """Trace what the state queued, then rewrite the stage file so a
        settled pending leaves the page. Outside `self._lock`."""
        try:
            self._record_ticks()
        except Exception:                     # noqa: BLE001
            pass
        self.writer.write()                   # never raises

    def _trace(self, event: str, **fields) -> None:
        try:
            self.writer.trace(event, **fields)
            self._record_ticks()
        except Exception:                     # noqa: BLE001
            pass

    def _entry(self, tag: str, text: str) -> None:
        """Report one entry this runner filed. Never raises."""
        try:
            if self.on_entry is not None:
                self.on_entry(tag, text, getattr(self.state.main, "id", "") or "")
        except Exception:                     # noqa: BLE001
            pass

    def _say(self, *parts) -> None:
        try:
            self.log(*parts)
        except Exception:                     # noqa: BLE001
            pass
