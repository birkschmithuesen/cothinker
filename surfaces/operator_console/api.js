// Operator contract client (issue #159): Bearer token header on every call,
// never in a URL; the push channel is read with fetch + a stream reader,
// because EventSource cannot carry the header (as surfaces/operator/examples/minimal).
const params = new URLSearchParams(location.search);
const base = params.get("api") || "/operator/";
const headers = { Authorization: "Bearer " + (params.get("token") || "") };

export async function getJson(path) {
  const response = await fetch(base + path, { headers });
  if (!response.ok) throw new Error(`${path} ${response.status}`);
  return response.json();
}

export async function post(path, body) {
  const response = await fetch(base + path, {
    method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify(body),
  });
  return { ok: response.ok, code: response.status, body: await response.json() };
}

export async function stream(onStatus, onDown) {
  try {
    const response = await fetch(base + "events", { headers });
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
