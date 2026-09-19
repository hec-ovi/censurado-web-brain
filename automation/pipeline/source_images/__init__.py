"""Find relevant source photographs and attach them through the media API."""
import base64
import json
import logging
import os
from contextlib import nullcontext
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .download import PublicDownloader
from .page import ArticlePage

LOG = logging.getLogger(__name__)


class SourceImages:
    def __init__(self, backend: dict, client=None):
        self.backend = backend
        self.client = client

    def attach(self, urls: list[str], article: dict, choose) -> dict:
        with httpx.Client() if self.client is None else nullcontext(self.client) as client:
            downloader = PublicDownloader(client)
            candidates = []
            seen = set()
            for url in list(dict.fromkeys(urls))[:4]:
                if len(candidates) >= 4:
                    break
                try:
                    mime, content, final_url = downloader.get(url, 2 * 1024 * 1024)
                    if mime not in ("text/html", "application/xhtml+xml"):
                        continue
                    page = ArticlePage(final_url)
                    page.feed(content.decode("utf-8", errors="replace"))
                    for candidate in page.images()[:3]:
                        if candidate["url"] in seen:
                            continue
                        seen.add(candidate["url"])
                        try:
                            kind, data, image_url = downloader.get(candidate["url"], 12 * 1024 * 1024)
                        except (httpx.HTTPError, ValueError, OSError, KeyError):
                            continue
                        if kind not in ("image/jpeg", "image/png", "image/webp", "image/gif"):
                            continue
                        candidates.append({**candidate, "url": image_url, "id": str(len(candidates) + 1),
                                           "data": data, "mime": kind})
                        if len(candidates) >= 4:
                            break
                except (httpx.HTTPError, ValueError, OSError, KeyError):
                    continue
            if not candidates:
                return {}
            descriptions = [{k: v for k, v in c.items() if k not in ("data", "mime", "priority")}
                            for c in candidates]
            prompt = (Path(__file__).parent / "prompts/select.md").read_text()
            prompt = prompt.replace("{article}", json.dumps(article, ensure_ascii=False))
            prompt = prompt.replace("{candidates}", json.dumps(descriptions, ensure_ascii=False))
            images = ["data:" + c["mime"] + ";base64," + base64.b64encode(c["data"]).decode()
                      for c in candidates]
            try:
                selected = choose(prompt, images)
                candidate = next((c for c in candidates if c["id"] == selected.get("id")), None)
                if candidate is None:
                    return {}
                response = client.post(self.backend["base_url"].rstrip("/") + "/media",
                                       content=candidate["data"], timeout=30,
                                       headers={"Content-Type": candidate["mime"],
                                                "Authorization": "Bearer " + os.environ[self.backend["token_env"]]})
                response.raise_for_status()
                return {"image": response.json()["url"],
                        "image_alt": selected.get("alt") or candidate["alt"] or article["title"],
                        "image_caption": candidate["caption"],
                        "image_credit": candidate["credit"] or urlsplit(candidate["source"]).hostname,
                        "image_source": candidate["source"], "image_original": candidate["url"]}
            except Exception as exc:
                LOG.warning("Source photograph unavailable: %s", type(exc).__name__)
                return {}
