# CoThinker — tests of the level-1 decision interface
# Copyright (C) 2026 Birk Schmithuesen
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU Affero General Public License, version 3.
# It is provided WITHOUT ANY WARRANTY. See LICENSE.
"""blocks/decisions_tasks_questions/decide.py: one call per block, all questions, numbers back.

Covered: the `State` window, the request builder (token estimate,
truncation, hard limit), the fake backend (deterministic, no network),
the laya stub, backend selection by `COTHINKER_DECIDER`, and the jev
backend against a LOCAL ThreadingHTTPServer -- never the real network.
The same canary net as tests/test_topic_detect.py: a marker word stands
in for speech inside an error body, and neither it nor the key may reach
an exception, a repr or stdout.

Fixture text is synthetic German, invented for this file.

    python3 -m pytest -q tests/test_decide.py
"""
from __future__ import annotations

import contextlib
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gateway"))

import pytest  # noqa: E402
from script_checks import check, collected, done  # noqa: E402
import blocks.decisions_tasks_questions.decide as d  # noqa: E402
import blocks.decisions_tasks_questions.questions as q  # noqa: E402

IDS = [f.id for f in q.QUESTIONS]
NOUL_IDS = [f.id for f in q.QUESTIONS if f.kind == "noul"]
SCHLUESSEL = "fake-or-4711"
KANARI = "KANARIENVOGEL"


def zustand(**kw):
    basis = dict(block="Wir sprechen jetzt ueber die Nistkaesten am Teich.",
                 previous=["Zuerst ging es um das Vogelfutter.",
                           "Dann um den Winterdienst im Park."],
                 heading="Nistkaesten", topics=["Vogelfutter", "Winterdienst"],
                 hints=["Wer bestellt die Kaesten?"], mode="sitzung")
    basis.update(kw)
    return d.State(**basis)


# ================================================ 1. constants and State
print("constants are the values the card pins")
check("TARGET_TOKENS is 3000", d.TARGET_TOKENS == 3000, str(d.TARGET_TOKENS))
check("MAX_TOKENS is 32000", d.MAX_TOKENS == 32000, str(d.MAX_TOKENS))
check("DEFAULT_CONTEXT_BLOCKS is 5", d.DEFAULT_CONTEXT_BLOCKS == 5,
      str(d.DEFAULT_CONTEXT_BLOCKS))

print("\nState keeps the last N blocks and the last 3 topic titles")
s = d.State()
for i in range(8):
    s.push(f"Block Nummer {i} ueber Nistkaesten.")
check("push makes the new text the current block",
      s.block == "Block Nummer 7 ueber Nistkaesten.", s.block)
check("previous keeps at most DEFAULT_CONTEXT_BLOCKS blocks, oldest dropped",
      s.previous == [f"Block Nummer {i} ueber Nistkaesten." for i in range(2, 7)],
      str(s.previous))
s2 = d.State(context_blocks=2)
for i in range(4):
    s2.push(f"B{i}")
check("N is configurable", s2.previous == ["B1", "B2"], str(s2.previous))
s3 = zustand(topics=["T1", "T2", "T3", "T4"])
check("only the last 3 topic titles reach the request",
      d.build_request(s3, q.QUESTIONS).state["letzte_themen"]
      == ["T2", "T3", "T4"],
      str(d.build_request(s3, q.QUESTIONS).state))

# ================================================ 2. the request builder
print("\nthe request carries all questions and the German state keys")
req = d.build_request(zustand(), q.QUESTIONS)
check("every question id is a key of the questions object, in set order",
      list(req.questions) == IDS, str(list(req.questions)))
check("noul questions go as type noul with the German text",
      all(req.questions[f.id] == {"type": "noul", "instructions": f.text}
          for f in q.QUESTIONS if f.kind == "noul"))
phase = req.questions["phase"]
check("the choice question carries one criterion per phase, in order",
      phase["type"] == "choice" and list(phase["criteria"]) == list(q.PHASES)
      and all(isinstance(v, str) and v for v in phase["criteria"].values()),
      str(phase))
check("state: neu is the current block, vorher the previous blocks",
      req.state["neu"] == "Wir sprechen jetzt ueber die Nistkaesten am Teich."
      and "Vogelfutter" in req.state["vorher"]
      and "Winterdienst" in req.state["vorher"], str(req.state))
