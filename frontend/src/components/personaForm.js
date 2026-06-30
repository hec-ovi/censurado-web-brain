import { el, field } from "./el.js";
import { t } from "./i18n.js";

// The harness's closed section vocabulary, mirrored from the brain's
// contracts.sections so the beat picker can only offer valid beats.
export const SECTIONS = ["tech", "world", "politics", "economics"];

// The create-persona form. Submitting POSTs the explicit persona fields to
// POST /personas/direct (a pure persist, no model call). On success it resets
// and calls onCreated(persona_id) so the list can refresh. There is no synthesis
// job to poll: the row is created and returned synchronously.
export function PersonaForm({ api, onCreated } = {}) {
  const nameInput = el("input", { type: "text", id: "pf-name", autocomplete: "off" });
  // A blank first option so an operator must pick a beat; a blank submission is
  // rejected client-side (the invalid state) before any request goes out.
  const beatSelect = el("select", { id: "pf-beat" }, [
    el("option", { value: "" }, t("Select a beat")),
    ...SECTIONS.map((s) => el("option", { value: s }, s)),
  ]);
  const whoInput = el("textarea", { id: "pf-who", rows: "2" });
  const styleInput = el("textarea", { id: "pf-style", rows: "2" });
  const aboutInput = el("textarea", { id: "pf-about", rows: "2" });
  const languageInput = el("input", { type: "text", id: "pf-language", autocomplete: "off" });
  const avatarInput = el("input", { type: "text", id: "pf-avatar", autocomplete: "off" });
  const submit = el("button", { type: "submit" }, t("Create persona"));
  const status = el("p", { class: "form-status", role: "status", "aria-live": "polite" });

  const form = el("form", { class: "persona-form" }, [
    field(t("Display name"), nameInput, "pf-name"),
    field(t("Beat"), beatSelect, "pf-beat"),
    field(t("Who I am"), whoInput, "pf-who"),
    field(t("Style"), styleInput, "pf-style"),
    field(t("About"), aboutInput, "pf-about"),
    field(t("Language"), languageInput, "pf-language"),
    field(t("Avatar path"), avatarInput, "pf-avatar"),
    submit,
    status,
  ]);

  form.addEventListener("submit", onSubmit);

  async function onSubmit(event) {
    event.preventDefault();
    const required = [nameInput, beatSelect, whoInput, styleInput];
    required.forEach((node) => node.setAttribute("aria-invalid", "false"));

    const display_name = nameInput.value.trim();
    const beat = beatSelect.value;
    const who_i_am = whoInput.value.trim();
    const style = styleInput.value.trim();

    const missing = [];
    if (!display_name) missing.push(nameInput);
    if (!beat) missing.push(beatSelect);
    if (!who_i_am) missing.push(whoInput);
    if (!style) missing.push(styleInput);
    if (missing.length) {
      missing.forEach((node) => node.setAttribute("aria-invalid", "true"));
      missing[0].focus();
      setStatus("error", t("Display name, beat, who I am, and style are required."));
      return;
    }

    const body = { display_name, beat, who_i_am, style };
    const about = aboutInput.value.trim();
    const language = languageInput.value.trim();
    const avatar_path = avatarInput.value.trim();
    if (about) body.about = about;
    if (language) body.language = language;
    if (avatar_path) body.avatar_path = avatar_path;

    setBusy(true);
    setStatus("pending", t("Creating..."));
    try {
      const persona = await api.createPersonaDirect(body);
      setStatus("done", t("Created {id}.", { id: persona.id }));
      form.reset();
      if (onCreated) onCreated(persona.id);
    } catch (err) {
      setStatus("error", t("Could not create persona: {msg}", { msg: err.message }));
    } finally {
      setBusy(false);
    }
  }

  function setBusy(busy) {
    submit.disabled = busy;
    form.setAttribute("aria-busy", busy ? "true" : "false");
  }
  function setStatus(state, text) {
    status.dataset.state = state;
    status.textContent = text;
    const assertive = state === "error";
    status.setAttribute("role", assertive ? "alert" : "status");
    status.setAttribute("aria-live", assertive ? "assertive" : "polite");
  }

  return { element: form };
}
