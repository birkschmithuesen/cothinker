# CoThinker — tests of speaker enrollment (voice-embedding cosine similarity)
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""python3 -m pytest -q tests/test_speaker_enroll.py

Only invented placeholder names and speech; no network (urlopen is faked).
"""
from __future__ import annotations

import io
import json
import math
import os
import struct
import sys
import urllib.error
import urllib.request
import wave
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "gateway"))

from stt.pipeline.speaker_labels import enroll as se  # noqa: E402  (the ONE import of the module under test)
from gateway import consumer as consumer_mod  # noqa: E402


# ------------------------------------------------------------------ helpers
def write_wav(path, seconds: float, rate: int = 8000, channels: int = 1,
              value: int = 100) -> Path:
    """A stdlib-only WAV recorder: constant-sample 16-bit PCM."""
    frames = int(round(seconds * rate))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(struct.pack("<h", value) * frames * channels)
    return Path(path)


def wav_info(data: bytes) -> tuple[int, int, int, int]:
    with wave.open(io.BytesIO(data), "rb") as wf:
        return (wf.getnchannels(), wf.getsampwidth(), wf.getframerate(),
                wf.getnframes())


class FakeResp:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_urlopen(monkeypatch, body=None, exc=None):
    seen = {}

    def _open(req, timeout=None):
        seen["req"] = req
        seen["timeout"] = timeout
        if exc is not None:
            raise exc
        return FakeResp(body)

    monkeypatch.setattr(urllib.request, "urlopen", _open)
    return seen


class FakeEmbed:
    """Embedding client stand-in: returns queued vectors or raises."""

    def __init__(self, vec=None, error=None) -> None:
        self.vec = vec if vec is not None else [1.0, 0.0]
        self.error = error
        self.clips: list[bytes] = []

    def embed(self, clip):
        self.clips.append(clip)
        if self.error:
            raise self.error
        return list(self.vec)


def make_session(tmp_path, seconds=30.0, vec=None, error=None, **kw):
    wav = write_wav(tmp_path / "bbb-1.wav", seconds)
    store = se.SpeakerEnrollment()
    client = FakeEmbed(vec=vec, error=error)
    logs: list[str] = []
    sess = se.EnrollmentSession(store, client, wav_path=wav,
                                log=logs.append, **kw)
    return sess, store, client, logs


# --------------------------------------------------------- cosine similarity
def test_cosine_identical_vectors_is_one() -> None:
    assert se.cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_orthogonal_is_zero() -> None:
    assert se.cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_opposite_is_minus_one() -> None:
    assert se.cosine_similarity([1.0, 1.0], [-1.0, -1.0]) == pytest.approx(-1.0)


def test_cosine_is_scale_invariant() -> None:
    a, b = [1.0, 2.0, 2.0], [2.0, 1.0, 0.5]
    assert se.cosine_similarity(a, b) == pytest.approx(
        se.cosine_similarity([10 * x for x in a], b))


def test_mutation_cosine_concrete_score() -> None:
    # dot = 3, |a| = 1, |b| = sqrt(18)
    assert se.cosine_similarity([1.0, 0.0], [3.0, 3.0]) == pytest.approx(
        1 / math.sqrt(2))


def test_cosine_is_symmetric() -> None:
    a, b = [0.3, -0.2, 0.9], [0.1, 0.8, -0.4]
    assert se.cosine_similarity(a, b) == pytest.approx(se.cosine_similarity(b, a))


@pytest.mark.parametrize("a,b", [
    ([], [1.0]), ([1.0], []), ([], []),
    ([1.0, 2.0], [1.0]), ([0.0, 0.0], [1.0, 1.0]), ([1.0, 1.0], [0.0, 0.0]),
])
def test_cosine_degenerate_input_is_zero(a, b) -> None:
    assert se.cosine_similarity(a, b) == 0.0


def test_mutation_cosine_is_normalised_not_a_raw_dot() -> None:
    assert se.cosine_similarity([100.0, 0.0], [100.0, 0.0]) == pytest.approx(1.0)
    assert se.cosine_similarity([100.0, 0.0], [0.0, 100.0]) == pytest.approx(0.0)


# ------------------------------------------------------------------ constants
def test_default_threshold_value() -> None:
    assert se.DEFAULT_THRESHOLD == 0.35


def test_unknown_label_value() -> None:
    assert se.UNKNOWN == "unbekannt"


def test_min_enroll_and_window_constants() -> None:
    assert se.MIN_ENROLL_SECONDS == 10.0
    assert se.MAX_ACCUMULATE_SECONDS == 60.0


def test_secret_header_name() -> None:
    assert se.SECRET_HEADER == "X-Shared-Secret"


def test_consumer_uses_enroll_default_threshold() -> None:
    assert consumer_mod.SPEAKER_THRESHOLD_DEFAULT == se.DEFAULT_THRESHOLD


# ------------------------------------------------------------ extract_name
@pytest.mark.parametrize("text,expected", [
    ("Ich bin Ada", "Ada"),
    ("ich bin ada", "Ada"),
    ("Mein Name ist Ben", "Ben"),
    ("mein name ist ben", "Ben"),
    ("Ich heiße Cleo", "Cleo"),
    ("ich heisse cleo", "Cleo"),
    ("Hier spricht Dora", "Dora"),
    ("Hier ist Emil", "Emil"),
    ("Hallo zusammen, ich bin Fenja und freue mich", "Fenja"),
    ("Ich bin Ada Muster", "Ada Muster"),
    ("ich bin Ben-Jonas", "Ben-Jonas"),
    ("Ich bin die Ada", "Ada"),
    ("Ich bin der Ben", "Ben"),
])
def test_extract_name_positive(text, expected) -> None:
    assert se.extract_name(text) == expected


@pytest.mark.parametrize("text", [
    "Ich bin froh",
    "ich bin der Meinung, dass das geht",
    "Ich bin jetzt hier",
    "ich bin müde",
    "ich bin dann mal weg",
    "Das ist ein Satz ohne Vorstellung",
    "",
    "ich bin",
    "Ich bin x",
])
def test_extract_name_rejects_non_names(text) -> None:
    assert se.extract_name(text) is None


def test_extract_name_second_word_in_stop_list_is_dropped() -> None:
    assert se.extract_name("Ich bin Ada Auch") == "Ada"


def test_extract_name_lowercase_second_word_is_not_surname() -> None:
    assert se.extract_name("ich bin Ada und Ben") == "Ada"


def test_extract_name_skips_stop_word_and_takes_later_intro() -> None:
    assert se.extract_name("ich bin froh, ich bin Ada") == "Ada"


@pytest.mark.parametrize("value", [None, 42, b"Ich bin Ada", ["Ich bin Ada"]])
def test_extract_name_non_string_is_none(value) -> None:
    assert se.extract_name(value) is None


# ---------------------------------------------------------- classify_control
@pytest.mark.parametrize("text", [
    "Robo, Vorstellungsrunde",
    "Robo, starte die Vorstellungsrunde",
    "robo vorstellungsrunde bitte",
    "Robo-Vorstellungsrunde",
    "Roboter, Vorstellungsrunde",
    "Hobo, Vorstellungsrunde",
    "Probo, Vorstellungsrunde",
    "Rubo, Vorstellungsrunde",
    "Robbo, Vorstellungsrunde",
    "Robo, stellt euch bitte vor, Vorstellungsrunde",
    "Lasst uns die Vorstellungsrunde machen",
    "Lass uns eine Vorstellungsrunde beginnen",
    "Wir starten die Vorstellungsrunde",
    "Machen wir die Vorstellungsrunde",
])
def test_classify_control_start(text) -> None:
    assert se.classify_control(text) == "start"


@pytest.mark.parametrize("text", [
    "Robo, Vorstellungsrunde fertig",
    "Robo, die Vorstellungsrunde ist beendet",
    "Robo, Vorstellungsrunde abgeschlossen",
    "Robo, Runde beenden",
    "Robo, Vorstellung Schluss",
    "Robo, Vorstellungsrunde Ende",
    "Robo, Vorstellungsrunde stopp",
    "Robo, Vorstellungsrunde stop",
    "Robo, wir haben uns alle vorgestellt",
    "robo, alle sind vorgestellt",
])
def test_classify_control_end(text) -> None:
    assert se.classify_control(text) == "end"


@pytest.mark.parametrize("text", [
    "Robo, stell das mal vor",
    "Robotik ist spannend, Vorstellung",
    "Vorstellungsrunde",
    "Ich bin Ada",
    "Robo, mach weiter",
    "",
    "Wir sind fertig",
    "Vorstellungsrunde fertig",
])
def test_classify_control_none(text) -> None:
    assert se.classify_control(text) is None


@pytest.mark.parametrize("value", [None, 5, b"Robo Vorstellungsrunde"])
def test_classify_control_non_string_is_none(value) -> None:
    assert se.classify_control(value) is None


def test_is_enroll_start_and_end_follow_classify() -> None:
    assert se.is_enroll_start("Robo, Vorstellungsrunde") is True
    assert se.is_enroll_end("Robo, Vorstellungsrunde") is False
    assert se.is_enroll_end("Robo, Vorstellungsrunde fertig") is True
    assert se.is_enroll_start("Robo, Vorstellungsrunde fertig") is False


# ------------------------------------------------------------ SpeakerEnrollment
def test_store_resolve_none_when_nobody_enrolled() -> None:
    assert se.SpeakerEnrollment().resolve([1.0, 0.0]) is None


def test_store_resolve_none_for_empty_embedding() -> None:
    s = se.SpeakerEnrollment()
    s.enroll("Ada", [1.0, 0.0])
    assert s.resolve([]) is None


def test_store_resolve_best_match() -> None:
    s = se.SpeakerEnrollment()
    s.enroll("Ada", [1.0, 0.0])
    s.enroll("Ben", [0.0, 1.0])
    assert s.resolve([0.9, 0.1]) == "Ada"
    assert s.resolve([0.1, 0.9]) == "Ben"


def test_store_resolve_unknown_below_threshold() -> None:
    s = se.SpeakerEnrollment()
    s.enroll("Ada", [1.0, 0.0])
    assert s.resolve([0.0, 1.0]) == se.UNKNOWN


def test_store_resolve_threshold_is_inclusive() -> None:
    s = se.SpeakerEnrollment(threshold=1 / math.sqrt(2))
    s.enroll("Ada", [1.0, 0.0])
    assert s.resolve([1.0, 1.0]) == "Ada"


def test_store_resolve_just_below_threshold_is_unknown() -> None:
    s = se.SpeakerEnrollment(threshold=0.75)
    s.enroll("Ada", [1.0, 0.0])
    assert s.resolve([1.0, 1.0]) == se.UNKNOWN


def test_store_custom_threshold_changes_outcome() -> None:
    vec = [1.0, 2.0]
    strict = se.SpeakerEnrollment(threshold=0.99)
    loose = se.SpeakerEnrollment(threshold=0.1)
    for s in (strict, loose):
        s.enroll("Ada", [1.0, 0.0])
    assert strict.resolve(vec) == se.UNKNOWN
    assert loose.resolve(vec) == "Ada"


def test_store_default_threshold_applied() -> None:
    assert se.SpeakerEnrollment().threshold == se.DEFAULT_THRESHOLD


def test_store_negative_scores_never_match() -> None:
    s = se.SpeakerEnrollment()
    s.enroll("Ada", [1.0, 0.0])
    assert s.resolve([-1.0, 0.0]) == se.UNKNOWN


def test_store_enroll_ignores_blank_name_and_empty_embedding() -> None:
    s = se.SpeakerEnrollment()
    s.enroll("  ", [1.0])
    s.enroll("Ada", [])
    assert s.names() == []


def test_store_enroll_strips_name_and_overwrites() -> None:
    s = se.SpeakerEnrollment()
    s.enroll("  Ada ", [1.0, 0.0])
    s.enroll("Ada", [0.0, 1.0])
    assert s.names() == ["Ada"]
    assert s.resolve([0.0, 1.0]) == "Ada"


def test_store_is_enrolled() -> None:
    s = se.SpeakerEnrollment()
    s.enroll("Ada", [1.0])
    assert s.is_enrolled("Ada") is True
    assert s.is_enrolled(" Ada ") is True
    assert s.is_enrolled("Ben") is False


def test_store_names_sorted_vs_in_order() -> None:
    s = se.SpeakerEnrollment()
    for n in ("Cleo", "Ada", "Ben"):
        s.enroll(n, [1.0])
    assert s.names() == ["Ada", "Ben", "Cleo"]
    assert s.names_in_order() == ["Cleo", "Ada", "Ben"]


def test_store_persists_and_reloads(tmp_path) -> None:
    path = tmp_path / "sub" / "speakers.json"
    s = se.SpeakerEnrollment(store_path=path)
    s.enroll("Ada", [1.0, 0.0])
    s.enroll("Ben", [0.0, 1.0])
    again = se.SpeakerEnrollment(store_path=path)
    assert again.names_in_order() == ["Ada", "Ben"]
    assert again.resolve([0.0, 1.0]) == "Ben"


def test_store_save_is_atomic_no_tmp_left(tmp_path) -> None:
    path = tmp_path / "speakers.json"
    se.SpeakerEnrollment(store_path=path).enroll("Ada", [1.0])
    assert sorted(p.name for p in tmp_path.iterdir()) == ["speakers.json"]


def test_store_file_format(tmp_path) -> None:
    path = tmp_path / "speakers.json"
    se.SpeakerEnrollment(store_path=path).enroll("Ada", [0.5, 0.25])
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "names": [{"name": "Ada", "embedding": [0.5, 0.25]}]}


def test_store_missing_file_starts_empty_silently(tmp_path) -> None:
    logs: list[str] = []
    s = se.SpeakerEnrollment(store_path=tmp_path / "none.json", log=logs.append)
    assert s.names() == [] and logs == []


@pytest.mark.parametrize("content", [
    "not json", "{}", '{"names": 5}', '{"names": [{"name": "Ada"}]}',
    '{"names": [{"name": "Ada", "embedding": ["x"]}]}',
])
def test_store_corrupt_file_starts_empty_with_one_warning(tmp_path, content) -> None:
    path = tmp_path / "speakers.json"
    path.write_text(content, encoding="utf-8")
    logs: list[str] = []
    s = se.SpeakerEnrollment(store_path=path, log=logs.append)
    assert s.names() == []
    assert len(logs) == 1 and "corrupt" in logs[0]


def test_store_unreadable_path_starts_empty_with_warning(tmp_path) -> None:
    path = tmp_path / "speakers.json"
    path.mkdir()  # reading a directory raises OSError (not FileNotFoundError)
    logs: list[str] = []
    s = se.SpeakerEnrollment(store_path=path, log=logs.append)
    assert s.names() == []
    assert len(logs) == 1 and "unreadable" in logs[0]


def test_store_load_drops_blank_entries(tmp_path) -> None:
    path = tmp_path / "speakers.json"
    path.write_text(json.dumps({"names": [
        {"name": " ", "embedding": [1.0]},
        {"name": "Ada", "embedding": []},
        {"name": "Ben", "embedding": [1.0]}]}), encoding="utf-8")
    assert se.SpeakerEnrollment(store_path=path).names() == ["Ben"]


def test_store_save_failure_logs_and_keeps_memory(tmp_path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    logs: list[str] = []
    s = se.SpeakerEnrollment(store_path=blocker / "speakers.json",
                             log=logs.append)
    s.enroll("Ada", [1.0])
    assert s.is_enrolled("Ada")
    assert any("not saved" in m for m in logs)


# ------------------------------------------------------------- EmbeddingClient
def test_client_requires_url() -> None:
    with pytest.raises(se.EmbedError):
        se.EmbeddingClient("")


def test_client_url_strips_trailing_slash() -> None:
    assert se.EmbeddingClient("http://svc.invalid:9/").url() == "http://svc.invalid:9/embed"


def test_client_repr_hides_secret() -> None:
    c = se.EmbeddingClient("http://svc.invalid", "s3cret-value")
    assert "s3cret-value" not in repr(c)
    assert "http://svc.invalid/embed" in repr(c)


def test_client_embed_success_posts_clip_with_secret(monkeypatch) -> None:
    seen = fake_urlopen(monkeypatch, json.dumps({"embedding": [1, 2.5]}).encode())
    c = se.EmbeddingClient("http://svc.invalid", "tok", timeout_s=2.0)
    assert c.embed(b"CLIP") == [1.0, 2.5]
    req = seen["req"]
    assert req.full_url == "http://svc.invalid/embed"
    assert req.get_method() == "POST"
    assert req.data == b"CLIP"
    assert req.get_header("X-shared-secret") == "tok"
    assert req.get_header("Content-type") == "application/octet-stream"
    assert seen["timeout"] == 2.0
    assert c.calls == 1


def test_client_embed_without_secret_sends_no_header(monkeypatch) -> None:
    seen = fake_urlopen(monkeypatch, b'{"embedding": [1]}')
    se.EmbeddingClient("http://svc.invalid").embed(b"x")
    assert seen["req"].get_header("X-shared-secret") is None


def test_client_default_timeout() -> None:
    assert se.EmbeddingClient("http://svc.invalid").timeout_s == se.DEFAULT_TIMEOUT_S


def test_client_http_error_is_embed_error(monkeypatch) -> None:
    err = urllib.error.HTTPError("http://x", 503, "bad", {}, None)
    fake_urlopen(monkeypatch, exc=err)
    with pytest.raises(se.EmbedError, match="HTTP 503"):
        se.EmbeddingClient("http://svc.invalid").embed(b"x")


def test_client_url_error_is_embed_error(monkeypatch) -> None:
    fake_urlopen(monkeypatch, exc=urllib.error.URLError("refused"))
    with pytest.raises(se.EmbedError, match="URLError"):
        se.EmbeddingClient("http://svc.invalid").embed(b"x")


def test_client_timeout_oserror_is_embed_error(monkeypatch) -> None:
    fake_urlopen(monkeypatch, exc=TimeoutError("slow"))
    with pytest.raises(se.EmbedError, match="TimeoutError"):
        se.EmbeddingClient("http://svc.invalid").embed(b"x")


@pytest.mark.parametrize("body", [
    b"not json", b"\xff\xfe", b"[1, 2]", b'{"other": 1}',
    b'{"embedding": []}', b'{"embedding": "abc"}', b'{"embedding": ["a"]}',
    b'{"embedding": [null]}',
])
def test_client_unusable_body_is_embed_error(monkeypatch, body) -> None:
    fake_urlopen(monkeypatch, body)
    with pytest.raises(se.EmbedError):
        se.EmbeddingClient("http://svc.invalid").embed(b"x")


def test_client_error_message_never_contains_secret(monkeypatch) -> None:
    fake_urlopen(monkeypatch, exc=urllib.error.URLError("refused"))
    c = se.EmbeddingClient("http://svc.invalid", "topsecret")
    with pytest.raises(se.EmbedError) as ei:
        c.embed(b"x")
    assert "topsecret" not in str(ei.value)


def test_client_makes_exactly_one_request_per_failure(monkeypatch) -> None:
    fake_urlopen(monkeypatch, exc=urllib.error.URLError("refused"))
    c = se.EmbeddingClient("http://svc.invalid")
    with pytest.raises(se.EmbedError):
        c.embed(b"x")
    assert c.calls == 1


# --------------------------------------------------------------------- cut_clip
def test_cut_clip_duration_and_format(tmp_path) -> None:
    wav = write_wav(tmp_path / "a.wav", 5.0, rate=8000)
    clip = se.cut_clip(wav, 1.0, 3.0)
    assert wav_info(clip) == (1, 2, 8000, 16000)


def test_cut_clip_preserves_samples(tmp_path) -> None:
    wav = write_wav(tmp_path / "a.wav", 2.0, value=-321)
    clip = se.cut_clip(wav, 0.5, 1.0)
    with wave.open(io.BytesIO(clip), "rb") as wf:
        first = struct.unpack("<h", wf.readframes(1))[0]
    assert first == -321


def test_cut_clip_keeps_stereo(tmp_path) -> None:
    wav = write_wav(tmp_path / "a.wav", 2.0, channels=2)
    assert wav_info(se.cut_clip(wav, 0.0, 1.0))[0] == 2


def test_cut_clip_clamps_end_to_file_length(tmp_path) -> None:
    wav = write_wav(tmp_path / "a.wav", 2.0)
    assert wav_info(se.cut_clip(wav, 1.0, 99.0))[3] == 8000


def test_cut_clip_negative_start_clamped(tmp_path) -> None:
    wav = write_wav(tmp_path / "a.wav", 2.0)
    assert wav_info(se.cut_clip(wav, -5.0, 1.0))[3] == 8000


@pytest.mark.parametrize("t0,t1", [(1.0, 1.0), (2.0, 1.0), (5.0, 6.0), (2.0, 3.0)])
def test_cut_clip_empty_or_outside_span_is_none(tmp_path, t0, t1) -> None:
    wav = write_wav(tmp_path / "a.wav", 2.0)
    assert se.cut_clip(wav, t0, t1) is None


def test_cut_clip_missing_file_is_none(tmp_path) -> None:
    assert se.cut_clip(tmp_path / "nope.wav", 0.0, 1.0) is None


def test_cut_clip_not_a_wav_is_none(tmp_path) -> None:
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"this is not a wav file")
    assert se.cut_clip(bad, 0.0, 1.0) is None


def test_cut_clip_accepts_str_path(tmp_path) -> None:
    wav = write_wav(tmp_path / "a.wav", 2.0)
    assert se.cut_clip(str(wav), 0.0, 1.0) is not None


# ------------------------------------------------------ find_newer_session_wav
def _set_mtime(path, t):
    os.utime(path, (t, t))


def test_find_newer_returns_newest_sibling(tmp_path) -> None:
    cur = write_wav(tmp_path / "bbb-1.wav", 1.0)
    n1 = write_wav(tmp_path / "bbb-2.wav", 1.0)
    n2 = write_wav(tmp_path / "bbb-3.wav", 1.0)
    _set_mtime(cur, 1000)
    _set_mtime(n1, 2000)
    _set_mtime(n2, 3000)
    assert se.find_newer_session_wav(cur) == n2


def test_find_newer_none_when_only_older(tmp_path) -> None:
    cur = write_wav(tmp_path / "bbb-5.wav", 1.0)
    old = write_wav(tmp_path / "bbb-1.wav", 1.0)
    _set_mtime(old, 1000)
    _set_mtime(cur, 2000)
    assert se.find_newer_session_wav(cur) is None


def test_find_newer_ignores_other_names_and_dirs(tmp_path) -> None:
    cur = write_wav(tmp_path / "bbb-1.wav", 1.0)
    other = write_wav(tmp_path / "other.wav", 1.0)
    sub = tmp_path / "sub"
    sub.mkdir()
    elsewhere = write_wav(sub / "bbb-9.wav", 1.0)
    _set_mtime(cur, 1000)
    _set_mtime(other, 5000)
    _set_mtime(elsewhere, 5000)
    assert se.find_newer_session_wav(cur) is None


def test_find_newer_missing_current_is_none(tmp_path) -> None:
    assert se.find_newer_session_wav(tmp_path / "bbb-1.wav") is None


# --------------------------------------------------------------- EnrollmentSession
def test_session_handle_control_toggles_active(tmp_path) -> None:
    sess, *_ = make_session(tmp_path)
    assert sess.active is False
    assert sess.handle_control("Robo, Vorstellungsrunde") == "start"
    assert sess.active is True
    assert sess.handle_control("Robo, Vorstellungsrunde fertig") == "end"
    assert sess.active is False


def test_session_handle_control_ordinary_utterance(tmp_path) -> None:
    sess, *_ = make_session(tmp_path)
    sess.active = True
    assert sess.handle_control("Das ist ein normaler Satz") is None
    assert sess.active is True


def test_session_enrolls_after_single_long_intro(tmp_path) -> None:
    sess, store, client, _ = make_session(tmp_path, vec=[1.0, 0.0])
    sess.handle_control("Robo, Vorstellungsrunde")
    assert sess.maybe_enroll("Ich bin Ada", 0.0, 12.0) == "Ada"
    assert store.names() == ["Ada"]
    assert len(client.clips) == 1
    assert wav_info(client.clips[0])[3] == 12 * 8000


def test_session_short_intro_does_not_enroll_yet(tmp_path) -> None:
    sess, store, client, _ = make_session(tmp_path)
    sess.handle_control("Robo, Vorstellungsrunde")
    assert sess.maybe_enroll("Ich bin Ada", 0.0, 4.0) is None
    assert store.names() == [] and client.clips == []


def test_session_accumulates_following_utterances(tmp_path) -> None:
    sess, store, client, _ = make_session(tmp_path)
    sess.handle_control("Robo, Vorstellungsrunde")
    assert sess.maybe_enroll("Ich bin Ben", 0.0, 4.0) is None
    assert sess.maybe_enroll("ich arbeite an Karten", 4.5, 8.0) is None
    assert sess.maybe_enroll("und mag Tee", 8.5, 14.0) == "Ben"
    assert store.names() == ["Ben"]
    # one span from the first t_start to the last t_end
    assert wav_info(client.clips[0])[3] == 14 * 8000


def test_session_new_intro_restarts_accumulation(tmp_path) -> None:
    sess, store, client, _ = make_session(tmp_path)
    sess.handle_control("Robo, Vorstellungsrunde")
    sess.maybe_enroll("Ich bin Ada", 0.0, 5.0)
    assert sess.maybe_enroll("Ich bin Ben", 6.0, 17.0) == "Ben"
    assert store.names() == ["Ben"]


def test_session_intro_outside_round_never_enrolls(tmp_path) -> None:
    sess, store, client, _ = make_session(tmp_path)
    assert sess.maybe_enroll("Ich bin Ada", 0.0, 20.0) is None
    assert store.names() == [] and client.clips == []


def test_session_intro_outside_round_drops_pending(tmp_path) -> None:
    sess, store, _c, _ = make_session(tmp_path)
    sess.handle_control("Robo, Vorstellungsrunde")
    sess.maybe_enroll("Ich bin Ada", 0.0, 4.0)
    sess.active = False
    sess.maybe_enroll("Ich bin Ben", 5.0, 6.0)
    sess.active = True
    assert sess.maybe_enroll("noch ein Satz", 7.0, 20.0) is None
    assert store.names() == []


def test_session_end_of_round_drops_pending(tmp_path) -> None:
    sess, store, *_ = make_session(tmp_path)
    sess.handle_control("Robo, Vorstellungsrunde")
    sess.maybe_enroll("Ich bin Ada", 0.0, 4.0)
    sess.handle_control("Robo, Vorstellungsrunde fertig")
    assert sess.maybe_enroll("noch ein Satz", 5.0, 20.0) is None
    assert store.names() == []


def test_session_window_exceeded_drops_pending(tmp_path) -> None:
    sess, store, *_ = make_session(tmp_path, seconds=200.0, max_window_s=20.0,
                                   min_seconds=100.0)
    sess.handle_control("Robo, Vorstellungsrunde")
    sess.maybe_enroll("Ich bin Ada", 0.0, 5.0)
    assert sess.maybe_enroll("weiter", 5.0, 25.0) is None
    assert sess._pending is None
    assert store.names() == []


@pytest.mark.parametrize("t0,t1", [(None, 5.0), (0.0, None), (None, None),
                                   (5.0, 5.0), (6.0, 5.0)])
def test_session_maybe_enroll_rejects_bad_span(tmp_path, t0, t1) -> None:
    sess, store, client, _ = make_session(tmp_path)
    sess.active = True
    assert sess.maybe_enroll("Ich bin Ada", t0, t1) is None
    assert sess._pending is None and client.clips == []


def test_session_custom_min_seconds(tmp_path) -> None:
    sess, store, *_ = make_session(tmp_path, min_seconds=2.0)
    sess.active = True
    assert sess.maybe_enroll("Ich bin Ada", 0.0, 3.0) == "Ada"


def test_session_embed_error_logs_once_and_does_not_enroll(tmp_path) -> None:
    sess, store, client, logs = make_session(tmp_path,
                                             error=se.EmbedError("down"))
    sess.active = True
    assert sess.maybe_enroll("Ich bin Ada", 0.0, 12.0) is None
    assert store.names() == []
    assert len(logs) == 1 and "unavailable" in logs[0] and "down" in logs[0]
    assert len(client.clips) == 1  # no retry


def test_session_without_embed_client_never_enrolls(tmp_path) -> None:
    wav = write_wav(tmp_path / "bbb-1.wav", 30.0)
    store = se.SpeakerEnrollment()
    sess = se.EnrollmentSession(store, None, wav_path=wav, log=lambda *_: None)
    sess.active = True
    assert sess.maybe_enroll("Ich bin Ada", 0.0, 12.0) is None
    assert sess.resolve(0.0, 3.0) is None


def test_session_without_wav_never_enrolls(tmp_path) -> None:
    store = se.SpeakerEnrollment()
    sess = se.EnrollmentSession(store, FakeEmbed(), wav_path=None,
                                log=lambda *_: None)
    sess.active = True
    assert sess.maybe_enroll("Ich bin Ada", 0.0, 12.0) is None
    assert sess.resolve(0.0, 3.0) is None


def test_session_span_outside_wav_does_not_enroll(tmp_path) -> None:
    sess, store, client, _ = make_session(tmp_path, seconds=5.0)
    sess.active = True
    assert sess.maybe_enroll("Ich bin Ada", 100.0, 115.0) is None
    assert client.clips == []


def test_session_resolve_known_unknown_and_nobody(tmp_path) -> None:
    sess, store, client, _ = make_session(tmp_path, vec=[1.0, 0.0])
    assert sess.resolve(0.0, 3.0) is None  # nobody enrolled yet
    store.enroll("Ada", [1.0, 0.0])
    assert sess.resolve(0.0, 3.0) == "Ada"
    client.vec = [0.0, 1.0]
    assert sess.resolve(0.0, 3.0) == se.UNKNOWN


@pytest.mark.parametrize("t0,t1", [(None, 3.0), (0.0, None)])
def test_session_resolve_without_span_is_none(tmp_path, t0, t1) -> None:
    sess, store, client, _ = make_session(tmp_path)
    store.enroll("Ada", [1.0, 0.0])
    assert sess.resolve(t0, t1) is None
    assert client.clips == []


def test_session_resolve_embed_error_is_none(tmp_path) -> None:
    sess, store, _c, logs = make_session(tmp_path, error=se.EmbedError("boom"))
    store.enroll("Ada", [1.0, 0.0])
    assert sess.resolve(0.0, 3.0) is None
    assert len(logs) == 1


def test_session_follows_tap_restart_with_offset(tmp_path) -> None:
    sess, store, client, logs = make_session(tmp_path, seconds=6.0)
    store.enroll("Ada", [1.0, 0.0])
    old = tmp_path / "bbb-1.wav"
    assert sess.resolve(0.0, 2.0) == "Ada"          # first call: size recorded
    new = write_wav(tmp_path / "bbb-2.wav", 4.0)
    _set_mtime(old, 1000)
    _set_mtime(new, 2000)
    # old file did not grow + newer sibling exists -> switch, offset = t_start
    assert sess.resolve(100.0, 102.0) == "Ada"
    assert sess._active_wav_path == new
    assert sess._wav_offset == 100.0
    assert wav_info(client.clips[-1])[3] == 2 * 8000
    assert any("session wav switched" in m for m in logs)
    assert sess.wav_path == old  # configured path stays unchanged


def test_session_quiet_meeting_without_sibling_does_not_switch(tmp_path) -> None:
    sess, store, client, _ = make_session(tmp_path, seconds=6.0)
    store.enroll("Ada", [1.0, 0.0])
    sess.resolve(0.0, 2.0)
    sess.resolve(2.0, 4.0)
    assert sess._active_wav_path == tmp_path / "bbb-1.wav"
    assert sess._wav_offset == 0.0


# ------------------------------------------------- consumer enrollment mirror
NAMES = ["Ada", "Ben"]


def test_mirror_start_without_enrollment_is_instruction() -> None:
    out = consumer_mod.enrollment_mirror("start", None, [])
    assert out.startswith("Vorstellungsrunde läuft")
    assert "Erkannt" not in out


def test_mirror_start_with_enrollment_lists_names() -> None:
    out = consumer_mod.enrollment_mirror("start", "Ben", NAMES)
    assert out.endswith("Erkannt: Ada, Ben")


def test_mirror_end_with_names() -> None:
    assert consumer_mod.enrollment_mirror("end", None, NAMES) == \
        "Vorstellungsrunde beendet — erkannt: Ada, Ben"


def test_mirror_end_without_names() -> None:
    assert consumer_mod.enrollment_mirror("end", None, []) == \
        "Vorstellungsrunde beendet — niemand erkannt, bitte wiederholen"


def test_mirror_plain_enrollment() -> None:
    assert consumer_mod.enrollment_mirror(None, "Ada", ["Ada"]) == "Erkannt: Ada"


def test_mirror_nothing_to_report_is_none() -> None:
    assert consumer_mod.enrollment_mirror(None, None, NAMES) is None


def test_mirror_follows_a_real_session(tmp_path) -> None:
    sess, store, *_ = make_session(tmp_path)
    control = sess.handle_control("Robo, Vorstellungsrunde")
    assert consumer_mod.enrollment_mirror(
        control, None, store.names_in_order()).startswith("Vorstellungsrunde läuft")
    enrolled = sess.maybe_enroll("Ich bin Ada", 0.0, 12.0)
    assert consumer_mod.enrollment_mirror(
        None, enrolled, store.names_in_order()) == "Erkannt: Ada"
    control = sess.handle_control("Robo, Vorstellungsrunde fertig")
    assert consumer_mod.enrollment_mirror(
        control, None, store.names_in_order()) == \
        "Vorstellungsrunde beendet — erkannt: Ada"


def test_reference_model_constant() -> None:
    assert "wespeaker" in se.REFERENCE_EMBEDDING_MODEL
