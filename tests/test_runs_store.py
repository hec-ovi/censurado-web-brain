"""The runs/assignments store: typed CRUD and the assignment lifecycle.

Drives the store's public API directly (it is in-process, no HTTP). Shares one
connection with a persona store so the foreign keys are real, the same way the
brain wires them.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from newsroom.db import open_db
from newsroom.personas import Persona, PersonaStore
from newsroom.runs import RunStore


class _Clock:
    """A clock that advances one second per call, for deterministic timestamps."""

    def __init__(self):
        self._t = datetime(2026, 6, 23, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        self._t += timedelta(seconds=1)
        return self._t


def _stores():
    conn = open_db(":memory:")
    personas = PersonaStore(conn, clock=_Clock())
    runs = RunStore(conn, clock=_Clock())
    return personas, runs


def _persona(personas: PersonaStore) -> str:
    return personas.create(
        Persona(display_name="Ada Reporter", beat="tech", who_i_am="x", style="y")
    ).id


# ----- runs -----


def test_create_run_assigns_id_and_round_trips():
    _, runs = _stores()
    run = runs.create_run(mode="managed", n_requested=3)
    assert run.id and run.status == "running" and run.n_requested == 3
    assert runs.get_run(run.id) == run


def test_create_run_rejects_invalid_mode():
    _, runs = _stores()
    with pytest.raises(ValueError, match="invalid run mode"):
        runs.create_run(mode="hourly")


def test_finish_run_sets_status_and_timestamp():
    _, runs = _stores()
    run = runs.create_run(mode="manual")
    runs.finish_run(run.id, status="done")
    finished = runs.get_run(run.id)
    assert finished.status == "done" and finished.finished_at


# ----- assignments -----


def test_create_assignment_round_trips():
    personas, runs = _stores()
    run = runs.create_run(mode="managed")
    pid = _persona(personas)
    a = runs.create_assignment(run_id=run.id, persona_id=pid, section="tech", angle="the angle")
    assert a.status == "assigned" and a.section == "tech" and a.angle == "the angle"
    assert runs.get_assignment(a.id) == a


def test_assignment_foreign_key_is_enforced():
    personas, runs = _stores()
    pid = _persona(personas)
    with pytest.raises(ValueError, match="cannot create assignment"):
        runs.create_assignment(run_id="no-such-run", persona_id=pid, section="tech")


def test_lifecycle_drafting_then_ready():
    personas, runs = _stores()
    run = runs.create_run(mode="managed")
    a = runs.create_assignment(run_id=run.id, persona_id=_persona(personas), section="tech")

    runs.mark_drafting(a.id)
    assert runs.get_assignment(a.id).status == "drafting"

    runs.finalize_assignment(
        a.id, final_body="# Body", content_hash="c" * 64,
        idempotency_key="k1", ledger_digest="sha256:abc",
    )
    ready = runs.get_assignment(a.id)
    assert ready.status == "ready"
    assert ready.final_body == "# Body"
    assert ready.content_hash == "c" * 64
    assert ready.idempotency_key == "k1"
    assert ready.ledger_digest == "sha256:abc"


def test_lifecycle_dropped_keeps_digest_and_never_a_body():
    personas, runs = _stores()
    run = runs.create_run(mode="managed")
    a = runs.create_assignment(run_id=run.id, persona_id=_persona(personas), section="tech")
    runs.mark_dropped(a.id, reason="budget_exhausted", ledger_digest="sha256:def")
    dropped = runs.get_assignment(a.id)
    assert dropped.status == "dropped"
    assert dropped.drop_reason == "budget_exhausted"
    assert dropped.ledger_digest == "sha256:def"
    assert dropped.final_body is None  # a dropped article is never published


def test_mark_published_records_platform_id():
    personas, runs = _stores()
    run = runs.create_run(mode="managed")
    a = runs.create_assignment(run_id=run.id, persona_id=_persona(personas), section="tech")
    runs.mark_published(a.id, published_id="art_000001")
    assert runs.get_assignment(a.id).status == "published"
    assert runs.get_assignment(a.id).published_id == "art_000001"


def test_unique_idempotency_key_rejects_a_second_finalize_with_the_same_key():
    personas, runs = _stores()
    run = runs.create_run(mode="managed")
    pid = _persona(personas)
    a1 = runs.create_assignment(run_id=run.id, persona_id=pid, section="tech")
    a2 = runs.create_assignment(run_id=run.id, persona_id=pid, section="tech")
    runs.finalize_assignment(a1.id, final_body="b", content_hash="h", idempotency_key="dup", ledger_digest="d")
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        runs.finalize_assignment(a2.id, final_body="b", content_hash="h", idempotency_key="dup", ledger_digest="d")


def test_update_missing_assignment_raises_key_error():
    _, runs = _stores()
    with pytest.raises(KeyError):
        runs.mark_drafting("no-such-assignment")


def test_list_assignments_for_a_run_is_ordered():
    personas, runs = _stores()
    run = runs.create_run(mode="managed")
    pid = _persona(personas)
    a = runs.create_assignment(run_id=run.id, persona_id=pid, section="tech", id="a")
    b = runs.create_assignment(run_id=run.id, persona_id=pid, section="world", id="b")
    listed = runs.list_assignments(run_id=run.id)
    assert [x.id for x in listed] == [a.id, b.id]