check("state carries heading, hints and mode under German keys",
      req.state["ueberschrift"] == "Nistkaesten"
      and req.state["offene_fragen"] == ["Wer bestellt die Kaesten?"]
      and req.state["modus"] == "sitzung", str(req.state))
check("the estimate is a positive int well under the target",
      isinstance(req.tokens_est, int) and 0 < req.tokens_est < d.TARGET_TOKENS,
      str(req.tokens_est))
check("no truncation for a small state",
      (req.truncated, req.dropped_blocks, req.trimmed_chars) == (False, 0, 0))
check("the estimate is deterministic",
      d.build_request(zustand(), q.QUESTIONS).tokens_est == req.tokens_est)

print("\ntruncation: oldest previous blocks go first, then the block is trimmed")
lang = "Ein langer Satz ueber Nistkaesten und Vogelfutter im Park. " * 40
gross = zustand(previous=[f"{i}: {lang}" for i in range(5)])
r = d.build_request(gross, q.QUESTIONS)
check("the estimate is brought under the target", r.tokens_est <= d.TARGET_TOKENS,
      str(r.tokens_est))
check("blocks were dropped and it is recorded as numbers",
      r.truncated is True and r.dropped_blocks > 0 and r.trimmed_chars == 0,
      str((r.truncated, r.dropped_blocks, r.trimmed_chars)))
check("the OLDEST blocks were dropped -- the newest survives",
      "4: Ein langer" in r.state["vorher"]
      and "0: Ein langer" not in r.state["vorher"], r.state["vorher"][:60])
riesig = zustand(previous=["kurz"], block=lang * 5)
r2 = d.build_request(riesig, q.QUESTIONS)
check("a huge current block: previous dropped first, then the block trimmed",
      r2.dropped_blocks == 1 and r2.trimmed_chars > 0 and r2.truncated
      and r2.tokens_est <= d.TARGET_TOKENS, str((r2.dropped_blocks,
                                                 r2.trimmed_chars,
                                                 r2.tokens_est)))
check("the block keeps its HEAD (what the question is about)",
      (lang * 5).startswith(r2.state["neu"]) and len(r2.state["neu"]) > 0)
check("truncation is deterministic",
      d.build_request(riesig, q.QUESTIONS).state == r2.state)

print("\nthe hard limit: never send silently")
fehler = ""
try:
    d.build_request(zustand(hints=["Frage " * 40000]), q.QUESTIONS)
except d.DecideError as e:
    fehler = e.token
check("a minimal prompt over MAX_TOKENS raises DecideError(over_limit)",
      fehler == "over_limit", fehler)
fehler = ""
try:
    d.decide(zustand(hints=["Frage " * 40000]), backend=d.FakeBackend())
except d.DecideError as e:
    fehler = e.token
check("decide() raises too -- the backend is never asked", fehler == "over_limit")

# ================================================ 3. the fake backend
print("\nfake backend: deterministic, shaped like the real thing, no network")
fb = d.FakeBackend()
a = d.decide(zustand(), backend=fb)
b = d.decide(zustand(), backend=d.FakeBackend())
check("result names its backend", a.backend == "fake", a.backend)
check("one answer per question, in set order",
      [x["question_id"] for x in a.answers] == IDS,
      str([x["question_id"] for x in a.answers]))
check("same block, same answers",
      [{k: v for k, v in x.items() if k != "ms"} for x in a.answers]
      == [{k: v for k, v in x.items() if k != "ms"} for x in b.answers])
c = d.decide(zustand(block="Ganz etwas anderes: der Haushalt 2027."), backend=fb)
check("another block, other answers",
      [x.get("p") for x in c.answers] != [x.get("p") for x in a.answers])
noul_ok = all(set(x) == {"question_id", "p", "confidence", "ms"}
              and 0.0 <= x["p"] <= 1.0 and 0.0 <= x["confidence"] <= 1.0
              for x in a.answers if x["question_id"] in NOUL_IDS)
check("noul answers are exactly {question_id, p, confidence, ms}", noul_ok,
      str(a.answers[0]))
