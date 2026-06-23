# Stage 2: Newsroom Brain Architecture

This is the final architecture for `censurado-web-brain`, the agentic harness that researches, drafts, reviews, and publishes articles to the `censurado-web` platform. The harness is a separate git repo and a separate stack; its only coupling to the platform is one HTTP publish contract over which it holds a single operator key. This document answers the eight open design questions decisively (Part A), specifies the isolated-module architecture with exact seam contracts and provably-bounded control flow (Part B), and lays out the ordered, independently-testable build plan (Part C). Every external claim was verified against the live platform code and against PyPI/GitHub as of 2026-06-23; where a critique was wrong I say why I kept the original design.

All version and contract claims below were re-verified directly: the publish handler (`internal/publish/publish.go`), the accept core (`internal/publish/apply.go`), the schema (`contracts/article.schema.json`), the platform CLI (`cli/main.go`, `cli/skill/SKILL.md`), and `docs/ARCHITECTURE.md`. The orchestration pick (pydantic-ai 1.107.0, MIT, Python >=3.10, no stable 2.x) was confirmed live on PyPI 2026-06-23.

---

## PART A: The eight open questions

### A.1 What is an agentic workflow?

An agentic workflow is a system where LLM and tool steps are orchestrated through predefined code paths: the developer owns the control flow, the model does the work inside the steps. This is the "workflow" half of Anthropic's workflow-vs-agent taxonomy, the load-bearing distinction in 2026 (workflows = LLMs and tools orchestrated through predefined code paths; agents = LLMs that dynamically direct their own process and tool usage). See Anthropic, "Building Effective AI Agents" (anthropic.com/research/building-effective-agents).

Two contrasts pin the term. It is not a plain workflow: a plain data pipeline has no model in the loop, while an agentic workflow's nodes reason and call tools with an LLM. And it is not an agentic loop (A.2): the steps are agentic, but the edges between them are code, not model decisions. The control flow is a fixed graph of `State / Nodes / Edges`; the model is called inside nodes but never decides what runs next, the edges do.

In this harness the entire outer shape (trigger -> manager triage -> fan-out dispatch -> per-article pipeline -> collect -> publish) is an agentic workflow: LLM-driven nodes, code-driven edges, deterministic and re-runnable top to bottom. The 2026 reality is that real systems are hybrids, structured enough to be reliable and flexible enough to absorb variance. Ours is deliberately a hybrid: an agentic workflow on the outside hosting bounded agentic loops on the inside (A.3).

### A.2 What is an "agentic loop"?

An agentic loop is model-driven control flow: perceive, reason, plan, act, observe, repeat, where the MODEL decides whether to continue. ReAct, Plan-and-Execute, and Reflexion are all this shape. The defining property, and the reason loops are dangerous, is that the model has no built-in concept of "done." Left alone it will keep going, or stop too early, on its own judgment. A loop is only safe when code outside the model enforces the exit. The research finding is firm on this: the halting proof rests on at least one monotonic, harness-enforced bound the model cannot reset (a max-iterations cap or a token/wall-clock budget, debited every turn). On top of that minimum, a production loop carries the full guard set the 2026 literature treats as standard: max iterations, a resource (token/wall-clock) budget, a no-progress detector, a circuit breaker on repeated identical or failed tool calls, and a wall-clock/timeout backstop (the circuit breaker and wall-clock are specified in A.8 and B.2). The no-progress detector, circuit breaker, and completion check make a loop halt sooner and smarter; the monotonic bound is what makes it halt at all. Any loop with only a model-driven exit is a hope, not a bound. This rule applies to every loop in the system without exception (see A.6 and A.8 for where the original design violated it and how this version fixes it).

### A.3 How do agentic workflows and agentic loops relate?

They compose by altitude. A deterministic agentic workflow hosts bounded agentic loops. The guards live exactly where the two altitudes meet: every place a workflow node hands control to a model-driven loop, the workflow owns the cap, the budget, and the progress detector that bring control back. In this harness the manager is the one orchestrator-workers seam, the single point where the workflow lets the model decide how many workers to spawn, and even that decision is clamped (A.6). Below the manager, journalist nodes run their own bounded loops and call tools, but no node below the manager may spawn another agent or persona. That invariant (the manager is the sole fan-out point) is what makes runaway recursion structurally impossible rather than merely unlikely.

### A.4 Best prompt technique per step

The table is built on the three load-bearing findings from the Plan 1 research: the **format tax** (asking for reasoning and rigid structure in the same call degrades both, so separate the reasoning call from the structuring call), the **self-correction blind spot** (a model cannot reliably find its own errors, so review and judging must come from a different model or from deterministic rules), and **weak style imitation** (a model copies surface tics, not voice, so persona few-shots must carry negative examples and be reinforced every sweep, not stated once).

