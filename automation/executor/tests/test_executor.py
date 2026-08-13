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


def test_derive_config_merges_panel_settings_over_the_file():
    from derive import derive_config

    base = {
        "adapters": {"api": {"base_url": "http://127.0.0.1:8080/v1", "model": "qwen-local"}},
        "nodes": [
            {"name": "draft", "adapter": "api", "model": "stale-override"},
            {"name": "evaluate", "adapter": "api"},
        ],
    }
    settings = {
        "lanes": {"local": {"model": "qwen-nuevo"},
                  "openrouter": {"model": "deepseek/deepseek-chat"}},
        "stages": {"evaluate": {"lane": "openrouter", "model": "openai/gpt-5-mini"},
                   "draft": {"lane": "local"},
                   "fantasma": {"lane": "openrouter"}},
    }
    cfg = derive_config(base, settings)
    # Lanes: local overrides the api adapter's model, keeps its base_url; the
    # openrouter entry appears with its defaults plus the chosen model.
    assert cfg["adapters"]["api"] == {"base_url": "http://127.0.0.1:8080/v1", "model": "qwen-nuevo"}
    orl = cfg["adapters"]["openrouter"]
    assert orl["kind"] == "api" and orl["api_key_env"] == "OPENROUTER_API_KEY"
    assert orl["model"] == "deepseek/deepseek-chat"
    # Stages: evaluate rides the remote lane with its own model; draft's lane
    # switch clears the stale per-node override; an unknown stage is ignored.
    nodes = {n["name"]: n for n in cfg["nodes"]}
    assert nodes["evaluate"]["adapter"] == "openrouter"
    assert nodes["evaluate"]["model"] == "openai/gpt-5-mini"
    assert nodes["draft"]["adapter"] == "api" and "model" not in nodes["draft"]
    # The base object is untouched, and empty settings derive it unchanged.
    assert base["nodes"][0]["model"] == "stale-override"
    assert derive_config(base, {}) == base


def test_latest_due_finds_the_most_recent_missed_minute():
    from schedule import latest_due
    from datetime import datetime as dt
    # Daily at 07:30: at Wed 10:00 the last due minute was Wed 07:30.
    assert latest_due(sched(), WED.replace(hour=10, minute=0)) == dt(2026, 8, 12, 7, 30)
    # At Wed 07:00 it was Tue 07:30 (still inside the 24h window).
    assert latest_due(sched(), WED.replace(hour=7, minute=0)) == dt(2026, 8, 11, 7, 30)
    # A weekly schedule whose last firing is outside the window has nothing due.
    assert latest_due(sched(cadence="weekly", weekdays=[0]), WED) is None


def test_catch_up_runs_pending_and_collapses_same_setup(fake_backend):
    store, backend = fake_backend
    # a: missed (no record). b: same setup as a -> collapsed under a's run.
    # c: different setup (has a prompt) -> its own catch-up run.
    # d: already finished -> untouched. e: stale "queued" from a crash -> resumed.
    store["schedules"] += [
        sched(slug="a"),
        sched(slug="b"),
        sched(slug="c", prompt="usando borge cubri la marcha"),
        sched(slug="d", runs=[{"run_id": "d-20260812-0730", "status": "ok"}]),
        sched(slug="e", runs=[{"run_id": "e-20260812-0730", "status": "queued"}]),
    ]
    calls = []
    executor = Executor(backend, Path("cfg.json"), llama_probe=lambda: True,
                        runner=lambda s, r: (calls.append((s["slug"], r)), ("ok", "listo"))[1])
    now = WED.replace(hour=10, minute=0)
    assert executor.catch_up(now) == 2, "one batch covers a+b+e (same setup); c fires alone"
    executor.drain()
    assert [slug for slug, _ in calls] == ["a", "c"]
    covered = {slug: r["detail"] for slug, r in store["runs"] if "cubierta" in r.get("detail", "")}
    assert covered == {"b": "cubierta por a-20260812-0730",
                       "e": "cubierta por a-20260812-0730"}
    outcomes = {slug: r["status"] for slug, r in store["runs"]}
    assert outcomes["a"] == "ok" and outcomes["c"] == "ok"
    assert "d" not in outcomes, "a finished run is never replayed"


def test_summarize_reads_the_batch_result_line():
    published = json.dumps({"status": "batch-published", "batch_id": "b", "artifacts": "x",
                            "articles": [{"status": "published"}, {"status": "failed"}]})
    assert summarize("noise\n" + published) == "1/2 publicadas"
    previewed = json.dumps({"status": "batch-previewed", "batch_id": "b", "artifacts": "x",
                            "articles": [{}, {}, {}]})
    assert summarize(previewed) == "3 en preview"
    assert summarize("not json at all") == "listo"


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
        elif self.path == "/automation-settings":
            self._json(200, {"settings": self.store.get("settings", {})})
        else:
            self._json(404, {"code": "not_found"})

    def do_PUT(self):
        if self.path == "/automation-status":
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.store["status"] = body["settings"]
            self._json(200, body)
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


