"""The brain MANAGEMENT API: the two lifecycle actions that were CLI-only (infra C7).

Two ``censurado-brain`` subcommands wrote to the world but had no HTTP route, so a
headless caller could not drive them over the API like the rest of the infra:

  * ``mirror-authors`` -- push every local persona's PUBLIC fields (handle/name/bio/
    avatar) to the platform author registry, the brain->backend backfill. Exposed here as
    ``POST /mirror/authors`` (``?dry_run=true`` previews the handles without contacting
    the platform), mirroring the CLI's report shape.
  * ``bootstrap`` -- idempotently SEED a fresh box (editorial config + any default
    portals/personas) and mirror the platform authors. Exposed here as ``POST /bootstrap``
    so a headless caller can provision a fresh box over the API.

Both actions touch the network, so they execute OFF the event loop
(``anyio.to_thread``) exactly like the status probe, and ``bootstrap`` opens its own
connection from ``settings.persona_db_path`` (the same as the CLI subcommand), so it is
self-contained rather than reusing the request's connection.

No auth here by design (the brain API has none today); the app-wide auth seam in
``create_app`` covers this router like every other.
"""

from __future__ import annotations

import anyio
from fastapi import APIRouter, Request
from pydantic import BaseModel

from newsroom.bootstrap import bootstrap
from newsroom.brain.problems import _problem
from newsroom.migrate import author_fields
from newsroom.mirror import PushResult, backfill_web_authors, push_web_author
from newsroom.personas import Persona

__all__ = ["router"]

router = APIRouter(tags=["management"])


class FailedPushOut(BaseModel):
    """One author the backfill could not push: the handle plus the failure code/detail."""

    handle: str
    code: str
    detail: str = ""


class MirrorAuthorsOut(BaseModel):
    """The ``POST /mirror/authors`` report (the CLI ``mirror-authors`` shape): the handles
    successfully upserted to the platform, the per-handle failures, and whether this was a
    ``dry_run`` preview (which pushed nothing)."""

    pushed: list[str]
    failed: list[FailedPushOut]
    dry_run: bool


class BootstrapIn(BaseModel):
    """The ``POST /bootstrap`` body (no parameters; seeding is find-or-create)."""


class BootstrapOut(BaseModel):
    """The ``POST /bootstrap`` result (the CLI ``bootstrap`` shape): the per-category seed
    result and the platform-author reconcile result. ``seeded`` / ``reconciled`` keep the
    stores' own report dicts rather than re-modeling every nested field."""

    seeded: dict
    reconciled: dict


def _build_push(settings):
    """The production per-author push: upsert a persona's PUBLIC fields to the platform
    with the operator key (the key must carry admin:write). The private prompt never
    crosses the boundary."""

    def push(persona: Persona) -> PushResult:
        return push_web_author(
            settings.publish_base_url,
            settings.admin_token or settings.operator_token,
            **author_fields(persona),
        )

    return push


def _dry_run_push(persona: Persona) -> PushResult:
    """A no-network push that always reports success, so a dry run lists what WOULD be
    pushed without contacting the platform."""
    return PushResult(handle=persona.id, ok=True, status=0)


@router.post("/mirror/authors", status_code=200, response_model=MirrorAuthorsOut)
async def mirror_authors(request: Request, dry_run: bool = False):
    """Push every local persona's public fields to the platform author registry (the
    brain->backend backfill, idempotent: the platform upserts on handle). ``?dry_run=true``
    lists the handles that WOULD be pushed and contacts nothing. A real push with no
    operator token configured is a 503 ``not_configured`` (the credential is required to
    write the registry). The push runs OFF the event loop so a slow platform never blocks
    the route. Returns the per-handle report (pushed / failed / dry_run)."""
    state = request.app.state
    settings = state.settings
    with state.lock:
        personas = state.store.list(include_inactive=True)

    if dry_run:
        report = backfill_web_authors(personas, _dry_run_push)
        report.dry_run = True
        return MirrorAuthorsOut(**report.as_dict())

    if not settings.operator_token:
        return _problem(503, "not_configured", detail="no operator token configured")

    push = _build_push(settings)
    report = await anyio.to_thread.run_sync(lambda: backfill_web_authors(personas, push))
    return MirrorAuthorsOut(**report.as_dict())


@router.post("/bootstrap", status_code=200, response_model=BootstrapOut)
async def run_bootstrap(body: BootstrapIn, request: Request):
    """Idempotently seed the newsroom (editorial config + any default portals/personas)
    and mirror the platform authors. Safe to re-run: seeding is find-or-create. Seeding
    goes OFF the event loop, over a connection bootstrap opens from
    ``settings.persona_db_path``. Returns the seed + reconcile summary."""
    settings = request.app.state.settings
    result = await anyio.to_thread.run_sync(lambda: bootstrap(settings))
    return BootstrapOut(**result)
