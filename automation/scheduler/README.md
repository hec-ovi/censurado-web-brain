# scheduler

A standalone timed-prompt runner: at the times in a config file it spawns a configured agent command with a configured prompt, logs the output, and appends one schema-validated record per attempt. It is wired to nothing in this repo (no supervisor, no run.sh, no systemd unit yet); you run it by hand and it talks only to the agent command you give it. The boundary is [CONTRACT.md](CONTRACT.md).

Any agent CLI that takes a prompt in argv and exits 0 on success works: the sibling `noob-cli` (`noob exec --yolo -p "{prompt}"`, pointed at a local model), `pi`, `claude -p`, whatever. `{prompt}` in the command is replaced with the schedule's prompt; no shell is involved.

```bash
cp automation/scheduler/fixtures/schedules.example.json my-schedules.json   # edit times + prompts
node automation/scheduler/run.mjs my-schedules.json --check                # validate, print next fires
node automation/scheduler/run.mjs my-schedules.json --once daily-sweep     # fire one now
node automation/scheduler/run.mjs my-schedules.json                        # the loop (Ctrl-C stops)
```

`at: "07:00"` fires daily at that local time; `every: "2h"` fires on a fixed cadence from process start. One run at a time: an occurrence that lands while a run is in flight is recorded as `skipped-overlap`, and occurrences missed while the process is down are skipped, not replayed. Records land in `<logDir>/runs.jsonl`, agent output in `<logDir>/<name>-<timestamp>.log`. Needs node >= 22, nothing else.

Tests run with the repo's JS lane (`npm test`).
