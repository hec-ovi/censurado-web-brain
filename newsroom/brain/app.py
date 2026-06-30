"""The brain's HTTP surface (FastAPI): the newsroom config plane.

A headless-drivable API over the persona store, the source (portal) registry, the
editorial config (house style + location), the prompt library, the platform-author
backfill + bootstrap, and a backend-connection status probe. There is no LLM here: the
operator's CLI agent does the writing; this surface only stores and serves the
newsroom's configuration.

One ``threading.Lock`` serializes the single shared SQLite connection across the request
handlers and the included routers; the connection is opened ``check_same_thread=False``
to allow that shared use.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import asdict
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from newsroom.brain.auth import require_auth
from newsroom.brain.problems import _problem
from newsroom.brain.routes import (
    admin_router,
    editorial_router,
    personas_router,
    portals_router,
    prompts_router,
    status_router,
)
from newsroom.brain.routes.personas import PersonaOut
from newsroom.config import Settings, load_settings
from newsroom.db import open_db
from newsroom.editorial import LocationStore, PortalStore, PromptStore, StyleStore
from newsroom.personas import PersonaStore

__all__ = ["create_app"]

try:  # the installed distribution version, surfaced in the OpenAPI/Swagger metadata
    API_VERSION = _pkg_version("censurado-web-brain")
except PackageNotFoundError:  # running from a source tree without an installed dist
    API_VERSION = "0.0.0"

API_DESCRIPTION = (
    "The newsroom config plane: a headless-drivable HTTP surface over persona, source, and "
    "editorial-config CRUD, the prompt library, the platform-author backfill, newsroom "
    "bootstrap, and the backend-connection status probe."
)


class HealthOut(BaseModel):
    """The GET /health liveness body: ``ok`` is always True when the process answers."""

    ok: bool


class PersonaListOut(BaseModel):
    """The GET /personas response: the ``limit``/``offset`` window plus ``total``, the
    count BEFORE pagination -- matching the source/version listings so a client pages the
    persona collection the same way."""

    personas: list[PersonaOut]
    total: int


async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Convert FastAPI/pydantic body & query validation failures into the brain's shared
    ``application/problem+json`` envelope, so a malformed request reads the SAME
    ``{status, code, detail}`` shape (with ``code == "validation_failed"``) every
    hand-mapped error uses, instead of FastAPI's default ``{"detail": [...]}``. Registered
    app-wide, so it covers every route. The ``detail`` is a concise human summary that joins
    each failure's ``loc`` and ``msg``."""
    parts: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
        msg = err.get("msg", "invalid")
        parts.append(f"{loc}: {msg}" if loc else msg)
    detail = "; ".join(parts) or "request validation failed"
    return _problem(422, "validation_failed", detail=detail)


