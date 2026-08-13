"""The api adapter: one stateless call to an OpenAI-compatible /chat/completions."""
import os

import httpx

from .errors import AdapterError


class ApiAdapter:
    def __init__(self, cfg: dict):
        self.base = cfg["base_url"].rstrip("/")
        self.model = cfg["model"]
        self.system = cfg.get("system")
        self.timeout = cfg.get("timeout_s", 300)
        self.headers = {}
        # A direct api_key (e.g. merged in from the panel's settings) wins over
        # the env-named one; either way the key never logs.
        key = cfg.get("api_key") or os.environ.get(cfg.get("api_key_env") or "", "")
        if key:
            self.headers["Authorization"] = f"Bearer {key}"

    def complete(self, prompt: str, want_json: bool) -> str:
        messages = []
        if self.system:
            messages.append({"role": "system", "content": self.system})
        messages.append({"role": "user", "content": prompt})
        payload: dict = {"model": self.model, "messages": messages}
        if want_json:
            payload["response_format"] = {"type": "json_object"}
        try:
            r = httpx.post(f"{self.base}/chat/completions", json=payload,
                           headers=self.headers, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise AdapterError(f"api unreachable: {e}") from e
        if r.status_code != 200:
            raise AdapterError(f"api {r.status_code}: {r.text[:200]}")
        try:
            return r.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as e:
            raise AdapterError(f"api response off-shape: {e}") from e
