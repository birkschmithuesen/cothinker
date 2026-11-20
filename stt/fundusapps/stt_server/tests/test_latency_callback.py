import json
import logging
import sys

# args.py calls parser.parse_args() at import time — provide valid argv
# before any stt_server import so argparse does not consume pytest's args.
_original_argv = sys.argv.copy()
sys.argv = ["pytest", "vosk"]

from fundusapps.stt_server import botland_callbacks  # noqa: E402

sys.argv = _original_argv

from unittest.mock import patch, MagicMock  # noqa: E402


def test_send_message_logs_post_sent_and_response_and_posts_turn_id(caplog):
    fake_response = MagicMock()
    fake_response.status_code = 202
    fake_response.text = "ok"

    botland_callbacks.config["BOTSERVER_HOST"] = "127.0.0.1"
    botland_callbacks.config["BOTSERVER_PORT"] = "5084"

    with patch("fundusapps.stt_server.botland_callbacks.requests.post",
               return_value=fake_response) as post:
        with caplog.at_level(logging.INFO):
            botland_callbacks.send_message_to_botland(
                messages=[{"role": "user", "content": "hi"}],
                sid="sess",
                turn_id="01TURN",
            )

    # The HTTP body must include turn_id and client_sent_ts_ms.
    assert post.called
    body = post.call_args.kwargs["json"]
    assert body["turn_id"] == "01TURN"
    assert isinstance(body["client_sent_ts_ms"], float)

    lat = [r.getMessage() for r in caplog.records if "[LATENCY" in r.getMessage()]
    sent = [m for m in lat if "phase=stt_post_message_sent" in m and "turn=01TURN" in m]
    resp = [m for m in lat if "phase=stt_post_message_response" in m and "turn=01TURN" in m]
    assert len(sent) == 1, lat
    assert len(resp) == 1, lat
    assert "rtt_ms=" in resp[0]


def test_send_message_handles_missing_turn_id_gracefully(caplog):
    """Older callers that don't pass turn_id must still work, just without LATENCY lines."""
    fake = MagicMock(); fake.status_code = 202; fake.text = "ok"
    botland_callbacks.config["BOTSERVER_HOST"] = "127.0.0.1"
    botland_callbacks.config["BOTSERVER_PORT"] = "5084"
    with patch("fundusapps.stt_server.botland_callbacks.requests.post", return_value=fake):
        with caplog.at_level(logging.INFO):
            botland_callbacks.send_message_to_botland(
                messages=[{"role": "user", "content": "hi"}],
                sid="sess",
            )
    lat = [r.getMessage() for r in caplog.records if "[LATENCY" in r.getMessage()]
    assert lat == []
