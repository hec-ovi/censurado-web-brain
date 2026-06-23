import { api as defaultApi } from "./api.js";
import { el } from "./components/el.js";
import { Health } from "./components/health.js";
import { PersonaList } from "./components/personaList.js";
import { PersonaForm } from "./components/personaForm.js";
import { RunPanel } from "./components/runPanel.js";

// Mount the author manager into `root`. `deps.api` is injectable so a test can
// mount the whole app against MSW or a stub; production passes nothing and the
// real /api client is used. Returns the live component handles for tests.
export function mountApp(root, deps = {}) {
  const api = deps.api || defaultApi;

  const health = Health({ api });
  const list = PersonaList({ api });
  const form = PersonaForm({ api, onCreated: () => list.reload() });
  const runs = RunPanel({ api });

  const header = el("header", { class: "app-header" }, [
    el("div", { class: "brand" }, [el("h1", {}, "Newsroom Brain"), el("span", { class: "subtitle" }, "author manager")]),
    health.element,
  ]);

  const newPersonaPanel = el("section", { class: "panel" }, [
    el("div", { class: "panel-head" }, [el("h2", {}, "New persona")]),
    form.element,
  ]);

  const grid = el("div", { class: "grid" }, [
    el("div", { class: "col" }, [newPersonaPanel, list.element]),
    el("div", { class: "col" }, [runs.element]),
  ]);

  root.replaceChildren(header, el("main", { class: "app-main" }, [grid]));

  health.check();
  list.reload();

  return { health, list, form, runs };
}
