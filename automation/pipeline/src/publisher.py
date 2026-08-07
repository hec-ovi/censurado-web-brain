"""Publish the approved piece to the backend, idempotency-keyed by the run id."""
import os

import httpx

from .errors import PublishError


class Publisher:
    def __init__(self, cfg: dict):
        self.base = cfg["base_url"].rstrip("/")
        self.token = os.environ[cfg["token_env"]]
        self.timeout = cfg.get("timeout_s", 60)

    def _headers(self, idempotency_key: str | None = None) -> dict:
        h = {"Authorization": f"Bearer {self.token}"}
        if idempotency_key:
            h["Idempotency-Key"] = idempotency_key
        return h

    def publish(self, piece: dict, inputs: dict, idempotency_key: str) -> dict:
        body = {
            "title": piece["title"],
            "body": piece["body"],
            "author": inputs["author"],
            "section": inputs["section"],
        }
        if isinstance(piece.get("topics"), list):
            body["topics"] = piece["topics"]
        if piece.get("standfirst"):
            body["metadata"] = {"standfirst": piece["standfirst"]}
        try:
            r = httpx.post(f"{self.base}/articles", json=body,
                           headers=self._headers(idempotency_key), timeout=self.timeout)
        except httpx.HTTPError as e:
            raise PublishError(f"backend unreachable: {e}") from e
        if r.status_code not in (200, 201):
            raise PublishError(f"POST /articles -> {r.status_code}: {r.text[:200]}")
        slug = r.json().get("slug", "")
        return {"slug": slug, "article_id": str(r.json().get("id", "")),
                "permalink": self._permalink(slug)}

    def _permalink(self, slug: str) -> str:
        if not slug:
            return ""
        try:
            g = httpx.get(f"{self.base}/articles/{slug}",
                          headers=self._headers(), timeout=self.timeout)
        except httpx.HTTPError:
            return ""
        if g.status_code != 200:
            return ""
        h = (g.json().get("content_hash") or "")[:8]
        return f"/a/{slug}-{h}/" if h else ""
