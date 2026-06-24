# censurado-web-brain

The agentic newsroom for [censurado-web](https://github.com/hec-ovi/censurado-web). AI journalist personas research the day's news, write full articles in their own voice, an art director gives each one a locally generated hero image, and the brain publishes them to the portal over one HTTP contract. It is built as an **agentic workflow hosting bounded agentic loops**: deterministic code owns the control flow, the model does the work inside each step, and guards at every seam make each run terminate.

![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)
![Python](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)
![Status: early](https://img.shields.io/badge/status-early-orange.svg)
![Agentic workflow](https://img.shields.io/badge/agentic-workflow-7c3aed.svg)
![Agentic loop](https://img.shields.io/badge/agentic-loop-0ea5e9.svg)

The portal stays non-agentic and never learns that personas exist. This repo is the only agentic part of the system, and the two meet at exactly one seam, the publish API.

---

## Agentic workflow

An agentic workflow orchestrates LLM and tool steps through predefined code paths: the developer owns the edges, the model does the work inside the nodes. This is the "workflow" half of the workflow-vs-agent distinction that current practice treats as load-bearing (Anthropic, [Building Effective AI Agents](https://www.anthropic.com/research/building-effective-agents)). Nodes reason and call tools with a model; the edges between them are plain code, so a run is deterministic and re-runnable top to bottom.

The brain's outer shape is one such workflow:

```
resolve roles  ->  manager triage  ->  fan-out dispatch  ->  per-article pipeline  ->  publish
```

- **Trigger-blind core.** One `execute_run` path serves every trigger. The `mode` (`manual`, `express`, `managed`) is resolved once by `plan_run` into a run scope, and `execute_run` never branches on it again. A source-level test keeps the path mode-blind, so adding a new trigger never forks the engine.
- **Sole fan-out.** `dispatch_run` is the only place the workflow spawns workers, and the manager is the only node that decides how many (clamped to `N_MAX`). No node below the manager may spawn another agent, which makes runaway recursion structurally impossible.
- **Typed handoffs.** Each node takes a typed input and returns an artifact (the outline, the draft, the claim-source ledger), never another node's transcript. A node's model can be swapped without touching its neighbors, and each journalist runs in its own context, so personas do not average into one voice.

## Agentic loop

An agentic loop is model-driven control flow: perceive, reason, act, observe, repeat, where the model decides whether to continue. That is also where the danger lives, because a model has no built-in concept of "done": left alone it runs on, or stops early, on its own judgment. A loop is safe only when code outside the model enforces the exit.

Every loop in the brain carries a monotonic, harness-enforced bound the model cannot reset, plus the standard guard set:

- **The research loop** (`run_research`) plans 3 to 6 sub-questions, searches each, and fills the ledger. It stops on `max_research_steps`, on a ledger-stall detector (a step that adds no new source URL counts as no progress), or on the shared budget. The stall detector catches what a circuit breaker misses: several distinct searches that each return nothing new.
- **The per-article sweeps** draft, then grade with a separate evaluator that returns `PASS` or `REVISE` with failing sections. The loop exits on `PASS`, on `MAX_SWEEPS`, or when the failing-section set repeats two sweeps running (a genuine stall). The evaluator resolves to a different endpoint than the drafter, or degrades to a rules-grounded check, so the drafter never grades itself.
- **A shared per-article budget** (token plus wall-clock) is debited by every sub-loop, so the article budget is a true ceiling over the sum of research, drafting, and enrichment, not a per-loop afterthought.

Bounding a loop is not capping output. The number of drafts is bounded; a single generation is never length-limited. There is no `max_tokens`, no word ceiling, no "be brief" anywhere in the prompts. When a budget runs out mid-draft, the assignment is **dropped** with reason `budget_exhausted` and its ledger kept for audit; a partial body is never published, which keeps the loop bound separate from a truncated output.

## How the two compose

They compose by altitude: the deterministic workflow hosts the bounded loops, and the guards live exactly where a workflow node hands control to a model-driven loop. The workflow owns the cap, the budget, and the progress detector that bring control back. The manager is the one seam where the workflow lets the model decide how many workers to spawn, and even that decision is clamped. This is the hybrid that current practice converges on: structured enough to be reliable, flexible enough to absorb the variance of real news.

## How it fits censurado-web

- The portal serves a static archive to readers and exposes one authenticated write API (`POST /articles` for one article, `POST /articles:batch` for many, plus `POST /media` for image bytes). It never learns that personas exist.
- This repo owns the personas (its own SQLite), the prompts (versioned `.md` files), and the agents that turn the day's news into finished articles.
- It authors each article as the persona who wrote it, using a single operator key that carries both the `articles:write` and `articles:publish-any` scopes. That key is the only coupling between the two systems.
- A run's finished articles publish together in one atomic batch (`POST /articles:batch`): the portal validates every item, then stores all of them or none, and each item carries its own content-derived idempotency key so a resend never doubles. Turn `NEWSROOM_PUBLISH_BATCH` off to fall back to one `POST /articles` per article.
- A generated hero image is uploaded to the portal and referenced by URL in the article's open `metadata.image` field, so attaching imagery needs no change to the article schema, and the image is outside the content hash, so it never disturbs idempotency.

## Run modes

One HTTP surface drives the brain, and the trigger is just a mode on `POST /runs`:

- `manual` runs a chosen journalist or subset (the operator may override `n` and `persona_ids`).
- `express` runs a small default batch.
- `managed` lets the manager pick today's news and assign journalists, up to `N_MAX`.

Any run can toggle the art director with an `images` flag on `POST /runs` (the CLI mirrors it with `--images` / `--no-images`); when omitted it follows the `NEWSROOM_AUTO_GENERATE_IMAGE` default.

The rest of the surface: `POST /personas` (returns `202` and a synthesis job to poll), `GET /personas`, `GET /runs/{id}` for a run's assignments and outcomes (including each article's `image_url`), and `GET /health`.

## Status

Early, but built end to end. The brain runs today: the persona store and async synthesis, the inference adapter, the bounded research loop and ledger, the per-article pipeline (draft, evaluate, finalize), the art director that writes a FLUX.2 image brief and renders a hero image on a local ComfyUI (then uploads it to the portal's `POST /media` and attaches it via `metadata.image`), the manager and fan-out, the raw-HTTP publish client (one atomic `POST /articles:batch` per run, with a per-article fallback), the trigger surface (`manual`, `express`, `managed`) over HTTP, the author-manager console (a buildless vanilla-JS frontend served by nginx, in `frontend/`), and the automation layer: a one-shot run command (`censurado-brain --mode managed`) for a periodic trigger, plus a Docker compose that runs the console next to the brain. The whole suite is green, including end-to-end image-generation tests driven against an in-repo ComfyUI fake; the FLUX.2 reference workflow wants one live smoke-test against the box, and the portal's media endpoint is the matching in-flight piece on the platform side. The architecture, including the image-generation seam, is written up in `docs/research/stage-2-newsroom-architecture.md`, and `AGENTS.md` is the operational map of the codebase.

## Layout

```
newsroom/            the brain package (one process, isolated sub-packages)
  brain/             the FastAPI app and persona synthesis
  runner/            the trigger-blind run orchestrator (manual/express/managed)
  manager/           the bounded triage agent and the sole fan-out
  pipeline/          the per-article draft/evaluate/finalize loop, plus the art-director step
  research/          the bounded research loop and claim-source ledger
  personas/          the persona store (own SQLite, brain-owned)
  runs/              the runs and assignments store (the idempotency anchor)
  publish/           the raw-HTTP publish client and the media (image) uploader
  imagery/           the ComfyUI client, FLUX.2 workflow templates, and the illustrator
  inference/         the completion adapter (OpenAI-dialect, per-backend shims)
  contracts/         the vendored article schema, section enum, and content hash
  cli.py             the automation entry point: a one-shot run that picks a mode
frontend/            the presentation layer: buildless vanilla JS + nginx, talks to the brain over /api
prompts/             versioned .md prompts (persona, manager, journalist, art_director)
testkit/             the shared in-repo fake (chat + publish + media + ComfyUI), used by every test
tests/               end-to-end tests that drive the real entry points
docs/research/       the architecture writeup
AGENTS.md            the operational map of the codebase (what each part does, then its contract file)
Dockerfile           the brain image (uvicorn serving the FastAPI surface)
deploy/              docker compose (brain + console + optional local model) and the trigger
```

## Develop

The toolchain is [uv](https://docs.astral.sh/uv).

```
make install   # create .venv and install the package with dev deps
make test      # run the suite
make lint      # ruff
```

Every test hits a real entry point (an HTTP route, a CLI invocation, or the orchestrator's public function) through to its side effect, not a mock of an internal function. One assertion runs everywhere: no request to the model ever carries an output-length cap.

## Principles

- **Isolated layers behind contracts.** Presentation only consumes the brain's API, inference is agnostic to which model or runtime answers, and the trigger is agnostic to what the brain does. Each layer has its own tests and can be swapped without touching the others.
- **Local-first and self-hostable.** The default path runs entirely on your own hardware: a local Gemma served by llama.cpp for text, and a local ComfyUI running FLUX.2 klein for images, with no hosted API required. Cloud and CLI-agent adapters plug in behind the same completion interface, and the image backend sits behind its own swappable client.
- **No output-length caps.** Article generation never sets a token, word, or sentence ceiling. Only the number of loop iterations is bounded; the model finishes on its own. Image size and step count are render parameters, not output caps.

## License

MIT. See [LICENSE](LICENSE).
