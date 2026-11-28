"""Standard end-to-end acceptance run (ADR 0022 level 4, issue #153).

Covers the parts of `demo.acceptance.e2e` that do not need a network, a
browser or `piper`/`espeak` installed: report building and token
redaction, the preflight matrix (mocked HTTP), SKIPPED-not-PASS for the
espeak fallback, the WebRTC stats classifier, and the probe script
builder. The participant/surfaces/contract orchestration itself is only
exercised by the real run documented in `docs/acceptance.md`.
"""
from __future__ import annotations

import json

import pytest

from demo.acceptance import e2e


# --------------------------------------------------------------------------
# Report / Check / redaction
# --------------------------------------------------------------------------


def test_report_redacts_token_from_check_detail():
    report = e2e.Report(base_url="https://example.org", token="s3cr3t-token")
    report.add("preflight stage", "PASS", "GET https://example.org/stage/?t=s3cr3t-token -> 200")
    assert "s3cr3t-token" not in json.dumps(report.to_dict())
    assert "s3cr3t-token" not in report.to_markdown()
    assert "<redacted>" in report.to_markdown()


def test_report_passed_is_false_with_any_fail():
    report = e2e.Report(base_url="https://example.org", token="x")
    report.add("a", "PASS")
    report.add("b", "SKIPPED")
    assert report.passed
    report.add("c", "FAIL")
    assert not report.passed


def test_report_to_markdown_lists_every_check_with_its_status():
    report = e2e.Report(base_url="https://example.org", token="x")
    report.add("a", "PASS", "detail a")
    report.add("b", "FAIL", "detail b")
    md = report.to_markdown()
    assert "PASS" in md and "a" in md and "detail a" in md
    assert "FAIL" in md and "b" in md and "detail b" in md


def test_redact_leaves_text_unchanged_without_a_match():
    assert e2e.redact("no secret here", "s3cr3t-token") == "no secret here"


def test_redact_handles_empty_token():
    assert e2e.redact("https://example.org/?t=", "") == "https://example.org/?t="


# --------------------------------------------------------------------------
# Preflight matrix (point 1), mocked HTTP
# --------------------------------------------------------------------------


def test_preflight_passes_when_token_gates_every_route():
    def fetch(url: str) -> int:
        return 200 if "t=good-token" in url else 404

    checks = e2e.preflight("https://example.org", "good-token", fetch=fetch)
    assert checks
    assert all(c.status == "PASS" for c in checks)
    assert {c.name for c in checks} == {"preflight " + name for name in e2e.DEFAULT_ROUTES}


def test_preflight_fails_a_route_that_ignores_the_token():
    def fetch(url: str) -> int:
        return 200  # always 200, token or not -- the route is not gated

    checks = e2e.preflight("https://example.org", "good-token", fetch=fetch)
    assert all(c.status == "FAIL" for c in checks)


def test_preflight_fails_a_route_that_404s_even_with_the_token():
    def fetch(url: str) -> int:
        return 404

    checks = e2e.preflight("https://example.org", "good-token", fetch=fetch)
    assert all(c.status == "FAIL" for c in checks)


def test_check_tls_reports_fail_on_certificate_error():
    import ssl

    def fetch(url: str) -> int:
        raise ssl.SSLCertVerificationError("self-signed certificate")

    check = e2e.check_tls("https://example.org", fetch=fetch)
    assert check.status == "FAIL"
    assert "certificate" in check.detail.lower()


def test_check_tls_reports_pass_when_fetch_succeeds():
    check = e2e.check_tls("https://example.org", fetch=lambda url: 200)
    assert check.status == "PASS"


# --------------------------------------------------------------------------
# Results (point 7): wake check is SKIPPED, never PASS, for the espeak
# fallback.
# --------------------------------------------------------------------------


def test_wait_for_call_answer_detects_a_non_empty_call_answer():
    snapshots = iter([{"call_answer": ""}, {"call_answer": ""}, {"call_answer": "Hier die Zusammenfassung."}])
    ticks = iter([0.0, 0.0, 2.0, 2.0, 4.0, 4.0])
    seen, elapsed = e2e.wait_for_call_answer(
        lambda: next(snapshots), timeout_s=60.0, poll_interval=2.0,
        clock=lambda: next(ticks), sleep=lambda _s: None)
    assert seen is True
    assert elapsed == 4.0


