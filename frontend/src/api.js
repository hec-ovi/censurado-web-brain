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

export const api = {
  health: () => request("/health"),
  listPersonas: (beat) => request("/personas" + query({ beat })),
  getPersona: (id) => request(`/personas/${encodeURIComponent(id)}`),
  createPersona: (seed) => request("/personas", { method: "POST", body: JSON.stringify(seed) }),
  getJob: (jobId) => request(`/personas/jobs/${encodeURIComponent(jobId)}`),
  createRun: (req) => request("/runs", { method: "POST", body: JSON.stringify(req) }),
  getRun: (runId) => request(`/runs/${encodeURIComponent(runId)}`),
};
