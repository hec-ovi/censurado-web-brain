import { el, clear, field, help } from "./el.js";

// The Editorial tab: the house-style config editor. The style guide is VERSIONED
// (every edit publishes a new immutable version and activates it), so this panel
// is five small sections over the same record:
//   1. Style    - the active version's voice/exemplars/rules/structure; Publish
//                 mints a new version (carrying the active lexicon + sourcing so
//                 editing voice never wipes them).
//   2. Lexicon  - banned terms + preferred swaps (a code-checked override).
//   3. Sourcing - the cross-source corroboration floor and the attribution gates.
//   4. Location - the newsroom's place/languages; gl/hl/ceid are derived/read-only.
//   5. Versions - the version history with a Promote (revert) per inactive row.
//
// `reload()` loads every section and is exposed so the app primes it on mount.
// The "no active style" 404 on style/lexicon/sourcing is tolerated: each shows a
// publish-first affordance instead of a hard error, so the tab is usable before
// anything is published.

const STYLE_HELP =
  "Voice is the house tone the writers adopt. Exemplars are short sample passages they imitate. Rules are the " +
  "explicit dos and don'ts. Publishing mints a new immutable version and activates it; the active lexicon and " +
  "sourcing ride along so editing voice never wipes them.";

const LEXICON_HELP =
  "Banned terms force a revise regardless of the LLM's verdict (they are checked in code, not judged by the model). " +
  "Preferred swaps map a term to the wording the house prefers.";

const SOURCING_HELP =
  "Minimum sources is the cross-source corroboration floor: how many independent sources must carry a claim " +
  "before it can run. Leave it blank to turn the gate off (blank is not zero).";

const LOCATION_HELP =
  "The newsroom's place and languages, used to scope the news feeds. gl, hl, and ceid are derived from these " +
  "fields and are read-only.";

const VERSIONS_HELP =
  "Every style edit publishes a new immutable version and activates it. Promoting an older version reactivates " +
  "it, which is how you roll back.";

