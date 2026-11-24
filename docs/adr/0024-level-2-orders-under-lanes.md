# ADR 0024: Level-2 orders under `--lanes` run as orders turns on the serial worker

- **Status:** proposed
- **Date:** 2026-11-24

## Context

Source: issue #178 (level-2 orders end discarded under `--lanes`) and issue #129 (`tasks` as a planned field of
the surface contract).

Level 2 (`--policy`, the `decisions-tasks-questions` block's policy runner)
announces a pending at once for every decision and task the decider
recognises, and queues a finished order for the language agent: "it
formulates, it never decides". The serial consumer hands the queued orders
to the agent in its next turn (`turn_opened`), applies the agent's answer
(`StageWriter.ingest`) and settles what brought nothing (`turn_closed`).

Under `--lanes` (the call lane and the mirroring coach) a finished block
goes to the lanes and never reaches `RunQueue`, so no turn ever takes the
orders. Every pending ends `discarded` after `PENDING_TIMEOUT_S`, and
`buehne.json` keeps `decisions` empty and has no `tasks` -- with the
deployment unit that runs `--policy --lanes` (issue #178). `tasks` was a
planned field of the surface contract (issue #129).

## Decision

Under `--lanes --policy`, a decider result hook enqueues one `orders` block
on the existing serial `RunQueue` whenever the policy runner has orders
waiting; `run_turn` runs it unchanged (`turn_opened` -> agent ->
`ingest` -> `turn_closed`) except that only answers to orders (`decision`,
`tag_entry` carrying an `order` id) are applied and no minutes items are
filed. A filed answer to an `aufgabe` order becomes a task (`text`,
`person`, `due` as the meeting named them) in the block's `tasks` view
intent and in `buehne.json`'s `tasks`.

## Consequences

- Decisions and tasks reach the stage under `--lanes`; their pendings
  settle `arrived` within one agent turn instead of `discarded`.
- No new process, queue or lock: the serial `WorkerClient` starts on the
  first orders turn only, so `--lanes` without `--policy`, or a meeting in
  which level 2 orders nothing, costs no agent call.
- The serial path's turn is unchanged. With `--policy` on either path an
  `aufgabe` order also asks the agent for `person` and `due`, and a filed
  task fills `tasks`.
- Under `--lanes` a level-2 `topic_block` or `wake` order settles
  `unanswered`: the topic and the call have their own owners there (the
  topics block, the call lane). Routing them is a later decision.
- Reversible: removing the hook restores the previous `--lanes` behaviour.

## Rejected options

- Call lane carries the orders: its reply is free text written onto the
  stage as the call's answer, not proposals through `ingest`, and its
  merge function would mix orders with the room's calls.
- Coach lane carries them: the coach is a block; a block never writes the
  proposal file or the stage (ADR 0006), and it has its own rhythm.
- A separate lane queue and worker for orders: a second worker process and
  a second copy of the turn's bookkeeping for the same job `run_turn`
  already does.
- Refuse `--policy` under `--lanes`: the deployment unit would lose level 2
  entirely.
- Settle without an agent: the decider answers numbers only; a decision
  text would have to copy speech, which level 2 must not do autonomously.
- Raise `PENDING_TIMEOUT_S`: hides the missing turn instead of adding it.
