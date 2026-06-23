import { el, clear } from "./el.js";
import { SECTIONS } from "./personaForm.js";

// The persona roster: a beat filter over GET /personas, rendering one card per
// author with an avatar (or initial fallback), beat badge, and who-i-am line.
// `reload()` is exposed so the create form can refresh it after a synthesis.
export function PersonaList({ api } = {}) {
  const filter = el("select", { class: "list-filter", id: "pl-filter", "aria-label": "Filter personas by beat" }, [
    el("option", { value: "" }, "All beats"),
    ...SECTIONS.map((s) => el("option", { value: s }, s)),
  ]);
  const listEl = el("div", { class: "persona-list" });
  const element = el("section", { class: "panel" }, [
    el("div", { class: "panel-head" }, [el("h2", {}, "Personas"), filter]),
    listEl,
  ]);

  filter.addEventListener("change", load);

  async function load() {
    clear(listEl);
    listEl.append(el("p", { class: "muted" }, "Loading personas..."));
    try {
      const data = await api.listPersonas(filter.value || null);
      const personas = (data && data.personas) || [];
      clear(listEl);
      if (!personas.length) {
        listEl.append(el("p", { class: "muted" }, "No personas yet. Create one to get started."));
        return;
      }
      for (const persona of personas) listEl.append(card(persona));
    } catch (err) {
      clear(listEl);
      listEl.append(el("p", { class: "error", role: "alert" }, `Could not load personas: ${err.message}`));
    }
  }

  return { element, reload: load };
}

function card(persona) {
  return el("article", { class: "persona-card", dataset: { id: persona.id } }, [
    avatar(persona),
    el("div", { class: "persona-body" }, [
      el("div", { class: "persona-head" }, [
        el("h3", {}, persona.display_name),
        el("span", { class: "badge" }, persona.beat),
      ]),
      el("p", { class: "persona-who" }, persona.who_i_am || ""),
    ]),
  ]);
}

function avatar(persona) {
  if (isSafeImageSrc(persona.avatar_path)) {
    return el("img", { class: "avatar", src: persona.avatar_path, alt: persona.display_name });
  }
  const initial = (persona.display_name || "?").trim().charAt(0).toUpperCase() || "?";
  return el("div", { class: "avatar avatar--fallback", "aria-hidden": "true" }, initial);
}

// Only render an avatar src we trust: a same-origin path or an explicit http(s)
// or data:image URL. Anything else (a javascript: or other odd scheme) falls
// back to the initial avatar.
function isSafeImageSrc(value) {
  if (!value || typeof value !== "string") return false;
  return value.startsWith("/") || /^https?:\/\//i.test(value) || /^data:image\//i.test(value);
}