def test_wait_for_call_answer_times_out_without_an_answer():
    ticks = iter([0.0, 10.0, 70.0])
    seen, elapsed = e2e.wait_for_call_answer(
        lambda: {"call_answer": ""}, timeout_s=60.0, poll_interval=2.0,
        clock=lambda: next(ticks), sleep=lambda _s: None)
    assert seen is False
    assert elapsed == 60.0


def test_wake_check_is_skipped_for_espeak_even_with_a_fast_answer():
    check = e2e.wake_check("espeak", lane_answer_seen=True, elapsed_s=3.0)
    assert check.status == "SKIPPED"
    assert "espeak" in check.detail.lower()


def test_wake_check_passes_for_piper_within_timeout():
    check = e2e.wake_check("piper", lane_answer_seen=True, elapsed_s=12.0)
    assert check.status == "PASS"


def test_wake_check_fails_for_piper_without_an_answer():
    check = e2e.wake_check("piper", lane_answer_seen=False, elapsed_s=60.0)
    assert check.status == "FAIL"


def test_wake_check_fails_for_piper_answer_too_slow():
    check = e2e.wake_check("piper", lane_answer_seen=True, elapsed_s=61.0)
    assert check.status == "FAIL"


def test_wake_check_boundary_is_inclusive_of_the_timeout_itself():
    # "within 60s" (commit b5d14a0b's own wording) includes the 60s mark
    # itself; only strictly past it is too slow.
    at_timeout = e2e.wake_check("piper", lane_answer_seen=True, elapsed_s=60.0,
                                 timeout_s=60.0)
    assert at_timeout.status == "PASS"
    just_past = e2e.wake_check("piper", lane_answer_seen=True, elapsed_s=60.0001,
                                timeout_s=60.0)
    assert just_past.status == "FAIL"


def test_score_check_passes_at_or_above_min_ratio():
    assert e2e.score_check(ratio=0.9, matched=20, min_ratio=0.9).status == "PASS"


def test_score_check_fails_below_min_ratio():
    assert e2e.score_check(ratio=0.5, matched=20, min_ratio=0.9).status == "FAIL"


def test_score_check_fails_when_nothing_matched():
    check = e2e.score_check(ratio=0.0, matched=0, min_ratio=0.9)
    assert check.status == "FAIL"
    assert "no" in check.detail.lower() or "0" in check.detail


def test_knowledge_check_skipped_without_wiki_dir():
    assert e2e.knowledge_check(wiki_dir_configured=False, hits=0).status == "SKIPPED"


def test_knowledge_check_passes_with_a_hit():
    assert e2e.knowledge_check(wiki_dir_configured=True, hits=2).status == "PASS"


def test_knowledge_check_fails_with_no_hits():
    assert e2e.knowledge_check(wiki_dir_configured=True, hits=0).status == "FAIL"


def test_events_check_reports_pass_or_fail():
    assert e2e.events_check("topic", seen=True).status == "PASS"
    assert e2e.events_check("view-intent", seen=False).status == "FAIL"


def test_count_knowledge_hits_matches_knowledge_or_wiki_notices():
    snapshot = {"notices": [{"kind": "risk"}, {"kind": "knowledge"}, {"kind": "wiki-hit"}]}
    assert e2e.count_knowledge_hits(snapshot) == 2


def test_count_knowledge_hits_is_zero_without_notices():
    assert e2e.count_knowledge_hits({}) == 0


# --------------------------------------------------------------------------
# Media path proof (point 4): classify the selected candidate pair and the
# audio byte counters from a raw `RTCPeerConnection.getStats()` dump.
# --------------------------------------------------------------------------


def _stats(pair_type: str, protocol: str, sent: int, received: int) -> list[dict]:
    return [
        {"id": "pair1", "type": "candidate-pair", "state": "succeeded",
         "nominated": True, "localCandidateId": "local1",
         "bytesSent": sent, "bytesReceived": received},
        {"id": "local1", "type": "local-candidate", "candidateType": pair_type,
         "protocol": protocol},
        {"id": "out1", "type": "outbound-rtp", "kind": "audio", "bytesSent": sent},
        {"id": "in1", "type": "inbound-rtp", "kind": "audio", "bytesReceived": received},
    ]


def test_classify_candidate_pair_reads_type_protocol_and_bytes():
    stats = _stats("srflx", "udp", sent=12000, received=34000)
    result = e2e.classify_candidate_pair(stats)
    assert result == {"candidate_type": "srflx", "protocol": "udp",
                       "audio_bytes_sent": 12000, "audio_bytes_received": 34000}


