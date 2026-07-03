"""The editorial-config management API: typed CRUD over the operator's house style and
publication place (infra chunk 5).

Two operator-owned editorial parameters had stores but NO management surface: the house STYLE
GUIDE (the "model of redaction + rules": voice, exemplars, rules, the banned-term
``lexicon``, the ``sourcing`` block with ``min_sources``, and the ``structure`` hints)
and the publication LOCATION (region/language/city that scopes discovery). This router
exposes both so an operator can read and edit them over HTTP without a code change,
mirroring the source/author routers' shape (typed In/Out models, a ``response_model`` +
``status_code`` on every route, the shared ``_problem`` body).

The style guide is VERSIONED: it is kept as immutable versions plus a one-row active
pointer, so an "edit" is never an in-place overwrite. ``POST /editorial/style`` publishes
a NEW version (activating it by default); ``POST /editorial/style/versions/{n}/promote``
moves the pointer (this is also how a rollback works); the lexicon and sourcing
sub-resources (``PUT /editorial/style/lexicon`` / ``.../sourcing``) are conveniences that
derive a new version from the active one with just that facet replaced. The location is a
single editable row: ``PUT /editorial/location`` upserts it (partial, only the named
fields), and ``GET`` always returns a usable value (the default until one is stored).

Every handler reads the shared ``StyleStore`` / ``LocationStore`` and the single
connection lock off ``request.app.state`` (the stores share the one SQLite connection the
rest of the brain uses); writes go under the lock so a panel edit and a concurrent
request never race on the connection. The router holds NO SQL: it calls the stores' existing
methods (``active``/``add_version``/``promote``/``list_versions`` and ``get``/``set``) and
maps their ``KeyError``/``ValueError`` to problem responses (404 not_found, 422
invalid_location).

No auth here by design (the brain API has none today). Auth is wired in ONE place:
``create_app`` hangs the shared ``newsroom.brain.auth.require_auth`` seam on the app, so
it covers this router with every other route at once, no per-handler change.
"""

from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter, Request
from pydantic import BaseModel

from newsroom.brain.problems import _problem
from newsroom.editorial import Location, StyleGuide

__all__ = ["router"]

router = APIRouter(tags=["editorial"])


# --------------------------------------------------------------------------- #
# Style guide (versioned: voice + exemplars + rules + lexicon + sourcing + structure)
# --------------------------------------------------------------------------- #


class StyleGuideIn(BaseModel):
    """The POST /editorial/style body: a FULL house-style version. Every field carries
    the ``StyleGuide`` dataclass's create default, so a minimal body (just a ``voice``,
    say) is enough. ``created_by`` is the optional author note recorded on the version;
    ``activate`` (default True) makes the new version the live one immediately -- set it
    False to stage a version that an operator promotes later."""

    voice: str = ""
    exemplars: list = []
    rules: list = []
    lexicon: dict = {}
    sourcing: dict = {}
    structure: dict = {}
    created_by: str = ""
    activate: bool = True


class StyleGuideOut(BaseModel):
    """One stored style version: the full guide plus the store-managed ``version`` /
    ``created_at`` and the ``is_active`` flag, so a client round-trips exactly what the
    store holds and can tell which version is live."""

    version: int
    voice: str
    exemplars: list
    rules: list
    lexicon: dict
    sourcing: dict
    structure: dict
    created_by: str
    created_at: str
    is_active: bool


class StyleVersionsOut(BaseModel):
    """The GET /editorial/style/versions response: the ``limit``/``offset`` window over
    the version history (NEWEST first, each marked with ``is_active``) plus ``total``, the
    count BEFORE pagination -- so an operator pages the audit trail exactly like the
    source/run listings rather than always pulling every version."""

    versions: list[StyleGuideOut]
    total: int


class LexiconOut(BaseModel):
    """The banned-term lexicon facet of the active style: ``banned_terms`` (checked in
    code at review) and ``preferred_swaps`` (offered to the drafter as gentle steers)."""

    banned_terms: list[str] = []
    preferred_swaps: dict[str, str] = {}


class LexiconIn(LexiconOut):
    """The PUT /editorial/style/lexicon body. Same shape as ``LexiconOut`` but named for
    INPUT, so an ``-Out`` model is never bound as a request body (the In/Out naming the
    other write routes follow: ``PortalIn`` / ``PersonaIn`` / ``StyleGuideIn``)."""


