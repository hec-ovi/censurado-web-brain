"""Publish the approved piece to the backend, idempotency-keyed by the run id."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import httpx

from .errors import PublishError

_TWEET_MARKER = re.compile(r"\{\{tweet:([0-9]+)\}\}")


class Publisher:
    def __init__(self, cfg: dict):
        backend = cfg["backend"]
        self.base = backend["base_url"].rstrip("/")
        self.token = os.environ[backend["token_env"]]
        self.timeout = backend.get("timeout_s", 60)
        toolkit = cfg.get("toolkit", {})
        default_cli = str(Path(__file__).resolve().parents[3] / "cli" / "censurado.py")
        self.toolkit_cmd = toolkit.get("cmd") or [sys.executable, default_cli]
        self.toolkit_timeout = toolkit.get("timeout_s", 60)

    def _headers(self, idempotency_key: str | None = None) -> dict:
        h = {"Authorization": f"Bearer {self.token}"}
        if idempotency_key:
            h["Idempotency-Key"] = idempotency_key
        return h

    def _tweet_snapshots(self, body: str) -> list[dict]:
        """Fetch the card for every {{tweet:<id>}} the body embeds, through the toolkit's
        `tweet` verb (the same auto-fetch `preview` does). A card that cannot be fetched is
        skipped and the piece publishes without it."""
        snaps = []
        for tid in sorted(set(_TWEET_MARKER.findall(body or ""))):
            try:
                p = subprocess.run([*self.toolkit_cmd, "tweet", tid],
                                   capture_output=True, text=True,
                                   timeout=self.toolkit_timeout)
                if p.returncode == 0:
                    snap = json.loads(p.stdout)
                    if isinstance(snap, dict):
                        snaps.append(snap)
            except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
                continue
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
        meta: dict = {}
        if piece.get("standfirst"):
            meta["description"] = piece["standfirst"]
        tweets = self._tweet_snapshots(piece.get("body", ""))
        if tweets:
            meta["tweets"] = tweets
        if meta:
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
