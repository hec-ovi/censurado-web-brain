import { el, clear, field, help } from "./el.js";

// The Prompts tab: the prompt-template editor. Each template is the stage
// instructions the brain runs (a journalist research step, an editor pass, ...),
// keyed by a path like "journalist/research.md". Templates are VERSIONED: every
// edit publishes a new immutable version and activates it, and an older version
// can be promoted back to roll the change back.
//
// The panel is three parts over one selected key:
//   1. Key selector - a <select> of every template key; picking one loads its
//      active body and version history. First load auto-selects the first key.
//   2. Editor       - the active version's body in a big textarea, an optional
//      "published by", and Publish (which mints a new active version). An empty
//      body is blocked client-side (the brain 422s invalid_prompt anyway).
//   3. Versions     - the key's history with an active marker and a Promote
//      (revert) button on every inactive row.
//
// `reload()` lists the keys and loads the selected one; it is exposed so the app
// primes the tab on mount.

const EDITOR_HELP =
  "These are the stage instructions the brain runs for this step. Editing publishes a new immutable version and " +
  "makes it active right away. {{TOKEN}} placeholders (double braces) are substituted at runtime: leave them in " +
  "place, removing one breaks the step.";

const VERSIONS_HELP =
  "Every edit publishes a new version and activates it. Promoting an older version reactivates it, which is how " +
  "you roll a change back.";

