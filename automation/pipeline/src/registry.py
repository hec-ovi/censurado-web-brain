"""The per-author used-URL registry: capped, FIFO, stored on the author's backend record.

A URL registered here means the author already wrote that news; the feeds context omits
those titulars so the same story is never pitched twice. The registry rides in the author's
`metadata.used_urls` (newest first), so every lane (CLI, pipeline, cloud) shares it through
the one operator API.
"""
import os
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from .errors import PublishError

_TRACKING = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src")


def normalize_url(url: str) -> str:
    """One canonical form per news URL: lowercase scheme/host, no fragment, no tracking
    params, no trailing slash."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                       if not (k.lower().startswith(_TRACKING[0]) or k.lower() in _TRACKING[1:])])
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                       parts.path.rstrip("/"), query, ""))


class UsedUrls:
    def __init__(self, backend_cfg: dict, cap: int = 50):
        self.base = backend_cfg["base_url"].rstrip("/")
        self.token = os.environ[backend_cfg["token_env"]]
        self.timeout = backend_cfg.get("timeout_s", 60)
        self.cap = cap

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    def _author(self, handle: str) -> dict:
        try:
            r = httpx.get(f"{self.base}/authors", headers=self._headers(), timeout=self.timeout)
            rows = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise PublishError(f"registry: cannot read authors: {e}") from e
        rows = rows.get("authors", rows) if isinstance(rows, dict) else rows
        for a in rows:
            if a.get("handle") == handle:
                return a
        raise PublishError(f"registry: author '{handle}' not found")

    def urls(self, handle: str) -> set[str]:
        meta = self._author(handle).get("metadata") or {}
        stored = meta.get("used_urls") or []
        return {u for u in stored if isinstance(u, str)}

    def register(self, handle: str, urls: list[str]) -> list[str]:
        """Add normalized URLs to the front of the author's list, dedup, trim to cap
        (oldest fall off), upsert the author record. Returns the stored list."""
        fresh = [normalize_url(u) for u in urls if u and u.strip()]
        if not fresh:
            return []
        a = self._author(handle)
        meta = dict(a.get("metadata") or {})
        stored = [u for u in (meta.get("used_urls") or []) if isinstance(u, str)]
        merged: list[str] = []
        for u in fresh + stored:
            if u not in merged:
                merged.append(u)
        meta["used_urls"] = merged[:self.cap]
        body = {"handle": a["handle"], "name": a.get("name") or "",
                "bio": a.get("bio") or "", "avatar": a.get("avatar") or "",
                "gender": a.get("gender") or "", "about": a.get("about") or "",
                "style": a.get("style") or "", "topics": a.get("topics") or [],
                "metadata": meta}
        try:
            r = httpx.post(f"{self.base}/authors", json=body,
                           headers=self._headers(), timeout=self.timeout)
        except httpx.HTTPError as e:
            raise PublishError(f"registry: author upsert unreachable: {e}") from e
        if r.status_code not in (200, 201):
            raise PublishError(f"registry: author upsert -> {r.status_code}: {r.text[:200]}")
        return meta["used_urls"]