ph = a.answers[-1]
check("the choice answer is {question_id, choice, confidence, ms} with a "
      "known phase",
      set(ph) == {"question_id", "choice", "confidence", "ms"}
      and ph["choice"] in q.PHASES, str(ph))
check("no free text anywhere: every value is a number or one of our tokens",
      all(isinstance(v, (int, float)) or v in IDS or v in q.PHASES
          for x in a.answers for v in x.values()))
check("numbers: tokens_in is the estimate, no cost, no errors",
      a.tokens_in == a.tokens_est > 0 and a.tokens_out == 0
      and a.cost_usd == 0.0 and a.errors == 0 and a.ms >= 0.0,
      str((a.tokens_in, a.tokens_est, a.cost_usd, a.errors)))
check("the result carries the question-set hash (text-free)",
      a.questions_hash == q.QUESTIONS_HASH, a.questions_hash)

# ================================================ 4. laya stub, env choice
print("\nlaya is a stub pointing at its card")
meldung = ""
try:
    d.LayaBackend()
except NotImplementedError as e:
    meldung = str(e)
check("LayaBackend raises NotImplementedError naming its card",
      d.LAYA_CARD in meldung, meldung)

print("\nCOTHINKER_DECIDER picks the backend")
check("unset -> None (decider off)", d.backend_from_env({}) is None)
check("empty -> None", d.backend_from_env({"COTHINKER_DECIDER": ""}) is None)
check("fake -> FakeBackend",
      isinstance(d.backend_from_env({"COTHINKER_DECIDER": "fake"}),
                 d.FakeBackend))
check("case and blanks do not matter",
      isinstance(d.backend_from_env({"COTHINKER_DECIDER": " Fake "}),
                 d.FakeBackend))
jb = d.backend_from_env({"COTHINKER_DECIDER": "jev",
                         "OPENROUTER_API_KEY": SCHLUESSEL})
check("jev -> JevBackend with the key from the environment",
      isinstance(jb, d.JevBackend) and jb.name == "jev")
check("the backend's repr hides the key", SCHLUESSEL not in repr(jb), repr(jb))
laya_fehler = ""
try:
    d.backend_from_env({"COTHINKER_DECIDER": "laya"})
except NotImplementedError as e:
    laya_fehler = str(e)
check("laya -> NotImplementedError", d.LAYA_CARD in laya_fehler)
unbekannt = ""
try:
    d.backend_from_env({"COTHINKER_DECIDER": "gpt"})
except ValueError as e:
    unbekannt = str(e)
check("an unknown value is a clear error naming the allowed ones",
      all(w in unbekannt for w in ("COTHINKER_DECIDER", "jev", "laya", "fake")),
      unbekannt)
kein = ""
try:
    d.backend_from_env({"COTHINKER_DECIDER": "jev"})
except d.DecideError as e:
    kein = e.token
check("jev without OPENROUTER_API_KEY -> DecideError(no_key)", kein == "no_key",
      kein)
aus = ""
try:
    d.decide(zustand(), backend=None, env={})
except d.DecideError as e:
    aus = e.token
check("decide() with the decider off raises DecideError(decider_off)",
      aus == "decider_off", aus)
check("decide() picks the backend from env when none is given",
      d.decide(zustand(), env={"COTHINKER_DECIDER": "fake"}).backend == "fake")

# ================================================ 5. jev over local HTTP
print("\njev backend: ONE request for all questions, against a local server")


def jev_antworten(**ueber):
    antworten = {i: {"type": "noul", "noul": 0.1 * (n + 1) - 0.05,
                     "confidence": 0.8}
                 for n, i in enumerate(NOUL_IDS)}
    antworten["phase"] = {"type": "choice", "choice": "diskussion",
                          "confidence": 0.7,
                          "probabilities": {p: 0.25 for p in q.PHASES}}
    antworten.update(ueber)
    return antworten


def jev_body(antworten, cost=3.25e-05, ein=812, aus=120):
    return (200, {"id": "gen-dec-0002", "model": "typesafe/jev-1.13-20260917",
                  "answers": antworten,
                  "usage": {"cost": cost, "input_tokens": ein,
                            "output_tokens": aus}})


