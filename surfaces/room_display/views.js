// Render functions of the room display: DOM only, no fetching, no timers.
// Every populated list item gets data-row; an empty region shows its data-empty element.
import { TEXT } from "./texts.js";

function el(tag, text, attrs = {}) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
  return node;
}

function fill(regionSelector, emptyKey, rows) {
  const region = document.querySelector(regionSelector);
  for (const row of rows) row.dataset.row = "";
  region.querySelector(".content").replaceChildren(...rows);
  region.querySelector(`[data-empty="${emptyKey}"]`).hidden = rows.length > 0;
}

export function mmss(seconds) {
  const total = Math.max(0, Math.round(Math.abs(seconds)));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

export function applyLabels(viewKeys, onChoose) {
  document.querySelector(".brand").textContent = TEXT.brand;
  for (const [key, text] of Object.entries(TEXT.empty)) {
    document.querySelector(`[data-empty="${key}"]`).textContent = text;
  }
  for (const [id, text] of Object.entries(TEXT.headings)) document.querySelector(`#${id} h2`).textContent = text;
  for (const [key, text] of Object.entries(TEXT.resultsHeadings)) {
    document.querySelector(`[data-results="${key}"] h3`).textContent = text;
  }
  for (const key of viewKeys) document.querySelector(`[data-view="${key}"] h2`).textContent = TEXT.views[key];
  const button = document.getElementById("menu-button");
  button.textContent = "☰";
  button.setAttribute("aria-label", TEXT.menu);
  document.querySelector("#menu h2").textContent = TEXT.menu;
  document.getElementById("menu-list").replaceChildren(...viewKeys.map((key, index) => {
    const choose = el("button", `${index + 1}  ${TEXT.views[key]}`, { type: "button", "data-choose": key });
    choose.addEventListener("click", () => onChoose(key));
    const item = el("li");
    item.append(choose);
    return item;
  }));
}

export function showView(key, mode, remainingMs) {
  for (const section of document.querySelectorAll("[data-view]")) section.hidden = section.dataset.view !== key;
  document.documentElement.dataset.mode = mode;
  document.getElementById("mode").textContent =
    mode === "rest" ? TEXT.mode.rest : `${TEXT.mode[mode]} ${mmss(remainingMs / 1000)}`;
}

function stateText(agentState) {
  if (agentState.key === "listening") {
    return agentState.remainingS != null
      ? `${TEXT.state.listening} ${mmss(agentState.remainingS)}` : TEXT.state.listening;
  }
  if (agentState.key === "working") return `${TEXT.state.working} ${agentState.label}`;
  return TEXT.state[agentState.key];
}

export function renderTop(headline, agentState, status) {
  document.getElementById("headline").textContent = headline || "";
  document.documentElement.dataset.state = agentState.key;
  document.getElementById("state").textContent = stateText(agentState);
  document.getElementById("status-line").textContent =
    status ? `${status.severity === "error" ? TEXT.fault + ": " : ""}${status.text}` : "";
}

export function renderAnswer(answer) {
  const node = document.getElementById("answer");
  node.textContent = answer ? answer.text : "";
  node.hidden = !answer;
}

export function renderAgenda(agenda, remainingS) {
  const rows = [];
  if (agenda && agenda.total > 0) {
    rows.push(el("p", agenda.current_text, { class: "agenda-item" }));
    rows.push(el("p", `${agenda.current}/${agenda.total}`, { class: "agenda-pos" }));
    if (remainingS !== null) {
      const over = remainingS < 0;
      rows.push(el("p", over ? `+${mmss(remainingS)} ${TEXT.overrun}` : mmss(remainingS),
        { class: over ? "agenda-time over" : "agenda-time" }));
    }
  }
  fill("#left-agenda", "left-agenda", rows);
}

export function renderSpeaker(voice) {
  const rows = [];
  if (voice && voice.speaking) {
    rows.push(el("p", voice.speaking, { class: "speaker" }));
    const sinceS = voice.since ? (Date.now() - Date.parse(voice.since)) / 1000 : -1;
    if (sinceS >= 0) rows.push(el("p", `${TEXT.since} ${mmss(sinceS)}`, { class: "speaker-since" }));
  }
  document.querySelector('[data-empty="left-speaker"]').textContent =
    voice ? TEXT.speakerNobody : TEXT.empty["left-speaker"];
  fill("#left-speaker", "left-speaker", rows);
}

// Mark a region as just confirmed by an intent for `ms` (an accent outline
// in style.css), even when its content did not change.
const freshTimers = new Map();
export function markFresh(selector, ms = 2000) {
  const region = document.querySelector(selector);
  region.dataset.fresh = "";
  clearTimeout(freshTimers.get(selector));
  freshTimers.set(selector, setTimeout(() => delete region.dataset.fresh, ms));
}

function queueNames(snapshot) {
  if (snapshot.queue) {
    return {
      speaking: snapshot.queue.speaking ? snapshot.queue.speaking.name : null,
      waiting: (snapshot.queue.waiting || []).map((seat) => seat.name),
    };
  }
  const agenda = snapshot.agenda;
  if (agenda && agenda.people && agenda.people.length) {
    return { speaking: agenda.speaker, waiting: agenda.people.filter((name) => name !== agenda.speaker) };
  }
  return { speaking: null, waiting: [] };
}

export function renderQueue(snapshot) {
  const names = queueNames(snapshot);
  for (const [selector, key] of [["#left-queue", "left-queue"], ['[data-view="queue"]', "queue"]]) {
    const rows = [];
    if (names.speaking) rows.push(el("li", names.speaking, { class: "queue-speaking" }));
    for (const name of names.waiting) rows.push(el("li", name));
    fill(selector, key, rows);
  }
}

export function renderLive(lines, mirror, partial) {
  const rows = (lines || []).map((line) => {
    const item = el("li");
    item.append(el("strong", line.speaker || TEXT.unknownSpeaker), el("span", line.text));
    return item;
  });
  if (!rows.length && mirror) rows.push(el("li", mirror));
  if (partial && partial.text) {
    const item = el("li", undefined, { "data-partial": "" });
    item.append(el("strong", partial.speaker || TEXT.unknownSpeaker), el("span", partial.text));
    rows.push(item);
  }
  fill('[data-view="live"]', "live", rows);
}

// A transcript_partial intent is stored even while the P toggle is off (so
// turning it on shows the latest one immediately), which means it does not
// change what renderLive puts on screen -- mark the (hidden) container
// attribute instead, so the intent is honestly "rendered" rather than
// falsely reported as "dropped" by the surface conformance check. `seq`
// (bumped once per transcript_partial intent, by the caller) guarantees a
// visible change even for the clearing intent, whose empty text would
// otherwise repeat whatever a same-frame transcript:recent already cleared
// it to.
export function markPartialPending(partial, seq) {
  const text = partial && partial.text ? partial.text : "";
  document.querySelector('[data-view="live"]').dataset.partialPending = `${seq}:${text}`;
}

export function renderTopics(agenda, history) {
  const items = agenda && agenda.items && agenda.items.length
    ? agenda.items.map((item) => ({ title: item.title, status: item.status }))
    : (history || []).map((entry) => ({ title: entry.title, status: null }));
  fill('[data-view="topics"]', "topics", items.map((item) => {
    const row = el("li", item.title);
    if (item.status) {
      row.dataset.status = item.status;
      row.append(el("span", TEXT.topicStatus[item.status], { class: "topic-status" }));
    }
    return row;
  }));
}

export function renderResults(snapshot) {
  const decisions = (snapshot.decisions || []).map((d) => el("li", d.time ? `${d.text} (${d.time})` : d.text));
  const tasks = (snapshot.tasks || []).map((t) =>
    el("li", `${t.text} — ${t.person || TEXT.noPerson} · ${t.due || TEXT.noDue}`));
  const question = snapshot.question && snapshot.question.state === "open" ? [el("li", snapshot.question.text)] : [];
  fill('[data-results="decisions"]', "results-decisions", decisions);
  fill('[data-results="tasks"]', "results-tasks", tasks);
  fill('[data-results="questions"]', "results-questions", question);
  const any = decisions.length + tasks.length + question.length > 0;
  const view = document.querySelector('[data-view="results"]');
  view.querySelector('[data-empty="results"]').hidden = any;
  for (const part of view.querySelectorAll("[data-results]")) part.hidden = !any;
}

export function renderShare(share) {
  fill('[data-view="share"]', "share", (share ? share.shares : []).map((row) => {
    const percent = Math.round(row.share * 100);
    const bar = el("span", undefined, { class: "bar" });
    bar.style.inlineSize = `${percent}%`;
    const item = el("li");
    item.append(el("span", row.given || row.person, { class: "who" }), bar, el("span", `${percent} %`, { class: "pct" }));
    return item;
  }));
}

export function renderMemory(knowledge) {
  const hits = knowledge ? knowledge.hits : [];
  document.querySelector('[data-view="memory"] .query').textContent =
    hits.length ? `${TEXT.memoryQuery} ${knowledge.query}` : "";
  fill('[data-view="memory"]', "memory", hits.map((hit) => {
    const item = el("li");
    item.append(el("span", hit.title, { class: "hit-title" }), el("span", hit.kind, { class: "hit-kind" }));
    return item;
  }));
}
