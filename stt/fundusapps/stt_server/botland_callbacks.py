import logging
import time

import requests

from fundusapps.common.turnid import format_latency_log
from fundusapps.stt_server.args import config


def send_message_to_botland(messages, sid, turn_id=None):
    """POST a finalized transcription to the botserver /message endpoint.

    `turn_id` is forwarded in the JSON body so the botserver can log
    `botserver_message_received` and pair it with the STT-side
    `stt_post_message_sent`/`stt_post_message_response` lines.
    """
    if not config.get('BOTSERVER_HOST') or not config.get('BOTSERVER_PORT'):
        logging.warning("Botland host or port not configured. Skipping sending message.")
        return None

    if not sid:
        logging.warning("Session ID (sid) is not set. Cannot send message to botland.")
        return None

    try:
        cookies = {'session': sid}
        botserver_host = config.get('BOTSERVER_REMOTE_HOST', config['BOTSERVER_HOST'])
        url = f"http://{botserver_host}:{config['BOTSERVER_PORT']}/message"

        body = {
            'messages': messages,
            'session_id': sid,
            'return_model': True,
        }
        if turn_id:
            client_sent_ts_ms = time.time() * 1000
            body['turn_id'] = turn_id
            body['client_sent_ts_ms'] = client_sent_ts_ms
            logging.info(format_latency_log(
                turn_id,
                "stt_post_message_sent",
                client_sent_ts_ms,
            ))

        t0 = time.time()
        logging.info(f"Sending message to botland with session ID {sid}: {messages}")
        response = requests.post(
            url,
            headers={
                'Content-Type': 'application/json',
                'User-Agent': 'FundusBot STT Server',
                'X-Session-Id': sid,
            },
            json=body,
            cookies=cookies,
            timeout=(2, 5),
        )
        rtt_ms = (time.time() - t0) * 1000

        if turn_id:
            logging.info(format_latency_log(
                turn_id,
                "stt_post_message_response",
                time.time() * 1000,
                status=response.status_code,
                rtt_ms=round(rtt_ms, 2),
            ))
    except Exception as e:
        logging.error(f"Error sending message to botland: {e}")
        return None

    if response.status_code not in (200, 202):
        logging.error(f"Error response from botland: {response.status_code} - {response.text}")
        return None