class SourcingOut(BaseModel):
    """The sourcing facet of the active style: ``min_sources`` (the cross-source
    corroboration floor the evaluator enforces), plus the attribution / no-fabricated-quote
    requirements. ``min_sources`` is null when the active guide does not set one."""

    min_sources: int | None = None
    min_per_type: int | None = None
    type_fallback: str = ""
    require_attribution: bool = False
    no_fabricated_quotes: bool = False


class SourcingIn(SourcingOut):
    """The PUT /editorial/style/sourcing body. Same shape as ``SourcingOut`` but named for
    INPUT, so an ``-Out`` model is never bound as a request body (the In/Out naming the
    other write routes follow)."""


class LocationOut(BaseModel):
    """The publication place: the stored fields plus the derived Google-News views
    (``gl`` / ``hl`` / ``ceid``) callers actually pass to a feed, so a client gets the
    same shape the discovery seams read."""

    region: str
    ui_lang: str
    language: str
    gdelt_country: str
    city: str
    latlong: str
    updated_at: str
    gl: str
    hl: str
    ceid: str


class LocationIn(BaseModel):
    """The PUT /editorial/location body: every field optional. A field left unset
    (``None``) is ignored, so a partial edit touches only what it names. ``region`` /
    ``ui_lang`` / ``language`` cannot be set blank (the store rejects it -> 422)."""

    region: str | None = None
    ui_lang: str | None = None
    language: str | None = None
    gdelt_country: str | None = None
    city: str | None = None
    latlong: str | None = None


def _style_out(guide: StyleGuide) -> StyleGuideOut:
    """A stored style version as a ``StyleGuideOut``; FastAPI serializes it from the
    route's ``response_model`` so the shape stays typed in the OpenAPI."""
    return StyleGuideOut(
        version=guide.version,
        voice=guide.voice,
        exemplars=guide.exemplars,
        rules=guide.rules,
        lexicon=guide.lexicon,
        sourcing=guide.sourcing,
        structure=guide.structure,
        created_by=guide.created_by,
        created_at=guide.created_at,
        is_active=guide.is_active,
    )


def _location_out(location: Location) -> LocationOut:
    """A stored location as a ``LocationOut``, including the derived ``gl`` / ``hl`` /
    ``ceid`` views (properties on the dataclass, so they are added explicitly)."""
    return LocationOut(
        region=location.region,
        ui_lang=location.ui_lang,
        language=location.language,
        gdelt_country=location.gdelt_country,
        city=location.city,
        latlong=location.latlong,
        updated_at=location.updated_at,
        gl=location.gl,
        hl=location.hl,
        ceid=location.ceid,
    )


@router.get("/editorial/style", status_code=200, response_model=StyleGuideOut)
async def get_active_style(request: Request):
    """The ACTIVE house style: the live version (voice + exemplars + rules + lexicon +
    sourcing + structure). 404 ``not_found`` when no version has been published yet (a
    fresh box before the seeder runs)."""
    state = request.app.state
    with state.lock:
        guide = state.style_store.active()
    if guide is None:
        return _problem(404, "not_found", detail="no active style guide")
    return _style_out(guide)


@router.post("/editorial/style", status_code=201, response_model=StyleGuideOut)
async def publish_style(body: StyleGuideIn, request: Request):
    """Publish a NEW immutable style version from the full body. Because the store is
    versioned, this never overwrites: it inserts a new version and (when ``activate``,
    the default) makes it live, so the prior version stays auditable and restorable via
    promote. Returns 201 + the stored version."""
    state = request.app.state
    guide = StyleGuide(
        voice=body.voice,
        exemplars=list(body.exemplars),
        rules=list(body.rules),
        lexicon=dict(body.lexicon),
        sourcing=dict(body.sourcing),
        structure=dict(body.structure),
    )
    with state.lock:
        stored = state.style_store.add_version(
            guide, created_by=body.created_by, activate=body.activate
        )
    return _style_out(stored)


@router.get("/editorial/style/versions", status_code=200, response_model=StyleVersionsOut)
async def list_style_versions(request: Request, limit: int = 100, offset: int = 0):
    """The version history, NEWEST first, each marked with ``is_active`` -- the audit trail
    an operator promotes (or rolls back) from. ``total`` is the count BEFORE pagination (so
    a client can page), ``versions`` is the ``limit``/``offset`` window, exactly like the
    source/run listings."""
    state = request.app.state
    with state.lock:
        versions = state.style_store.list_versions()
    total = len(versions)
    limit = max(0, limit)
    offset = max(0, offset)
    window = versions[offset : offset + limit]
    return StyleVersionsOut(versions=[_style_out(g) for g in window], total=total)


