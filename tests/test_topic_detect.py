# CoThinker — tests of the topic-boundary detectors
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""Tests of the topic-boundary candidates (drift, laya, jev), the embeddings
client, the laya worker protocol and the jev decisions client.

Ported from a script-style test: one pytest function per former check, same
names (snake_case). The module body runs once at import, in the original
order, because later checks build on state earlier ones left behind; every
test function asserts the recorded result of its check.

The original also covered the evaluation bench (bench/replay.py, bench/loso.py,
bench/detect.py, bench/run.py). That package is not part of this repository;
its checks are kept as skipped stubs so the names and counts stay visible.

NO CALL REACHES the internet: the clients talk to local ThreadingHTTPServers,
the laya detector talks to a fake worker script written into a temp dir, and
no `torch` is imported.

EVERY sentence in here is INVENTED. No line of a real meeting is in this
file, and none may ever be added.

    python3 -m pytest -q tests/test_topic_detect.py
"""
from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from blocks.topics import detect as td  # noqa: E402

_NEEDS_BENCH = ("needs the bench/ evaluation package (replay, session, loso, "
                "detect, run), which is not part of this repository")


@dataclass
class Utterance:
    """Stand-in for the bench's Utterance: block_views() is duck-typed."""
    index: int
    at: float
    clock: str
    chars: int
    text: str


@dataclass
class BlockCut:
    """Stand-in for the bench's BlockCut: block_views() is duck-typed."""
    reason: str
    kind: str
    first_idx: int
    last_idx: int
    at: float
    chars: int


RESULTS: dict = {}


def _slug(text: str, limit: int = 48) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:limit].strip("_")


def check(name, label, cond, detail=""):
    """Record one former `check(...)` result; the test function of the same
    name asserts it. The module body runs once, in the original order,
    because later checks build on the state earlier ones left behind."""
    RESULTS[name] = (bool(cond), str(detail) or label)


def _verify(name):
    ok, detail = RESULTS[name]
    assert ok, detail

# Invented sentences. Nothing here was ever said by anybody.
SAETZE = [
    "Wir beginnen heute mit dem Thema Vogelfutter im Winter.",
    "Meisen nehmen Sonnenblumenkerne, Amseln eher Rosinen.",
    "Damit ist der Punkt erledigt.",
    "Helfer, zeig uns bitte die Liste der Futterstellen.",
    "Jetzt zum Zeitplan fuer die Nistkaesten im Maerz.",
]

# ================================================== 1. vectors and windows
print("blocks/topics/detect.py: the vector helpers")
check("test_cosine_of_a_vector_with_itself_is_1", "cosine of a vector with itself is 1",
      abs(td.cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) - 1.0) < 1e-12,
      str(td.cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])))
check("test_cosine_of_two_orthogonal_vectors_is_0", "cosine of two orthogonal vectors is 0",
      abs(td.cosine([1.0, 0.0], [0.0, 1.0])) < 1e-12)
check("test_cosine_of_opposite_vectors_is_1_the_drift_score_is_therefore_clamped_by", "cosine of opposite vectors is -1 -- the drift score is therefore "
      "clamped by the caller, not here",
      abs(td.cosine([1.0, 0.0], [-1.0, 0.0]) + 1.0) < 1e-12)
check("test_a_zero_vector_gives_0_0_instead_of_a_division_by_zero", "a zero vector gives 0.0 instead of a division by zero",
      td.cosine([0.0, 0.0], [1.0, 1.0]) == 0.0)
try:
    td.cosine([1.0], [1.0, 2.0])
    laengenfehler = ""
except ValueError as fehler:
    laengenfehler = str(fehler)
check("test_vectors_of_different_length_are_refused_with_both_lengths", "vectors of different length are refused, with both lengths",
      "1" in laengenfehler and "2" in laengenfehler, laengenfehler)
check("test_mean_vector_averages_component_wise", "mean_vector averages component-wise",
      td.mean_vector([[0.0, 2.0], [2.0, 4.0]]) == [1.0, 3.0],
      str(td.mean_vector([[0.0, 2.0], [2.0, 4.0]])))
check("test_mean_vector_of_an_empty_list_is_empty_not_an_exception", "mean_vector of an empty list is empty, not an exception",
      td.mean_vector([]) == [])

print("\nthe decision unit: one view per block cut")
utt = [Utterance(index=i, at=1000.0 + 10.0 * i, clock="", chars=len(t),
                    text=t)
       for i, t in enumerate(SAETZE)]
cuts = [BlockCut(reason="pause", kind="pause", first_idx=0, last_idx=1,
                    at=1013.5, chars=len(SAETZE[0]) + 1 + len(SAETZE[1])),
        BlockCut(reason="pause", kind="pause", first_idx=2, last_idx=4,
                    at=1042.5,
                    chars=len(SAETZE[2]) + 1 + len(SAETZE[3]) + 1
                    + len(SAETZE[4]))]
views = td.block_views(cuts, utt)

check("test_one_view_per_cut", "one view per cut", len(views) == 2, str(len(views)))
check("test_index_is_0_based_and_dense", "index is 0-based and dense", [v.index for v in views] == [0, 1],
      str([v.index for v in views]))
check("test_start_is_the_at_of_the_block_s_first_utterance_convention_ii_where_the", "start is the `at` of the block's FIRST utterance -- convention (ii), "
      "where the detector claims the topic begins",
      [v.start for v in views] == [1000.0, 1020.0],
      str([v.start for v in views]))
check("test_end_is_the_at_of_the_block_s_last_utterance_convention_i_the_newest", "end is the `at` of the block's LAST utterance -- convention (i), the "
      "newest speech the decision could see",
      [v.end for v in views] == [1010.0, 1040.0],
      str([v.end for v in views]))
check("test_at_is_the_cut_time_and_t_dec_is_measured_from_it_not_from_end", "`at` is the CUT time, and t_dec is measured from it, not from `end`",
      [v.at for v in views] == [1013.5, 1042.5],
      str([v.at for v in views]))
check("test_the_text_is_the_block_s_utterances_joined_with_one_space", "the text is the block's utterances joined with one space",
      views[0].text == SAETZE[0] + " " + SAETZE[1], views[0].text[:20])
check("test_chars_equals_the_cut_s_own_char_count_the_same_join_gateway_blocks_py", "chars equals the cut's own char count -- the same join "
      "gateway/blocks.py does, so a view is the block and not a rebuild",
      [v.chars for v in views] == [c.chars for c in cuts],
      str([(v.chars, c.chars) for v, c in zip(views, cuts)]))

print("\nthe drift window: earlier blocks only, 180 s back, at most 6")
weit = [td.BlockView(index=i, start=1000.0 + 40.0 * i, end=1020.0 + 40.0 * i,
                     at=1023.0 + 40.0 * i, text=f"block {i}", chars=7)
        for i in range(9)]
check("test_only_blocks_whose_end_is_at_most_180_s_before_this_block_s_start_stay", "only blocks whose end is at most 180 s before this block's start "
      "stay in the window",
      [v.index for v in td.drift_window(weit[:8], weit[8])] == [3, 4, 5, 6, 7],
      str([v.index for v in td.drift_window(weit[:8], weit[8])]))
dicht = [td.BlockView(index=i, start=1000.0 + 10.0 * i, end=1005.0 + 10.0 * i,
                      at=1006.0 + 10.0 * i, text=f"block {i}", chars=7)
         for i in range(11)]
check("test_at_most_six_blocks_and_the_newest_six", "at most six blocks, and the NEWEST six",
      [v.index for v in td.drift_window(dicht[:10], dicht[10])]
      == [4, 5, 6, 7, 8, 9],
      str([v.index for v in td.drift_window(dicht[:10], dicht[10])]))
check("test_an_empty_history_gives_an_empty_window", "an empty history gives an empty window", td.drift_window([], weit[0]) == [])
fern = td.BlockView(index=99, start=900000.0, end=900010.0, at=900013.0,
                    text="spaeter", chars=7)
check("test_a_block_after_a_long_silence_has_an_empty_window_no_decision_is", "a block after a long silence has an empty window -- no decision is "
      "possible there, and the detector has to say so",
      td.drift_window(weit, fern) == [])

print("\nthe boundary rule: p >= threshold, and t_dec = cut time + latency")
punkte = [td.Score(index=0, p=0.10, ms=200.0),
          td.Score(index=1, p=0.50, ms=250.0)]
grenzen = td.boundaries(punkte, views, 0.40)
check("test_only_a_block_whose_score_reaches_the_threshold_becomes_a_boundary", "only a block whose score reaches the threshold becomes a boundary",
      [g[0] for g in grenzen] == [1], str(grenzen))
check("test_the_boundary_carries_the_evidence_position_the_claim_position_and_t_dec", "the boundary carries the evidence position, the claim position and "
      "t_dec = cut time + measured latency",
      grenzen[0][1:] == (views[1].end, views[1].start, views[1].at + 0.25),
      str(grenzen[0]))
check("test_a_score_exactly_at_the_threshold_is_a_boundary_the_rule_is_p_threshold", "a score exactly AT the threshold is a boundary -- the rule is "
      "p >= threshold, spelled out because the LOSO candidates are the "
      "scores themselves",
      [g[0] for g in td.boundaries(punkte, views, 0.50)] == [1],
      str(td.boundaries(punkte, views, 0.50)))
check("test_a_block_carrying_an_error_token_never_becomes_a_boundary_whatever_its_p", "a block carrying an error token never becomes a boundary, whatever "
      "its p says",
      td.boundaries([td.Score(index=1, p=1.0, ms=1.0, error="timeout")],
                    views, 0.0) == [])

try:
    td.Detector().score([], views[0])
    vertrag = ""
except NotImplementedError as fehler:
    vertrag = str(fehler) or "raised"
check("test_the_detector_base_class_is_a_contract_not_a_detector_score_raises", "the Detector base class is a contract, not a detector: score() "
      "raises NotImplementedError", vertrag != "", vertrag)

# ============================================ 2. the embeddings client
print("\ngateway/infomaniak_embed.py: one POST, no key anywhere near stdout")

import json  # noqa: E402
import threading  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402

from gateway import infomaniak_embed as bie  # noqa: E402

# A key-shaped string that is NOT a key, short enough to stay under the
# leak guard's 16-character secret pattern.
SCHLUESSEL = "fake-key-0815"
# The canary: if this word ever reaches stdout, stderr or an exception
# message, the data rule is broken. Same net as tests/test_bench.py.
KANARI = "KANARIENVOGEL"


def embed_antwort(dims=4, tokens=7, model="bge_multilingual_gemma2"):
    return (200, {"object": "list", "model": model,
                  "data": [{"object": "embedding", "index": 0,
                            "embedding": [0.5] * dims}],
                  "usage": {"prompt_tokens": tokens}})


class FakeEmbeddings(BaseHTTPRequestHandler):
    """A stand-in for the Infomaniak endpoint. Records every request."""
    protocol_version = "HTTP/1.1"
    requests: list = []
    replies: list = []          # (status, body) popped in order

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        roh = self.rfile.read(n)
        FakeEmbeddings.requests.append(
            {"path": self.path,
             "auth": self.headers.get("Authorization"),
             "body": json.loads(roh.decode("utf-8"))})
        status, body = (FakeEmbeddings.replies.pop(0)
                        if FakeEmbeddings.replies else embed_antwort())
        daten = json.dumps(body).encode("utf-8") if isinstance(body, dict) \
            else body
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(daten)))
        self.end_headers()
        self.wfile.write(daten)


esrv = ThreadingHTTPServer(("127.0.0.1", 0), FakeEmbeddings)
esrv.daemon_threads = True
threading.Thread(target=esrv.serve_forever, daemon=True).start()
EMBED_BASE = f"http://127.0.0.1:{esrv.server_address[1]}"

FakeEmbeddings.requests.clear()
FakeEmbeddings.replies.clear()
klient = bie.Client(SCHLUESSEL, "bge_multilingual_gemma2",
                    base_url=EMBED_BASE, product_id="110416")
