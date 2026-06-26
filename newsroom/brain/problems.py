"""The brain's shared ``application/problem+json`` error helper (RFC 7807-shaped).

This lives in its own neutral module, imported by BOTH ``newsroom.brain.app`` (the
inline routes) and the routers under ``newsroom.brain.routes`` (the source-management
API and the chunks that follow). Keeping it here breaks the import cycle a router
would otherwise create: a router needs the helper, but ``app`` imports the router to
mount it, so the helper cannot live in ``app`` without ``router -> app -> router``.

Every brain error body is the SAME tiny shape (``status`` + machine ``code`` +
optional human ``detail``) so a client can branch on ``code`` without parsing prose,
and so a future central error/auth layer has one place to evolve.
"""

from __future__ import annotations

from fastapi.responses import JSONResponse

__all__ = ["_problem"]


def _problem(status: int, code: str, detail: str | None = None) -> JSONResponse:
    body: dict = {"status": status, "code": code}
    if detail:
        body["detail"] = detail
    return JSONResponse(status_code=status, content=body, media_type="application/problem+json")
