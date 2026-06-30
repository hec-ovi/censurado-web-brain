// The brain HTTP client. Every call is same-origin under /api, which nginx
// reverse-proxies to the brain, so this module has no environment branches and
// no test-only code path: tests intercept fetch at the network layer (MSW).
//
// Errors are normalized to an Error carrying { code, status, body } so the UI
// can show the brain's problem+json `code`/`detail` instead of a bare status.

const BASE = "/api";

// Resolve to an absolute URL against the current origin. In the browser this is
// the page origin (nginx serves the app and proxies /api to the brain); under
// jsdom in tests it is the configured document origin. Same code path in both,
// and Node's fetch (which rejects relative URLs) gets a parseable URL.
function toUrl(path) {
  const origin = (globalThis.location && globalThis.location.origin) || "http://localhost";
  return new URL(BASE + path, origin);
}

async function request(path, options = {}) {
  const res = await fetch(toUrl(path), {
    headers: { "content-type": "application/json", ...(options.headers || {}) },
    ...options,
  });

  const text = await res.text();
  let body = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = { raw: text };
    }
  }

  if (!res.ok) {
    const code = (body && body.code) || `http_${res.status}`;
    const detail = (body && body.detail) || res.statusText || code;
    const err = new Error(detail);
    err.code = code;
    err.status = res.status;
    err.body = body;
    throw err;
  }
  return body;
}

function query(params) {
  const usable = Object.entries(params).filter(([, v]) => v != null && v !== "");
  if (!usable.length) return "";
  return "?" + usable.map(([k, v]) => `${k}=${encodeURIComponent(v)}`).join("&");
}

// Shorthands for the verbs, so each method reads as one line.
function post(path, body) {
  return request(path, { method: "POST", body: JSON.stringify(body) });
}
function patch(path, body) {
  return request(path, { method: "PATCH", body: JSON.stringify(body) });
}
function put(path, body) {
  return request(path, { method: "PUT", body: JSON.stringify(body) });
}
function del(path) {
  // The brain answers DELETE with 204 No Content; request() leaves body null.
  return request(path, { method: "DELETE" });
}
const enc = encodeURIComponent;

export const api = {
  health: () => request("/health"),

  // Personas
  listPersonas: (beat) => request("/personas" + query({ beat })),
  getPersona: (id) => request(`/personas/${enc(id)}`),
  createPersona: (seed) => post("/personas", seed), // 202-then-poll synthesis path
  createPersonaDirect: (body) => post("/personas/direct", body),
  patchPersona: (id, body) => patch(`/personas/${enc(id)}`, body),
  deletePersona: (id) => del(`/personas/${enc(id)}`),
  getJob: (jobId) => request(`/personas/jobs/${enc(jobId)}`),
  getPersonaSources: (id) => request(`/personas/${enc(id)}/sources`),
  putPersonaSources: (id, sources) => put(`/personas/${enc(id)}/sources`, { sources }),

  // Portals (sources of record the newsroom is allowed to draw from)
  listPortals: ({ enabled, limit, offset } = {}) => request("/portals" + query({ enabled, limit, offset })),
  getPortal: (id) => request(`/portals/${enc(id)}`),
  createPortal: (body) => post("/portals", body),
  patchPortal: (id, body) => patch(`/portals/${enc(id)}`, body),
  deletePortal: (id) => del(`/portals/${enc(id)}`),
  enablePortal: (id) => post(`/portals/${enc(id)}/enable`),
  disablePortal: (id) => post(`/portals/${enc(id)}/disable`),

  // Editorial style and location
  getStyle: () => request("/editorial/style"),
  postStyle: (body) => post("/editorial/style", body),
  getLexicon: () => request("/editorial/style/lexicon"),
  putLexicon: (body) => put("/editorial/style/lexicon", body),
  getSourcing: () => request("/editorial/style/sourcing"),
  putSourcing: (body) => put("/editorial/style/sourcing", body),
  getStyleVersions: ({ limit, offset } = {}) => request("/editorial/style/versions" + query({ limit, offset })),
  promoteStyleVersion: (version) => post(`/editorial/style/versions/${enc(version)}/promote`),
  getLocation: () => request("/editorial/location"),
  putLocation: (body) => put("/editorial/location", body),

  // Backend status
  getBackendStatus: () => request("/status/backend"),

  // Prompt templates
  listPrompts: () => request("/prompts"),
  getPrompt: (key) => request("/prompts/template" + query({ key })),
  getPromptVersions: ({ key, limit, offset } = {}) => request("/prompts/versions" + query({ key, limit, offset })),
  publishPrompt: (body) => post("/prompts/template", body),
  promotePromptVersion: (version) => post(`/prompts/versions/${enc(version)}/promote`),
};
