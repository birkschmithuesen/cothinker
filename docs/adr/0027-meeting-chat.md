# ADR 0027: Meeting chat — typed tasks for the agent through the surface contract

- **Status:** accepted
- **Date:** 2026-11-27

## Context

Source: issue #208. In the live trial the hardest part was the agent recognising what people want from speech: the
wake phrase misheard, the request cut off, the wrong topic. A text channel removes that problem and makes every
request traceable. The surface contract (ADR 0019) already has a `message` user action, but live mode accepts only
floor and call-window taps (issue #183), and nothing answers a message. Two agent paths exist: the serial turn
answers in stage proposals, with no prose reply; the call lane (`gateway/call_lane.py`, the consumer's `--lanes`)
answers a wake order in prose, on the stage. The meeting page is call-only (ADR 0021) and every surface has a fixed
path (ADR 0020).

## Decision

1. A chat message is the contract's `message` user action with `intent_id: "chat:room"`, accepted in live mode
   with 1–1000 characters of non-blank text, rate-limited per person like every action; the person is the
   server's cookie id, never a value the surface sends. A display name is the existing `participant:name` edit,
   now also accepted through `POST /actions`.
2. The thread is one view intent, `chat:room`, of a new kind `chat`, and one snapshot field, `chat`, both carrying
   the last 50 messages; each participant message carries its own status (`pending`, `answered`, `failed`,
   `unavailable`). A new block, `meeting-chat`, owns the thread; Reflect lists it as its 16th block.
3. A chat message is an order on the call lane, the same lane as a spoken wake order, without the wake phrase. The
   answer takes the call lane's stage path unchanged and is added to the chat. A session without the call lane
   answers chat messages as `unavailable`.
4. Every message and every answer is a line of the session's minutes raw transcript (`## Rohtranskript`), written
   by its one writer and marked `(Chat)`.
5. The participant chat is its own surface, `surfaces/meeting_chat/`, at a new fixed path `/chat/`. This amends
   ADR 0020's path table by one row; ADR 0021 is unchanged. The operator console embeds that page for a
   same-origin `?chat=` URL and does not call the participant contract itself.

## Consequences

- Requests no longer depend on speech recognition, and each one is traceable in the spool and the minutes.
- The serial (non-lane) path does not answer chat; a later change can add that.
- Automatic request detection can later write what it recognised into the same thread (issue #208's later step).
- A deployment's front proxy gets a `/chat/` location, configured in its private repository.
- Where the answer appears on the room display, and agent status there, stay with issue #206.

## Rejected options

- One view intent per message: live mode maps snapshot fields to intents, and a surface that loads mid-session
  needs the whole thread from one field.
- Folding chat messages into the serial turn's next batch: that turn has no prose reply to show in a chat.
- A new tag in the minutes' tag set for chat lines: that set is the curated minutes' vocabulary; a typed request
  is raw record, like a spoken one.
- A panel inside `/meeting/` or `/meeting-split/`: changes the pages ADR 0021 defines.
- A chat panel on the stage page: the stage's palette is not token-based yet (ADR 0023), and a new panel there
  would need that retrofit first.
- Reading the video call's own chat through the tap: the tap is the voice path, and the chat must work without it.
