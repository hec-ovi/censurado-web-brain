#!/usr/bin/env python3
"""The schedule executor: polls the backend's schedule registry each minute,
queues due edition batches, and fires them through the pipeline one at a time.

It runs as a compose service beside the stack, so it lives and dies with docker.
All firing state lives in the BACKEND: a due minute is recorded on the
schedule's run strip as "queued" the moment it is seen, becomes "running" when
the batch starts, and is replaced with the outcome when it ends; the run id
derives deterministically from slug + minute, so a restarted executor sees the
record and never double-fires. Close firings QUEUE behind the one in flight and
run in arrival order; minutes that pass while the executor is DOWN are skipped,
never replayed. Each tick also heartbeats /automation-status (clock, model-lane
health, run in flight, queue) for the panel's Automation tab.
"""
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from collections import deque
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from backend import Backend, BackendError  # noqa: E402
from derive import OPENROUTER_DEFAULTS, derive_config  # noqa: E402
from schedule import already_fired, due_run_id, is_due, latest_due  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = REPO / "automation" / "pipeline" / "pipeline.config.json"
RUN_PY = REPO / "automation" / "pipeline" / "run.py"


def summarize(stdout: str) -> str:
    """One human line (in the newsroom's Spanish) from the batch's stdout result
    JSON, for the run strip the panel shows."""
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
            return f"{published}/{len(articles)} publicadas"
        if result.get("status") == "batch-previewed":
            return f"{len(articles)} en preview"
        if result.get("status") == "topics-cleansed":
            return (f"{result.get('tags_before', '?')}→{result.get('tags_after', '?')} temas, "
                    f"{result.get('applied', 0)} notas")
        return result.get("status", "listo")
    return "listo"


