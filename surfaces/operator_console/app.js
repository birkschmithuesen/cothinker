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
  const answer = await post(path, body);
  views.showError(answer.ok ? "" : answer.body.detail);
  if (answer.body.status) render(answer.body.status);
  return answer;
}

views.applyLabels();

document.getElementById("start").addEventListener("submit", (event) => {
  event.preventDefault();
  command("start", {
    project_id: document.getElementById("start-project").value,
    title: document.getElementById("start-title").value,
  });
});

document.getElementById("stop").addEventListener("click", () => views.openStopDialog(status.meeting.count));
document.getElementById("stop-cancel").addEventListener("click", () => document.getElementById("stop-dialog").close());
document.getElementById("stop-confirm").addEventListener("click", async (event) => {
  const force = event.currentTarget.dataset.force === "true";
  document.getElementById("stop-dialog").close();
  const answer = await command("stop", { confirm: true, force });
  if (answer.code === 409 && answer.body.participant_count) views.openStopDialog(answer.body.participant_count);
});
document.getElementById("switch").addEventListener("click", () =>
  command("project", { project_id: document.getElementById("switch-project").value }));

Promise.all([getJson("status"), getJson("projects").catch(() => [])]).then(([first, list]) => {
  projects = list;
  onPush(first);
});
stream(onPush, () => views.showError(TEXT.connectionLost));