@router.post(
    "/editorial/style/versions/{version}/promote", status_code=200, response_model=StyleGuideOut
)
async def promote_style_version(version: int, request: Request):
    """Make ``version`` the active style (this is also how a ROLLBACK works: promote an
    older version). 404 ``not_found`` if the version does not exist. Returns the now-active
    version."""
    state = request.app.state
    try:
        with state.lock:
            promoted = state.style_store.promote(version)
    except KeyError:
        return _problem(404, "not_found", detail=f"no style version {version}")
    return _style_out(promoted)


@router.get("/editorial/style/lexicon", status_code=200, response_model=LexiconOut)
async def get_lexicon(request: Request):
    """The banned-term lexicon of the ACTIVE style. 404 ``not_found`` when no style
    version is active yet."""
    state = request.app.state
    with state.lock:
        guide = state.style_store.active()
    if guide is None:
        return _problem(404, "not_found", detail="no active style guide")
    return LexiconOut(**(guide.lexicon or {}))


@router.put("/editorial/style/lexicon", status_code=200, response_model=LexiconOut)
async def set_lexicon(body: LexiconIn, request: Request):
    """Replace the lexicon facet. Respecting the versioned store, this derives a NEW
    active version from the active one with ONLY the lexicon replaced (the rest of the
    guide is carried over), so the change is auditable and reversible. 404 ``not_found``
    if there is no active guide to derive from (publish a full style first)."""
    state = request.app.state
    with state.lock:
        active = state.style_store.active()
        if active is None:
            return _problem(404, "not_found", detail="no active style guide")
        derived = replace(active, lexicon=body.model_dump())
        stored = state.style_store.add_version(
            derived, created_by=active.created_by, activate=True
        )
    return LexiconOut(**(stored.lexicon or {}))


@router.get("/editorial/style/sourcing", status_code=200, response_model=SourcingOut)
async def get_sourcing(request: Request):
    """The sourcing block of the ACTIVE style (``min_sources`` + the attribution /
    no-fabricated-quote flags). 404 ``not_found`` when no style version is active yet."""
    state = request.app.state
    with state.lock:
        guide = state.style_store.active()
    if guide is None:
        return _problem(404, "not_found", detail="no active style guide")
    return SourcingOut(**(guide.sourcing or {}))


@router.put("/editorial/style/sourcing", status_code=200, response_model=SourcingOut)
async def set_sourcing(body: SourcingIn, request: Request):
    """Replace the sourcing facet (the ``min_sources`` corroboration floor lives here).
    Like the lexicon PUT, this derives a NEW active version from the active one with only
    sourcing replaced, so the edit is versioned. 404 ``not_found`` if there is no active
    guide to derive from."""
    state = request.app.state
    with state.lock:
        active = state.style_store.active()
        if active is None:
            return _problem(404, "not_found", detail="no active style guide")
        derived = replace(active, sourcing=body.model_dump())
        stored = state.style_store.add_version(
            derived, created_by=active.created_by, activate=True
        )
    return SourcingOut(**(stored.sourcing or {}))


@router.get("/editorial/location", status_code=200, response_model=LocationOut)
async def get_location(request: Request):
    """The publication place. Always returns a usable value: the stored row, or the
    in-memory default (Argentina, Spanish) when nothing is stored yet -- so a client
    never branches on "is it configured"."""
    state = request.app.state
    with state.lock:
        location = state.location_store.get()
    return _location_out(location)


@router.put("/editorial/location", status_code=200, response_model=LocationOut)
async def set_location(body: LocationIn, request: Request):
    """Upsert the location, applying ONLY the named (non-``None``) fields over the
    current value. An empty body is a no-op read that returns the current location
    without bumping ``updated_at``. 422 ``invalid_location`` on an unknown field or a
    blank ``region`` / ``ui_lang`` / ``language`` (the store rejects it)."""
    changes = body.model_dump(exclude_none=True)
    state = request.app.state
    if not changes:  # nothing to change; return the current value without a write
        with state.lock:
            location = state.location_store.get()
        return _location_out(location)
    try:
        with state.lock:
            stored = state.location_store.set(**changes)
    except ValueError as exc:
        return _problem(422, "invalid_location", detail=str(exc))
    return _location_out(stored)
