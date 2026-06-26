import { el, clear, field, help } from "./el.js";

// The Sources tab: a portals manager. A portal is a source of record (a news
// outlet, by its domain/homepage/feeds) the newsroom is allowed to draw from.
// The panel is a create form on top and a roster of source cards below; each
// card toggles enabled, edits the non-domain fields, or deletes the source.
//
// `onChanged` (optional) fires after any successful mutation so a parent can
// refresh anything that depends on the source set. `reload()` re-fetches and
// re-renders the list, and is exposed so the app can prime it on mount.

const SOURCE_HELP =
  "A source (portal) is a news outlet the newsroom may draw from: its domain, homepage, and feeds. " +
  "Add the outlets you trust here; disabled sources stay on file but are skipped.";

const OWNERSHIP_HELP =
  "Co-owned mastheads collapse to one independent source for the corroboration gate. Give shared owners the " +
  "same group so two papers under one owner count once, not twice, when a claim needs a second source.";

export function SourcesPanel({ api, onChanged } = {}) {
  // --- Create form -------------------------------------------------------
  const domainInput = el("input", { type: "text", id: "sp-domain", autocomplete: "off" });
  const descInput = el("input", { type: "text", id: "sp-desc", autocomplete: "off" });
  const ownershipInput = el("input", { type: "text", id: "sp-ownership", autocomplete: "off" });
  const homepageInput = el("input", { type: "url", id: "sp-homepage", placeholder: "https://example.com" });
  const feedsInput = el("input", { type: "text", id: "sp-feeds", placeholder: "https://example.com/feed.xml, ..." });
  const feedTypeInput = el("input", { type: "text", id: "sp-feed-type", value: "auto" });
  const languageInput = el("input", { type: "text", id: "sp-language", value: "es" });
  const enabledCheck = el("input", { type: "checkbox", id: "sp-enabled", checked: true });
  const submit = el("button", { type: "submit" }, "Add source");
  const createStatus = el("p", { class: "form-status", role: "status", "aria-live": "polite" });

  const createRefs = {
    homepage: homepageInput,
    description: descInput,
    ownership: ownershipInput,
    feeds: feedsInput,
    feedType: feedTypeInput,
    language: languageInput,
    enabled: enabledCheck,
  };

  const form = el("form", { class: "source-form" }, [
    field("Domain", domainInput, "sp-domain"),
    field("Description", descInput, "sp-desc"),
    helpField("Ownership group", ownershipInput, "sp-ownership", OWNERSHIP_HELP),
    field("Homepage", homepageInput, "sp-homepage"),
    field("Feed URLs (comma separated)", feedsInput, "sp-feeds"),
    field("Feed type", feedTypeInput, "sp-feed-type"),
    field("Language", languageInput, "sp-language"),
    field("Enabled", enabledCheck, "sp-enabled"),
    submit,
    createStatus,
  ]);
  form.addEventListener("submit", onCreate);

  const createPanel = el("section", { class: "panel" }, [
    el("div", { class: "panel-head" }, [el("h2", {}, "Add source"), help(SOURCE_HELP)]),
    form,
  ]);

  // --- List --------------------------------------------------------------
  const listEl = el("div", { class: "source-list" });
  const listPanel = el("section", { class: "panel" }, [
    el("div", { class: "panel-head" }, [el("h2", {}, "Sources")]),
    listEl,
  ]);

  const element = el("div", { class: "sources" }, [createPanel, listPanel]);

  async function onCreate(event) {
    event.preventDefault();
    domainInput.setAttribute("aria-invalid", "false");
    const domain = domainInput.value.trim();
    if (!domain) {
      domainInput.setAttribute("aria-invalid", "true");
      domainInput.focus();
      setStatus(createStatus, "error", "Domain is required.");
      return;
    }
    const body = collectEditable(createRefs);
    body.domain = domain;

    setBusy(submit, form, true);
    setStatus(createStatus, "pending", "Adding source...");
    try {
      await api.createPortal(body);
      form.reset();
      domainInput.setAttribute("aria-invalid", "false");
      setStatus(createStatus, "done", `Added ${domain}.`);
      await reload();
      if (onChanged) onChanged();
    } catch (err) {
      setStatus(createStatus, "error", `Could not add source (${err.code}): ${err.message}`);
    } finally {
      setBusy(submit, form, false);
    }
  }

  async function reload() {
    clear(listEl);
    listEl.append(el("p", { class: "muted" }, "Loading sources..."));
    try {
      const data = await api.listPortals();
      const portals = (data && data.portals) || [];
      clear(listEl);
      if (!portals.length) {
        listEl.append(el("p", { class: "muted" }, "No sources yet."));
        return;
      }
      for (const portal of portals) listEl.append(card(portal));
    } catch (err) {
      clear(listEl);
      listEl.append(el("p", { class: "error", role: "alert" }, `Could not load sources: ${err.message}`));
    }
  }

  // A single source card: domain heading, status pill, optional ownership
  // badge and description, the per-card actions, and a hidden inline edit form.
  function card(portal) {
    const enabled = !!portal.enabled;
    const pill = el("span", { class: "status", dataset: { state: enabled ? "online" : "offline" } }, enabled ? "online" : "offline");
    const head = el("div", { class: "source-head" }, [
      el("h3", {}, portal.domain),
      pill,
      portal.ownership_group ? el("span", { class: "badge" }, portal.ownership_group) : null,
    ]);
    const desc = portal.description ? el("p", { class: "source-desc" }, portal.description) : null;
    const cardStatus = el("p", { class: "form-status", role: "status", "aria-live": "polite" });

    const toggleBtn = el("button", { type: "button" }, enabled ? "Disable" : "Enable");
    toggleBtn.addEventListener("click", () =>
      act(toggleBtn, () => (enabled ? api.disablePortal(portal.id) : api.enablePortal(portal.id)), cardStatus));

    // Two-click delete instead of window.confirm (which is a no-op under jsdom):
    // the first click arms the button, the second performs the delete.
    let armed = false;
    const deleteBtn = el("button", { type: "button", class: "secondary" }, "Delete");
    deleteBtn.addEventListener("click", () => {
      if (!armed) {
        armed = true;
        deleteBtn.textContent = "Confirm";
        deleteBtn.dataset.confirm = "true";
        return;
      }
      act(deleteBtn, () => api.deletePortal(portal.id), cardStatus);
    });

    const editForm = buildEditForm(portal, cardStatus);
    const editBtn = el("button", { type: "button", class: "secondary" }, "Edit");
    editBtn.addEventListener("click", () => {
      editForm.hidden = !editForm.hidden;
      editBtn.setAttribute("aria-expanded", editForm.hidden ? "false" : "true");
    });
    editBtn.setAttribute("aria-expanded", "false");

    const actions = el("div", { class: "source-actions" }, [toggleBtn, editBtn, deleteBtn]);
    return el("article", { class: "source-card", dataset: { id: portal.id } }, [head, desc, actions, editForm, cardStatus]);
  }

  // The inline edit form: every editable field EXCEPT domain (PATCH refuses it).
  // Prefilled from the portal so an edit starts from its current values.
  function buildEditForm(portal, cardStatus) {
    const id = portal.id;
    const homepage = el("input", { type: "url", id: `se-${id}-homepage`, value: portal.homepage || "" });
    const description = el("input", { type: "text", id: `se-${id}-desc`, value: portal.description || "" });
    const ownership = el("input", { type: "text", id: `se-${id}-ownership`, value: portal.ownership_group || "" });
    const feeds = el("input", { type: "text", id: `se-${id}-feeds`, value: (portal.feed_urls || []).join(", ") });
    const feedType = el("input", { type: "text", id: `se-${id}-feed-type`, value: portal.feed_type || "auto" });
    const language = el("input", { type: "text", id: `se-${id}-language`, value: portal.language || "es" });
    const enabled = el("input", { type: "checkbox", id: `se-${id}-enabled` });
    enabled.checked = !!portal.enabled;
    const refs = { homepage, description, ownership, feeds, feedType, language, enabled };

    const save = el("button", { type: "submit" }, "Save");
    const cancel = el("button", { type: "button", class: "secondary" }, "Cancel");

    const editForm = el("form", { class: "source-edit" }, [
      field("Description", description, `se-${id}-desc`),
      helpField("Ownership group", ownership, `se-${id}-ownership`, OWNERSHIP_HELP),
      field("Homepage", homepage, `se-${id}-homepage`),
      field("Feed URLs (comma separated)", feeds, `se-${id}-feeds`),
      field("Feed type", feedType, `se-${id}-feed-type`),
      field("Language", language, `se-${id}-language`),
      field("Enabled", enabled, `se-${id}-enabled`),
      el("div", { class: "source-actions" }, [save, cancel]),
    ]);
    editForm.hidden = true;
    cancel.addEventListener("click", () => {
      editForm.hidden = true;
    });
    editForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const body = collectEditable(refs, { allowClear: true });
      save.disabled = true;
      setStatus(cardStatus, "pending", "Saving...");
      try {
        await api.patchPortal(id, body);
        await reload();
        if (onChanged) onChanged();
      } catch (err) {
        save.disabled = false;
        setStatus(cardStatus, "error", `Could not save (${err.code}): ${err.message}`);
      }
    });
    return editForm;
  }

  // Run a card action, then reload the list. On success the card is replaced by
  // the fresh render, so the button is discarded; on failure it re-enables and
  // the card surfaces the brain's error.
  async function act(button, run, statusNode) {
    button.disabled = true;
    try {
      await run();
      await reload();
      if (onChanged) onChanged();
    } catch (err) {
      button.disabled = false;
      setStatus(statusNode, "error", `Action failed (${err.code}): ${err.message}`);
    }
  }

  return { element, reload };
}