class FakeDecisions(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests: list = []
    replies: list = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        roh = self.rfile.read(n)
        FakeDecisions.requests.append(
            {"path": self.path, "auth": self.headers.get("Authorization"),
             "body": json.loads(roh.decode("utf-8"))})
        status, body = (FakeDecisions.replies.pop(0) if FakeDecisions.replies
                        else jev_body(jev_antworten()))
        daten = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(daten)))
        self.end_headers()
        self.wfile.write(daten)


srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeDecisions)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"

jev = d.JevBackend(SCHLUESSEL, base_url=BASE)
FakeDecisions.requests.clear()
res = d.decide(zustand(), backend=jev)
check("exactly one request for the whole block", len(FakeDecisions.requests) == 1,
      str(len(FakeDecisions.requests)))
anfrage = FakeDecisions.requests[0]
check("it goes to /api/alpha/decisions with the key as bearer token",
      anfrage["path"] == "/api/alpha/decisions"
      and anfrage["auth"] == f"Bearer {SCHLUESSEL}")
check("the one request carries all 11 question ids",
      list(anfrage["body"]["questions"]) == IDS and len(IDS) == 11,
      str(list(anfrage["body"]["questions"])))
check("the state sent is the built state (neu/vorher/...)",
      anfrage["body"]["state"] == d.build_request(zustand(), q.QUESTIONS).state)
check("p is read from the noul field",
      [round(x["p"], 4) for x in res.answers[:10]]
      == [round(0.1 * (n + 1) - 0.05, 4) for n in range(10)],
      str([x.get("p") for x in res.answers]))
check("confidence is read as a number",
      all(x["confidence"] == 0.8 for x in res.answers[:10]))
check("the choice is read", res.answers[10] == {
    "question_id": "phase", "choice": "diskussion", "confidence": 0.7,
    "ms": res.answers[10]["ms"]}, str(res.answers[10]))
check("cost and tokens come from usage",
      (res.cost_usd, res.tokens_in, res.tokens_out) == (3.25e-05, 812, 120),
      str((res.cost_usd, res.tokens_in, res.tokens_out)))
check("the pre-send estimate is kept next to the measured tokens",
      res.tokens_est == d.build_request(zustand(), q.QUESTIONS).tokens_est)
check("backend, model and timing are reported",
      res.backend == "jev" and res.model == "typesafe/jev-1.13-20260917"
      and res.ms > 0.0 and all(x["ms"] > 0.0 for x in res.answers),
      str((res.backend, res.model, res.ms)))
check("no errors on a clean answer", res.errors == 0, str(res.errors))

FakeDecisions.replies.append(jev_body(jev_antworten(), ein=0, aus=0))
ohne = d.decide(zustand(), backend=jev)
check("without usage tokens the estimate stands in for tokens_in",
      ohne.tokens_in == ohne.tokens_est > 0, str(ohne.tokens_in))

print("\nmalformed answers: marked per question with an error token")
kaputt = jev_antworten(
    neues_thema={"type": "noul", "noul": "ja, sehr"},
    beschluss={"type": "noul", "noul": True},
    aufgabe={"type": "noul", "noul": 1.7, "confidence": "hoch"},
    idee="kein Objekt",
    phase={"type": "choice", "choice": "pause", "confidence": 0.9})
del kaputt["offene_frage"]
kaputt[f"{KANARI}_frage"] = {"type": "noul", "noul": 0.5,
                              "explanation": KANARI}
FakeDecisions.replies.append(jev_body(kaputt))
m = d.decide(zustand(), backend=jev)
per = {x["question_id"]: x for x in m.answers}
check("still one entry per question we asked, in order, nothing extra",
      [x["question_id"] for x in m.answers] == IDS)
check("a string noul -> error no_noul, no p",
      per["neues_thema"].get("error") == "no_noul" and "p" not in per["neues_thema"],
      str(per["neues_thema"]))
check("a bool noul -> error no_noul", per["beschluss"].get("error") == "no_noul")
check("a missing answer -> error missing",
      per["offene_frage"] == {"question_id": "offene_frage", "error": "missing",
                              "ms": per["offene_frage"]["ms"]},
      str(per["offene_frage"]))
check("a non-object answer -> error missing (nothing usable)",
      per["idee"].get("error") == "missing", str(per["idee"]))
