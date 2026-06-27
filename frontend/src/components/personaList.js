import { el, clear, field, help, isSafeImageSrc } from "./el.js";
import { SECTIONS } from "./personaForm.js";

// The persona roster: a beat filter over GET /personas, rendering one card per
// author with an avatar (or initial fallback), beat badge, and who-i-am line.
// Each card carries per-author actions: an inline Edit form (PATCH the persona),
// a two-click Delete, and a Sources panel that links the author's source pool.
//
// `reload()` is exposed so the create form can refresh it after a synthesis.
// `onChanged` (optional) fires after any successful per-card mutation so a parent
// can refresh anything that depends on the persona set.

const LINK_HELP =
  "An author only researches the sources linked here. Cross-source corroboration across them is what " +
  "drives relevance: a claim that several linked outlets carry independently rises, an unsourced one does not. " +
  "Link a few trusted, independent outlets per author.";

export function PersonaList({ api, onChanged } = {}) {
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

  // A single persona card: avatar/initial, name + beat badge, who-i-am preview,
  // the per-card actions, and the inline panels (built lazily on first open so
  // the roster stays light and a hidden beat <select> doesn't shadow the visible
  // beat badge in queries).
  function card(persona) {
    const cardStatus = el("p", { class: "form-status", role: "status", "aria-live": "polite" });

    let editForm = null;
    let sources = null;

    const editBtn = el("button", { type: "button", class: "secondary" }, "Edit");
    editBtn.setAttribute("aria-expanded", "false");

    const sourcesBtn = el("button", { type: "button", class: "secondary" }, "Sources");
    sourcesBtn.setAttribute("aria-expanded", "false");

    // Two-click delete instead of window.confirm (a no-op under jsdom): the first
    // click arms the button, the second performs the delete.
    let armed = false;
    const deleteBtn = el("button", { type: "button", class: "secondary" }, "Delete");
    function disarmDelete() {
      armed = false;
      deleteBtn.textContent = "Delete";
      delete deleteBtn.dataset.confirm;
    }
    deleteBtn.addEventListener("click", () => {
      if (!armed) {
        armed = true;
        deleteBtn.textContent = "Confirm";
        deleteBtn.dataset.confirm = "true";
        return;
      }
      // On a failed delete (e.g. 409 persona_in_use) disarm so the next click
      // re-arms rather than silently re-firing the delete.
      act(deleteBtn, () => api.deletePersona(persona.id), cardStatus, disarmDelete);
    });

    const actions = el("div", { class: "persona-actions" }, [editBtn, sourcesBtn, deleteBtn]);

    const article = el("article", { class: "persona-card", dataset: { id: persona.id } }, [
      avatar(persona),
      el("div", { class: "persona-body" }, [
        el("div", { class: "persona-head" }, [
          el("h3", {}, persona.display_name),
          el("span", { class: "badge" }, persona.beat),
        ]),
        el("p", { class: "persona-who" }, persona.who_i_am || ""),
      ]),
      actions,
      cardStatus,
    ]);

    editBtn.addEventListener("click", () => {
      if (!editForm) {
        editForm = buildEditForm(persona, cardStatus);
        article.insertBefore(editForm, cardStatus);
      } else {
        editForm.hidden = !editForm.hidden;
      }
      editBtn.setAttribute("aria-expanded", editForm.hidden ? "false" : "true");
    });

    sourcesBtn.addEventListener("click", () => {
      if (!sources) {
        sources = buildSourcesPanel(persona);
        article.insertBefore(sources.panel, cardStatus);
        sources.load();
      } else {
        sources.panel.hidden = !sources.panel.hidden;
        if (!sources.panel.hidden) sources.load();
      }
      sourcesBtn.setAttribute("aria-expanded", sources.panel.hidden ? "false" : "true");
    });

    return article;
  }

  // The inline edit form: the voice fields plus beat/active/avatar. `sources` is
  // NOT here on purpose: linking is its own panel. The few-shot example lists are
  // JSON-array textareas, prefilled pretty-printed and parsed on save.
  function buildEditForm(persona, cardStatus) {
    const id = persona.id;
    const displayName = el("input", { type: "text", id: `pe-${id}-name`, value: persona.display_name || "" });
    const beat = el("select", { id: `pe-${id}-beat` }, SECTIONS.map((s) => el("option", { value: s }, s)));
    beat.value = persona.beat || SECTIONS[0];
    const language = el("input", { type: "text", id: `pe-${id}-language`, value: persona.language || "" });
    const avatarPath = el("input", { type: "text", id: `pe-${id}-avatar`, value: persona.avatar_path || "" });
    const active = el("input", { type: "checkbox", id: `pe-${id}-active` });
    active.checked = !!persona.active;
    const whoIAm = el("textarea", { id: `pe-${id}-who`, rows: "3" }, persona.who_i_am || "");
    const about = el("textarea", { id: `pe-${id}-about`, rows: "3" }, persona.about || "");
    const style = el("textarea", { id: `pe-${id}-style`, rows: "3" }, persona.style || "");
    const fewPos = el("textarea", { id: `pe-${id}-pos`, rows: "4" }, JSON.stringify(persona.few_shots_pos || [], null, 2));
    const fewNeg = el("textarea", { id: `pe-${id}-neg`, rows: "4" }, JSON.stringify(persona.few_shots_neg || [], null, 2));

    const save = el("button", { type: "submit" }, "Save");
    const cancel = el("button", { type: "button", class: "secondary" }, "Cancel");

    const form = el("form", { class: "persona-edit" }, [
      field("Display name", displayName, `pe-${id}-name`),
      field("Beat", beat, `pe-${id}-beat`),
      field("Language", language, `pe-${id}-language`),
      field("Avatar path", avatarPath, `pe-${id}-avatar`),
      field("Active", active, `pe-${id}-active`),
      field("Who I am", whoIAm, `pe-${id}-who`),
      field("About", about, `pe-${id}-about`),
      field("Style", style, `pe-${id}-style`),
      field("Positive examples (JSON array)", fewPos, `pe-${id}-pos`),
      field("Negative examples (JSON array)", fewNeg, `pe-${id}-neg`),
      el("div", { class: "persona-actions" }, [save, cancel]),
    ]);
    cancel.addEventListener("click", () => {
      form.hidden = true;
    });

    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      // Parse the two example lists first; a bad list aborts before any request.
      const pos = parseJsonArray(fewPos.value, "Positive examples");
      if (pos.error) return setStatus(cardStatus, "error", pos.error);
      const neg = parseJsonArray(fewNeg.value, "Negative examples");
      if (neg.error) return setStatus(cardStatus, "error", neg.error);

      const body = {
        display_name: displayName.value.trim(),
        beat: beat.value,
        language: language.value.trim(),
        avatar_path: avatarPath.value.trim(),
        active: active.checked,
        who_i_am: whoIAm.value,
        about: about.value,
        style: style.value,
        few_shots_pos: pos.value,
        few_shots_neg: neg.value,
      };

      save.disabled = true;
      setStatus(cardStatus, "pending", "Saving...");
      try {
        await api.patchPersona(id, body);
        await load();
        if (onChanged) onChanged();
      } catch (err) {
        save.disabled = false;
        setStatus(cardStatus, "error", `Could not save (${err.code}): ${err.message}`);
      }
    });
    return form;
  }

  // The inline source-linking panel: every portal as a checkbox, the author's
  // currently-linked ids pre-checked. Save REPLACES the whole set (unchecking all
  // sends []). Loads the live portal list and the persona's stored ids together.
  function buildSourcesPanel(persona) {
    const id = persona.id;
    const checksWrap = el("div", { class: "persona-source-checks" });
    const status = el("p", { class: "form-status", role: "status", "aria-live": "polite" });
    const save = el("button", { type: "button" }, "Save sources");

    const panel = el("div", { class: "persona-sources" }, [
      el("div", { class: "panel-head" }, [el("h4", {}, "Linked sources"), help(LINK_HELP)]),
      checksWrap,
      el("div", { class: "persona-actions" }, [save]),
      status,
    ]);

    let boxes = [];

    async function loadSources() {
      clear(checksWrap);
      checksWrap.append(el("p", { class: "muted" }, "Loading sources..."));
      boxes = [];
      try {
        // Pull a large page so every portal is a checkbox: Save derives the linked
        // set from the rendered boxes, so an unrendered linked portal would be dropped.
        const [portalsData, linked] = await Promise.all([
          api.listPortals({ limit: 1000 }),
          api.getPersonaSources(id),
        ]);
        const portals = (portalsData && portalsData.portals) || [];
        const selected = new Set((linked && linked.sources) || []);
        clear(checksWrap);
        if (!portals.length) {
          save.disabled = true;
          checksWrap.append(el("p", { class: "muted" }, "Add sources in the Sources tab first."));
          return;
        }
        save.disabled = false;
        const tbody = el("tbody");
        for (const portal of portals) {
          const boxId = `ps-${id}-${portal.id}`;
          const box = el("input", { type: "checkbox", id: boxId });
          box.checked = selected.has(portal.id);
          boxes.push({ id: portal.id, box });
          const desc = (portal.description || "").trim();
          tbody.append(
            el("tr", { class: "link-row" }, [
              el("td", { class: "link-check" }, box),
              el("td", { class: "link-portal" }, el("label", { for: boxId }, portal.domain)),
              el("td", { class: "link-desc", title: desc }, desc || "—"),
            ]),
          );
        }
        const table = el("table", { class: "link-table" }, [
          el("thead", {}, el("tr", {}, [
            el("th", { class: "link-check" }, ""),
            el("th", {}, "Fuente"),
            el("th", {}, "Descripción"),
          ])),
          tbody,
        ]);
        checksWrap.append(el("div", { class: "link-table-wrap" }, table));
      } catch (err) {
        clear(checksWrap);
        checksWrap.append(el("p", { class: "error", role: "alert" }, `Could not load sources: ${err.message}`));
      }
    }

    save.addEventListener("click", async () => {
      const checked = boxes.filter((b) => b.box.checked).map((b) => b.id);
      save.disabled = true;
      setStatus(status, "pending", "Saving...");
      try {
        await api.putPersonaSources(id, checked);
        setStatus(status, "done", "Sources updated.");
        await loadSources();
        if (onChanged) onChanged();
      } catch (err) {
        save.disabled = false;
        setStatus(status, "error", `Could not save sources (${err.code}): ${err.message}`);
      }
    });

    return { panel, load: loadSources };
  }

  // Run a card action, then reload the roster. On success the card is replaced by
  // the fresh render, so the button is discarded; on failure it re-enables and the
  // card surfaces the brain's code/detail (e.g. persona_in_use on delete).
  async function act(button, run, statusNode, onFail) {
    button.disabled = true;
    try {
      await run();
      await load();
      if (onChanged) onChanged();
    } catch (err) {
      button.disabled = false;
      if (onFail) onFail();
      setStatus(statusNode, "error", `Action failed (${err.code}): ${err.message}`);
    }
  }

  return { element, reload: load };
}

function avatar(persona) {
  if (isSafeImageSrc(persona.avatar_path)) {
    return el("img", { class: "avatar", src: persona.avatar_path, alt: persona.display_name });
  }
  const initial = (persona.display_name || "?").trim().charAt(0).toUpperCase() || "?";
  return el("div", { class: "avatar avatar--fallback", "aria-hidden": "true" }, initial);
}

// Parse a JSON-array textarea. An empty field means an empty list (not an error),
// so an operator can clear the examples. Returns { value } on a valid array, or
// { error } with a message when the text is not valid JSON or not an array.
function parseJsonArray(text, label) {
  if (!text.trim()) return { value: [] };
  let value;
  try {
    value = JSON.parse(text);
  } catch {
    return { error: `${label} must be a valid JSON array.` };
  }
  if (!Array.isArray(value)) return { error: `${label} must be a JSON array.` };
  return { value };
}

function setStatus(node, state, text) {
  node.dataset.state = state;
  node.textContent = text;
  const assertive = state === "error";
  node.setAttribute("role", assertive ? "alert" : "status");
  node.setAttribute("aria-live", assertive ? "assertive" : "polite");
}
