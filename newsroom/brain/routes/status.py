"""The backend-connection status API: an operator diagnostic for the one process
boundary the brain depends on (infra chunk 6).

The brain talks to the separate platform backend (``censurado-web-backend``) for three
things, all over the SAME env-driven base URL + operator token: PUBLISHING articles
(POST /articles), MIRRORING the author registry (GET /authors -> reconcile), and
reading. C1-C5 added management surfaces; this chunk makes that backend connection
OBSERVABLE so an operator (or the admin UI later) can answer "is the brain wired to the
backend correctly?" without reading logs or env files.

``GET /status/backend`` reports the configured backend base URL, whether the operator
token is set, and a LIVE probe of the backend read API (GET /authors): reachable
yes/no, authorized yes/no, and the remote live-author count as a small useful signal.
It DEGRADES gracefully -- a backend that is down or rejecting the token returns 200 with
``reachable``/``authorized`` flags, never a 500 -- and the probe runs with a bounded
timeout off the event loop, so an unresponsive backend cannot hang the route.

Like the other routers, this reads its config off ``request.app.state`` (the shared
``settings``), holds a ``response_model`` + ``status_code``, uses the shared problem
envelope for the one validation failure it can hit, and carries NO auth (the brain API
has none today; the env operator token is the backend credential, not a brain gate).
"""

from __future__ import annotations

import anyio
from fastapi import APIRouter, Request
from pydantic import BaseModel

from newsroom.brain.problems import _problem
from newsroom.mirror import probe_backend

__all__ = ["router"]

router = APIRouter()


class BackendStatusOut(BaseModel):
    """The brain<->backend connection diagnostic.

    ``backend_base_url`` is the configured platform base URL (the publish + mirror + read
    seams all target it); ``token_configured`` says whether an operator token is set at
    all (the credential the brain authenticates to the backend with). The remaining
    fields are the LIVE probe of the backend read API (GET /authors):

      * ``reachable`` -- the backend answered an HTTP request (False only on a transport
        fault: down host, refused connection, timeout).
      * ``authorized`` -- the operator token was accepted (False on a 401/403).
      * ``author_count`` -- the remote live-author count on a successful probe, else null.
      * ``status_code`` -- the probe's HTTP status (0 on a transport fault).
      * ``detail`` -- a short human note: the transport error, or the backend's problem
        code/detail, empty on a clean success.

    ``reachable`` AND ``authorized`` both True is the all-good wiring; the two flags plus
    ``status_code``/``detail`` let a caller tell "backend down" from "token rejected"."""

    backend_base_url: str
    token_configured: bool
    reachable: bool
    authorized: bool
    author_count: int | None = None
    status_code: int
    detail: str = ""


@router.get("/status/backend", status_code=200, response_model=BackendStatusOut)
async def backend_status(request: Request):
    """Report the brain's connection to the platform backend, including a LIVE probe.

    Reads the backend base URL + operator token from the shared settings, then probes
    the backend read API (GET /authors) with a bounded timeout, OFF the event loop so a
    slow backend never blocks other requests. The probe never raises: a backend that is
    down or rejecting the token degrades to ``reachable``/``authorized`` flags on a 200
    body, NOT a 500. Always 200 (the diagnostic itself succeeded); the body carries the
    verdict. 503 ``not_configured`` only when settings are missing entirely (a
    misassembled app), which is a server fault, not a backend-connectivity verdict."""
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        return _problem(503, "not_configured", detail="settings are not available on app.state")

    base_url = str(settings.publish_base_url).rstrip("/")
    probe = await anyio.to_thread.run_sync(
        lambda: probe_backend(base_url, settings.operator_token)
    )
    return BackendStatusOut(
        backend_base_url=base_url,
        token_configured=bool(settings.operator_token),
        reachable=probe.reachable,
        authorized=probe.authorized,
        author_count=probe.author_count,
        status_code=probe.status,
        detail=probe.detail,
    )
