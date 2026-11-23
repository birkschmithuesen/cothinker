# CoThinker — tests of the shared Speechmatics EU realtime client
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Tests of `shared/speechmatics.py`: the wire config (`sm_config`), the
transcript-assembly helpers, and `SpeechmaticsStream`'s read loop.

The pure helpers need no network and no `websockets`. The full
`SpeechmaticsStream` lifecycle is checked against a scripted fake server
(`tests/fake_speechmatics.py`); those checks `importorskip("websockets")`
so the file still runs (just fewer checks) where that package is absent.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

from shared import speechmatics as sm


# ============================================================ pure helpers
def test_import_needs_no_workshop():
    """`shared/speechmatics.py` must be importable on its own -- no
    `workshop` package, which is not part of this repository."""
    assert "workshop" not in inspect.getsource(sm)


def test_max_speakers_is_the_default_table_size():
    assert sm.MAX_SPEAKERS == sm.DEFAULT_TABLE_SIZE == 5


def test_effective_max_speakers_default_adds_three():
    assert sm.effective_max_speakers(None) == sm.MAX_SPEAKERS + 3


def test_effective_max_speakers_explicit_value_used():
    assert sm.effective_max_speakers(10) == 10


def test_sm_config_message_and_audio_format():
    cfg = sm.sm_config(None, False, "de", 1.0, 0.5)
    assert cfg["message"] == "StartRecognition"
    assert cfg["audio_format"] == {"type": "raw", "encoding": "pcm_s16le",
                                   "sample_rate": sm.RATE}


def test_sm_config_transcription_config_fields():
    cfg = sm.sm_config(None, False, "de", 1.2, 0.6)
    tcfg = cfg["transcription_config"]
    assert tcfg["language"] == "de"
    assert tcfg["model"] == "enhanced"
    assert tcfg["diarization"] == "speaker"
    assert tcfg["enable_partials"] is True
    assert tcfg["max_delay"] == 1.2
    assert tcfg["conversation_config"] == {"end_of_utterance_silence_trigger": 0.6}
    assert tcfg["audio_filtering_config"] == {}


def test_sm_config_omits_speakers_when_none():
    cfg = sm.sm_config(None, False, "de", 1.0, 0.5)
    assert "speakers" not in cfg["transcription_config"]["speaker_diarization_config"]


def test_sm_config_includes_speakers_when_given():
    speakers = [{"label": "P1", "speaker_identifiers": ["abc"]}]
    cfg = sm.sm_config(speakers, False, "de", 1.0, 0.5)
    assert cfg["transcription_config"]["speaker_diarization_config"]["speakers"] == speakers


def test_sm_config_get_speakers_flag_set():
    cfg = sm.sm_config(None, True, "de", 1.0, 0.5)
    assert cfg["transcription_config"]["speaker_diarization_config"]["get_speakers"] is True


def test_sm_config_get_speakers_omitted_by_default():
    cfg = sm.sm_config(None, False, "de", 1.0, 0.5)
    assert "get_speakers" not in cfg["transcription_config"]["speaker_diarization_config"]


def test_sm_config_speaker_sensitivity_omitted_by_default():
    cfg = sm.sm_config(None, False, "de", 1.0, 0.5)
    assert "speaker_sensitivity" not in cfg["transcription_config"]["speaker_diarization_config"]


def test_sm_config_speaker_sensitivity_included_when_given():
    cfg = sm.sm_config(None, False, "de", 1.0, 0.5, speaker_sensitivity=0.3)
    assert cfg["transcription_config"]["speaker_diarization_config"]["speaker_sensitivity"] == 0.3


def test_sm_config_additional_vocab_omitted_when_none():
    cfg = sm.sm_config(None, False, "de", 1.0, 0.5)
    assert "additional_vocab" not in cfg["transcription_config"]


def test_sm_config_additional_vocab_included_when_given():
    vocab = [{"content": "CoThinker"}]
    cfg = sm.sm_config(None, False, "de", 1.0, 0.5, additional_vocab=vocab)
    assert cfg["transcription_config"]["additional_vocab"] == vocab


