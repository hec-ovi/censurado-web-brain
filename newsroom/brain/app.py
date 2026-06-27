"""The brain's HTTP surface (FastAPI). Step 3 adds asynchronous persona synthesis.

``POST /personas`` accepts a seed brief, validates the beat against the harness
section enum, and returns ``202 Accepted`` IMMEDIATELY with a job id and a
``Location`` to poll. The actual model call runs OFF the request (a background
task in a worker thread), so the route never blocks on the model. The client polls
``GET /personas/jobs/{job_id}`` until the job is ``done`` (or ``failed``), then
reads the finished draft at ``GET /personas/{persona_id}``.

One ``threading.Lock`` serializes the single shared SQLite connection: the request
handlers read through it on the event loop, the background synthesis writes through
it from a worker thread. The connection is opened ``check_same_thread=False`` to
allow that shared use; the lock keeps the access one-at-a-time.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from fastapi import BackgroundTasks, Depends, FastAPI, Request
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
    runs_router,
    status_router,
)
from newsroom.brain.routes.personas import PersonaOut
from newsroom.brain.synthesis import PersonaSeed, synthesize_persona
from newsroom.config import Settings, load_settings
from newsroom.contracts.sections import SECTION_ENUM, is_valid_section
from newsroom.db import open_db
from newsroom.editorial import LocationStore, PortalStore, PromptStore, StyleStore
from newsroom.inference.provider import DEFAULT_MODEL, DEFAULT_PROVIDER, DIALECTS, ProviderConfig
from newsroom.personas import PersonaStore, slugify
from newsroom.runner import (
    RUN_MODES,
    RunDeps,
    RunScope,
    build_run_deps,
    execute_direct,
    execute_run,
    start_direct,
    start_run,
)
from newsroom.runs import Run

__all__ = ["create_app", "Job"]

try:  # the installed distribution version, surfaced in the OpenAPI/Swagger metadata
    API_VERSION = _pkg_version("censurado-web-brain")
except PackageNotFoundError:  # running from a source tree without an installed dist
    API_VERSION = "0.0.0"

API_DESCRIPTION = (
    "The agentic newsroom brain: a headless-drivable HTTP surface over source/author/"
    "editorial config CRUD, the run lifecycle (managed/express/manual batches and the "
    "direct-from-brief path), asynchronous persona synthesis, the platform-author backfill, "
    "newsroom bootstrap, and the backend-connection status probe."
)


class PersonaSeedIn(BaseModel):
    """The POST /personas request body."""

    display_name: str
    beat: str
    seed: str
    sources: list[str] = []


class RunRequest(BaseModel):
    """The POST /runs request body. ``mode`` is the entire trigger surface; ``n`` and
    ``persona_ids`` are optional operator overrides (honored mainly by ``manual``).
    ``images`` overrides the art-director image step for this run (True/False), or is
    omitted to use the server default (``settings.auto_generate_image``)."""

    mode: str
    n: int | None = None
    persona_ids: list[str] | None = None
    images: bool | None = None


class DirectBriefRequest(BaseModel):
    """The POST /articles/from-link request body (run mode 3): one persona writes one
    article from a BRIEF. ``persona_id`` is required; the brief is a free-text instruction
    (``brief``), 0..N links the operator vouched for (``links``), and an optional ``focus``
    that narrows the angle. The agent reads the links and researches OUTWARD from the
    brief; at least one of ``brief`` / ``links`` must be non-empty (there has to be
    something to write about). ``url`` is a legacy single-link alias folded into ``links``
    so older callers keep working."""

    persona_id: str
    brief: str = ""
    links: list[str] = []
    focus: str | None = None
    url: str | None = None
    images: bool | None = None


class HealthOut(BaseModel):
    """The GET /health liveness body: ``ok`` is always True when the process answers."""

    ok: bool


class JobAcceptedOut(BaseModel):
    """The POST /personas (synthesis) 202 body: the ``job_id`` to poll, the ``persona_id``
    the finished draft will land under, and the initial ``status`` (``pending``)."""

    job_id: str
    persona_id: str
    status: str


class JobStatusOut(BaseModel):
    """The GET /personas/jobs/{job_id} body: the synthesis job's state. ``status`` is
    ``pending`` | ``done`` | ``failed``; ``persona_id`` is the (eventual) draft id;
    ``error`` carries the failure reason when ``status == "failed"``, else empty."""

    job_id: str
    status: str
    persona_id: str
    error: str


class PersonaListOut(BaseModel):
    """The GET /personas response: the ``limit``/``offset`` window plus ``total``, the
    count BEFORE pagination -- matching the source/run/version listings so a client pages
    the persona collection the same way."""

    personas: list[PersonaOut]
    total: int


class RunAcceptedOut(BaseModel):
    """The POST /runs and POST /articles/from-link 202 body: the ``run_id`` to poll, the
    resolved ``mode``, and the initial ``status``."""

    run_id: str
    mode: str
    status: str


class AssignmentOut(BaseModel):
    """One assignment inside a run detail: the editorial slot plus its terminal status
    (``published_id`` when it published, ``drop_reason`` when it was dropped,
    ``image_url`` when a hero image was attached)."""

    id: str
    persona_id: str
    section: str
    status: str
    published_id: str | None = None
    drop_reason: str | None = None
    image_url: str | None = None


class RunDetailOut(BaseModel):
    """The GET /runs/{run_id} body: one run record plus its per-assignment statuses (the
    drill-in the compact ``GET /runs`` listing points at). Identity is ``run_id`` by the
    run/job convention (see ``routes.runs.RunOut``)."""

    run_id: str
    mode: str
    status: str
    n_requested: int | None
    created_at: str
    finished_at: str | None
    assignments: list[AssignmentOut]


@dataclass
class Job:
    """An in-memory synthesis job. Brain is single-process, so jobs live in RAM;
    the resulting persona is what persists, in the store."""

    status: str  # pending | done | failed
    persona_id: str = ""
    error: str = ""


async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Convert FastAPI/pydantic body & query validation failures into the brain's shared
    ``application/problem+json`` envelope, so a malformed request reads the SAME
    ``{status, code, detail}`` shape (with ``code == "validation_failed"``) every
    hand-mapped error uses, instead of FastAPI's default ``{"detail": [...]}``. Registered
    app-wide, so it covers every route (personas, runs, portals, and the routers to come).
    The ``detail`` is a concise human summary that joins each failure's ``loc`` and ``msg``."""
    parts: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
        msg = err.get("msg", "invalid")
        parts.append(f"{loc}: {msg}" if loc else msg)
    detail = "; ".join(parts) or "request validation failed"
    return _problem(422, "validation_failed", detail=detail)