| Step | Technique | Why |
|---|---|---|
| Persona synthesis | Few-shot from seed description, emit traits + positive AND negative exemplars | Style is learned from contrast; negatives prevent generic-voice collapse |
| Manager triage | Bounded ReAct (search top news, rank, assign), forced terminal | Needs live judgment over today's news; must be clamped (A.6, blocker fix) |
| Research | Plan-then-search: generate 3-6 sub-questions, search each, fill the ledger | Grounding-first; NOT the full STORM perspective-simulation pipeline (see rejection in B.5) |
| Outline | Single structured call, reasoning separated from structure | Format tax: outline shape is structure, plan it without forcing prose |
| Draft | Persona-conditioned prose call, body unbounded, no max_tokens | Voice + length are the task; capping either breaks it |
| Review | Separate-evaluator PASS/REVISE with section-level feedback | Self-correction blind spot: the drafter cannot grade itself |
| Fact-check | Deterministic CitationVerify over the ledger, single bounded revise on miss | Rules beat an LLM yes/no for "is this claim sourced" |
| Finalize | Dedicated JSON call, Pydantic-validated, single retry on parse failure | Format tax again: structure the finished article in its own call |

Review and the completion judge are **collapsed into one evaluator call** that returns `{verdict: PASS | REVISE, feedback, failing_sections}`. PASS means done. This is the pragmatism critique's fix (#5) and it also resolves a loops-critique hazard (a separate judge that shared the drafter's weights would share its blind spots). The evaluator role MUST resolve to a different endpoint than the draft role, or, when only the local Gemma is available, the evaluator degrades to a rules-grounded check (every section maps to at least one ledger claim, no unresolved TODO markers, CitationVerify passes) and `max_sweeps` becomes the real terminator. "Evaluator role != draft role" is a startup assertion that fails fast when both resolve to the same endpoint.

### A.5 How is each layer isolated?

By typed nodes with their own context windows: parameters in, artifacts out. A node receives a typed input and returns a typed output; it does not see another node's transcript, scratchpad, or model. This buys two things from one mechanism. Swappability: any node's model can change without touching its neighbors, because the seam is the type, not the conversation. Anti-homogenization: each journalist runs in its own isolated context, so personas do not bleed into a single averaged voice. The persona store is brain-owned and in-process only; no external module touches its SQLite file. The platform has its own author handling, but its `author` field is a free string at the seam (`NewArticle` only requires non-empty, there is no authors-table foreign key), so the harness owning persona identity is fully compatible with the platform.

### A.6 Dynamic but useful, not unbounded

Dynamism is confined to two places and both are fenced:

- **Manager fan-out** is clamped: `len(assignments) <= N_MAX`. The manager's own internal ReAct loop is bounded by `max_manager_steps` + a tool-call budget + a circuit breaker, with a forced "emit assignments now" terminal when the step cap is hit (it degrades to whatever it has ranked rather than failing the run). N_MAX is the primary fan-out limiter (A.8 corrects an earlier claim that batch wall-clock was the limiter).
- **Per-article sweeps** are judge-driven but capped: `for sweep in range(MAX_SWEEPS)`, exit early on evaluator PASS.

Every loop carries all three guards. Just-in-time research means a node fetches only what it needs when it needs it, never a speculative crawl. The `research`, `enrich`, and `fact-check` loops each get their own explicit `max_steps` cap and a ledger-stall detector (see A.8); they are not exempt because they live "inside a step."

### A.7 Context management

The claim-source ledger is the shared spine. Every factual claim a journalist will publish is a row `{claim, url, snippet, fetched_at}`; the ledger is the article's grounding and its audit trail. Handoffs between nodes pass artifacts (the outline, the draft, the ledger), never raw transcripts, so context does not accumulate across sweeps. Research is just-in-time fetch, not bulk ingest. The one context-growth risk left is the ledger itself growing within a single article; the research-loop stall cap (A.8) bounds that too, since a research step that adds zero new ledger rows counts as no-progress.

### A.8 No infinite runs, and why bounding a loop is not capping output

Two layers. **Deterministic backstops** under every loop: `max_iterations` (per loop: `max_sweeps`, `max_manager_steps`, `max_research_steps`, `max_enrich_steps`, `max_factcheck_steps`), a shared per-article token + wall-clock budget that every sub-loop debits from the same ceiling, and a circuit breaker on repeated failed or identical tool calls. **A dynamic completion check** on top: the collapsed evaluator's PASS verdict, plus a no-progress detector keyed on the evaluator's own signal.

The blocker fixes from the loops critique, folded in:

- The **research loop** now has `max_research_steps` AND a ledger-stall detector: if a research step adds zero new ledger rows whose URL is not already present, increment a stall counter; abort research after `S` consecutive stalls. A circuit breaker alone (which only catches failed or byte-identical calls) does not catch K distinct successful searches that each return junk. This was an under-guarded loop by the design's own three-guard rule; now it is closed.
- The **manager loop** has its own full guard stack (above), not just an output clamp.
- **Enrich and fact-check are single-pass by default**: one enrich call, one fact-check call, no open re-search loop. If fact-check finds an unsupported claim it routes to a single bounded revise, not an open search loop. If a future version must let them loop, each gets an explicit cap drawn from the SAME per-article budget, so the article budget is a true ceiling over the sum of all sub-loops. The article budget is debited by every sub-loop or it is not a real bound.

**No-progress, fixed.** The original design gated early-stop on whole-draft cosine similarity above ~0.9. That conflates "converged and done" with "converged and stuck": a healthy revise that fixes one paragraph produces a draft 0.9-similar to its predecessor while still improving. The fix gates no-progress on the evaluator's failing-section SET: if the set of failing sections is identical for two consecutive sweeps (the evaluator keeps flagging the same unfixed issues), that is genuine stall, stop and drop-or-publish-as-is. Any whole-draft similarity check is cut from v1; if re-added later it ships off by default, threshold in config, applied only to the specific sections the evaluator flagged, never as a day-one terminator.

