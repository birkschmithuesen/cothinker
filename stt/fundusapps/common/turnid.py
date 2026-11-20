"""ULID generator + LATENCY log formatter shared by STT/Botserver/TTS.

Inline 10-line ULID implementation — no new dependency. ULID layout is
48 bits of millisecond-precision timestamp + 80 bits of randomness,
encoded in 26 chars of Crockford base32. Lexicographically sortable.
"""
import os
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _b32encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


def ulid_now() -> str:
    """Return a fresh ULID. Lexicographically sortable by time."""
    ts_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = int.from_bytes(os.urandom(10), "big") & ((1 << 80) - 1)
    return _b32encode(ts_ms, 10) + _b32encode(rand, 16)


def format_latency_log(turn_id: str, phase: str, t_local_ms: float, **extras) -> str:
    """Render the canonical [LATENCY ...] log line. See plan conventions."""
    parts = [f"turn={turn_id}", f"phase={phase}", f"t_local_ms={t_local_ms}"]
    for k, v in extras.items():
        s = str(v)
        if " " in s or "\t" in s:
            parts.append(f'{k}="{s}"')
        else:
            parts.append(f"{k}={s}")
    return "[LATENCY " + " ".join(parts) + "]"
