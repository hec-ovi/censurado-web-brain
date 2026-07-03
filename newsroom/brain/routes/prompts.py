"""The prompt-library management API: typed CRUD over the versioned prompt templates.

Two prompt families share this router. The AGENTIC WORKFLOW nodes (keys under
``workflow/``, e.g. ``workflow/30-research.md``, plus ``workflow/manifest.json``) are plain
FILES in the shipped prompt library: a read serves the file, a publish WRITES the file in
place, and they are versioned by git, not the DB. The author-voice and house-style prompts
(e.g. ``persona/synthesize.md``) use the versioned store (immutable versions plus a per-key
active pointer), so an operator can edit and roll them back over HTTP without a code change.
Both mirror the editorial router's shape (typed In/Out models, a ``response_model`` +
``status_code`` on every route, the shared ``_problem`` body).

A prompt's key is its relative path with forward slashes (e.g. ``workflow/30-research.md``).
Because a key contains a slash, it never sits in the URL path: it rides as a ``key`` query
param (reads) or in the body (the publish POST). ``POST /prompts/template`` publishes a NEW
version of a key (activating it by default); ``POST /prompts/versions/{version}/promote``
moves that key's pointer (this is also how a rollback works), and since a version is a
globally-unique int the key is derived from it, so the path needs no key.

Every handler reads the shared ``PromptStore`` and the single connection lock off
``request.app.state`` (the store shares the one SQLite connection the rest of the brain
uses); writes go under the lock so a panel edit and a concurrent request never race on the
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
    path, e.g. ``workflow/30-research.md``). ``created_by`` is the optional author note
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


def _list_shipped_keys(prompts_dir: Path | str) -> list[str]:
    """Every shipped prompt key under ``prompts_dir`` (the on-disk library), each its relative
    path with forward slashes (e.g. ``workflow/95-image.md``). Globs ``*.md`` and
    ``*.json`` (the step-gate's ``workflow/manifest.json`` node-order config rides in the same
    library), so a stray file of another extension is never a key. The shipped
    ``prompts/**`` are the CANONICAL workflow content that travels with the brain, so the
    listing knows them even before any seeder lifts them into the versioned store."""
    root = Path(prompts_dir)
    keys: list[str] = []
    for pattern in ("*.md", "*.json"):
        for path in root.rglob(pattern):
            if not path.is_file():
                continue
            keys.append(path.relative_to(root).as_posix())
    return keys


@router.get("/prompts", status_code=200, response_model=PromptListOut)
async def list_prompts(request: Request):
    """The prompt library: one row per key, each its ACTIVE version. The key set is the UNION
    of the versioned store's keys and the shipped on-disk keys, so a fresh box (empty store,
    before any seeder runs) still lists every shipped workflow prompt. For a key the store has
    an active version of, that stored version wins (the store is the OVERRIDE layer); otherwise
    the shipped disk body is synthesized as ``version`` 0, ``created_by`` ``"disk"``, exactly
    as the single-key read path does. Keys are deduped (a stored override never doubles with its
    disk file) and sorted for stable output. ``total`` is the count of keys."""
    state = request.app.state
    with state.lock:
        keys = set(state.prompt_store.list_keys()) | set(
            _list_shipped_keys(state.settings.prompts_dir)
        )
        actives = {key: state.prompt_store.active(key) for key in keys}
        templates: list[PromptOut] = []
        for key in sorted(keys):
            # Workflow nodes are file-based: always serve the disk file, never a store row
            # (a stale seeded version must never shadow the git-versioned file).
            stored = None if _is_workflow(key) else actives[key]
            if stored is not None:
                templates.append(_prompt_out(stored))
                continue
            body = _read_shipped_prompt(state.settings.prompts_dir, key)
            if body is None:
                continue
            templates.append(
                PromptOut(
                    key=key, version=0, body=body, created_by="disk", created_at="", is_active=True
                )
            )
    return PromptListOut(templates=templates, total=len(templates))


def _read_shipped_prompt(prompts_dir: Path | str, key: str) -> str | None:
    """Read a shipped prompt ``key`` (``<role>/<name>.md``) from the on-disk prompt library,
    or ``None`` if it is absent. The shipped ``prompts/*.md`` are agnostic workflow content
    that travels with the brain, so they back the read path even before any seeder runs.
    Guards path traversal: the key must be a ``.md`` or ``.json`` file that resolves to inside
    ``prompts_dir`` (a key with ``..`` or pointing elsewhere is treated as absent, never a
    file read), so this never serves a file outside the library. ``.json`` is allowed so the
    step-gate's ``workflow/manifest.json`` (the per-mode node order) is served from the same
    library as the node bodies."""
    if not (key.endswith(".md") or key.endswith(".json")):
        return None
    root = Path(prompts_dir).resolve()
    candidate = root.joinpath(*key.split("/")).resolve()
    if not candidate.is_relative_to(root):
        return None
    try:
        return candidate.read_text(encoding="utf-8")
    except OSError:
        return None


_WORKFLOW_PREFIX = "workflow/"


def _is_workflow(key: str) -> bool:
    """Agentic-workflow node keys (``workflow/<node>.md`` and ``workflow/manifest.json``) are
    file-based: served and edited straight on disk, never through the versioned store."""
    return key.startswith(_WORKFLOW_PREFIX)


def _write_shipped_prompt(prompts_dir: Path | str, key: str, body: str) -> None:
    """Write a workflow node ``key`` back to the on-disk prompt library (an edit in place; the
    file is git-versioned, so there is no DB version). Same path-traversal guard as the read
    side: the key must be an EXISTING ``.md``/``.json`` file that resolves inside
    ``prompts_dir``. Raises ``ValueError`` on a bad key, so the route maps it to 422; adding a
    brand-new node is a code change (it needs a manifest entry too), not an API write."""
    if not (key.endswith(".md") or key.endswith(".json")):
        raise ValueError(f"workflow key must be a .md or .json file: {key!r}")
    root = Path(prompts_dir).resolve()
    candidate = root.joinpath(*key.split("/")).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(f"key escapes the prompt library: {key!r}")
    if not candidate.is_file():
        raise ValueError(
            f"no workflow prompt {key!r} to edit; adding a new node is a code change"
        )
    candidate.write_text(body, encoding="utf-8")


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
    # Workflow nodes are file-based: serve the on-disk file directly, never the store.
    if not _is_workflow(key):
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
    """Publish a prompt edit. A WORKFLOW node (key under ``workflow/``) is file-based: this
    WRITES the ``.md``/``.json`` in place (git is the version history), returning it as
    ``version`` 0 / ``created_by`` ``"disk"``. Any other key (author voice, house style) is
    stored VERSIONED: it inserts a new immutable version and (when ``activate``, the default)
    makes it live, so the prior version stays auditable and restorable via promote. 422
    ``invalid_prompt`` on a blank key or an unknown workflow file. Returns 201."""
    state = request.app.state
    if _is_workflow(body.key):
        if not body.body.strip():
            return _problem(422, "invalid_prompt", detail="a workflow node body cannot be blank")
        try:
            _write_shipped_prompt(state.settings.prompts_dir, body.key, body.body)
        except ValueError as exc:
            return _problem(422, "invalid_prompt", detail=str(exc))
        return PromptOut(
            key=body.key, version=0, body=body.body, created_by="disk", created_at="", is_active=True
        )
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