**Budget exhaustion has a defined terminal.** On token or wall-clock exhaustion mid-draft, the harness does NOT finalize the partial draft. It marks the assignment `dropped` with reason `budget_exhausted`, persists the ledger for audit, and never POSTs a partial body. This is the one place the loop-bound and the no-output-cap rule could collide: a truncated published body would LOOK like an output cap even though it was a loop bound. Dropping instead of truncating keeps the two cleanly separate.

**Batch wall-clock is a backstop, not the limiter.** Local concurrency is 1-2 articles at a time (GPU KV-cache). So `batch_wall_clock` is derived, not set independently: `batch_wall_clock >= ceil(N_MAX / concurrency) * per_article_wall_clock`, so a healthy full batch is never guillotined. N_MAX is the real fan-out bound; batch wall-clock only catches a hung process.

**Bounding a loop is not capping output.** Limiting how many times a loop runs is orthogonal to limiting how many tokens a single generation may emit. The harness has the first and refuses the second: no `max_tokens`, no `max_words`, no "in N words" or "be brief" prompt phrasing anywhere. The draft call runs to natural completion; only the NUMBER of drafts is bounded. Structured output at the finalize seam is obtained by validation-and-retry (B.3), not by truncation, and the `body` field is never length-constrained.

---

## PART B: The isolated-module architecture

### B.0 The external seam (the ONLY coupling), pinned to the verified platform code

This is the single contract that crosses the repo boundary. Everything in the pin below was read out of the live platform source, not paraphrased.

**Auth scopes (BLOCKER fix from the isolation critique).** The harness key MUST hold BOTH scopes:

```
["articles:write", "articles:publish-any"]
```

`articles:publish-any` is NOT standalone. `authenticate()` rejects with 403 `insufficient_scope` unless the key holds `articles:write` (publish.go:197-200), and ONLY then is the cross-persona author check (which consults `ScopePublishAny`, publish.go:127) ever reached. A key holding publish-any alone 403s before the author check. The earlier "the one publish-any key" framing would have made every run fail on day one. Provision the operator key with both scopes.

