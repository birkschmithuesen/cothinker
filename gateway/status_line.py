#!/usr/bin/env python3
# CoThinker - the operating line
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software under the GNU Affero General Public
# License, version 3. It comes WITHOUT ANY WARRANTY. See LICENSE.
"""The operating line: written by the consumer, deterministically.

It answers three questions the round actually asked during the analysed
meeting: did you hear me, are you still alive, are you behind. It cannot
come from the agent -- a run takes 40-180 s and the acknowledgement has
to be there in seconds.

Tone: technical with a wink. Emojis allowed, precision first. The
formulations live HERE, in code, one per state -- not in a language
model. The FORMULATION is fixed per state; the rendered text is not:
`listening` -- the dominant state -- interpolates `{at}`, a wall clock
with second resolution, so its line changes on every call.

Callers must therefore never feed the rendered status text into a
change-detection hash. The page's fingerprint (`stage/stage.py`) leaves
it out on purpose and applies the line unconditionally on every poll
instead; in the fingerprint it would report a change every three seconds
and run the swap logic continuously -- exactly the flicker this line was
decided not to cause.

The running time is NOT in the text as a NUMBER. The page counts it from
`status.since` -- but it belongs INSIDE the sentence, after "seit", not
appended behind the full stop, which read as broken German ("… nur
gründlich 1:23"). The `working` line therefore carries the placeholder
`DURATION_MARK` where the counted duration goes, and the page replaces
exactly that token. It has no digits, so nothing derived from a clock
enters the text here either.

Operational trouble does NOT displace this line. It is written next to
it, as `status.warning`, and the page renders it as a second line in the
header zone: the warning about a sync service writing along is sticky
until its condition clears -- one conflict file at ten o'clock used to
cost the round the answer to "did you hear me" for the rest of the
meeting. Both fit; neither wins.
"""
from __future__ import annotations

# Where the page writes the counted duration into the `working` line. It
# is a literal part of the German string (and of the normative table in
# docs/stage-methodology.md), and it carries no digit, so the rendered
# text still holds no value from a clock.
DURATION_MARK = "m:ss"

# German, verbatim, approved 2026-09-08. Do not translate.
STATES = {
    "listening": "🎧 hört zu · zuletzt {at}",
    "working": "⚙️ Robo werkelt seit m:ss — nicht abgestürzt, nur gründlich",
    "backlog": "🌊 {count} Abschnitte im Rückstau — Robo nimmt sie zusammen, "
               "lieber grob und aktuell",
    "failed": "💤 letzter Lauf verschluckt — Abschnitt kommt beim nächsten Mal "
              "wieder mit",
}


# Entries may carry `{placeholder}` fields (e.g. `{count}`). Never read a
# value here directly -- always go through warning(), which formats it.
WARNINGS = {
    "conflicts": "⚠️ Konfliktdateien im Projektordner — ein Sync-Dienst "
                 "schreibt mit",
    "restored": "🧵 {count} Rohzeilen nachgetragen — jemand anders hat ins "
                "Protokoll geschrieben",
    "discarded": "📎 eine Zeile von Robo war unlesbar und wurde verworfen",
    # The stream is the raw material. A drop used to be logged and
    # nothing else: the stage kept saying "hört zu" with a frozen
    # timestamp while nothing at all was being recorded.
    "stream": "🔌 Tonstrecke abgerissen — es wird gerade nichts mitgeschrieben",
    # The one failure that costs what cannot be recovered. Named as what
    # it is, instead of surfacing later as a foreign write.
    "protocol": "🚨 Protokoll nicht beschreibbar — Rohzeilen gehen gerade "
                "verloren",
    # After a restart: the file was there but could not be taken over.
    "stage": "🧩 Bühne nach dem Neustart nicht wiederherstellbar — was vorher "
             "stand, ist weg",
}


class _SafeDict(dict):
    """Renders a missing format field as "" instead of raising.

    A stage line must never crash on a caller's typo -- it should just
    come out a little terse.
    """

    def __missing__(self, key: str) -> str:
        return ""


def warning(key: str, **fields) -> str:
    """Render a warning by key, filling in its placeholders (if any).

    An unknown key degrades to "" -- the same value status() already
    treats as "no warning is showing" -- rather than raising, so a typo
    in a warning key can never surface all the way up to the stage. A
    missing field degrades the same way: the placeholder is dropped
    rather than left in the text or raising a KeyError, since either of
    those is worse than a slightly terse warning.
    """
    template = WARNINGS.get(key)
    if template is None:
        return ""
    return template.format_map(_SafeDict(fields))


# Highest first. One ranking, now carried by two lines: the top two
# (`warning`, `failed`) are trouble and go to `trouble()`, the rest are
# the routine states and go to `pick()`. Nothing changed about who
# outranks whom -- only that the acknowledgement no longer has to lose
# its seat for a condition that can stand for hours.
PRECEDENCE = ("failed", "working", "backlog", "listening")


def pick(states: list[str]) -> str:
    """The winning state out of everything that currently applies."""
    for candidate in PRECEDENCE:
        if candidate in states:
            return candidate
    return "listening"


def trouble(warning: str = "", failed: bool = False) -> str:
    """The second header line: what is going wrong, or "".

    A warning outranks the failed run -- possible data loss is worse than
    a swallowed section, and the swallowed section comes back with the
    next one anyway. Both outrank nothing else: the operating line beside
    this one keeps answering "did you hear me" the whole time.

    `warning` must be the return value of `warning(...)` (or "") -- never
    a raw `WARNINGS[...]` entry, which may still carry an unformatted
    placeholder like `{count}`.
    """
    if warning:
        return warning
    return STATES["failed"] if failed else ""


def status(state: str, *, at: str = "", count: int = 0) -> dict:
    """One rendered operating line.

    Warnings are NOT rendered here. They used to replace the whole line,
    and because a conflict file does not remove itself, one of them
    silenced the acknowledgement for the rest of the meeting. A warning
    travels beside this line (`status.warning`) and the page gives it a
    line of its own -- see the module docstring.
    """
    try:
        recognized = state in STATES
    except TypeError:
        # An unhashable state (e.g. a list) can't be looked up at all --
        # degrade the same way an unrecognised-but-hashable state does.
        recognized = False
    template = STATES[state] if recognized else STATES["listening"]
    return {
        "state": state if recognized else "listening",
        "text": template.format(at=at, count=count),
        # Only `working` gets a duration counted by the page.
        "ticking": state == "working",
    }
