"""The brain's future-auth seam: ONE no-op dependency the whole surface hangs off.

The brain API has no auth today (the ``operator_token`` is the brain->backend credential,
not a gate on the brain's own routes). But the surface must be auth-READY in a single
place rather than in each of the dozen-plus handlers. ``newsroom.brain.app.create_app``
wires this dependency onto ``FastAPI(dependencies=[...])``, so it runs ahead of EVERY
operation: the inline lifecycle routes (synthesis, runs, articles-from-link, health) AND
every included router (portals, personas, runs, editorial, status, admin), without
touching a single handler.

To turn auth ON later, change THIS function (read ``request`` for an Authorization
header, verify it, raise ``fastapi.HTTPException(401/403)`` on failure) -- or pass a real
dependency as ``create_app(auth_dependency=...)``. Either way it is a one-place change;
the wiring already covers the entire surface, so enabling auth never means editing the
routers one by one.
"""

from __future__ import annotations

from fastapi import Request

__all__ = ["require_auth"]


async def require_auth(request: Request) -> None:
    """No-op auth gate: allows every request today.

    This is the single seam real auth replaces. It takes the ``Request`` so a future
    implementation can inspect headers/cookies and raise on a bad credential without any
    route change. Returns ``None`` (no principal) for now."""
    return None
