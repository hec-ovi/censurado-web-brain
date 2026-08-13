"""Executor contract tests: the due-time semantics the panel mirrors, and the
loop's firing protocol (running -> outcome, deterministic run id, no double
fire, failures recorded) against a real HTTP fake of the backend seam."""
import http.server
import json
import sys
import threading
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import Backend, BackendError  # noqa: E402
from executor import Executor, summarize  # noqa: E402
from schedule import already_fired, day_matches, due_run_id, is_due  # noqa: E402

WED = datetime(2026, 8, 12, 7, 30)  # a Wednesday


def sched(**over):
    base = {"slug": "edicion", "name": "Edicion", "cadence": "daily",
            "times": ["07:30"], "weekdays": [], "monthdays": [],
            "mode": "preview", "authors": [], "enabled": True, "runs": []}
    base.update(over)
    return base


# ----- the clock semantics (shared with the panel's forecast) -----------------

def test_daily_fires_only_at_its_minute():
    assert is_due(sched(), WED)
    assert not is_due(sched(), WED.replace(minute=31))
    assert not is_due(sched(times=["18:00"]), WED)


def test_weekly_uses_backend_weekday_numbering_sunday_zero():
    # 2026-08-12 is a Wednesday -> backend weekday 3; Sunday 2026-08-16 -> 0.
    assert day_matches(sched(cadence="weekly", weekdays=[3]), WED.date())
    assert not day_matches(sched(cadence="weekly", weekdays=[0]), WED.date())
    sunday = datetime(2026, 8, 16, 7, 30)
    assert day_matches(sched(cadence="weekly", weekdays=[0]), sunday.date())


def test_monthly_matches_day_of_month():
    assert is_due(sched(cadence="monthly", monthdays=[12]), WED)
    assert not is_due(sched(cadence="monthly", monthdays=[13]), WED)


def test_disabled_and_deleted_never_fire():
    assert not is_due(sched(enabled=False), WED)
    assert not is_due(sched(deleted=True), WED)


def test_run_id_is_deterministic_and_marks_the_minute_fired():
    run_id = due_run_id("edicion", WED)
    assert run_id == "edicion-20260812-0730"
    assert not already_fired(sched(), run_id)
    assert already_fired(sched(runs=[{"run_id": run_id, "status": "running"}]), run_id)


def test_summarize_reads_the_batch_result_line():
    published = json.dumps({"status": "batch-published", "batch_id": "b", "artifacts": "x",
                            "articles": [{"status": "published"}, {"status": "failed"}]})
    assert summarize("noise\n" + published) == "1/2 published"
    previewed = json.dumps({"status": "batch-previewed", "batch_id": "b", "artifacts": "x",
                            "articles": [{}, {}, {}]})
    assert summarize(previewed) == "3 previewed"
    assert summarize("not json at all") == "done"


# ----- the loop against a real HTTP backend fake ------------------------------

class FakeBackendHandler(http.server.BaseHTTPRequestHandler):
    store = None  # {"schedules": [...], "runs": [(slug, record), ...]}

    def log_message(self, *args):
        pass

    def _json(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path == "/schedules":
            self._json(200, {"schedules": self.store["schedules"]})
        else:
            self._json(404, {"code": "not_found"})

    def do_POST(self):
        if self.path.startswith("/schedules/") and self.path.endswith("/runs"):
            slug = self.path.split("/")[2]
            record = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if slug not in {s["slug"] for s in self.store["schedules"]}:
                self._json(404, {"code": "not_found"})
                return
            self.store["runs"].append((slug, record))
            # Mirror the backend: the strip carries the record, replaced by run_id.
            for schedule in self.store["schedules"]:
                if schedule["slug"] != slug:
                    continue
                runs = [r for r in schedule["runs"] if r.get("run_id") != record["run_id"]]
                schedule["runs"] = [record] + runs
            self._json(200, {})
        else:
            self._json(404, {"code": "not_found"})


@pytest.fixture
def fake_backend():
    store = {"schedules": [], "runs": []}
    FakeBackendHandler.store = store
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeBackendHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield store, Backend(f"http://127.0.0.1:{server.server_address[1]}", "tok")
    server.shutdown()


def test_tick_fires_due_schedule_and_records_running_then_outcome(fake_backend):
    store, backend = fake_backend
    store["schedules"].append(sched())
    calls = []

    def runner(schedule, run_id):
        calls.append((schedule["slug"], run_id))
        return "ok", "5/8 published"

    executor = Executor(backend, Path("cfg.json"), runner=runner)
    assert executor.tick(WED) == 1
    assert calls == [("edicion", "edicion-20260812-0730")]
    statuses = [(slug, r["run_id"], r["status"]) for slug, r in store["runs"]]
    assert statuses == [
        ("edicion", "edicion-20260812-0730", "running"),
        ("edicion", "edicion-20260812-0730", "ok"),
    ]
    assert store["runs"][1][1]["detail"] == "5/8 published"
    assert store["runs"][1][1]["started_at"] and store["runs"][1][1]["finished_at"]

    # The same minute never fires twice: the strip now carries the run id.
    assert executor.tick(WED) == 0
    assert len(calls) == 1


def test_tick_skips_not_due_and_records_failures(fake_backend):
    store, backend = fake_backend
    store["schedules"].append(sched(slug="tarde", times=["18:00"]))
    store["schedules"].append(sched(slug="falla"))

    executor = Executor(backend, Path("cfg.json"),
                        runner=lambda s, r: ("failed", "exit 4: adapter down"))
    assert executor.tick(WED) == 1
    slugs = {slug for slug, _ in store["runs"]}
    assert slugs == {"falla"}, "the 18:00 schedule must not fire at 07:30"
    assert store["runs"][-1][1]["status"] == "failed"
    assert "adapter down" in store["runs"][-1][1]["detail"]


def test_unreachable_backend_is_survived():
    executor = Executor(Backend("http://127.0.0.1:9", "tok", timeout_s=1), Path("cfg.json"),
                        runner=lambda s, r: ("ok", ""))
    assert executor.tick(WED) == 0


def test_backend_error_carries_the_http_detail(fake_backend):
    _, backend = fake_backend
    with pytest.raises(BackendError, match="404.*not_found"):
        backend.record_run("ghost", {"run_id": "r", "status": "ok"})
