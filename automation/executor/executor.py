#!/usr/bin/env python3
"""The schedule executor: polls the backend's schedule registry each minute and
fires due edition batches through the pipeline, one at a time.

It runs as a compose service beside the stack, so it lives and dies with docker.
All firing state lives in the BACKEND: a firing is recorded on the schedule's
run strip as "running" before the batch starts and replaced with the outcome
when it ends, and the run id derives deterministically from slug + minute, so a
restarted executor sees the record and never double-fires. One batch runs at a
time (the loop is the lock); minutes that pass while the executor is down or
busy are skipped, never replayed.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backend import Backend, BackendError  # noqa: E402
from schedule import already_fired, due_run_id, is_due  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO / "automation" / "pipeline" / "pipeline.config.json"
RUN_PY = REPO / "automation" / "pipeline" / "run.py"


def summarize(stdout: str) -> str:
    """One human line from the batch's stdout result JSON, for the run strip."""
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            result = json.loads(line)
        except json.JSONDecodeError:
            continue
        articles = result.get("articles") or []
        if result.get("status") == "batch-published":
            published = sum(1 for a in articles if a.get("status") == "published")
            return f"{published}/{len(articles)} published"
        if result.get("status") == "batch-previewed":
            return f"{len(articles)} previewed"
        return result.get("status", "done")
    return "done"


class Executor:
    """One tick = list the schedules, fire what is due at this minute. `runner`
    is injectable so tests exercise the loop without a real batch."""

    def __init__(self, backend: Backend, config_path: Path,
                 batch_timeout_s: int = 7200, runner=None):
        self.backend = backend
        self.config_path = config_path
        self.batch_timeout_s = batch_timeout_s
        self.runner = runner or self._run_batch

    def tick(self, now: datetime | None = None) -> int:
        now = now or datetime.now()
        try:
            schedules = self.backend.schedules()
        except BackendError as e:
            print(f"[executor] backend unreachable: {e}", file=sys.stderr)
            return 0
        fired = 0
        for schedule in schedules:
            if not is_due(schedule, now):
                continue
            run_id = due_run_id(schedule["slug"], now)
            if already_fired(schedule, run_id):
                continue
            self.fire(schedule, run_id, now)
            fired += 1
        return fired

    def fire(self, schedule: dict, run_id: str, now: datetime) -> None:
        slug = schedule["slug"]
        started = now.astimezone().isoformat(timespec="seconds")
        print(f"[executor] firing {run_id} (mode {schedule.get('mode', 'preview')})")
        try:
            self.backend.record_run(slug, {"run_id": run_id, "status": "running", "started_at": started})
        except BackendError as e:
            print(f"[executor] could not record start of {run_id}: {e}", file=sys.stderr)
            return
        status, detail = self.runner(schedule, run_id)
        finished = datetime.now().astimezone().isoformat(timespec="seconds")
        print(f"[executor] {run_id}: {status} ({detail})")
        try:
            self.backend.record_run(slug, {
                "run_id": run_id, "status": status, "detail": detail,
                "started_at": started, "finished_at": finished,
            })
        except BackendError as e:
            print(f"[executor] could not record outcome of {run_id}: {e}", file=sys.stderr)

    def _run_batch(self, schedule: dict, run_id: str) -> tuple[str, str]:
        cmd = [sys.executable, str(RUN_PY), "batch",
               "--config", str(self.config_path),
               "--mode", schedule.get("mode", "preview"),
               "--run-id", run_id]
        authors = schedule.get("authors") or []
        if authors:
            cmd += ["--authors", ",".join(authors)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.batch_timeout_s)
        except subprocess.TimeoutExpired:
            return "failed", f"timed out after {self.batch_timeout_s}s"
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip()[-300:]
            return "failed", f"exit {proc.returncode}: {tail}"
        return "ok", summarize(proc.stdout)


def main() -> int:
    base_url = os.environ.get("EXECUTOR_BACKEND_URL", "http://127.0.0.1:8082")
    token = os.environ.get("NEWSROOM_OPERATOR_TOKEN", "")
    if not token:
        print("NEWSROOM_OPERATOR_TOKEN is not set", file=sys.stderr)
        return 2
    config = Path(os.environ.get("EXECUTOR_CONFIG", str(DEFAULT_CONFIG)))
    timeout_s = int(os.environ.get("EXECUTOR_BATCH_TIMEOUT_S", "7200"))
    executor = Executor(Backend(base_url, token), config, batch_timeout_s=timeout_s)
    print(f"[executor] up: backend {base_url}, config {config}")
    while True:
        executor.tick()
        # Sleep to just past the next minute boundary so each wall-clock minute
        # gets exactly one tick. A long batch simply holds the loop; the minutes
        # it covered are skipped by design.
        time.sleep(60 - datetime.now().second % 60 + 1)


if __name__ == "__main__":
    sys.exit(main())
