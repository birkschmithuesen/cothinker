# CoThinker — blocks/decisions_tasks_questions: level-1 questions, decider, level-2 policy
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Building block `decisions-tasks-questions`: the level-1 question set, the
decider and the level-2 policy."""
from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping, Optional, Sequence

from shared.minutes_format import marker
from shared.modules import CoreEvent
from shared.surface import ViewIntent

from .decider import from_env as _decider_from_env
from .decider import stage_context as _stage_context

BLOCK_ID = "decisions-tasks-questions"
API_VERSION = 1


class IntentWriter:
    """What the policy runner gets instead of the stage writer (ADR 0006:
    a block never writes a surface's file).

    `announce`, `settle` and `warn` become `pending:<n>` view intents; a
    settle re-emits the SAME intent id as its announce, so a surface sees
    one pending move through its life. Everything else the runner reads or
    calls on the stage (the state, the protocol, the topic page, the
    trace) is read-through to the stage object the session host handed in.
    That is NOT all reads: `write()` (PolicyRunner._flush) and the question
    handling via `state.apply` / `state.resolve` still reach the stage
    directly. They remain direct stage writes until the main-agent turn
    becomes a block (B.8).
    """

    def __init__(self, stage, *, emit: Callable[[ViewIntent], None],
                 block_id: str, clock: Callable[[], float]) -> None:
        self._iw_stage = stage
        self._iw_emit = emit
        self._iw_block = block_id
        self._iw_clock = clock
        self._iw_book = threading.Lock()
        self._iw_n = 0
        self._iw_open: dict = {}

    def __getattr__(self, name: str):
        if name.startswith("_iw_"):
            raise AttributeError(name)
        return getattr(self._iw_stage, name)

    def _intent(self, pid: str, payload: dict, expires_at=None) -> ViewIntent:
        return ViewIntent(id=pid, block=self._iw_block, kind="status",
                          audience="room", payload=payload,
                          expires_at=expires_at)

    def _next_id(self) -> str:
        with self._iw_book:
            self._iw_n += 1
            return f"pending:{self._iw_n}"

    def announce(self, kind: str, label: str) -> str:
        """The pending's intent id. Always one: whether the stage takes the
        pending is the stage sink's business, and a settle of a pending the
        stage never took is harmless."""
        pid = self._next_id()
        with self._iw_book:
            self._iw_open[pid] = (kind, label)
        self._iw_emit(self._intent(pid, {"op": "announce", "kind": kind,
                                         "label": label}))
        return pid

    def settle(self, pending_id: str, outcome: str) -> bool:
        """True when this announce was still open. The settled intent
        expires at once: the board drops it, the stage sink applies it."""
        with self._iw_book:
            booked = self._iw_open.pop(pending_id, None)
        if booked is None:
            return False
        kind, label = booked
        self._iw_emit(self._intent(
            pending_id, {"op": "settle", "kind": kind, "label": label,
                         "outcome": outcome},
            expires_at=self._iw_clock()))
        return True

    def warn(self, text: str) -> str:
        pid = self._next_id()
        self._iw_emit(self._intent(pid, {"op": "warn", "text": text}))
        return pid


def page_call_out_note(page, level2: bool) -> str:
    """The prompt note of a Zuruf turn whose page already exists (German,
    ASCII like the rest of the prompt). "" without a page.

    The block carries the wake word, so the turn also gets ORDER_NOTE
    ("Fuehre ihn AUS ... `verlauf` **erledigt** [[dateiname]]") -- read
    alone, that makes the agent create a second page and file a second
    entry. This note says the order is already done. With level 2 the
    fill order (`ORDER_TEXTS["document"]`) rides along in the same turn,
    so the filling is not asked for twice; without it, it is asked for
    here.
    """
    if not page:
        return ""
    pfad = Path(page)
    text = (f"\nZURUF SEITE: Die Themenseite `{pfad}` zu diesem Themenblock "
            f"ist schon angelegt, der Verweis [[{pfad.stem}]] steht schon im "
            "Protokoll. Das geht dem DIREKTEN AUFTRAG oben vor: lege fuer "
            "diesen Zuruf KEINE weitere Datei und KEINEN Protokolleintrag "
            "an (auch keinen Eintrag mit **erledigt**).")
    if not level2:
        text += (" Ergaenze die Seite unterhalb ihrer ersten Zeilen (die "
                 "bleiben stehen) um 3-7 Stichpunkte zum Thema, in eigenen "
                 "Worten.")
    return text + "\n"


class DecisionsBlock:
    """Consumes `segment`; emits `block_output` `decider_result` and `entry`.

    The decider answers on its own thread, so its outputs wait in an outbox
    that the next event the block host feeds it (a `tick` at the latest)
    hands back.

    Host handles -- called directly by the session host (`gateway/consumer.py`)
    until the main agent turn becomes a block, and by nothing else:
    `attach`, `turn_orders`, `page_order`, `open_ask_id`, `end_turn`,
    `stage_context`, plus the stage's own hooks `marker_for` / `delivered`,
    `add_result_hook` and `finish`.
    """
    id = BLOCK_ID
    api_version = API_VERSION

    def __init__(self) -> None:
        self._ctx = None
        self._stage = None
        self._decider = None
        self._runner = None
        self._hooks: list = []
        self._outbox: deque = deque()
        self._results_done = 0
        self._log: Callable = print
        self._session_dir = Path(".")
        self._project_dir = Path(".")
        self._clock: Callable = datetime.now
        self._wants_page: Callable = lambda text: False
        self._order_note_for: Callable = lambda wake_word: ""

    # ------------------------------------------------ block contract
    def start(self, ctx, config: Mapping[str, object]) -> None:
        self._ctx = ctx

    def on_event(self, ev: CoreEvent) -> Sequence[CoreEvent]:
        if ev.kind == "segment":
            self._on_segment(ev.payload)
        return self._drain()

    def stop(self) -> None:
        """Nothing: the decider's backlog is finished by `finish()`, which
        the session host calls at its own point of the shutdown."""

    # ------------------------------------------------ host handles
    def attach(self, stage, *, decisions_path, session_dir, project_dir,
               events=None, log: Callable = print, policy: bool = False,
               no_agent: bool = False, mode: str = "sitzung",
               wants_page: Optional[Callable] = None,
               order_note_for: Optional[Callable] = None,
               clock: Callable = datetime.now, env=None,
               **decider_kw) -> None:
        """Bind the block to the session host's stage and start what
        `COTHINKER_DECIDER` (and `policy`) ask for.

        `stage` is the host's stage writer: the block reads the stage's
        state and protocol and files topic pages through it, but whatever
        it announces on the stage goes out as view intents.
        `wants_page` / `order_note_for` are the host's own text helpers.
        Decider provider configuration is the environment's, as before.
        """
        self._stage = stage
        self._log = log
        self._session_dir = Path(session_dir)
        self._project_dir = Path(project_dir)
        self._clock = clock
        if wants_page is not None:
            self._wants_page = wants_page
        if order_note_for is not None:
            self._order_note_for = order_note_for
        decider = _decider_from_env(decisions_path, events=events, log=log,
                                    env=env, **decider_kw)
        if decider is not None:
            decider.on_result = self._on_result
            decider.start()
            log(f"decider on: {decider.backend_name} -> "
                f"{Path(decisions_path).name}")
        self._decider = decider
        if not policy:
            return
        if decider is None:
            log("policy off: needs COTHINKER_DECIDER")
        elif stage.protocol is None:
            log("policy off: needs a v2 protocol")
        elif no_agent:
            log("policy off: nothing would formulate its orders (--no-agent)")
        else:
            # Imported only here: without level 2 the runner module is
            # never loaded.
            from .policy_runner import PolicyRunner
            writer = IntentWriter(stage, emit=self._ctx.emit, block_id=BLOCK_ID,
                                  clock=time.time)
            self._runner = PolicyRunner(
                writer, mode=mode, session_dir=session_dir,
                project_dir=project_dir, backend=decider.backend, log=log)
            self._runner.on_entry = self._on_entry
            stage.policy = self
            log(f"policy on: mode {mode}")

    @property
    def decider_on(self) -> bool:
        return self._decider is not None

    @property
    def policy_on(self) -> bool:
        return self._runner is not None

    def add_result_hook(self, fn: Callable) -> None:
        """`fn(result, snap, reason)` runs on the decider's thread after the
        block's own handling of each result."""
        self._hooks.append(fn)

    def stage_context(self, state) -> dict:
        return _stage_context(state)

    def turn_orders(self, segment) -> tuple:
        """What one turn adds to `order_note`, and the spoken text: the Zuruf
        page first (so level 2's fill order rides along with THIS turn),
        then level 2's orders, then the note that the Zuruf's page already
        exists. Without level 2: ("", segment.text) plus the page note."""
        seite = self._page_call_out(segment)
        extra_note, gesprochen = self._policy_turn(segment)
        return (extra_note + page_call_out_note(seite, self._runner is not None),
                gesprochen)

    def page_order(self, text: str):
        """The Zuruf "Robo, leg dafuer eine Seite an": the topic page through
        level 2, None without it. Never raises."""
        if self._runner is None:
            return None
        return self._runner.page_order(text)

    def open_ask_id(self) -> str:
        return self._runner.open_ask_id() if self._runner is not None else ""

    def end_turn(self, *args, **kw) -> None:
        if self._runner is not None:
            self._runner.end_turn(*args, **kw)

    def marker_for(self, order_id) -> str:
        return self._runner.marker_for(order_id) if self._runner is not None else ""

    def delivered(self, order_id) -> None:
        if self._runner is not None:
            self._runner.delivered(order_id)

    def finish(self) -> None:
        """Shutdown of level 1 and 2, bounded (`decider.FINISH_TIMEOUT_S`).
        Level 2 is detached first and closed: the agent is gone, and an
        action on the backlog would freeze into the final stage file."""
        decider = self._decider
        if decider is None:
            return
        if self._runner is not None:
            decider.on_result = None
            try:
                self._runner.close()
            except (Exception, KeyboardInterrupt) as e:  # noqa: BLE001
                self._log("policy close failed:", repr(e))
        try:
            decider.finish()
        except KeyboardInterrupt:
            self._log("decider: finish aborted, the raw transcript is complete")

    def wait_for_results(self, count: int, timeout: float = 5.0) -> bool:
        """Waits until the decider has handled `count` results (tests)."""
        end = time.monotonic() + timeout
        while self._results_done < count:
            if time.monotonic() >= end:
                return False
            time.sleep(0.01)
        return True

    # ------------------------------------------------ segments and results
    def _on_segment(self, payload: Mapping) -> None:
        if self._decider is None:
            return
        at = payload.get("at")
        stamp = None
        if isinstance(at, (int, float)) and not isinstance(at, bool):
            stamp = datetime.fromtimestamp(at).astimezone()
        state = getattr(self._stage, "state", None)
        self._decider.submit(payload.get("text", ""),
                             reason=payload.get("reason", ""),
                             chars=payload.get("chars"), at=stamp,
                             **_stage_context(state))

    def _on_result(self, result, snap, reason: str = "") -> None:
        try:
            if self._runner is not None:
                self._runner.on_result(result, snap, reason)
            self._outbox.append({
                "from": BLOCK_ID, "kind": "decider_result",
                "block": self._results_done + 1, "reason": reason,
                "answers": list(result.answers)})
            for fn in list(self._hooks):
                try:
                    fn(result, snap, reason)
                except Exception as e:        # noqa: BLE001 -- one hook never stops the next
                    self._log("decider result hook failed:", type(e).__name__)
        finally:
            self._results_done += 1

    def _on_entry(self, tag: str, text: str, topic_id: str) -> None:
        self._outbox.append({"from": BLOCK_ID, "kind": "entry", "tag": tag,
                             "text": text, "topic_id": topic_id})

    def _drain(self) -> list:
        out = []
        while True:
            try:
                payload = self._outbox.popleft()
            except IndexError:
                return out
            out.append(CoreEvent("block_output", payload))

    # ------------------------------------------------ the turn's share
    def _policy_turn(self, b) -> tuple:
        """Level 2's share of one turn: (extra order note, spoken).

        Takes the orders the policy decided since the last turn -- they ride
        along with this one, finished, the agent only formulates -- and turns
        them into what the turn needs: the note appended to the prompt's
        `order_note`, and the text `ingest()` checks the proposals' quotes
        against. A wake order the policy made out of a block without the wake
        word gets the same order note a call-out gets; its evidence is the
        block it was decided on, which may have been said before this turn's
        own block. Without a runner: ("", b.text).
        """
        if self._runner is None:
            return "", b.text
        auftraege = self._runner.take_turn()
        extra_note = auftraege.note
        if auftraege.wake and not b.wake_word:
            extra_note += self._order_note_for(True)
        spoken = b.text + (" " + auftraege.spoken if auftraege.spoken else "")
        return extra_note, spoken

    def _page_call_out(self, b) -> Optional[Path]:
        """The Zuruf "Robo, leg dafuer eine Seite an". Never raises.

        Only for a block that carries the wake word, only into a v2
        protocol, only when the host's `wants_page` says so. An explicit
        order is a confirmation, not an autonomous act, in every mode: the
        marker is `*(bestätigt HH:MM)*`. With level 2 the page goes through
        it, so the agent gets the order to fill it in this very turn;
        without it only skeleton and backlink are created. Returns the page:
        the one created now, or the one the current block already has; None
        when there is none.
        """
        stage = self._stage
        try:
            if (not getattr(b, "wake_word", False) or stage is None
                    or stage.protocol is None
                    or not self._wants_page(getattr(b, "text", ""))):
                return None
            if self._runner is not None:
                seite = self.page_order(b.text)
            else:
                jetzt = self._clock()
                seite = stage.file_topic_page(
                    stage.page_title(), session_dir=self._session_dir,
                    project_dir=self._project_dir,
                    mark=marker(False, f"{jetzt:%H:%M}"), created=jetzt)
            return seite if seite is not None else stage.topic_page()
        except Exception as e:                # noqa: BLE001 -- agent thread
            self._log("topic page call-out skipped:", repr(e))
            return None
