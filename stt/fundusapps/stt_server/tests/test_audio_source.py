import sys
import logging

# args.py runs argparse at import time; mock argv so importing sr is safe.
_original_argv = sys.argv.copy()
sys.argv = ["pytest", "vosk"]
from fundusapps.stt_server.sr import AudioSource  # noqa: E402
sys.argv = _original_argv


def test_audio_source_fans_out_to_all_subscribers():
    """Every subscriber receives every captured frame, in order."""
    source = AudioSource(device=0, sample_rate=44100, num_channels=2)
    q1 = source.subscribe()
    q2 = source.subscribe()
    frame_a = b"\x01\x02" * 100
    frame_b = b"\x03\x04" * 100
    # Simulate two PortAudio callbacks (no real stream is opened).
    source._callback(frame_a, 100, 0.0, status=None)
    source._callback(frame_b, 100, 0.001, status=None)
    assert q1.get(timeout=1.0)[1] == frame_a
    assert q1.get(timeout=1.0)[1] == frame_b
    assert q2.get(timeout=1.0)[1] == frame_a
    assert q2.get(timeout=1.0)[1] == frame_b


def test_audio_source_callback_logs_status(caplog):
    """A non-None status flag from PortAudio is logged at WARNING level."""
    source = AudioSource(device=0, sample_rate=44100, num_channels=2)
    source.subscribe()
    with caplog.at_level(logging.WARNING):
        source._callback(b"\x00\x00", 1, 0.0, status="input overflow")
    assert "input overflow" in caplog.text.lower()


def test_audio_source_with_asio_channels_stores_zero_based_selectors():
    """asio_channels=[12, 13] is stored as-is (already zero-based from caller)."""
    source = AudioSource(
        device=48, sample_rate=44100, num_channels=2, asio_channels=[12, 13]
    )
    assert source.asio_channels == [12, 13]
    assert source.num_channels == 2