class Executor:
    """One tick = list the schedules, queue what is due, heartbeat. A single
    worker drains the queue in arrival order, so one batch runs at a time.
    `runner` and `llama_probe` are injectable so tests exercise the loop without
    a real batch or a live model server."""

    def __init__(self, backend: Backend, config_path: Path,
                 batch_timeout_s: int = 7200, runner=None, llama_probe=None,
                 remote_probe=None):
        self.backend = backend
        self.config_path = config_path
        self.batch_timeout_s = batch_timeout_s
        self.runner = runner or self._run_batch
        self.llama_probe = llama_probe or self._probe_llama
        self.remote_probe = remote_probe or self._probe_remote
        self.queue: deque = deque()
        self.current: str | None = None
        self.lock = threading.Lock()
        self.wake = threading.Event()

    # ---- the poll tick -------------------------------------------------------

    def tick(self, now: datetime | None = None) -> int:
        now = now or datetime.now()
        queued = 0
        try:
            schedules = self.backend.schedules()
        except BackendError as e:
            print(f"[executor] backend unreachable: {e}", file=sys.stderr)
            return 0
        for schedule in schedules:
            if not is_due(schedule, now):
                continue
            run_id = due_run_id(schedule["slug"], now)
            if already_fired(schedule, run_id) or self._pending(run_id):
                continue
            try:
                self.backend.record_run(schedule["slug"], {"run_id": run_id, "status": "queued"})
            except BackendError as e:
                print(f"[executor] could not queue {run_id}: {e}", file=sys.stderr)
                continue
            print(f"[executor] queued {run_id} (mode {schedule.get('mode', 'preview')})")
            with self.lock:
                self.queue.append((schedule, run_id, now))
            queued += 1
        self.heartbeat(now)
        if queued:
            self.wake.set()
        return queued

    def _pending(self, run_id: str) -> bool:
        with self.lock:
            return run_id == self.current or any(r == run_id for _, r, _ in self.queue)

    # ---- startup catch-up ----------------------------------------------------

    def catch_up(self, now: datetime | None = None) -> int:
        """Run what is PENDING at startup: each enabled schedule's most recent
        due minute (last 24h) that never reached an outcome — either it was
        missed while the executor was down, or a queued/running record was left
        by a crash (the batch resumes idempotently under the same run id).
        Pending firings with the SAME setup (mode + prompt + authors) collapse
        into one batch: the first fires, the others' strips record who covered
        them. Older misses stay skipped."""
        now = now or datetime.now()
        try:
            schedules = self.backend.schedules()
        except BackendError as e:
            print(f"[executor] backend unreachable at catch-up: {e}", file=sys.stderr)
            return -1
        pending = []
        for schedule in schedules:
            if not schedule.get("enabled") or schedule.get("deleted"):
                continue
            due = latest_due(schedule, now)
            if due is None:
                continue
            run_id = due_run_id(schedule["slug"], due)
            record = next((r for r in schedule.get("runs") or []
                           if r.get("run_id") == run_id), None)
            if record and record.get("status") in ("ok", "failed"):
                continue
            if self._pending(run_id):
                continue
            pending.append((schedule, run_id))
        groups: dict = {}
        for schedule, run_id in pending:
            key = (schedule.get("task", "batch"), schedule.get("mode", "preview"),
                   schedule.get("prompt", ""),
                   tuple(sorted(schedule.get("authors") or [])))
            groups.setdefault(key, []).append((schedule, run_id))
        fired = 0
        for items in groups.values():
            lead, lead_run_id = items[0]
            for schedule, run_id in items[1:]:
                try:
                    self.backend.record_run(schedule["slug"], {
                        "run_id": run_id, "status": "ok",
                        "detail": f"cubierta por {lead_run_id}"})
                except BackendError as e:
                    print(f"[executor] could not mark {run_id} covered: {e}", file=sys.stderr)
            try:
                self.backend.record_run(lead["slug"], {"run_id": lead_run_id, "status": "queued"})
            except BackendError as e:
                print(f"[executor] could not queue catch-up {lead_run_id}: {e}", file=sys.stderr)
                continue
            print(f"[executor] catch-up queued {lead_run_id} "
                  f"(+{len(items) - 1} covered)")
            with self.lock:
                self.queue.append((lead, lead_run_id, now))
            fired += 1
        self.heartbeat(now)
        if fired:
            self.wake.set()
        return fired

    # ---- the heartbeat -------------------------------------------------------

    def heartbeat(self, now: datetime | None = None) -> None:
        """Best-effort status for the panel: the executor's clock, the model
        lane's health, the run in flight, the queue, and the EFFECTIVE model
        configuration (file + panel settings merged) so the panel edits what
        actually runs instead of an invisible overlay."""
        now = now or datetime.now()
        with self.lock:
            queued = [r for _, r, _ in self.queue]
            current = self.current
        effective = self._effective_state()
        # The probes target the lanes that actually run (panel values included).
        lanes = effective.get("lanes") or {}
        self._local_base = (lanes.get("local") or {}).get("base_url", "")
        self._remote_lane = lanes.get("openrouter") or {}
        status = {
            "at": now.astimezone().isoformat(timespec="seconds"),
            "llama_ok": bool(self.llama_probe()),
            "remote_state": self.remote_probe(),
            "running": current,
            "queued": queued,
            "effective": effective,
        }
        try:
            self.backend.put_status(status)
        except BackendError as e:
            print(f"[executor] heartbeat failed: {e}", file=sys.stderr)

    def _effective_state(self) -> dict:
        """What actually runs, in the panel's vocabulary: each lane's endpoint
        and model, whether a remote key is available (never the key itself), and
        the lane + model each pipeline stage resolves to after the merge."""
        try:
            base = json.loads(self.config_path.read_text())
        except (OSError, ValueError):
            return {}
        try:
            settings = self.backend.settings()
        except BackendError:
            settings = {}
        cfg = derive_config(base, settings)
        adapters = cfg.get("adapters", {})
        local = adapters.get("api", {})
        remote = {**OPENROUTER_DEFAULTS, **adapters.get("openrouter", {})}
        key_set = bool(remote.get("api_key")
                       or os.environ.get(remote.get("api_key_env") or "", ""))
        stages = {}
        for node in cfg.get("nodes", []):
            lane = "openrouter" if node.get("adapter") == "openrouter" else "local"
            lane_model = remote.get("model") if lane == "openrouter" else local.get("model")
            stages[node["name"]] = {"lane": lane,
                                    "model": node.get("model") or lane_model or ""}
        return {
            "lanes": {
                "local": {"base_url": local.get("base_url", ""),
                          "model": local.get("model", "")},
                "openrouter": {"base_url": remote.get("base_url", ""),
                               "model": remote.get("model", ""),
                               "key_set": key_set},
            },
            "stages": stages,
        }

    def _probe_llama(self) -> bool:
        """llama.cpp liveness: GET /health on the EFFECTIVE local endpoint (the
        heartbeat stashes it; the file config is the fallback), 3s cap."""
        base = getattr(self, "_local_base", "")
        if not base:
            try:
                base = json.loads(self.config_path.read_text())["adapters"]["api"]["base_url"]
            except (OSError, KeyError, ValueError):
                return False
        url = base.rstrip("/").removesuffix("/v1") + "/health"
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                return resp.status == 200
        except OSError:
            return False

    def _probe_remote(self) -> str:
        """The remote lane's verdict: "ok" (reachable, key accepted), "auth"
        (reachable, key rejected), "down" (unreachable), "nokey" (nothing to
        authenticate with). OpenRouter verifies the key on GET /key; every other
        OpenAI-compatible provider (OpenAI, xAI, Groq, DeepSeek...) authenticates
        GET /models the same Bearer way."""
        lane = getattr(self, "_remote_lane", {}) or {}
        base = (lane.get("base_url") or "").rstrip("/")
        if not base:
            return "down"
        key = ""
        try:
            settings = self.backend.settings()
            key = ((settings.get("lanes") or {}).get("openrouter") or {}).get("api_key", "")
        except BackendError:
            pass
        if not key:
            key = os.environ.get("OPENROUTER_API_KEY", "")
        if not key:
            return "nokey"
        path = "/key" if "openrouter.ai" in base else "/models"
        req = urllib.request.Request(base + path, headers={"Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return "ok" if resp.status == 200 else "down"
        except urllib.error.HTTPError as e:
            return "auth" if e.code in (401, 403) else "down"
        except OSError:
            return "down"

    # ---- the worker ----------------------------------------------------------

    def drain(self) -> None:
        """Process everything queued, one batch at a time, in arrival order."""
        while True:
            with self.lock:
                if not self.queue:
                    return
                schedule, run_id, queued_at = self.queue.popleft()
                self.current = run_id
            try:
                self.fire(schedule, run_id, queued_at)
            finally:
                with self.lock:
                    self.current = None

    def worker(self) -> None:
        while True:
            self.wake.wait(timeout=5)
            self.wake.clear()
            self.drain()

    # ---- one firing ----------------------------------------------------------

    def fire(self, schedule: dict, run_id: str, now: datetime) -> None:
        slug = schedule["slug"]
        started = datetime.now().astimezone().isoformat(timespec="seconds")
        print(f"[executor] firing {run_id} (mode {schedule.get('mode', 'preview')})")
        try:
            self.backend.record_run(slug, {"run_id": run_id, "status": "running", "started_at": started})
        except BackendError as e:
            print(f"[executor] could not record start of {run_id}: {e}", file=sys.stderr)
            return
        self.heartbeat()
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

    def _effective_config(self) -> Path:
        """The config this firing runs with: the file, with the panel's
        automation settings (backend singleton) merged over it when any are set.
        Written beside the base config so its relative paths keep resolving; an
        unreachable backend or empty settings fall back to the file untouched."""
        try:
            settings = self.backend.settings()
        except BackendError as e:
            print(f"[executor] settings unreadable, using the file config: {e}", file=sys.stderr)
            return self.config_path
        if not settings:
            return self.config_path
        derived = derive_config(json.loads(self.config_path.read_text()), settings)
        out = self.config_path.with_name(".executor.config.json")
        out.write_text(json.dumps(derived, ensure_ascii=False, indent=1))
        return out

    def _run_batch(self, schedule: dict, run_id: str) -> tuple[str, str]:
        if schedule.get("task") == "topics":
            cmd = [sys.executable, str(RUN_PY), "topics",
                   "--config", str(self._effective_config()), "--run-id", run_id]
        else:
            cmd = [sys.executable, str(RUN_PY), "batch",
                   "--config", str(self._effective_config()),
                   "--mode", schedule.get("mode", "preview"),
                   "--run-id", run_id]
            authors = schedule.get("authors") or []
            if authors:
                cmd += ["--authors", ",".join(authors)]
            if schedule.get("prompt"):
                cmd += ["--directive", schedule["prompt"]]
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
    threading.Thread(target=executor.worker, daemon=True).start()
    print(f"[executor] up: backend {base_url}, config {config}")
    # The backend may still be booting beside us; the catch-up scan retries
    # briefly so a compose-up never silently skips its pending runs.
    for _ in range(6):
        if executor.catch_up() >= 0:
            break
        time.sleep(5)
    while True:
        executor.tick()
        # Sleep to just past the next minute boundary so each wall-clock minute
        # gets exactly one tick; the worker thread drains the queue meanwhile.
        time.sleep(60 - datetime.now().second % 60 + 1)


if __name__ == "__main__":
    sys.exit(main())