def test_sm_config_max_speakers_default():
    cfg = sm.sm_config(None, False, "de", 1.0, 0.5)
    assert (cfg["transcription_config"]["speaker_diarization_config"]["max_speakers"]
           == sm.MAX_SPEAKERS + 3)


def test_sm_config_max_speakers_explicit():
    cfg = sm.sm_config(None, False, "de", 1.0, 0.5, max_speakers=9)
    assert cfg["transcription_config"]["speaker_diarization_config"]["max_speakers"] == 9


def test_sm_config_prefer_current_speaker_default_false():
    cfg = sm.sm_config(None, False, "de", 1.0, 0.5)
    assert cfg["transcription_config"]["speaker_diarization_config"]["prefer_current_speaker"] is False


def test_sm_config_prefer_current_speaker_true():
    cfg = sm.sm_config(None, False, "de", 1.0, 0.5, prefer_current_speaker=True)
    assert cfg["transcription_config"]["speaker_diarization_config"]["prefer_current_speaker"] is True


def test_word_results_filters_word_type():
    msg = {"results": [{"type": "word", "alternatives": [{"content": "hi"}]},
                       {"type": "punctuation", "alternatives": [{"content": "."}]}]}
    assert sm._word_results(msg) == [msg["results"][0]]


def test_word_volume_reads_sibling_field():
    assert sm._word_volume({"start_time": 0.1, "end_time": 0.2, "volume": 0.42}) == 0.42


def test_word_volume_missing_is_none():
    assert sm._word_volume({"start_time": 0.1}) is None


def test_transcript_results_includes_word_and_punctuation():
    msg = {"results": [{"type": "word"}, {"type": "punctuation"}, {"type": "other"}]}
    assert len(sm._transcript_results(msg)) == 2


def test_join_transcript_respects_attaches_to_previous():
    results = [{"type": "word", "alternatives": [{"content": "Hallo"}]},
              {"type": "punctuation", "attaches_to": "previous",
               "alternatives": [{"content": ","}]},
              {"type": "word", "alternatives": [{"content": "Welt"}]}]
    assert sm._join_transcript(results) == "Hallo, Welt"


def test_join_transcript_respects_attaches_to_next():
    results = [{"type": "word", "alternatives": [{"content": "sagte"}]},
              {"type": "punctuation", "attaches_to": "next",
               "alternatives": [{"content": '"'}]},
              {"type": "word", "alternatives": [{"content": "Hallo"}]}]
    assert sm._join_transcript(results) == 'sagte "Hallo'


def test_join_transcript_empty_content_skipped():
    results = [{"type": "word", "alternatives": [{"content": ""}]},
              {"type": "word", "alternatives": [{"content": "Hallo"}]}]
    assert sm._join_transcript(results) == "Hallo"


def test_sttevent_defaults():
    ev = sm.SttEvent(kind="final", text="hi", speaker_label=None,
                     start_s=None, end_s=None, msg_id="m1")
    assert ev.volume is None
    assert ev.words is None


def _stream() -> "sm.SpeechmaticsStream":
    return sm.SpeechmaticsStream("key", "de", None)


def test_error_event_uses_type_as_text():
    ev = _stream()._error_event({"type": "bad_request"})
    assert ev.kind == "error" and ev.text == "bad_request"


def test_error_event_defaults_to_literal_error_text():
    ev = _stream()._error_event({})
    assert ev.text == "error"


def test_eou_event_reads_metadata_span():
    ev = _stream()._eou_event({"metadata": {"start_time": 1.0, "end_time": 2.5}})
    assert ev.kind == "eou" and ev.start_s == 1.0 and ev.end_s == 2.5 and ev.text == ""


def test_transcript_event_majority_speaker_label():
    msg = {"results": [
        {"type": "word", "start_time": 0.0, "end_time": 0.2,
         "alternatives": [{"content": "Hallo", "speaker": "S1"}]},
        {"type": "word", "start_time": 0.2, "end_time": 0.4,
         "alternatives": [{"content": "Welt", "speaker": "S1"}]},
    ]}
    ev = _stream()._transcript_event("AddTranscript", msg)
    assert ev.kind == "final" and ev.speaker_label == "S1"
    assert ev.start_s == 0.0 and ev.end_s == 0.4


