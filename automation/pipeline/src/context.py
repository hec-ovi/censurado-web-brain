"""Resolve a node's declared context sources into prompt placeholder values."""
import os
from pathlib import Path

import httpx

from .errors import AdapterError


class ContextFetcher:
    def __init__(self, backend_cfg: dict):
        self.base = backend_cfg["base_url"].rstrip("/")
        self.token = os.environ[backend_cfg["token_env"]]
        self.timeout = backend_cfg.get("timeout_s", 60)

    def resolve(self, spec: dict, inputs: dict) -> dict:
        out = {}
        for key, src in spec.items():
            if "file" in src:
                out[key] = Path(src["file"]).read_text()
            elif "persona" in src:
                out[key] = self._persona(inputs["author"])
            elif "editorial" in src:
                out[key] = self._editorial(src["editorial"])
        return out

    def _get(self, path: str):
        try:
            r = httpx.get(self.base + path,
                          headers={"Authorization": f"Bearer {self.token}"},
                          timeout=self.timeout)
        except httpx.HTTPError as e:
            raise AdapterError(f"context read {path} unreachable: {e}") from e
        if r.status_code != 200:
            raise AdapterError(f"context read {path} -> {r.status_code}")
        return r.json()

    def _persona(self, handle: str) -> str:
        data = self._get("/authors")
        rows = data if isinstance(data, list) else data.get("authors", [])
        for a in rows:
            if a.get("handle") == handle:
                meta = a.get("metadata") or {}
                parts = [f"Nombre: {a.get('name', '')}",
                         f"Bio: {a.get('bio', '')}",
                         f"Carta de estilo:\n{a.get('style', '')}"]
                if meta.get("who_i_am"):
                    parts.append(f"Quien soy:\n{meta['who_i_am']}")
                return "\n\n".join(parts)
        raise AdapterError(f"author '{handle}' not found in the backend")

    def _editorial(self, lang: str) -> str:
        data = self._get(f"/editorial-text?lang={lang}")
        return "\n".join(f"- {e['key']}: {e['value']}"
                         for e in data.get("entries", []) if not e.get("deleted"))
