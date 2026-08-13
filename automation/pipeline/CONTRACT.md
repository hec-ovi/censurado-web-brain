# Article pipeline contract

contractVersion: 1.4.0

## Purpose

Run one article end to end as a durable DBOS workflow: each editorial node is one stateless model call through a configured adapter (`api` or `cli`), research context (fresh titulars from the source registry's feeds, web search + fenced page reads) is fetched by code and inlined into the prompts, a gate node can stop the run, and the approved piece publishes exactly once to the backend.

## Inputs

- **Config file** (`--config`, required): schema [schema/pipeline-config.schema.json](schema/pipeline-config.schema.json). `adapters` is a map of NAMED entries: `api` and `cli` imply their kind, and any other name (e.g. `openrouter`) declares `"kind": "api"|"cli"`, so several OpenAI-compatible endpoints can coexist and a node picks one by name. A node may also set `"model"` to override its api-kind adapter's default model, which is how gates run on a big remote model while drafts stay local. Cross-field rules the loader enforces fail-closed (exit 2, every violation listed): node names unique, exactly one node with role `draft`, every node's adapter present under `adapters`, adapter kinds valid (api needs `base_url`+`model`, cli a `cmd` argv carrying `{prompt}` or `stdin`), a node `model` only on an api-kind adapter, every prompt file exists (respin prompts included), `websearch.cmd` is a non-empty argv list, a websearch context's `queries_from`/`urls_from` and a respin's `target` name an earlier node, respin only on gate nodes. Relative paths (`run_dir`, prompts) resolve against the config file's directory.
- **Invocation**: `--topic`, `--author`, `--section` (required), `--run-id` (optional; defaults to a fresh id), `--mode preview|auto` (default `preview`). The run id is the durability key. `preview` walks every node and gate but holds the piece for approval; `auto` publishes as soon as the gate passes.
- **Approve**: `run.py approve --config <c> --run-id <r>` publishes a previewed run's held piece (from its `piece.json` artifact), idempotency-keyed by the same run id.
- **Events console**: `run.py events --config <c> [-n N] [--follow]` prints the run/approve/failure event stream.
- **Batch**: `run.py batch --config <c> [--run-id <b>] [--mode preview|auto] [--authors a,b] [--directive "<texto>"]` runs the daily edition (config block `batch`): every author with a beat and attached sources pitches candidates (title + description + source link, count decided by the day) from their own feeds, the jefe de redaccion selects the edition (portada ranks, per-note image decision with a visual brief) — an operator `--directive` (a custom schedule's prompt) reaches both the candidates and the jefe prompts as `{directiva}` and steers them; empty means a free edition — and the selected articles run the ordinary durable workflow in parallel (`batch.concurrency`, preview mode) with run ids `<batch>-n<i>`. Publishing is sequential in portada order (rank 1 last, so it tops the portada): automatic in `auto`, or later via `run.py batch-approve --config <c> --run-id <b>`. The selection persists in `<run_dir>/<batch>/plan.json` (re-running the same batch id resumes it; completed articles replay idempotently) and the outcome in `result.json`, schema [schema/batch-result.schema.json](schema/batch-result.schema.json). Each selected candidate's source link lands in the author's used-URL registry (`metadata.used_urls`, newest first, `batch.used_urls_cap` FIFO): the feeds context omits already-used titulars, so the same news is never pitched twice.
- **Hero image** (`--image-brief`, set by the jefe in a batch): rendered best-effort through the toolkit's `image` verb after the gate passes; when ComfyUI is off the piece publishes text-only. A rendered hero lands as `metadata.image` plus an image card.
- **Prompt files**: markdown with `{topic}`, `{author}`, `{section}`, `{<node-name>}` (a prior node's parsed output), and any `{<context-key>}` placeholders; unknown braces pass through untouched.
- **Node context** (optional, per node): `context` maps placeholder names to sources fetched by a durable step and inlined before rendering: `{"file": "<path>"}` (file content, e.g. a SKILL.md), `{"persona": true}` (the run author's full card from the backend: name, bio, style, who_i_am, profile topics, and the few-shot voice examples), `{"editorial": "<lang>"}` (the backend editorial lexicon), `{"topics": true}` (the portal's existing topic slugs, so tags reuse exact slugs), `{"articles": {"limit": N}}` (recent published notes, the pool `{{relacionado:<slug>}}` draws from), `{"feeds": {...}}` (fresh titulars from the run author's registered sources: each source's registry `feed_urls` is fetched and windowed by `hours`, and `site_search` sources are listed for the search lane), `{"websearch": {"queries_from": "<node>", "urls_from": "<node>", ...}}` (run the searches an earlier json node proposed as `{"queries": [...]}` through the websearch CLI, read the source pages that node picked as `read_urls` DIRECTLY with no search engine in between, dedup by URL, and inline everything as fenced untrusted content). This is how the api lane reads the house recipe and the live web without a CLI agent.
- **Websearch config** (top-level `websearch`, optional): `cmd` argv of the websearch CLI (default `["websearch"]`) and `timeout_s`. Only needed when a node declares a websearch context.
- **Respin** (optional, gate nodes only): `"respin": {"prompt": "<file>", "target": "<earlier node>", "passes": N}`. On a non-publish verdict the gate's `notes` feed the rewrite prompt (placeholder `{notes}` plus the running context), the target node's output is replaced by the rewrite, and the gate re-runs; after `passes` failed rewrites the run is rejected. Respin artifacts land as `<target>-respin-<n>` and `<gate>-respin-<n>`.
- **Secrets via env**: `backend.token_env` names the env var holding the operator token; `adapters.api.api_key_env` (optional) names the API key var.

## Outputs

- One result JSON on stdout, schema [schema/run-result.schema.json](schema/run-result.schema.json): always `status` (`published` | `rejected` | `previewed`), `run_id`, `artifacts`; plus `slug`, `article_id`, `permalink` when published; plus `notes` when rejected; plus `piece` (what publish would have posted) when previewed.
- Per-node artifacts under `<run_dir>/<run_id>/`: `<node>.txt` (raw adapter output) and `<node>.json` (parsed). A previewed run also writes `piece.json` (the held piece plus its inputs), which is what `approve` publishes.
- One event line per invocation appended to `<run_dir>/events.jsonl`, schema [schema/event.schema.json](schema/event.schema.json): `run-start`, then `run` or `approve` with its status (`published` | `rejected` | `previewed` | `failed`, failures carrying `exit_code` and the first 300 chars of the error). The `events` command renders this file; the stream is append-only.
- The DBOS system db at `<run_dir>/.dbos.sqlite` (the durable state; deleting it forgets run history).

## Errors (closed set, as exit codes of `run.py`)

- `2` CONFIG_INVALID (also bad usage)
- `3` REJECTED: the gate blocked the run (an outcome, not a failure). Canonical gate output is `{"observaciones": [{"nivel": "bloqueante"|"pulido", "detalle": "..."}]}`: the code blocks when any `bloqueante` exists and passes `pulido` items along to later nodes; a plain `{"verdict", "notes"}` object is also honored.
- `4` ADAPTER_FAILED: a node failed after 3 attempts
- `5` PUBLISH_FAILED: the backend refused after 3 attempts
- `1` any other failure
- `0` published, or previewed (mode `preview`)

`approve` uses the same set: `2` when the run has no previewed piece, `5` when the backend refuses, `0` published. `events` is `0` (or `2` on a bad config).

## Dependencies

- The backend publish API (`POST /articles`, `GET /articles/{slug}`) with an operator token; `Idempotency-Key` semantics are the backend's. Context sources additionally read `GET /authors/{handle}/sources`, `GET /sources`, `GET /topics`, and `GET /articles`. The published piece carries `metadata.description` (the bajada) and, when the body embeds `{{tweet:<id>}}` markers, `metadata.tweets` with the fetched card snapshots.
- The censurado.py toolkit (top-level `toolkit`, optional; default the repo's own `cli/censurado.py`): the publisher runs its `tweet <id>` verb to fetch each embedded card, mirroring the CLI preview's auto-fetch; an unfetchable card is skipped and the piece publishes without it.
- The `api` adapter's endpoint: any OpenAI-compatible `/chat/completions`.
- The `cli` adapter's binary: argv in (with `{prompt}` substituted, or the whole prompt on stdin when `stdin` is set), article text on stdout, exit code out.
- The websearch CLI (websearch-skill) for the websearch context: `web-search`/`web-fetch` with `--json`, one `Envelope {ok, data, error}` per call, fenced page content.

## Invariants

- One publish per run id: the workflow id is both the DBOS durability key and the `Idempotency-Key`, so step retries, crashes, re-invocations, and repeated `approve` calls with the same run id never create a second article.
- Re-running a finished run id replays the stored result without touching any adapter or the backend.
- Steps are stateless: everything a node needs is in its rendered prompt; everything it produces is its artifact.
- The pipeline never deploys. Going live (the CDN push) stays a human action outside this box.

## How to modify this blackbox safely

Change `src/` freely; keep both schemas describing what the code actually reads and writes, keep this file matching them, and keep `tests/` passing (`make test` at the repo root picks them up). Additive config/result fields: optional in the schema plus a minor `contractVersion` bump. Breaking shape changes: new schema file alongside the old, never edited in place.
