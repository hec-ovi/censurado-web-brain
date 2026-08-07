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
        key_env = cfg.get("api_key_env")
        if key_env and os.environ.get(key_env):
            self.headers["Authorization"] = f"Bearer {os.environ[key_env]}"

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
