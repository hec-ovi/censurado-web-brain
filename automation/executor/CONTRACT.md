# Schedule executor contract

## Purpose

Fire the newsroom's edition batches on the operator-defined schedules: poll the backend's `/schedules` registry each minute and run `run.py batch` for every schedule due at that minute, one batch at a time.

## Inputs

- **Env**: `NEWSROOM_OPERATOR_TOKEN` (required; reads `/schedules`, writes run records), `EXECUTOR_BACKEND_URL` (default `http://127.0.0.1:8082`), `EXECUTOR_CONFIG` (default the repo's `automation/pipeline/pipeline.config.json`), `EXECUTOR_BATCH_TIMEOUT_S` (default 7200).
- **The schedule registry** (backend seam, see `censurado-web-backend/contracts/CONTRACT.md`): each schedule's `enabled`, `cadence` (`daily|weekly|monthly`), `times` (`HH:MM`, local wall clock), `weekdays` (0=Sunday..6), `monthdays` (1..31), `mode` (`preview|auto`), `authors` filter, and `runs` strip.

## Behavior (the invariants)

- A schedule fires when the current local minute equals one of its times on a cadence-matching day. The clock semantics are the same pure functions the panel forecasts with.
- The run id is deterministic: `<slug>-<YYYYMMDD-HHMM>`. Before the batch starts the executor records `{run_id, status: "running"}` on the schedule's run strip; when it ends it replaces that record with `ok` (detail like `5/8 published`) or `failed` (exit tail). A restarted executor sees the record and never double-fires, so the backend is the only state.
- One batch in flight: the poll loop is the lock. Minutes that pass while the executor is down or a batch is running are skipped, never replayed.
- The executor never deploys; a `preview` schedule's articles wait for `batch-approve`, an `auto` schedule's publish to the local portal only.

## Outputs

- Run records on each schedule's strip (`POST /schedules/{slug}/runs`), which feed the panel's Automation tab.
- The fired batches' own artifacts under the pipeline's `run_dir` (this box adds none).
- A log line per firing on stdout (the compose service's `docker logs`).

## Errors

- Backend unreachable at tick: logged, the tick is skipped (next minute retries).
- Batch non-zero exit or timeout: recorded as `failed` with the stderr tail; the executor keeps running.
- Missing token: exit 2 at start.

## Deployment

The `executor` compose service (repo `docker-compose.yml`): the repo bind-mounted at `/newsroom`, `network_mode: host` so the pipeline config's loopback endpoints (publish :8082, llama.cpp :8080, SearXNG :8888, ComfyUI :8188) work verbatim in-container, `/etc/localtime` mounted read-only so the wall clock matches the host and the panel. The image (built from the repo-root context) carries the pipeline's python deps and the websearch CLI; code is live from the mount.

## Dependencies

- The pipeline box (`automation/pipeline/CONTRACT.md`): `run.py batch --config --mode --run-id [--authors]`, result JSON on stdout.
- The backend's `/schedules` read + run-record seam.