def test_classify_candidate_pair_with_relay_tcp_and_no_media():
    stats = _stats("relay", "tcp", sent=0, received=0)
    result = e2e.classify_candidate_pair(stats)
    assert result["candidate_type"] == "relay"
    assert result["protocol"] == "tcp"
    assert result["audio_bytes_sent"] == 0


def test_classify_candidate_pair_with_no_pair_at_all():
    result = e2e.classify_candidate_pair([])
    assert result["candidate_type"] is None
    assert result["audio_bytes_sent"] == 0


def test_classify_candidate_pair_picks_most_traffic_when_none_nominated():
    # Confirmed live: several candidate-pairs can reach state "succeeded"
    # during ICE checks (one per local/remote combination tried) before
    # any carries "nominated": true -- the one actually used for media
    # is the one with real traffic, not whichever sorts first.
    stats = [
        {"id": "quiet", "type": "candidate-pair", "state": "succeeded",
         "localCandidateId": "local-quiet", "bytesSent": 0, "bytesReceived": 0},
        {"id": "busy", "type": "candidate-pair", "state": "succeeded",
         "localCandidateId": "local-busy", "bytesSent": 4389, "bytesReceived": 0},
        {"id": "local-quiet", "type": "local-candidate", "candidateType": "host",
         "protocol": "udp"},
        {"id": "local-busy", "type": "local-candidate", "candidateType": "srflx",
         "protocol": "udp"},
    ]
    result = e2e.classify_candidate_pair(stats)
    assert result["candidate_type"] == "srflx"


def test_classify_candidate_pair_prefers_nominated_even_with_fewer_bytes():
    # The exact race the function's own docstring describes: a pair can
    # be nominated while another, merely-succeeded pair still carries
    # more bytes (e.g. retried/stale traffic). Nominated must win anyway.
    stats = [
        {"id": "nominated-quiet", "type": "candidate-pair", "state": "succeeded",
         "nominated": True, "localCandidateId": "local-quiet",
         "bytesSent": 10, "bytesReceived": 0},
        {"id": "busy-not-nominated", "type": "candidate-pair", "state": "succeeded",
         "localCandidateId": "local-busy", "bytesSent": 4389, "bytesReceived": 0},
        {"id": "local-quiet", "type": "local-candidate", "candidateType": "host",
         "protocol": "udp"},
        {"id": "local-busy", "type": "local-candidate", "candidateType": "srflx",
         "protocol": "udp"},
    ]
    result = e2e.classify_candidate_pair(stats)
    assert result["candidate_type"] == "host"


def test_media_check_passes_when_audio_bytes_flow():
    stats = e2e.classify_candidate_pair(_stats("host", "udp", sent=5000, received=5000))
    check = e2e.media_check("Alex", stats)
    assert check.status == "PASS"
    assert "host" in check.detail and "udp" in check.detail


def test_media_check_fails_when_no_audio_bytes_flow():
    stats = e2e.classify_candidate_pair(_stats("relay", "tcp", sent=0, received=0))
    check = e2e.media_check("Sam", stats)
    assert check.status == "FAIL"


# --------------------------------------------------------------------------
# Speech (point 2): the probe timeline reuses make_tts.sentences() for the
# scoreable keyword content and adds the fixed probe lines.
# --------------------------------------------------------------------------


def test_build_timeline_contains_the_wake_line_exactly_twice():
    timeline = e2e.build_timeline(per_speaker=2, lead_in=5.0)
    wake_events = [e for e in timeline["events"] if e["text"] == e2e.WAKE_LINE]
    assert len(wake_events) == 2


def test_build_timeline_contains_one_agenda_and_one_decision_line():
    timeline = e2e.build_timeline(per_speaker=2, lead_in=5.0)
    texts = [e["text"] for e in timeline["events"]]
    assert e2e.AGENDA_LINE in texts
    assert e2e.DECISION_LINE in texts


def test_build_timeline_keeps_the_keyword_events_from_make_tts():
    timeline = e2e.build_timeline(per_speaker=3, lead_in=5.0)
    from demo.acceptance import make_tts
    expected_sentences = {text for text, _kw in make_tts.sentences(6)}
    texts = {e["text"] for e in timeline["events"]}
    assert expected_sentences <= texts


def test_build_timeline_events_do_not_overlap():
    from demo.acceptance import make_tts

    timeline = e2e.build_timeline(per_speaker=2, lead_in=5.0)
    by_speaker: dict[str, list[float]] = {}
    for ev in timeline["events"]:
        by_speaker.setdefault(ev["speaker"], []).append(ev["start_s"])
    for starts in by_speaker.values():
        starts.sort()
        for a, b in zip(starts, starts[1:]):
            assert b - a >= make_tts.SLOT_S


