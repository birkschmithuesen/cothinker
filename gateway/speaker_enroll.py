#!/usr/bin/env python3
# CoThinker — speaker enrollment via voice-embedding cosine similarity
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Speaker enrollment, behind `--speakers` in
gateway/consumer.py, default off.

An intro round at meeting start ("Robo, Vorstellungsrunde") binds a short
voice sample to each participant's name; afterwards every utterance is
attributed by cosine similarity against the enrolled set, "unbekannt"
below the threshold. The GPU embedding service itself is out of scope
here -- `EmbeddingClient` talks to whatever answers `POST {url}/embed`
with a JSON `{"embedding": [...]}`, which is a contract, not a
deployment.

Every step degrades to "do nothing, keep today's label" on its own: no
service configured, no session recording, no usable audio-clock span, no
enrolled person yet, a connection error -- none of it may ever raise into
the SSE reader (gateway/consumer.py), because that is exactly where
nothing may raise.
"""
from __future__ import annotations

import json
import math
import os
import re
import urllib.error
import urllib.request
import wave
from io import BytesIO
from pathlib import Path
from typing import Callable

# Operator, overnight enrollment test (CPU, 07.09. recording, 36 LOSO trials):
# ECAPA and ResNet34 both scored accuracy 1.0; ResNet34 (RTF 0.012) is the
# chosen reference model (see REFERENCE_EMBEDDING_MODEL below) and 0.35
# cosine is its threshold.
DEFAULT_THRESHOLD = 0.35
DEFAULT_TIMEOUT_S = 5.0

# The reference embedding service (out of scope here, lives on ml-server --
# see the module docstring) is expected to run this model, per the same
# overnight test.
REFERENCE_EMBEDDING_MODEL = "pyannote/wespeaker-voxceleb-resnet34-LM"
UNKNOWN = "unbekannt"

# Shared-secret header, the same shape hermes-stt's wire contract uses:
# a header, not a URL query parameter, so it never ends up in an access
# log. The value itself comes from SPEAKER_EMBED_SECRET (an env var,
# gateway/consumer.py) -- never a CLI argument (a process list) and never
# logged by this module.
SECRET_HEADER = "X-Shared-Secret"

MIN_ENROLL_SECONDS = 10.0

# Accumulation window for one introduction: after "Ich bin <Name>" the
# following utterances count towards that person's sample until the next
# introduction, the end of the round, or this many seconds on the audio
# clock -- STT cuts a spoken introduction with a short pause into several
# finals, and a rule "ONE final >= 10 s" enrolled nobody live.
MAX_ACCUMULATE_SECONDS = 60.0

# ------------------------------------------------------------ control intent
# The intro round no longer NEEDS a start command: any introduction phrase
# enrolls on its own (EnrollmentSession.maybe_enroll). Start/end only
# drive the stage feedback and keep the control line away from the agent.
#
# `classify_control()` is the single decision point the consumer asks; a
# model-based classifier can replace or extend it later without touching
# gateway/consumer.py. The rule is a deliberately lenient keyword match
# (intent, not exact wording):
#
#   wake word   a token starting with "robo" (case-insensitive), also
#               hyphen-glued ("Robo-Vorstellungsrunde") or welded
#               ("Roboter," -- STT artefact), not "Robotik"; plus the
#               mishearings seen live: Hobo, Probo, Rubo, Robbo.
#   START       ("vorstell" stem AND wake word) OR ("Vorstellungsrunde"
#               AND a start verb: starte(n), beginne(n), lass(t) uns,
#               machen wir) -- in both cases no end marker.
#   END         wake word + ("vorstell" or "runde") + an end marker
#               (fertig, beendet, beende(n), abgeschlossen, schluss, ende,
#               stop/stopp), OR wake word + "alle ... vorgestellt".
#
# Decided ambiguity: "Robo, stell das mal vor" is NOT a start -- the
# separable verb carries no "vorstell" token and is far more often an
# ordinary order ("present this") than a request for an intro round.
_WAKE_RE = re.compile(
    r"(?<![a-zäöüß])(?:robo(?!ti)[a-zäöüß]*|hobo|probo|rubo|robbo)",
    re.IGNORECASE)
_VORSTELL_RE = re.compile(r"vorstell", re.IGNORECASE)
_RUNDE_RE = re.compile(r"runde", re.IGNORECASE)
_START_VERB_RE = re.compile(
    r"\b(?:starte|starten|startet|beginne|beginnen|lass\s+uns|lasst\s+uns|"
    r"machen\s+wir)\b", re.IGNORECASE)
_END_MARK_RE = re.compile(
    r"\b(?:fertig|beendet|beenden|beende|abgeschlossen|schluss|ende|"
    r"stopp?)\b", re.IGNORECASE)
_ALL_DONE_RE = re.compile(r"\balle\b.*\bvorgestellt\b", re.IGNORECASE)

# Introduction phrases. The name may arrive lower-case from STT
# ("ich bin anna") and is capitalised; a stop list keeps ordinary
# sentences ("ich bin froh", "ich bin der Meinung") from becoming names.
_INTRO_RE = re.compile(
    r"\b(?:ich\s+bin|mein\s+name\s+ist|ich\s+hei(?:ß|ss)e|hier\s+spricht|"
    r"hier\s+ist)\s+(?:die\s+|der\s+)?"
    r"([A-Za-zÄÖÜäöüß][a-zäöüß\-]+)(?:\s+(?-i:([A-ZÄÖÜ][a-zäöüß\-]+)))?",
    re.IGNORECASE)
_NOT_A_NAME = frozenset("""
    hier da dabei dafür dagegen jetzt heute gerade grad auch noch nicht kein
    keine mir mich sehr so ganz gleich gern gerne froh müde fertig sicher
    unsicher einverstanden dran neu neugierig gespannt zufrieden okay ok ja
    nein eigentlich schon wieder zurück weg da drin dort bereit total echt
    ein eine einer mal immer nur einfach etwas bisschen ziemlich halt eben
    meinung ansicht überzeugung auffassung das dies dieser diese es mal
    leider wirklich selbst allein raus durch fast bald also aber und oder
    der die den dem des wir ihr sie er gut schlecht krank zuständig
    verantwortlich dir dir's dann impressed eher überzeugt ueberzeugt
    begeistert muede""".split())


class EmbedError(Exception):
    """The embedding service was not reachable, or did not answer usably."""


def classify_control(text: str) -> str | None:
    """'start', 'end' or None for one utterance -- THE control decision.

    Keyword fast path, no model (see the block comment above). Every
    caller (EnrollmentSession, gateway/consumer.py) goes through here, so
    a classifier can be slotted in behind this one signature later.
    """
    if not isinstance(text, str):
        return None
    wake = bool(_WAKE_RE.search(text))
    has_vorstell = bool(_VORSTELL_RE.search(text))
    if wake and _ALL_DONE_RE.search(text):
        return "end"
    if (wake and (has_vorstell or _RUNDE_RE.search(text))
            and _END_MARK_RE.search(text)):
        return "end"
    if _END_MARK_RE.search(text):
        return None
    if has_vorstell and wake:
        return "start"
    if re.search(r"vorstellungsrunde", text, re.IGNORECASE) \
            and _START_VERB_RE.search(text):
        return "start"
    return None


def is_enroll_start(text: str) -> bool:
    """Does `text` open the intro round -- but not also close it."""
    return classify_control(text) == "start"


def is_enroll_end(text: str) -> bool:
    """Does `text` close the intro round."""
    return classify_control(text) == "end"


def extract_name(text: str) -> str | None:
    """The name after an introduction phrase, capitalised, or None.

    "Ich bin Max", "mein Name ist Lea", "ich heiße mara" (lower-case
    STT output), "hier spricht Tom". A second capitalised word is kept
    as a surname. Common non-name words after "ich bin" yield None.
    """
    if not isinstance(text, str):
        return None
    for m in _INTRO_RE.finditer(text):
        first = m.group(1).strip("-")
        if not first or first.lower() in _NOT_A_NAME or len(first) < 2:
            continue
        name = first[:1].upper() + first[1:]
        second = m.group(2)
        if second and second.lower() not in _NOT_A_NAME:
            name = f"{name} {second}"
        return name
    return None


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors.

    0.0 for empty input, mismatched lengths or a zero vector -- those are
    "no similarity", not an error, because a malformed embedding must
    never crash the SSE reader that calls into this module.

    MUTATION CHECK (tests/test_speaker_enroll.py::test_mutation_*): the
    enrollment tests assert concrete scores and a threshold outcome that
    only hold for THIS formula. Drop the normalisation (``dot`` alone) or
    swap the dot product for a distance and those tests flip from pass to
    fail -- there is no variant that both breaks the formula and leaves
    the suite green.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class EmbeddingClient:
    """One POST per audio clip to `{base_url}/embed`, over urllib.

    THE SECRET NEVER LEAVES THIS OBJECT, the same rule
    gateway/infomaniak_embed.py follows: it comes from the environment,
    it is not a constructor default anyone would print, and `__repr__`
    hides it -- a repr lands in tracebacks and log lines.
    """

    def __init__(self, base_url: str, secret: str = "", *,
                 timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        if not base_url:
            raise EmbedError(
                "no URL -- set SPEAKER_EMBED_URL in the environment")
        self.base_url = base_url.rstrip("/")
        self._secret = secret
        self.timeout_s = timeout_s
        self.calls = 0

    def __repr__(self) -> str:
        return f"EmbeddingClient(url={self.url()!r}, calls={self.calls})"

    def url(self) -> str:
        return f"{self.base_url}/embed"

    def embed(self, clip_bytes: bytes) -> list[float]:
        """The embedding vector for one audio clip (wav or raw f32 body).

        Raises `EmbedError` on anything that is not a usable vector --
        connection failure, timeout, a non-2xx status, an unparsable
        body. Exactly one request, no retry: a retry loop here would
        turn one slow or dead service into a storm of calls from every
        utterance in the meeting.
        """
        headers = {"content-type": "application/octet-stream"}
        if self._secret:
            headers[SECRET_HEADER] = self._secret
        req = urllib.request.Request(self.url(), data=clip_bytes,
                                     method="POST", headers=headers)
        self.calls += 1
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:
                roh = resp.read()
        except urllib.error.HTTPError as e:
            raise EmbedError(
                f"HTTP {e.code} from the embedding service") from None
        except (urllib.error.URLError, OSError) as e:
            raise EmbedError(f"no answer from the embedding service "
                             f"({type(e).__name__})") from None
        try:
            obj = json.loads(roh.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise EmbedError(f"answer is not JSON ({len(roh)} bytes)") from None
        vektor = obj.get("embedding") if isinstance(obj, dict) else None
        if not isinstance(vektor, list) or not vektor:
            raise EmbedError("answer carries no embedding vector")
        try:
            return [float(x) for x in vektor]
        except (TypeError, ValueError):
            raise EmbedError(f"embedding vector is not numeric "
                             f"({len(vektor)} entries)") from None


class SpeakerEnrollment:
    """Name -> embedding store, persisted per session (live bug
    2026-10-20 19:15: a consumer restart lost every enrolled voice and
    the intro round had to be repeated).

    With `store_path` set, every `enroll()` writes name, embedding and
    enroll order to that file (atomic: tmp file + `os.replace`), and the
    constructor reloads it if present. A missing file starts empty; a
    corrupt one starts empty too, after one warning through `log` --
    never raises, because this runs on the SSE loop's startup path.

    Embeddings are biometric-ish data: `store_path` is expected to sit
    inside the meeting's own session folder (never logged here, never
    part of the repo -- see .gitignore).
    """

    def __init__(self, threshold: float = DEFAULT_THRESHOLD, *,
                 store_path: Path | str | None = None,
                 log: Callable[..., None] = print) -> None:
        self.threshold = threshold
        self._persons: dict[str, list[float]] = {}
        self.store_path = Path(store_path) if store_path else None
        self.log = log
        if self.store_path is not None:
            self._load()

    def enroll(self, name: str, embedding: list[float]) -> None:
        name = str(name).strip()
        if not name or not embedding:
            return
        self._persons[name] = list(embedding)
        self._save()

    def _load(self) -> None:
        try:
            raw = self.store_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError as e:
            self.log(f"speaker store unreadable, starting empty "
                     f"({self.store_path.name}: {e})")
            return
        try:
            data = json.loads(raw)
            entries = data["names"]
            persons: dict[str, list[float]] = {}
            for entry in entries:
                name = str(entry["name"]).strip()
                embedding = [float(x) for x in entry["embedding"]]
                if name and embedding:
                    persons[name] = embedding
        except (ValueError, KeyError, TypeError):
            self.log(f"speaker store corrupt, starting empty "
                     f"({self.store_path.name})")
            return
        self._persons = persons

    def _save(self) -> None:
        if self.store_path is None:
            return
        body = json.dumps({"names": [{"name": n, "embedding": e}
                                     for n, e in self._persons.items()]})
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.store_path.with_name(self.store_path.name + ".tmp")
            tmp.write_text(body, encoding="utf-8")
            os.replace(tmp, self.store_path)
        except OSError as e:
            self.log(f"speaker store not saved ({e})")

    def is_enrolled(self, name: str) -> bool:
        return str(name).strip() in self._persons

    def names(self) -> list[str]:
        return sorted(self._persons)

    def names_in_order(self) -> list[str]:
        """Enrolled names in the order they were enrolled (stage text)."""
        return list(self._persons)

    def resolve(self, embedding: list[float]) -> str | None:
        """The best-matching name, UNKNOWN below the threshold.

        None when nobody is enrolled yet -- there is nothing to compare
        against, and tagging every utterance "unbekannt" before the
        first enrollment lands would be noise, not information.
        """
        if not self._persons or not embedding:
            return None
        best_name, best_score = None, -1.0
        for name, vector in self._persons.items():
            score = cosine_similarity(embedding, vector)
            if score > best_score:
                best_name, best_score = name, score
        return best_name if best_score >= self.threshold else UNKNOWN


def _safe_mtime(path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return float("-inf")


def find_newer_session_wav(current_wav) -> "Path | None":
    """The most recently modified sibling `bbb-*.wav`, newer than
    `current_wav`, in `current_wav`'s own directory -- or None.

    Mirrors `find_session_wav` in scripts/cothinker-start.sh: same naming
    pattern, same directory, newest-by-mtime wins. Scoped to that ONE
    directory on purpose -- a tap restart writes its new file next to the
    old one, and a wav from a different recording directory (a different
    session, a different day) must never be picked up (AGENTS.md: a
    session's wav is that session's own, see the 07.09.2026 finding on
    cross-session writes).
    """
    current = Path(current_wav)
    current_mtime = _safe_mtime(current)
    if current_mtime == float("-inf"):
        return None
    try:
        candidates = [p for p in current.parent.glob("bbb-*.wav")
                      if p != current]
    except OSError:
        return None
    newer = [p for p in candidates if _safe_mtime(p) > current_mtime]
    if not newer:
        return None
    return max(newer, key=_safe_mtime)


def cut_clip(wav_path, t_start: float, t_end: float) -> bytes | None:
    """A standalone WAV clip for `[t_start, t_end)` of `wav_path`.

    `t_start`/`t_end` are seconds on the same audio clock the STT server
    stamps onto `final` events (gateway/timing.py): a sample position in
    the server's own debug recording. Returns None -- never raises -- on
    a missing/unreadable file or a span outside it; the caller treats
    that exactly like "no span available".
    """
    try:
        with wave.open(str(wav_path), "rb") as wf:
            rate = wf.getframerate()
            channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            total_frames = wf.getnframes()
            start_frame = max(0, int(round(t_start * rate)))
            end_frame = min(total_frames, int(round(t_end * rate)))
            if end_frame <= start_frame or start_frame >= total_frames:
                return None
            wf.setpos(start_frame)
            raw = wf.readframes(end_frame - start_frame)
    except (OSError, wave.Error, EOFError):
        return None
    if not raw:
        return None
    out = BytesIO()
    with wave.open(out, "wb") as clip:
        clip.setnchannels(channels)
        clip.setsampwidth(sampwidth)
        clip.setframerate(rate)
        clip.writeframes(raw)
    return out.getvalue()


class EnrollmentSession:
    """Intro round plus free self-introductions, wired into the SSE loop.

    `gateway/consumer.py` calls `handle_control()` for every final first;
    when it returns None (an ordinary utterance, not a start/end), it
    calls `maybe_enroll()`, and `resolve()` looks a name up at any time --
    both silently return None (nothing to do, caller's existing label
    stands) the instant anything they need is missing.

    Enrollment ACCUMULATES: an introduction opens a pending sample for
    that name, and following utterances add their duration until the
    next introduction, the end of the round, or MAX_ACCUMULATE_SECONDS.
    Once `min_seconds` are collected, the span from the first t_start to
    the last t_end is embedded (cut_clip takes one span) and enrolled.

    Outside an active round an introduction never enrolls, new name or
    already-enrolled one: an "ich bin ..." in the middle of a meeting is
    far more often ordinary speech ("ich bin dann mal weg") than a
    deliberate re-introduction, and only the intro round carries the
    operator's intent to enroll.
    """

    def __init__(self, store: SpeakerEnrollment,
                 embed_client: EmbeddingClient | None, *,
                 wav_path=None, min_seconds: float = MIN_ENROLL_SECONDS,
                 max_window_s: float = MAX_ACCUMULATE_SECONDS,
                 log: Callable[..., None] = print) -> None:
        self.store = store
        self.embed_client = embed_client
        self.wav_path = wav_path
        self.min_seconds = min_seconds
        self.max_window_s = max_window_s
        self.log = log
        self.active = False
        # Tap-restart following (live bug 2026-10-20 19:13): `wav_path`
        # above is the configured path, kept unchanged for callers that
        # read it once at startup (gateway/consumer.py wires
        # SpeakerSplitter off it). `_active_wav_path` is what clips are
        # actually cut from, and can move on to a newer sibling file.
        self._active_wav_path = wav_path
        self._last_wav_size: int | None = None
        # Rebases a server-wide t_start/t_end onto the active file's own
        # clock (0 at the start of that file). Set to the server-wide
        # t_start of the first call after a detected switch; replaced,
        # never accumulated, on every further switch.
        self._wav_offset: float = 0.0
        # (name, first t_start, last t_end, collected seconds) or None
        self._pending: tuple[str, float, float, float] | None = None

    def handle_control(self, text: str) -> str | None:
        """Toggles the intro round on a start/end utterance.

        Returns 'start' / 'end' (truthy) when `text` was one -- the caller
        then neither enrolls it nor sends it to the agent as an order --
        and None for an ordinary utterance.
        """
        kind = classify_control(text)
        if kind == "end":
            self.active = False
            self._pending = None
        elif kind == "start":
            self.active = True
            self._pending = None
        return kind

    def maybe_enroll(self, text: str, t_start, t_end) -> str | None:
        """Feeds one utterance into the accumulator; enrolls when ready.

        Returns the newly enrolled name, or None -- never raises.
        """
        if t_start is None or t_end is None or t_end <= t_start:
            return None
        name = extract_name(text)
        if name:
            if self.active:
                self._pending = (name, t_start, t_end, t_end - t_start)
            else:
                self._pending = None
        elif self._pending is not None:
            p_name, p_start, _p_end, p_sum = self._pending
            self._pending = (p_name, p_start, t_end, p_sum + (t_end - t_start))
        if self._pending is None:
            return None
        p_name, p_start, p_end, p_sum = self._pending
        if p_end - p_start > self.max_window_s:
            self._pending = None
            return None
        if p_sum < self.min_seconds:
            return None
        self._pending = None
        embedding = self._embed_span(p_start, p_end)
        if embedding is None:
            return None
        self.store.enroll(p_name, embedding)
        return p_name

    def resolve(self, t_start, t_end) -> str | None:
        """The best-match name for this utterance's clip.

        None when resolution is unavailable for any reason (no span, no
        session recording, no service, no answer, nobody enrolled) --
        the caller's existing recognizer-id label then stands unchanged.
        """
        if t_start is None or t_end is None:
            return None
        embedding = self._embed_span(t_start, t_end)
        if embedding is None:
            return None
        return self.store.resolve(embedding)

    def _current_wav_path(self, t_start=None):
        """The wav clips are cut from right now -- follows a tap restart.

        A tap restart (`scripts/stt-vserver.sh --restart-tap`) leaves the
        configured wav behind, stopped, and starts a new `bbb-*.wav` next
        to it. Detected here by the configured file's size no longer
        growing between two calls PLUS a newer sibling existing -- a
        quiet meeting (nobody speaking) also leaves the wav's size
        unchanged for a while, but there is no newer sibling file then,
        so nothing switches.

        Each new recording starts its own audio clock at 0, but `t_start`/
        `t_end` arrive as positions on the server-wide clock
        (`stt/fundusapps/stt_server/audio_clock.py`'s sample count, never
        reset across a tap restart -- only the tap process restarts, not
        the STT server). `_wav_offset` is set here, on the call that
        detects the switch, to that call's `t_start` -- the server-wide
        position of the new file's own sample 0 -- so `_embed_span` can
        rebase every following span onto it.
        """
        if self.wav_path is None:
            return None
        try:
            size = Path(self._active_wav_path).stat().st_size
        except OSError:
            size = None
        grew = self._last_wav_size is None or (
            size is not None and size > self._last_wav_size)
        if size is not None:
            self._last_wav_size = size
        if not grew:
            newer = find_newer_session_wav(self._active_wav_path)
            if newer is not None:
                self.log(f"session wav switched: "
                         f"{self._active_wav_path} -> {newer}")
                self._active_wav_path = newer
                self._last_wav_size = None
                self._wav_offset = t_start if t_start is not None else 0.0
        return self._active_wav_path

    def _embed_span(self, t_start, t_end) -> list[float] | None:
        wav_path = self._current_wav_path(t_start)
        if wav_path is None or self.embed_client is None:
            return None
        clip = cut_clip(wav_path, t_start - self._wav_offset,
                         t_end - self._wav_offset)
        if clip is None:
            return None
        try:
            return self.embed_client.embed(clip)
        except EmbedError as e:
            # Exactly one line, this one call -- gateway/consumer.py's SSE
            # loop runs once per utterance, so a dead service already
            # produces one line per utterance without this module adding
            # a second. No retry above this call either (EmbeddingClient
            # itself does not retry): together that is "no retry storm".
            self.log(f"speaker embedding unavailable, keeping the "
                     f"recognizer id ({e})")
            return None
