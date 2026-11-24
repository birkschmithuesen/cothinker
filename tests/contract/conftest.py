"""Shared machinery of the level-2 contract tests (ADR 0022 rule 3).

One mechanism for every API: a contract test module sets `CONTRACT_API`
(e.g. "agent"), lists its built-in subjects in `BUILTIN_SUBJECTS` (id ->
subject) and says how a subject named from outside is resolved
(`resolve_outside(value)`). Every test that asks for the `subject` fixture
then runs once per built-in subject and, when the environment variable
`COTHINKER_CONTRACT_<API>` is set, once more against that outside subject --
the same test, not a copy of it. `docs/contract-tests.md` has one paragraph
per API.

"No network" means no connection beyond the loopback interface: the autouse
guard below refuses any other socket connect made in this process.
"""
from __future__ import annotations

import ipaddress
import os
import socket

import pytest

ENV_PREFIX = "COTHINKER_CONTRACT_"


def pytest_generate_tests(metafunc):
    api = getattr(metafunc.module, "CONTRACT_API", None)
    if api is None or "subject" not in metafunc.fixturenames:
        return
    subjects = dict(metafunc.module.BUILTIN_SUBJECTS)
    outside = os.environ.get(ENV_PREFIX + api.upper())
    if outside:
        subjects["outside"] = metafunc.module.resolve_outside(outside)
    metafunc.parametrize("subject", list(subjects.values()), ids=list(subjects))


def _is_loopback(address) -> bool:
    if isinstance(address, (str, bytes)):          # AF_UNIX path
        return True
    host = address[0]
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def loopback_only(monkeypatch):
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def connect(self, address):
        if not _is_loopback(address):
            raise RuntimeError(f"contract tests must not reach the network: {address!r}")
        return real_connect(self, address)

    def connect_ex(self, address):
        if not _is_loopback(address):
            raise RuntimeError(f"contract tests must not reach the network: {address!r}")
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