**The publish path: CLI, not raw HTTP (MAJOR fix #3).** `docs/ARCHITECTURE.md:144` states: "Agents publish through a thin CLI, never by calling the API by hand." The platform ships that CLI (`cli/main.go`) and a `cli/skill/SKILL.md`; it owns local validation (strict decode, `DisallowUnknownFields`), idempotency-key handling, retry, and distinct exit codes (`0` created-or-replayed, `2` auth, `3` validation/4xx). The harness publish client **shells the platform CLI** rather than re-implementing the wire contract. This inherits the platform's retry/validation/exit-code semantics for free and avoids exactly the class of bug (idempotency drift) the CLI already solved. The cost is a binary dependency on the platform CLI, which the harness pins as a versioned external tool. (Note: the platform CLI generates a RANDOM idempotency key when none is passed; the harness MUST pass `--idempotency-key` with its own content-derived key, see below.) Raw HTTP remains a documented fallback if the CLI is unavailable, but then the harness must mirror the CLI's local-validation and the corrected idempotency flow itself. We pick the CLI and own the consequence, rather than silently contradicting section 4.

**The body shape (verified field-for-field against `contracts/article.schema.json`):**

```jsonc
{
  "title":   "string, 1..300, required (300 is input-hygiene, NOT a content cap)",
  "body":    "string, minLength 1, NO maxLength, required (Markdown, never truncated)",
  "author":  "string, 1..120, required (persona id; free string, no FK platform-side)",
  "section": "string, 1..120, required (free string; harness enforces its own enum, see #7 fix)",
  "topics":  "string[] unique, optional",
  "slug":    "string, 1..200, /^[a-z0-9]+(?:-[a-z0-9]+)*$/, optional",
  "published_at": "RFC3339 date-time, optional",
  "metadata": "object, additionalProperties:true, optional (the ONE open extension point)"
}
```

The envelope is strict: `additionalProperties:false` at top level + the handler's `DisallowUnknownFields()` (publish.go:116). Any unknown top-level field is a hard 422. Any NEW required platform field silently breaks every harness publish. This makes schema drift a first-class hazard (#4 fix below).

**Idempotency (BLOCKER fix #2 from the isolation critique, reinforced by loops-critique #11).** The platform dedups by CONTENT HASH, not by key: `Apply` looks up the key, and if it finds the same key with a DIFFERENT content hash it returns `FailureIdempotencyConflict` -> 422 `idempotency_key_reused` (apply.go:110-115). The content hash is `SHA-256(title, body, author, section)` (article.go). Therefore:

- Do NOT mint the idempotency key before the body exists. The earlier design minted `hash(run_id + persona_id + angle-or-day)` BEFORE the loop ran; a crash-retry that re-ran a non-deterministic loop would present the SAME key with a DIFFERENT body -> hard 422, not the idempotent replay the doc promised.
- Mint and persist the key from the FINALIZED article at the moment FINALIZE produces it. Key on `assignment.id` (a unique PK, collision-free by construction) combined with the final content hash: `hash(assignment_id + content_hash)`. This is stable across retries (the assignment row and the stored body are both persisted before the POST) and cannot collide when the manager assigns the same persona two angles in one run.
- On crash AFTER finalize: replay the STORED body verbatim (same content hash -> same key -> true idempotent replay, 200, no double-publish).
- On crash BEFORE finalize: fresh attempt, fresh key.
- The `assignments.idempotency_key UNIQUE` index stays; its value derives from the finalized article, never from `(run, persona, angle)`.

**Schema as a governed contract (MAJOR fix #4).** The harness does NOT read the platform's schema file across repos (that would be filesystem coupling and break the isolated-repo claim) and does NOT keep an unmanaged copy (that drifts silently). It VENDORS a pinned copy under a contract version, anchored to the schema's `$id` (`https://censurado.local/contracts/article.schema.json`). A CI contract test fetches the platform schema (from a published URL or a git submodule pinned to a release tag) and asserts the vendored copy still matches, failing loudly on drift. The seam is then a versioned artifact, not a hidden copy.

**`metadata` provenance namespace (MAJOR fix #5).** `metadata` is the ONLY top-level extension the strict schema permits (`additionalProperties:true`). The publish client MAY stamp a reserved harness namespace:

```jsonc
"metadata": { "newsroom": { "run_id": "...", "persona_id": "...", "model": "...", "sweeps": 3, "ledger_digest": "sha256:..." } }
```

Even if unused at v1, reserving the namespace means the day provenance must be visible platform-side, the non-breaking channel already exists. Note: stamping `metadata` changes the content hash only if the platform hashes it; it does not (hash is over title/body/author/section), so provenance is safe to add without disturbing idempotency.

**Section vocabulary (MINOR fix #7).** `section` is a free string at the seam (verified: the platform slugifies whatever it receives into `/section/<slug>` navigation, with no server-side enum). A harness typo or stray beat would silently create an orphan section page on the public site with no error. So the harness pins its OWN section enum (`tech | world | politics | economics`), vendored alongside the schema, and the publish client validates `section` against it locally before invoking the CLI, rejecting a stray section rather than minting one.

### B.1 Modules

This is ONE FastAPI process with internal Python modules, plus exactly TWO real process boundaries (the pragmatism critique's reframing, #3): the brain's own HTTP API (consumed by the frontend and the trigger) and the platform publish boundary (the CLI). The other "modules" are packages inside the process; their contract IS the function signature. They are listed as modules for code-organization clarity, not as a microservice mesh.

```
                         TWO REAL PROCESS BOUNDARIES
   [Frontend / Trigger] --HTTP--> [ Brain FastAPI ] --CLI--> [ Platform publish ]

   Inside the Brain process (in-process packages, function-signature seams):
     brain/        orchestrator: the workflow graph + bounded loops
     personas/     persona store (own SQLite, brain-owned, no external reader)
     inference/    the completion adapter (one chat contract, per-backend shims)
     research/     the websearch tool wrapper + claim-source ledger
     publish/      the publish client (vendored schema + section enum + CLI shell)
```

| # | Module | In (typed) | Out (typed) | Real boundary? |
|---|---|---|---|---|
| 1 | publish | finalized `PublishArticleInput` + assignment_id | `{id, slug}` or typed failure | YES (CLI to platform) |
| 2 | personas | persona_id / persona draft | `Persona` record | no (in-process repo) |
| 3 | inference | `ChatRequest` (messages, optional capability flags) | `ChatResponse` (text) | no (in-process; shells out per backend) |
| 4 | research | sub-question | ledger rows | no (in-process; calls websearch-skill) |
| 5 | presentation | author CRUD / synth request | persona records / 202+poll | YES (frontend over brain HTTP) |
| 6 | brain | trigger `{mode, n?}` | run record + published articles | n/a (the orchestrator itself) |

**Inference adapter, honest naming (MAJOR fix #6).** The adapter is NOT "fully agnostic." It speaks the OpenAI Chat Completions wire dialect, which the local llama.cpp server speaks natively, the cloud path reuses, and the `hermes` CLI reaches through a `/v1/chat/completions` shim. It is an **OpenAI-Chat-Completions-dialect adapter with per-backend shims**, not a neutral interface. Backend-specific fields (`grammar`/GBNF, `thinking`/`enable_thinking`, `max_stops`) are leaks of specific backends and are made explicitly optional and capability-gated via the provider config's flags (`supports_thinking`, `supports_grammar`). A backend lacking a capability degrades gracefully (e.g. no grammar -> finalize falls back to prose-then-parse, see B.3) rather than breaking. The shim-per-CLI-agent pattern is the stated cost of the dialect choice. Pydantic AI's `Model` abstraction absorbs some of this but does not make the dialect neutral, and the doc says so.

**Presentation does not block on inference (MINOR fix #8).** `POST /personas {description}` triggers LLM synthesis, which on a local Gemma with no output cap (correctly) can take seconds to minutes. So persona synthesis is asynchronous, mirroring runs: `POST /personas` returns `202 + poll handle`, exactly like `POST /runs`. The presentation layer never assumes a fast synchronous return.

### B.2 Control flow, with explicit termination guards

```
TRIGGER {mode: manual|express|managed, n?}        [deterministic]
   |
   v
INGEST + MANAGER  [BOUNDED ReAct loop]
   guards: max_manager_steps, tool-call budget, circuit breaker,
           forced "emit assignments now" terminal on cap,
           output clamp len(assignments) <= N_MAX        <-- N_MAX is the fan-out bound
   |
   v
FAN-OUT DISPATCH (concurrency 1-2 local)           [deterministic]
   |  for each assignment (sole fan-out point; journalists cannot spawn agents)
   v
  +-------------------- PER-ARTICLE PIPELINE --------------------+
  | RESEARCH      [BOUNDED loop]                                 |
  |   guards: max_research_steps, ledger-stall (S stalls),      |
  |           shared per-article token+wall-clock budget         |
  | OUTLINE       [deterministic single call]                    |
  | DRAFT  <--+   [model call, body UNBOUNDED, no max_tokens]    |
  |   |       |                                                  |
  |   v       | REVISE+feedback (steered)                        |
  | EVALUATE -+   [separate-endpoint PASS/REVISE+failing_sections]|
  |   guards: for sweep in range(MAX_SWEEPS); PASS -> break;     |
  |           no-progress = identical failing-section set x2;    |
  |           debits the shared per-article budget               |
  | ENRICH        [single pass, no re-search loop]               |
  | FACT-CHECK    [deterministic CitationVerify + 1 bounded revise]|
  | FINALIZE      [dedicated JSON call -> Pydantic validate +    |
  |                1 retry; mint idem key from content hash]      |
  +-------------------------------------------------------------+
   |  budget exhausted mid-pipeline -> assignment.dropped(budget_exhausted), NO POST
   v
COLLECT                                             [deterministic]
   |
   v
PUBLISH (shell platform CLI, --idempotency-key from content hash)  [deterministic]
```

Termination is now total: the manager, research, sweeps, enrich, and fact-check are each capped, and every sub-loop debits the one per-article budget, so the article budget is a real ceiling over their sum (not just over the sweep loop, which was the original gap). The only model-driven exit is the evaluator PASS, and `MAX_SWEEPS` backstops it.

### B.3 Orchestration choice

**Baseline: plain Python.** The outer workflow is an explicit `for assignment in manager_assignments[:N_MAX]:`; the inner loop is an explicit `for sweep in range(MAX_SWEEPS): draft(); v = evaluate(); if v.verdict == "PASS": break`. This is the control flow written so it reads top to bottom, which serves the showcase goal (demonstrate workflows + bounded loops clearly) better than a graph DSL where edges are inferred from return-type annotations. The pragmatism critique is right that for a 1-to-3x/day internal tool, readable code beats a framework whose cleverness hides the very distinction we are teaching.

**Pydantic AI 1.107.0 (MIT, Python >=3.10; no stable 2.x, 2.0.0bN prerelease only; verified PyPI 2026-06-23): used ONLY at the finalize seam.** Its earned weight is typed structured-output validation: FINALIZE prompts for JSON in a dedicated reasoning-free call, parses with the `PublishArticleInput` Pydantic model, and on validation failure re-prompts ONCE with the error. This honors the format tax (structure in its own call) and the no-output-cap rule (no `max_tokens`, body unbounded), and removes the GBNF footgun.

**Dropped from v1:**
- **`pydantic-graph` / per-node durable checkpointing.** At 1-to-3 runs/day on one host, the only failure to survive is "the box rebooted mid-run," and the correct recovery is "run it again." Exactly-once already lives at the publish seam (content-derived idempotency key persisted before POST + the platform's content-hash dedup). Per-node checkpointing is redundant with the one durability mechanism that matters. The durability story is one sentence: a run that dies is re-run from scratch; the persisted idempotency key makes the only side effect (publish) exactly-once, so re-running is safe. The `assignments` table is the audit trail, not a resume-checkpoint store. (Temporal/DBOS/Prefect are not on the roadmap.)
- **GBNF grammar-constrained finalize.** GBNF derived from JSON Schema is hand-maintained and carries the "did someone leave the `body` rule unbounded?" footgun. Pydantic-validate-and-retry-once is simpler, already in the stack, and footgun-free. Reserve GBNF only if the local Gemma is observed to produce malformed JSON often enough to matter, gated behind `supports_grammar`.

**Why not LangGraph:** more concept surface than a single-host, low-cadence tool needs, and it sells the same durable-checkpointing apparatus we just argued is redundant here. Kept only as a fallback if the build later grows multi-host or needs human-in-the-loop pause/resume across days.

### B.4 Reuse map

| Source | Verb | What |
|---|---|---|
| **gamentic** (github.com/hec-ovi/gamentic) | ADAPT | Port the proven shape: FastAPI + SQLite + llama.cpp/Vulkan + per-agent design + the Anna-mode `/v1/chat/completions` shim + `setup.html`/CLI/doctor guided setup. The Anna-shim-to-`hermes`-adapter analogy holds: Anna is already a CLI agent behind an OpenAI face, so the `hermes` path reuses a proven pattern. **Before porting, grep the gamentic checkout for `max_tokens`, `n_predict`, `n-predict`, and "words" and strip every length cap** (the global no-cap rule). The Plan 1 doc asserted a specific `max_tokens=400` / "UNDER 400 words" string; that exact string was not independently confirmable from the GitHub web view, so the instruction is the general one: ensure NO length cap of any kind is ported, whatever its exact form. ALSO, when adapting the llama.cpp/Vulkan serving (Strix Halo gfx1151, the `server-vulkan` image, `--n-gpu-layers 99`), set the container env `GGML_VK_PREFER_HOST_MEMORY=1` so model tensors and KV cache live in GTT (shared system memory), never the small BIOS VRAM carveout (verified against the ggml-vulkan source; optionally `GGML_VK_ALLOW_SYSMEM_FALLBACK=1` as a safety net). This is a serving/container-env setting, not the Python adapter. |
| **websearch-skill** (github.com/hec-ovi/websearch-skill, v0.1.0, 2026-06-22) | REUSE | Whole, as the research tool: MCP mode, five tools, `--json` Envelope, keyless multi-engine, SSRF guard, per-page-nonce untrusted fence. Verified against the repo. |
| **hermes** (the user's CLI agent) | ADAPT | One inference backend behind the dialect adapter, reached through a `/v1/chat/completions` shim. Capability-gated like every other backend. |
| **platform CLI** (`cli/main.go` + `cli/skill/SKILL.md`) | REUSE | The publish path (B.0). Shell it; do not re-implement the wire contract. |

### B.5 Persona SQLite schema

Brain-owned, in-process only. Sketch:

```sql
CREATE TABLE personas (
  id            TEXT PRIMARY KEY,             -- stable persona id; becomes article.author
  display_name  TEXT NOT NULL,
  beat          TEXT NOT NULL CHECK (beat IN ('tech','world','politics','economics')),
  who_i_am      TEXT NOT NULL,                -- identity / self description
  about         TEXT,                         -- public bio
  style         TEXT NOT NULL,                -- voice notes
  few_shots_pos TEXT,                         -- JSON array of positive exemplars
  few_shots_neg TEXT,                         -- JSON array of NEGATIVE exemplars (anti-homogenization)
  sources       TEXT,                         -- JSON array of preferred sources
  avatar_path   TEXT,                         -- profile picture
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

CREATE TABLE runs (
  id          TEXT PRIMARY KEY,
  mode        TEXT NOT NULL CHECK (mode IN ('manual','express','managed')),
  status      TEXT NOT NULL,                  -- queued|running|done|failed
  n_requested INTEGER,
  created_at  TEXT NOT NULL,
  finished_at TEXT
);

CREATE TABLE assignments (
  id              TEXT PRIMARY KEY,           -- the idempotency anchor (collision-free)
  run_id          TEXT NOT NULL REFERENCES runs(id),
  persona_id      TEXT NOT NULL REFERENCES personas(id),
  section         TEXT NOT NULL,              -- validated against the harness section enum
  angle           TEXT,                       -- the manager's per-assignment brief
  status          TEXT NOT NULL,              -- assigned|drafting|published|dropped
  drop_reason     TEXT,                       -- e.g. budget_exhausted
  final_body      TEXT,                       -- the FINALIZED article, stored for verbatim replay
  content_hash    TEXT,                       -- SHA-256(title,body,author,section)
  idempotency_key TEXT UNIQUE,                -- derived from (assignment_id + content_hash)
  ledger_digest   TEXT,                       -- audit anchor for metadata.newsroom
  published_id    TEXT,                       -- platform article id on success
  created_at      TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_assignment_idem ON assignments(idempotency_key);
```

`final_body` is persisted so a post-finalize crash replays the byte-identical body (true idempotent 200). `idempotency_key` is NULL until finalize and is derived from the content hash, never from `(run, persona, angle)`.

### B.6 Prompt folder layout

All prompts are versioned `.md` files, no length caps in any of them, no "in N words" phrasing:

```
prompts/
  persona/
    synthesize.md          # seed description -> persona draft
  manager/
    triage.md              # rank today's news, assign within N_MAX
  journalist/
    research.md            # generate 3-6 sub-questions, ground every claim
    outline.md
    draft.md               # persona-conditioned, body unbounded
    evaluate.md            # PASS|REVISE + failing_sections (separate endpoint)
    enrich.md              # single pass
    finalize.md            # JSON only, Pydantic-validated
```

### B.7 Automation layer

**v1: system cron + `--mode managed`.** One line. A cron entry fires the harness 1-to-3x/day. The brain is trigger-blind: one run-execution function, three entry points (`manual`, `express`, `managed`), and the `mode` enum is the entire trigger surface, so the automation layer can change without touching the brain.

If you later want a click-to-run UI with run history, **Windmill CE** (AGPLv3, Docker Compose) is the natural graduation and leaves the brain untouched. (The Plan 1 doc pinned Windmill 1.725.1; latest as of 2026-06-23 is 1.737.0 per github.com/windmill-labs/windmill/releases, but the choice is version-independent: it rests on the AGPL + Docker-Compose fit, not a specific release.)

**n8n is rejected:** overkill for a cron-plus-one-line need, and its fair-code license is a worse fit than Windmill's AGPL for a self-hostable open tool.

---

## PART C: Plan 2 build plan

Ten ordered, independently-testable steps. Every step's test hits the real entry point (an HTTP route, a CLI invocation, or the orchestrator's public function) through to its side effect, never a mock of an internal function. Frontend steps use Testing-Library-style component tests with network mocked at the boundary.

**One shared test fixture (pragmatism fix #4), built first and reused by every step:** a single in-repo FastAPI fake that serves both `/v1/chat/completions` (scriptable responses) and a stand-in for the platform publish path (records bodies, returns `{id, slug}`, dedupes by idempotency key, and models BOTH `articles:write` and `articles:publish-any` scopes so the 403 path is testable). Every e2e test points at this one fake. A single shared assertion runs in every test: no request body to `/v1/chat/completions` ever contains `max_tokens` (or any length cap).

| Step | Builds | Contract | Test (real entry point) |
|---|---|---|---|
| 0 | The shared fake server + repo skeleton + vendored schema and section enum | fake serves both endpoints; vendored schema matches platform | CI contract test: vendored schema == platform schema (drift fails loudly) |
| 1 | Inference adapter (dialect + capability gates) | `ChatRequest -> ChatResponse`; no `max_tokens` ever sent | drive adapter against the fake's `/v1/chat/completions`; assert capability degrade (no `supports_grammar` -> prose path); assert no length cap in any request |
| 2 | Persona store + `personas/` SQLite | CRUD typed records; brain-owned | call the store's public API; round-trip a persona incl. negative few-shots |
| 3 | Persona synthesis, async | `POST /personas -> 202 + poll` | hit the brain HTTP route; assert 202, poll to a full draft persona; assert it does NOT block synchronously |
| 4 | Research tool + ledger | sub-question -> ledger rows; `max_research_steps` + stall cap | drive a research loop of DISTINCT-but-useless searches against the fake; assert the cap halts it and the stall counter fires |
| 5 | Per-article pipeline (draft/evaluate sweep loop + finalize) | `for sweep in range(MAX_SWEEPS)`; PASS breaks; budget-exhaust -> drop | (a) scripted stub model PASSes on sweep 2 -> exactly 2 drafts; (b) never-PASS -> EXACTLY `MAX_SWEEPS` drafts then stop (assert exact count); (c) drive budget exhaustion mid-draft -> assert NO POST, assignment `dropped(budget_exhausted)`, no truncated body |
| 6 | Manager + fan-out | bounded ReAct, `N_MAX` clamp, sole fan-out point | (a) `len(assignments) <= N_MAX`; (b) manager step-cap hit -> emits PARTIAL manifest, does not fail run; (c) no cross-persona context bleed; (d) journalist graph has no edge that constructs another journalist or manager node |
| 7 | Publish client (shell platform CLI, section enum, idem key from content hash, metadata namespace) | both scopes required; content-derived key; section validated locally | (a) key missing `articles:write` -> 403 `insufficient_scope`; (b) finalized body POSTs once -> `{id,slug}`; (c) replay the same run POSTs the BYTE-IDENTICAL stored body -> 200, no double-publish; (d) stray section rejected locally before POST; (e) `metadata.newsroom` present and accepted |
| 8 | Trigger surface | three modes, one run-execution fn, brain trigger-blind | invoke all three entry points; assert identical run-execution path; assert the brain has no cron/n8n knowledge |
| 9 | Presentation frontend | HTTP-only, DB/inference-blind | Testing-Library: render author manager, drive create/edit/delete with user-event, MSW-mock the brain API, assert async via `findBy*`, cover disabled/invalid states |
| 10 | Cron wiring + end-to-end managed run | `--mode managed` fires the full pipeline | one cron-style invocation end to end against the fake: manager -> N journalists -> publish, assert N articles recorded, none truncated, idempotent on re-run |

**Compaction points (where to checkpoint context during the build):** after Step 1 (inference proven), after Step 5 (the one model-driven loop proven bounded), after Step 7 (the external seam proven exact, including the 403 and idempotent-replay paths), and after Step 10 (full run green). These are the four points where a fresh context can resume from a stable, tested artifact.

---

## Appendix: critiques rejected, and why

A few critique points were sound observations but I kept the original call; recording why so the decision is auditable:

- **"Drop pydantic-ai entirely" (pragmatism #1, strong form).** Rejected in its strong form. Plain Python is the baseline for the control flow (accepted), but pydantic-ai stays at the FINALIZE seam for typed structured-output validation, which is the one place a validated `PublishArticleInput` before publishing earns its weight and is cheaper than hand-rolling parse-and-retry plumbing. Dropping the GRAPH (accepted) is different from dropping the LIBRARY (rejected).
- **"Collapse review and judge" vs "judge must be a different model" (pragmatism #5 vs loops #5).** These pull in opposite directions; I took both correctly by collapsing them into ONE evaluator call AND requiring that one evaluator resolve to a different endpoint than the drafter (or degrade to rules). The collapse removes a redundant call; the different-endpoint requirement removes the correlated-blind-spot hazard. Neither critique is rejected; they are reconciled.
- **"Raw HTTP is a legitimate seam, keep it" (isolation #3, option b).** Rejected as the v1 default in favor of shelling the platform CLI (option a). Option b is defensible for isolation but requires the harness to mirror the CLI's validation and retry, which is exactly the drift the CLI exists to prevent. The binary dependency is the smaller cost. Raw HTTP stays documented as a fallback only.

All external facts above were verified live on 2026-06-23: platform code (`internal/publish/*.go`, `contracts/article.schema.json`, `cli/main.go`, `cli/skill/SKILL.md`, `docs/ARCHITECTURE.md`), pydantic-ai 1.107.0 on PyPI, websearch-skill v0.1.0 and gamentic on GitHub, and Windmill 1.737.0 on GitHub releases.

---

## Appendix: refinements from the agentic re-question (for Plan 2)

A second research pass re-asked all eight questions under the strict "agentic workflow" reading (the term of art, not a generic workflow) and audited this doc section by section. Verdict: the substance was already agentic-native and no design decision was skewed; the only required change was tightening A.2 above. The pass also surfaced a few local-model-specific build details worth carrying into Plan 2. They refine the sections above, they do not replace them.

- **Persona synthesis uses direct role-play framing, not an interview.** Gemma-3-class local models are specifically weak at holding a persona, so the contrastive positive-and-negative few-shots matter more here than on a frontier model, and the synthesis prompt tells the model to BE the persona rather than describe it.
- **The draft node re-injects the persona fresh every call and runs warmer.** The "helpful assistant" attractor lives in the weights, so a persona stated once drifts back to neutral over a long generation; re-inject it each node. A higher draft temperature fights voice homogenization across journalists, and journalists never see each other's drafts in context (that closed loop is what averages voices into mush).
- **Fact-check and enrich run persona-blind.** Expert-or-persona framing measurably degrades factual accuracy, so the verification pass drops the persona and checks claims against the ledger plainly.
- **Context is engineered against "context rot."** Recall degrades as the window fills, starting before the hard token limit, and the local Gemma window is smaller than a frontier model's. So: just-in-time retrieval (pass IDs and URLs, load bodies only when a tool needs them), compaction of a loop's trajectory into the ledger near the limit, and artifact handoffs instead of transcripts.
- **Why a too-small output cap can CAUSE a runaway loop.** A truncated generation fails its completion check, which triggers another iteration; the documented failure mode is a loop that burns its whole budget because each turn was cut off mid-thought. This is the concrete reason the global no-output-cap rule and the loop bound are complementary: bound the number of turns in the harness, and let each turn finish naturally.

---

## Appendix: news freshness, non-repetition, and continuity (coverage memory)

A requirement added during Plan 2: the harness must publish FRESH news, must NOT republish a story it has already covered, and must REFERENCE and EXTEND prior related coverage as a story evolves day to day (new information surfaces daily, and related topics build on each other). This is the "coverage memory."

**Source of truth.** The harness is the SOLE publisher, so its own record of what it has published is authoritative; it does not need to scrape the platform. A `coverage` view over the `assignments` table (or a dedicated `coverage` table) holds, per published article: `published_id` / `slug`, `section`, `topics`, key entities, headline, `published_at`, `content_hash`, and a cheap similarity fingerprint (a normalized title plus entity and topic shingles; an embedding is optional and later). It may be reconciled against the platform's RSS or latest feed as a cross-check, but the harness record leads.

**Where it acts.**
- **Manager triage (Step 6)**, before assigning, classifies each candidate story against recent coverage into one of three:
  - DUPLICATE (same event, no new information) -> DROP, do not reassign.
  - FOLLOW-UP (same topic, new developments today) -> assign with the prior article(s) attached by slug or URL and an instruction to cover ONLY what is new and to link and cite the prior coverage ("as reported on <date>").
  - NEW -> a normal assignment.
  The classification is rules-first (a deterministic fingerprint and threshold) with the manager model making the final follow-up-versus-drop-versus-new call over the retrieved related coverage, consistent with the design's "rules where they suffice, model for judgment" stance.
- **Research (Step 4)** is time-filtered toward recent sources, and the related prior coverage is passed to the journalist as an artifact (slugs, headlines, key claims) so the draft references it and does not repeat it. This rides the existing just-in-time and artifact-handoff context discipline; it adds no new transcript channel.

"Organized" falls out of keying coverage by section, topic, entity, and date, which also lets the manager group related stories. This addition touches the persona and brain SQLite (a coverage view or table, B.5), the manager (Step 6), and research (Step 4); it is designed in full when those steps are built. It does not change the publish seam or the no-output-cap rule.

---

## Appendix: image generation (future feature, not in the v1 build)

A later iteration should let an article carry a generated image (a hero image or inline figures), the way the user's `gamentic` repo already solves it: a local image backend driven by reusable ComfyUI workflow templates (a JSON graph with the prompt and seed parameterized), served alongside the text model. This is a deliberate FUTURE addition, not part of the ten-step v1 build, and it is recorded here so the seam choice does not paint it into a corner.

How it fits without disturbing what is built:

- **Reuse, do not rebuild.** Port gamentic's ComfyUI template approach (the parameterized workflow JSON plus the call/poll client) the same way the text inference adapter was ported from gamentic, and serve it on the same local box. Like the text model, it carries no output cap that would matter; an image backend has its own size parameters, which are not the "never cap LLM output length" concern.
- **A new pipeline node, persona-blind, bounded.** Image generation slots in as one more bounded node in the per-article pipeline (after finalize, or as an enrich-time figure step), with its own iteration cap and its share of the per-article budget, exactly like every other sub-loop. It is NOT a new fan-out point; the manager stays the sole one.
- **The publish seam needs no breaking change.** The platform's `body` is Markdown, so a generated, hosted image embeds as a normal Markdown image (`![alt](url)`) in the body with zero schema change. A structured image reference (caption, alt text, dimensions) can instead ride the `metadata` open namespace (`metadata.newsroom.images`), which the strict envelope already permits, until the platform grows a first-class image field. Either path leaves the content-hash idempotency intact (the hash is over title/body/author/section; embedding in the body changes the body and thus the hash, which is correct, while a metadata-only reference does not affect it).

This reverses the platform-side media removal only for the brain: the platform stays media-light, and the brain generates and attaches imagery on its side, publishing through the same single HTTP contract.