check("a p outside 0..1 is clamped, a text confidence is None",
      per["aufgabe"]["p"] == 1.0 and per["aufgabe"]["confidence"] is None,
      str(per["aufgabe"]))
check("a choice outside the phases -> error bad_choice",
      per["phase"].get("error") == "bad_choice" and "choice" not in per["phase"],
      str(per["phase"]))
check("errors are counted", m.errors == 5, str(m.errors))
check("the canary from the answer is nowhere in the result",
      KANARI not in repr(m), "")

print("\ncall-level failures: DecideError with a token, no body, no key")
ausgabe = io.StringIO()
fehler_text = []
with contextlib.redirect_stdout(ausgabe), contextlib.redirect_stderr(ausgabe):
    for status in (401, 402, 500):
        FakeDecisions.replies.append(
            (status, {"error": {"code": status,
                                "message": f"{KANARI} {SCHLUESSEL}"}}))
        try:
            d.decide(zustand(), backend=jev)
        except d.DecideError as e:
            fehler_text.append((e.token, str(e), repr(e)))
check("each HTTP error becomes DecideError(http_<status>)",
      [t for t, _, _ in fehler_text] == ["http_401", "http_402", "http_500"],
      str([t for t, _, _ in fehler_text]))
check("THE CANARY: neither the body's text nor the key reach the error",
      all(KANARI not in s and SCHLUESSEL not in s
          for t in fehler_text for s in t), str(fehler_text))
check("THE CANARY: nothing on stdout/stderr either",
      KANARI not in ausgabe.getvalue()
      and SCHLUESSEL not in ausgabe.getvalue(), ausgabe.getvalue()[:80])
check("THE CANARY: the key is not in the backend's repr or vars-free str",
      SCHLUESSEL not in repr(jev) and SCHLUESSEL not in str(jev))

tot = d.JevBackend(SCHLUESSEL, base_url="http://127.0.0.1:1", timeout_s=2.0)
tok = ""
try:
    d.decide(zustand(), backend=tot)
except d.DecideError as e:
    tok = e.token
check("an unreachable endpoint -> DecideError with a transport token",
      tok in ("URLError", "ConnectionRefusedError", "timeout"), tok)

srv.shutdown()

# ============================================ the scripted backend (MM-B)
print("\nscript: deterministic answers from a JSON rule file")
import atexit  # noqa: E402
import shutil  # noqa: E402
import tempfile  # noqa: E402

skript_dir = Path(tempfile.mkdtemp(prefix="cothinker-decide-script-"))
atexit.register(shutil.rmtree, skript_dir, ignore_errors=True)
skript = skript_dir / "regeln.json"
skript.write_text(json.dumps({
    "default": 0.1,
    "rules": [
        {"match": "MARKEBESCHLUSS", "answers": {"beschluss": 0.9}},
        {"match": "MARKEBESCHLUSS", "answers": {"beschluss": 0.2,
                                                "aufgabe": 0.6}},
        {"match": "MARKEJA", "answers": {"zuruf_robo": 0.8,
                                         "rueckfrage_bejaht": 0.9,
                                         "termin_art": "arbeit"}},
        {"match": "MARKEFALSCH", "answers": {"termin_art": "zirkus"}},
    ]}), encoding="utf-8")
sb = d.ScriptBackend(str(skript))
check("ScriptBackend is a Backend named `script`",
      isinstance(sb, d.Backend) and sb.name == "script")
check("`script` is one of BACKENDS", "script" in d.BACKENDS)
r1 = d.decide(zustand(block="Wir beschliessen das jetzt. MARKEBESCHLUSS"),
              backend=sb)
p1 = {a["question_id"]: a.get("p") for a in r1.answers}
check("the first matching rule that names a question decides it",
      p1["beschluss"] == 0.9, str(p1))
check("a later matching rule still decides a question the first one "
      "leaves out", p1["aufgabe"] == 0.6, str(p1))
check("a noul no rule names gets `default`",
      all(p1[i] == 0.1 for i in NOUL_IDS if i not in ("beschluss", "aufgabe")),
      str(p1))
check("confidence is 0.9", all(a.get("confidence") == 0.9
                               for a in r1.answers if "p" in a))
