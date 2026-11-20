import re
import time

from fundusapps.common.turnid import ulid_now, format_latency_log


def test_ulid_now_is_26_char_crockford():
    t = ulid_now()
    assert isinstance(t, str)
    assert len(t) == 26
    # Crockford base32 — uppercase A–Z and 0–9 minus I, L, O, U
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", t), t


def test_ulid_now_is_monotonic_within_one_ms():
    a = ulid_now()
    time.sleep(0.005)
    b = ulid_now()
    # First 10 chars are time component, lex-sortable
    assert a[:10] <= b[:10]


def test_ulid_now_unique_across_back_to_back_calls():
    ids = {ulid_now() for _ in range(1000)}
    assert len(ids) == 1000


def test_format_latency_log_minimal():
    line = format_latency_log("01ABC", "vad_speech_end", 1700000000123.4)
    assert line == "[LATENCY turn=01ABC phase=vad_speech_end t_local_ms=1700000000123.4]"


def test_format_latency_log_with_extras_quotes_values_with_spaces():
    line = format_latency_log(
        "01ABC.1", "filler_first_sent", 1700000000200.0,
        phrase="ähm äh", chars=42,
    )
    assert line.startswith("[LATENCY turn=01ABC.1 phase=filler_first_sent t_local_ms=1700000000200.0")
    assert 'phrase="ähm äh"' in line
    assert "chars=42" in line
    assert line.endswith("]")
