// Operator contract client (issue #159): the token is sent as a Bearer header on
// every call and never put into a request URL (the page itself receives it as
// the `?token=` query parameter, which is how the contract check passes it);
// the push channel is read with fetch + a stream reader,
// because EventSource cannot carry the header (as surfaces/operator/examples/minimal).
const params = new URLSearchParams(location.search);
const base = params.get("api") || "/operator/";
const headers = { Authorization: "Bearer " + (params.get("token") || "") };

export async function getJson(path) {
  const response = await fetch(base + path, { headers });
  if (!response.ok) {
    const error = new Error(`${path} ${response.status}`);
    error.code = response.status;
    throw error;
  }
  return response.json();
}

export async function post(path, body) {
  const response = await fetch(base + path, {
    method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  let parsed = {};
  try { parsed = await response.json(); } catch (err) { /* non-JSON body, e.g. a proxy error page */ }
  const answerBody = parsed && typeof parsed === "object" ? parsed : {};
  return { ok: response.ok, code: response.status, body: answerBody, message: response.ok ? "" : detailText(answerBody.detail) };
}

// FastAPI sends `detail` as a string, or as a list of validation errors.
function detailText(detail) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((item) => (item && typeof item.msg === "string" ? item.msg : "")).filter(Boolean).join("; ");
  }
  return "";
}

export async function stream(onStatus, onDown, onOpen) {
  try {
    const response = await fetch(base + "events", { headers });
    if (!response.ok) throw new Error(`events ${response.status}`);
    if (onOpen) onOpen();
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        const chunk = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const data = chunk.split("\n").find((line) => line.startsWith("data:"));
        if (data) onStatus(JSON.parse(data.slice(5).trim()));
      }
    }
  } catch (err) {
    // fall through: the caller shows the connection as down
  }
  onDown();
}