wert = klient.embed("Ein synthetischer Satz ueber Nistkaesten.")
anfrage = FakeEmbeddings.requests[-1]

check("test_the_request_goes_to_2_ai_product_openai_v1_embeddings", "the request goes to /2/ai/<product>/openai/v1/embeddings",
      anfrage["path"] == "/2/ai/110416/openai/v1/embeddings", anfrage["path"])
check("test_the_key_travels_as_a_bearer_token_in_the_authorization_header", "the key travels as a bearer token in the Authorization header",
      anfrage["auth"] == f"Bearer {SCHLUESSEL}", str(anfrage["auth"]))
check("test_the_body_carries_exactly_model_and_a_one_element_input_list", "the body carries exactly model and a one-element input list",
      anfrage["body"] == {"model": "bge_multilingual_gemma2",
                          "input": ["Ein synthetischer Satz ueber "
                                    "Nistkaesten."]},
      str(sorted(anfrage["body"])))
check("test_the_vector_comes_back_with_its_dimension", "the vector comes back with its dimension",
      len(wert.vector) == 4 and wert.vector[0] == 0.5, str(wert.vector))
check("test_tokens_come_from_usage_prompt_tokens_that_is_what_is_billed", "tokens come from usage.prompt_tokens -- that is what is billed",
      wert.tokens == 7, str(wert.tokens))
check("test_latency_is_measured_in_milliseconds", "latency is measured in milliseconds", wert.ms >= 0.0, str(wert.ms))
check("test_the_call_counter_went_up", "the call counter went up", klient.calls == 1, str(klient.calls))
FakeEmbeddings.replies.append(embed_antwort(model="ein-anderes-modell"))
check("test_the_model_the_server_answered_with_is_reported_not_the_one_asked_for_a", "the model the server ANSWERED with is reported, not the one asked "
      "for -- a silently substituted model must be visible",
      klient.embed("noch ein Satz").model == "ein-anderes-modell")

print("\nerrors carry a status or a type, never a response body")
FakeEmbeddings.replies.append(
    (500, {"error": {"message": f"kaputt wegen {KANARI}"}}))
try:
    klient.embed("noch ein Satz")
    fehler500 = ""
except bie.EmbedError as fehler:
    fehler500 = str(fehler)
check("test_an_http_500_raises_embederror_naming_the_status_code", "an HTTP 500 raises EmbedError naming the status code",
      "500" in fehler500, fehler500)
check("test_and_the_message_carries_no_part_of_the_response_body_a_4xx_body_can", "and the message carries NO part of the response body -- a 4xx body "
      "can quote the request, and the request carries the transcript",
      KANARI not in fehler500, fehler500)

FakeEmbeddings.replies.append((200, b"<html>kein JSON</html>"))
try:
    klient.embed("noch ein Satz")
    fehlerjson = ""
except bie.EmbedError as fehler:
    fehlerjson = str(fehler)
check("test_a_non_json_answer_names_the_byte_count_only", "a non-JSON answer names the byte count only",
      "22" in fehlerjson and "html" not in fehlerjson, fehlerjson)

FakeEmbeddings.replies.append((200, {"data": [], "usage": {}}))
try:
    klient.embed("noch ein Satz")
    fehlerleer = ""
except bie.EmbedError as fehler:
    fehlerleer = str(fehler)
check("test_an_answer_without_a_usable_data_array_is_refused", "an answer without a usable data array is refused",
      fehlerleer != "", fehlerleer)

FakeEmbeddings.replies.append(
    (200, {"data": [{"embedding": ["nicht", "numerisch"]}], "usage": {}}))
try:
    klient.embed("noch ein Satz")
    fehlerzahl = ""
except bie.EmbedError as fehler:
    fehlerzahl = str(fehler)
check("test_a_non_numeric_vector_is_refused_with_the_entry_count_and_no_entry", "a non-numeric vector is refused, with the entry count and no entry",
      "2" in fehlerzahl and "numerisch" not in fehlerzahl, fehlerzahl)

check("test_repr_of_the_client_does_not_contain_the_key_a_repr_lands_in_tracebacks", "repr() of the client does not contain the key -- a repr lands in "
      "tracebacks and log lines",
      SCHLUESSEL not in repr(klient), repr(klient))
try:
    bie.Client("", "bge_multilingual_gemma2")
    leerfehler = ""
except bie.EmbedError as fehler:
    leerfehler = str(fehler)
check("test_an_empty_key_is_refused_at_construction_time_naming_the_environment", "an empty key is refused at construction time, naming the "
      "environment variable",
      "INFOMANIAK_API_KEY" in leerfehler, leerfehler)

# ====================================== 3. the laya worker's protocol
print("\nblocks/topics/laya_worker.py: one JSON line in, one JSON line out")

import io  # noqa: E402

from blocks.topics import laya_worker as tlw  # noqa: E402

ZUSTAENDE: list = []


def vorhersage(state, fragen):
    """Stands in for laya's Agent.predict(). No torch anywhere."""
    ZUSTAENDE.append((dict(state), fragen))
    if state["neu"] == "BOOM":
        raise ValueError(f"kaputt wegen {KANARI}")
    return {"answers": {tlw.QUESTION_ID: {tlw.QUESTION_TYPE: 0.73}}}


eingang = io.StringIO("".join(zeile + "\n" for zeile in [
    json.dumps({"id": 1, "vorher": "Block eins", "neu": "Block zwei"}),
    "kein json",
    json.dumps({"id": 3, "vorher": "Block eins", "neu": "BOOM"}),
    json.dumps({"id": 4, "vorher": "Block eins", "neu": "Block vier"}),
]))
ausgang = io.StringIO()
tlw.serve(eingang, ausgang, vorhersage, load_s=1.5)
roh = ausgang.getvalue()
antworten = [json.loads(z) for z in roh.splitlines() if z.strip()]

check("test_the_first_line_is_the_ready_line_and_carries_the_load_time", "the first line is the ready line and carries the load time",
      antworten[0] == {"ready": True, "load_s": 1.5}, str(antworten[0]))
check("test_one_answer_per_request_line_in_order_each_with_the_request_s_own_id", "one answer per request line, in order, each with the request's "
      "own id",
      [a.get("id") for a in antworten[1:]] == [1, -1, 3, 4],
      str([a.get("id") for a in antworten[1:]]))
check("test_p_is_answers_question_id_question_type_of_the_prediction", "p is answers[QUESTION_ID][QUESTION_TYPE] of the prediction",
      antworten[1]["p"] == 0.73, str(antworten[1]))
check("test_ms_is_measured_not_invented", "ms is measured, not invented", antworten[1]["ms"] >= 0.0,
      str(antworten[1]))
check("test_the_state_handed_to_predict_is_exactly_vorher_and_neu", "the state handed to predict is exactly vorher and neu",
      ZUSTAENDE[0][0] == {"vorher": "Block eins", "neu": "Block zwei"},
      str(ZUSTAENDE[0][0]))
check("test_the_question_is_the_pinned_one_the_noul_type_and_the_german_instruction", "the question is the PINNED one: the noul type and the German "
      "instruction, not something assembled per call",
      ZUSTAENDE[0][1] == {"neues_thema": {
          "type": "noul",
          "instructions": "Eroeffnet `neu` ein neues Thema gegenueber "
                          "`vorher`?"}},
      str(ZUSTAENDE[0][1]))
check("test_a_predict_that_raises_answers_with_an_error_and_the_loop_goes_on_one", "a predict that raises answers with an error and the loop goes ON "
      "-- one failed decision must not cost the whole session",
      antworten[3] == {"id": 3, "error": "ValueError"}, str(antworten[3]))
check("test_the_error_is_the_exception_type_never_its_message_a_model_library_s", "the error is the exception TYPE, never its message: a model "
      "library's exception text can quote its input",
      KANARI not in roh, roh[-200:])
check("test_a_line_that_is_not_a_usable_request_is_answered_with_id_1_and_a_token", "a line that is not a usable request is answered with id -1 and a "
      "token, and the next line is still served",
      antworten[2] == {"id": -1, "error": "bad_request"}
      and antworten[4]["id"] == 4, str(antworten[2]))
check("test_importing_this_module_imports_neither_torch_nor_laya_the_protocol_is", "importing this module imports neither torch nor laya -- the "
      "protocol is testable under the repo's own Python 3.9",
      "torch" not in sys.modules and "laya" not in sys.modules,
      str(sorted(m for m in sys.modules if "torch" in m or "laya" == m)))

# ============================================== 4a. the drift detector
print("\nblocks/topics/detect.py: DriftDetector -- 1 - cos against the window")

VEKTOREN = {
    "gleich": [1.0, 0.0, 0.0],
    "aehnlich": [0.8, 0.6, 0.0],
    "anders": [0.0, 1.0, 0.0],
    "gegenteil": [-1.0, 0.0, 0.0],
}
EINBETTUNGEN: list = []


def fake_embed(text):
    EINBETTUNGEN.append(text)
    if text == "BOOM":
        raise ValueError("kaputt")
    return list(VEKTOREN[text]), 7, 11.0


def blick(i, text, start):
    return td.BlockView(index=i, start=start, end=start + 5.0,
                        at=start + 8.0, text=text, chars=len(text))


b0 = blick(0, "gleich", 1000.0)
b1 = blick(1, "aehnlich", 1040.0)
b2 = blick(2, "anders", 1080.0)
b3 = blick(3, "gegenteil", 1120.0)
b4 = blick(4, "anders", 900000.0)
b5 = blick(5, "BOOM", 1160.0)

drift = td.DriftDetector(fake_embed, model="bge_multilingual_gemma2")
EINBETTUNGEN.clear()
s0 = drift.score([], b0)

check("test_the_first_block_scores_0_0_an_empty_window_allows_no_decision_and_a", "the first block scores 0.0 -- an empty window allows no decision, "
      "and a made-up number there would be scored as if it were one",
      s0.p == 0.0 and s0.error == "", str(s0))
check("test_but_the_block_is_embedded_all_the_same_in_live_operation_the_embedding", "...but the block IS embedded all the same: in live operation the "
      "embedding is computed when the block arrives, and the next "
      "decision needs it",
      EINBETTUNGEN == ["gleich"], str(EINBETTUNGEN))

s2 = drift.score([b0, b1], b2)
erwartet = 1.0 - (0.3 / (0.9 ** 2 + 0.3 ** 2) ** 0.5)
check("test_the_score_is_1_cos_against_the_mean_of_the_window_to_the_digit", "the score is 1 - cos against the MEAN of the window, to the digit",
      abs(s2.p - erwartet) < 1e-12, f"{s2.p} vs {erwartet}")
check("test_tokens_and_ms_report_the_current_block_only_in_live_operation_the", "tokens and ms report the CURRENT block only -- in live operation "
      "the window's embeddings exist already, and billing them again "
      "would triple the reported cost of every decision",
      s2.tokens == 7 and 11.0 <= s2.ms < 60.0, str(s2))
s1 = drift.score([b0], b1)
check("test_a_block_on_the_same_topic_scores_lower_than_one_on_a_new_topic", "a block on the same topic scores lower than one on a new topic",
      s1.p < s2.p, f"{s1.p} vs {s2.p}")
s3 = drift.score([b0], b3)
check("test_the_score_is_clamped_into_0_1_an_opposite_vector_gives_2_before_the", "the score is clamped into 0..1 -- an opposite vector gives 2 "
      "before the clamp, and a probability above one is not one",
      s3.p == 1.0, str(s3.p))
check("test_name_and_model_are_reported_because_the_table_has_a_column_for_each", "name and model are reported, because the table has a column for "
      "each", (drift.name, drift.model) == ("drift", "bge_multilingual_gemma2"),
      str((drift.name, drift.model)))
s4 = drift.score([b0, b1, b2], b4)
check("test_a_block_after_a_long_silence_scores_0_0_the_window_is_empty_even_though", "a block after a long silence scores 0.0 -- the window is empty "
      "even though the text differs",
      s4.p == 0.0 and s4.error == "", str(s4))
