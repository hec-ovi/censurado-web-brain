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
from dataclasses import asdict, dataclass

from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from newsroom.brain.problems import _problem
from newsroom.brain.routes import personas_router, portals_router
from newsroom.brain.synthesis import PersonaSeed, synthesize_persona
from newsroom.config import Settings, load_settings
from newsroom.contracts.sections import SECTION_ENUM, is_valid_section
from newsroom.db import open_db
from newsroom.editorial import PortalStore
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


class DirectLinkRequest(BaseModel):
    """The POST /articles/from-link request body (run mode 3). One persona writes one
    article seeded from ``url``; ``brief`` is the optional angle ("cover the budget
    angle"), defaulting to a generic "write about this source"."""

    url: str
    persona_id: str
    brief: str | None = None
    images: bool | None = None


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
        persona_id = synthesize_persona(
            seed, cfg=state.cfg, store=state.store, prompts_dir=state.prompts_dir, lock=state.lock
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
    deps: RunDeps, run: Run, *, url: str, persona_id: str, brief: str | None, images: bool | None
) -> None:
    """Background worker for the direct-from-link run, mirroring ``_run_in_background``.
    ``execute_direct`` records the run ``failed`` on any error, so a fault here is
    persisted (pollable over GET /runs/{id}), not lost."""
    try:
        execute_direct(
            run=run, url=url, persona_id=persona_id, deps=deps, brief=brief, images=images
        )
    except Exception:
        pass


def create_app(
    settings: Settings | None = None,
    store: PersonaStore | None = None,
    run_deps: RunDeps | None = None,
    portal_store: PortalStore | None = None,
) -> FastAPI:
    """Build the brain app. A test passes a ``settings`` pointed at the fake; for runs
    it also injects ``run_deps`` (with in-process search/research doubles) so a run
    never leaves the box. Production lets everything default from the environment.

    The persona store and the run deps share ONE SQLite connection and ONE lock, so
    synthesis writes and run writes serialize on the same connection."""
    settings = settings or load_settings()
    app = FastAPI(title="censurado-web-brain")

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

    caps = DIALECTS[DEFAULT_PROVIDER]
    app.state.store = store
    app.state.portal_store = portal_store
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

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @app.post("/personas")
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

    @app.get("/personas/jobs/{job_id}")
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

    @app.get("/personas/{persona_id}")
    async def get_persona(persona_id: str, request: Request):
        state = request.app.state
        with state.lock:
            persona = state.store.get(persona_id)
        if persona is None:
            return _problem(404, "persona_not_found")
        return asdict(persona)

    @app.get("/personas")
    async def list_personas(request: Request, beat: str | None = None):
        state = request.app.state
        with state.lock:
            # The registry view shows every persona, active or soft-deactivated, so an
            # operator can see a mirror tombstone and its ``active`` flag. The run path
            # is the one that filters to active (see runner._select_personas).
            items = state.store.list(beat=beat, include_inactive=True)
        return {"personas": [asdict(p) for p in items]}

    @app.post("/runs")
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

    @app.post("/articles/from-link")
    async def create_from_link(
        body: DirectLinkRequest, background_tasks: BackgroundTasks, request: Request
    ):
        """Run mode 3: one persona writes one article seeded from a URL, bypassing the
        manager. Validates the persona up front (404 if unknown), creates a ``direct``
        run, executes OFF the request, and returns 202 + a poll Location, exactly like
        POST /runs."""
        deps: RunDeps = request.app.state.run_deps
        if not body.url.strip():
            return _problem(422, "validation_failed", detail="url is required")
        with request.app.state.lock:
            persona = deps.persona_store.get(body.persona_id)
        if persona is None:
            return _problem(404, "persona_not_found", detail=f"no persona {body.persona_id!r}")
        run = start_direct(deps=deps)
        background_tasks.add_task(
            _run_direct_in_background, deps, run,
            url=body.url, persona_id=body.persona_id, brief=body.brief, images=body.images,
        )
        return JSONResponse(
            status_code=202,
            content={"run_id": run.id, "mode": run.mode, "status": run.status},
            headers={"Location": f"/runs/{run.id}"},
        )

    @app.get("/runs/{run_id}")
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

    # The author (persona) MANAGEMENT API: the non-synthesis direct-create, update, and
    # delete routes. Added as its own router beside the inline synthesis/list/get routes,
    # which stay untouched; direct-create sits at POST /personas/direct so it never
    # collides with the synthesis POST /personas.
    app.include_router(personas_router)

    return app
