# Schedule executor contract

## Purpose

Fire the newsroom's edition batches on the operator-defined schedules: poll the backend's `/schedules` registry each minute and run `run.py batch` for every schedule due at that minute, one batch at a time.

## Inputs

- **Env**: `NEWSROOM_OPERATOR_TOKEN` (required; reads `/schedules`, writes run records), `EXECUTOR_BACKEND_URL` (default `http://127.0.0.1:8082`), `EXECUTOR_CONFIG` (default the repo's `automation/pipeline/pipeline.config.json`), `EXECUTOR_BATCH_TIMEOUT_S` (default 7200).
- **The schedule registry** (backend seam, see `censurado-web-backend/contracts/CONTRACT.md`): each schedule's `enabled`, `cadence` (`daily|weekly|monthly`), `times` (`HH:MM`, local wall clock), `weekdays` (0=Sunday..6), `monthdays` (1..31), `mode` (`preview|auto`), `authors` filter, and `runs` strip.
- **The automation settings** (backend singleton `GET /automation-settings`, edited in the panel's Models section): `{lanes: {local: {base_url?, model?}, openrouter: {base_url?, model?}}, stages: {<node>: {lane?: "local"|"openrouter", model?}}}`. Before each firing the executor merges them over the file config (`derive.py`): the `local` lane overrides the `api` adapter, `openrouter` overrides (or creates, with `OPENROUTER_API_KEY` defaults) the remote adapter, and a stage entry re-points its node's adapter and model — a lane switch without its own model clears the node's file override so the lane default applies. The derived config is written beside the base one (`.executor.config.json`, a runtime artifact) so relative paths keep resolving; empty settings or an unreadable backend fall back to the file untouched.

## Behavior (the invariants)

- A schedule fires when the current local minute equals one of its times on a cadence-matching day. The clock semantics are the same pure functions the panel forecasts with.
- The run id is deterministic: `<slug>-<YYYYMMDD-HHMM>`. The moment a due minute is seen the executor records `{run_id, status: "queued"}` on the schedule's run strip and enqueues the firing; the single worker records `running` when the batch starts and replaces the record with `ok` (detail like `5/8 published`) or `failed` (exit tail) when it ends. A restarted executor sees the record and never double-fires, so the backend is the only state.
- One batch in flight, close firings QUEUE: firings that come due while a batch runs wait in arrival order and fire as soon as the worker frees. Minutes that pass while the executor is DOWN are skipped, never replayed.
- Each tick heartbeats `PUT /automation-status` with `{at, llama_ok, running, queued}` (the executor's clock, the model lane's `/health` probe, the run in flight, the queue), which feeds the panel's status card; the poll loop keeps ticking while the worker runs a batch, so the heartbeat stays fresh mid-firing.
- The executor never deploys; a `preview` schedule's articles wait for `batch-approve`, an `auto` schedule's publish to the local portal only.

## Outputs

- Run records on each schedule's strip (`POST /schedules/{slug}/runs`), which feed the panel's Automation tab.
- The heartbeat singleton (`PUT /automation-status`), best-effort each tick.
- The fired batches' own artifacts under the pipeline's `run_dir` (this box adds none).
- A log line per queue/fire/outcome on stdout (the compose service's `docker logs`).

## Errors

- Backend unreachable at tick: logged, the tick is skipped (next minute retries).
- Batch non-zero exit or timeout: recorded as `failed` with the stderr tail; the executor keeps running.
- Missing token: exit 2 at start.

## Deployment

The `executor` compose service (repo `docker-compose.yml`): the repo bind-mounted at `/newsroom`, `network_mode: host` so the pipeline config's loopback endpoints (publish :8082, llama.cpp :8080, SearXNG :8888, ComfyUI :8188) work verbatim in-container, `/etc/localtime` mounted read-only so the wall clock matches the host and the panel. The image (built from the repo-root context) carries the pipeline's python deps and the websearch CLI; code is live from the mount.

## Dependencies

- The pipeline box (`automation/pipeline/CONTRACT.md`): `run.py batch --config --mode --run-id [--authors]`, result JSON on stdout.
- The backend's `/schedules` read + run-record seam.