s5 = drift.score([b0], b5)
check("test_a_failed_embedding_becomes_an_error_token_not_an_exception_one_bad_call", "a failed embedding becomes an error TOKEN, not an exception: one "
      "bad call must not cost the other 227 decisions, and the token "
      "makes the gap visible in the report",
      (s5.error, s5.p) == ("ValueError", 0.0), str(s5))

# ============================================== 4b. the laya detector
print("\nblocks/topics/detect.py: LayaDetector against a FAKE worker")

import atexit  # noqa: E402
import shutil  # noqa: E402
import subprocess as sp  # noqa: E402
import tempfile  # noqa: E402

FAKE_WORKER = '''#!/usr/bin/env python3
"""A stand-in for blocks/topics/laya_worker.py. Same protocol, no torch."""
import json, sys, time
from pathlib import Path

argv = sys.argv[1:]
modell = argv[argv.index("--model") + 1]
protokoll = Path(modell) / "log.jsonl"
if "flut" in modell:
    sys.stderr.write("x" * 200000)
print(json.dumps({"ready": True, "load_s": 0.25}), flush=True)
for zeile in sys.stdin:
    zeile = zeile.strip()
    if not zeile:
        continue
    req = json.loads(zeile)
    vorher, neu = req["vorher"], req["neu"]
    with protokoll.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"id": req["id"], "nv": len(vorher),
                            "nn": len(neu), "vend": vorher[-1:],
                            "nstart": neu[:1]}) + "\\n")
    if neu.startswith("LANGSAM"):
        time.sleep(3.0)
    if neu.startswith("KAPUTT"):
        print(json.dumps({"id": req["id"], "error": "model_failed"}),
              flush=True)
        continue
    print(json.dumps({"id": req["id"],
                      "p": 0.9 if neu.startswith("NEU") else 0.2,
                      "ms": 12.5}), flush=True)
'''

tmp = Path(tempfile.mkdtemp(prefix="cothinker-laya-"))
atexit.register(shutil.rmtree, tmp, ignore_errors=True)
WORKER = tmp / "fake_worker.py"
WORKER.write_text(FAKE_WORKER, encoding="utf-8")


def fake_laya(name, **kw):
    ordner = tmp / name
    ordner.mkdir(parents=True, exist_ok=True)
    return td.LayaDetector(sys.executable, str(WORKER), str(ordner), **kw)


def fake_log(name):
    ziel = tmp / name / "log.jsonl"
    if not ziel.exists():
        return []
    return [json.loads(z) for z in
            ziel.read_text(encoding="utf-8").splitlines() if z.strip()]


det = fake_laya("klein")
l0 = det.score([], blick(0, "Block eins", 1000.0))
check("test_the_first_block_scores_0_0_without_a_call_asking_the_model_against_an", "the first block scores 0.0 WITHOUT a call -- asking the model "
      "against an empty `vorher` would measure its prior, not a boundary",
      (l0.p, l0.error) == (0.0, "") and fake_log("klein") == [], str(l0))
l1 = det.score([blick(0, "Block eins", 1000.0)],
               blick(1, "NEU: Block zwei", 1040.0))
check("test_the_detector_started_the_worker_and_read_its_ready_line", "the detector started the worker and read its ready line",
      det.load_s == 0.25, str(det.load_s))
check("test_the_state_is_the_earlier_blocks_text_as_vorher_and_this_block_s_as_neu", "the state is the earlier blocks' text as `vorher` and this "
      "block's as `neu`",
      fake_log("klein")[0]["nv"] == 10
      and fake_log("klein")[0]["vend"] == "s"
      and fake_log("klein")[0]["nstart"] == "N",
      str(fake_log("klein")))
check("test_p_and_ms_come_from_the_worker_clamped_into_0_1", "p and ms come from the worker, clamped into 0..1",
      (l1.p, l1.ms) == (0.9, 12.5), str(l1))
check("test_the_request_ids_count_up_and_the_answer_is_matched_by_id", "the request ids count up and the answer is matched by id",
      fake_log("klein")[0]["id"] == 1, str(fake_log("klein")))

lang = fake_laya("lang")
vorgeschichte = [blick(i, "A" * 500, 1000.0 + 10.0 * i) for i in range(3)]
lang.score(vorgeschichte, blick(3, "B" * 900, 1040.0))
check("test_vorher_is_cut_to_the_last_1200_characters_and_neu_to_the_first_600_the", "`vorher` is cut to the LAST 1200 characters and `neu` to the "
      "FIRST 600 -- the newest context and the head of the new block are "
      "what the question is about",
      fake_log("lang")[0]["nv"] == 1200
      and fake_log("lang")[0]["nn"] == 600
      and fake_log("lang")[0]["vend"] == "A"
      and fake_log("lang")[0]["nstart"] == "B",
      str(fake_log("lang")))
lang.close()

kaputt = fake_laya("kaputt")
lk = kaputt.score([blick(0, "Block eins", 1000.0)],
                  blick(1, "KAPUTT jetzt", 1040.0))
check("test_a_worker_error_line_becomes_an_error_token_and_p_stays_0_0", "a worker error line becomes an error token, and p stays 0.0",
      (lk.error, lk.p) == ("model_failed", 0.0), str(lk))
kaputt.close()

langsam = fake_laya("langsam", timeout_s=0.5)
lt = langsam.score([blick(0, "Block eins", 1000.0)],
                   blick(1, "LANGSAM jetzt", 1040.0))
check("test_a_request_that_outruns_the_timeout_yields_the_token_timeout", "a request that outruns the timeout yields the token `timeout`",
      lt.error == "timeout", str(lt))
check("test_the_child_is_gone_afterwards_and_every_later_decision_answers_with_the", "...the child is gone afterwards, and every later decision answers "
      "with the same token without starting anything again -- a "
      "desynchronised pipe cannot be measured against",
      langsam._proc is None
      and langsam.score([blick(0, "Block eins", 1000.0)],
                        blick(2, "NEU: noch was", 1080.0)).error == "timeout"
      and len(fake_log("langsam")) == 1,
      str(fake_log("langsam")))

flut = fake_laya("flut")
lf = flut.score([blick(0, "Block eins", 1000.0)],
                blick(1, "NEU: Block zwei", 1040.0))
check("test_a_worker_that_floods_stderr_does_not_deadlock_the_parent_stderr_goes_to", "a worker that floods stderr does not deadlock the parent -- "
      "stderr goes to DEVNULL, and a PIPE nobody reads fills up and "
      "stops the child mid-answer",
      lf.p == 0.9, str(lf))
kind = flut._proc
flut.close()
check("test_close_ends_the_child_process", "close() ends the child process", kind.poll() is not None,
      str(kind.poll()))
check("test_the_three_pinned_constants_are_the_module_s_so_a_later_live_switch", "the three pinned constants are the module's, so a later live "
      "switch reads the same numbers the measurement used",
      (td.LAYA_VORHER_CHARS, td.LAYA_NEU_CHARS, td.LAYA_TIMEOUT_S)
      == (1200, 600, 30.0),
      str((td.LAYA_VORHER_CHARS, td.LAYA_NEU_CHARS, td.LAYA_TIMEOUT_S)))

print("\nblocks/topics/detect.py: laya_input() -- the one place the input "
      "is cut")
lv, ln = td.laya_input([blick(0, "A" * 700, 1000.0),
                        blick(1, "B" * 700, 1010.0)],
                       blick(2, "C" * 900, 1020.0))
check("test_laya_input_joins_the_history_with_one_space_and_keeps_the_last_1200", "laya_input() joins the history with one space and keeps the LAST "
      "1200 characters, and keeps the FIRST 600 of the current block",
      (len(lv), lv[:1], lv[-1:], len(ln), ln[:1])
      == (1200, "A", "B", 600, "C"),
      str((len(lv), lv[:1], lv[-1:], len(ln))))
check("test_an_empty_history_gives_an_empty_vorher_the_case_the_detector_answers", "an empty history gives an empty `vorher` -- the case the detector "
      "answers with 0.0 and never asks the model about",
      td.laya_input([], blick(0, "Neu", 1000.0)) == ("", "Neu"))
check("test_the_cut_lengths_are_parameters_defaulting_to_the_module_constants", "the cut lengths are parameters, defaulting to the module constants",
      td.laya_input([blick(0, "abcdef", 1.0)], blick(1, "uvwxyz", 2.0),
                    vorher_chars=2, neu_chars=3) == ("ef", "uvw"))

# ============================ 10. the OpenRouter decisions client (Jev)
print("\ngateway/openrouter_jev.py: one POST, typed answer, no body in errors")

import time as _time  # noqa: E402

from gateway import openrouter_jev as boj  # noqa: E402

JEV_SCHLUESSEL = "fake-or-4711"
# Standing in for an API error `message` that quotes the request: if this
# string reaches an exception, a Score or any output, the rule is broken.
GEHEIM = "GEHEIMNIS-aus-dem-Request"


def jev_antwort(noul=0.96, choice=None, cost=1.9992e-05, ein=476, aus=70,
                model="typesafe/jev-1.13-20260917"):
    """A 200 answer shaped like the OpenAPI example of /api/alpha/decisions."""
    antworten = {"neues_thema": {"type": "noul", "noul": noul}}
    if choice is not None:
        antworten["gleiches_thema_vertiefung_neu_rueckkehr"] = {
            "type": "choice", "choice": choice, "confidence": 0.75,
            "probabilities": {"gleiches_thema": 0.05, "vertiefung": 0.1,
                              "neues_thema": 0.75, "rueckkehr": 0.1}}
    return (200, {"id": "gen-dec-0001", "model": model,
                  "provider": "TypeSafe", "answers": antworten,
                  "usage": {"cost": cost, "input_tokens": ein,
                            "output_tokens": aus}})


def jev_fehler(status):
    return (status, {"error": {"code": status,
                               "message": f"bad request: {GEHEIM} {KANARI}"}})


class FakeDecisions(BaseHTTPRequestHandler):
    """A stand-in for OpenRouter's decisions endpoint. Records requests.

    A reply `("sleep", s)` waits `s` seconds before answering 200 -- the
    client's timeout is what is being tested there.
    """
    protocol_version = "HTTP/1.1"
    requests: list = []
    replies: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        roh = self.rfile.read(n)
        FakeDecisions.requests.append(
            {"path": self.path,
             "auth": self.headers.get("Authorization"),
             "body": json.loads(roh.decode("utf-8"))})
        status, body = (FakeDecisions.replies.pop(0)
                        if FakeDecisions.replies else jev_antwort())
        mehr = 0
        if status == "sleep":
            _time.sleep(body)
            status, body = jev_antwort()
        elif status == "short":
            # Announce more bytes than are sent, then hang up: the client
            # sees http.client.IncompleteRead, which is not an OSError.
            mehr = body
            status, body = jev_antwort()
            self.close_connection = True
        daten = json.dumps(body).encode("utf-8") if isinstance(body, dict) \
            else body
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(daten) + mehr))
            self.end_headers()
            self.wfile.write(daten)
        except OSError:
            pass    # the client gave up already (timeout test)


jsrv = ThreadingHTTPServer(("127.0.0.1", 0), FakeDecisions)
jsrv.daemon_threads = True
threading.Thread(target=jsrv.serve_forever, daemon=True).start()
JEV_BASE = f"http://127.0.0.1:{jsrv.server_address[1]}"
NOUL_FRAGE = {"neues_thema": {
    "type": "noul",
    "instructions": "Eroeffnet `neu` ein neues Thema gegenueber `vorher`?"}}

FakeDecisions.requests.clear()
FakeDecisions.replies.clear()
jklient = boj.Client(JEV_SCHLUESSEL, base_url=JEV_BASE)
jwert = jklient.decide({"vorher": "Ein Satz ueber Nistkaesten.",
                        "neu": "Ein Satz ueber Vogelfutter."}, NOUL_FRAGE)
