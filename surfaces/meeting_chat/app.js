// Meeting chat wiring (issue #208, ADR 0027): ?api= base, GET snapshot once,
// SSE events for the `chat:room` intent, POST actions for a message and for
// the display name. Every other intent kind is logged as "dropped <kind>"
// (ADR 0006). Text is only ever set through textContent.
import { TEXT } from "./texts.js";

const api = new URLSearchParams(location.search).get("api") || "/";
const NAME_KEY = "cothinker.chat.name";
const thread = document.getElementById("thread");
const notice = document.getElementById("notice");
const message = document.getElementById("message");
const nameInput = document.getElementById("name");

function label(selector, text) {
  document.querySelector(selector).textContent = text;
}

function applyLabels() {
  label(".brand", TEXT.brand);
  label("h1", TEXT.title);
  label('label[for="name"]', TEXT.nameLabel);
  label("#name-button", TEXT.nameButton);
  label('label[for="message"]', TEXT.messageLabel);
  label("#send-button", TEXT.sendButton);
  label("#empty", TEXT.empty);
  message.placeholder = TEXT.messagePlaceholder;
}

function item(m) {
  const li = document.createElement("li");
  li.dataset.role = m.role;
  li.dataset.id = m.id;
  const who = document.createElement("div");
  who.className = "who";
  who.textContent = m.role === "agent" ? TEXT.agent : m.name || TEXT.guest;
  const text = document.createElement("div");
  text.className = "text";
  text.textContent = m.text;
  li.append(who, text);
  if (m.role === "participant" && TEXT.status[m.status]) {
    const status = document.createElement("div");
    status.className = "status";
    status.dataset.status = m.status;
    status.textContent = TEXT.status[m.status];
    li.append(status);
  }
  return li;
}

function render(messages) {
  thread.replaceChildren(...messages.map(item));
  document.getElementById("empty").hidden = messages.length > 0;
}

async function post(action) {
  try {
    const response = await fetch(api + "actions", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json" }, body: JSON.stringify(action),
    });
    return response.status;
  } catch (err) {
    return 0;
  }
}

async function sendName(name, silent) {
  const status = await post({ kind: "edit", intent_id: "participant:name", text: name });
  if (silent) return;
  notice.textContent = status === 202 ? TEXT.nameSaved : status === 429 ? TEXT.tooFast : TEXT.sendFailed;
}

async function sendMessage() {
  const text = message.value.trim();
  if (!text) return;
  const status = await post({ kind: "message", intent_id: "chat:room", text });
  if (status === 202) {
    message.value = "";
    notice.textContent = "";
  } else {
    notice.textContent = status === 429 ? TEXT.tooFast : TEXT.sendFailed;
  }
}

document.getElementById("name-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const name = nameInput.value.trim();
  if (!name) return;
  localStorage.setItem(NAME_KEY, name);
  sendName(name);
});

document.getElementById("send-form").addEventListener("submit", (event) => {
  event.preventDefault();
  sendMessage();
});

message.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendMessage();
  }
});

applyLabels();
render([]);
const stored = localStorage.getItem(NAME_KEY);
if (stored) {
  nameInput.value = stored;
  sendName(stored, true);
}

fetch(api + "snapshot")
  .then((response) => (response.ok ? response.json() : {}))
  .then((snapshot) => render((snapshot.chat && snapshot.chat.messages) || []))
  .catch(() => {});

const events = new EventSource(api + "events");
events.addEventListener("view-intent", (event) => {
  const intent = JSON.parse(event.data);
  if (intent.kind === "chat") render(intent.messages);
  else console.log("dropped " + intent.kind);
});
