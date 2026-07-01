"""The author (persona) MANAGEMENT API: typed CRUD over personas, with NO model call.

A persona is an author identity whose ``id`` becomes ``article.author`` at the publish
seam. The brain runs no model: an operator's CLI agent synthesizes a persona's voice
offline (following ``prompts/persona/synthesize.md``) and POSTs the finished JSON here;
this surface only validates and stores it. Personas also arrive via the platform mirror,
which reconciles the web author registry into local rows. This router is the operator
surface: persist a persona from explicit fields, edit its mutable fields, and remove it,
mirroring the source-management router's shape (typed In/Patch/Out models, a
``response_model`` + ``status_code`` on every route, the shared ``_problem`` body).

Create lives at ``POST /personas/direct`` (a pure persist). Update and delete take the
persona id on the path; they sit beside the inline read routes (``GET /personas`` and
``GET /personas/{persona_id}``, defined in ``create_app``) as distinct HTTP methods, so
the read path is untouched.

Every handler reads the shared ``PersonaStore`` and the single connection lock off
``request.app.state`` (the store shares the one SQLite connection the rest of the brain
uses); writes go under the lock so a panel edit and a concurrent request never race on
the connection. The router holds NO SQL: it calls the store's existing methods
(``create``/``update``/``delete``/``get``) and maps the store's ``ValueError``/``KeyError``
to problem responses (404 ``persona_not_found`` -- the resource-specific code the inline
``GET /personas/{id}`` also uses -- 409 on a duplicate id or an in-use delete, 422 on an
invalid field/beat).

No auth here by design (the brain API has none today). Auth is wired in ONE place:
``create_app`` hangs the shared ``newsroom.brain.auth.require_auth`` seam on the app, so
it covers this router with every other route at once, no per-handler change.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from newsroom.brain.problems import _problem
from newsroom.brain.routes.portals import PortalOut
from newsroom.brain.routes.portals import _out as _portal_out
from newsroom.contracts.sections import SECTION_ENUM, is_valid_section
from newsroom.editorial import PortalStore
from newsroom.personas import Persona

__all__ = ["router"]

router = APIRouter(tags=["personas"])


class PersonaIn(BaseModel):
    """The POST /personas/direct body. ``display_name``, ``beat``, ``who_i_am`` and
    ``style`` are the required identity fields the ``Persona`` dataclass needs; ``id`` is
    optional (the store derives it from ``display_name`` when blank). Everything else
    carries the create defaults the dataclass uses, so a minimal body is enough."""

    display_name: str
    beat: str
    who_i_am: str
    style: str
    id: str = ""
    about: str = ""
    language: str = "español neutro"
    few_shots_pos: list = []
    few_shots_neg: list = []
    sources: list[str] = []
    profile_topics: list[str] = []
    avatar_path: str = ""
    active: bool = True


class PersonaPatch(BaseModel):
    """The PATCH /personas/{id} body: every mutable field optional. A field left unset
    (``None``) is ignored, so a partial edit touches only what it names. The set mirrors
    the store's ``_MUTABLE_FIELDS`` exactly; an invalid value (e.g. an unknown ``beat``)
    is rejected by the store and surfaced as 422."""

    display_name: str | None = None
    beat: str | None = None
    who_i_am: str | None = None
    about: str | None = None
    style: str | None = None
    language: str | None = None
    few_shots_pos: list | None = None
    few_shots_neg: list | None = None
    sources: list[str] | None = None
    profile_topics: list[str] | None = None
    avatar_path: str | None = None
    active: bool | None = None


class PersonaOut(BaseModel):
    """The response shape: the full stored persona, including the managed id and
    timestamps, so a client round-trips exactly what the store holds."""

    id: str
    display_name: str
    beat: str
    who_i_am: str
    style: str
    about: str
    language: str
    few_shots_pos: list
    few_shots_neg: list
    sources: list
    profile_topics: list
    avatar_path: str
    active: bool
    created_at: str
    updated_at: str


def _out(persona: Persona) -> PersonaOut:
    """A stored persona as a ``PersonaOut`` model; FastAPI serializes it from the route's
    ``response_model`` so the shape stays typed in the OpenAPI."""
    return PersonaOut(**asdict(persona))


@router.post("/personas/direct", status_code=201, response_model=PersonaOut)
async def create_persona_direct(body: PersonaIn, request: Request):
    """Create a persona from explicit fields: the row is persisted immediately and the
    stored ``PersonaOut`` returned with 201. The id is the body's ``id`` when given, else
    derived from ``display_name``. A bad beat is rejected UP FRONT with 422
    ``invalid_beat``; a duplicate id -> 409 ``duplicate_id``; any other store rejection
    (underivable id) -> 422 ``invalid_persona``."""
    state = request.app.state
    if not is_valid_section(body.beat):
        return _problem(422, "invalid_beat", detail=f"beat must be one of {SECTION_ENUM}")
    persona = Persona(
        display_name=body.display_name,
        beat=body.beat,
        who_i_am=body.who_i_am,
        style=body.style,
        id=body.id,
        about=body.about,
        language=body.language,
        few_shots_pos=list(body.few_shots_pos),
        few_shots_neg=list(body.few_shots_neg),
        sources=list(body.sources),
        profile_topics=list(body.profile_topics),
        avatar_path=body.avatar_path,
        active=body.active,
    )
    try:
        with state.lock:
            stored = state.store.create(persona)
    except ValueError as exc:
        if "already exists" in str(exc):
            return _problem(409, "duplicate_id", detail=str(exc))
        return _problem(422, "invalid_persona", detail=str(exc))
    return _out(stored)


@router.patch("/personas/{persona_id}", status_code=200, response_model=PersonaOut)
async def patch_persona(persona_id: str, body: PersonaPatch, request: Request):
    """Partial update: only the fields the body NAMES (non-``None``) are applied. 404 if
    the persona is missing; 422 on a value the store rejects (e.g. an invalid ``beat``).
    An empty body is a no-op read that returns the current persona without bumping
    ``updated_at`` (so a panel "save" with no edits is idempotent)."""
    changes = body.model_dump(exclude_none=True)
    state = request.app.state
    if not changes:
        with state.lock:
            persona = state.store.get(persona_id)
        if persona is None:
            return _problem(404, "persona_not_found", detail=f"no persona {persona_id!r}")
        return _out(persona)
    try:
        with state.lock:
            stored = state.store.update(persona_id, **changes)
    except KeyError:
        return _problem(404, "persona_not_found", detail=f"no persona {persona_id!r}")
    except ValueError as exc:
        return _problem(422, "invalid_persona", detail=str(exc))
    return _out(stored)


@router.delete("/personas/{persona_id}", status_code=204)
async def delete_persona(persona_id: str, request: Request):
    """Delete a persona. 204 on removal; 404 if it did not exist; 409 ``persona_in_use``
    if an assignment still references it (the store forbids the delete rather than
    orphaning a run's authorship)."""
    state = request.app.state
    try:
        with state.lock:
            removed = state.store.delete(persona_id)
    except ValueError as exc:
        return _problem(409, "persona_in_use", detail=str(exc))
    if not removed:
        return _problem(404, "persona_not_found", detail=f"no persona {persona_id!r}")
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Source <-> author linking (infra chunk 3)
#
# A persona's ``sources`` field is the per-author research pool: a list of portal
# ids the persona sweeps (it falls back to the global registry when empty). C2's
# ``PATCH /personas/{id}`` can already overwrite that list BLIND -- it never checks
# the ids point at real sources. These routes are the VALIDATED linking surface: a
# persona can only be linked to a portal that exists in the ``PortalStore``, so the
# editorial "which sources does this author read" checkbox can never persist a
# dangling id. The link set IS the persona's ``sources`` list (no join table); these
# handlers read/validate against the portal store and write it back via the persona
# store's ``update``, exactly the field C2 patches.
# --------------------------------------------------------------------------- #


class SourceLinksIn(BaseModel):
    """The PUT /personas/{id}/sources body: the FULL set of portal ids the persona
    should be linked to (a replace, not a merge). The field is ``sources`` -- the SAME
    name the persona carries everywhere else (``PersonaIn`` / ``PersonaPatch`` /
    ``PersonaOut``), since the link set IS the persona's ``sources`` list. An empty list
    clears the pool. Each id is validated against the ``PortalStore`` before the write; an
    unknown id is rejected (422) rather than silently stored, so the set can never carry a
    dangling link."""

    sources: list[str] = []


class PersonaSourcesOut(BaseModel):
    """The persona's source pool. ``sources`` is the persona's ``sources`` field
    verbatim (the stored link set, in order); ``portals`` is each of those ids resolved
    to its live ``PortalOut``. An id that no longer resolves (its portal was deleted, or
    a blind C2 patch wrote a stray id) still appears in ``sources`` but NOT in
    ``portals`` -- so a stale link is visible to the operator rather than silently
    dropped, and can be cleaned up by unlinking it."""

    persona_id: str
    sources: list[str]
    portals: list[PortalOut]


def _dedup(ids: list[str]) -> list[str]:
    """The id list with duplicates removed, first-seen order preserved, so the stored
    link set is clean (a portal is linked once) without reordering the operator's set."""
    seen: set[str] = set()
    out: list[str] = []
    for pid in ids:
        if pid not in seen:
            seen.add(pid)
            out.append(pid)
    return out


def _sources_out(persona: Persona, portal_store: PortalStore) -> PersonaSourcesOut:
    """Build the source-pool view for ``persona``: its raw ``sources`` ids plus those
    resolved to live ``PortalOut`` rows. Skips an id whose portal is gone (it stays in
    ``sources``), so the view never invents a row for a stale link. The caller holds
    the connection lock (the portal lookups share the one connection)."""
    portals: list[PortalOut] = []
    for pid in persona.sources:
        portal = portal_store.get(pid)
        if portal is not None:
            portals.append(_portal_out(portal))
    return PersonaSourcesOut(
        persona_id=persona.id, sources=list(persona.sources), portals=portals
    )


@router.get("/personas/{persona_id}/sources", status_code=200, response_model=PersonaSourcesOut)
async def get_persona_sources(persona_id: str, request: Request):
    """The persona's current source pool: its linked portal ids plus those resolved to
    the live portal rows. 404 if the persona is missing."""
    state = request.app.state
    with state.lock:
        persona = state.store.get(persona_id)
        if persona is None:
            return _problem(404, "persona_not_found", detail=f"no persona {persona_id!r}")
        return _sources_out(persona, state.portal_store)


@router.put("/personas/{persona_id}/sources", status_code=200, response_model=PersonaSourcesOut)
async def set_persona_sources(persona_id: str, body: SourceLinksIn, request: Request):
    """SET the full link set from a list of portal ids (replace, not merge). 404 if the
    persona is missing; 422 ``unknown_source`` (naming the offending ids) if ANY id does
    not exist in the portal registry -- validated BEFORE the write, so a bad id never
    lands. Duplicates collapse; an empty list clears the pool. Returns the resolved pool."""
    state = request.app.state
    ids = _dedup(body.sources)
    with state.lock:
        persona = state.store.get(persona_id)
        if persona is None:
            return _problem(404, "persona_not_found", detail=f"no persona {persona_id!r}")
        unknown = [pid for pid in ids if state.portal_store.get(pid) is None]
        if unknown:
            return _problem(422, "unknown_source", detail=f"unknown source id(s): {unknown}")
        stored = state.store.update(persona_id, sources=ids)
        return _sources_out(stored, state.portal_store)


@router.post(
    "/personas/{persona_id}/sources/{portal_id}", status_code=200, response_model=PersonaSourcesOut
)
async def link_persona_source(persona_id: str, portal_id: str, request: Request):
    """Link ONE portal to the persona (idempotent add). 404 if the persona OR the portal
    is missing. Re-linking an already-linked source is a success that leaves the pool (and
    ``updated_at``) untouched. Returns the resolved pool."""
    state = request.app.state
    with state.lock:
        persona = state.store.get(persona_id)
        if persona is None:
            return _problem(404, "persona_not_found", detail=f"no persona {persona_id!r}")
        if state.portal_store.get(portal_id) is None:
            return _problem(404, "portal_not_found", detail=f"no portal {portal_id!r}")
        if portal_id in persona.sources:  # already linked: idempotent, no write
            return _sources_out(persona, state.portal_store)
        stored = state.store.update(persona_id, sources=[*persona.sources, portal_id])
        return _sources_out(stored, state.portal_store)


@router.delete(
    "/personas/{persona_id}/sources/{portal_id}", status_code=200, response_model=PersonaSourcesOut
)
async def unlink_persona_source(persona_id: str, portal_id: str, request: Request):
    """Unlink ONE portal from the persona (idempotent remove). 404 only if the PERSONA is
    missing -- a missing portal is NOT a 404 here, so an operator can clean a stale link
    whose portal was already deleted. Unlinking an absent source is a success that leaves
    the pool (and ``updated_at``) untouched. Returns the resolved pool."""
    state = request.app.state
    with state.lock:
        persona = state.store.get(persona_id)
        if persona is None:
            return _problem(404, "persona_not_found", detail=f"no persona {persona_id!r}")
        if portal_id not in persona.sources:  # not linked: idempotent, no write
            return _sources_out(persona, state.portal_store)
        remaining = [pid for pid in persona.sources if pid != portal_id]
        stored = state.store.update(persona_id, sources=remaining)
        return _sources_out(stored, state.portal_store)