janfrage = FakeDecisions.requests[-1]
check("test_the_request_goes_to_api_alpha_decisions", "the request goes to /api/alpha/decisions",
      janfrage["path"] == "/api/alpha/decisions", janfrage["path"])
check("test_the_key_travels_as_a_bearer_token_in_the_authorization_header_2", "the key travels as a bearer token in the Authorization header",
      janfrage["auth"] == f"Bearer {JEV_SCHLUESSEL}", str(janfrage["auth"]))
check("test_the_body_is_exactly_model_state_and_questions_and_the_model_defaults_to", "the body is exactly model, state and questions, and the model "
      "defaults to typesafe/jev-1.13",
      janfrage["body"] == {"model": "typesafe/jev-1.13",
                           "state": {"vorher": "Ein Satz ueber Nistkaesten.",
                                     "neu": "Ein Satz ueber Vogelfutter."},
                           "questions": NOUL_FRAGE},
      str(sorted(janfrage["body"])))
check("test_the_noul_answer_is_read_from_answers_id_noul_the_field_is_called_noul", "the noul answer is read from answers[id]['noul'] -- the field is "
      "called noul, not p",
      jwert.answers["neues_thema"]["noul"] == 0.96, str(jwert.answers))
check("test_tokens_are_input_output_and_cost_is_usage_cost_in_usd", "tokens are input + output, and cost is usage.cost in USD",
      (jwert.input_tokens, jwert.output_tokens, jwert.tokens,
       jwert.cost_usd) == (476, 70, 546, 1.9992e-05), str(jwert))
check("test_the_model_the_server_answered_with_is_reported_a_dated_snapshot_or_a", "the model the server ANSWERED with is reported -- a dated snapshot "
      "or a substitute must be visible",
      jwert.model == "typesafe/jev-1.13-20260917", jwert.model)
check("test_latency_is_measured_and_the_call_counter_went_up", "latency is measured and the call counter went up",
      jwert.ms >= 0.0 and jklient.calls == 1, str((jwert.ms, jklient.calls)))

FakeDecisions.replies.append((200, {
    "answers": {"neues_thema": {"type": "noul", "noul": 0.4,
                                "explanation": f"weil {KANARI}"},
                "x": {"type": "choice", "choice": f"{KANARI} sagt was",
                      "probabilities": {"a b c": 0.5, "ok": 0.5}}},
    "usage": {}}))
jsauber = jklient.decide({"vorher": "a", "neu": "b"},
                         dict(NOUL_FRAGE, x={"type": "choice"}))
check("test_an_answer_is_reduced_to_numbers_and_key_like_tokens_an_unknown_free", "an answer is reduced to numbers and key-like tokens: an unknown "
      "free-text field, a free-text choice and a non-key probability key "
      "are all dropped before anybody can cache or print them",
      KANARI not in json.dumps(jsauber.answers)
      and jsauber.answers["x"] == {"type": "choice",
                                   "probabilities": {"ok": 0.5}},
      json.dumps(jsauber.answers)[:200])
check("test_missing_usage_gives_zero_tokens_and_zero_cost_not_an_exception", "missing usage gives zero tokens and zero cost, not an exception",
      (jsauber.tokens, jsauber.cost_usd) == (0, 0.0), str(jsauber))

print("\nJev errors carry a status, a code or a type -- never the message")
for status in (400, 401, 402, 403, 404, 413):
    FakeDecisions.replies.append(jev_fehler(status))
    try:
        jklient.decide({"vorher": "a", "neu": "b"}, NOUL_FRAGE)
        jf = None
    except boj.JevError as fehler:
        jf = fehler
    text = "" if jf is None else f"{jf} {jf.token} {jf!r} {jf.args}"
    check(f"test_http_{status}_raises_jeverror_with_token_and_error_code", f"HTTP {status} raises JevError with the token http_{status} and "
          f"the numeric error code",
          jf is not None and jf.token == f"http_{status}"
          and f"error code {status}" in str(jf), text)
    check(f"test_http_{status}_leaks_nothing_of_the_error_message", f"...and NOTHING of the error message: the message can quote the "
          f"request, and the request is speech of third parties "
          f"(HTTP {status})",
          GEHEIM not in text and KANARI not in text, text)

FakeDecisions.replies.append((500, b"<html>kein JSON " + GEHEIM.encode()
                              + b"</html>"))
try:
    jklient.decide({"vorher": "a", "neu": "b"}, NOUL_FRAGE)
    jf500 = None
except boj.JevError as fehler:
    jf500 = fehler
check("test_an_error_body_that_is_not_json_still_gives_the_status_token_and_no_code", "an error body that is not JSON still gives the status token, and "
      "no code is invented",
      jf500 is not None and jf500.token == "http_500"
      and "code" not in str(jf500) and GEHEIM not in str(jf500),
      str(jf500))

FakeDecisions.replies.append((200, b"<html>kein JSON</html>"))
try:
    jklient.decide({"vorher": "a", "neu": "b"}, NOUL_FRAGE)
    jfj = None
except boj.JevError as fehler:
    jfj = fehler
check("test_a_non_json_200_answer_names_the_byte_count_only_token_bad_json", "a non-JSON 200 answer names the byte count only, token bad_json",
      jfj is not None and jfj.token == "bad_json" and "22" in str(jfj)
      and "html" not in str(jfj), str(jfj))

FakeDecisions.replies.append((200, {"answers": {}, "usage": {}}))
try:
    jklient.decide({"vorher": "a", "neu": "b"}, NOUL_FRAGE)
    jfm = None
except boj.JevError as fehler:
    jfm = fehler
check("test_an_answer_that_leaves_a_question_unanswered_is_an_error_missing_answer", "an answer that leaves a question unanswered is an error "
      "(missing_answer), not a silent 'no'",
      jfm is not None and jfm.token == "missing_answer", str(jfm))

FakeDecisions.replies.append(("short", 50))
try:
    jklient.decide({"vorher": "a", "neu": "b"}, NOUL_FRAGE)
    jfi = None
    jfi_andere = ""
except boj.JevError as fehler:
    jfi = fehler
    jfi_andere = ""
except Exception as fehler:          # noqa: BLE001
    jfi = None
    jfi_andere = type(fehler).__name__
check("test_a_truncated_body_http_client_incompleteread_an_httpexception_and_not_an", "a truncated body (http.client.IncompleteRead, an HTTPException and "
      "not an OSError) is wrapped in JevError with the type name as token "
      "-- it must not escape unwrapped",
      jfi is not None and jfi.token == "IncompleteRead",
      jfi_andere or str(jfi) + " / " + str(getattr(jfi, "token", "")))

langsam_klient = boj.Client(JEV_SCHLUESSEL, base_url=JEV_BASE, timeout_s=0.3)
FakeDecisions.replies.append(("sleep", 1.5))
try:
    langsam_klient.decide({"vorher": "a", "neu": "b"}, NOUL_FRAGE)
    jft = None
except boj.JevError as fehler:
    jft = fehler
check("test_a_request_that_outruns_the_timeout_raises_jeverror_with_the_token", "a request that outruns the timeout raises JevError with the token "
      "timeout", jft is not None and jft.token == "timeout", str(jft))

import socket  # noqa: E402

# A port that was free a moment ago and has nobody listening on it now.
with socket.socket() as frei:
    frei.bind(("127.0.0.1", 0))
    socket_frei = frei.getsockname()[1]
tot = boj.Client(JEV_SCHLUESSEL, base_url=f"http://127.0.0.1:{socket_frei}",
                 timeout_s=2.0)
try:
    tot.decide({"vorher": "a", "neu": "b"}, NOUL_FRAGE)
    jfc = None
except boj.JevError as fehler:
    jfc = fehler
check("test_a_refused_connection_raises_jeverror_carrying_the_exception_type_name", "a refused connection raises JevError carrying the exception type "
      "name as its token",
      jfc is not None and jfc.token in ("URLError", "ConnectionRefusedError"),
      str(jfc) + " / " + str(getattr(jfc, "token", "")))

check("test_repr_of_the_client_does_not_contain_the_key", "repr() of the client does not contain the key",
      JEV_SCHLUESSEL not in repr(jklient), repr(jklient))
try:
    boj.Client("")
    jleer = ""
except boj.JevError as fehler:
    jleer = str(fehler)
check("test_an_empty_key_is_refused_at_construction_time_naming_the_environment_2", "an empty key is refused at construction time, naming the "
      "environment variable",
      "OPENROUTER_API_KEY" in jleer, jleer)

# ============================================== 11. the jev detector
print("\nblocks/topics/detect.py: JevDetector against the FAKE decisions server")

FakeDecisions.requests.clear()
FakeDecisions.replies.clear()
jdet = td.JevDetector(boj.Client(JEV_SCHLUESSEL, base_url=JEV_BASE))
j0 = jdet.score([], blick(0, "Block eins", 1000.0))
check("test_the_first_block_scores_0_0_without_a_call_asking_against_an_empty", "the first block scores 0.0 WITHOUT a call -- asking against an "
      "empty `vorher` would measure the model's prior, not a boundary",
      (j0.p, j0.error, j0.cost_usd) == (0.0, "", None)
      and FakeDecisions.requests == [], str(j0))

FakeDecisions.replies.append(jev_antwort(noul=0.83, cost=2.5e-05))
j1 = jdet.score([blick(0, "Block eins", 1000.0)],
                blick(1, "Block zwei", 1040.0))
jreq = FakeDecisions.requests[-1]["body"]
check("test_p_is_answers_neues_thema_noul_of_the_answer", "p is answers['neues_thema']['noul'] of the answer",
      j1.p == 0.83 and j1.error == "", str(j1))
check("test_tokens_are_usage_input_tokens_usage_output_tokens", "tokens are usage.input_tokens + usage.output_tokens",
      j1.tokens == 546, str(j1.tokens))
check("test_the_usd_cost_of_the_call_rides_on_the_score_for_cost_record", "the USD cost of the call rides on the Score, for cost_record()",
      j1.cost_usd == 2.5e-05, str(j1.cost_usd))
check("test_ms_is_the_measured_duration_of_the_call", "ms is the measured duration of the call",
      j1.ms >= 0.0 and j1.index == 1, str(j1))
check("test_the_state_is_exactly_vorher_and_neu", "the state is exactly vorher and neu",
      jreq["state"] == {"vorher": "Block eins", "neu": "Block zwei"},
      str(jreq["state"]))
check("test_without_with_choice_exactly_one_question_is_asked_the_pinned_noul", "without with_choice exactly ONE question is asked: the pinned noul "
      "question, in German, the same wording the laya worker uses",
      jreq["questions"] == NOUL_FRAGE and jreq["model"] == "typesafe/jev-1.13",
      str(jreq["questions"]))

vier = [blick(i, t, 1000.0 + 10.0 * i)
        for i, t in enumerate(["Erster Block.", "Zweiter Block.",
                               "Dritter Block."])]
jdet.score(vier, blick(3, "Vierter Block.", 1040.0))
check("test_vorher_is_the_last_two_earlier_blocks_not_the_whole_history_the", "`vorher` is the LAST TWO earlier blocks, not the whole history -- "
      "the question is about the newest context",
      FakeDecisions.requests[-1]["body"]["state"]["vorher"]
      == "Zweiter Block. Dritter Block.",
      FakeDecisions.requests[-1]["body"]["state"]["vorher"])
jdet.score([blick(i, "A" * 500, 1000.0 + 10.0 * i) for i in range(3)],
           blick(3, "B" * 900, 1040.0))
jlang = FakeDecisions.requests[-1]["body"]["state"]
check("test_vorher_is_cut_to_its_last_1200_characters_and_neu_to_its_first_600", "`vorher` is cut to its LAST 1200 characters and `neu` to its FIRST "
      "600",
      (len(jlang["vorher"]), len(jlang["neu"])) == (1001, 600)
      and jlang["neu"] == "B" * 600,
      str((len(jlang["vorher"]), len(jlang["neu"]))))
