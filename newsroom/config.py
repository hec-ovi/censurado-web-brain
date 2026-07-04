"""Newsroom CLI configuration.

One settings object, environment-driven (prefix ``NEWSROOM_``), read once. It carries only
the publish-seam credentials the maintenance sweeps need: where the backend is and the
operator key(s). The CLI holds no data and runs no server, so there is nothing else to
configure.
"""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="NEWSROOM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Publish seam: the platform backend the sweeps read + edit over HTTP. This is the write
    # API (localhost:8082 in the local stack), the SAME endpoint cli/censurado.py uses. The
    # operator token MUST hold the admin:write scope for the edit passes (topic cleanse and
    # embeds recheck edit articles in place via PUT /articles).
    publish_base_url: str = "http://127.0.0.1:8082"
    operator_token: str = Field(default="", repr=False)
    # The operator EDIT key (admin:write scope). Falls back to operator_token when unset, so a
    # deployment that mints one key with all scopes needs no extra config.
    admin_token: str = Field(default="", repr=False)


def load_settings() -> Settings:
    """Build a Settings instance from the environment / .env file."""
    return Settings()