# --------------------------------------------------------------------------
# Cleanup (point 8): the tool never deletes data, it prints the command.
# --------------------------------------------------------------------------


def test_run_contract_check_redacts_token_from_its_own_subreport(tmp_path, monkeypatch):
    contract_dir = tmp_path / "contract"
    contract_dir.mkdir()

    def fake_run(cmd, capture_output, text):
        (contract_dir / "report.json").write_text(
            json.dumps({"surface": "https://example.org/monitor/?t=s3cr3t", "pass": True}))

        class _Result:
            returncode = 0
            stdout = "PASS: overall"
            stderr = ""
        return _Result()

    monkeypatch.setattr(e2e.subprocess, "run", fake_run)
    check = e2e.run_contract_check("https://example.org/monitor/?t=s3cr3t", "s3cr3t", tmp_path)
    assert check.status == "PASS"
    sub_report = (contract_dir / "report.json").read_text()
    assert "s3cr3t" not in sub_report
    assert "<redacted>" in sub_report


def test_cleanup_command_names_the_session_prefix_and_no_token():
    cmd = e2e.cleanup_command("e2e-20261124T120000Z")
    assert "e2e-20261124T120000Z" in cmd
    assert "t=" not in cmd
    # Must not claim a specific CLI tool/subcommand that does not exist
    # anywhere in this repo (grep -rn "project-cli" only ever finds this
    # function's own prior, wrong claim).
    assert "cli" not in cmd.lower()
    assert "project-cli" not in cmd
    assert "session delete" not in cmd


# --------------------------------------------------------------------------
# Stage transcript extraction (point 7): the stage's own `.roh` section
# renders each markdown bullet `- **HH:MM** *Speaker:* text` as an `<li>`;
# its innerText reads "HH:MM Speaker: text". Turn that back into the
# bullet score.py already parses, so scoring is reused, not duplicated.
# --------------------------------------------------------------------------


def test_stage_transcript_line_to_bullet_round_trips_through_score():
    from demo.acceptance import score
    bullet = e2e.stage_transcript_line_to_bullet("16:28 Alex: Wir klären den Apfelbaum bis Freitag.")
    assert bullet == "- **16:28** *Alex:* Wir klären den Apfelbaum bis Freitag."
    assert score.parse_line(bullet) == ("Alex", "Wir klären den Apfelbaum bis Freitag.")


def test_stage_transcript_line_to_bullet_rejects_unrecognised_text():
    assert e2e.stage_transcript_line_to_bullet("Rohtranskript (12 Zeilen)") is None


# --------------------------------------------------------------------------
# Voice download (point 2): auto-download with a checksum.
# --------------------------------------------------------------------------


def test_download_voice_writes_file_on_matching_checksum(tmp_path):
    payload = b"fake-onnx-voice-bytes"
    import hashlib
    digest = hashlib.sha256(payload).hexdigest()
    dest = tmp_path / "voice.onnx"
    e2e.download_voice("https://example.org/voice.onnx", dest, digest,
                        fetch=lambda url: payload)
    assert dest.read_bytes() == payload


def test_download_voice_rejects_a_checksum_mismatch(tmp_path):
    dest = tmp_path / "voice.onnx"
    with pytest.raises(ValueError, match="checksum"):
        e2e.download_voice("https://example.org/voice.onnx", dest, "0" * 64,
                            fetch=lambda url: b"other-bytes")
    assert not dest.exists()


# --------------------------------------------------------------------------
# CLI argument parsing
# --------------------------------------------------------------------------


def test_arg_parser_requires_base_url_token_file_and_out():
    with pytest.raises(SystemExit):
        e2e.build_arg_parser().parse_args([])


def test_arg_parser_defaults_tts_to_piper():
    args = e2e.build_arg_parser().parse_args(
        ["--base-url", "https://example.org", "--token-file", "t", "--out", "o"])
    assert args.tts == "piper"


def test_arg_parser_rejects_an_unknown_tts_engine():
    with pytest.raises(SystemExit):
        e2e.build_arg_parser().parse_args(
            ["--base-url", "https://example.org", "--token-file", "t",
             "--out", "o", "--tts", "siri"])


def test_e2e_accepts_preflight_report_flag():
    args = e2e.build_arg_parser().parse_args(
        ["--base-url", "u", "--token-file", "t", "--out", "o",
         "--preflight-report", "p.json"])
    assert args.preflight_report == "p.json"