jdet.score([blick(i, "A" * 900, 1000.0 + 10.0 * i) for i in range(3)],
           blick(3, "B", 1040.0))
check("test_and_a_long_history_really_is_cut_at_1200", "...and a long history really is cut at 1200",
      len(FakeDecisions.requests[-1]["body"]["state"]["vorher"]) == 1200,
      str(len(FakeDecisions.requests[-1]["body"]["state"]["vorher"])))

print("\nwith_choice: one call, two answers (speculative fan-out)")
jwahl = td.JevDetector(boj.Client(JEV_SCHLUESSEL, base_url=JEV_BASE),
                       with_choice=True)
vorher_n = len(FakeDecisions.requests)
FakeDecisions.replies.append(jev_antwort(noul=0.91, choice="neues_thema"))
jw = jwahl.score([blick(0, "Block eins", 1000.0)],
                 blick(1, "Block zwei", 1040.0))
jwreq = FakeDecisions.requests[-1]["body"]["questions"]
check("test_with_choice_asks_both_questions_in_one_request_a_second_call_would", "with_choice asks BOTH questions in ONE request -- a second call "
      "would double the latency and the fixed cost",
      len(FakeDecisions.requests) - vorher_n == 1
      and sorted(jwreq) == ["gleiches_thema_vertiefung_neu_rueckkehr",
                            "neues_thema"],
      str(sorted(jwreq)))
check("test_the_choice_question_is_a_choice_with_the_four_german_criteria", "the choice question is a `choice` with the four German criteria",
      jwreq["gleiches_thema_vertiefung_neu_rueckkehr"]["type"] == "choice"
      and sorted(jwreq["gleiches_thema_vertiefung_neu_rueckkehr"]["criteria"])
      == ["gleiches_thema", "neues_thema", "rueckkehr", "vertiefung"],
      str(jwreq["gleiches_thema_vertiefung_neu_rueckkehr"]))
check("test_p_still_comes_from_the_noul_answer_of_the_same_call", "p still comes from the noul answer of the same call",
      jw.p == 0.91 and jw.error == "", str(jw))
check("test_the_choice_answer_is_kept_per_block_index_on_the_detector_as_the_chosen", "the choice answer is kept per block index on the detector, as the "
      "chosen key, its confidence and the probabilities",
      jwahl.choices.get(1, {}).get("choice") == "neues_thema"
      and jwahl.choices[1]["confidence"] == 0.75
      and jwahl.choices[1]["probabilities"]["neues_thema"] == 0.75,
      str(jwahl.choices))
FakeDecisions.replies.append(jev_antwort(noul=0.2, choice=f"{KANARI} frei"))
jw2 = jwahl.score([blick(0, "Block eins", 1000.0)],
                  blick(2, "Block drei", 1080.0))
check("test_a_choice_outside_the_criteria_we_sent_is_not_recorded_and_the_noul", "a choice outside the criteria we sent is not recorded -- and the "
      "noul answer still counts",
      jw2.p == 0.2 and "choice" not in jwahl.choices.get(2, {})
      and KANARI not in json.dumps(jwahl.choices),
      str(jwahl.choices.get(2)))

print("\nJev failures become error TOKENS on the Score, never a crash")
for status in (400, 401, 402):
    FakeDecisions.replies.append(jev_fehler(status))
    jfs = jdet.score([blick(0, "Block eins", 1000.0)],
                     blick(1, "Block zwei", 1040.0))
    check(f"test_http_{status}_becomes_score_error_token", f"HTTP {status} -> Score.error == 'http_{status}', p 0.0, and "
          f"no part of the API message anywhere in the Score",
          (jfs.error, jfs.p) == (f"http_{status}", 0.0)
          and GEHEIM not in repr(jfs) and KANARI not in repr(jfs),
          repr(jfs))
jdet_langsam = td.JevDetector(boj.Client(JEV_SCHLUESSEL, base_url=JEV_BASE,
                                         timeout_s=0.3))
FakeDecisions.replies.append(("sleep", 1.5))
jft2 = jdet_langsam.score([blick(0, "Block eins", 1000.0)],
                          blick(1, "Block zwei", 1040.0))
check("test_a_timeout_score_error_timeout_no_exception", "a timeout -> Score.error 'timeout', no exception",
      jft2.error == "timeout", str(jft2))
FakeDecisions.replies.append(jev_antwort(noul=0.6))
check("test_and_the_next_decision_is_served_normally_http_holds_no_pipe_that_a_late", "...and the NEXT decision is served normally: HTTP holds no pipe "
      "that a late answer could desynchronise, so unlike laya the "
      "detector does not declare itself dead",
      jdet_langsam.score([blick(0, "Block eins", 1000.0)],
                         blick(2, "Block drei", 1080.0)).p == 0.6)
jdet_tot = td.JevDetector(boj.Client(
    JEV_SCHLUESSEL, base_url=f"http://127.0.0.1:{socket_frei}",
    timeout_s=2.0))
jfc2 = jdet_tot.score([blick(0, "Block eins", 1000.0)],
                      blick(1, "Block zwei", 1040.0))
check("test_a_refused_connection_score_error_carries_the_type_name", "a refused connection -> Score.error carries the type name",
      jfc2.error in ("URLError", "ConnectionRefusedError"), str(jfc2))
FakeDecisions.replies.append((200, {
    "answers": {"neues_thema": {"type": "noul", "noul": "hoch"}},
    "usage": {}}))
jfn = jdet.score([blick(0, "Block eins", 1000.0)],
                 blick(1, "Block zwei", 1040.0))
check("test_an_answer_without_a_numeric_noul_score_error_no_noul_rather_than_a_0_0", "an answer without a numeric noul -> Score.error 'no_noul' rather "
      "than a 0.0 that would read as 'no boundary'",
      jfn.error == "no_noul", str(jfn))


class KaputterKlient:
    model = "x"

    def decide(self, state, questions):
        raise RuntimeError(f"kaputt wegen {KANARI}")


jfk = td.JevDetector(KaputterKlient()).score(
    [blick(0, "Block eins", 1000.0)], blick(1, "Block zwei", 1040.0))
check("test_any_other_exception_becomes_its_type_name_never_its_message", "any other exception becomes its TYPE NAME, never its message",
      jfk.error == "RuntimeError" and KANARI not in repr(jfk), repr(jfk))
jdet.close()
jdet.close()
check("test_close_is_safe_to_call_twice_and_name_and_model_are_reported", "close() is safe to call twice, and name and model are reported",
      (jdet.name, jdet.model) == ("jev", "typesafe/jev-1.13"),
      str((jdet.name, jdet.model)))
check("test_the_pinned_constants_are_the_module_s", "the pinned constants are the module's",
      (td.JEV_VORHER_CHARS, td.JEV_NEU_CHARS, td.JEV_VORHER_BLOCKS)
      == (1200, 600, 2),
      str((td.JEV_VORHER_CHARS, td.JEV_NEU_CHARS, td.JEV_VORHER_BLOCKS)))
check("test_the_other_scores_are_unchanged_cost_usd_defaults_to_none_so_drift_and", "the other Scores are unchanged: cost_usd defaults to None, so "
      "drift and laya do not suddenly report a USD cost of 0",
      td.Score(index=0).cost_usd is None)
quelle = (REPO / "blocks" / "topics" / "detect.py").read_text(encoding="utf-8")
import inspect  # noqa: E402

JEV_SITES = [
    # (what, source, snippet the marker must stand before)
    ("detector: noul -> p", inspect.getsource(td.JevDetector.score),
     'antworten.get(JEV_NOUL_ID) or {}).get("noul")'),
    ("detector: missing noul is a gap", inspect.getsource(td.JevDetector.score),
     'error="no_noul"'),
    ("detector: clamp", inspect.getsource(td.JevDetector.score),
     "p=min(1.0, max(0.0, float(roh)))"),
    ("detector: choice among our criteria",
     inspect.getsource(td.JevDetector._keep_choice),
     'if wahl.get("choice") in JEV_CHOICE_CRITERIA'),
    ("client: non-object answer", inspect.getsource(boj.clean_answer),
     "if not isinstance(antwort, dict)"),
    ("client: numeric fields", inspect.getsource(boj.clean_answer),
     "for name in _NUMBER_FIELDS"),
    ("client: choice token", inspect.getsource(boj.clean_answer),
     'wahl = _token(antwort.get("choice"))'),
    ("client: probabilities", inspect.getsource(boj.clean_answer),
     "if isinstance(wahrsch, dict)"),
    ("client: missing_answer", inspect.getsource(boj.Client.decide),
     "if fehlend and require_all:"),
    ("client: only our ids", inspect.getsource(boj.Client.decide),
     "sauber = {k: clean_answer(antworten[k]) for k in questions"),
    ("client: usage", inspect.getsource(boj.Client.decide),
     'nutzung = obj.get("usage")'),
]
for was, src, stelle in JEV_SITES:
    vorher_code = src[:src.find(stelle)] if stelle in src else ""
    # The marker must be within the few lines just before the site.
    nah = "\n".join(vorher_code.splitlines()[-5:])
    check(f"test_jev_site_marker_{_slug(was)}", f"JEV-SITE marker at: {was}",
          stelle in src and "# JEV-SITE:" in nah, nah[-200:])
quelle_jev = (REPO / "gateway" / "openrouter_jev.py").read_text(
    encoding="utf-8")
check("test_the_marker_counts_are_pinned_4_in_topic_detect_py_7_in_openrouter_jev", "the marker counts are pinned: 4 in topic_detect.py, 7 in "
      "openrouter_jev.py -- a new interpretation site without a marker, "
      "or a marker lost in an edit, changes the count",
      (quelle.count("# JEV-SITE:"), quelle_jev.count("# JEV-SITE:"))
      == (4, 7),
      str((quelle.count("# JEV-SITE:"), quelle_jev.count("# JEV-SITE:"))))


def test_cosine_of_a_vector_with_itself_is_1():
    _verify("test_cosine_of_a_vector_with_itself_is_1")


def test_cosine_of_two_orthogonal_vectors_is_0():
    _verify("test_cosine_of_two_orthogonal_vectors_is_0")


def test_cosine_of_opposite_vectors_is_1_the_drift_score_is_therefore_clamped_by():
    _verify("test_cosine_of_opposite_vectors_is_1_the_drift_score_is_therefore_clamped_by")


def test_a_zero_vector_gives_0_0_instead_of_a_division_by_zero():
    _verify("test_a_zero_vector_gives_0_0_instead_of_a_division_by_zero")


def test_vectors_of_different_length_are_refused_with_both_lengths():
    _verify("test_vectors_of_different_length_are_refused_with_both_lengths")


def test_mean_vector_averages_component_wise():
    _verify("test_mean_vector_averages_component_wise")


def test_mean_vector_of_an_empty_list_is_empty_not_an_exception():
    _verify("test_mean_vector_of_an_empty_list_is_empty_not_an_exception")


def test_one_view_per_cut():
    _verify("test_one_view_per_cut")


def test_index_is_0_based_and_dense():
    _verify("test_index_is_0_based_and_dense")


def test_start_is_the_at_of_the_block_s_first_utterance_convention_ii_where_the():
    _verify("test_start_is_the_at_of_the_block_s_first_utterance_convention_ii_where_the")


def test_end_is_the_at_of_the_block_s_last_utterance_convention_i_the_newest():
    _verify("test_end_is_the_at_of_the_block_s_last_utterance_convention_i_the_newest")


def test_at_is_the_cut_time_and_t_dec_is_measured_from_it_not_from_end():
    _verify("test_at_is_the_cut_time_and_t_dec_is_measured_from_it_not_from_end")


def test_the_text_is_the_block_s_utterances_joined_with_one_space():
    _verify("test_the_text_is_the_block_s_utterances_joined_with_one_space")


def test_chars_equals_the_cut_s_own_char_count_the_same_join_gateway_blocks_py():
    _verify("test_chars_equals_the_cut_s_own_char_count_the_same_join_gateway_blocks_py")


