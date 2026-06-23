"""The run HTTP surface (Step 8): ``POST /runs`` -> 202 + poll, mirroring the Step 3
persona route. This is the real entry point an operator (or the automation layer)
hits, exercised over real HTTP against the shared fake.

The run executes OFF the request in a worker thread (a plain background task), so the
route returns 202 immediately with a run id to poll, and the blocking pipeline (and
the finalize seam's ``run_sync``) never touches the event loop. The poll endpoint
reads the run record and its assignments through the same shared-connection lock the
background run writes under.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

import httpx

from newsroom.brain import create_app
from newsroom.config import Settings
from newsroom.db import open_db
from newsroom.inference.provider import DIALECTS, ProviderConfig
from newsroom.manager.coverage import CoverageStore
from newsroom.manager.preflight import ResolvedRoles
from newsroom.manager.types import Candidate
from newsroom.personas import Persona, PersonaStore
from newsroom.research.ledger import Ledger
from newsroom.runner import RunDeps
from newsroom.runs import RunStore


def _cfg(fake, model: str) -> ProviderConfig:
    return ProviderConfig(
        role="x", provider="local", base_url=f"{fake.base_url}/v1", model=model, **DIALECTS["local"]
    )


def _ready_ledger(_assignment, _spec, _budget) -> Ledger:
    led = Ledger(clock=lambda: datetime(2026, 6, 23, tzinfo=timezone.utc))
    led.add(claim="grounding", url="https://src.test/a", snippet="a source")
    return led


def _deps(fake, tmp_path) -> RunDeps:
    conn = open_db(":memory:", check_same_thread=False)
    persona_store = PersonaStore(conn)
    persona_store.create(
        Persona(id="ada", display_name="Ada", beat="tech", who_i_am="I cover chips.", style="dry")
    )
    drafter = _cfg(fake, "drafter-model")
    settings = Settings(
        persona_db_path=tmp_path / "brain.db",
        inference_base_url=f"{fake.base_url}/v1",
        publish_base_url=fake.base_url,
        operator_token="op-token",
    )
    return RunDeps(
        store=RunStore(conn),
        persona_store=persona_store,
        coverage_store=CoverageStore(conn),
        roles=ResolvedRoles(
            drafter=drafter, evaluator=drafter, finalize=_cfg(fake, "finalize-model"),
            manager=drafter, evaluator_distinct=False,
        ),
        search_news=lambda _q: [Candidate(headline="seed", url="", snippet="")],
        make_ledger=_ready_ledger,
        publish_base_url=fake.base_url,
        operator_token="op-token",
        prompts_dir=settings.prompts_dir,
        settings=settings,
        lock=threading.Lock(),
    )


def _assign(persona_id: str, headline: str) -> str:
    return json.dumps(
        {"action": "assign", "assignments": [
            {"persona_id": persona_id, "headline": headline, "angle": "cover it", "triage": "new"}
        ]}
    )


def _poll_until_done(client: httpx.Client, run_id: str, *, timeout_s: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        body = client.get(f"/runs/{run_id}").json()
        if body["status"] in ("done", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish within {timeout_s}s")


def test_post_runs_returns_202_then_polls_to_a_published_run(fake, tmp_path, serve_app):
    deps = _deps(fake, tmp_path)
    base_url = serve_app(create_app(settings=deps.settings, store=deps.persona_store, run_deps=deps))
    client = httpx.Client(base_url=base_url, timeout=10)

    # Script the full run: manager assign, then ada's pipeline, then finalize.
    fake.state.script_chat(_assign("ada", "Chips ship early"))
    for body in ("outline", "draft", "enriched"):
        fake.state.script_chat(body)
    fake.state.script_chat(json.dumps({"title": "Chips Ship Early", "body": "Shipped.", "topics": []}))

    accepted = client.post("/runs", json={"mode": "managed"})
    assert accepted.status_code == 202
    handle = accepted.json()
    run_id = handle["run_id"]
    assert handle["mode"] == "managed" and handle["status"] == "running"
    assert accepted.headers["Location"] == f"/runs/{run_id}"

    final = _poll_until_done(client, run_id)

    assert final["status"] == "done"
    assert final["mode"] == "managed"
    assert len(final["assignments"]) == 1
    row = final["assignments"][0]
    assert row["persona_id"] == "ada" and row["section"] == "tech"
    assert row["status"] == "published" and row["published_id"]
    assert len(fake.state.publish_requests) == 1
    client.close()


def test_post_runs_failing_run_is_polled_to_failed(fake, tmp_path, serve_app):
    # A run that raises OFF the request (here: coverage read blows up) must be recorded
    # as status='failed' and surfaced via GET /runs/{id}, not lost as a 500 or left a
    # zombie 'running'. This exercises the background swallow + the poll 'failed' branch.
    class _BoomCoverage:
        def recent(self, *, limit):
            raise RuntimeError("coverage store is down")

    deps = _deps(fake, tmp_path)
    deps.coverage_store = _BoomCoverage()
    base_url = serve_app(create_app(settings=deps.settings, store=deps.persona_store, run_deps=deps))
    client = httpx.Client(base_url=base_url, timeout=10)

    accepted = client.post("/runs", json={"mode": "managed"})
    assert accepted.status_code == 202
    run_id = accepted.json()["run_id"]

    final = _poll_until_done(client, run_id)
    assert final["status"] == "failed"  # the off-request failure was recorded, not lost
    client.close()


def test_post_runs_rejects_an_unknown_mode(fake, tmp_path, serve_app):
    deps = _deps(fake, tmp_path)
    base_url = serve_app(create_app(settings=deps.settings, store=deps.persona_store, run_deps=deps))
    client = httpx.Client(base_url=base_url, timeout=10)

    resp = client.post("/runs", json={"mode": "turbo"})

    assert resp.status_code == 422
    assert resp.json()["code"] == "invalid_mode"
    # An invalid trigger never creates a run record or calls the model.
    assert fake.state.chat_requests == []
    assert deps.store._conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    client.close()


def test_get_unknown_run_is_404(fake, tmp_path, serve_app):
    deps = _deps(fake, tmp_path)
    base_url = serve_app(create_app(settings=deps.settings, store=deps.persona_store, run_deps=deps))
    client = httpx.Client(base_url=base_url, timeout=10)

    resp = client.get("/runs/does-not-exist")

    assert resp.status_code == 404
    assert resp.json()["code"] == "run_not_found"
    client.close()
