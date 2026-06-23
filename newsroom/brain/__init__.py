"""The brain: the harness's own HTTP surface and its orchestration."""

from __future__ import annotations

from .app import create_app

__all__ = ["create_app"]
