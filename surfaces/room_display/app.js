// Room display wiring (issue #173): ?api= base, GET /snapshot, SSE /events.
// /events carries view intents only, so every intent is followed by a fresh
// GET /snapshot (spec 3.1). Intents unknown to this surface are logged as
// "dropped <kind>" (ADR 0006).
import { createSwitcher } from "./switcher.js";
import * as views from "./views.js";

const api = new URLSearchParams(location.search).get("api") || "/";
const VIEW_KEYS = ["live", "topics", "results", "queue", "share", "memory"];
const HANDLED = new Set([
  "timer", "status", "transcript:recent", "tasks", "queue", "share", "knowledge", "voice",
  "transcript_partial",
]);

// Mirrors CALL_ANSWER_VISIBLE_S in gateway/call_lane.py:161; hardcoded (not
// imported) because the browser surface never pulls in gateway code.
const CALL_ANSWER_VISIBLE_S = 300;

let snapshot = {};
let fetched = false;
let snapshotOk = false;
let lastStatus = null;
let callAnswer = null;
let showPartials = false;
let partialSeq = 0;
let timer = null;
const events = new EventSource(api + "events");

const switcher = createSwitcher({
  restView: "live", autoHoldMs: 30000, pinMs: 120000,
  onChange: () => views.showView(switcher.current(), switcher.mode(), switcher.remainingMs()),
});

function callAnswerVisible() {
  return !!callAnswer && (Date.now() - callAnswer.at) / 1000 < CALL_ANSWER_VISIBLE_S;
}

// Priority (issue #206): offline beats everything; then an answer still
// within its visibility window beats working beats listening beats idle --
// an answer that just landed must not be hidden by a new pending
// announcement starting at the same moment.
function agentStatus() {
  if (!snapshotOk || events.readyState !== EventSource.OPEN) return { key: "offline" };
  if (callAnswerVisible()) return { key: "answer" };
  if (snapshot.pending && snapshot.pending.length) return { key: "working", label: snapshot.pending[0].label };
  if (snapshot.listen && snapshot.listen.state === "listening") {
    return { key: "listening", remainingS: snapshot.listen.closes_in_s };
  }
  return { key: "idle" };
}

function remainingS() {
  const agenda = snapshot.agenda;
  if (!agenda) return null;
  if (timer && timer.label === agenda.current_text) return timer.remaining_s - (Date.now() - timer.at) / 1000;
  if (agenda.item_budget_s == null) return null;
  return agenda.item_budget_s - (Date.now() / 1000 - agenda.item_started_at);
}

function renderAll() {
  views.renderTop(snapshot.headline, agentStatus(), lastStatus);
  views.renderAnswer(callAnswerVisible() ? callAnswer : null);
  views.renderAgenda(snapshot.agenda, remainingS());
  views.renderSpeaker(snapshot.voice);
  views.renderQueue(snapshot);
  views.renderLive(snapshot.transcript_recent, snapshot.mirror, showPartials ? snapshot.transcript_partial : null);
  views.markPartialPending(snapshot.transcript_partial, partialSeq);
  views.renderTopics(snapshot.agenda, snapshot.history);
  views.renderResults(snapshot);
  views.renderShare(snapshot.share);
  views.renderMemory(snapshot.knowledge);
}

function trigger(before, after) {
  const ids = (s) => new Set((s.decisions || []).map((d) => d.id));
  const openQuestion = (s) => (s.question && s.question.state === "open" ? s.question.id : null);
  const oldIds = ids(before);
  if ([...ids(after)].some((id) => !oldIds.has(id))) return "results";
  if (openQuestion(after) && openQuestion(after) !== openQuestion(before)) return "results";
  if (after.agenda && (!before.agenda || after.agenda.current !== before.agenda.current)) return "topics";
  return null;
}