def create_app(
    settings: Settings | None = None,
    store: PersonaStore | None = None,
    portal_store: PortalStore | None = None,
    style_store: StyleStore | None = None,
    location_store: LocationStore | None = None,
    prompt_store: PromptStore | None = None,
    auth_dependency: Callable | None = None,
) -> FastAPI:
    """Build the brain app. A test passes a ``settings`` pointed at a temp DB, or injects
    pre-built stores; production lets everything default from the environment. The stores
    share ONE SQLite connection and ONE lock, so every write serializes on it.

    ``auth_dependency`` overrides the app-wide auth seam: by default the no-op
    ``newsroom.brain.auth.require_auth`` is wired onto ``FastAPI(dependencies=[...])``,
    which runs ahead of EVERY route (the inline persona reads AND every included router) --
    so turning auth on later is a one-place change. A test passes a dependency that raises
    to prove the seam guards the whole surface at once."""
    settings = settings or load_settings()

    # The single auth seam, wired app-wide: it covers the inline routes and every router
    # below in one place. Default is the no-op; pass ``auth_dependency`` to override.
    auth = auth_dependency or require_auth
    app = FastAPI(
        title="censurado-web-brain",
        description=API_DESCRIPTION,
        version=API_VERSION,
        dependencies=[Depends(auth)],
    )

    # Browser CORS: a browser admin/mobile-web client served from another origin must
    # clear the preflight to call the brain. Origins are env-driven
    # (``NEWSROOM_CORS_ORIGINS``); a bare ``*`` allows any origin but drops credentialed
    # CORS (the spec forbids ``*`` with credentials, and the brain authenticates by
    # header, not cookie).
    origins = list(settings.cors_origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials="*" not in origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Every body/query validation failure becomes the shared problem+json envelope
    # (code == "validation_failed"), so clients branch on one error shape across the
    # whole API rather than special-casing FastAPI's default {"detail": [...]}.
    app.add_exception_handler(RequestValidationError, _validation_error_handler)

    conn = None
    if store is None:
        conn = open_db(settings.persona_db_path, check_same_thread=False)
        store = PersonaStore(conn)

    # One lock across every surface over the single shared connection, so a console edit
    # and any concurrent request serialize on the same lock.
    lock = threading.Lock()

    # The source-management API shares the one connection the persona surface uses. When a
    # test injects a pre-built persona store (conn is None here), it injects portal_store
    # alongside it if the source routes are exercised; otherwise it stays unset.
    if portal_store is None and conn is not None:
        portal_store = PortalStore(conn)

    # The editorial-config API (house style + location) shares the same one connection.
    # Built from the shared conn when not injected; a test that injects a pre-built persona
    # store (conn is None) injects these alongside it when the editorial routes are exercised.
    if style_store is None and conn is not None:
        style_store = StyleStore(conn)
    if location_store is None and conn is not None:
        location_store = LocationStore(conn)

    # The prompt-library API (the versioned journalist/manager/etc. prompt templates) shares
    # the same one connection. Built from the shared conn when not injected; a test that
    # injects a pre-built persona store (conn is None) injects it alongside when the prompt
    # routes are exercised.
    if prompt_store is None and conn is not None:
        prompt_store = PromptStore(conn)

    # The full settings ride on app.state so the status router can read the backend base
    # URL + operator token (the env-driven publish/mirror/read seam config) to probe the
    # backend connection. The other routers read their own stores off state.
    app.state.settings = settings
    app.state.store = store
    app.state.portal_store = portal_store
    app.state.style_store = style_store
    app.state.location_store = location_store
    app.state.prompt_store = prompt_store
    app.state.lock = lock

    @app.get("/health", status_code=200, response_model=HealthOut, tags=["system"])
    async def health() -> dict:
        return {"ok": True}

    @app.get("/personas/{persona_id}", status_code=200, response_model=PersonaOut, tags=["personas"])
    async def get_persona(persona_id: str, request: Request):
        state = request.app.state
        with state.lock:
            persona = state.store.get(persona_id)
        if persona is None:
            return _problem(404, "persona_not_found")
        return asdict(persona)

    @app.get("/personas", status_code=200, response_model=PersonaListOut, tags=["personas"])
    async def list_personas(
        request: Request, beat: str | None = None, limit: int = 100, offset: int = 0
    ):
        state = request.app.state
        with state.lock:
            # The registry view shows every persona, active or soft-deactivated, so an
            # operator can see a mirror tombstone and its ``active`` flag. The run path
            # is the one that filters to active (see runner._select_personas).
            items = state.store.list(beat=beat, include_inactive=True)
        total = len(items)
        limit = max(0, limit)
        offset = max(0, offset)
        # ``total`` is the count BEFORE the window (so a client can page), matching the
        # source/version listings; ``personas`` is the limit/offset slice.
        window = items[offset : offset + limit]
        return {"personas": [asdict(p) for p in window], "total": total}

    # The source-management API (portal CRUD + per-author source links).
    app.include_router(portals_router)

    # The author (persona) MANAGEMENT API: direct-create (persist), update, delete, and the
    # per-author source links. Direct-create sits at POST /personas/direct.
    app.include_router(personas_router)

    # The editorial-config MANAGEMENT API: the house style guide (versioned, with its
    # lexicon + sourcing sub-resources) and the publication location, under /editorial.
    app.include_router(editorial_router)

    # The prompt-library MANAGEMENT API: the versioned journalist/manager/etc. prompt
    # templates, under /prompts. Keys carry a slash, so the key rides as a query/body param.
    app.include_router(prompts_router)

    # The backend-connection STATUS API: GET /status/backend reports the configured backend
    # base URL + whether the operator token is set, plus a live, bounded probe of the backend
    # read API. Degrades gracefully (a down backend is a 200 verdict).
    app.include_router(status_router)

    # The MANAGEMENT API: POST /mirror/authors (the brain->backend author backfill) and
    # POST /bootstrap (seed a fresh box + mirror authors) -- the two lifecycle actions that
    # were CLI-only, now headless-drivable over HTTP like the rest of the infra.
    app.include_router(admin_router)

    return app