def test_tick_queues_then_drain_records_running_and_outcome(fake_backend):
    store, backend = fake_backend
    store["schedules"].append(sched())
    calls = []

    def runner(schedule, run_id):
        calls.append((schedule["slug"], run_id))
        return "ok", "5/8 published"

    executor = Executor(backend, Path("cfg.json"), runner=runner, llama_probe=lambda: True)
    assert executor.tick(WED) == 1
    executor.drain()
    assert calls == [("edicion", "edicion-20260812-0730")]
    statuses = [(slug, r["run_id"], r["status"]) for slug, r in store["runs"]]
    assert statuses == [
        ("edicion", "edicion-20260812-0730", "queued"),
        ("edicion", "edicion-20260812-0730", "running"),
        ("edicion", "edicion-20260812-0730", "ok"),
    ]
    assert store["runs"][2][1]["detail"] == "5/8 published"
    assert store["runs"][2][1]["started_at"] and store["runs"][2][1]["finished_at"]

    # The same minute never fires twice: the strip now carries the run id.
    assert executor.tick(WED) == 0
    executor.drain()
    assert len(calls) == 1


def test_close_firings_queue_and_run_in_arrival_order(fake_backend):
    # Two schedules due at the same minute: both are recorded as queued at the
    # tick, then the single worker runs them one after the other.
    store, backend = fake_backend
    store["schedules"].append(sched(slug="alfa"))
    store["schedules"].append(sched(slug="beta"))
    calls = []

    executor = Executor(backend, Path("cfg.json"), llama_probe=lambda: True,
                        runner=lambda s, r: (calls.append(r), ("ok", "done"))[1])
    assert executor.tick(WED) == 2
    queued = [(slug, r["status"]) for slug, r in store["runs"]]
    assert queued == [("alfa", "queued"), ("beta", "queued")], "both wait before any runs"
    # The heartbeat published the queue before anything ran.
    assert store["status"]["queued"] == ["alfa-20260812-0730", "beta-20260812-0730"]

    executor.drain()
    assert calls == ["alfa-20260812-0730", "beta-20260812-0730"], "arrival order, one at a time"
    outcomes = {slug: r["status"] for slug, r in store["runs"]}
    assert outcomes == {"alfa": "ok", "beta": "ok"}, "both firings reached their outcome"


def test_heartbeat_reports_clock_and_lane_health(fake_backend):
    store, backend = fake_backend
    executor = Executor(backend, Path("cfg.json"), runner=lambda s, r: ("ok", ""),
                        llama_probe=lambda: False)
    executor.tick(WED)
    assert store["status"]["llama_ok"] is False
    assert store["status"]["running"] is None
    assert store["status"]["queued"] == []
    assert store["status"]["at"].startswith("2026-08-12T07:30")


def test_tick_skips_not_due_and_records_failures(fake_backend):
    store, backend = fake_backend
    store["schedules"].append(sched(slug="tarde", times=["18:00"]))
    store["schedules"].append(sched(slug="falla"))

    executor = Executor(backend, Path("cfg.json"), llama_probe=lambda: True,
                        runner=lambda s, r: ("failed", "exit 4: adapter down"))
    assert executor.tick(WED) == 1
    executor.drain()
    slugs = {slug for slug, _ in store["runs"]}
    assert slugs == {"falla"}, "the 18:00 schedule must not fire at 07:30"
    assert store["runs"][-1][1]["status"] == "failed"
    assert "adapter down" in store["runs"][-1][1]["detail"]


def test_unreachable_backend_is_survived():
    executor = Executor(Backend("http://127.0.0.1:9", "tok", timeout_s=1), Path("cfg.json"),
                        runner=lambda s, r: ("ok", ""), llama_probe=lambda: True)
    assert executor.tick(WED) == 0


def test_backend_error_carries_the_http_detail(fake_backend):
    _, backend = fake_backend
    with pytest.raises(BackendError, match="404.*not_found"):
        backend.record_run("ghost", {"run_id": "r", "status": "ok"})


def test_backend_settings_roundtrip(fake_backend):
    store, backend = fake_backend
    assert backend.settings() == {}
    store["settings"] = {"stages": {"evaluate": {"lane": "openrouter"}}}
    assert backend.settings()["stages"]["evaluate"]["lane"] == "openrouter"
