import { api as defaultApi } from "./api.js";
import { el, help } from "./components/el.js";
import { Health } from "./components/health.js";
import { BackendStatus } from "./components/backendStatus.js";
import { PersonaList } from "./components/personaList.js";
import { PersonaForm } from "./components/personaForm.js";
import { SourcesPanel } from "./components/sourcesPanel.js";
import { RunPanel } from "./components/runPanel.js";
import { EditorialPanel } from "./components/editorialPanel.js";
import { PromptsPanel } from "./components/promptsPanel.js";

const THEME_KEY = "admin-theme";

function readTheme() {
  try {
    return localStorage.getItem(THEME_KEY) || "system";
  } catch {
    return "system";
  }
}

function applyTheme(value) {
  const theme = value === "light" || value === "dark" ? value : "system";
  document.documentElement.dataset.theme = theme;
  return theme;
}

function saveTheme(value) {
  const theme = applyTheme(value);
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* localStorage can be unavailable in hardened browsers. */
  }
  return theme;
}

function ThemeControl() {
  const buttons = ["system", "light", "dark"].map((theme) =>
    el(
      "button",
      {
        type: "button",
        class: "theme-option",
        dataset: { theme },
        "aria-pressed": "false",
        onClick: () => setTheme(theme),
      },
      theme[0].toUpperCase() + theme.slice(1),
    ),
  );

  function setTheme(theme) {
    const active = saveTheme(theme);
    buttons.forEach((button) => {
      button.setAttribute("aria-pressed", button.dataset.theme === active ? "true" : "false");
    });
  }

  const initial = applyTheme(readTheme());
  buttons.forEach((button) => {
    button.setAttribute("aria-pressed", button.dataset.theme === initial ? "true" : "false");
  });
  return el("div", { class: "theme-switch", role: "group", "aria-label": "Theme" }, buttons);
}

// Mount the console into `root`. `deps.api` is injectable so a test can mount
// the whole app against MSW or a stub; production passes nothing and the real
// /api client is used. Returns the live component handles for tests.
//
// The console is a six-tab ARIA tablist. Switching tabs only toggles `hidden`
// (hide, not unmount), so controls in inactive tabs stay in the DOM and remain
// findable by tests and assistive tech instead of being rebuilt. Every tab is a
// real panel; none are placeholders.
export function mountApp(root, deps = {}) {
  const api = deps.api || defaultApi;

  const health = Health({ api });
  const backend = BackendStatus({ api });
  const list = PersonaList({ api });
  const form = PersonaForm({ api, onCreated: () => list.reload() });
  const sources = SourcesPanel({ api });
  const runs = RunPanel({ api });
  const editorial = EditorialPanel({ api });
  const prompts = PromptsPanel({ api });

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
    { id: "sources", label: "Sources", content: [sources.element] },
    { id: "runs", label: "Runs", content: [runs.element] },
    { id: "editorial", label: "Editorial", content: [editorial.element] },
    { id: "prompts", label: "Prompts", content: [prompts.element] },
    { id: "status", label: "Status", content: [healthPanel, backend.element] },
  ];

  const tabButtons = [];
  const panels = [];
  const tablist = el("div", { class: "tablist", role: "tablist", "aria-label": "Console sections" });
  const pageTitle = el("h1", { class: "page-title" }, tabs[0].label);
  let active = 0;

  function select(i) {
    active = i;
    pageTitle.textContent = tabs[i].label;
    tabButtons.forEach((btn, j) => {
      const on = j === i;
      btn.setAttribute("aria-selected", on ? "true" : "false");
      btn.toggleAttribute("aria-current", on);
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

  const sidebar = el("aside", { class: "app-sidebar", "aria-label": "Primary" }, [
    el("div", { class: "brand" }, [
      el("span", { class: "brand-mark" }, "AP"),
      el("span", { class: "brand-name" }, "Admin Panel"),
    ]),
    tablist,
  ]);

  const topbar = el("header", { class: "app-topbar" }, [
    el("div", {}, [el("p", { class: "kicker" }, "Control panel"), pageTitle]),
    el("div", { class: "topbar-actions" }, [ThemeControl()]),
  ]);

  root.replaceChildren(
    el("div", { class: "app-shell" }, [
      sidebar,
      el("div", { class: "app-workspace" }, [topbar, el("main", { class: "app-main" }, panels)]),
    ]),
  );

  health.check();
  backend.refresh();
  list.reload();
  sources.reload();
  runs.loadAuthors();
  runs.reloadHistory();
  editorial.reload();
  prompts.reload();

  return { health, backend, list, form, sources, runs, editorial, prompts, tabs: tabButtons, panels, select };
}
