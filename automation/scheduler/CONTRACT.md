# Scheduler layer contract

contractVersion: 1.0.0

## Purpose

Run configured prompts through a configured agent command at configured times, appending one schema-validated record per attempt.

## Inputs

- **Config file** (the only argument to `run.mjs`): schema [schema/schedule-config.schema.json](schema/schedule-config.schema.json). Preconditions: the file parses as JSON and conforms, plus two cross-field rules the schema cannot express and the loader enforces the same fail-closed way: each schedule carries exactly one of `at` (local `HH:MM`, daily) or `every` (`<n>s|m|h`, from process start), and names are unique. Any violation refuses to start (exit 2) and lists every violation.
- **`{prompt}` substitution**: each occurrence of the literal `{prompt}` in `agent.cmd` argv is replaced with the schedule's `prompt`. The command is spawned directly (argv, no shell). Relative `logDir` and `agent.cwd` resolve against the config file's directory.

## Outputs

- **Run records**: one JSON line appended to `<logDir>/runs.jsonl` per attempt, schema [schema/run-record.schema.json](schema/run-record.schema.json). Postconditions: every line validates against the schema before it is written (a violation crashes the scheduler rather than emitting an off-contract line); `status` is `ok` (exit 0), `failed` (nonzero exit or unspawnable, exit 127), `timeout` (killed at `agent.timeoutMs`, default 1h), or `skipped-overlap` (a run was already in flight; `exitCode`/`logFile` absent).
- **Agent output**: stdout+stderr of each run in `<logDir>/<name>-<timestamp>.log`, referenced by the record's `logFile`.

## Events

None. The record stream is the only output; nothing is pushed anywhere.

## Errors (closed set, as exit codes of `run.mjs`)

- `2` CONFIG_INVALID (also bad usage)
- `3` LOCK_HELD: another scheduler (live pid) holds `<logDir>/.scheduler.lock`; a stale lock (dead pid) is replaced silently
- `4` UNKNOWN_SCHEDULE (`--once` with a name not in the config)
- `1` any other failure; for `--once`, also "the run did not end `ok`"
- `0` success

## Dependencies

None. No other layer's contract is read; the agent behind `agent.cmd` is outside the boundary (its contract is argv in, exit code out).

## Invariants

- At most one agent run in flight per logDir, across the loop and `--once` (same lock, plus in-loop overlap skip).
- Occurrences missed while the scheduler is down are skipped, never replayed; `every` intervals restart from process start.
- The scheduler never edits, retries, or interprets agent output; it only records.
- `runs.jsonl` is append-only.

## How to modify this blackbox safely

Change `src/` freely; keep both schemas describing what the code actually reads and writes, keep this file matching them, and keep `tests/` passing (`npm test` at the repo root picks them up). Additive config/record fields: optional in the schema plus a minor `contractVersion` bump. Breaking shape changes: new schema file alongside the old, never edited in place.
