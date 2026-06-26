import { el, clear } from "./el.js";

// A diagnostics panel for the publishing backend the brain talks to. It pings
// GET /status/backend and renders the connection flags: the configured base
// URL, whether the backend is reachable and the brain is authorized against it,
// how many authors it sees, and the last status code / detail. `reachable` and
// `authorized` carry a data-state (online/offline) so they read like the health
// dot. `refresh()` is exposed so the caller decides when to poll (the app pings
// on mount).
export function BackendStatus({ api }) {
  const body = el("div", { class: "backend-status" });
  const element = el("section", { class: "panel" }, [
    el("div", { class: "panel-head" }, [el("h2", {}, "Backend")]),
    body,
  ]);

  function flagRow(label, value, state) {
    const valueEl = el("span", { class: "backend-value" }, value);
    if (state != null) valueEl.dataset.state = state ? "online" : "offline";
    return el("div", { class: "backend-row" }, [el("span", { class: "backend-label" }, label), valueEl]);
  }

  function render(status) {
    clear(body);
    body.append(
      flagRow("backend_base_url", status.backend_base_url || "(unset)"),
      flagRow("token_configured", status.token_configured ? "yes" : "no", !!status.token_configured),
      flagRow("reachable", status.reachable ? "yes" : "no", !!status.reachable),
      flagRow("authorized", status.authorized ? "yes" : "no", !!status.authorized),
      flagRow("author_count", status.author_count != null ? String(status.author_count) : "-"),
      flagRow("status_code", status.status_code != null ? String(status.status_code) : "-"),
      flagRow("detail", status.detail || ""),
    );
  }

  async function refresh() {
    clear(body);
    body.append(el("p", { class: "muted" }, "Checking backend..."));
    try {
      render(await api.getBackendStatus());
    } catch (err) {
      clear(body);
      body.append(el("p", { class: "error", role: "alert" }, `Could not load backend status: ${err.message}`));
    }
  }

  return { element, refresh };
}