def _run_synthesis(state, job_id: str, seed: PersonaSeed) -> None:
    """Background worker: synthesize, then record the job's terminal state. Runs in
    a worker thread (a plain ``def`` task), so the blocking model call never touches
    the event loop."""
    derived = slugify(seed.display_name)
    try:
        # Resolve the operator's active prompt edits under the shared lock (the store holds
        # none), so an edited persona/synthesize.md is what this synthesis uses.
        overrides = None
        prompt_store = getattr(state, "prompt_store", None)
        if prompt_store is not None:
            with state.lock:
                overrides = prompt_store.active_overrides()
        persona_id = synthesize_persona(
            seed, cfg=state.cfg, store=state.store, prompts_dir=state.prompts_dir,
            lock=state.lock, overrides=overrides,
        )
        result = Job(status="done", persona_id=persona_id)
    except Exception as exc:  # synthesis failures become a failed job, never a crash
        result = Job(status="failed", persona_id=derived, error=str(exc))
    with state.lock:
        state.jobs[job_id] = result


def _run_in_background(deps: RunDeps, run: Run, scope: RunScope) -> None:
    """Background worker: execute a run that the request already created. Runs in a
    worker thread (a plain ``def`` task), so the blocking pipeline (and the finalize
    seam's ``run_sync``) never touches the event loop. ``execute_run`` already marks
    the run ``failed`` on any error, so a failure here is recorded, not lost."""
    try:
        execute_run(run=run, scope=scope, deps=deps)
    except Exception:
        pass  # the run record was marked failed inside execute_run; nothing to add