check("a choice no rule names is left out (gap), never guessed",
      all("error" in a for a in r1.answers if a["question_id"] == "phase"),
      str([a for a in r1.answers if a["question_id"] == "phase"]))
check("the backend token is `script`", r1.backend == "script")
check("QUESTIONS_HASH is unchanged by the new backend",
      r1.questions_hash == q.QUESTIONS_HASH)

extra = tuple(q.CONFIRM_QUESTIONS) + tuple(q.MODE_QUESTIONS)
r2 = d.decide(zustand(block="Ja, genau so. MARKEJA"), extra, backend=sb)
b2 = r2.by_id()
check("it answers whatever question set it is handed (confirm)",
      b2[q.CONFIRM_ID].get("p") == 0.9, str(b2))
check("... and a choice from the rule when it is one of the criteria",
      b2[q.MODE_ID].get("choice") == "arbeit", str(b2))
r3 = d.decide(zustand(block="Voellig anders. MARKEFALSCH"), extra, backend=sb)
b3 = r3.by_id()
check("a choice outside the question's criteria is left out (gap)",
      "error" in b3[q.MODE_ID] and "choice" not in b3[q.MODE_ID], str(b3))
check("... and the confirm noul gets the default",
      b3[q.CONFIRM_ID].get("p") == 0.1, str(b3))
r4 = d.decide(zustand(block="Kein Marker hier."), backend=sb)
check("no rule matches: every noul is `default`",
      all(a.get("p") == 0.1 for a in r4.answers
          if a["question_id"] in NOUL_IDS), str(r4.answers)[:200])

skript.write_text(json.dumps({"rules": [
    {"match": "MARKEBESCHLUSS", "answers": {"beschluss": 0.3}}]}),
    encoding="utf-8")
r5 = d.decide(zustand(block="Wir beschliessen das jetzt. MARKEBESCHLUSS"),
              backend=sb)
p5 = {a["question_id"]: a.get("p") for a in r5.answers}
check("the file is read on every ask (a test can change it)",
      p5["beschluss"] == 0.3, str(p5))
check("`default` is 0.0 when the file leaves it out",
      p5["aufgabe"] == 0.0, str(p5))

print("\nscript: failures carry a token, never the file's content")
for inhalt in (f"{{ kaputt {KANARI}", json.dumps([KANARI]),
               json.dumps({"rules": KANARI})):
    skript.write_text(inhalt, encoding="utf-8")
    fehler = None
    with contextlib.redirect_stdout(io.StringIO()) as aus_s, \
            contextlib.redirect_stderr(io.StringIO()) as err_s:
        try:
            d.decide(zustand(), backend=sb)
        except d.DecideError as e:
            fehler = e
    check(f"unusable file ({inhalt[:12]!r}...) -> DecideError(no_script)",
          fehler is not None and fehler.token == "no_script", repr(fehler))
    check("THE CANARY: the file's content is not in the error or output",
          fehler is not None and KANARI not in str(fehler)
          and KANARI not in repr(fehler) and KANARI not in aus_s.getvalue()
          and KANARI not in err_s.getvalue(), repr(fehler))
fehlt = None
try:
    d.decide(zustand(), backend=d.ScriptBackend(str(skript_dir / "fehlt.json")))
except d.DecideError as e:
    fehlt = e
check("a missing file -> DecideError(no_script)",
      fehlt is not None and fehlt.token == "no_script", repr(fehlt))

eb = d.backend_from_env({"COTHINKER_DECIDER": "script",
                         "COTHINKER_DECIDER_SCRIPT": str(skript)})
check("backend_from_env: script + COTHINKER_DECIDER_SCRIPT -> ScriptBackend",
      isinstance(eb, d.ScriptBackend), repr(eb))
ohne = None
try:
    d.backend_from_env({"COTHINKER_DECIDER": "script"})
except d.DecideError as e:
    ohne = e
check("script without COTHINKER_DECIDER_SCRIPT -> DecideError(no_script)",
      ohne is not None and ohne.token == "no_script", repr(ohne))
import shutil  # noqa: E402
shutil.rmtree(skript_dir, ignore_errors=True)

done()


@pytest.mark.parametrize("name,ok,info", collected())
def test_check(name, ok, info):
    assert ok, info
