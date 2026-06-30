"""Harness configuration.

One settings object, environment-driven (prefix ``NEWSROOM_``), read once at
startup. It carries the brain's HTTP surface, its stores and prompt assets, the
publish seam credentials, and the ComfyUI image-render parameters. Image
width/height/steps are render parameters, not output-length caps.
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

    # Publish seam. The platform requires the operator key scope(s).
    publish_base_url: str = "http://127.0.0.1:8080"
    operator_token: str = Field(default="", repr=False)
    # The operator EDIT key (admin:write scope), used by maintenance passes that mutate
    # existing articles in place (the topic cleanse remaps via PUT /articles, which the
    # append-only operator_token cannot reach). Falls back to operator_token when unset,
    # so a deployment that mints one key with all scopes needs no extra config.
    admin_token: str = Field(default="", repr=False)

    # ----- Imagery seam (ComfyUI image generation). -----
    # The operator's CLI agent decides what to illustrate (the art direction); ComfyUI
    # renders it on the local box and the PNG is uploaded to the platform's media endpoint.
    # There is NO output-length cap here; image width/height/steps are render parameters,
    # not generation-length caps.
    comfyui_base_url: str = "http://127.0.0.1:8188"  # the local ComfyUI server (FLUX.2 klein)
    image_workflow: str = "flux2_klein"  # named workflow template family under newsroom/imagery/templates
    image_width: int = 1024
    image_height: int = 1024
    image_steps: int = 4  # klein is a distilled few-step model
    reference_image_limit: int = 2  # max source reference images fed to the FLUX.2 reference workflow
    # Where generated images are uploaded (POST /media). Defaults to publish_base_url
    # (same host as the article publish seam) when left blank.
    media_base_url: str = ""


def load_settings() -> Settings:
    """Build a Settings instance from the environment / .env file."""
    return Settings()
