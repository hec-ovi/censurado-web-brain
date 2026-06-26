import { api as defaultApi } from "./api.js";
import { el, help } from "./components/el.js";
import { Health } from "./components/health.js";
import { BackendStatus } from "./components/backendStatus.js";
import { PersonaList } from "./components/personaList.js";
import { PersonaForm } from "./components/personaForm.js";
import { RunPanel } from "./components/runPanel.js";

// Mount the console into `root`. `deps.api` is injectable so a test can mount
// the whole app against MSW or a stub; production passes nothing and the real
// /api client is used. Returns the live component handles for tests.
//
// The console is a six-tab ARIA tablist. Switching tabs only toggles `hidden`
// (hide, not unmount), so controls in inactive tabs stay in the DOM and remain
// findable by tests and assistive tech instead of being rebuilt. Sources,
// Editorial, and Prompts are placeholders here; they get filled in later phases.
export function mountApp(root, deps = {}) {
  const api = deps.api || defaultApi;

  const health = Health({ api });
  const backend = BackendStatus({ api });
  const list = PersonaList({ api });
  const form = PersonaForm({ api, onCreated: () => list.reload() });
  const runs = RunPanel({ api });

  const newPersonaPanel = el("section", { class: "panel" }, [
    el("div", { class: "panel-head" }, [
      el("h2", {}, "New persona"),
      help("Synthesize an author persona from a short seed. The new author joins the roster below."),
    ]),
    form.element,
  ]);

  const healthPanel = el("section", { class: "panel" }, [
    el("div", { class: "panel-head" }, [
      el("h2", {}, "Health"),
      help("Live ping of the brain API behind nginx. Green means the API answered."),
    ]),
    health.element,
  ]);

  const tabs = [
    { id: "authors", label: "Authors", content: [newPersonaPanel, list.element] },
    { id: "sources", label: "Sources", content: [placeholder("Sources")] },
    { id: "runs", label: "Runs", content: [runs.element] },
    { id: "editorial", label: "Editorial", content: [placeholder("Editorial")] },
    { id: "prompts", label: "Prompts", content: [placeholder("Prompts")] },
    { id: "status", label: "Status", content: [healthPanel, backend.element] },
  ];

  const tabButtons = [];
  const panels = [];
  const tablist = el("div", { class: "tablist", role: "tablist", "aria-label": "Console sections" });
  let active = 0;

  function select(i) {
    active = i;
    tabButtons.forEach((btn, j) => {
      const on = j === i;
      btn.setAttribute("aria-selected", on ? "true" : "false");
      btn.tabIndex = on ? 0 : -1;
    });
    panels.forEach((p, j) => {
      p.hidden = j !== i;
    });
    tabButtons[i].focus();
  }

  function onTabKey(event, i) {
    const last = tabs.length - 1;
    let next = null;
    if (event.key === "ArrowRight") next = i === last ? 0 : i + 1;
    else if (event.key === "ArrowLeft") next = i === 0 ? last : i - 1;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = last;
    if (next != null) {
      event.preventDefault();
      select(next);
    }
  }

  tabs.forEach((tab, i) => {
    const tabId = `tab-${tab.id}`;
    const panelId = `panel-${tab.id}`;
    const button = el(
      "button",
      {
        class: "tab",
        role: "tab",
        type: "button",
        id: tabId,
        "aria-controls": panelId,
        "aria-selected": i === 0 ? "true" : "false",
        tabindex: i === 0 ? "0" : "-1",
        onClick: () => select(i),
        onKeydown: (event) => onTabKey(event, i),
      },
      tab.label,
    );
    const panel = el(
      "div",
      { class: "tab-panel", role: "tabpanel", id: panelId, "aria-labelledby": tabId, tabindex: "0" },
      el("div", { class: "tab-stack" }, tab.content),
    );
    panel.hidden = i !== 0;
    tabButtons.push(button);
    panels.push(panel);
    tablist.append(button);
  });

  const header = el("header", { class: "app-header" }, [
    el("div", { class: "brand" }, [
      el("h1", {}, "Newsroom Brain"),
      el("span", { class: "subtitle" }, "console"),
    ]),
  ]);

  root.replaceChildren(header, el("main", { class: "app-main" }, [tablist, ...panels]));

  health.check();
  backend.refresh();
  list.reload();

  return { health, backend, list, form, runs, tabs: tabButtons, panels, select };
}

// A stand-in panel for a section that lands in a later phase: just the heading
// and a muted note, so the tab is navigable and labeled before it has content.
function placeholder(title) {
  return el("section", { class: "panel" }, [
    el("h2", {}, title),
    el("p", { class: "muted" }, "Built in a later step."),
  ]);
}