def test_only_blocks_whose_end_is_at_most_180_s_before_this_block_s_start_stay():
    _verify("test_only_blocks_whose_end_is_at_most_180_s_before_this_block_s_start_stay")


def test_at_most_six_blocks_and_the_newest_six():
    _verify("test_at_most_six_blocks_and_the_newest_six")


def test_an_empty_history_gives_an_empty_window():
    _verify("test_an_empty_history_gives_an_empty_window")


def test_a_block_after_a_long_silence_has_an_empty_window_no_decision_is():
    _verify("test_a_block_after_a_long_silence_has_an_empty_window_no_decision_is")


def test_only_a_block_whose_score_reaches_the_threshold_becomes_a_boundary():
    _verify("test_only_a_block_whose_score_reaches_the_threshold_becomes_a_boundary")


def test_the_boundary_carries_the_evidence_position_the_claim_position_and_t_dec():
    _verify("test_the_boundary_carries_the_evidence_position_the_claim_position_and_t_dec")


def test_a_score_exactly_at_the_threshold_is_a_boundary_the_rule_is_p_threshold():
    _verify("test_a_score_exactly_at_the_threshold_is_a_boundary_the_rule_is_p_threshold")


def test_a_block_carrying_an_error_token_never_becomes_a_boundary_whatever_its_p():
    _verify("test_a_block_carrying_an_error_token_never_becomes_a_boundary_whatever_its_p")


def test_the_detector_base_class_is_a_contract_not_a_detector_score_raises():
    _verify("test_the_detector_base_class_is_a_contract_not_a_detector_score_raises")


def test_the_request_goes_to_2_ai_product_openai_v1_embeddings():
    _verify("test_the_request_goes_to_2_ai_product_openai_v1_embeddings")


def test_the_key_travels_as_a_bearer_token_in_the_authorization_header():
    _verify("test_the_key_travels_as_a_bearer_token_in_the_authorization_header")


def test_the_body_carries_exactly_model_and_a_one_element_input_list():
    _verify("test_the_body_carries_exactly_model_and_a_one_element_input_list")


def test_the_vector_comes_back_with_its_dimension():
    _verify("test_the_vector_comes_back_with_its_dimension")


def test_tokens_come_from_usage_prompt_tokens_that_is_what_is_billed():
    _verify("test_tokens_come_from_usage_prompt_tokens_that_is_what_is_billed")


def test_latency_is_measured_in_milliseconds():
    _verify("test_latency_is_measured_in_milliseconds")


def test_the_call_counter_went_up():
    _verify("test_the_call_counter_went_up")


def test_the_model_the_server_answered_with_is_reported_not_the_one_asked_for_a():
    _verify("test_the_model_the_server_answered_with_is_reported_not_the_one_asked_for_a")


def test_an_http_500_raises_embederror_naming_the_status_code():
    _verify("test_an_http_500_raises_embederror_naming_the_status_code")


def test_and_the_message_carries_no_part_of_the_response_body_a_4xx_body_can():
    _verify("test_and_the_message_carries_no_part_of_the_response_body_a_4xx_body_can")


def test_a_non_json_answer_names_the_byte_count_only():
    _verify("test_a_non_json_answer_names_the_byte_count_only")


def test_an_answer_without_a_usable_data_array_is_refused():
    _verify("test_an_answer_without_a_usable_data_array_is_refused")


def test_a_non_numeric_vector_is_refused_with_the_entry_count_and_no_entry():
    _verify("test_a_non_numeric_vector_is_refused_with_the_entry_count_and_no_entry")


def test_repr_of_the_client_does_not_contain_the_key_a_repr_lands_in_tracebacks():
    _verify("test_repr_of_the_client_does_not_contain_the_key_a_repr_lands_in_tracebacks")


def test_an_empty_key_is_refused_at_construction_time_naming_the_environment():
    _verify("test_an_empty_key_is_refused_at_construction_time_naming_the_environment")


def test_the_first_line_is_the_ready_line_and_carries_the_load_time():
    _verify("test_the_first_line_is_the_ready_line_and_carries_the_load_time")


def test_one_answer_per_request_line_in_order_each_with_the_request_s_own_id():
    _verify("test_one_answer_per_request_line_in_order_each_with_the_request_s_own_id")


def test_p_is_answers_question_id_question_type_of_the_prediction():
    _verify("test_p_is_answers_question_id_question_type_of_the_prediction")


def test_ms_is_measured_not_invented():
    _verify("test_ms_is_measured_not_invented")


def test_the_state_handed_to_predict_is_exactly_vorher_and_neu():
    _verify("test_the_state_handed_to_predict_is_exactly_vorher_and_neu")


def test_the_question_is_the_pinned_one_the_noul_type_and_the_german_instruction():
    _verify("test_the_question_is_the_pinned_one_the_noul_type_and_the_german_instruction")


def test_a_predict_that_raises_answers_with_an_error_and_the_loop_goes_on_one():
    _verify("test_a_predict_that_raises_answers_with_an_error_and_the_loop_goes_on_one")


def test_the_error_is_the_exception_type_never_its_message_a_model_library_s():
    _verify("test_the_error_is_the_exception_type_never_its_message_a_model_library_s")


def test_a_line_that_is_not_a_usable_request_is_answered_with_id_1_and_a_token():
    _verify("test_a_line_that_is_not_a_usable_request_is_answered_with_id_1_and_a_token")


def test_importing_this_module_imports_neither_torch_nor_laya_the_protocol_is():
    _verify("test_importing_this_module_imports_neither_torch_nor_laya_the_protocol_is")


def test_the_first_block_scores_0_0_an_empty_window_allows_no_decision_and_a():
    _verify("test_the_first_block_scores_0_0_an_empty_window_allows_no_decision_and_a")


def test_but_the_block_is_embedded_all_the_same_in_live_operation_the_embedding():
    _verify("test_but_the_block_is_embedded_all_the_same_in_live_operation_the_embedding")


def test_the_score_is_1_cos_against_the_mean_of_the_window_to_the_digit():
    _verify("test_the_score_is_1_cos_against_the_mean_of_the_window_to_the_digit")


def test_tokens_and_ms_report_the_current_block_only_in_live_operation_the():
    _verify("test_tokens_and_ms_report_the_current_block_only_in_live_operation_the")


def test_a_block_on_the_same_topic_scores_lower_than_one_on_a_new_topic():
    _verify("test_a_block_on_the_same_topic_scores_lower_than_one_on_a_new_topic")


def test_the_score_is_clamped_into_0_1_an_opposite_vector_gives_2_before_the():
    _verify("test_the_score_is_clamped_into_0_1_an_opposite_vector_gives_2_before_the")


def test_name_and_model_are_reported_because_the_table_has_a_column_for_each():
    _verify("test_name_and_model_are_reported_because_the_table_has_a_column_for_each")


def test_a_block_after_a_long_silence_scores_0_0_the_window_is_empty_even_though():
    _verify("test_a_block_after_a_long_silence_scores_0_0_the_window_is_empty_even_though")


def test_a_failed_embedding_becomes_an_error_token_not_an_exception_one_bad_call():
    _verify("test_a_failed_embedding_becomes_an_error_token_not_an_exception_one_bad_call")


def test_the_first_block_scores_0_0_without_a_call_asking_the_model_against_an():
    _verify("test_the_first_block_scores_0_0_without_a_call_asking_the_model_against_an")


def test_the_detector_started_the_worker_and_read_its_ready_line():
    _verify("test_the_detector_started_the_worker_and_read_its_ready_line")


def test_the_state_is_the_earlier_blocks_text_as_vorher_and_this_block_s_as_neu():
    _verify("test_the_state_is_the_earlier_blocks_text_as_vorher_and_this_block_s_as_neu")


def test_p_and_ms_come_from_the_worker_clamped_into_0_1():
    _verify("test_p_and_ms_come_from_the_worker_clamped_into_0_1")


def test_the_request_ids_count_up_and_the_answer_is_matched_by_id():
    _verify("test_the_request_ids_count_up_and_the_answer_is_matched_by_id")


def test_vorher_is_cut_to_the_last_1200_characters_and_neu_to_the_first_600_the():
    _verify("test_vorher_is_cut_to_the_last_1200_characters_and_neu_to_the_first_600_the")


def test_a_worker_error_line_becomes_an_error_token_and_p_stays_0_0():
    _verify("test_a_worker_error_line_becomes_an_error_token_and_p_stays_0_0")


def test_a_request_that_outruns_the_timeout_yields_the_token_timeout():
    _verify("test_a_request_that_outruns_the_timeout_yields_the_token_timeout")


def test_the_child_is_gone_afterwards_and_every_later_decision_answers_with_the():
    _verify("test_the_child_is_gone_afterwards_and_every_later_decision_answers_with_the")


def test_a_worker_that_floods_stderr_does_not_deadlock_the_parent_stderr_goes_to():
    _verify("test_a_worker_that_floods_stderr_does_not_deadlock_the_parent_stderr_goes_to")


def test_close_ends_the_child_process():
    _verify("test_close_ends_the_child_process")


def test_the_three_pinned_constants_are_the_module_s_so_a_later_live_switch():
    _verify("test_the_three_pinned_constants_are_the_module_s_so_a_later_live_switch")


def test_laya_input_joins_the_history_with_one_space_and_keeps_the_last_1200():
    _verify("test_laya_input_joins_the_history_with_one_space_and_keeps_the_last_1200")


def test_an_empty_history_gives_an_empty_vorher_the_case_the_detector_answers():
    _verify("test_an_empty_history_gives_an_empty_vorher_the_case_the_detector_answers")


