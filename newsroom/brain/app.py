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
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from newsroom.brain.synthesis import PersonaSeed, synthesize_persona
from newsroom.config import Settings, load_settings
from newsroom.contracts.sections import SECTION_ENUM, is_valid_section
from newsroom.db import open_db
from newsroom.inference.provider import DEFAULT_MODEL, DEFAULT_PROVIDER, DIALECTS, ProviderConfig
from newsroom.personas import PersonaStore, slugify

__all__ = ["create_app", "Job"]


class PersonaSeedIn(BaseModel):
    """The POST /personas request body."""

    display_name: str
    beat: str
    seed: str
    sources: list[str] = []


@dataclass
class Job:
    """An in-memory synthesis job. Brain is single-process, so jobs live in RAM;
    the resulting persona is what persists, in the store."""

    status: str  # pending | done | failed
    persona_id: str = ""
    error: str = ""


def _problem(status: int, code: str, detail: str | None = None) -> JSONResponse:
    body: dict = {"status": status, "code": code}
    if detail:
        body["detail"] = detail
    return JSONResponse(status_code=status, content=body, media_type="application/problem+json")


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


def create_app(settings: Settings | None = None, store: PersonaStore | None = None) -> FastAPI:
    """Build the brain app. A test passes a ``settings`` pointed at the fake and an
    in-memory ``store``; production lets both default from the environment."""
    settings = settings or load_settings()
    app = FastAPI(title="censurado-web-brain")

    if store is None:
        store = PersonaStore(open_db(settings.persona_db_path, check_same_thread=False))

    caps = DIALECTS[DEFAULT_PROVIDER]
    app.state.store = store
    app.state.jobs = {}
    app.state.lock = threading.Lock()
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
            items = state.store.list(beat=beat)
        return {"personas": [asdict(p) for p in items]}

    return app
