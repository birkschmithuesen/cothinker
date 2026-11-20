"""PCM plumbing between the browser tap and the STT server."""
import base64
import logging
import socket
import struct
import time
from pathlib import Path


_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _parse_tcp_device(device):
    """Twin of fundusapps.stt_server.pcm_source.parse_tcp_device.

    Copied, not imported: importing pcm_source imports sr, which runs the
    STT server's argparse at import time -- the tap is its own process and
    must never do that. test_pcm.py pins that both agree.
    """
    rest = device.strip()
    if not rest.startswith("tcp:"):
        raise ValueError(f"expected tcp:HOST:PORT, got {device!r}")
    host, sep, port = rest[4:].rpartition(":")
    if not sep or host not in _LOOPBACK:
        raise ValueError(f"tcp device must be tcp:<loopback>:<port>, got {device!r}")
    try:
        port_no = int(port)
    except ValueError:
        raise ValueError(f"tcp device has no numeric port: {device!r}") from None
    if not 0 <= port_no <= 65535:
        raise ValueError(f"tcp device port out of range: {device!r}")
    return host, port_no


def decode_chunk(b64):
    raw = base64.b64decode(b64)
    return raw[: len(raw) - (len(raw) % 2)]


class WavRecorder:
    """16-bit mono WAV whose header is correct after every write.

    The raw recording is the one thing a crash must not cost (AGENTS.md:
    raw material first). The standard `wave` module only fixes the sizes
    on close(), so a killed process would leave a file every reader calls
    empty. Here the two size fields are rewritten after each write -- two
    small seeks per chunk (every ~128 ms).
    """

    def __init__(self, path, sample_rate=16000):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.sample_rate = sample_rate
        self.frames = 0
        self._f = open(self.path, "wb")
        self._f.write(self._header(0))
        self._f.flush()

    def _header(self, data_bytes):
        return (b"RIFF" + struct.pack("<I", 36 + data_bytes) + b"WAVE"
                + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, self.sample_rate,
                                        self.sample_rate * 2, 2, 16)
                + b"data" + struct.pack("<I", data_bytes))

    def write(self, pcm):
        if not pcm:
            return
        self._f.seek(0, 2)
        self._f.write(pcm)
        self.frames += len(pcm) // 2
        data_bytes = self.frames * 2
        self._f.seek(4)
        self._f.write(struct.pack("<I", 36 + data_bytes))
        self._f.seek(40)
        self._f.write(struct.pack("<I", data_bytes))
        self._f.flush()

    def close(self):
        if self._f is not None:
            self._f.close()
            self._f = None


class PcmSink:
    """Loopback TCP client to the STT server's tcp: input.

    While the server is unreachable, chunks are DROPPED, not queued: a
    queue would replay stale audio in a burst after reconnecting and put
    minutes-old speech on the audio clock as if it were live. The recorder
    keeps everything regardless.
    """

    def __init__(self, device, reconnect_s=1.0):
        self.host, self.port = _parse_tcp_device(device)
        self.reconnect_s = reconnect_s
        self._sock = None
        self._next_try = 0.0
        self.dropped_bytes = 0

    def _connect(self):
        now = time.monotonic()
        if now < self._next_try:
            return False
        try:
            self._sock = socket.create_connection((self.host, self.port), timeout=0.5)
            logging.info("PCM sink connected to %s:%s", self.host, self.port)
            return True
        except OSError:
            self._sock = None
            self._next_try = now + self.reconnect_s
            return False

    def send(self, pcm):
        if self._sock is None and not self._connect():
            self.dropped_bytes += len(pcm)
            return False
        try:
            self._sock.sendall(pcm)
            return True
        except OSError:
            logging.warning("PCM sink lost the STT server; dropping until reconnect")
            self.close()
            self._next_try = time.monotonic() + self.reconnect_s
            self.dropped_bytes += len(pcm)
            return False

    def close(self):
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
