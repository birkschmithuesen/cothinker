"""A capture source fed over loopback TCP instead of a sound card.

On the headless vServer there is no sound card and no PortAudio. The
meeting audio arrives from `fundusapps.bbb_tap` (or `feed_wav`) as raw
little-endian int16 PCM at the configured sample rate. This source hands
it to the recognizers exactly as `AudioSource` hands over PortAudio
blocks: same subscriber fan-out, same peak/RMS metering, same block size.
The audio clock therefore counts these samples like any others.

Selected with `--audio_devices tcp:127.0.0.1:5053`. Loopback only:
anything that can connect to the port is transcribed as if it were the
meeting.
"""
import logging
import socket
import threading

from fundusapps.stt_server.sr import AudioSource

TCP_PREFIX = "tcp:"
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def is_tcp_device(device):
    return isinstance(device, str) and device.strip().startswith(TCP_PREFIX)


def parse_tcp_device(device):
    rest = device.strip()[len(TCP_PREFIX):]
    host, sep, port = rest.rpartition(":")
    if not sep or not host:
        raise ValueError(f"tcp device must look like tcp:127.0.0.1:5053, got {device!r}")
    if host not in _LOOPBACK:
        raise ValueError(f"tcp device must be on loopback, got host {host!r}")
    try:
        port_no = int(port)
    except ValueError:
        raise ValueError(f"tcp device has no numeric port: {device!r}") from None
    if not 0 <= port_no <= 65535:
        raise ValueError(f"tcp device port out of range: {device!r}")
    return host, port_no


class PcmTcpSource(AudioSource):
    """AudioSource whose frames come from one TCP client at a time."""

    def __init__(self, device, sample_rate, num_channels=1,
                 asio_channels=None, blocksize=4096):
        super().__init__(device=device, sample_rate=sample_rate,
                         num_channels=num_channels, asio_channels=None,
                         blocksize=blocksize)
        self.host, self.port = parse_tcp_device(device)
        self.clients_accepted = 0
        self._server = None
        self._thread = None
        self._stop = threading.Event()

    def start(self):
        family = socket.AF_INET6 if self.host == "::1" else socket.AF_INET
        srv = socket.socket(family, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(1)
        srv.settimeout(0.2)
        self.port = srv.getsockname()[1]
        self._server = srv
        self._stop.clear()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        logging.info(f"PCM input listening on {self.host}:{self.port} "
                     f"({self.sample_rate} Hz, {self.num_channels} ch)")

    def _serve(self):
        block_bytes = self.blocksize * 2 * self.num_channels
        while not self._stop.is_set():
            try:
                conn, _ = self._server.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self.clients_accepted += 1
            # A new client is a new stream: the time between the last one's
            # end and this one's start never reached the audio clock. The
            # recognizer re-anchors its epoch when it sees this change (see
            # AudioSource.epoch_generation), the sample count keeps running.
            self.epoch_generation += 1
            logging.info("PCM input: client connected")
            conn.settimeout(0.2)
            pending = b""
            with conn:
                while not self._stop.is_set():
                    try:
                        data = conn.recv(65536)
                    except socket.timeout:
                        continue
                    except OSError:
                        break
                    if not data:
                        break
                    pending += data
                    while len(pending) >= block_bytes:
                        block, pending = pending[:block_bytes], pending[block_bytes:]
                        self._callback(block, self.blocksize, None, None)
            # A partial block at disconnect is dropped: it would shift the
            # sample alignment of whatever the next client sends.
            logging.info("PCM input: client disconnected")

    def stop(self):
        self._stop.set()
        if self._server is not None:
            try:
                self._server.close()
            except OSError:
                pass
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
