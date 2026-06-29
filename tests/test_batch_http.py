"""The HTTP batch trigger: ``POST /runs/batch`` (run mode 4), driven end to end against
the shared fake through the real ASGI app.

The contracts pinned here:

  * the route creates a ``batch`` run, executes OFF the request, and returns 202 + a poll
    ``Location`` exactly like ``POST /runs`` / ``POST /articles/from-link``; the run then
    polls to ``done`` with one published article per swept desk;
  * the discovery seam is fanned ONCE PER AUTHOR, carrying the request ``timeframe`` to the
    backend, so each desk lists topics from its OWN sources;
  * an invalid ``timeframe`` is a 422 BEFORE any run is created.

The per-author search SCOPING itself is pinned in ``test_research_scoping.py`` and the
planner fan in ``test_batch.py``; here the discovery factory is a double, so this file owns
the ROUTE.
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


def _roles(fake) -> ResolvedRoles:
    drafter = _cfg(fake, "drafter-model")
    return ResolvedRoles(
        drafter=drafter, evaluator=drafter, finalize=_cfg(fake, "finalize-model"),
        manager=drafter, evaluator_distinct=False,
    )


def _ready_ledger(_assignment, _spec, _persona, _budget) -> Ledger:
    led = Ledger(clock=lambda: datetime(2026, 6, 23, tzinfo=timezone.utc))
    led.add(claim="grounding", url="https://src.test/a", snippet="a source")
    return led


def _settings(fake, tmp_path, **over) -> Settings:
    base = dict(
        persona_db_path=tmp_path / "brain.db",
        inference_base_url=f"{fake.base_url}/v1",
        publish_base_url=fake.base_url,
        operator_token="op-token",
    )
    base.update(over)
    return Settings(**base)


def _ada() -> Persona:
    return Persona(id="ada", display_name="Ada", beat="tech", who_i_am="I cover chips.", style="dry")


def _discover_double(per_author_hits):
    """A discover-factory double recording each (persona_id, freshness) and yielding the
    author's canned candidates."""

    swept: list[tuple[str, str | None]] = []

    def factory(persona, *, freshness=None):
        swept.append((persona.id, freshness))

        def search(_query):
            return list(per_author_hits.get(persona.id, []))

        return search

    factory.swept = swept
    return factory


def _batch_deps(fake, settings, *, personas, discover) -> RunDeps:
    conn = open_db(":memory:", check_same_thread=False)
    persona_store = PersonaStore(conn)
    for p in personas:
        persona_store.create(p)
    return RunDeps(
        store=RunStore(conn),
        persona_store=persona_store,
        coverage_store=CoverageStore(conn),
        roles=_roles(fake),
        search_news=lambda _q: [],
        make_ledger=_ready_ledger,
        publish_base_url=fake.base_url,
        operator_token="op-token",
        prompts_dir=settings.prompts_dir,
        settings=settings,
        lock=threading.Lock(),
        discover_news=discover,
    )


def _assign(persona_id: str, headline: str) -> str:
    return json.dumps({"action": "assign", "assignments": [
        {"persona_id": persona_id, "headline": headline, "angle": "cover it", "triage": "new"}
    ]})


def _finalize_ok(title: str, body: str) -> str:
    return json.dumps({"title": title, "body": body, "topics": ["chips"]})


def _poll_until_done(client: httpx.Client, run_id: str, *, timeout_s: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        body = client.get(f"/runs/{run_id}").json()
        if body["status"] in ("done", "done_with_errors", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish within {timeout_s}s")


def test_post_runs_batch_returns_202_then_polls_to_published(fake, tmp_path, serve_app):
    settings = _settings(fake, tmp_path, batch_per_author=1)
    discover = _discover_double({
        "ada": [Candidate(headline="ada story", section="tech", url="https://news.test/ada")],
    })
    deps = _batch_deps(fake, settings, personas=[_ada()], discover=discover)
    base_url = serve_app(create_app(settings=settings, store=deps.persona_store, run_deps=deps))
    client = httpx.Client(base_url=base_url, timeout=10)

    # ada's manager assigns immediately, then her one article runs its pipeline + finalizes.
    fake.state.script_chat(_assign("ada", headline="Ada story"))
    for body in ("an outline", "a clean draft", "an enriched body", "a respin 1", "a respin 2"):
        fake.state.script_chat(body)
    fake.state.script_chat(_finalize_ok("Ada Story", "The body."))

    accepted = client.post("/runs/batch", json={"persona_ids": ["ada"], "timeframe": "day"})
    assert accepted.status_code == 202
    handle = accepted.json()
    assert handle["mode"] == "batch"
    assert accepted.headers["Location"] == f"/runs/{handle['run_id']}"

    final = _poll_until_done(client, handle["run_id"])
    assert final["status"] == "done"
    assert len(final["assignments"]) == 1
    assert final["assignments"][0]["status"] == "published"
    assert final["assignments"][0]["persona_id"] == "ada"
    # The sweep ran ada's OWN discovery with the requested timeframe.
    assert discover.swept == [("ada", "day")]
    client.close()


def test_post_runs_batch_rejects_an_invalid_timeframe(fake, tmp_path, serve_app):
    settings = _settings(fake, tmp_path)
    discover = _discover_double({})
    deps = _batch_deps(fake, settings, personas=[_ada()], discover=discover)
    base_url = serve_app(create_app(settings=settings, store=deps.persona_store, run_deps=deps))
    client = httpx.Client(base_url=base_url, timeout=10)

    resp = client.post("/runs/batch", json={"timeframe": "fortnight"})

    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_failed"
    assert deps.store._conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0  # no run created
    assert discover.swept == []  # never swept a desk
    client.close()
