import { el } from "./el.js";
import { t } from "./i18n.js";

// A small status badge that pings GET /health. It starts in "checking" and
// flips to "online" or "offline". `check()` is exposed so the caller decides
// when to poll (the app pings on mount).
export function Health({ api }) {
  const dot = el("span", { class: "health-dot" });
  const label = el("span", { class: "health-label" });
  const element = el("span", { class: "health", role: "status", "aria-live": "polite", title: t("Brain API status") }, [
    dot,
    label,
  ]);

  function set(state, text) {
    element.dataset.state = state;
    dot.dataset.state = state;
    label.textContent = text;
  }
  set("checking", t("checking"));

  async function check() {
    set("checking", t("checking"));
    try {
      await api.health();
      set("online", t("online"));
    } catch {
      set("offline", t("offline"));
    }
  }

  return { element, check };
}
