"""Backend liveness/auth probe (the transport behind the ``status`` CLI verb).

A single non-raising GET against the backend read API, mapped to a typed verdict an
operator (or a health check) branches on: is the backend reachable, and is the operator
token accepted? No content is mirrored anywhere; the backend owns all data, this only
reports whether the CLI is wired to it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

__all__ = ["PROBE_TIMEOUT", "BackendProbe", "probe_backend"]

# A fast liveness check with a short, bounded timeout so an unresponsive backend never
# hangs the caller. Override with NEWSROOM_STATUS_PROBE_TIMEOUT.
PROBE_TIMEOUT = float(os.getenv("NEWSROOM_STATUS_PROBE_TIMEOUT", "5"))


@dataclass(frozen=True)
class BackendProbe:
    """The result of a live reachability/auth probe against the backend read API.

    Two independent axes an operator (or a health check) branches on to answer "is the
    CLI wired to the backend correctly?":

      * ``reachable`` -- an HTTP response came back at all (the host answered). False
        only on a transport fault (DNS, refused connection, timeout).
      * ``authorized`` -- the operator key was ACCEPTED: True on a 2xx, False on a
        401/403. A non-auth non-2xx (e.g. a 500) leaves ``authorized`` True (the token
        was not rejected) and carries the reason in ``status`` / ``detail``.

    ``author_count`` is the remote LIVE-author count on a 2xx (the small useful signal:
    a non-blank handle, not tombstoned), else None. ``status`` is the HTTP status (0 on a
    transport fault) and ``detail`` is a short human note (the transport error string, or
    the backend's problem ``code``/``detail``)."""

    reachable: bool
    authorized: bool
    status: int
    author_count: int | None = None
    detail: str = ""


def probe_backend(
    base_url: str, token: str, *, timeout: float = PROBE_TIMEOUT
) -> BackendProbe:
    """A NON-RAISING liveness/auth probe of the backend author read API (GET /authors).

    It never raises out, and the bounded ``timeout`` keeps an unresponsive backend from
    hanging the caller.

      * transport fault -> ``reachable=False`` (``status=0``, ``detail`` = the error);
      * 401/403 -> ``reachable=True, authorized=False`` (the token was rejected);
      * any other non-2xx -> ``reachable=True, authorized=True`` with the status/detail;
      * 2xx -> ``reachable=True, authorized=True`` plus the live ``author_count``."""
    try:
        resp = httpx.get(
            f"{base_url.rstrip('/')}/authors",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        return BackendProbe(reachable=False, authorized=False, status=0, detail=str(exc))

    status = resp.status_code
    try:
        body = resp.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}

    if status // 100 == 2:
        rows = body.get("authors", [])
        count = 0
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                handle = str(row.get("handle", "") or "").strip()
                if handle and not row.get("deleted"):
                    count += 1
        return BackendProbe(reachable=True, authorized=True, status=status, author_count=count)

    code = str(body.get("code", ""))
    detail = str(body.get("detail", ""))
    return BackendProbe(
        reachable=True,
        authorized=status not in (401, 403),
        status=status,
        detail=code or detail or f"http_{status}",
    )
