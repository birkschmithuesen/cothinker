// Operator console wiring (issue #173): status, push channel, commands.
import { getJson, post, stream } from "./api.js";
import { TEXT } from "./texts.js";
import * as views from "./views.js";

let status = null;
let projects = [];

function render(next) {
  status = next;
  views.renderStatus(status);
  views.renderControl(status, projects);
}

function onPush(next) {
  render(next);
  views.logUpdate(next);
}

async function command(path, body) {
  let answer;
  try {
    answer = await post(path, body);
  } catch (err) {
    views.showError(TEXT.commandFailed);
    return { ok: false, code: 0, body: {} };
  }
  views.showError(answer.ok ? "" : answer.message || TEXT.commandFailed);
  if (answer.body.status) render(answer.body.status);
  return answer;
}

const RECONNECT_MS = 3000;

function statusFailure(err) {
  views.showConnection(err && (err.code === 401 || err.code === 403) ? TEXT.authFailed : TEXT.statusFailed);
}

async function loadStatus() {
  try {
    const next = await getJson("status");
    if (!streamDown) views.showConnection("");
    onPush(next);
    return true;
  } catch (err) {
    statusFailure(err);
    return false;
  }
}

// True from a lost stream until its next attempt; a status reload alone must not
// hide the warning while the push channel is known to be down.
let streamDown = false;

function streamUp() {
  streamDown = false;
  views.showConnection("");
}

function connect() {
  stream((next) => { streamUp(); onPush(next); }, async () => {
    streamDown = true;
    views.showConnection(TEXT.connectionLost);
    await new Promise((resolve) => setTimeout(resolve, RECONNECT_MS));
    streamDown = false;
    connect();
    await loadStatus();
  }, streamUp);
}

// The meeting chat (issue #208, ADR 0027): the participant contract's own
// chat page, embedded only for a same-origin `?chat=` URL. This page's code
// never calls the participant contract itself (one page, one audience).
function showChat() {
  const chat = new URLSearchParams(location.search).get("chat");
  if (!chat) return;
  let url;
  try {
    url = new URL(chat, location.href);
  } catch (err) {
    return;
  }
  if (url.origin !== location.origin) return;
  const frame = document.getElementById("chat-frame");
  frame.title = TEXT.chatFrame;
  frame.src = url.href;
  frame.closest("section").hidden = false;
}

views.applyLabels();
showChat();

document.getElementById("start").addEventListener("submit", (event) => {
  event.preventDefault();
  command("start", {
    project_id: document.getElementById("start-project").value,
    title: document.getElementById("start-title").value,
  });
});

document.getElementById("stop").addEventListener("click", () => {
  if (status) views.openStopDialog(status.meeting.count);
});
document.getElementById("stop-cancel").addEventListener("click", () => document.getElementById("stop-dialog").close());
document.getElementById("stop-confirm").addEventListener("click", async (event) => {
  const force = event.currentTarget.dataset.force === "true";
  document.getElementById("stop-dialog").close();
  const answer = await command("stop", { confirm: true, force });
  if (answer.code === 409 && answer.body.participant_count) views.openStopDialog(answer.body.participant_count);
});
document.getElementById("switch").addEventListener("click", () =>
  command("project", { project_id: document.getElementById("switch-project").value }));

getJson("projects").catch(() => []).then(async (list) => {
  projects = list;
  await loadStatus();
});
connect();
