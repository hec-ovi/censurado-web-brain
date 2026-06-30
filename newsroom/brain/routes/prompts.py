"""The prompt-library management API: typed CRUD over the versioned prompt templates.

The journalist/manager/etc. prompt ``.md`` files live in a versioned store (immutable
versions plus a per-key active pointer), mirroring the house style guide. This router
exposes them so an operator can read, edit, and roll back a prompt over HTTP without a
code change, mirroring the editorial router's shape (typed In/Out models, a
``response_model`` + ``status_code`` on every route, the shared ``_problem`` body).

A prompt's key is its relative path with forward slashes (e.g. ``journalist/research.md``).
Because a key contains a slash, it never sits in the URL path: it rides as a ``key`` query
param (reads) or in the body (the publish POST). ``POST /prompts/template`` publishes a NEW
version of a key (activating it by default); ``POST /prompts/versions/{version}/promote``
moves that key's pointer (this is also how a rollback works), and since a version is a
globally-unique int the key is derived from it, so the path needs no key.

Every handler reads the shared ``PromptStore`` and the single connection lock off
``request.app.state`` (the store shares the one SQLite connection the rest of the brain
uses); writes go under the lock so a console edit and a concurrent request never race on the
connection. The router holds NO SQL: it calls the store's methods and maps their
``KeyError`` / ``ValueError`` to problem responses (404 not_found / prompt_not_found, 422
invalid_prompt). Auth is wired app-wide in ``create_app``, so there is none here.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

from newsroom.brain.problems import _problem
from newsroom.editorial import PromptTemplate

__all__ = ["router"]

router = APIRouter(tags=["prompts"])


class PromptIn(BaseModel):
    """The POST /prompts/template body: a new version of a prompt ``key`` (its relative
    path, e.g. ``journalist/research.md``). ``created_by`` is the optional author note
    recorded on the version; ``activate`` (default True) makes the new version the live one
    for that key immediately -- set it False to stage a version an operator promotes later
    (note the FIRST version of a brand-new key is always activated regardless of this flag)."""

    key: str
    body: str = ""
    created_by: str = ""
    activate: bool = True


class PromptOut(BaseModel):
    """One stored prompt version: the ``key`` + ``body`` plus the store-managed ``version``
    / ``created_at`` and the ``is_active`` flag, so a client round-trips exactly what the
    store holds and can tell which version of the key is live."""

    key: str
    version: int
    body: str
    created_by: str
    created_at: str
    is_active: bool


class PromptListOut(BaseModel):
    """The GET /prompts response: one row per key (its ACTIVE version) plus ``total``, the
    count of keys -- the library overview an operator browses."""

    templates: list[PromptOut]
    total: int


class PromptVersionsOut(BaseModel):
    """The GET /prompts/versions response: the ``limit``/``offset`` window over one key's
    version history (NEWEST first, each marked with ``is_active``) plus ``total``, the count
    BEFORE pagination -- so an operator pages the audit trail exactly like the style
    listings rather than always pulling every version."""

    versions: list[PromptOut]
    total: int


def _prompt_out(template: PromptTemplate) -> PromptOut:
    """A stored prompt version as a ``PromptOut``; FastAPI serializes it from the route's
    ``response_model`` so the shape stays typed in the OpenAPI."""
    return PromptOut(
        key=template.key,
        version=template.version,
        body=template.body,
        created_by=template.created_by,
        created_at=template.created_at,
        is_active=template.is_active,
    )


@router.get("/prompts", status_code=200, response_model=PromptListOut)
async def list_prompts(request: Request):
    """The prompt library: one row per key, each its ACTIVE version. ``total`` is the count
    of keys. An empty store (before the seeder runs) is an empty list, not a 404."""
    state = request.app.state
    with state.lock:
        actives = [state.prompt_store.active(key) for key in state.prompt_store.list_keys()]
    templates = [_prompt_out(t) for t in actives if t is not None]
    return PromptListOut(templates=templates, total=len(templates))


def _read_shipped_prompt(prompts_dir: Path | str, key: str) -> str | None:
    """Read a shipped prompt ``key`` (``<role>/<name>.md``) from the on-disk prompt library,
    or ``None`` if it is absent. The shipped ``prompts/*.md`` are agnostic workflow content
    that travels with the brain, so they back the read path even before any seeder runs.
    Guards path traversal: the key must be a ``.md`` file that resolves to inside
    ``prompts_dir`` (a key with ``..`` or pointing elsewhere is treated as absent, never a
    file read), so this never serves a file outside the library."""
    if not key.endswith(".md"):
        return None
    root = Path(prompts_dir).resolve()
    candidate = root.joinpath(*key.split("/")).resolve()
    if not candidate.is_relative_to(root):
        return None
    try:
        return candidate.read_text(encoding="utf-8")
    except OSError:
        return None


@router.get("/prompts/template", status_code=200, response_model=PromptOut)
async def get_active_prompt(request: Request, key: str):
    """The ACTIVE version of one prompt ``key``. Prefers the versioned store (an operator's
    published edit), and when the store has no active version for the key it falls back to the
    on-disk prompt library shipped with the brain (``settings.prompts_dir``). So the shipped
    prompts serve on a fresh box with no seeding step, and the versioned store stays a pure
    OVERRIDE layer on top of them. 404 ``prompt_not_found`` only when NEITHER the store nor the
    on-disk library has the key. The on-disk fallback is reported as ``version`` 0, ``created_by``
    ``"disk"`` so a client can tell a shipped default from a stored version."""
    state = request.app.state
    with state.lock:
        template = state.prompt_store.active(key)
    if template is not None:
        return _prompt_out(template)
    body = _read_shipped_prompt(state.settings.prompts_dir, key)
    if body is None:
        return _problem(404, "prompt_not_found", detail=f"no active prompt for key {key!r}")
    return PromptOut(key=key, version=0, body=body, created_by="disk", created_at="", is_active=True)


@router.get("/prompts/versions", status_code=200, response_model=PromptVersionsOut)
async def list_prompt_versions(request: Request, key: str, limit: int = 100, offset: int = 0):
    """One key's version history, NEWEST first, each marked with ``is_active`` -- the audit
    trail an operator promotes (or rolls back) from. ``total`` is the count BEFORE
    pagination (so a client can page), ``versions`` is the ``limit``/``offset`` window."""
    state = request.app.state
    with state.lock:
        versions = state.prompt_store.list_versions(key)
    total = len(versions)
    limit = max(0, limit)
    offset = max(0, offset)
    window = versions[offset : offset + limit]
    return PromptVersionsOut(versions=[_prompt_out(t) for t in window], total=total)


@router.post("/prompts/template", status_code=201, response_model=PromptOut)
async def publish_prompt(body: PromptIn, request: Request):
    """Publish a NEW immutable version of a prompt ``key`` from the body. Because the store
    is versioned, this never overwrites: it inserts a new version and (when ``activate``,
    the default) makes it the live version of the key, so the prior version stays auditable
    and restorable via promote. 422 ``invalid_prompt`` on a blank key. Returns 201 + the
    stored version."""
    state = request.app.state
    try:
        with state.lock:
            stored = state.prompt_store.add_version(
                body.key, body.body, created_by=body.created_by, activate=body.activate
            )
    except ValueError as exc:
        return _problem(422, "invalid_prompt", detail=str(exc))
    return _prompt_out(stored)


@router.post("/prompts/versions/{version}/promote", status_code=200, response_model=PromptOut)
async def promote_prompt_version(version: int, request: Request):
    """Make ``version`` the active version of its key (this is also how a ROLLBACK works:
    promote an older version). The key is derived from the version (a version is globally
    unique). 404 ``not_found`` if the version does not exist. Returns the now-active
    version."""
    state = request.app.state
    try:
        with state.lock:
            template = state.prompt_store.get(version)
            if template is None:
                raise KeyError(version)
            promoted = state.prompt_store.promote(template.key, version)
    except KeyError:
        return _problem(404, "not_found", detail=f"no prompt version {version}")
    return _prompt_out(promoted)
