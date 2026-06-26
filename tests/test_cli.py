"""The automation entry point (Step 10): ``newsroom.cli.main``, driven end to end.

This is the command an external periodic trigger invokes. The tests exercise the
REAL entry point (``main`` with parsed argv) through to its side effects against the
shared fake: a full managed run publishes one article, a publish failure surfaces as
a non-zero exit, a crash is recorded and reported, and the arg surface (mode / n /
persona subset) resolves the scope it should.

The headline test goes through the PRODUCTION dependency assembly
(``build_run_deps`` + ``roles_for_settings``); only the two network seams (web search
and the research loop) are stubbed, so the run never leaves the box while the rest of
the wiring is the real thing.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

import pytest

from newsroom.cli import main
from newsroom.config import Settings
from newsroom.db import open_db
from newsroom.personas import Persona, PersonaStore
from newsroom.research.ledger import Ledger
from newsroom.runner import build_run_deps


# ----- builders (a thin local copy of the run scripting) -----


def _settings(fake, tmp_path, **over) -> Settings:
    base = dict(
        persona_db_path=tmp_path / "brain.db",
        inference_base_url=f"{fake.base_url}/v1",
        publish_base_url=fake.base_url,
        operator_token="op-token",
    )
    base.update(over)
    return Settings(**base)


def _ready_ledger(_assignment, _spec, _persona, _budget) -> Ledger:
    """A make_ledger double: a non-empty grounded ledger so the rules evaluator can
    PASS, without running the research loop or touching the network."""
    led = Ledger(clock=lambda: datetime(2026, 6, 23, tzinfo=timezone.utc))
    led.add(claim="grounding", url="https://src.test/a", snippet="a source")
    return led


def _no_search(_query: str) -> list:
    return []


def _assign(persona_id: str, headline: str = "A fresh story") -> str:
    return json.dumps(
        {"action": "assign", "assignments": [
            {"persona_id": persona_id, "headline": headline, "angle": "cover it", "triage": "new"}
        ]}
    )


def _assign_many(specs: list[tuple[str, str]]) -> str:
    return json.dumps(
        {"action": "assign", "assignments": [
            {"persona_id": pid, "headline": h, "angle": "cover it", "triage": "new"} for pid, h in specs
        ]}
    )


def _finalize_ok(title: str = "Title", body: str = "Final body.") -> str:
    return json.dumps({"title": title, "body": body, "topics": ["chips"]})


def _ada() -> Persona:
    return Persona(id="ada", display_name="Ada", beat="tech", who_i_am="I cover chips.", style="dry")


def _bea() -> Persona:
    return Persona(id="bea", display_name="Bea", beat="world", who_i_am="I cover summits.", style="wry")


def _deps_with(fake, settings, personas, *, search_news=None, make_ledger=None):
    """Build run deps through the REAL production assembly over a fresh connection,
    pre-seeding the personas. Only the two network seams are stubbed; everything else
    (the store, coverage, the resolved roles pointed at the fake) is the real wiring
    the command uses in production."""
    conn = open_db(settings.persona_db_path, check_same_thread=False)
    persona_store = PersonaStore(conn)
    for persona in personas:
        persona_store.create(persona)
    return build_run_deps(
        settings,
        conn=conn,
        lock=threading.Lock(),
        persona_store=persona_store,
        search_news=search_news or _no_search,
        make_ledger=make_ledger or _ready_ledger,
    )


# ----- the full managed run, end to end through the command -----


def test_cli_managed_run_publishes_end_to_end(fake, tmp_path, capsys):
    # The default invocation (no args) is a managed run: the command picks the managed
    # mode, the manager assigns one story to ada, her pipeline drafts and finalizes it,
    # and the publish tail POSTs it. Exit 0, a JSON summary, and the real side effects.
    settings = _settings(fake, tmp_path)
    deps = _deps_with(fake, settings, [_ada()])
    fake.state.script_chat(_assign("ada", headline="Chips ship early"))
    for body in ("an outline", "a clean draft", "an enriched body"):
        fake.state.script_chat(body)
    fake.state.script_chat(_finalize_ok("Chips Ship Early", "The chips shipped."))

    code = main([], build_deps=lambda _s: deps)  # no args -> default mode "managed"

    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "managed"
    assert out["status"] == "done"
    assert (out["assigned"], out["published"], out["failed"]) == (1, 1, 0)

    # The run really happened: one article on the platform (published via the default
    # atomic batch), the run row closed, and a coverage row so the next run will not
    # republish the story.
    assert len(fake.state.batch_requests) == 1 and len(fake.state.batch_requests[0]["items"]) == 1
    run = deps.store.get_run(out["run_id"])
    assert run.status == "done" and run.finished_at
    coverage = deps.coverage_store.recent(limit=5)
    assert len(coverage) == 1 and coverage[0].headline == "Chips Ship Early"


def test_cli_publish_failure_exits_2_and_keeps_the_article(fake, tmp_path, capsys):
    # A finalized article whose publish 403s makes the run done_with_errors; the command
    # exits 2 (not 0) so an operator's alerting fires, and the body is kept.
    settings = _settings(fake, tmp_path, operator_token="noscope-token")  # fake 403s this key
    deps = _deps_with(fake, settings, [_ada()])
    fake.state.script_chat(_assign("ada"))
    for body in ("outline", "draft", "enriched"):
        fake.state.script_chat(body)
    fake.state.script_chat(_finalize_ok())

    code = main(["--mode", "managed"], build_deps=lambda _s: deps)

    assert code == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "done_with_errors"
    assert (out["assigned"], out["published"], out["failed"]) == (1, 0, 1)
    row = deps.store.list_assignments(run_id=out["run_id"])[0]
    assert row.status == "publish_failed" and row.final_body  # kept, re-publishable


def test_cli_failed_run_exits_1_and_reports_the_error(fake, tmp_path, capsys):
    # If the run itself raises, the command exits 1 and prints a failed summary with the
    # error; the run record is marked failed (a dead run is re-run, never lost).
    class _BoomCoverage:
        def recent(self, *, limit):
            raise RuntimeError("coverage store is down")

    settings = _settings(fake, tmp_path)
    deps = _deps_with(fake, settings, [_ada()])
    deps.coverage_store = _BoomCoverage()

    code = main(["--mode", "managed"], build_deps=lambda _s: deps)

    assert code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "failed"
    assert "coverage store is down" in out["error"]
    assert deps.store.get_run(out["run_id"]).status == "failed"


def test_cli_n_zero_is_a_clean_noop(fake, tmp_path, capsys):
    # --n 0 is a no-op trigger: it short-circuits before the manager, spends zero
    # inference, and still exits 0 with an empty summary.
    settings = _settings(fake, tmp_path)
    deps = _deps_with(fake, settings, [_ada()])

    code = main(["--mode", "managed", "--n", "0"], build_deps=lambda _s: deps)

    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["assigned"], out["published"], out["failed"]) == (0, 0, 0)
    assert fake.state.chat_requests == []  # n=0 spent zero inference


def test_cli_dropped_article_is_surfaced_in_the_summary(fake, tmp_path, capsys):
    # An article the pipeline drops (invalid finalize on both the attempt and its retry)
    # makes a "done" run that published nothing. It is NOT a publish failure, so it would
    # be invisible if the summary only counted publishes; the `dropped` count surfaces it
    # and keeps assigned == published + failed + dropped consistent.
    settings = _settings(fake, tmp_path)
    deps = _deps_with(fake, settings, [_ada()])
    fake.state.script_chat(_assign("ada"))
    for body in ("outline", "draft", "enriched"):
        fake.state.script_chat(body)
    fake.state.script_chat(json.dumps({"title": ""}))  # invalid finalize
    fake.state.script_chat(json.dumps({"title": ""}))  # invalid retry

    code = main(["--mode", "managed"], build_deps=lambda _s: deps)

    assert code == 0  # the run itself is fine; one article dropped
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "done"
    assert (out["assigned"], out["published"], out["failed"], out["dropped"]) == (1, 0, 0, 1)
    assert fake.state.publish_requests == []  # a dropped body is never published


def test_cli_run_creation_failure_exits_1_with_a_null_run_id(fake, tmp_path, capsys):
    # If even creating the run record fails (a store/disk error before the run exists),
    # the command still prints a parseable failed summary with a null run id and exits 1,
    # rather than crashing with only a traceback.
    settings = _settings(fake, tmp_path)
    deps = _deps_with(fake, settings, [_ada()])

    class _BoomStore:
        def create_run(self, **_kw):
            raise RuntimeError("cannot create run")

    deps.store = _BoomStore()

    code = main(["--mode", "managed"], build_deps=lambda _s: deps)

    assert code == 1
    out = json.loads(capsys.readouterr().out)
    assert out["run_id"] is None and out["status"] == "failed"
    assert "cannot create run" in out["error"]
    assert fake.state.chat_requests == []  # failed before any inference


def test_cli_persona_ids_scope_the_run(fake, tmp_path, capsys):
    # --persona-ids parses to a subset that scopes the run: the manager tries to assign
    # both personas, but only the in-scope one (ada) is assignable; an unknown id is
    # silently skipped, not an error.
    settings = _settings(fake, tmp_path)
    deps = _deps_with(fake, settings, [_ada(), _bea()])
    fake.state.script_chat(_assign_many([("ada", "A tech story"), ("bea", "A world story")]))
    for body in ("outline", "draft", "enriched"):
        fake.state.script_chat(body)
    fake.state.script_chat(_finalize_ok())

    code = main(["--mode", "manual", "--persona-ids", "ada, ghost"], build_deps=lambda _s: deps)

    assert code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mode"] == "manual"
    assert (out["assigned"], out["published"]) == (1, 1)  # bea out of scope, ghost skipped
    assert deps.store.list_assignments(run_id=out["run_id"])[0].persona_id == "ada"


def test_cli_rejects_an_unknown_mode(fake, tmp_path):
    # argparse's choices= guard rejects a mode outside the trigger surface before any
    # run is built: a SystemExit (the standard argparse usage-error exit).
    settings = _settings(fake, tmp_path)
    deps = _deps_with(fake, settings, [_ada()])
    with pytest.raises(SystemExit):
        main(["--mode", "turbo"], build_deps=lambda _s: deps)
    assert fake.state.chat_requests == []  # rejected before any inference