async function refresh() {
  try {
    const response = await fetch(api + "snapshot");
    if (!response.ok) throw new Error(`snapshot ${response.status}`);
    const next = await response.json();
    const hit = fetched ? trigger(snapshot, next) : null;
    // call_answer has no owning intent kind, so it only ever arrives in the
    // snapshot (a change shows up as a generic StatusIntent, which just
    // triggers this refetch); the snapshot carries no timestamp for it, so
    // the visibility window starts when we first see the new text here.
    if (next.call_answer && next.call_answer !== snapshot.call_answer) {
      callAnswer = { text: next.call_answer, at: Date.now() };
    }
    snapshot = next;
    fetched = true;
    snapshotOk = true;
    if (hit) switcher.auto(hit);
  } catch (err) {
    snapshotOk = false;
  }
  renderAll();
}

function onIntent(intent) {
  if (!HANDLED.has(intent.kind)) {
    console.log("dropped " + intent.kind);
    return;
  }
  if (intent.kind === "timer") timer = { label: intent.label, remaining_s: intent.remaining_s, at: Date.now() };
  else if (intent.kind === "status") lastStatus = intent;
  else if (intent.kind === "transcript:recent") {
    snapshot.transcript_recent = intent.lines;
    snapshot.transcript_partial = null;
  } else if (intent.kind === "transcript_partial") {
    partialSeq += 1;
    snapshot.transcript_partial = {
      turn_id: intent.turn_id, speaker: intent.speaker, text: intent.text, started_at: intent.started_at,
    };
  } else if (intent.kind === "tasks") {
    const grew = intent.tasks.length > (snapshot.tasks || []).length;
    snapshot.tasks = intent.tasks;
    if (grew) switcher.auto("results");
  } else if (intent.kind === "queue") snapshot.queue = { mode: intent.mode, speaking: intent.speaking, waiting: intent.waiting };
  else if (intent.kind === "share") snapshot.share = { shares: intent.shares, total_words: intent.total_words };
  else if (intent.kind === "knowledge") {
    const isNew = !snapshot.knowledge || snapshot.knowledge.topic_id !== intent.topic_id;
    snapshot.knowledge = { topic_id: intent.topic_id, query: intent.query, hits: intent.hits };
    if (isNew && intent.hits.length) switcher.auto("memory");
  } else if (intent.kind === "voice") {
    snapshot.voice = { speaking: intent.speaking, since: intent.since, recent: intent.recent };
    // Mark "Speaking now" as just confirmed, even when the name is unchanged
    // (spec 3.6): the room sees that attribution is live.
    views.markFresh("#left-speaker");
  }
  renderAll();
  refresh();
}

function moveFocus(step) {
  const buttons = [...document.querySelectorAll("#menu-list button")];
  const index = buttons.indexOf(document.activeElement);
  buttons[(index + step + buttons.length) % buttons.length].focus();
}

function choose(key) {
  switcher.pin(key);
  document.getElementById("menu").close();
}

views.applyLabels(VIEW_KEYS, choose);
views.showView(switcher.current(), switcher.mode(), 0);
renderAll();

const menu = document.getElementById("menu");

// Open the menu with focus on the view shown now, so arrow keys start from there.
function openMenu() {
  menu.showModal();
  document.querySelector(`#menu-list [data-choose="${switcher.current()}"]`).focus();
}

document.getElementById("menu-button").addEventListener("click", openMenu);
document.addEventListener("keydown", (event) => {
  if (event.key === "m" || event.key === "M") {
    event.preventDefault();
    if (menu.open) menu.close(); else openMenu();
  } else if (event.key === "p" || event.key === "P") {
    event.preventDefault();
    showPartials = !showPartials;
    renderAll();
  } else if (menu.open && /^[1-6]$/.test(event.key)) {
    choose(VIEW_KEYS[Number(event.key) - 1]);
  } else if (menu.open && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
    event.preventDefault();
    moveFocus(event.key === "ArrowDown" ? 1 : -1);
  }
});

events.addEventListener("open", refresh);
events.addEventListener("error", renderAll);
events.addEventListener("view-intent", (event) => onIntent(JSON.parse(event.data)));
setInterval(() => {
  views.renderTop(snapshot.headline, agentStatus(), lastStatus);
  views.renderAnswer(callAnswerVisible() ? callAnswer : null);
  views.renderAgenda(snapshot.agenda, remainingS());
  views.renderSpeaker(snapshot.voice);
  views.showView(switcher.current(), switcher.mode(), switcher.remainingMs());
}, 1000);
refresh();
