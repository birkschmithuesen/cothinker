"""The contract-test machinery itself (tests/contract/conftest.py): the
loopback-only guard, and an outside subject named by environment variable
running the same contract test as the built-in ones (ADR 0022 rule 3).
"""
from __future__ import annotations

import socket

import pytest


def test_a_connection_beyond_loopback_is_refused():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        with pytest.raises(RuntimeError, match="must not reach the network"):
            s.connect(("192.0.2.1", 9))


def test_a_loopback_connection_is_allowed():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(server.getsockname(), timeout=5):
            pass