def _run_direct_in_background(
    deps: RunDeps, run: Run, *, links: list[str], persona_id: str, brief: str | None,
    focus: str | None, images: bool | None,
) -> None:
    """Background worker for the direct-from-brief run, mirroring ``_run_in_background``.
    ``execute_direct`` records the run ``failed`` on any error, so a fault here is
    persisted (pollable over GET /runs/{id}), not lost."""
    try:
        execute_direct(
            run=run, links=links, persona_id=persona_id, deps=deps, brief=brief, focus=focus,
            images=images,
        )
    except Exception:
        pass


def create_app(
    settings: Settings | None = None,
    store: PersonaStore | None = None,
    run_deps: RunDeps | None = None,
    portal_store: PortalStore | None = None,
    style_store: StyleStore | None = None,
    location_store: LocationStore | None = None,
    prompt_store: PromptStore | None = None,
    auth_dependency: Callable | None = None,
) -> FastAPI:
    """Build the brain app. A test passes a ``settings`` pointed at the fake; for runs
    it also injects ``run_deps`` (with in-process search/research doubles) so a run
    never leaves the box. Production lets everything default from the environment.

    The persona store and the run deps share ONE SQLite connection and ONE lock, so
    synthesis writes and run writes serialize on the same connection.

    ``auth_dependency`` overrides the app-wide auth seam: by default the no-op
    ``newsroom.brain.auth.require_auth`` is wired onto ``FastAPI(dependencies=[...])``,
    which runs ahead of EVERY route (the inline lifecycle routes AND every included
    router) -- so turning auth on later is a one-place change. A test passes a dependency
    that raises to prove the seam guards the whole surface at once."""
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

    lock = threading.Lock()
    if run_deps is None:
        if conn is None:
            raise ValueError("inject run_deps alongside a pre-built store")
        run_deps = build_run_deps(settings, conn=conn, lock=lock, persona_store=store)
    # One lock across both surfaces over the single shared connection. Back-fill the
    # deps' lock when it is unset (an injected RunDeps may default lock=None) so the
    # synthesis path, the run path, and the GET /runs poll all serialize on the SAME
    # lock -- never a split where one side degrades to nullcontext().
    if run_deps.lock is None:
        run_deps.lock = lock
    lock = run_deps.lock

    # The source-management API shares the one connection the persona/run surfaces use,
    # so a console source edit and a running pipeline serialize on the same lock. When a
    # test injects a pre-built persona store (conn is None here), it injects portal_store
    # alongside it if the source routes are exercised; otherwise it stays unset.
    if portal_store is None and conn is not None:
        portal_store = PortalStore(conn)

    # The editorial-config API (house style + location) shares the same one connection,
    # so a console edit and a running pipeline serialize on the same lock. Built from the
    # shared conn when not injected; a test that injects a pre-built persona store (conn
    # is None) injects these alongside it when the editorial routes are exercised.
    if style_store is None and conn is not None:
        style_store = StyleStore(conn)
    if location_store is None and conn is not None:
        location_store = LocationStore(conn)

    # The prompt-library API (the versioned journalist/manager/etc. prompt templates) shares
    # the same one connection, so a console prompt edit and a running pipeline serialize on
    # the same lock. Built from the shared conn when not injected; a test that injects a
    # pre-built persona store (conn is None) injects it alongside when the prompt routes are
    # exercised.
    if prompt_store is None and conn is not None:
        prompt_store = PromptStore(conn)

    caps = DIALECTS[DEFAULT_PROVIDER]
    # The full settings ride on app.state so the status router can read the backend base
    # URL + operator token (the env-driven publish/mirror/read seam config) to probe the
    # backend connection. The other routers read their own stores off state; this is the
    # one that needs the connection config rather than a store.
    app.state.settings = settings
    app.state.store = store
    app.state.portal_store = portal_store
    app.state.style_store = style_store
    app.state.location_store = location_store
    app.state.prompt_store = prompt_store
    app.state.run_deps = run_deps
    app.state.jobs = {}
    app.state.lock = lock
    app.state.prompts_dir = settings.prompts_dir
    app.state.cfg = ProviderConfig(
        role="persona_synth",
        provider=DEFAULT_PROVIDER,
        base_url=str(settings.inference_base_url).rstrip("/"),
        model=DEFAULT_MODEL,
        **caps,
    )

    @app.get("/health", status_code=200, response_model=HealthOut, tags=["system"])
    async def health() -> dict:
        return {"ok": True}

    @app.post("/personas", status_code=202, response_model=JobAcceptedOut, tags=["personas"])
    async def create_persona(body: PersonaSeedIn, background_tasks: BackgroundTasks, request: Request):
        if not is_valid_section(body.beat):
            return _problem(422, "invalid_beat", detail=f"beat must be one of {SECTION_ENUM}")
        if not body.display_name.strip() or not body.seed.strip():
            return _problem(422, "validation_failed", detail="display_name and seed are required")
        persona_id = slugify(body.display_name)
        if not persona_id:
            return _problem(422, "validation_failed", detail="display_name yields no usable id")

        job_id = uuid.uuid4().hex
        state = request.app.state
        with state.lock:
            state.jobs[job_id] = Job(status="pending", persona_id=persona_id)
        seed = PersonaSeed(
            display_name=body.display_name, beat=body.beat, seed=body.seed, sources=body.sources
        )
        # Runs AFTER this response is sent; the route does not wait for the model.
        background_tasks.add_task(_run_synthesis, state, job_id, seed)
        return JSONResponse(
            status_code=202,
            content={"job_id": job_id, "persona_id": persona_id, "status": "pending"},
            headers={"Location": f"/personas/jobs/{job_id}"},
        )

    @app.get("/personas/jobs/{job_id}", status_code=200, response_model=JobStatusOut, tags=["personas"])
    async def get_job(job_id: str, request: Request):
        state = request.app.state
        with state.lock:
            job = state.jobs.get(job_id)
        if job is None:
            return _problem(404, "job_not_found")
        return {
            "job_id": job_id,
            "status": job.status,
            "persona_id": job.persona_id,
            "error": job.error,
        }

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
        # source/run/version listings; ``personas`` is the limit/offset slice.
        window = items[offset : offset + limit]
        return {"personas": [asdict(p) for p in window], "total": total}

    @app.post("/runs", status_code=202, response_model=RunAcceptedOut, tags=["runs"])
    async def create_run(body: RunRequest, background_tasks: BackgroundTasks, request: Request):
        deps: RunDeps = request.app.state.run_deps
        if body.mode not in RUN_MODES:
            return _problem(422, "invalid_mode", detail=f"mode must be one of {RUN_MODES}")
        # Create the run record now (so we can return its id immediately) and resolve
        # scope, then execute OFF the request. The route never waits for the pipeline.
        run, scope = start_run(
            body.mode, deps=deps, n=body.n, persona_ids=body.persona_ids, images=body.images
        )
        background_tasks.add_task(_run_in_background, deps, run, scope)
        return JSONResponse(
            status_code=202,
            content={"run_id": run.id, "mode": run.mode, "status": run.status},
            headers={"Location": f"/runs/{run.id}"},
        )

    @app.post("/articles/from-link", status_code=202, response_model=RunAcceptedOut, tags=["runs"])
    async def create_from_link(
        body: DirectBriefRequest, background_tasks: BackgroundTasks, request: Request
    ):
        """Run mode 3: one persona writes one article from a BRIEF (a free-text
        instruction, 0..N vouched-for links, an optional focus), bypassing the manager.
        The agent reads the links and researches OUTWARD from the brief; the cross-source
        corroboration gate is OFF for this path (the operator vouched for the source).
        Validates the persona up front (404 if unknown) and requires something to write
        about (422 if both brief and links are empty), creates a ``direct`` run, executes
        OFF the request, and returns 202 + a poll Location, exactly like POST /runs."""
        deps: RunDeps = request.app.state.run_deps
        # Fold the legacy single ``url`` into the links list, drop blanks, de-dup.
        raw_links = [*body.links, body.url] if body.url else list(body.links)
        links: list[str] = []
        for raw in raw_links:
            cleaned = (raw or "").strip()
            if cleaned and cleaned not in links:
                links.append(cleaned)
        brief = (body.brief or "").strip()
        if not brief and not links:
            return _problem(
                422, "validation_failed", detail="provide a brief and/or at least one link"
            )
        with request.app.state.lock:
            persona = deps.persona_store.get(body.persona_id)
        if persona is None:
            return _problem(404, "persona_not_found", detail=f"no persona {body.persona_id!r}")
        run = start_direct(deps=deps)
        background_tasks.add_task(
            _run_direct_in_background, deps, run,
            links=links, persona_id=body.persona_id, brief=brief, focus=body.focus,
            images=body.images,
        )
        return JSONResponse(
            status_code=202,
            content={"run_id": run.id, "mode": run.mode, "status": run.status},
            headers={"Location": f"/runs/{run.id}"},
        )

    @app.get("/runs/{run_id}", status_code=200, response_model=RunDetailOut, tags=["runs"])
    async def get_run(run_id: str, request: Request):
        deps: RunDeps = request.app.state.run_deps
        with request.app.state.lock:
            run = deps.store.get_run(run_id)
            assignments = deps.store.list_assignments(run_id=run_id) if run is not None else []
        if run is None:
            return _problem(404, "run_not_found")
        return {
            "run_id": run.id,
            "mode": run.mode,
            "status": run.status,
            "n_requested": run.n_requested,
            "created_at": run.created_at,
            "finished_at": run.finished_at,
            "assignments": [
                {
                    "id": a.id,
                    "persona_id": a.persona_id,
                    "section": a.section,
                    "status": a.status,
                    "published_id": a.published_id,
                    "drop_reason": a.drop_reason,
                    "image_url": a.image_url,
                }
                for a in assignments
            ],
        }

    # The source-management API lives in its own router (added, not inlined) so the
    # seven tested-thin inline routes above stay untouched. A future chunk migrates
    # those onto routers too.
    app.include_router(portals_router)

    # The run LISTING API (GET /runs): a read-only collection view over the run records,
    # added as its own router beside the inline run lifecycle routes (POST /runs,
    # GET /runs/{id}, POST /articles/from-link), which stay untouched. FastAPI matches
    # GET /runs (router) and GET /runs/{id} (inline) by path, so the two coexist.
    app.include_router(runs_router)

    # The author (persona) MANAGEMENT API: the non-synthesis direct-create, update, and
    # delete routes. Added as its own router beside the inline synthesis/list/get routes,
    # which stay untouched; direct-create sits at POST /personas/direct so it never
    # collides with the synthesis POST /personas.
    app.include_router(personas_router)

    # The editorial-config MANAGEMENT API: the house style guide (versioned, with its
    # lexicon + sourcing sub-resources) and the publication location, under /editorial.
    # Added as its own router; it reads the StyleStore / LocationStore off app.state.
    app.include_router(editorial_router)

    # The prompt-library MANAGEMENT API: the versioned journalist/manager/etc. prompt
    # templates, under /prompts. Added as its own router; it reads the PromptStore off
    # app.state. Keys carry a slash, so the key rides as a query/body param, never the path.
    app.include_router(prompts_router)

    # The backend-connection STATUS API: GET /status/backend reports the configured
    # backend base URL + whether the operator token is set, plus a live, bounded probe of
    # the backend read API (reachable / authorized / remote author count). Reads
    # ``settings`` off app.state; degrades gracefully (a down backend is a 200 verdict).
    app.include_router(status_router)

    # The MANAGEMENT API: POST /mirror/authors (the brain->backend author backfill) and
    # POST /bootstrap (seed a fresh box, optionally run one batch) -- the two lifecycle
    # actions that were CLI-only, now headless-drivable over HTTP like the rest of the infra.
    app.include_router(admin_router)

    return app
