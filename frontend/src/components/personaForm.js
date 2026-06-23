import { el, field } from "./el.js";
import { pollUntil } from "../poll.js";

// The harness's closed section vocabulary, mirrored from the brain's
// contracts.sections so the beat picker can only offer valid beats.
export const SECTIONS = ["tech", "world", "politics", "economics"];

// The create-persona form. Submitting POSTs a seed, then polls the synthesis
// job (202-then-poll) until it is done or failed, surfacing each state. On
// success it resets and calls onCreated(persona_id) so the list can refresh. If
// polling times out (the job outlasts the budget) it reports "still working"
// rather than a false failure.
//
// `poll` and `pollOpts` are injectable so a test drives the job loop with a
// fake clock instead of real delays.
export function PersonaForm({ api, onCreated, poll = pollUntil, pollOpts } = {}) {
  const nameInput = el("input", { type: "text", id: "pf-name", autocomplete: "off" });
  const beatSelect = el("select", { id: "pf-beat" }, SECTIONS.map((s) => el("option", { value: s }, s)));
  const seedInput = el("textarea", { id: "pf-seed", rows: "3" });
  const sourcesInput = el("input", { type: "text", id: "pf-sources", placeholder: "example.com, another.org" });
  const submit = el("button", { type: "submit" }, "Synthesize persona");
  const status = el("p", { class: "form-status", role: "status", "aria-live": "polite" });

  const form = el("form", { class: "persona-form" }, [
    field("Display name", nameInput, "pf-name"),
    field("Beat", beatSelect, "pf-beat"),
    field("Seed description", seedInput, "pf-seed"),
    field("Preferred sources (comma separated)", sourcesInput, "pf-sources"),
    submit,
    status,
  ]);

  form.addEventListener("submit", onSubmit);

  async function onSubmit(event) {
    event.preventDefault();
    nameInput.setAttribute("aria-invalid", "false");
    seedInput.setAttribute("aria-invalid", "false");

    const display_name = nameInput.value.trim();
    const seed = seedInput.value.trim();
    if (!display_name || !seed) {
      if (!display_name) nameInput.setAttribute("aria-invalid", "true");
      if (!seed) seedInput.setAttribute("aria-invalid", "true");
      (!display_name ? nameInput : seedInput).focus();
      setStatus("error", "Display name and seed are required.");
      return;
    }
    const sources = splitList(sourcesInput.value);

    setBusy(true);
    setStatus("pending", "Submitting...");
    let personaId = "";
    try {
      const job = await api.createPersona({ display_name, beat: beatSelect.value, seed, sources });
      personaId = job.persona_id || "";
      setStatus("pending", `Synthesizing "${personaId}"...`);
      const done = await poll(
        () => api.getJob(job.job_id),
        (j) => j.status === "done" || j.status === "failed",
        pollOpts,
      );
      if (done.status === "done") {
        setStatus("done", `Created ${done.persona_id}.`);
        form.reset();
        if (onCreated) onCreated(done.persona_id);
      } else {
        setStatus("error", `Synthesis failed: ${done.error || "unknown error"}.`);
      }
    } catch (err) {
      if (err.code === "poll_timeout") {
        const which = personaId ? ` "${personaId}"` : "";
        setStatus("pending", `Still synthesizing${which}. This is taking longer than usual; reload later to check.`);
      } else {
        setStatus("error", `Could not create persona: ${err.message}`);
      }
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

function splitList(value) {
  return value
    .split(/[,\n]/)
    .map((s) => s.trim())
    .filter(Boolean);
}