def test_transcript_event_partial_kind():
    msg = {"results": [{"type": "word", "alternatives": [{"content": "Hal"}]}]}
    ev = _stream()._transcript_event("AddPartialTranscript", msg)
    assert ev.kind == "partial"


def test_transcript_event_volume_mean_of_words():
    msg = {"results": [
        {"type": "word", "volume": 0.2, "alternatives": [{"content": "a"}]},
        {"type": "word", "volume": 0.6, "alternatives": [{"content": "b"}]},
    ]}
    ev = _stream()._transcript_event("AddTranscript", msg)
    assert ev.volume == pytest.approx(0.4)


def test_transcript_event_words_none_when_no_word_results():
    ev = _stream()._transcript_event("AddTranscript", {"results": []})
    assert ev.words is None


def test_transcript_event_words_items_built_from_word_results():
    msg = {"results": [{"type": "word", "start_time": 0.0, "end_time": 0.3,
                        "volume": 0.5,
                        "alternatives": [{"content": "Hallo", "speaker": "S2"}]}]}
    ev = _stream()._transcript_event("AddTranscript", msg)
    assert ev.words == [{"w": "Hallo", "label": "S2", "volume": 0.5,
                         "start_s": 0.0, "end_s": 0.3}]


# ============================================= SpeechmaticsStream, live loop
def test_stream_sends_start_recognition_with_config():
    pytest.importorskip("websockets")
    from fake_speechmatics import FakeSpeechmaticsServer

    async def scenario():
        async with FakeSpeechmaticsServer() as srv:
            stream = sm.SpeechmaticsStream("secret-key", "de", None, url=srv.url)
            await stream.start()
            await stream.close()
            return srv.received

    received = asyncio.run(scenario())
    assert received[0]["message"] == "StartRecognition"
    assert received[0]["transcription_config"]["language"] == "de"


def test_stream_authorization_header_carries_the_key():
    pytest.importorskip("websockets")
    from fake_speechmatics import FakeSpeechmaticsServer

    async def scenario():
        async with FakeSpeechmaticsServer() as srv:
            stream = sm.SpeechmaticsStream("secret-key", "de", None, url=srv.url)
            await stream.start()
            await stream.close()
            return srv.authorization

    assert asyncio.run(scenario()) == "Bearer secret-key"


def test_stream_speakers_and_extra_config_reach_the_wire():
    pytest.importorskip("websockets")
    from fake_speechmatics import FakeSpeechmaticsServer

    speakers = [{"label": "P1", "speaker_identifiers": ["abc"]}]

    async def scenario():
        async with FakeSpeechmaticsServer() as srv:
            stream = sm.SpeechmaticsStream(
                "key", "de", speakers, url=srv.url, max_speakers=9,
                prefer_current_speaker=True, speaker_sensitivity=0.4,
                additional_vocab=[{"content": "CoThinker"}])
            await stream.start()
            await stream.close()
            return srv.received[0]

    start = asyncio.run(scenario())
    dcfg = start["transcription_config"]["speaker_diarization_config"]
    assert dcfg["speakers"] == speakers
    assert dcfg["max_speakers"] == 9
    assert dcfg["prefer_current_speaker"] is True
    assert dcfg["speaker_sensitivity"] == 0.4
    assert start["transcription_config"]["additional_vocab"] == [{"content": "CoThinker"}]


def test_stream_start_raises_no_exception_but_queues_error_event():
    pytest.importorskip("websockets")
    from fake_speechmatics import FakeSpeechmaticsServer

    async def scenario():
        async with FakeSpeechmaticsServer(
                start_message={"message": "Error", "type": "invalid_auth_token"}) as srv:
            stream = sm.SpeechmaticsStream("bad-key", "de", None, url=srv.url)
            await stream.start()
            return [ev async for ev in stream.events()]

    events = asyncio.run(scenario())
    assert len(events) == 1
    assert events[0].kind == "error" and events[0].text == "invalid_auth_token"


