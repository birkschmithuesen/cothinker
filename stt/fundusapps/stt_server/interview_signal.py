"""Interview start/stop signal to an external receiver (the "core").

Only the transport lives here. The decision — did the physical switch on the
microphone move? — is `vad.MicGate`, and the wiring is `sr.STTServer`.

Why a POST and not the SSE stream the core already consumes: `docs/stt-contract.md`
over in the core's repo pins that stream to transcription events, and the core's
own decoder (`kg/stt_client.py`) dispatches on `type in ("final", "partial")`
and drops everything else. The MicGateEvent does travel over that stream — the
operator page needs it — but the core would never see it. So the signal takes
its own, deliberately dull path.

Fire-and-forget by design: `sr.STTServer` calls this from a throwaway thread and
a failure here must never touch recognition. A core that is down means the
interview is not marked, not that the microphone stops working.
"""
import logging

import requests

# Short and asymmetric: connecting to a machine on the same exhibition LAN
# either works immediately or is not going to. The read timeout is longer
# because the core closes a session synchronously in the handler.
TIMEOUT = (2, 5)


def make_interview_signal_callback(kg_url, timeout=TIMEOUT):
    """Build the (mic_on: bool) -> None callback, or None when no URL is set.

    Returning None rather than a no-op callable is the point: `STTServer` then
    skips the thread entirely, and `--kg-url` being empty is a configuration,
    not an error to be logged once a switch.
    """
    base = (kg_url or "").strip().rstrip("/")
    if not base:
        return None

    url = f"{base}/api/interview_switch"

    def _signal(mic_on):
        payload = {"on": bool(mic_on), "source": "mic_switch"}
        try:
            response = requests.post(url, json=payload, timeout=timeout)
        except Exception as e:
            logging.error(f"Interview signal to {url} failed: {e}")
            return
        if response.status_code not in (200, 202, 204):
            logging.error(
                f"Interview signal to {url} rejected: "
                f"{response.status_code} - {response.text[:200]}"
            )
            return
        logging.info(
            f"Interview signal sent: {'start' if mic_on else 'stop'} -> {url}"
        )

    return _signal
