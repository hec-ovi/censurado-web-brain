"""Run mode 3: the direct-from-link path (hand one persona a URL, get an article),
driven end to end against the shared fake.

The contracts pinned here:

  * the URL is FETCHED and seeds the grounding ledger, then the single assignment is
    drafted, finalized, and published, all WITHOUT the manager (no triage, no
    fan-out): ``run_manager`` is never called;
  * the fetch DEBITS the same per-article budget, so a huge page bounds the article
    (it drops on budget exhaustion) exactly like research spend does;
  * a bad/empty fetch degrades safely: the article grounds on corroboration instead,
    never crashing;
  * the HTTP route (``POST /articles/from-link``) returns 202 + a poll Location and
    validates the persona; the CLI ``direct`` verb runs the same path end to end.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

import httpx

from newsroom.brain import create_app
from newsroom.cli import main
from newsroom.config import Settings
from newsroom.db import open_db
from newsroom.inference.provider import DIALECTS, ProviderConfig
from newsroom.manager.coverage import CoverageStore
from newsroom.manager.preflight import ResolvedRoles
from newsroom.personas import Persona, PersonaStore
from newsroom.research.ledger import Ledger
from newsroom.runner import RunDeps, build_run_deps, run_direct
from newsroom.runner import run as run_mod
from newsroom.runs import RunStore


# ----- builders -----


def _cfg(fake, model: str) -> ProviderConfig:
    return ProviderConfig(
        role="x", provider="local", base_url=f"{fake.base_url}/v1", model=model, **DIALECTS["local"]
    )


def _roles(fake) -> ResolvedRoles:
    # drafter == evaluator endpoint -> rules-grounded evaluator (no eval model call).
    drafter = _cfg(fake, "drafter-model")
    return ResolvedRoles(
        drafter=drafter, evaluator=drafter, finalize=_cfg(fake, "finalize-model"),
        manager=drafter, evaluator_distinct=False,
    )


def _empty_ledger(_assignment, _spec, _persona, _budget) -> Ledger:
    """A corroboration double that adds nothing, so the article grounds ONLY on the
    URL seed (isolating the seed path)."""
    return Ledger()


def _ready_ledger(_assignment, _spec, _persona, _budget) -> Ledger:
    """A corroboration double that grounds the article on its own source, so a piece
    can still publish when the primary fetch fails."""
    led = Ledger(clock=lambda: datetime(2026, 6, 23, tzinfo=timezone.utc))
    led.add(claim="grounding", url="https://src.test/a", snippet="a source")
    return led


def _no_search(_query):
    return []


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


def _direct_deps(fake, settings, *, personas, fetch, make_ledger=None,
                 coverage_store=None, lock=None) -> RunDeps:
    conn = open_db(":memory:", check_same_thread=False)
    persona_store = PersonaStore(conn)
    for p in personas:
        persona_store.create(p)
    return RunDeps(
        store=RunStore(conn),
        persona_store=persona_store,
        coverage_store=coverage_store if coverage_store is not None else CoverageStore(conn),
        roles=_roles(fake),
        search_news=_no_search,
        make_ledger=make_ledger or _empty_ledger,
        publish_base_url=fake.base_url,
        operator_token="op-token",
        prompts_dir=settings.prompts_dir,
        settings=settings,
        lock=lock if lock is not None else threading.Lock(),
        fetch=fetch,
    )


def _finalize_ok(title="Direct Title", body="The finalized body.") -> str:
    return json.dumps({"title": title, "body": body, "topics": ["chips"]})


def _script_pipeline(fake, *, cite: str, title="Direct Title", body="The finalized body."):
    """Script one article's pipeline: outline, a draft that CITES the seed url (so the
    rules evaluator passes), an enriched body, then a valid finalize."""
    fake.state.script_chat("an outline")
    fake.state.script_chat(f"Grounded prose citing {cite}.")
    fake.state.script_chat("an enriched body")
    fake.state.script_chat(_finalize_ok(title, body))


# ----- the seed path: fetch, draft, publish, no manager -----


def test_direct_run_seeds_from_the_url_publishes_and_bypasses_the_manager(fake, tmp_path, monkeypatch):
    url = "https://clarin.com/nota-1"
    fetched: list[str] = []

    def fetch(u: str) -> str:
        fetched.append(u)
        return "Plaza de Mayo: la protesta del martes. Cuerpo de la nota."

    settings = _settings(fake, tmp_path)
    deps = _direct_deps(fake, settings, personas=[_ada()], fetch=fetch, make_ledger=_empty_ledger)

    # The manager must NEVER run on the direct path: a spy records any call.
    manager_calls: list = []
    monkeypatch.setattr(run_mod, "run_manager", lambda **kw: manager_calls.append(kw))

    _script_pipeline(fake, cite=url)

    report = run_direct(deps=deps, url=url, persona_id="ada", brief="cubri la protesta")

    assert report.mode == "direct"
    assert report.status == "done"
    assert manager_calls == []            # the manager was bypassed entirely
    assert fetched == [url]               # the link was fetched once and seeded
    assert [p.ok for p in report.published] == [True]
    # The seeded source reached the drafter's grounding (the ledger artifact carries the url).
    prompts = " ".join(json.dumps(r["body"]) for r in fake.state.chat_requests)
    assert url in prompts
    # The article was assigned to the named persona, in its own beat, and published.
    row = deps.store.list_assignments(run_id=report.run_id)[0]
    assert row.persona_id == "ada" and row.section == "tech" and row.status == "published"


def test_direct_run_uses_the_brief_as_the_angle(fake, tmp_path):
    url = "https://lanacion.com.ar/x"
    settings = _settings(fake, tmp_path)
    deps = _direct_deps(fake, settings, personas=[_ada()], fetch=lambda _u: "fuente", make_ledger=_empty_ledger)
    _script_pipeline(fake, cite=url)

    report = run_direct(deps=deps, url=url, persona_id="ada", brief="el angulo economico")

    assert report.status == "done"
    assert report.manifest.assignments[0].angle == "el angulo economico"


# ----- the fetch is budget-bounded -----


def test_a_large_fetch_debits_the_budget_and_drops_the_article(fake, tmp_path):
    # A huge page debits the per-article budget; with a tiny ceiling the budget is spent
    # by the fetch alone, so the article DROPS before drafting (budget_exhausted) and no
    # model call is ever made. This proves the fetch counts toward the same bound.
    settings = _settings(fake, tmp_path, per_article_token_budget=10)
    deps = _direct_deps(
        fake, settings, personas=[_ada()], fetch=lambda _u: "x" * 4000, make_ledger=_ready_ledger
    )

    report = run_direct(deps=deps, url="https://clarin.com/huge", persona_id="ada")

    assert [o.status for o in report.outcomes] == ["dropped"]
    assert report.outcomes[0].stop_reason == "budget_exhausted"
    assert report.published == []
    assert fake.state.chat_requests == []  # never drafted: the fetch spent the ceiling


# ----- a failed fetch degrades safely -----


def test_a_failed_fetch_grounds_on_corroboration_instead_of_crashing(fake, tmp_path):
    # The fetch returns "" (a blocked/bad URL). The article must not crash: it grounds on
    # the author's corroboration research instead and still publishes.
    settings = _settings(fake, tmp_path)
    deps = _direct_deps(
        fake, settings, personas=[_ada()], fetch=lambda _u: "", make_ledger=_ready_ledger
    )
    _script_pipeline(fake, cite="https://src.test/a")  # cite the corroboration source

    report = run_direct(deps=deps, url="https://blocked.test/x", persona_id="ada")

    assert report.status == "done"
    assert [p.ok for p in report.published] == [True]


def test_an_unknown_persona_finishes_clean_without_crashing(fake, tmp_path):
    settings = _settings(fake, tmp_path)
    deps = _direct_deps(fake, settings, personas=[_ada()], fetch=lambda _u: "x", make_ledger=_empty_ledger)

    report = run_direct(deps=deps, url="https://x.test/a", persona_id="ghost")

    assert report.status == "done"
    assert report.manifest.assignments == []
    assert fake.state.chat_requests == []  # nothing assigned, nothing drafted


# ----- the HTTP surface -----


def test_post_articles_from_link_returns_202_then_polls_to_published(fake, tmp_path, serve_app):
    url = "https://infobae.com/nota"
    settings = _settings(fake, tmp_path)
    deps = _direct_deps(fake, settings, personas=[_ada()], fetch=lambda _u: "fuente", make_ledger=_empty_ledger)
    base_url = serve_app(create_app(settings=settings, store=deps.persona_store, run_deps=deps))
    client = httpx.Client(base_url=base_url, timeout=10)
    _script_pipeline(fake, cite=url)

    accepted = client.post("/articles/from-link", json={"url": url, "persona_id": "ada", "brief": "cubrilo"})
    assert accepted.status_code == 202
    handle = accepted.json()
    assert handle["mode"] == "direct"
    assert accepted.headers["Location"] == f"/runs/{handle['run_id']}"

    final = _poll_until_done(client, handle["run_id"])
    assert final["status"] == "done"
    assert len(final["assignments"]) == 1
    assert final["assignments"][0]["status"] == "published"
    assert final["assignments"][0]["persona_id"] == "ada"
    client.close()


def test_from_link_rejects_an_unknown_persona(fake, tmp_path, serve_app):
    settings = _settings(fake, tmp_path)
    deps = _direct_deps(fake, settings, personas=[_ada()], fetch=lambda _u: "x", make_ledger=_empty_ledger)
    base_url = serve_app(create_app(settings=settings, store=deps.persona_store, run_deps=deps))
    client = httpx.Client(base_url=base_url, timeout=10)

    resp = client.post("/articles/from-link", json={"url": "https://x.test/a", "persona_id": "ghost"})

    assert resp.status_code == 404
    assert resp.json()["code"] == "persona_not_found"
    assert fake.state.chat_requests == []  # never started a run
    assert deps.store._conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    client.close()


def test_from_link_rejects_an_empty_url(fake, tmp_path, serve_app):
    settings = _settings(fake, tmp_path)
    deps = _direct_deps(fake, settings, personas=[_ada()], fetch=lambda _u: "x", make_ledger=_empty_ledger)
    base_url = serve_app(create_app(settings=settings, store=deps.persona_store, run_deps=deps))
    client = httpx.Client(base_url=base_url, timeout=10)

    resp = client.post("/articles/from-link", json={"url": "  ", "persona_id": "ada"})

    assert resp.status_code == 422
    assert resp.json()["code"] == "validation_failed"
    client.close()


# ----- the CLI verb -----


def test_cli_direct_writes_an_article_from_a_link(fake, tmp_path, capsys):
    url = "https://clarin.com/cli-nota"
    settings = _settings(fake, tmp_path)

    def build_deps(_s: Settings) -> RunDeps:
        # Use the TEST settings (fake URLs + tmp_path DB), not the CLI's load_settings().
        conn = open_db(settings.persona_db_path, check_same_thread=False)
        persona_store = PersonaStore(conn)
        persona_store.create(_ada())
        return build_run_deps(
            settings, conn=conn, lock=threading.Lock(), persona_store=persona_store,
            search_news=_no_search, make_ledger=_empty_ledger, fetch=lambda _u: "fuente de la nota",
        )

    _script_pipeline(fake, cite=url)

    code = main(
        ["direct", "--url", url, "--persona", "ada", "--brief", "una nota directa"],
        build_deps=build_deps,
    )

    assert code == 0
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["mode"] == "direct"
    assert summary["status"] == "done"
    assert summary["published"] == 1


def test_cli_direct_unknown_persona_is_a_clean_failure(fake, tmp_path, capsys):
    settings = _settings(fake, tmp_path)

    def build_deps(_s: Settings) -> RunDeps:
        conn = open_db(settings.persona_db_path, check_same_thread=False)
        return build_run_deps(
            settings, conn=conn, lock=threading.Lock(), persona_store=PersonaStore(conn),
            search_news=_no_search, make_ledger=_empty_ledger, fetch=lambda _u: "x",
        )

    code = main(["direct", "--url", "https://x.test/a", "--persona", "ghost"], build_deps=build_deps)

    assert code == 1  # _EXIT["failed"]
    summary = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert summary["status"] == "failed" and "ghost" in summary["error"]
    assert fake.state.chat_requests == []  # never ran a pipeline


def _poll_until_done(client: httpx.Client, run_id: str, *, timeout_s: float = 15.0) -> dict:
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        body = client.get(f"/runs/{run_id}").json()
        if body["status"] in ("done", "done_with_errors", "failed"):
            return body
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish within {timeout_s}s")