def test_stream_events_yields_partial_then_final():
    pytest.importorskip("websockets")
    from fake_speechmatics import FakeSpeechmaticsServer

    script = [
        {"message": "AddPartialTranscript",
         "results": [{"type": "word", "alternatives": [{"content": "Hal"}]}]},
        {"message": "AddTranscript",
         "results": [{"type": "word", "alternatives": [{"content": "Hallo"}]}]},
        {"message": "EndOfTranscript"},
    ]

    async def scenario():
        async with FakeSpeechmaticsServer(script=script) as srv:
            stream = sm.SpeechmaticsStream("key", "de", None, url=srv.url)
            await stream.start()
            return [ev async for ev in stream.events()]

    events = asyncio.run(scenario())
    assert [e.kind for e in events] == ["partial", "final"]
    assert events[0].text == "Hal" and events[1].text == "Hallo"


def test_stream_eou_event_between_finals():
    pytest.importorskip("websockets")
    from fake_speechmatics import FakeSpeechmaticsServer

    script = [
        {"message": "AddTranscript",
         "results": [{"type": "word", "alternatives": [{"content": "Hallo"}]}]},
        {"message": "EndOfUtterance", "metadata": {"start_time": 0.0, "end_time": 1.0}},
        {"message": "EndOfTranscript"},
    ]

    async def scenario():
        async with FakeSpeechmaticsServer(script=script) as srv:
            stream = sm.SpeechmaticsStream("key", "de", None, url=srv.url)
            await stream.start()
            return [ev async for ev in stream.events()]

    events = asyncio.run(scenario())
    assert [e.kind for e in events] == ["final", "eou"]
    assert events[1].start_s == 0.0 and events[1].end_s == 1.0


def test_stream_error_message_during_read_loop_ends_stream():
    pytest.importorskip("websockets")
    from fake_speechmatics import FakeSpeechmaticsServer

    script = [{"message": "Error", "type": "buffer_error"}]

    async def scenario():
        async with FakeSpeechmaticsServer(script=script) as srv:
            stream = sm.SpeechmaticsStream("key", "de", None, url=srv.url)
            await stream.start()
            return [ev async for ev in stream.events()]

    events = asyncio.run(scenario())
    assert len(events) == 1
    assert events[0].kind == "error" and events[0].text == "buffer_error"


def test_stream_send_pcm_counts_chunks_sent():
    pytest.importorskip("websockets")
    from fake_speechmatics import FakeSpeechmaticsServer

    async def scenario():
        async with FakeSpeechmaticsServer() as srv:
            stream = sm.SpeechmaticsStream("key", "de", None, url=srv.url)
            await stream.start()
            await stream.send_pcm(b"\x00\x01" * 10)
            await stream.send_pcm(b"")   # empty chunk is a no-op
            n = stream._chunks_sent
            await stream.close()
            return n

    assert asyncio.run(scenario()) == 1


def test_stream_close_sends_end_of_stream_with_chunk_count():
    pytest.importorskip("websockets")
    from fake_speechmatics import FakeSpeechmaticsServer

    async def scenario():
        async with FakeSpeechmaticsServer() as srv:
            stream = sm.SpeechmaticsStream("key", "de", None, url=srv.url)
            await stream.start()
            await stream.send_pcm(b"\x00" * 10)
            await stream.send_pcm(b"\x00" * 10)
            await stream.close()
            return srv.received

    received = asyncio.run(scenario())
    end_messages = [m for m in received if isinstance(m, dict)
                    and m.get("message") == "EndOfStream"]
    assert end_messages == [{"message": "EndOfStream", "last_seq_no": 2}]


def test_stream_events_stops_after_close():
    pytest.importorskip("websockets")
    from fake_speechmatics import FakeSpeechmaticsServer

    async def scenario():
        async with FakeSpeechmaticsServer() as srv:
            stream = sm.SpeechmaticsStream("key", "de", None, url=srv.url)
            await stream.start()
            await stream.close()
            return [ev async for ev in stream.events()]

    assert asyncio.run(scenario()) == []
