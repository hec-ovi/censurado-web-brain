"""The author (persona) MANAGEMENT API: typed, non-synthesis CRUD over personas.

A persona is an author identity whose ``id`` becomes ``article.author`` at the publish
seam. The brain already grows personas two ways: asynchronous SYNTHESIS (``POST
/personas``, an off-request LLM job that drafts a voice from a seed brief) and the
platform mirror (which reconciles the web author registry into local rows). What was
missing was the plain operator surface: build a persona from explicit fields with NO
model call, edit its mutable fields, and remove it. This router is that surface,
mirroring the source-management router's shape (typed In/Patch/Out models, a
``response_model`` + ``status_code`` on every route, the shared ``_problem`` body).

The direct create lives at ``POST /personas/direct`` so it never collides with the
existing synthesis route at ``POST /personas`` (synthesis stays where clients already
poll it). Update and delete take the persona id on the path; they sit beside the inline
``GET /personas/{persona_id}`` as distinct HTTP methods, so the read path is untouched.

Every handler reads the shared ``PersonaStore`` and the single connection lock off
``request.app.state`` (the store shares the one SQLite connection the rest of the brain
uses); writes go under the lock so a console edit and a running pipeline never race on
the connection. The router holds NO SQL: it calls the store's existing methods
(``create``/``update``/``delete``/``get``) and maps the store's ``ValueError``/``KeyError``
to problem responses (404 not_found, 409 on a duplicate id or an in-use delete, 422 on
an invalid field/beat).

No auth here by design (the brain API has none today). The router is structured so a
future central auth dependency can be added on the ``APIRouter`` without touching the
handlers.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from newsroom.brain.problems import _problem
from newsroom.personas import Persona

__all__ = ["router"]

router = APIRouter()


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
    """Create a persona from explicit fields, with NO synthesis job: the row is
    persisted immediately and the stored ``PersonaOut`` returned with 201. The id is the
    body's ``id`` when given, else derived from ``display_name``. A duplicate id -> 409
    ``duplicate_id``; any other store rejection (invalid beat, underivable id) -> 422
    ``invalid_persona``."""
    state = request.app.state
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


@router.patch("/personas/{persona_id}", response_model=PersonaOut)
async def patch_persona(persona_id: str, body: PersonaPatch, request: Request):
    """Partial update: only the fields the body NAMES (non-``None``) are applied. 404 if
    the persona is missing; 422 on a value the store rejects (e.g. an invalid ``beat``).
    An empty body is a no-op read that returns the current persona without bumping
    ``updated_at`` (so a console "save" with no edits is idempotent)."""
    changes = body.model_dump(exclude_none=True)
    state = request.app.state
    if not changes:
        with state.lock:
            persona = state.store.get(persona_id)
        if persona is None:
            return _problem(404, "not_found", detail=f"no persona {persona_id!r}")
        return _out(persona)
    try:
        with state.lock:
            stored = state.store.update(persona_id, **changes)
    except KeyError:
        return _problem(404, "not_found", detail=f"no persona {persona_id!r}")
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
        return _problem(404, "not_found", detail=f"no persona {persona_id!r}")
    return Response(status_code=204)
