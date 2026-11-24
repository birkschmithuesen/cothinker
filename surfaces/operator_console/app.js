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
    onPush(await getJson("status"));
    return true;
  } catch (err) {
    statusFailure(err);
    return false;
  }
}

function connect() {
  stream((next) => { views.showConnection(""); onPush(next); }, async () => {
    views.showConnection(TEXT.connectionLost);
    await new Promise((resolve) => setTimeout(resolve, RECONNECT_MS));
    connect();
    await loadStatus();
  });
}

views.applyLabels();

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