def test_the_cut_lengths_are_parameters_defaulting_to_the_module_constants():
    _verify("test_the_cut_lengths_are_parameters_defaulting_to_the_module_constants")


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_one_decision_per_proposal():
    """one decision per proposal"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_first_boundary_is_accepted_nothing_is_up_yet_so_the_minimum_hold():
    """the first boundary is accepted -- nothing is up yet, so the minimum hold time has nothing to protect"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_second_is_refused_with_the_brake_s_own_reason_30_s_after_a_surface():
    """the second is refused with the brake's OWN reason, 30 s after a surface that has to stand 45"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_third_passes_the_hold_time_at_50_s_but_hits_the_one_per_window_cap():
    """the third passes the hold time at 50 s but hits the one-per-window cap"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_fourth_is_accepted_again_once_the_60_s_window_has_moved_past():
    """the fourth is accepted again once the 60 s window has moved past"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_source_says_which_detector_produced_it_and_it_is_not_recorded():
    """source says which detector produced it, and it is not `recorded`"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_proposal_no_is_1_based_and_counts_per_session():
    """proposal_no is 1-based and counts per session"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_line_no_carries_the_block_index_the_boundary_came_from_the_only_route():
    """line_no carries the BLOCK index the boundary came from -- the only route back from a decision to its block"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_t_ev_is_passed_through_untouched_and_t_dec_is_the_clock_the_brake():
    """t_ev is passed through untouched and t_dec is the clock the brake actually ran on"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_an_unsorted_list_gives_the_same_answer_the_proposals_are_put_in_t_dec():
    """an unsorted list gives the same answer -- the proposals are put in t_dec order first, because a brake that sees time run backwards measures nothing"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_lowering_the_brake_s_constants_at_runtime_changes_the_answer_no():
    """lowering the brake's constants at runtime changes the answer: NO constant of gateway/stage_state.py is copied into bench/, so the bench measures the brake and not its own copy of it"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_candidates_are_the_distinct_scores_plus_0_0_and_1_0_ascending_any():
    """candidates are the distinct scores plus 0.0 and 1.0, ascending -- any threshold between two scores decides exactly like the lower of them, so nothing else can win"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_positions_takes_the_evidence_position_by_default_and_the_claim_position():
    """positions() takes the evidence position by default and the claim position on request"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_f1_at_threshold_0_8_on_a_is_exactly_1_0_100_matches_100_205_matches_200():
    """f1 at threshold 0.8 on A is exactly 1.0: 100 matches 100, 205 matches 200 within 30 s, nothing left over"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_f1_at_threshold_0_0_on_a_is_0_8_the_third_prediction_has_no_reference():
    """f1 at threshold 0.0 on A is 0.8: the third prediction has no reference and counts as a false positive (p 2/3, r 1)"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_pick_threshold_maximises_the_sum_of_f1_over_the_records_it_is_given_on():
    """pick_threshold maximises the SUM of F1 over the records it is given -- on B alone that is 0.85"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_a_tie_picks_the_higher_threshold_with_no_reference_at_all_every():
    """a tie picks the HIGHER threshold: with no reference at all every threshold scores 0.0, and the rule has to be stated rather than left to dict order"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_held_out_session_gets_the_other_session_s_threshold_a_is_measured():
    """the held-out session gets the OTHER session's threshold: A is measured at 0.85, which is B's optimum, not at its own 0.8"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_and_its_held_out_f1_is_therefore_lower_than_its_oracle_that_gap_is_the():
    """...and its held-out F1 is therefore lower than its oracle -- that gap is the price of not tuning on the session you report"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_oracle_is_reported_per_session_with_its_own_threshold_labelled_as():
    """the oracle is reported per session WITH its own threshold, labelled as the optimistic upper bound it is"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_convention_ii_is_reported_separately_and_differs_here_the_claim():
    """convention (ii) is reported separately and differs here: the claim positions sit 40 s before the evidence positions and fall out of the 30 s window"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_a_session_with_no_scored_block_gives_threshold_1_0_and_f1_0_0_instead():
    """a session with no scored block gives threshold 1.0 and F1 0.0 instead of an exception -- a detector that answered nothing has precision 0, not 1"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_detector_key_maps_the_names_and_the_two_embedding_models_to_the_four():
    """detector_key maps the names and the two embedding models to the four keys the table has rows for"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_an_unknown_embedding_model_is_refused_naming_the_known_ones_a_typo_must():
    """an unknown embedding model is refused, naming the known ones -- a typo must not silently produce a fifth table row"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_model_slug_turns_a_vendor_prefixed_name_into_a_file_name_without_a():
    """model_slug turns a vendor-prefixed name into a file name without a slash in it"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_scores_the_metrics_and_the_embedding_cache_all_live_in_the_session():
    """the scores, the metrics and the embedding cache all live in the session's OWN bench folder in the vault"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_client_is_called_once_per_distinct_text():
    """the client is called once per DISTINCT text"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_a_cache_hit_returns_the_original_call_s_tokens_and_latency_the_cost():
    """a cache hit returns the ORIGINAL call's tokens and latency -- the cost column describes the detector, not how warm this particular re-run's cache was; `hits` says which"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_cache_survives_a_flush_and_a_fresh_embedder_on_the_same_file_so_a():
    """the cache survives a flush and a fresh embedder on the same file, so a repeated run costs nothing"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_cache_file_holds_sha256_keys_and_numbers_no_text_it_sits_in_the():
    """the cache file holds sha256 keys and numbers -- NO text: it sits in the vault, but the rule is that nothing stores speech it does not have to"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_run_detector_hands_over_the_earlier_views_only_never_the_current_one():
    """run_detector hands over the EARLIER views only, never the current one and never a later one -- offline peeking into the future cannot be reproduced live and would be a silent cheat"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_guard_cpu_does_not_trip_on_tests_test_stage_py_the_check_is_on_the_file():
    """guard_cpu does NOT trip on tests/test_stage.py: the check is on the FILE NAME, and `endswith('stage.py')` would match it"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_and_it_passes_on_a_process_list_without_either():
    """and it passes on a process list without either"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_views_of_replays_the_blocks_and_returns_dense_views_whose_chars_are_the():
    """views_of replays the blocks and returns dense views whose chars are the replay's own"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_worker_decisions_reproduces_the_recorded_baseline_brake_verdicts():
    """worker_decisions reproduces the recorded baseline, brake verdicts included: four topic proposals, two accepted, one too soon, one too many"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_baseline_carries_p_1_0_and_no_claim_position_the_recorded_worker():
    """the baseline carries p 1.0 and NO claim position -- the recorded worker does not say where the topic begins, and inventing one would give it a column it never earned"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_cost_record_reports_the_worker_in_run_seconds():
    """cost_record reports the worker in run seconds"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_laya_in_cpu_milliseconds_which_is_the_only_thing_it_spends():
    """...laya in CPU milliseconds, which is the only thing it spends"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_and_drift_in_tokens_plus_the_chf_they_cost_at_the_list_price_6000():
    """...and drift in tokens plus the CHF they cost at the list price (6000 tokens x 0.065 CHF / 1 M = 0.00039)"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_write_scores_writes_into_the_session_s_bench_folder_and_load_scores():
    """write_scores writes into the session's bench folder and load_scores reads exactly that back"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_it_counts_the_failed_decisions_and_lists_their_tokens_so_an_incomplete():
    """it counts the failed decisions and lists their tokens, so an incomplete measurement cannot pass as a complete one"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_latency_quantiles_are_computed_over_the_successful_decisions_only():
    """the latency quantiles are computed over the SUCCESSFUL decisions only"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_and_the_file_carries_no_text_at_all():
    """and the file carries no text at all"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_a_corrupt_scores_file_becomes_a_sessionerror_naming_the_byte_count_and():
    """a corrupt scores file becomes a SessionError naming the byte count and the position -- json's own message quotes the broken input, and that input is speech-derived"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_detector_worker_exits_0_and_writes_topic_worker_json_with_the_recorded():
    """--detector worker exits 0 and writes topic-worker.json with the recorded proposals"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_it_does_not_write_metrics_json_the_baseline_of_sub_project_b_is_not():
    """it does NOT write metrics.json: the baseline of sub-project B is not ours to overwrite, and a re-run must not move it"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_and_it_asks_no_model_at_all_no_reference_cache_appears():
    """...and it asks no model at all -- no reference cache appears"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_data_rule_canary_no_transcript_word_on_stdout_or_stderr():
    """THE DATA-RULE CANARY: no transcript word on stdout or stderr"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_detector_drift_exits_0_writes_topic_drift_bge_json_and_embeds_each_of():
    """--detector drift exits 0, writes topic-drift-bge.json and embeds each of the three blocks exactly once"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_a_second_run_costs_nothing_the_vault_cache_answers_every_block_and_the():
    """a second run costs nothing: the vault cache answers every block, and the scores file says so"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_detector_laya_exits_0_writes_topic_laya_json_and_records_the_model_s():
    """--detector laya exits 0, writes topic-laya.json and records the model's load time"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_scores_file_names_the_weights_revision_a_measurement_that_cannot():
    """the scores file names the weights' revision -- a measurement that cannot name its weights is not reproducible"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_a_run_whose_decisions_failed_exits_1_and_says_how_many_of_how_many_a():
    """a run whose decisions failed exits 1 and says how many of how many -- a partial measurement must not pass as a measurement"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_an_unknown_detector_is_refused_and_the_three_allowed_names_are_in_the():
    """an unknown detector is refused, and the three allowed names are in the message"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_without_a_key_the_drift_run_fails_with_exit_1_and_names_the_environment():
    """without a key the drift run fails with exit 1 and names the environment variable -- the key is never a CLI argument, because an argument stands in every process list on this machine"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_old_path_is_untouched_without_detector_the_run_still_writes_metrics():
    """the OLD path is untouched: without --detector the run still writes metrics.json and no topic-*.json"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_a_candidate_that_wins_two_sessions_and_loses_one_is_not_recommended_the():
    """a candidate that wins two sessions and loses one is NOT recommended -- the rule is all three, so a single lucky session cannot carry it"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_a_candidate_that_is_the_baseline_in_every_session_and_higher_on_the():
    """a candidate that is >= the baseline in every session and higher on the mean is recommended, with both means in the sentence"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_with_two_qualifying_candidates_the_higher_mean_wins():
    """with two qualifying candidates the higher mean wins"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_sentence_is_one_sentence_no_newline_and_nothing_in_it_but_numbers():
    """the sentence is ONE sentence: no newline, and nothing in it but numbers and detector keys"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_it_names_the_gap_to_the_reference_ceiling_when_one_is_known_a_candidate():
    """it names the gap to the reference ceiling when one is known -- a candidate at the ceiling and one far below it are different results even at the same F1"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_and_it_says_plainly_that_nobody_wins_when_nobody_does():
    """and it says plainly that nobody wins when nobody does"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_compare_exits_0_and_prints_the_headline_table():
    """--compare exits 0 and prints the headline table"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_plus_the_second_table_at_15_and_60_s_and_the_third_with_the_oracle():
    """...plus the second table at 15 and 60 s and the third with the oracle beside the held-out threshold"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_one_row_per_detector_and_session_baseline_rows_first():
    """one row per detector and session, baseline rows first"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_metrics_key_json_is_written_per_session_and_detector():
    """metrics-<key>.json is written per session and detector"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_baseline_metrics_json_of_sub_project_b_is_not_touched():
    """the baseline metrics.json of sub-project B is NOT touched"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_data_rule_canary_the_whole_comparison_carries_no_transcript_word():
    """THE DATA-RULE CANARY: the whole comparison carries no transcript word, although the reference it reads is full of them"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_recommendation_sentence_is_printed_with_the_tables():
    """the recommendation sentence is printed with the tables"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_worker_row_s_convention_ii_column_reads_n_a_the_recorded_worker():
    """the worker row's convention (ii) column reads n/a -- the recorded worker names no claim position, and a 0.000 there would read as a measured zero"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_threshold_overrides_the_loso_choice_for_a_sensitivity_check():
    """--threshold overrides the LOSO choice, for a sensitivity check"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_a_session_without_reference_a_json_fails_with_exit_1_and_names_the():
    """a session without reference-A.json fails with exit 1 and names the missing file"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_an_unknown_detector_key_fails_with_exit_1_and_lists_the_four_known_ones():
    """an unknown detector key fails with exit 1 and lists the four known ones"""


def test_the_request_goes_to_api_alpha_decisions():
    _verify("test_the_request_goes_to_api_alpha_decisions")


def test_the_key_travels_as_a_bearer_token_in_the_authorization_header_2():
    _verify("test_the_key_travels_as_a_bearer_token_in_the_authorization_header_2")


def test_the_body_is_exactly_model_state_and_questions_and_the_model_defaults_to():
    _verify("test_the_body_is_exactly_model_state_and_questions_and_the_model_defaults_to")


def test_the_noul_answer_is_read_from_answers_id_noul_the_field_is_called_noul():
    _verify("test_the_noul_answer_is_read_from_answers_id_noul_the_field_is_called_noul")


def test_tokens_are_input_output_and_cost_is_usage_cost_in_usd():
    _verify("test_tokens_are_input_output_and_cost_is_usage_cost_in_usd")


def test_the_model_the_server_answered_with_is_reported_a_dated_snapshot_or_a():
    _verify("test_the_model_the_server_answered_with_is_reported_a_dated_snapshot_or_a")


def test_latency_is_measured_and_the_call_counter_went_up():
    _verify("test_latency_is_measured_and_the_call_counter_went_up")


def test_an_answer_is_reduced_to_numbers_and_key_like_tokens_an_unknown_free():
    _verify("test_an_answer_is_reduced_to_numbers_and_key_like_tokens_an_unknown_free")


def test_missing_usage_gives_zero_tokens_and_zero_cost_not_an_exception():
    _verify("test_missing_usage_gives_zero_tokens_and_zero_cost_not_an_exception")


def test_an_error_body_that_is_not_json_still_gives_the_status_token_and_no_code():
    _verify("test_an_error_body_that_is_not_json_still_gives_the_status_token_and_no_code")


def test_a_non_json_200_answer_names_the_byte_count_only_token_bad_json():
    _verify("test_a_non_json_200_answer_names_the_byte_count_only_token_bad_json")


def test_an_answer_that_leaves_a_question_unanswered_is_an_error_missing_answer():
    _verify("test_an_answer_that_leaves_a_question_unanswered_is_an_error_missing_answer")


