# Article pipeline contract

contractVersion: 1.2.0

## Purpose

Run one article end to end as a durable DBOS workflow: each editorial node is one stateless model call through a configured adapter (`api` or `cli`), a gate node can stop the run, and the approved piece publishes exactly once to the backend.

## Inputs

- **Config file** (`--config`, required): schema [schema/pipeline-config.schema.json](schema/pipeline-config.schema.json). Cross-field rules the loader enforces fail-closed (exit 2, every violation listed): node names unique, exactly one node with role `draft`, every node's adapter present under `adapters`, every prompt file exists, the cli `cmd` carries a `{prompt}` element. Relative paths (`run_dir`, prompts) resolve against the config file's directory.
- **Invocation**: `--topic`, `--author`, `--section` (required), `--run-id` (optional; defaults to a fresh id). The run id is the durability key.
- **Prompt files**: markdown with `{topic}`, `{author}`, `{section}`, `{<node-name>}` (a prior node's parsed output), and any `{<context-key>}` placeholders; unknown braces pass through untouched.
- **Node context** (optional, per node): `context` maps placeholder names to sources fetched by a durable step and inlined before rendering: `{"file": "<path>"}` (file content, e.g. a SKILL.md), `{"persona": true}` (the run author's card from the backend), `{"editorial": "<lang>"}` (the backend editorial lexicon). This is how the api lane reads the house recipe without a CLI.
- **Secrets via env**: `backend.token_env` names the env var holding the operator token; `adapters.api.api_key_env` (optional) names the API key var.

## Outputs

- One result JSON on stdout, schema [schema/run-result.schema.json](schema/run-result.schema.json): always `status` (`published` | `rejected`), `run_id`, `artifacts`; plus `slug`, `article_id`, `permalink` when published; plus `notes` when rejected.
- Per-node artifacts under `<run_dir>/<run_id>/`: `<node>.txt` (raw adapter output) and `<node>.json` (parsed).
- The DBOS system db at `<run_dir>/.dbos.sqlite` (the durable state; deleting it forgets run history).

## Errors (closed set, as exit codes of `run.py`)

- `2` CONFIG_INVALID (also bad usage)
- `3` REJECTED: a gate node returned a verdict other than `publish` (an outcome, not a failure)
- `4` ADAPTER_FAILED: a node failed after 3 attempts
- `5` PUBLISH_FAILED: the backend refused after 3 attempts
- `1` any other failure
- `0` published

## Dependencies

- The backend publish API (`POST /articles`, `GET /articles/{slug}`) with an operator token; `Idempotency-Key` semantics are the backend's.
- The `api` adapter's endpoint: any OpenAI-compatible `/chat/completions`.
- The `cli` adapter's binary: argv in (with `{prompt}` substituted, or the whole prompt on stdin when `stdin` is set), article text on stdout, exit code out.

## Invariants

- One publish per run id: the workflow id is both the DBOS durability key and the `Idempotency-Key`, so step retries, crashes, and re-invocations with the same run id never create a second article.
- Re-running a finished run id replays the stored result without touching any adapter or the backend.
- Steps are stateless: everything a node needs is in its rendered prompt; everything it produces is its artifact.
- The pipeline never deploys. Going live (the CDN push) stays a human action outside this box.

## How to modify this blackbox safely

Change `src/` freely; keep both schemas describing what the code actually reads and writes, keep this file matching them, and keep `tests/` passing (`make test` at the repo root picks them up). Additive config/result fields: optional in the schema plus a minor `contractVersion` bump. Breaking shape changes: new schema file alongside the old, never edited in place.
