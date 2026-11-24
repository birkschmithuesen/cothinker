"""Operator token auth (issue #159): "a separate operator token, never the
participant token. Read from a file given by flag/env; compared in
constant time; token never logged or echoed." Mirrors `stage/stage.py`'s
`_token_ok` (`secrets.compare_digest` on UTF-8 bytes) and
`demo/session_server.py`'s `check_token`.
"""
from __future__ import annotations

import logging

from surfaces.operator.auth import check_token, load_token


def test_load_token_strips_trailing_whitespace(tmp_path):
    path = tmp_path / "operator.token"
    path.write_text("s3cr3t-operator-token\n", encoding="utf-8")
    assert load_token(path) == "s3cr3t-operator-token"


def test_check_token_accepts_the_matching_token():
    assert check_token("s3cr3t", "s3cr3t") is True


def test_check_token_rejects_a_wrong_token():
    assert check_token("wrong", "s3cr3t") is False


def test_check_token_rejects_a_missing_candidate():
    assert check_token(None, "s3cr3t") is False
    assert check_token("", "s3cr3t") is False


def test_check_token_rejects_when_expected_is_empty():
    assert check_token("anything", "") is False


def test_check_token_does_not_raise_on_non_ascii_input():
    """stage.py's lesson: compare_digest raises TypeError on a non-ASCII
    `str` operand if compared as `str`; always compare as UTF-8 bytes."""
    assert check_token("tökén", "s3cr3t") is False


def test_check_token_never_logs_the_token(caplog):
    with caplog.at_level(logging.DEBUG):
        check_token("s3cr3t-candidate", "s3cr3t-expected")
    for record in caplog.records:
        assert "s3cr3t-candidate" not in record.getMessage()
        assert "s3cr3t-expected" not in record.getMessage()