// The editable subset of the portal, shared by create and edit. `enabled` is
// always sent as the checkbox state; `feed_urls` is split on commas/newlines,
// trimmed, emptied. The free-text fields (homepage, description, ownership_group,
// feed_urls) behave differently per mode: on CREATE (allowClear=false) a blank is
// omitted so the server default applies and never clobbers; on EDIT
// (allowClear=true) a blank is sent as "" / [] so an operator can CLEAR a field
// (e.g. un-group a co-owned masthead). `feed_type`/`language` are enum-ish and
// never sent blank in either mode, so they can't be cleared to an invalid value.
// The caller adds `domain` for a create.
function collectEditable(refs, { allowClear = false } = {}) {
  const body = {};
  const homepage = refs.homepage.value.trim();
  if (homepage || allowClear) body.homepage = homepage;
  const description = refs.description.value.trim();
  if (description || allowClear) body.description = description;
  const ownership = refs.ownership.value.trim();
  if (ownership || allowClear) body.ownership_group = ownership;
  const feeds = splitList(refs.feeds.value);
  if (feeds.length || allowClear) body.feed_urls = feeds;
  const feedType = refs.feedType.value.trim();
  if (feedType) body.feed_type = feedType;
  const language = refs.language.value.trim();
  if (language) body.language = language;
  body.enabled = refs.enabled.checked;
  return body;
}

function splitList(value) {
  return value
    .split(/[,\n]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

// A labeled control with an inline (?) help marker. The help sits beside the
// label (not inside it) so the control's accessible name stays the bare label.
function helpField(labelText, control, id, helpText) {
  return el("div", { class: "field" }, [
    el("span", { class: "field-label" }, [el("label", { for: id }, labelText), help(helpText)]),
    control,
  ]);
}

function setBusy(button, formEl, busy) {
  button.disabled = busy;
  formEl.setAttribute("aria-busy", busy ? "true" : "false");
}

function setStatus(node, state, text) {
  node.dataset.state = state;
  node.textContent = text;
  const assertive = state === "error";
  node.setAttribute("role", assertive ? "alert" : "status");
  node.setAttribute("aria-live", assertive ? "assertive" : "polite");
}
