# ADR 0024: Call window replaces the instant wake-word cut

- **Status:** accepted
- **Date:** 2026-11-24

## Context

Source: live trial 2026-11-24 (GitHub issue #180). The wake word used to cut and send the block the instant it
matched: "Hey Robo. Ich gebe dir." went out alone, and the content of the sentences that followed it never reached
the agent. A one-breath order ("Hey Robo, fass zusammen") still has to work, but an order spoken over several
sentences, with pauses between them, needs a window that stays open until the order is actually finished.

## Decision

The wake word (`shared.wake_word.WAKE_PHRASE_RE`) OPENS a call window instead of cutting immediately
(`gateway.segmenter.BlockBuilder`: idle/listening state, `open_call(source)`/`close_call(source)`, `state()`).
Every sentence spoken while it is open belongs to the call -- a pause does not cut it. The window closes on:

- the end phrase in a sentence ("Robo, Ende" / "Robo Ende" / "Danke, Robo" --
  `shared.wake_word.END_PHRASE_RE`, same prefix/false-friend rules as the wake phrase; stripped from the order),
- a tap (`close_call("tap")`),
- `--call-silence-s` of silence (default 8 s), or
- `--call-max-s` at the latest (default 90 s).

Exactly one block goes out on close: `wake_word=True`, `reason` one of `end|tap|silence|max`. A tap that closes an
empty window (nothing said since it opened) sends nothing and is recorded as `reason=cancel`.

The window's state is surfaced through the existing block/view-intent machinery (ADR 0006, ADR 0008), not a new
mechanism: a new building block `blocks/call_window/` (`CallWindowBlock`) consumes `action` events with
`intent_id == "listen:room"` (`op: open|close|toggle`) and `block_output` events shaped `{"call_window": state}`
the host dispatches after every transition, and emits the view intent `listen:room` (kind `listen`). The session
host (`gateway/consumer.py`, `gateway.block_host_wiring.wire_call_window`/`dispatch_call_window_state`) binds the
block to the real `BlockBuilder.open_call`/`close_call`/`state` -- the same interim host-handle pattern
`blocks/knowledge_link`'s `use_project` already uses. The stage renders `listen:room` through the same generic
region mechanism `queue`/`voice`/`share`/`session_graph`/`knowledge` already use (`gateway/stage_surface.py`), so
`buehne.json` and `/api/snapshot` (ADR 0019, live mode) carry it without a stage-specific special case.

The surface contract (ADR 0019) gains, additively:

- `ListenSnapshot` (`surfaces/contract/models.py`): `state: "idle"|"listening"`, `since: float|None` (epoch
  seconds), `opened_by: "voice"|"tap"|None`, `closes_in_s: float|None` (silence countdown), `last_close:
  {"reason": "end"|"tap"|"silence"|"max"|"cancel", "at": float}|None` -- the exact shape of
  `BlockBuilder.state()`.
- `StageStateSnapshot.listen: ListenSnapshot | None` (key-absent-means-hidden, same convention as `queue`/`voice`).
- `ListenIntent`, kind `listen`, registered in the `ViewIntent` union; the same shape as `ListenSnapshot` plus the
  envelope's `kind`/`id`.
- A `TapAction` with `intent_id: "listen:room"`, `target: "listen-toggle"` is the shape a surface's toggle button
  sends; how that action reaches the live core (`POST /api/actions`) is tracked separately (issue #183) and is not
  part of this decision.
- The "meeting" fixture scenario demonstrates one full listening phase (open by voice, close with `reason: end`).

One round toggle button, on the stage and on the participant view (same page, ADR 0021): green "Robo hört" while
listening, red/neutral "Robo" while idle, icon plus word (colour is never the only signal). A tap while idle opens;
a tap while listening closes and sends, or cancels if the call is empty.

## Consequences

- A sentence addressed to Robo is no longer at risk of being cut mid-order; the trade-off is that a silent gap of
  up to `--call-silence-s` after the LAST content sentence is unavoidable latency before the agent sees the order.
- Every surface that shows call state now reads one consistent shape (`listen:room`) instead of inferring it from
  the mirror text or the absence of a cut.
- `gateway/segmenter.py`'s `BlockBuilder` grows a second responsibility (window state) next to its original one
  (pause/length cutting); both stay inside one class because they share the same lock and the same sentence stream
  (documented in the module's own CONCURRENCY note).

## Rejected options

- Keep the instant cut and instead re-assemble the order downstream from several blocks: the agent would still see
  a fragment first and have no reliable signal that more was coming, reproducing the live incident.
- A fixed "N seconds after the wake word" window instead of an explicit close: either too short for a real order or
  too long for a short one; the end phrase, the tap and the two timeouts together cover both without guessing a
  duration.
- Model the window as a new core API (a sixth, next to ADR 0002's five) instead of a building block: it fits ADR
  0008's "everything that works is a block" exactly as the speaker queue and the timer already do.
