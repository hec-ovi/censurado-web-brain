"""Publish the approved piece to the backend, idempotency-keyed by the run id."""
import os
import re

import httpx

from .errors import PublishError
from .toolkit import run_verb

_TWEET_MARKER = re.compile(r"\{\{tweet:([0-9]+)\}\}")


class Publisher:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        backend = cfg["backend"]
        self.base = backend["base_url"].rstrip("/")
        self.token = os.environ[backend["token_env"]]
        self.timeout = backend.get("timeout_s", 60)

    def _headers(self, idempotency_key: str | None = None) -> dict:
        h = {"Authorization": f"Bearer {self.token}"}
        if idempotency_key:
            h["Idempotency-Key"] = idempotency_key
        return h

    def _byline(self, handle: str) -> dict:
        """The registry byline the portal renders (face, name, bio), same keys the CLI fills."""
        try:
            r = httpx.get(f"{self.base}/authors", headers=self._headers(), timeout=self.timeout)
            rows = r.json()
        except (httpx.HTTPError, ValueError):
            return {}
        rows = rows.get("authors", rows) if isinstance(rows, dict) else rows
        for a in rows:
            if a.get("handle") == handle:
                meta = {}
                if a.get("name"):
                    meta["author_name"] = a["name"]
                if a.get("bio") or a.get("about"):
                    meta["author_bio"] = a.get("bio") or a.get("about")
                if a.get("avatar"):
                    meta["author_avatar"] = a["avatar"]
                return meta
        return {}

    def _tweet_snapshots(self, body: str) -> list[dict]:
        """Fetch the card for every {{tweet:<id>}} the body embeds, through the toolkit's
        `tweet` verb (the same auto-fetch `preview` does). A card that cannot be fetched is
        skipped and the piece publishes without it."""
        snaps = []
        for tid in sorted(set(_TWEET_MARKER.findall(body or ""))):
            snap = run_verb(self.cfg, "tweet", tid)
            if snap:
                snaps.append(snap)
        return snaps

    def publish(self, piece: dict, inputs: dict, idempotency_key: str) -> dict:
        body = {
            "title": piece["title"],
            "body": piece["body"],
            "author": inputs["author"],
            "section": inputs["section"],
        }
        if isinstance(piece.get("topics"), list):
            body["topics"] = piece["topics"]
        meta: dict = {**self._byline(inputs["author"])}
        if piece.get("image"):
            meta["image"] = piece["image"]
            meta["card"] = {"type": "image", "src": piece["image"],
                            "alt": piece.get("image_alt", "")}
        else:
            meta["card"] = {"type": "text"}
        if piece.get("standfirst"):
            meta["description"] = piece["standfirst"]
        tweets = self._tweet_snapshots(piece.get("body", ""))
        if tweets:
            meta["tweets"] = tweets
        body["metadata"] = meta
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
