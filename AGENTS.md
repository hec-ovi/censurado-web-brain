# AGENTS.md

A map of `censurado-web-brain` for an agent (human or model) that needs to work in it
fast. It explains, in words, what each part does, how it fits, and what it expects,
then points at the file that IS the contract. It does not restate the code; open the
pointed-at file for exact signatures.

The deep design rationale (the eight questions, the seam pins, the build plan) lives in
`docs/research/stage-2-newsroom-architecture.md`. This file is the operational map.

This file maps the codebase (for working IN the brain). To DRIVE the running brain
(trigger runs, edit authors and sources, tune editorial controls), use its HTTP API,
summarized under [Operating the running brain](#operating-the-running-brain) below.
To AUTHOR and publish an article yourself, the publish contract and the editorial bar
live in the harness `cli/AGENTS.md` (the publishing skill), since the publish API and
the operator token sit in the harness.

## What this is

The brain is an agentic newsroom: AI journalist personas research the day's news, write
full articles in their own voice, an art director gives each a hero image, and the brain
publishes them to the `censurado-web` portal over one HTTP contract. It is one FastAPI
process. There are exactly two real process boundaries:

```
[Frontend / cron] --HTTP--> [ Brain (FastAPI) ] --HTTP--> [ Platform: POST /articles:batch, /articles, /media ]
                                       \--HTTP--> [ ComfyUI: /prompt /history /view ]
```

Everything else is in-process packages whose contract is the function signature. The
shape is a deterministic workflow (code owns the edges) hosting bounded loops (the model
works inside the nodes), with a guard at every seam so every run terminates.

Two invariants hold everywhere, enforced by tests:
- **No output-length cap, ever.** No `max_tokens`/`max_words`/"in N words" on any model
  call. Only loop counts and a per-article budget are bounded; a single generation always
  finishes. Image width/height/steps are render parameters, not length caps. The guard is
  `testkit/assertions.py` (a wire-level 422 in the fake + a conftest teardown assert).
- **One publish seam.** The brain and the portal meet only at the platform HTTP API. The
  brain owns personas, prompts, and the agents; the portal has no persona concept (it
  stores `author` as a free string).

## Run flow (the outer workflow)

`resolve roles -> manager triage -> fan-out dispatch -> per-article pipeline -> publish`.
The brain is trigger-blind: the only thing a managed trigger picks is a `mode`
(`manual|express|managed`), resolved once into a `RunScope`; the execution path never
branches on mode again. Two side entry points reuse the same dispatch+publish tail
(`_dispatch_publish_finish`) with a different front: `execute_direct` (write one article
from an operator brief, bypassing the manager, corroboration forced off) and
`execute_batch` (`plan_batch` runs the manager ONCE PER AUTHOR over each author's own
source-scoped, time-filtered discovery search, then merges the per-author manifests).
- → `newsroom/runner/run.py` (`plan_run`, `start_run`, `execute_run`, `RunScope`,
  `RunDeps`, `RunReport`; `execute_direct`/`run_direct`, `execute_batch`/`run_batch`,
  `_dispatch_publish_finish`). `execute_run` is the single managed path; a source-guard
  test keeps it mode-blind.
- → `newsroom/manager/batch.py` (`plan_batch`: per-author manager fan, round-robin
  interleave, no cross-author dedup, overall clamp).
- → `newsroom/runner/deps.py` (`build_run_deps`, `roles_for_settings`) assembles the real
  seams from settings; tests inject in-process doubles for the network seams
  (`search_news`, `make_ledger`, `illustrate`) so a run never leaves the box.

## Components

### Inference (the text-model adapter)
One function over the OpenAI Chat-Completions wire dialect; backends differ only by the
values in a resolved `ProviderConfig`, with no per-backend subclasses. Roles (`drafter`, `evaluator`,
`finalize`, `manager`, `art_director`) resolve from the env cascade
`NEWSROOM_ROLE_<ROLE>_* -> NEWSROOM_INFERENCE_* -> dialect default`. One retry on connect
errors only; never sends a length cap.
- → `newsroom/inference/adapter.py` (`chat`, `ChatRequest`, `ChatResponse`, `ToolCall`)
- → `newsroom/inference/provider.py` (`ProviderConfig`, `resolve`, `DIALECTS`, `endpoint_id`)

### Imagery (the art director's rendering backend)
The parallel seam to inference, for images. It drives a local ComfyUI running FLUX.2
klein. `ComfyClient` hides ComfyUI's async wire protocol (submit a graph, poll history,
fetch the PNG; upload a reference) behind one synchronous `generate`. `graph.py` fills a
checked-in API-format template by node id, and chains `LoadImage -> VAEEncode ->
ReferenceLatent` when reference images are supplied (FLUX.2 native reference
conditioning). `references.py` collects source `og:image` URLs from the ledger
(best-effort, untrusted). `Illustrator` composes references + the art-director LLM call +
ComfyUI + the media upload into the `illustrate` seam the pipeline calls; a hard failure
raises (per-reference download failures are skipped), and the pipeline's
`_art_direct_image` catches it and degrades to no image.
- → `newsroom/imagery/comfy_client.py` (`ComfyClient`, `ComfyError`)
- → `newsroom/imagery/graph.py` (`build_graph`, `TEMPLATES`) + `templates/flux2_klein_t2i.json`
- → `newsroom/imagery/references.py` (`collect_reference_images`, `ReferenceImage`)
- → `newsroom/imagery/illustrator.py` (`Illustrator`, `ImageResult`)
- Expects: a reachable ComfyUI at `NEWSROOM_COMFYUI_BASE_URL` with `flux-2-klein-4b`,
  `qwen_3_4b` (CLIPLoader type `flux2`), and `flux2-vae` installed. The reference graph is
  built from documented nodes and has not yet been smoke-tested against a live box.

