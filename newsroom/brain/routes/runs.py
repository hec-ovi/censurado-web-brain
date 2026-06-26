"""The run LISTING API: a read-only window over the run records the run path creates.

The brain already exposes the run lifecycle inline in ``app.py``: ``POST /runs`` triggers
a run (mode + the operator's ``n`` / ``persona_ids`` targeting), ``POST
/articles/from-link`` triggers the direct path, and ``GET /runs/{id}`` surfaces ONE run
with its assignments and per-assignment statuses. What was missing was the collection
view: an operator (or the automation layer) needs to see ALL runs and their status
without knowing each id up front, to find the run that just failed or the last managed
batch. This router is that surface, mirroring the source/author listings' shape
(``limit``/``offset`` window plus ``total``, the shared ``_problem`` body).

The handler reads the shared ``RunStore`` off ``request.app.state.run_deps`` and the one
connection lock (the run store shares the single SQLite connection the rest of the brain
uses); the read goes under the lock so a console list and a running pipeline never race on
the connection. The router holds NO SQL: it calls the store's ``list_runs`` method and
windows the result, exactly like ``portals.list_portals``.

No auth here by design (the brain API has none today). Auth is wired in ONE place:
``create_app`` hangs the shared ``newsroom.brain.auth.require_auth`` seam on the app, so
it covers this router with every other route at once, no per-handler change.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from newsroom.runner import RunDeps
from newsroom.runs import Run

__all__ = ["router"]

router = APIRouter(tags=["runs"])


class RunOut(BaseModel):
    """The run-level view in the listing: the same run record fields ``GET /runs/{id}``
    returns, MINUS the per-assignment list (the collection view stays compact; a client
    drills into one run for its assignments).

    The identity key here is ``run_id`` (and a synthesis job's is ``job_id``), NOT the
    generic ``id`` that ``PortalOut`` / ``PersonaOut`` carry. This is intentional and kept
    for compatibility: a run/job id is what a client POSTs for and then POLLs on (it rides
    in the ``Location`` header and the 202 body), so the lifecycle name is the stable one
    callers already use; renaming it to ``id`` would break those pollers for no gain."""

    run_id: str
    mode: str
    status: str
    n_requested: int | None
    created_at: str
    finished_at: str | None


class RunListOut(BaseModel):
    """The GET /runs response: the ``limit``/``offset`` window plus ``total``, the count
    BEFORE pagination (so a client can page through the full set)."""

    runs: list[RunOut]
    total: int


def _out(run: Run) -> RunOut:
    """A stored run as a ``RunOut`` model; FastAPI serializes it from the route's
    ``response_model`` so the shape stays typed in the OpenAPI."""
    return RunOut(
        run_id=run.id,
        mode=run.mode,
        status=run.status,
        n_requested=run.n_requested,
        created_at=run.created_at,
        finished_at=run.finished_at,
    )


@router.get("/runs", status_code=200, response_model=RunListOut)
async def list_runs(
    request: Request,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    """List runs, NEWEST first, optionally filtered to a single ``status`` (``running`` |
    ``done`` | ``done_with_errors`` | ``failed``). ``total`` is the count BEFORE pagination
    (so a client can page), ``runs`` is the ``limit``/``offset`` window over the
    newest-first order the store returns. Drill into one run's assignments via
    ``GET /runs/{id}``."""
    deps: RunDeps = request.app.state.run_deps
    with request.app.state.lock:
        runs = deps.store.list_runs(status=status)
    total = len(runs)
    limit = max(0, limit)
    offset = max(0, offset)
    window = runs[offset : offset + limit]
    return RunListOut(runs=[_out(r) for r in window], total=total)
