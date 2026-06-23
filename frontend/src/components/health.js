import { el } from "./el.js";

// A small status badge that pings GET /health. It starts in "checking" and
// flips to "online" or "offline". `check()` is exposed so the caller decides
// when to poll (the app pings on mount).
export function Health({ api }) {
  const dot = el("span", { class: "health-dot" });
  const label = el("span", { class: "health-label" });
  const element = el("span", { class: "health", role: "status", "aria-live": "polite", title: "Brain API status" }, [
    dot,
    label,
  ]);

  function set(state, text) {
    element.dataset.state = state;
    dot.dataset.state = state;
    label.textContent = text;
  }
  set("checking", "checking");

  async function check() {
    set("checking", "checking");
    try {
      await api.health();
      set("online", "online");
    } catch {
      set("offline", "offline");
    }
  }

  return { element, check };
}
