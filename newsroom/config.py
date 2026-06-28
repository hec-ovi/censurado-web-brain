"""Harness configuration.

One settings object, environment-driven (prefix ``NEWSROOM_``), read once at
startup. There is deliberately NO output-length setting of any kind here: the
harness never caps how many tokens a single generation may emit (no
``max_tokens``, no ``max_words``, no "in N words"). What IS bounded lives below as
LOOP bounds, the number of times a loop may run and the shared resource budget a
run may spend, which is orthogonal to a single generation's length. Exhausting a
budget DROPS an assignment; it never truncates a body (see the architecture doc,
A.8).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEWSROOM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # HTTP surface (the brain's own API, consumed by the frontend and the trigger).
    host: str = "127.0.0.1"
    port: int = 8722

    # Browser CORS allow-list for the brain API, so a browser admin/mobile-web client
    # (served from another origin) clears the preflight and can call the brain. Driven by
    # ``NEWSROOM_CORS_ORIGINS`` as a comma-separated list (or ``*`` for any origin); the
    # default is the local dev origins a Vite/Next admin UI runs on. ``create_app`` reads
    # this and, when the list is exactly ``*``, drops credentialed CORS (the spec forbids
    # ``*`` with credentials), since the brain authenticates by header, not cookie.
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:8722",
            "http://127.0.0.1:8722",
        ]
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        """Accept ``NEWSROOM_CORS_ORIGINS`` as a comma-separated string (the natural env
        form) as well as a JSON/list. A bare ``*`` means any origin; a blank value clears
        the list. A real list (e.g. a test passing ``cors_origins=[...]``) is returned
        unchanged."""
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                return []
            return [part.strip() for part in stripped.split(",") if part.strip()]
        return value

    # Brain-owned stores and prompt assets.
    persona_db_path: Path = _REPO_ROOT / "data" / "personas.db"
    prompts_dir: Path = _REPO_ROOT / "prompts"

    # Inference seam (Step 1). The default backend is the local llama.cpp/Vulkan
    # Gemma speaking the OpenAI Chat-Completions dialect.
    inference_base_url: str = "http://127.0.0.1:8080/v1"

    # Publish seam (Step 7). The platform requires BOTH scopes on the operator key.
    publish_base_url: str = "http://127.0.0.1:8080"
    operator_token: str = Field(default="", repr=False)
    # The operator EDIT key (admin:write scope), used by maintenance passes that mutate
    # existing articles in place (the topic cleanse remaps via PUT /articles, which the
    # append-only operator_token cannot reach). Falls back to operator_token when unset,
    # so a deployment that mints one key with all scopes needs no extra config.
    admin_token: str = Field(default="", repr=False)
    # Publish a run's ready articles together in ONE atomic request (POST /articles:batch)
    # instead of one POST per article. The batch is all-or-nothing: any invalid item
    # rejects the whole batch (nothing written), so it stays re-publishable on the next
    # run. Turn off to fall back to the per-article path (each publishes independently).
    publish_batch: bool = True

    # ----- Imagery seam (art-director image generation). -----
    # An art-director step pairs with the journalist: after an article is finalized it
    # writes a FLUX.2 illustration brief, renders it on the local ComfyUI box, uploads
    # the PNG to the platform's media endpoint, and stamps metadata.image on the article.
    # The whole step is best-effort: any failure degrades to no image, never drops the
    # article. There is NO output-length cap here either; image width/height/steps are
    # render parameters, not generation-length caps.
    auto_generate_image: bool = True  # default on; overridable per run (RunScope.generate_images)
    comfyui_base_url: str = "http://127.0.0.1:8188"  # the local ComfyUI server (FLUX.2 klein)
    image_workflow: str = "flux2_klein"  # named workflow template family under newsroom/imagery/templates
    image_width: int = 1024
    image_height: int = 1024
    image_steps: int = 4  # klein is a distilled few-step model
    reference_image_limit: int = 2  # max source reference images fed to the FLUX.2 reference workflow
    # Where generated images are uploaded (POST /media). Defaults to publish_base_url
    # (same host as the article publish seam) when left blank.
    media_base_url: str = ""

    # ----- LOOP bounds (NOT output caps): iteration counts and a shared budget. -----
    n_max: int = 4  # manager fan-out clamp: len(assignments) <= n_max
    express_n: int = 2  # express mode's default small batch (still clamped to n_max)
    max_manager_steps: int = 8  # manager ReAct step cap
    manager_circuit_breaker: int = 3  # consecutive no-progress/failed manager steps -> stop
    fanout_concurrency: int = 1  # articles drafted at once (GPU KV-cache; 1-2 local)
    max_sweeps: int = 4  # per-article draft/evaluate sweeps
    max_research_steps: int = 6  # research loop step cap
    research_stall_limit: int = 2  # consecutive zero-new-row research steps -> abort
    per_article_token_budget: int = 200_000  # shared ceiling debited by every sub-loop
    per_article_wall_clock_s: int = 1_800  # shared wall-clock ceiling per article

    # ----- coverage memory (manager triage): fresh, non-repeating, continuous news. -----
    coverage_lookback: int = 50  # recent published articles the manager triages against
    coverage_duplicate_threshold: float = 0.6  # >= this overlap -> DUPLICATE (drop, never republish)
    coverage_followup_threshold: float = 0.3  # >= this (and < duplicate) -> FOLLOW_UP (extend + cite)


def load_settings() -> Settings:
    """Build a Settings instance from the environment / .env file."""
    return Settings()
