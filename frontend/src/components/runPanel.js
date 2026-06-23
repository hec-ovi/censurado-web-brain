import { el, clear, field } from "./el.js";
import { pollUntil } from "../poll.js";

// The trigger surface. `mode` is the whole trigger; n and persona_ids are
// optional operator overrides. Managed is first so the default click runs the
// manager-driven path.
export const MODES = ["managed", "express", "manual"];

// The brain marks a run done, done_with_errors, or failed; anything else means
// it is still running, so the poll keeps going.
const TERMINAL = new Set(["done", "done_with_errors", "failed"]);

// Starts a run via POST /runs, then polls GET /runs/{id} to a terminal status
// and renders the assignment outcomes. A poll timeout reports "still running"
// rather than a false terminal status. `poll`/`pollOpts` are injectable for a
// fake clock in tests.
export function RunPanel({ api, poll = pollUntil, pollOpts } = {}) {
  const modeSelect = el("select", { id: "rp-mode" }, MODES.map((m) => el("option", { value: m }, m)));
  const nInput = el("input", { type: "number", id: "rp-n", min: "0", step: "1", placeholder: "default" });
  const personasInput = el("input", { type: "text", id: "rp-personas", placeholder: "all (comma separated ids)" });
  const submit = el("button", { type: "submit" }, "Start run");
  const status = el("p", { class: "form-status", role: "status", "aria-live": "polite" });
  const resultEl = el("div", { class: "run-result" });

  const form = el("form", { class: "run-form" }, [
    field("Mode", modeSelect, "rp-mode"),
    field("Count (n)", nInput, "rp-n"),
    field("Persona ids", personasInput, "rp-personas"),
    submit,
    status,
  ]);
  form.addEventListener("submit", onSubmit);

  const element = el("section", { class: "panel" }, [
    el("div", { class: "panel-head" }, [el("h2", {}, "Runs")]),
    form,
    resultEl,
  ]);

  async function onSubmit(event) {
    event.preventDefault();
    const req = { mode: modeSelect.value };
    const rawN = nInput.value.trim();
    if (rawN !== "") {
      const n = Number.parseInt(rawN, 10);
      if (Number.isInteger(n)) req.n = n;
    }
    const ids = personasInput.value.split(",").map((s) => s.trim()).filter(Boolean);
    if (ids.length) req.persona_ids = ids;

    setBusy(true);
    setStatus("pending", "Starting run...");
    clear(resultEl);
    let runId = "";
    try {
      const created = await api.createRun(req);
      runId = created.run_id || "";
      setStatus("pending", `Run ${runId} ${created.status}...`);
      const final = await poll(() => api.getRun(runId), (r) => TERMINAL.has(r.status), pollOpts);
      setStatus(final.status === "failed" ? "error" : "done", `Run ${final.status}.`);
      renderRun(final);
    } catch (err) {
      if (err.code === "poll_timeout") {
        setStatus("pending", `Run ${runId} is still running; taking longer than usual. Reload later to check.`);
      } else {
        setStatus("error", `Could not start run: ${err.message}`);
      }
    } finally {
      setBusy(false);
    }
  }

  function renderRun(run) {
    clear(resultEl);
    const assignments = (run && run.assignments) || [];
    if (!assignments.length) {
      resultEl.append(el("p", { class: "muted" }, "No assignments produced."));
      return;
    }
    const rows = assignments.map((a) =>
      el("tr", {}, [
        el("td", {}, a.persona_id),
        el("td", {}, el("span", { class: "badge" }, a.section)),
        el("td", {}, el("span", { class: "status", dataset: { state: a.status } }, a.status)),
        el("td", {}, a.published_id || a.drop_reason || ""),
      ]),
    );
    resultEl.append(
      el("table", { class: "assignments" }, [
        el("caption", { class: "visually-hidden" }, "Run assignment outcomes"),
        el(
          "thead",
          {},
          el("tr", {}, [el("th", {}, "Persona"), el("th", {}, "Section"), el("th", {}, "Status"), el("th", {}, "Result")]),
        ),
        el("tbody", {}, rows),
      ]),
    );
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

  return { element, renderRun };
}
