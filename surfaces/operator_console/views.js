// Operator console render functions: DOM only. Populated items carry data-row;
// a panel without data shows its data-empty element.
import { TEXT } from "./texts.js";

const UPDATE_CAP = 20;

function el(tag, text, attrs = {}) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
  return node;
}

function panel(key) { return document.querySelector(`[data-panel="${key}"]`); }

function fill(container, emptyKey, rows) {
  for (const row of rows) row.dataset.row = "";
  container.replaceChildren(...rows);
  document.querySelector(`[data-empty="${emptyKey}"]`).hidden = rows.length > 0;
}

function pairs(entries) {
  return entries.flatMap(([term, value, cls]) => [el("dt", term), el("dd", String(value), cls ? { class: cls } : {})]);
}

function clock(iso) { return iso ? new Date(iso).toLocaleTimeString("en-US") : "–"; }

export function mmss(seconds) {
  const total = Math.max(0, Math.round(seconds));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

// Decimal-point number formatting, at most `digits` decimals.
function num(value, digits) {
  return value.toLocaleString("en-US", { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

// Error level: always the word, the colour only in addition (#176 `ErrorEntry.level`).
const LEVEL_CLASS = { info: "", warning: "warn", error: "danger" };

export function applyLabels() {
  document.querySelector(".brand").textContent = TEXT.brand;
  for (const [key, text] of Object.entries(TEXT.panels)) panel(key).querySelector("h2").textContent = text;
  for (const [key, text] of Object.entries(TEXT.empty)) document.querySelector(`[data-empty="${key}"]`).textContent = text;
  for (const [key, text] of Object.entries(TEXT.label)) document.querySelector(`[data-label="${key}"]`).textContent = text;
  document.getElementById("start-button").textContent = TEXT.start;
  document.getElementById("stop").textContent = TEXT.stop;
  document.getElementById("switch").textContent = TEXT.switch;
  document.querySelector("#stop-dialog h2").textContent = TEXT.stopTitle;
  document.getElementById("stop-cancel").textContent = TEXT.cancel;
}

export function renderStatus(status) {
  document.documentElement.dataset.running = String(status.running);
  document.getElementById("running-state").textContent = status.running ? TEXT.running : TEXT.stopped;

  fill(panel("services").querySelector(".content"), "services", status.services.map((s) => {
    const cls = s.state === "active" ? "ok" : s.state === "failed" ? "danger" : "warn";
    return el("li", `${s.name}: ${TEXT.serviceState[s.state]} · ${clock(s.since)}`, { class: cls });
  }));

  fill(panel("meeting").querySelector(".content"), "meeting", status.meeting.participants.map((p) => {
    const item = el("li", `${p.name} · ${p.speaking ? TEXT.speaking : TEXT.silent} `);
    item.append(el("meter", undefined, { min: "-60", max: "0", value: String(p.level_dbfs) }),
      el("span", ` ${Math.round(p.level_dbfs)} dBFS`));
    return item;
  }));

  const stt = status.stt;
  fill(panel("stt").querySelector(".content"), "stt", stt.connected || stt.engine ? pairs([
    [TEXT.stt.engine, `${stt.engine} · ${stt.connected ? TEXT.stt.connected : TEXT.stt.disconnected}`,
      stt.connected ? "ok" : "danger"],
    [TEXT.stt.lastLine, `${Math.round(stt.seconds_since_last_final)} ${TEXT.seconds}`],
    [TEXT.stt.minutesToday, stt.minutes_today.toFixed(1)],
  ]) : []);

  const session = status.session;
  fill(panel("session").querySelector(":scope > .content"), "session", session.session_folder ? pairs([
    [TEXT.session.project, session.project.label],
    ...(session.title ? [[TEXT.session.title, session.title]] : []),
    [TEXT.session.folder, session.session_folder],
    [TEXT.session.utterances, session.utterances],
    [TEXT.session.lastSaved, clock(session.last_saved_at)],
  ]) : []);
  const agenda = session.agenda;
  fill(panel("session").querySelector('[data-part="agenda"] .content'), "session-agenda", agenda ? pairs([
    [TEXT.session.item, `${agenda.index}/${agenda.total} ${agenda.current_item}`],
    [TEXT.session.remaining, mmss(agenda.remaining_s)],
  ]) : []);

  const last = status.call.last;
  fill(panel("call").querySelector(".content"), "call", last ? pairs([
    [TEXT.call.at, `${clock(last.at)} · ${last.answered ? TEXT.call.answered : TEXT.call.unanswered}`,
      last.answered ? "ok" : "warn"],
    [TEXT.call.latency, `${last.latency_s.toFixed(1)} ${TEXT.seconds}`],
  ]) : []);

  fill(panel("errors").querySelector(".content"), "errors",
    [...status.errors].reverse().map((e) => el("li",
      `${clock(e.at)} ${TEXT.errorLevel[e.level]} · ${e.service}: ${e.message}`,
      { class: LEVEL_CLASS[e.level], "data-level": e.level })));

  const free = status.disk.free_percent;
  const level = free < 5 ? ["danger", TEXT.disk.critical] : free < 15 ? ["warn", TEXT.disk.low] : ["ok", ""];
  const disk = panel("disk").querySelector(".content");
  disk.className = `content ${level[0]}`;
  const hoursLeft = status.disk.recording_hours_left;
  disk.textContent = [
    `${num(free, 1)} % ${TEXT.disk.free}${level[1] ? " · " + level[1] : ""}`,
    `${num(status.disk.free_bytes / 1e9, 1)} ${TEXT.disk.gigabytes}`,
    ...(hoursLeft == null ? [] : [`${TEXT.disk.recordingLeft} ${num(hoursLeft, 1)} ${TEXT.disk.hours}`]),
  ].join(" · ");
}

export function renderControl(status, projects) {
  const options = () => projects.map((p) => el("option", p.label, { value: p.id }));
  const startProject = document.getElementById("start-project");
  const switchProject = document.getElementById("switch-project");
  if (startProject.options.length !== projects.length) {
    startProject.replaceChildren(...options());
    switchProject.replaceChildren(...options());
  }
  if (status.session.project.id && document.activeElement !== switchProject) switchProject.value = status.session.project.id;
  const none = projects.length === 0;
  document.querySelector('[data-empty="control"]').hidden = !none;
  for (const id of ["start-project", "start-title", "start-button"]) document.getElementById(id).disabled = status.running || none;
  document.getElementById("stop").disabled = !status.running;
  switchProject.disabled = !status.can_switch_project || none;
  document.getElementById("switch").disabled = !status.can_switch_project || none;
  document.getElementById("switch-hint").textContent = status.can_switch_project ? "" : TEXT.switchHint;
}

export function logUpdate(status) {
  const updates = document.getElementById("updates");
  const line = el("div", `${new Date().toLocaleTimeString("en-US")} · ${status.running ? TEXT.running : TEXT.stopped} · ${status.meeting.count}`);
  updates.prepend(line);
  while (updates.children.length > UPDATE_CAP) updates.lastElementChild.remove();
  document.querySelector('[data-empty="updates"]').hidden = true;
}

export function showConnection(text) { document.getElementById("connection-state").textContent = text || ""; }

export function showError(text) { document.getElementById("command-error").textContent = text || ""; }

export function openStopDialog(count) {
  document.getElementById("stop-warning").textContent = count > 0 ? TEXT.stopWarning(count) : "";
  const confirm = document.getElementById("stop-confirm");
  confirm.textContent = count > 0 ? TEXT.stopForce : TEXT.stopPlain;
  confirm.dataset.force = String(count > 0);
  document.getElementById("stop-dialog").showModal();
}
