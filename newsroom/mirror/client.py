"""Read client for the platform author registry (the mirror's inbound seam).

The platform (censurado-web) OWNS author existence and the public profile fields
(name, bio, avatar). The brain MIRRORS that list and keeps only the private
identity prompt. A single GET against the platform read API, mapped to a small
typed record. Any VALID bearer
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
    "push_web_source",
    "set_author_sources",
    "DEFAULT_TIMEOUT",
    "PROBE_TIMEOUT",
    "BackendProbe",
    "probe_backend",
]

DEFAULT_TIMEOUT = float(os.getenv("NEWSROOM_MIRROR_TIMEOUT", "15"))
# The status probe is a fast liveness check, NOT the (longer) mirror fetch: a separate,
# shorter default so an unresponsive backend never makes the status route block for the
# full mirror timeout. Override with NEWSROOM_STATUS_PROBE_TIMEOUT.
PROBE_TIMEOUT = float(os.getenv("NEWSROOM_STATUS_PROBE_TIMEOUT", "5"))


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
    """The typed outcome of one upsert against the platform. ``handle`` is the upserted
    key (an author handle, or a source slug for ``push_web_source``). ``ok`` is True on a
    200; otherwise ``code``/``detail`` carry the platform's problem (or ``transport_error``
    when the request never reached it)."""

    handle: str
    ok: bool
    status: int
    code: str = ""
    detail: str = ""


def _post_upsert(base_url: str, token: str, path: str, key: str, body: dict, timeout: float) -> PushResult:
    """POST a JSON body to an operator upsert endpoint and map the reply to a PushResult.
    A transport fault returns a typed failure rather than raising, so a migration loop
    reports per-row and never aborts mid-way."""
    try:
        resp = httpx.post(
            f"{base_url.rstrip('/')}{path}",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        return PushResult(handle=key, ok=False, status=0, code="transport_error", detail=str(exc))
    if resp.status_code == 200:
        return PushResult(handle=key, ok=True, status=200)
    parsed: object = {}
    try:
        parsed = resp.json()
    except ValueError:
        parsed = {}
    code = str(parsed.get("code", "")) if isinstance(parsed, dict) else ""
    detail = str(parsed.get("detail", "")) if isinstance(parsed, dict) else ""
    return PushResult(handle=key, ok=False, status=resp.status_code, code=code, detail=detail)


def push_web_author(
    base_url: str,
    token: str,
    *,
    handle: str,
    name: str = "",
    bio: str = "",
    avatar: str = "",
    gender: str = "",
    about: str = "",
    style: str = "",
    topics: list[str] | None = None,
    sources: list[str] | None = None,
    metadata: dict[str, object] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> PushResult:
    """Upsert one author to the platform registry (POST /authors, keyed on handle), the
    one-time move that makes the platform authoritative. The operator key must carry the
    ``admin:write`` scope. Idempotent (a re-run replaces the row), so re-running repairs
    without dupes.

    The body maps 1:1 to the platform's ``authorInput``: the six scalar columns
    (name/bio/avatar/gender/about/style) are ALWAYS sent, because the upsert replaces them
    wholesale, so a caller must send the COMPLETE row or blank a column. ``topics`` and
    ``metadata`` are sent only when non-empty; ``metadata`` (the private tail: beat,
    who_i_am, language, few_shots, the layer-1 profile_topics copy) REPLACES the stored
    blob wholesale, so it too must be complete. ``sources`` is a pointer-like field: None
    leaves the author_sources join untouched, an explicit list (even empty) replaces it, so
    the join is set in the same request the source rows already exist for."""
    body: dict[str, object] = {
        "handle": handle, "name": name, "bio": bio, "avatar": avatar,
        "gender": gender, "about": about, "style": style,
    }
    if topics:
        body["topics"] = list(topics)
    if sources is not None:
        body["sources"] = list(sources)
    if metadata:
        body["metadata"] = dict(metadata)
    return _post_upsert(base_url, token, "/authors", handle, body, timeout)


def push_web_source(
    base_url: str,
    token: str,
    *,
    domain: str,
    slug: str = "",
    homepage: str = "",
    description: str = "",
    feed_urls: list[str] | None = None,
    feed_type: str = "",
    language: str = "",
    ownership_group: str = "",
    lean: str = "",
    enabled: bool | None = None,
    status: str = "",
    last_checked: str = "",
    last_ok: str = "",
    metadata: dict[str, object] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> PushResult:
    """Upsert one source to the platform registry (POST /sources, keyed on slug), the
    portals -> sources half of the move. ``domain`` is required; ``slug`` is sent
    explicitly so the exact id an author's source-join resolves against is preserved (the
    server would otherwise re-derive it from the domain). ``enabled`` is sent explicitly
    when given because the platform's flag is a pointer defaulting to TRUE, so a disabled
    portal pushed without ``enabled=False`` would come back silently re-enabled. The other
    operational fields (feed_type/lean/language/status) carry their real values so the
    server keeps them instead of falling back to a default."""
    body: dict[str, object] = {"domain": domain}
    if slug:
        body["slug"] = slug
    if homepage:
        body["homepage"] = homepage
    if description:
        body["description"] = description
    if feed_urls:
        body["feed_urls"] = list(feed_urls)
    if feed_type:
        body["feed_type"] = feed_type
    if language:
        body["language"] = language
    if ownership_group:
        body["ownership_group"] = ownership_group
    if lean:
        body["lean"] = lean
    if enabled is not None:
        body["enabled"] = bool(enabled)
    if status:
        body["status"] = status
    if last_checked:
        body["last_checked"] = last_checked
    if last_ok:
        body["last_ok"] = last_ok
    if metadata:
        body["metadata"] = dict(metadata)
    return _post_upsert(base_url, token, "/sources", slug or domain, body, timeout)


def delete_web_author(
    base_url: str,
    token: str,
    *,
    handle: str,
    timeout: float = DEFAULT_TIMEOUT,
) -> PushResult:
    """Tombstone an author (DELETE /authors/{handle}); the stored row and its source
    links survive, it just stops being public. Used by the move to carry a retired local
    persona (active=0) across as a tombstoned author rather than a live one. A 204 (no
    content) is success; a 404 (already absent) is treated as success too, so a re-run is
    idempotent."""
    try:
        resp = httpx.delete(
            f"{base_url.rstrip('/')}/authors/{handle}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        return PushResult(handle=handle, ok=False, status=0, code="transport_error", detail=str(exc))
    if resp.status_code in (204, 404):
        return PushResult(handle=handle, ok=True, status=resp.status_code)
    return PushResult(handle=handle, ok=False, status=resp.status_code, code=f"http_{resp.status_code}")


def set_author_sources(
    base_url: str,
    token: str,
    *,
    handle: str,
    sources: list[str],
    timeout: float = DEFAULT_TIMEOUT,
) -> PushResult:
    """Replace an author's attached-source set (PUT /authors/{handle}/sources), the
    author_sources join half of the move. Wholesale replace; the source rows must already
    exist. A missing author is a 404 (mapped to a typed failure, not raised). Used when the
    join is set separately from the author upsert; the author push can also carry a
    ``sources`` pointer to do both in one request."""
    try:
        resp = httpx.put(
            f"{base_url.rstrip('/')}/authors/{handle}/sources",
            headers={"Authorization": f"Bearer {token}"},
            json={"sources": list(sources)},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        return PushResult(handle=handle, ok=False, status=0, code="transport_error", detail=str(exc))
    if resp.status_code == 200:
        return PushResult(handle=handle, ok=True, status=200)
    parsed: object = {}
    try:
        parsed = resp.json()
    except ValueError:
        parsed = {}
    code = str(parsed.get("code", "")) if isinstance(parsed, dict) else ""
    detail = str(parsed.get("detail", "")) if isinstance(parsed, dict) else ""
    return PushResult(handle=handle, ok=False, status=resp.status_code, code=code, detail=detail)


@dataclass(frozen=True)
class BackendProbe:
    """The result of a live reachability/auth probe against the backend read API.

    Two independent axes an operator (or the admin UI) branches on to answer "is the
    brain wired to the backend correctly?":

      * ``reachable`` -- an HTTP response came back at all (the host answered). False
        only on a transport fault (DNS, refused connection, timeout).
      * ``authorized`` -- the operator key was ACCEPTED: True on a 2xx, False on a
        401/403. A non-auth non-2xx (e.g. a 500) leaves ``authorized`` True (the token
        was not rejected) and carries the reason in ``status`` / ``detail``.

    ``author_count`` is the remote LIVE-author count on a 2xx (the small useful signal,
    counted the same way ``fetch_web_authors`` filters: a non-blank handle, not
    tombstoned), else None. ``status`` is the HTTP status (0 on a transport fault) and
    ``detail`` is a short human note (the transport error string, or the backend's
    problem ``code``/``detail``)."""

    reachable: bool
    authorized: bool
    status: int
    author_count: int | None = None
    detail: str = ""


def probe_backend(
    base_url: str, token: str, *, timeout: float = PROBE_TIMEOUT
) -> BackendProbe:
    """A NON-RAISING liveness/auth probe of the backend author read API (GET /authors).

    This is the diagnostic twin of ``fetch_web_authors``: that one RAISES so the
    reconcile can skip cleanly, while this one REPORTS the connection state so the
    status route (and the ``status`` CLI verb) can render a clean diagnostic. It never
    raises out, and the bounded ``timeout`` keeps an unresponsive backend from hanging
    the caller.

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
