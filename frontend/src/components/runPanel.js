import { el, clear, field, help, isSafeImageSrc } from "./el.js";
import { pollUntil } from "../poll.js";

// The manager-mode vocabulary for a batch run: managed runs the full manager
// logic, express a smaller quick batch, manual the operator-driven path.
export const MODES = ["managed", "express", "manual"];

// The brain marks a run done, done_with_errors, or failed; anything else means
// it is still running, so the poll keeps going.
const TERMINAL = new Set(["done", "done_with_errors", "failed"]);

const FULL_HELP =
  "Full manager: the manager assigns every active author and runs the whole newsroom. " +
  "Leave the count blank to let the manager decide how many pieces to commission.";
const BATCH_HELP =
  "Author batch: scope the manager to the authors you tick. Express runs a smaller, quicker batch; " +
  "managed and manual run the full manager logic over just those authors.";
const SINGLE_HELP =
  "Single article: the direct path. Write one piece from a brief and/or a specific link with the chosen " +
  "author. This bypasses the manager and the cross-source corroboration floor, so use it for a one-off.";

// A short visible lead per trigger (the full explanation lives in the (?) tooltip).
const FULL_SUMMARY = "Runs the whole newsroom: the manager assigns every active author.";
const BATCH_SUMMARY = "Scopes the manager to the authors you tick.";
const SINGLE_SUMMARY = "Writes one piece directly, bypassing the manager.";

// The three operator triggers. `full` is first so the default surface runs the
// whole manager.
const TRIGGERS = [
  { value: "full", label: "Full manager", help: FULL_HELP },
  { value: "batch", label: "Author batch", help: BATCH_HELP },
  { value: "single", label: "Single article", help: SINGLE_HELP },
];

const HISTORY_STATUSES = ["running", "done", "done_with_errors", "failed"];