export function PromptsPanel({ api } = {}) {
  // The key whose editor + versions are currently shown. Empty until reload
  // finds at least one template.
  let currentKey = "";

  // --- Key selector ------------------------------------------------------
  const select = el("select", { id: "pp-key", "aria-label": "Prompt template" });
  const selectNote = el("p", { class: "muted", role: "status", "aria-live": "polite" });
  select.addEventListener("change", () => {
    currentKey = select.value;
    loadKey();
  });

  const selectPanel = el("section", { class: "panel" }, [
    el("div", { class: "panel-head" }, [el("h2", {}, "Prompt templates")]),
    field("Template", select, "pp-key"),
    selectNote,
  ]);

  // --- Editor ------------------------------------------------------------
  const activeNote = el("p", { class: "muted", role: "status", "aria-live": "polite" });
  // No maxlength / no length cap: a prompt body can be arbitrarily long.
  const body = el("textarea", { id: "pp-body", rows: "16" });
  const createdBy = el("input", { type: "text", id: "pp-created-by", autocomplete: "off" });
  const publish = el("button", { type: "submit" }, "Publish new version");
  const editorStatus = el("p", { class: "form-status", role: "status", "aria-live": "polite" });

  const editorForm = el("form", { class: "prompts-editor" }, [
    field("Body", body, "pp-body"),
    field("Published by (optional)", createdBy, "pp-created-by"),
    publish,
    editorStatus,
  ]);
  editorForm.addEventListener("submit", onPublish);

  const editorPanel = el("section", { class: "panel" }, [
    el("div", { class: "panel-head" }, [el("h2", {}, "Editor"), help(EDITOR_HELP)]),
    activeNote,
    editorForm,
  ]);

  // --- Versions ----------------------------------------------------------
  const versionsList = el("div", { class: "prompts-versions" });
  const versionsPanel = el("section", { class: "panel" }, [
    el("div", { class: "panel-head" }, [el("h2", {}, "Versions"), help(VERSIONS_HELP)]),
    versionsList,
  ]);

  const element = el("div", { class: "prompts" }, [selectPanel, editorPanel, versionsPanel]);

  // List the keys, repopulate the <select>, and load the selected key. Keeps the
  // current selection if it still exists, otherwise falls back to the first key.
  async function reload() {
    try {
      const data = await api.listPrompts();
      const templates = (data && data.templates) || [];
      const keys = templates.map((t) => t.key);
      clear(select);
      if (!keys.length) {
        currentKey = "";
        selectNote.dataset.state = "offline";
        selectNote.className = "muted";
        selectNote.textContent = "No prompt templates.";
        clearEditor();
        clear(versionsList);
        return;
      }
      for (const key of keys) select.append(el("option", { value: key }, key));
      if (!keys.includes(currentKey)) currentKey = keys[0];
      select.value = currentKey;
      selectNote.dataset.state = "online";
      selectNote.className = "muted";
      selectNote.textContent = "";
      await loadKey();
    } catch (err) {
      selectNote.dataset.state = "";
      selectNote.className = "error";
      selectNote.setAttribute("role", "alert");
      selectNote.textContent = `Could not load templates: ${err.message}`;
    }
  }

  // Load the active body + version history for the current key, concurrently.
  async function loadKey() {
    if (!currentKey) return;
    await Promise.all([loadEditor(), loadVersions()]);
  }

  async function loadEditor() {
    // Capture the key and gate Publish BEFORE the first await: a key switch (or an
    // out-of-order response) must never leave one template's body in the editor
    // under another key, which a publish would then write to the wrong template.
    const key = currentKey;
    publish.disabled = true;
    setStatus(editorStatus, "", "");
    try {
      const t = await api.getPrompt(key);
      if (key !== currentKey) return; // a newer selection won; drop this stale result
      body.value = t.body || "";
      activeNote.dataset.state = "online";
      activeNote.className = "muted";
      activeNote.textContent = `Active version ${t.version} for ${t.key}.`;
    } catch (err) {
      if (key !== currentKey) return;
      body.value = "";
      activeNote.dataset.state = "";
      activeNote.className = "error";
      activeNote.textContent = `Could not load template: ${err.message}`;
    } finally {
      if (key === currentKey) publish.disabled = false;
    }
  }

  function clearEditor() {
    body.value = "";
    activeNote.dataset.state = "offline";
    activeNote.className = "muted";
    activeNote.textContent = "No template selected.";
    setStatus(editorStatus, "", "");
  }

  async function onPublish(event) {
    event.preventDefault();
    if (!currentKey) return;
    // The brain rejects a blank body (422 invalid_prompt); block it here so no
    // pointless POST goes out, and tell the operator inline.
    if (!body.value.trim()) {
      setStatus(editorStatus, "error", "Body cannot be empty.");
      return;
    }

    const payload = {
      key: currentKey,
      body: body.value,
      created_by: createdBy.value.trim(),
      activate: true,
    };

    setBusy(publish, editorForm, true);
    setStatus(editorStatus, "pending", "Publishing...");
    try {
      await api.publishPrompt(payload);
      await loadKey();
      setStatus(editorStatus, "done", "Published a new version.");
    } catch (err) {
      setStatus(editorStatus, "error", `Could not publish (${err.code}): ${err.message}`);
    } finally {
      setBusy(publish, editorForm, false);
    }
  }

  async function loadVersions() {
    clear(versionsList);
    versionsList.append(el("p", { class: "muted" }, "Loading versions..."));
    try {
      const data = await api.getPromptVersions({ key: currentKey });
      const list = (data && data.versions) || [];
      clear(versionsList);
      if (!list.length) {
        versionsList.append(el("p", { class: "muted" }, "No versions yet."));
        return;
      }
      for (const v of list) versionsList.append(versionRow(v));
    } catch (err) {
      clear(versionsList);
      versionsList.append(el("p", { class: "error", role: "alert" }, `Could not load versions: ${err.message}`));
    }
  }

  // A single version row: number, an active/inactive pill, who/when, and a
  // Promote (revert) button on inactive rows. The active row carries no button.
  function versionRow(v) {
    const active = !!v.is_active;
    const pill = el(
      "span",
      { class: "status", dataset: { state: active ? "online" : "offline" } },
      active ? "active" : "inactive",
    );
    const meta = el("div", { class: "version-meta" }, [
      el("span", { class: "version-num" }, `v${v.version}`),
      pill,
      el("span", { class: "muted" }, v.created_by || "unknown"),
      el("span", { class: "muted" }, v.created_at || ""),
    ]);
    const rowStatus = el("p", { class: "form-status", role: "status", "aria-live": "polite" });
    const children = [meta];

    if (!active) {
      const promote = el("button", { type: "button", class: "secondary" }, "Promote");
      promote.addEventListener("click", async () => {
        promote.disabled = true;
        setStatus(rowStatus, "pending", "Promoting...");
        try {
          await api.promotePromptVersion(v.version);
          await loadKey();
        } catch (err) {
          promote.disabled = false;
          setStatus(rowStatus, "error", `Could not promote (${err.code}): ${err.message}`);
        }
      });
      children.push(promote);
    }
    children.push(rowStatus);
    return el("article", { class: "version-row", dataset: { version: String(v.version) } }, children);
  }

  return { element, reload };
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