export function EditorialPanel({ api } = {}) {
  const style = buildStyle();
  const lexicon = buildLexicon();
  const sourcing = buildSourcing();
  const location = buildLocation();
  const versions = buildVersions();

  const element = el("div", { class: "editorial" }, [
    style.panel,
    lexicon.panel,
    sourcing.panel,
    location.panel,
    versions.panel,
  ]);

  // Load every section concurrently. Each section catches its own failure
  // (including the "no active style" 404 on style/lexicon/sourcing, which shows a
  // publish-first affordance rather than a hard error), so one miss never blocks
  // the others and reload() never rejects.
  async function reload() {
    await Promise.all([style.load(), lexicon.load(), sourcing.load(), location.load(), versions.load()]);
  }

  // ---- 1. STYLE ---------------------------------------------------------
  // Holds the last loaded active StyleGuideOut so Publish can carry its lexicon +
  // sourcing forward (publishing voice must not blank them). `null` means no
  // active style (404), in which case Publish goes out from empty defaults.
  function buildStyle() {
    let loaded = null;

    const versionNote = el("p", { class: "muted", role: "status", "aria-live": "polite" });
    const voice = el("textarea", { id: "ed-style-voice", rows: "4" });
    const exemplars = el("textarea", { id: "ed-style-exemplars", rows: "4" });
    const rules = el("textarea", { id: "ed-style-rules", rows: "4" });
    const structure = el("textarea", { id: "ed-style-structure", rows: "4" });
    const createdBy = el("input", { type: "text", id: "ed-style-created-by", autocomplete: "off" });
    const publish = el("button", { type: "submit" }, "Publish new version");
    const status = el("p", { class: "form-status", role: "status", "aria-live": "polite" });

    const form = el("form", { class: "editorial-style" }, [
      field("Voice", voice, "ed-style-voice"),
      field("Exemplars (JSON array)", exemplars, "ed-style-exemplars"),
      field("Rules (JSON array)", rules, "ed-style-rules"),
      field("Structure (JSON object)", structure, "ed-style-structure"),
      field("Published by (optional)", createdBy, "ed-style-created-by"),
      publish,
      status,
    ]);
    form.addEventListener("submit", onPublish);

    const panel = el("section", { class: "panel" }, [
      el("div", { class: "panel-head" }, [el("h2", {}, "Style"), help(STYLE_HELP)]),
      versionNote,
      form,
    ]);

    async function load() {
      try {
        const s = await api.getStyle();
        loaded = s;
        voice.value = s.voice || "";
        exemplars.value = JSON.stringify(s.exemplars || [], null, 2);
        rules.value = JSON.stringify(s.rules || [], null, 2);
        structure.value = JSON.stringify(s.structure || {}, null, 2);
        versionNote.dataset.state = "online";
        versionNote.className = "muted";
        versionNote.textContent = `Active version ${s.version}.`;
        publish.disabled = false; // clean read: safe to publish
      } catch (err) {
        if (err.status === 404) {
          // No active style yet: prime the fields with empty defaults and show
          // the publish-first affordance instead of an error.
          loaded = null;
          voice.value = "";
          exemplars.value = "[]";
          rules.value = "[]";
          structure.value = "{}";
          versionNote.dataset.state = "offline";
          versionNote.className = "muted";
          versionNote.textContent = "No active style yet. Publish one below to get started.";
          publish.disabled = false; // nothing to carry forward: safe to publish
        } else {
          // A genuine read failure (not a clean 404): we cannot see the active
          // lexicon/sourcing, so publishing the empty form would wipe them.
          // Block publishing until a reload succeeds.
          loaded = null;
          versionNote.dataset.state = "";
          versionNote.className = "error";
          versionNote.textContent = `Could not load style: ${err.message}. Reload before publishing.`;
          publish.disabled = true;
        }
      }
    }

    async function onPublish(event) {
      event.preventDefault();
      if (publish.disabled) return; // style read failed; refuse to overwrite blind
      // Parse the three JSON fields first; a bad field aborts before any POST.
      const ex = parseJson(exemplars.value, "Exemplars", "array");
      if (ex.error) return setStatus(status, "error", ex.error);
      const ru = parseJson(rules.value, "Rules", "array");
      if (ru.error) return setStatus(status, "error", ru.error);
      const st = parseJson(structure.value, "Structure", "object");
      if (st.error) return setStatus(status, "error", st.error);

      const body = {
        voice: voice.value,
        exemplars: ex.value,
        rules: ru.value,
        structure: st.value,
        created_by: createdBy.value.trim(),
        activate: true,
        // Carry the active version's lexicon + sourcing so a voice publish does
        // not wipe them (empty defaults when there is no active style).
        lexicon: (loaded && loaded.lexicon) || {},
        sourcing: (loaded && loaded.sourcing) || {},
      };

      setBusy(publish, form, true);
      setStatus(status, "pending", "Publishing...");
      try {
        await api.postStyle(body);
        await reload();
        setStatus(status, "done", "Published a new version.");
      } catch (err) {
        setStatus(status, "error", `Could not publish (${err.code}): ${err.message}`);
      } finally {
        setBusy(publish, form, false);
      }
    }

    return { panel, load };
  }

  // ---- 2. LEXICON -------------------------------------------------------
  function buildLexicon() {
    const banned = el("textarea", { id: "ed-lex-banned", rows: "4", placeholder: "one term per line" });
    const swaps = el("textarea", { id: "ed-lex-swaps", rows: "4" });
    const save = el("button", { type: "submit" }, "Save lexicon");
    const status = el("p", { class: "form-status", role: "status", "aria-live": "polite" });

    const form = el("form", { class: "editorial-lexicon" }, [
      field("Banned terms (one per line)", banned, "ed-lex-banned"),
      field("Preferred swaps (JSON object)", swaps, "ed-lex-swaps"),
      save,
      status,
    ]);
    form.addEventListener("submit", onSave);

    const panel = el("section", { class: "panel" }, [
      el("div", { class: "panel-head" }, [el("h2", {}, "Lexicon"), help(LEXICON_HELP)]),
      form,
    ]);

    async function load() {
      try {
        const lex = await api.getLexicon();
        banned.value = (lex.banned_terms || []).join("\n");
        swaps.value = JSON.stringify(lex.preferred_swaps || {}, null, 2);
        setStatus(status, "", "");
      } catch (err) {
        if (err.status === 404) {
          banned.value = "";
          swaps.value = "{}";
          setStatus(status, "", "Publish a style first to edit the lexicon.");
        } else {
          setStatus(status, "error", `Could not load lexicon: ${err.message}`);
        }
      }
    }

    async function onSave(event) {
      event.preventDefault();
      const swapsParsed = parseJson(swaps.value, "Preferred swaps", "object");
      if (swapsParsed.error) return setStatus(status, "error", swapsParsed.error);
      const banned_terms = splitLines(banned.value);

      setBusy(save, form, true);
      setStatus(status, "pending", "Saving...");
      try {
        await api.putLexicon({ banned_terms, preferred_swaps: swapsParsed.value });
        await reload();
        setStatus(status, "done", "Lexicon saved.");
      } catch (err) {
        setStatus(status, "error", `Could not save lexicon (${err.code}): ${err.message}`);
      } finally {
        setBusy(save, form, false);
      }
    }

    return { panel, load };
  }

  // ---- 3. SOURCING ------------------------------------------------------
  function buildSourcing() {
    const minSources = el("input", { type: "number", id: "ed-src-min", min: "0", step: "1", placeholder: "off" });
    const requireAttr = el("input", { type: "checkbox", id: "ed-src-attr" });
    const noFab = el("input", { type: "checkbox", id: "ed-src-nofab" });
    const save = el("button", { type: "submit" }, "Save sourcing");
    const status = el("p", { class: "form-status", role: "status", "aria-live": "polite" });

    const form = el("form", { class: "editorial-sourcing" }, [
      field("Minimum sources (blank = gate off)", minSources, "ed-src-min"),
      field("Require attribution", requireAttr, "ed-src-attr"),
      field("No fabricated quotes", noFab, "ed-src-nofab"),
      save,
      status,
    ]);
    form.addEventListener("submit", onSave);

    const panel = el("section", { class: "panel" }, [
      el("div", { class: "panel-head" }, [el("h2", {}, "Sourcing"), help(SOURCING_HELP)]),
      form,
    ]);

    async function load() {
      try {
        const s = await api.getSourcing();
        minSources.value = s.min_sources == null ? "" : String(s.min_sources);
        requireAttr.checked = !!s.require_attribution;
        noFab.checked = !!s.no_fabricated_quotes;
        setStatus(status, "", "");
      } catch (err) {
        if (err.status === 404) {
          minSources.value = "";
          requireAttr.checked = false;
          noFab.checked = false;
          setStatus(status, "", "Publish a style first to edit sourcing.");
        } else {
          setStatus(status, "error", `Could not load sourcing: ${err.message}`);
        }
      }
    }

    async function onSave(event) {
      event.preventDefault();
      // Blank means the gate is OFF (null), not zero. A value must be a whole
      // number >= 0.
      const raw = minSources.value.trim();
      let min = null;
      if (raw !== "") {
        const n = Number.parseInt(raw, 10);
        if (!Number.isInteger(n) || n < 0) {
          return setStatus(status, "error", "Minimum sources must be a whole number, or blank to turn the gate off.");
        }
        min = n;
      }

      setBusy(save, form, true);
      setStatus(status, "pending", "Saving...");
      try {
        await api.putSourcing({
          min_sources: min,
          require_attribution: requireAttr.checked,
          no_fabricated_quotes: noFab.checked,
        });
        await reload();
        setStatus(status, "done", "Sourcing saved.");
      } catch (err) {
        setStatus(status, "error", `Could not save sourcing (${err.code}): ${err.message}`);
      } finally {
        setBusy(save, form, false);
      }
    }

    return { panel, load };
  }

  // ---- 4. LOCATION ------------------------------------------------------
  // Holds the last loaded location so Save sends only the changed fields (the
  // PUT is partial).
  function buildLocation() {
    let loaded = {};

    const region = el("input", { type: "text", id: "ed-loc-region", autocomplete: "off" });
    const uiLang = el("input", { type: "text", id: "ed-loc-ui-lang", autocomplete: "off" });
    const language = el("input", { type: "text", id: "ed-loc-language", autocomplete: "off" });
    const gdelt = el("input", { type: "text", id: "ed-loc-gdelt", autocomplete: "off" });
    const city = el("input", { type: "text", id: "ed-loc-city", autocomplete: "off" });
    const latlong = el("input", { type: "text", id: "ed-loc-latlong", autocomplete: "off" });
    const derived = el("div", { class: "editorial-derived" });
    const save = el("button", { type: "submit" }, "Save location");
    const status = el("p", { class: "form-status", role: "status", "aria-live": "polite" });

    // The editable fields, paired with the key the PUT expects.
    const editable = [
      ["region", region],
      ["ui_lang", uiLang],
      ["language", language],
      ["gdelt_country", gdelt],
      ["city", city],
      ["latlong", latlong],
    ];

    const form = el("form", { class: "editorial-location" }, [
      field("Region", region, "ed-loc-region"),
      field("UI language", uiLang, "ed-loc-ui-lang"),
      field("Language", language, "ed-loc-language"),
      field("GDELT country", gdelt, "ed-loc-gdelt"),
      field("City", city, "ed-loc-city"),
      field("Lat/long", latlong, "ed-loc-latlong"),
      el("div", { class: "field" }, [el("span", { class: "field-label" }, "Derived (read-only)"), derived]),
      save,
      status,
    ]);
    form.addEventListener("submit", onSave);

    const panel = el("section", { class: "panel" }, [
      el("div", { class: "panel-head" }, [el("h2", {}, "Location"), help(LOCATION_HELP)]),
      form,
    ]);

    async function load() {
      try {
        const loc = (await api.getLocation()) || {};
        loaded = loc;
        for (const [key, input] of editable) input.value = loc[key] == null ? "" : String(loc[key]);
        clear(derived);
        derived.append(readOnlyRow("gl", loc.gl), readOnlyRow("hl", loc.hl), readOnlyRow("ceid", loc.ceid));
        setStatus(status, "", "");
      } catch (err) {
        setStatus(status, "error", `Could not load location: ${err.message}`);
      }
    }

    async function onSave(event) {
      event.preventDefault();
      // Partial PUT: send only the fields whose value differs from what loaded.
      const body = {};
      for (const [key, input] of editable) {
        const value = input.value.trim();
        const before = loaded[key] == null ? "" : String(loaded[key]);
        if (value !== before) body[key] = value;
      }
      if (!Object.keys(body).length) {
        setStatus(status, "done", "No changes to save.");
        return;
      }

      setBusy(save, form, true);
      setStatus(status, "pending", "Saving...");
      try {
        await api.putLocation(body);
        await reload();
        setStatus(status, "done", "Location saved.");
      } catch (err) {
        setStatus(status, "error", `Could not save location (${err.code}): ${err.message}`);
      } finally {
        setBusy(save, form, false);
      }
    }

    return { panel, load };
  }

  // ---- 5. VERSIONS ------------------------------------------------------
  function buildVersions() {
    const listEl = el("div", { class: "editorial-versions" });
    const panel = el("section", { class: "panel" }, [
      el("div", { class: "panel-head" }, [el("h2", {}, "Versions"), help(VERSIONS_HELP)]),
      listEl,
    ]);

    async function load() {
      clear(listEl);
      listEl.append(el("p", { class: "muted" }, "Loading versions..."));
      try {
        const data = await api.getStyleVersions({ limit: 100, offset: 0 });
        const list = (data && data.versions) || [];
        clear(listEl);
        if (!list.length) {
          listEl.append(el("p", { class: "muted" }, "No versions yet."));
          return;
        }
        for (const v of list) listEl.append(row(v));
      } catch (err) {
        clear(listEl);
        listEl.append(el("p", { class: "error", role: "alert" }, `Could not load versions: ${err.message}`));
      }
    }

    // A single version row: number, an active/inactive pill, who/when, and a
    // Promote (revert) button on inactive rows. The active row has no button.
    function row(v) {
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
            await api.promoteStyleVersion(v.version);
            await reload();
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

    return { panel, load };
  }

  return { element, reload };
}

// A read-only label/value row for the derived location fields (gl/hl/ceid).
function readOnlyRow(label, value) {
  return el("div", { class: "derived-row" }, [
    el("span", { class: "derived-label" }, label),
    el("span", { class: "derived-value" }, value == null || value === "" ? "-" : String(value)),
  ]);
}

// Parse a JSON textarea into the expected kind ("array" or "object"). An empty
// field means the empty form of that kind ([] or {}), so an operator can clear
// it. Returns { value } on success, or { error } with a message when the text is
// not valid JSON or not the expected kind.
function parseJson(text, label, kind) {
  const trimmed = text.trim();
  if (!trimmed) return { value: kind === "array" ? [] : {} };
  let value;
  try {
    value = JSON.parse(trimmed);
  } catch {
    return { error: `${label} must be valid JSON.` };
  }
  if (kind === "array" && !Array.isArray(value)) return { error: `${label} must be a JSON array.` };
  if (kind === "object" && (value === null || typeof value !== "object" || Array.isArray(value))) {
    return { error: `${label} must be a JSON object.` };
  }
  return { value };
}

// Split a textarea into a trimmed, de-blanked list, one item per line.
function splitLines(value) {
  return value
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
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