// The Runs tab. Two panels: a TRIGGER panel that exposes the three operator
// triggers (full manager, author batch, single article) and polls the launched
// run to a terminal status, and a HISTORY panel that lists past runs with a
// status filter and a per-row View into a run's assignments.
//
// `poll`/`pollOpts` are injectable so tests drive the poll loop with a fake
// clock. `loadAuthors()` primes the author pickers and `reloadHistory()` loads
// the run list; both are exposed so the app can call them on mount.
export function RunPanel({ api, poll = pollUntil, pollOpts } = {}) {
  // --- Trigger panel -----------------------------------------------------
  const triggerSelect = el(
    "select",
    { id: "rp-trigger" },
    TRIGGERS.map((t) => el("option", { value: t.value }, t.label)),
  );
  const triggerFields = el("div", { class: "trigger-fields" });
  const submit = el("button", { type: "submit" }, "Start run");
  const status = el("p", { class: "form-status", role: "status", "aria-live": "polite" });
  const resultEl = el("div", { class: "run-result" });

  // The live field references for the currently-rendered trigger. Rebuilt by
  // renderTriggerFields() whenever the trigger type changes.
  let current = null;

  const form = el("form", { class: "run-form" }, [
    field("Trigger", triggerSelect, "rp-trigger"),
    triggerFields,
    submit,
    status,
  ]);
  form.addEventListener("submit", onSubmit);
  triggerSelect.addEventListener("change", () => {
    setStatus("", "");
    renderTriggerFields();
  });

  const triggerPanel = el("section", { class: "panel" }, [
    el("div", { class: "panel-head" }, [
      el("h2", {}, "Trigger a run"),
      help(
        "Three ways to commission articles: full manager runs the whole newsroom, author batch scopes the " +
          "manager to the authors you pick, single article writes one piece directly from a link.",
      ),
    ]),
    form,
    resultEl,
  ]);

  // --- History panel -----------------------------------------------------
  const historyFilter = el("select", { id: "rp-history-filter", "aria-label": "Filter runs by status" }, [
    el("option", { value: "" }, "All statuses"),
    ...HISTORY_STATUSES.map((s) => el("option", { value: s }, s)),
  ]);
  const refreshBtn = el("button", { type: "button", class: "secondary" }, "Refresh");
  const historyList = el("div", { class: "run-history" });
  const historyDetail = el("div", { class: "history-detail" });

  historyFilter.addEventListener("change", reloadHistory);
  refreshBtn.addEventListener("click", reloadHistory);

  const historyPanel = el("section", { class: "panel" }, [
    el("div", { class: "panel-head" }, [
      el("h2", {}, "Run history"),
      el("div", { class: "history-controls" }, [historyFilter, refreshBtn]),
    ]),
    historyList,
    historyDetail,
  ]);

  const element = el("div", { class: "runs" }, [triggerPanel, historyPanel]);

  // --- Author cache (shared by the batch and single triggers) ------------
  let personas = [];
  let personasState = "idle"; // idle | loading | ready | error
  let personasError = "";

  function needsAuthors(type) {
    return type === "batch" || type === "single";
  }

  // Load the author roster used by the batch checkbox list and the single-article
  // select. Rebuilds the current trigger if it depends on authors, so the picker
  // fills in once the fetch resolves.
  async function loadAuthors() {
    personasState = "loading";
    if (needsAuthors(triggerSelect.value)) renderTriggerFields();
    try {
      const data = await api.listPersonas();
      personas = (data && data.personas) || [];
      personasState = "ready";
    } catch (err) {
      personasError = err.message;
      personasState = "error";
    }
    if (needsAuthors(triggerSelect.value)) renderTriggerFields();
  }

  // --- Trigger field rendering -------------------------------------------
  function renderTriggerFields() {
    clear(triggerFields);
    const type = triggerSelect.value;
    if (type === "full") current = buildFull();
    else if (type === "batch") current = buildBatch();
    else current = buildSingle();
    triggerFields.append(current.node);
  }

  function buildFull() {
    const nInput = el("input", { type: "number", id: "rp-full-n", min: "0", step: "1", placeholder: "default" });
    const images = checkbox("rp-full-images", true);
    const node = el("div", {}, [
      triggerHelpLine(FULL_SUMMARY, FULL_HELP),
      field("Count (n)", nInput, "rp-full-n"),
      field("Auto-generate images", images, "rp-full-images"),
    ]);
    return { type: "full", node, nInput, images };
  }

  function buildBatch() {
    const sub = el("select", { id: "rp-batch-mode" }, MODES.map((m) => el("option", { value: m }, m)));
    sub.value = "managed";
    const nInput = el("input", { type: "number", id: "rp-batch-n", min: "0", step: "1", placeholder: "default" });
    const images = checkbox("rp-batch-images", true);

    const checksWrap = el("div", { class: "author-checks" });
    const boxes = [];
    if (personasState === "loading") {
      checksWrap.append(el("p", { class: "muted" }, "Loading authors..."));
    } else if (personasState === "error") {
      checksWrap.append(el("p", { class: "error", role: "alert" }, `Could not load authors: ${personasError}`));
    } else if (!personas.length) {
      checksWrap.append(el("p", { class: "muted" }, "No authors yet. Create one in the Authors tab."));
    } else {
      for (const p of personas) {
        const box = el("input", { type: "checkbox", id: `rp-batch-author-${p.id}` });
        boxes.push({ id: p.id, box });
        checksWrap.append(field(p.display_name, box, `rp-batch-author-${p.id}`));
      }
    }

    const node = el("div", {}, [
      triggerHelpLine(BATCH_SUMMARY, BATCH_HELP),
      el("div", { class: "field" }, [el("span", { class: "field-label" }, "Authors"), checksWrap]),
      field("Manager mode", sub, "rp-batch-mode"),
      field("Count (n)", nInput, "rp-batch-n"),
      field("Auto-generate images", images, "rp-batch-images"),
    ]);
    return { type: "batch", node, sub, nInput, images, checkedIds: () => boxes.filter((b) => b.box.checked).map((b) => b.id) };
  }

  function buildSingle() {
    const persona = el("select", { id: "rp-single-persona" }, [
      el("option", { value: "" }, "Select an author..."),
      ...personas.map((p) => el("option", { value: p.id }, p.display_name)),
    ]);
    const brief = el("textarea", { id: "rp-single-brief", rows: "3" });
    const links = el("textarea", { id: "rp-single-links", rows: "3", placeholder: "one URL per line, or comma separated" });
    const focus = el("input", { type: "text", id: "rp-single-focus", placeholder: "optional angle" });
    const images = checkbox("rp-single-images", true);

    const authorNote =
      personasState === "loading"
        ? el("p", { class: "muted" }, "Loading authors...")
        : personasState === "error"
          ? el("p", { class: "error", role: "alert" }, `Could not load authors: ${personasError}`)
          : personasState === "ready" && !personas.length
            ? el("p", { class: "muted" }, "No authors yet. Create one in the Authors tab.")
            : null;

    const node = el("div", {}, [
      triggerHelpLine(SINGLE_SUMMARY, SINGLE_HELP),
      field("Author", persona, "rp-single-persona"),
      authorNote,
      field("Brief", brief, "rp-single-brief"),
      field("Links", links, "rp-single-links"),
      field("Focus", focus, "rp-single-focus"),
      field("Auto-generate images", images, "rp-single-images"),
    ]);
    return { type: "single", node, persona, brief, links, focus, images };
  }

  // --- Submit dispatch ----------------------------------------------------
  function onSubmit(event) {
    event.preventDefault();
    const type = triggerSelect.value;
    if (type === "full") return submitFull();
    if (type === "batch") return submitBatch();
    return submitSingle();
  }

  function submitFull() {
    const req = { mode: "managed" };
    addN(req, current.nInput);
    req.images = current.images.checked;
    return runAndPoll(() => api.createRun(req));
  }

  function submitBatch() {
    const ids = current.checkedIds();
    if (!ids.length) {
      setStatus("error", "Pick at least one author for the batch.");
      return undefined;
    }
    const req = { mode: current.sub.value, persona_ids: ids };
    addN(req, current.nInput);
    req.images = current.images.checked;
    return runAndPoll(() => api.createRun(req));
  }

  function submitSingle() {
    const persona_id = current.persona.value;
    const brief = current.brief.value.trim();
    const links = splitList(current.links.value);
    const focus = current.focus.value.trim();
    if (!persona_id) {
      setStatus("error", "Pick an author for the article.");
      return undefined;
    }
    if (!brief && !links.length) {
      setStatus("error", "Give a brief or at least one link.");
      return undefined;
    }
    const body = { persona_id, images: current.images.checked };
    if (brief) body.brief = brief;
    if (links.length) body.links = links;
    if (focus) body.focus = focus;
    return runAndPoll(() => api.createDirect(body));
  }

  // Launch a run (202), then poll GET /runs/{id} to a terminal status and render
  // the assignment outcomes. A poll timeout reports "still running" and points at
  // history rather than reporting a false terminal status. Refreshes history once
  // the run was created so the new run shows up.
  async function runAndPoll(createFn) {
    setBusy(true);
    setStatus("pending", "Starting run...");
    clear(resultEl);
    let runId = "";
    let created = false;
    try {
      const res = await createFn();
      created = true;
      runId = res.run_id || "";
      setStatus("pending", `Run ${runId} ${res.status}...`);
      try {
        const final = await poll(() => api.getRun(runId), (r) => TERMINAL.has(r.status), pollOpts);
        setStatus(final.status === "failed" ? "error" : "done", `Run ${final.status}.`);
        renderRun(final);
      } catch (pollErr) {
        if (pollErr.code === "poll_timeout") {
          setStatus("pending", `Run ${runId} is still running; check history.`);
        } else {
          throw pollErr;
        }
      }
    } catch (err) {
      setStatus("error", `Could not start run: ${err.message}`);
    } finally {
      setBusy(false);
      if (created) await reloadHistory();
    }
  }

  // --- History ------------------------------------------------------------
  async function reloadHistory() {
    clear(historyList);
    historyList.append(el("p", { class: "muted" }, "Loading runs..."));
    try {
      const data = await api.listRuns({ status: historyFilter.value || undefined });
      const runs = (data && data.runs) || [];
      clear(historyList);
      if (!runs.length) {
        historyList.append(el("p", { class: "muted" }, "No runs yet."));
        return;
      }
      historyList.append(historyTable(runs));
    } catch (err) {
      clear(historyList);
      historyList.append(el("p", { class: "error", role: "alert" }, `Could not load runs: ${err.message}`));
    }
  }

  function historyTable(runs) {
    const rows = runs.map((r) =>
      el("tr", {}, [
        el("td", {}, r.run_id),
        el("td", {}, el("span", { class: "badge" }, r.mode)),
        el("td", {}, el("span", { class: "status", dataset: { state: r.status } }, r.status)),
        el("td", {}, r.n_requested == null ? "" : String(r.n_requested)),
        el("td", {}, r.created_at || ""),
        el("td", {}, r.finished_at || ""),
        el("td", {}, viewButton(r.run_id)),
      ]),
    );
    return el("table", { class: "history-table" }, [
      el("caption", { class: "visually-hidden" }, "Run history"),
      el(
        "thead",
        {},
        el("tr", {}, [
          el("th", {}, "Run"),
          el("th", {}, "Mode"),
          el("th", {}, "Status"),
          el("th", {}, "Requested"),
          el("th", {}, "Created"),
          el("th", {}, "Finished"),
          el("th", {}, ""),
        ]),
      ),
      el("tbody", {}, rows),
    ]);
  }

  function viewButton(runId) {
    const btn = el("button", { type: "button", class: "secondary" }, "View");
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      clear(historyDetail);
      historyDetail.append(el("p", { class: "muted" }, "Loading run..."));
      try {
        const run = await api.getRun(runId);
        renderRun(run, historyDetail);
      } catch (err) {
        clear(historyDetail);
        historyDetail.append(el("p", { class: "error", role: "alert" }, `Could not load run: ${err.message}`));
      } finally {
        btn.disabled = false;
      }
    });
    return btn;
  }

  // --- Assignments rendering (shared by a launched run and a history View) ---
  function renderRun(run, target = resultEl) {
    clear(target);
    const assignments = (run && run.assignments) || [];
    if (!assignments.length) {
      target.append(el("p", { class: "muted" }, "No assignments produced."));
      return;
    }
    const rows = assignments.map((a) =>
      el("tr", {}, [
        el("td", {}, a.persona_id),
        el("td", {}, el("span", { class: "badge" }, a.section)),
        el("td", {}, el("span", { class: "status", dataset: { state: a.status } }, a.status)),
        el("td", {}, a.published_id || a.drop_reason || ""),
        el("td", {}, heroThumb(a)),
      ]),
    );
    target.append(
      el("table", { class: "assignments" }, [
        el("caption", { class: "visually-hidden" }, "Run assignment outcomes"),
        el(
          "thead",
          {},
          el("tr", {}, [
            el("th", {}, "Persona"),
            el("th", {}, "Section"),
            el("th", {}, "Status"),
            el("th", {}, "Result"),
            el("th", {}, "Image"),
          ]),
        ),
        el("tbody", {}, rows),
      ]),
    );
  }

  // The article's generated hero image, when one was produced and its URL is a src we
  // trust (a /media path or an http(s)/data:image URL). Otherwise nothing renders.
  function heroThumb(a) {
    if (!isSafeImageSrc(a.image_url)) return "";
    return el("img", {
      class: "hero-thumb",
      src: a.image_url,
      alt: `Hero image for ${a.persona_id}'s article`,
      loading: "lazy",
    });
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

  // Render the default (full manager) trigger up front.
  renderTriggerFields();

  return { element, renderRun, loadAuthors, reloadHistory };
}

// A short visible lead for the selected trigger, with a (?) marker carrying the
// full explanation in its tooltip.
function triggerHelpLine(summary, helpText) {
  return el("p", { class: "muted trigger-help" }, [help(helpText), " ", summary]);
}

function checkbox(id, checked) {
  const box = el("input", { type: "checkbox", id });
  box.checked = !!checked;
  return box;
}

// Apply an optional integer count override to a run request from a number input.
function addN(req, input) {
  const raw = input.value.trim();
  if (raw === "") return;
  const n = Number.parseInt(raw, 10);
  if (Number.isInteger(n)) req.n = n;
}

function splitList(value) {
  return value
    .split(/[,\n]/)
    .map((s) => s.trim())
    .filter(Boolean);
}
