"""Read client for the platform author registry (the mirror's inbound seam).

The platform (censurado-web) OWNS author existence and the public profile fields
(name, bio, avatar). The brain MIRRORS that list and keeps only the private
identity prompt. This is the inverse of ``newsroom.publish.client``: a single GET
against the platform read API, mapped to a small typed record. Any VALID bearer
token reads (the read API checks the token, not a scope), so the operator key is
reused here.

This layer only TRANSPORTS the public author list. The reconcile decision (create
shell, refresh public fields, soft-deactivate, never touch the prompt) lives in
``newsroom.mirror.reconcile`` with no network, so the policy is tested in isolation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

__all__ = [
    "WebAuthor",
    "fetch_web_authors",
    "PushResult",
    "push_web_author",
    "DEFAULT_TIMEOUT",
]

DEFAULT_TIMEOUT = float(os.getenv("NEWSROOM_MIRROR_TIMEOUT", "15"))


@dataclass(frozen=True)
class WebAuthor:
    """One author as the platform owns it: the join handle (== persona id == the
    article author field) plus the three public display fields the brain mirrors.
    The private prompt is deliberately NOT here; it never leaves the brain."""

    handle: str
    name: str = ""
    bio: str = ""
    avatar: str = ""


def fetch_web_authors(
    base_url: str, token: str, *, timeout: float = DEFAULT_TIMEOUT
) -> list[WebAuthor]:
    """GET the platform's live author registry and map it to typed records.

    Returns only LIVE authors: the endpoint excludes tombstoned ones by default and
    any ``deleted`` row is dropped defensively too, so a handle MISSING from the
    result is genuinely gone from the platform (the signal the reconcile soft-
    deactivates on). Raises ``httpx.HTTPError`` on a transport fault or a non-2xx
    response, which the caller (bootstrap) catches to SKIP the reconcile rather than
    fail the boot when the platform is unreachable."""
    resp = httpx.get(
        f"{base_url.rstrip('/')}/authors",
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
    )
    resp.raise_for_status()
    body = resp.json()
    rows = body.get("authors", []) if isinstance(body, dict) else []
    out: list[WebAuthor] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        handle = str(row.get("handle", "") or "").strip()
        if not handle or row.get("deleted"):
            continue
        out.append(
            WebAuthor(
                handle=handle,
                name=str(row.get("name", "") or ""),
                bio=str(row.get("bio", "") or ""),
                avatar=str(row.get("avatar", "") or ""),
            )
        )
    return out


@dataclass(frozen=True)
class PushResult:
    """The typed outcome of one author upsert against the platform. ``ok`` is True on
    a 200; otherwise ``code``/``detail`` carry the platform's problem (or
    ``transport_error`` when the POST never reached it)."""

    handle: str
    ok: bool
    status: int
    code: str = ""
    detail: str = ""


def push_web_author(
    base_url: str,
    token: str,
    *,
    handle: str,
    name: str = "",
    bio: str = "",
    avatar: str = "",
    timeout: float = DEFAULT_TIMEOUT,
) -> PushResult:
    """POST one author's PUBLIC fields to the platform operator registry (POST /authors,
    keyed on handle), the one-time backfill that makes the platform authoritative. The
    operator key must carry the ``admin:write`` scope. The upsert is idempotent, so a
    re-run is a no-op. A transport fault returns a typed failure rather than raising, so
    the backfill loop reports per-handle and never aborts mid-way."""
    try:
        resp = httpx.post(
            f"{base_url.rstrip('/')}/authors",
            headers={"Authorization": f"Bearer {token}"},
            json={"handle": handle, "name": name, "bio": bio, "avatar": avatar},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        return PushResult(handle=handle, ok=False, status=0, code="transport_error", detail=str(exc))
    if resp.status_code == 200:
        return PushResult(handle=handle, ok=True, status=200)
    body: object = {}
    try:
        body = resp.json()
    except ValueError:
        body = {}
    code = str(body.get("code", "")) if isinstance(body, dict) else ""
    detail = str(body.get("detail", "")) if isinstance(body, dict) else ""
    return PushResult(handle=handle, ok=False, status=resp.status_code, code=code, detail=detail)