### Research (the bounded grounding loop)
Plan 3 to 6 sub-questions, search each into a URL-deduplicated claim-source ledger. Not
model-driven: bounded by `max_research_steps`, a ledger-stall detector (a step adding no
new URL is no progress), and the shared per-article budget.
- → `newsroom/research/loop.py` (`run_research`, `plan_subquestions`, `ResearchOutcome`)
- → `newsroom/research/ledger.py` (`Ledger`, `LedgerRow`, `digest`)
- → `newsroom/research/tool.py` (`ResearchTool`, wraps the `websearch-skill` dependency)

### Personas (brain-owned identities)
Typed CRUD over the brain's own SQLite. A persona's `id` becomes the article `author` at
the publish seam (a free string platform-side). Carries positive AND negative voice
exemplars (local models lean on contrast).
- → `newsroom/personas/store.py` (`Persona`, `PersonaStore`, `open_store`, `slugify`)
- → `newsroom/brain/synthesis.py` (`synthesize_persona`, async via `POST /personas`)

### Per-article pipeline (the one model-driven loop)
Drives one assignment: `outline -> draft/evaluate sweeps -> enrich -> fact-check ->
respin x2 -> finalize -> art-direct`. Three simultaneous guards bound the sweep loop
(`MAX_SWEEPS`, the evaluator's PASS, an identical-failing-section-set stall); after
fact-check the author re-spins its own article in its own voice (`respin_passes`,
default 2) against an anti-slop / redundancy / staged-format rubric, preserving every
grounded fact and citation; finalize then lifts `title + subtitle + summary + body +
topics + slug + keywords` and stamps the dek/summary into
`metadata.subtitle`/`metadata.description` and the article-specific SEO terms into
`metadata.keywords`. After finalize (before the content hash) a widget step
(`attach_widgets`) inspects the grounded ledger: for each cited tweet/youtube it captures
a keyless snapshot and emits an inline body marker (`{{tweet:id}}` / `{{video:id}}`, only
when a real snapshot/available video exists), storing the snapshot in
`metadata.tweets[]`/`metadata.media_checks{}` (outside the hash); and it emits ONE
`{{relacionado:slug}}` for the best prior-coverage match. The Go generator renders these
markers statically. A shared `ArticleBudget`
(token + wall-clock) is debited by every stage and DROPS the assignment on exhaustion
(never truncates). The persona is re-injected warm on each draft; enrich/fact-check run
persona-blind; the art director runs persona-AWARE so the image matches the byline. The
image step is best-effort after finalize: it stamps `metadata.image` (outside the content
hash, so idempotency is unaffected) and never drops a finalized article.
- → `newsroom/pipeline/article.py` (`run_article_pipeline`, `ArticleOutcome`, `_art_direct_image`)
- → `newsroom/pipeline/evaluate.py` (`evaluate_draft`, `Evaluation`)
- → `newsroom/pipeline/factcheck.py` (`fact_check`, `citation_verify`, `CitationResult`)
- → `newsroom/pipeline/article.py` (`_respin`, the voiced 2-pass self-revision) + `prompts/journalist/respin.md`
- → `newsroom/pipeline/finalize.py` (`finalize_article`, pydantic-ai structured output: title/subtitle/summary/body/topics/slug/keywords)
- → `newsroom/pipeline/widgets.py` (`attach_widgets`: ledger -> tweet/video/related markers + snapshots; never raises) and `newsroom/embeds/` (keyless capture: fxtwitter, youtube oEmbed, the recheck sweep)
- → `newsroom/pipeline/artdirect.py` (`art_direct`, `ArtDirection`)
- → `newsroom/pipeline/budget.py` (`ArticleBudget`) and `context.py` (`persona_block`, `ledger_text`)

### Manager + fan-out (the sole spawn point)
The manager triages today's news against recent coverage (DUPLICATE/FOLLOW_UP/NEW) and
emits assignments clamped to `N_MAX`; it is a bounded ReAct loop. `dispatch_run` is the
ONLY place workers spawn, on a small thread pool (GPU KV-cache means concurrency 1 to 2);
it mints one budget per article and forwards the `illustrate` seam. No node below it may
spawn another, so runaway recursion is structurally impossible.
- → `newsroom/manager/manager.py` (`run_manager`), `triage` prompt, coverage classifier
- → `newsroom/manager/dispatch.py` (`dispatch_run`, `DispatchResult`, `LedgerBuilder`)
- → `newsroom/manager/coverage.py` (`CoverageStore`), `preflight.py` (`ResolvedRoles`, `resolve_roles`)

### Runs store + identity
The runs/assignments lifecycle and the idempotency anchor. An assignment moves
`assigned -> drafting -> ready -> published | publish_failed | dropped`. At finalize the
body, content hash, idempotency key, and (if generated) `image_url` are persisted before
any POST, so a crash after finalize replays the byte-identical body.
- → `newsroom/runs/store.py` (`RunStore`, `Run`, `Assignment`, `finalize_assignment`)
- → `newsroom/db.py` (the SQLite `SCHEMA`: `personas`, `runs`, `assignments`, `coverage`)
- → `newsroom/contracts/hashing.py` (`content_hash` over title/body/author/section only; `idempotency_key`)

### Publish + media (the platform boundary)
Raw HTTP to the portal. By default a run's ready articles publish TOGETHER in one atomic
request: `publish_batch` POSTs them to `/articles:batch` (each item carries its own
content-derived `idempotency_key`, since one HTTP header cannot carry N keys), the
platform validates every item then stores all or none, and `publish_batch_assignments`
fans the side effects (mark published, one coverage row) back to each assignment.
`publish_article` is the per-article fallback (`POST /articles` with the
`Idempotency-Key` header), used when `NEWSROOM_PUBLISH_BATCH` is off; both run the same
local pre-checks (`local_publish_check`: section validity, content/key drift) so a bad
item never reaches the wire. Before sending the batch, `publish_batch_assignments`
de-collides slugs on the DERIVED permalink (the platform's rule, ported in
`contracts/slug.py`): a within-batch slug clash would 422 the whole atomic batch, so the
later item is pinned to a unique slug.
`upload_media` POSTs a generated PNG's raw bytes to `/media`
(scope `articles:write`, no idempotency key, content-addressed) and returns a
`/media/<sha>.png` URL the pipeline stamps into `metadata.image`.
- → `newsroom/publish/client.py` (`publish_article`, `publish_batch`, `build_payload`, `PublishResult`, `BatchResult`)
- → `newsroom/publish/media.py` (`upload_media`, `MediaAsset`, `MediaUploadError`)
- → `newsroom/publish/service.py` (`publish_assignment`, `publish_batch_assignments`)
- → `newsroom/runner/run.py` (`_publish_ready`: batches the run's ready articles, or falls back per-article)

### Contracts (the cross-repo seam, vendored)
The article and batch shapes are pinned copies of the platform schemas, governed by a
drift test so they cannot silently diverge. The brain pins its own closed section enum
(the platform treats `section` as a free string). Media rides in the open `metadata` bag,
no schema change.
- → `newsroom/contracts/article.py` (`PublishArticleInput` strict, `FinalizedDraft`)
- → `newsroom/contracts/schema.py` (loaders + the pinned `$id`s for all three contracts)
- → `newsroom/contracts/vendored/v1/*.schema.json` (`article`, `batch-request`, `batch-response`; + `SOURCE.md`, do not hand-edit)
- → `newsroom/contracts/hashing.py` (`content_hash`, `idempotency_key`), `slug.py` (`derive_slug`, ported from the platform)
- → `newsroom/contracts/sections.py` (`SECTION_ENUM`, `is_valid_section`)

### Brain HTTP surface
The FastAPI app. `POST /personas`, `POST /runs`, `POST /articles/from-link`, and
`POST /runs/batch` all return `202 + poll` and run the model work off the request
(background thread, one shared-connection lock). `POST /runs` accepts
`{mode, n?, persona_ids?, images?}`; `POST /articles/from-link` takes a brief +
0..N links + focus (direct mode); `POST /runs/batch` takes
`{persona_ids?, timeframe?, max_total?, images?}` (the per-author sweep); `GET /runs/{id}`
surfaces each assignment incl. `image_url`.
- → `newsroom/brain/app.py` (`create_app`, `RunRequest`, `DirectBriefRequest`, `BatchRequest`, routes)
- → `newsroom/cli.py` (`censurado-brain --mode ...` automation entry; `direct` / `batch` / `embeds recheck` verbs, `[--images/--no-images]`)
- → `newsroom/config.py` (`Settings`: all `NEWSROOM_*` env, incl. the imagery knobs; no length setting by policy)

### Frontend (the author-manager console)
Buildless vanilla ES modules served by nginx; no framework, no bundler. Talks to the
brain only over `/api` (nginx strips the prefix), polls the 202 surfaces, and previews
generated hero images (proxied via `/media/`). Component factories build DOM with the
`el`/`field` helpers; image src is gated by `isSafeImageSrc`.
- → `frontend/src/api.js` (the only brain client), `frontend/src/poll.js`
- → `frontend/src/components/` (`runPanel.js` has the images toggle + hero thumbnail;
  `personaForm.js`, `personaList.js`, `health.js`; `el.js` for `el`/`field`/`isSafeImageSrc`)
- → `frontend/nginx.conf` (the `/api` and `/media` proxies + CSP)

### Prompts
Versioned `.md` files with `{{TOKEN}}` placeholders; no front-matter, no length caps. The
loader is two functions.
- → `newsroom/prompts.py` (`load_prompt`, `render`)
- → `prompts/journalist/*.md`, `prompts/manager/triage.md`, `prompts/persona/synthesize.md`,
  `prompts/art_director/illustrate.md`

### Tests + the shared fake
Every test drives a real entry point (HTTP route, CLI, or orchestrator function) through
to its side effect against ONE in-repo fake that stands in for the platform (`/articles`,
`/articles:batch`, `/media`), the inference backend (`/v1/chat/completions`), and ComfyUI
(`/prompt`, `/history`, `/view`, `/upload/image`). The fake imports from `newsroom`, never
the reverse.
- → `testkit/fake_server.py` (`create_fake_app`, `FakeState`), `testkit/assertions.py` (the no-cap guard)
- → `tests/` (e.g. `test_publish_batch.py`, `test_imagery.py`, `test_media_upload.py`, `test_article_pipeline.py`, `test_runs_http.py`, `test_schema_drift.py`)

### Infra
- → `Dockerfile` (the brain image: uvicorn serving `create_app --factory`)
- → `deploy/docker-compose.yml` (brain + console + optional local model) and `deploy/.env.example`
- → `deploy/crontab.example` (the periodic trigger: `censurado-brain --mode managed`)

## External seams to know

- **Publish payload** (`POST /articles`): required `title, body, author, section`;
  optional `topics, slug, published_at, metadata`. Strict envelope (unknown top-level
  field is a hard error). Auth needs BOTH `articles:write` and `articles:publish-any`.
- **Batch publish** (`POST /articles:batch`): body `{"articles": [...]}`, each item the
  article shape plus a required per-item `idempotency_key` (no `Idempotency-Key` header).
  Atomic: any invalid item is a `422` with a per-item error list and nothing is written;
  idempotent per item on resend. Success is `{"results": [{index, id, slug, status}]}`,
  `201` if any created else `200` (all deduplicated). Default `<= 500` items.
- **Media** (`POST /media`): raw image bytes as the body (not multipart), `articles:write`,
  returns `{url, sha256, ...}`. The portal renders `metadata.image` (+ `metadata.image_alt`,
  `metadata.youtube`, `metadata.video`) from the open metadata bag.
- **ComfyUI**: `POST /prompt {prompt, client_id} -> {prompt_id}`, poll
  `GET /history/{id}`, fetch `GET /view`, upload references `POST /upload/image`.

## Operating the running brain

The brain has no auth; treat it as a trusted-network service. In the harness it sits
behind the console nginx proxy at `http://127.0.0.1:8083/api/<path>` (the container
listens on `:8000`); standalone it binds `127.0.0.1:8722`. Errors come back as
`application/problem+json`. This is the run/edit surface; for authoring an article by
hand and the editorial bar, see the harness `cli/AGENTS.md`.

**Drive a run** (async: returns `202` + a `run_id`, then poll `GET /runs/{id}` until
`done` / `done_with_errors` / `failed`):

- `POST /runs {"mode": "managed|express|manual", "n"?, "persona_ids"?, "images"?}`.
  `managed` triages live news and assigns across authors; `express` is a smaller
  batch; `manual` honors an explicit `n` / `persona_ids` subset. CLI:
  `censurado-brain --mode managed --n 4`.
- `POST /articles/from-link {"persona_id", "brief"?, "links"?, "focus"?}`: one author
  writes one piece from a brief, bypassing the manager (the corroboration gate is
  forced off because you vouched for the source; every other gate still runs). CLI:
  `censurado-brain direct --persona <id> --brief "..." --link <url>`.

**Authors and sources.** `GET /personas` and `GET /personas/{id}` (the full record:
`who_i_am`, `about`, `style`, `few_shots_*`, `sources`, ...); `PATCH /personas/{id}`
(partial edit); `POST /personas/direct` (create from explicit fields) or
`POST /personas` (synthesize from a seed, async). Manage the source registry with
`/portals` and the per-author corroboration pool with
`PUT|POST|DELETE /personas/{id}/sources`. Co-owned outlets share an `ownership_group`
and count once in the corroboration gate.

**Editorial controls** (these shape every run): `/editorial/style` (versioned house
style), `/editorial/style/lexicon` (banned terms force a revise),
`/editorial/style/sourcing` (the `min_sources` floor and the corroboration threshold;
blank turns the gate off, which differs from zero), `/editorial/location`, and
`/prompts` (every pipeline stage is a versioned `.md` template; `POST /prompts/template`
publishes a version that takes effect on the next run). The full endpoint list is in
`newsroom/brain/app.py`.