def test_a_truncated_body_http_client_incompleteread_an_httpexception_and_not_an():
    _verify("test_a_truncated_body_http_client_incompleteread_an_httpexception_and_not_an")


def test_a_request_that_outruns_the_timeout_raises_jeverror_with_the_token():
    _verify("test_a_request_that_outruns_the_timeout_raises_jeverror_with_the_token")


def test_a_refused_connection_raises_jeverror_carrying_the_exception_type_name():
    _verify("test_a_refused_connection_raises_jeverror_carrying_the_exception_type_name")


def test_repr_of_the_client_does_not_contain_the_key():
    _verify("test_repr_of_the_client_does_not_contain_the_key")


def test_an_empty_key_is_refused_at_construction_time_naming_the_environment_2():
    _verify("test_an_empty_key_is_refused_at_construction_time_naming_the_environment_2")


def test_the_first_block_scores_0_0_without_a_call_asking_against_an_empty():
    _verify("test_the_first_block_scores_0_0_without_a_call_asking_against_an_empty")


def test_p_is_answers_neues_thema_noul_of_the_answer():
    _verify("test_p_is_answers_neues_thema_noul_of_the_answer")


def test_tokens_are_usage_input_tokens_usage_output_tokens():
    _verify("test_tokens_are_usage_input_tokens_usage_output_tokens")


def test_the_usd_cost_of_the_call_rides_on_the_score_for_cost_record():
    _verify("test_the_usd_cost_of_the_call_rides_on_the_score_for_cost_record")


def test_ms_is_the_measured_duration_of_the_call():
    _verify("test_ms_is_the_measured_duration_of_the_call")


def test_the_state_is_exactly_vorher_and_neu():
    _verify("test_the_state_is_exactly_vorher_and_neu")


def test_without_with_choice_exactly_one_question_is_asked_the_pinned_noul():
    _verify("test_without_with_choice_exactly_one_question_is_asked_the_pinned_noul")


def test_vorher_is_the_last_two_earlier_blocks_not_the_whole_history_the():
    _verify("test_vorher_is_the_last_two_earlier_blocks_not_the_whole_history_the")


def test_vorher_is_cut_to_its_last_1200_characters_and_neu_to_its_first_600():
    _verify("test_vorher_is_cut_to_its_last_1200_characters_and_neu_to_its_first_600")


def test_and_a_long_history_really_is_cut_at_1200():
    _verify("test_and_a_long_history_really_is_cut_at_1200")


def test_with_choice_asks_both_questions_in_one_request_a_second_call_would():
    _verify("test_with_choice_asks_both_questions_in_one_request_a_second_call_would")


def test_the_choice_question_is_a_choice_with_the_four_german_criteria():
    _verify("test_the_choice_question_is_a_choice_with_the_four_german_criteria")


def test_p_still_comes_from_the_noul_answer_of_the_same_call():
    _verify("test_p_still_comes_from_the_noul_answer_of_the_same_call")


def test_the_choice_answer_is_kept_per_block_index_on_the_detector_as_the_chosen():
    _verify("test_the_choice_answer_is_kept_per_block_index_on_the_detector_as_the_chosen")


def test_a_choice_outside_the_criteria_we_sent_is_not_recorded_and_the_noul():
    _verify("test_a_choice_outside_the_criteria_we_sent_is_not_recorded_and_the_noul")


def test_a_timeout_score_error_timeout_no_exception():
    _verify("test_a_timeout_score_error_timeout_no_exception")


def test_and_the_next_decision_is_served_normally_http_holds_no_pipe_that_a_late():
    _verify("test_and_the_next_decision_is_served_normally_http_holds_no_pipe_that_a_late")


def test_a_refused_connection_score_error_carries_the_type_name():
    _verify("test_a_refused_connection_score_error_carries_the_type_name")


def test_an_answer_without_a_numeric_noul_score_error_no_noul_rather_than_a_0_0():
    _verify("test_an_answer_without_a_numeric_noul_score_error_no_noul_rather_than_a_0_0")


def test_any_other_exception_becomes_its_type_name_never_its_message():
    _verify("test_any_other_exception_becomes_its_type_name_never_its_message")


def test_close_is_safe_to_call_twice_and_name_and_model_are_reported():
    _verify("test_close_is_safe_to_call_twice_and_name_and_model_are_reported")


def test_the_pinned_constants_are_the_module_s():
    _verify("test_the_pinned_constants_are_the_module_s")


def test_the_other_scores_are_unchanged_cost_usd_defaults_to_none_so_drift_and():
    _verify("test_the_other_scores_are_unchanged_cost_usd_defaults_to_none_so_drift_and")


def test_the_marker_counts_are_pinned_4_in_topic_detect_py_7_in_openrouter_jev():
    _verify("test_the_marker_counts_are_pinned_4_in_topic_detect_py_7_in_openrouter_jev")


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_jev_is_a_detector_name_and_its_own_table_key():
    """jev is a detector name and its own table key"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_jev_cache_lives_in_the_session_s_own_bench_folder():
    """the jev cache lives in the session's own bench folder"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_question_hash_is_stable_and_differs_with_the_choice_question_added():
    """the question hash is stable, and differs with the choice question added -- a changed question must not be answered from the cache"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_and_differs_when_only_the_instruction_text_changes():
    """...and differs when only the instruction TEXT changes"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_same_state_and_questions_are_asked_once_the_second_decide_is():
    """the same state and questions are asked ONCE: the second decide() is answered from the cache without an HTTP request"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_a_hit_returns_the_original_call_s_answer_tokens_cost_and_latency_the():
    """a hit returns the ORIGINAL call's answer, tokens, cost and latency -- the cost column describes the detector, not the cache"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_a_different_state_is_a_miss():
    """a different state is a miss"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_a_failed_call_is_not_cached_the_same_request_is_sent_again_next_time():
    """a FAILED call is not cached: the same request is sent again next time instead of replaying the failure"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_cache_survives_a_flush_and_a_fresh_decider_on_the_same_file_a():
    """the cache survives a flush and a fresh decider on the same file: a second run over the session makes no second network call"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_cache_file_holds_hashes_and_numbers_no_text_of_the_state():
    """the cache file holds hashes and numbers -- NO text of the state"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_another_model_is_a_miss_the_key_includes_the_model_asked_for():
    """another model is a miss -- the key includes the model asked for"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_a_jev_row_carries_cost_usd_the_no_call_first_block_does_not():
    """a jev row carries cost_usd; the no-call first block does not"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_rows_of_scores_without_cost_usd_are_unchanged_drift_and_laya_files_gain():
    """rows of Scores without cost_usd are unchanged -- drift and laya files gain no new key"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_cost_record_reports_jev_in_usd_median_per_paid_decision_and_the_total():
    """cost_record reports jev in USD: median per paid decision and the total, plus the tokens -- the error row and the no-call row are not decisions that cost anything"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_a_paid_call_that_still_failed_no_noul_counts_toward_usd_total_and():
    """a PAID call that still failed (no_noul) counts toward usd_total and tokens_total -- money spent is money spent -- but not toward the per-decision median, which describes a decision"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_and_the_other_units_are_exactly_what_they_were_no_usd_total_key_appears():
    """...and the other units are exactly what they were: no usd_total key appears for worker, laya or drift"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_attach_choices_puts_the_side_answer_next_to_its_decision_by_index():
    """attach_choices puts the side answer next to its decision, by index"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_detector_jev_exits_0_writes_topic_jev_json_with_one_decision_per_block():
    """--detector jev exits 0, writes topic-jev.json with one decision per block, and asks the endpoint for every block but the first"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_scores_file_names_the_model_and_as_its_revision_the_hash_of_the():
    """the scores file names the model and, as its revision, the hash of the questions asked -- a changed wording is a different measurement"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_its_cost_record_is_in_usd_summed_from_usage_cost():
    """its cost record is in USD, summed from usage.cost"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_printed_summary_shows_the_usd_total_next_to_the_unit():
    """the printed summary shows the USD total next to the unit"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_data_rule_canary_no_transcript_word_and_no_key_on_stdout_or_stderr():
    """THE DATA-RULE CANARY: no transcript word and no key on stdout or stderr"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_state_sent_is_built_from_the_blocks_the_canary_is_in_the_request():
    """the state sent is built from the blocks -- the canary IS in the request, which is exactly why it must not be anywhere else"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_vault_cache_file_holds_no_transcript_word():
    """the vault cache file holds no transcript word"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_a_second_run_makes_no_second_network_call_the_vault_cache_answers_every():
    """a second run makes NO second network call: the vault cache answers every block, and the scores file says so"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_jev_choice_writes_its_own_key_and_file_topic_jev_choice_json_and_leaves():
    """--jev-choice writes its OWN key and file, topic-jev-choice.json, and leaves the plain topic-jev.json untouched -- the two variants must both be comparable"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_jev_choice_asks_both_questions_in_one_request_per_block_two_new():
    """--jev-choice asks both questions in ONE request per block (two new requests for two blocks, not four), and the changed questions miss the cache instead of reusing the noul-only answers"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_and_each_decision_row_carries_its_choice_side_answer():
    """...and each decision row carries its choice side answer"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_without_a_key_the_jev_run_fails_with_exit_1_and_names_the_environment():
    """without a key the jev run fails with exit 1 and names the environment variable -- never a CLI flag"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_a_run_whose_calls_come_back_402_exits_1_says_how_many_of_how_many():
    """a run whose calls come back 402 exits 1, says how many of how many failed, and names the token http_402"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_and_the_api_s_error_message_is_nowhere_in_the_output():
    """...and the API's error message is nowhere in the output"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_render_detector_prints_the_usd_total_for_jev():
    """render_detector prints the USD total for jev"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_and_prints_no_usd_total_for_a_detector_that_has_none():
    """...and prints NO usd_total for a detector that has none"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_compare_cell_names_the_unit_and_the_usd_total_with_enough_decimals():
    """the compare cell names the unit and the USD total, with enough decimals that a per-call cost of a few thousandths of a cent is not printed as 0.000"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_and_the_chf_cell_is_exactly_what_it_was():
    """...and the CHF cell is exactly what it was"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_compare_with_jev_choice_before_d8b_has_that_run_fails_with_exit_1_and():
    """--compare with jev-choice before d8b has that run fails with exit 1 and names the flags to run"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_compare_accepts_both_jev_keys_side_by_side_and_prints_their_rows_with_a():
    """--compare accepts both jev keys side by side and prints their rows with a USD cell"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_and_writes_metrics_jev_choice_json_next_to_metrics_jev_json():
    """...and writes metrics-jev-choice.json next to metrics-jev.json"""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_the_data_rule_canary_for_the_jev_comparison():
    """THE DATA-RULE CANARY for the jev comparison"""



# ---- checks that sit inside loops: one test per iteration
@pytest.mark.parametrize("status", (400, 401, 402, 403, 404, 413))
def test_http_error_raises_jeverror_with_token_and_error_code(status):
    _verify(f"test_http_{status}_raises_jeverror_with_token_and_error_code")


@pytest.mark.parametrize("status", (400, 401, 402, 403, 404, 413))
def test_http_error_leaks_nothing_of_the_error_message(status):
    _verify(f"test_http_{status}_leaks_nothing_of_the_error_message")


@pytest.mark.parametrize("status", (400, 401, 402))
def test_http_failure_becomes_score_error_token(status):
    _verify(f"test_http_{status}_becomes_score_error_token")


@pytest.mark.parametrize(
    "name", sorted(k for k in RESULTS if k.startswith("test_jev_site_marker_")))
def test_jev_site_marker(name):
    _verify(name)


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_guard_cpu_refuses_while_consumer_py_runs():
    """guard_cpu refuses while consumer.py runs (bench/detect.py)."""


@pytest.mark.skip(reason=_NEEDS_BENCH)
def test_guard_cpu_refuses_while_stage_py_runs():
    """guard_cpu refuses while stage.py runs (bench/detect.py)."""
