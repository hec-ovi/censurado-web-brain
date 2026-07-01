"""The source (portal) management API: typed CRUD over the operator's news sources.

A portal is a local news site the publication pulls from (e.g. ``example.com``,
``news.example``). The registry is the allowlist that makes discovery place-local and
operator-owned, so an operator needs a first-class way to curate it: add a source,
describe it, point it at its feeds, group co-owned outlets, and enable/disable it
without a redeploy. This router is that surface, mirroring the persona routes' shape
(202/JSONResponse style, the shared ``_problem`` body).

Every handler reads the shared ``PortalStore`` and the single connection lock off
``request.app.state`` (the store shares the one SQLite connection the rest of the
brain uses); writes go under the lock so a panel edit and a concurrent request never
race on the connection. The router holds NO SQL: it calls the store's existing
methods (``create``/``update``/``delete``/``set_enabled``/``get``/``list``) and maps the
store's ``ValueError``/``KeyError`` to problem responses.

No auth here by design (the brain API has none today; ``operator_token`` is the
brain->web credential, not a guard on the brain's own API). Auth is wired in ONE place:
``create_app`` hangs the shared ``newsroom.brain.auth.require_auth`` seam on the app, so
it covers this router with every other route at once, no per-handler change.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from newsroom.brain.problems import _problem
from newsroom.editorial import Portal

__all__ = ["router"]

router = APIRouter(tags=["portals"])


class PortalIn(BaseModel):
    """The POST /portals body. ``domain`` is required (a URL or bare host; the store
    normalizes it and derives the id). Everything else carries the create defaults the
    ``Portal`` dataclass uses."""

    domain: str
    homepage: str = ""
    description: str = ""
    feed_urls: list[str] = []
    feed_type: str = "auto"
    language: str = "es"
    ownership_group: str = ""
    lean: str = "neutral"
    enabled: bool = True


class PortalPatch(BaseModel):
    """The PATCH /portals/{id} body: every field optional. A field left unset (``None``)
    is ignored, so a partial edit touches only what it names. An invalid value (e.g. an
    unknown ``feed_type``) is rejected by the store and surfaced as 422."""

    homepage: str | None = None
    description: str | None = None
    feed_urls: list[str] | None = None
    feed_type: str | None = None
    language: str | None = None
    ownership_group: str | None = None
    lean: str | None = None
    enabled: bool | None = None


class PortalOut(BaseModel):
    """The response shape: the full stored portal, including the managed id and
    timestamps, so a client round-trips exactly what the store holds."""

    id: str
    domain: str
    homepage: str
    description: str
    feed_urls: list[str]
    feed_type: str
    language: str
    ownership_group: str
    lean: str
    enabled: bool
    status: str
    last_checked: str
    last_ok: str
    created_at: str
    updated_at: str


class PortalListOut(BaseModel):
    """The GET /portals response: the ``limit``/``offset`` window plus ``total``, the
    count BEFORE pagination (so a client can page through the full set)."""

    portals: list[PortalOut]
    total: int


def _out(portal: Portal) -> PortalOut:
    """A stored portal as a ``PortalOut`` model; FastAPI serializes it from the
    route's ``response_model`` so the shape stays typed in the OpenAPI."""
    return PortalOut(**asdict(portal))


@router.post("/portals", status_code=201, response_model=PortalOut)
async def create_portal(body: PortalIn, request: Request):
    """Create a source from the body. 201 + the stored ``PortalOut`` on success. A
    duplicate domain (the store hits the unique constraint) -> 409 ``duplicate_domain``;
    any other store rejection (blank/underivable domain, invalid ``feed_type``) -> 422."""
    state = request.app.state
    portal = Portal(
        domain=body.domain,
        homepage=body.homepage,
        description=body.description,
        feed_urls=list(body.feed_urls),
        feed_type=body.feed_type,
        language=body.language,
        ownership_group=body.ownership_group,
        lean=body.lean,
        enabled=body.enabled,
    )
    try:
        with state.lock:
            stored = state.portal_store.create(portal)
    except ValueError as exc:
        if "already exists" in str(exc):
            return _problem(409, "duplicate_domain", detail=str(exc))
        return _problem(422, "invalid_portal", detail=str(exc))
    return _out(stored)


@router.get("/portals", status_code=200, response_model=PortalListOut)
async def list_portals(
    request: Request,
    enabled: bool | None = None,
    limit: int = 100,
    offset: int = 0,
):
    """List sources, optionally filtered to ``enabled``. ``total`` is the count BEFORE
    pagination (so a client can page), ``portals`` is the ``limit``/``offset`` window
    over the oldest-first order the store returns."""
    state = request.app.state
    with state.lock:
        portals = state.portal_store.list(enabled=enabled)
    total = len(portals)
    limit = max(0, limit)
    offset = max(0, offset)
    window = portals[offset : offset + limit]
    return PortalListOut(portals=[_out(p) for p in window], total=total)


@router.get("/portals/{portal_id}", status_code=200, response_model=PortalOut)
async def get_portal(portal_id: str, request: Request):
    state = request.app.state
    with state.lock:
        portal = state.portal_store.get(portal_id)
    if portal is None:
        return _problem(404, "portal_not_found", detail=f"no portal {portal_id!r}")
    return _out(portal)


@router.patch("/portals/{portal_id}", status_code=200, response_model=PortalOut)
async def patch_portal(portal_id: str, body: PortalPatch, request: Request):
    """Partial update: only the fields the body NAMES (non-``None``) are applied. 404 if
    the portal is missing; 422 on a value the store rejects (e.g. an invalid
    ``feed_type``)."""
    changes = body.model_dump(exclude_none=True)
    state = request.app.state
    if not changes:  # nothing to change; return the current state rather than touch updated_at
        with state.lock:
            portal = state.portal_store.get(portal_id)
        if portal is None:
            return _problem(404, "portal_not_found", detail=f"no portal {portal_id!r}")
        return _out(portal)
    try:
        with state.lock:
            stored = state.portal_store.update(portal_id, **changes)
    except KeyError:
        return _problem(404, "portal_not_found", detail=f"no portal {portal_id!r}")
    except ValueError as exc:
        return _problem(422, "invalid_portal", detail=str(exc))
    return _out(stored)


@router.delete("/portals/{portal_id}", status_code=204)
async def delete_portal(portal_id: str, request: Request):
    """Delete a source. 204 on removal; 404 if it did not exist."""
    state = request.app.state
    with state.lock:
        removed = state.portal_store.delete(portal_id)
    if not removed:
        return _problem(404, "portal_not_found", detail=f"no portal {portal_id!r}")
    return Response(status_code=204)


@router.post("/portals/{portal_id}/enable", status_code=200, response_model=PortalOut)
async def enable_portal(portal_id: str, request: Request):
    return _set_enabled(request, portal_id, True)


@router.post("/portals/{portal_id}/disable", status_code=200, response_model=PortalOut)
async def disable_portal(portal_id: str, request: Request):
    return _set_enabled(request, portal_id, False)


def _set_enabled(request: Request, portal_id: str, enabled: bool):
    """Flip ``enabled`` via the store, returning the updated ``PortalOut`` (404 if the
    portal is missing). Shared by the enable/disable routes."""
    state = request.app.state
    try:
        with state.lock:
            stored = state.portal_store.set_enabled(portal_id, enabled)
    except KeyError:
        return _problem(404, "portal_not_found", detail=f"no portal {portal_id!r}")
    return _out(stored)
