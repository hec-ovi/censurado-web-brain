"""The api adapter: one stateless call to an OpenAI-compatible /chat/completions."""
import json
import os

import httpx

from .errors import AdapterError

# A degenerate decode repeats itself forever, with any cycle length: one pair,
# or a whole block of lines. The signature that survives every shape: the very
# latest stretch of output has already appeared, verbatim, several times close
# behind it. Real prose or a real JSON map never repeats 400 identical chars
# three times. On detection the stream is aborted, which cancels the decode
# server side (a streaming disconnect, unlike a plain timeout, stops llama).
LOOP_WINDOW = 12000
LOOP_NEEDLE = 400
LOOP_REPEATS = 3


def _looping(text: str) -> bool:
    tail = text[-LOOP_WINDOW:]
    if len(tail) < LOOP_NEEDLE * LOOP_REPEATS:
        return False
    return tail.count(tail[-LOOP_NEEDLE:]) >= LOOP_REPEATS


class ApiAdapter:
    def __init__(self, cfg: dict):
        self.base = cfg["base_url"].rstrip("/")
        self.model = cfg["model"]
        self.system = cfg.get("system")
        self.timeout = cfg.get("timeout_s", 300)
        self.temperature = cfg.get("temperature")
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
        payload: dict = {"model": self.model, "messages": messages, "stream": True}
        if self.temperature is not None:
            payload["temperature"] = self.temperature
        if want_json:
            # The json_schema form engages the server's real grammar (llama.cpp
            # treats json_object as advisory, so long outputs can come back
            # broken); the name field is required by remote providers.
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "salida", "schema": {"type": "object"}}}
        try:
            with httpx.stream("POST", f"{self.base}/chat/completions", json=payload,
                              headers=self.headers, timeout=self.timeout) as r:
                if r.status_code != 200:
                    r.read()
                    raise AdapterError(f"api {r.status_code}: {r.text[:200]}")
                if "text/event-stream" not in r.headers.get("content-type", ""):
                    # The server answered in one piece.
                    r.read()
                    try:
                        return r.json()["choices"][0]["message"]["content"]
                    except (KeyError, IndexError, ValueError) as e:
                        raise AdapterError(f"api response off-shape: {e}") from e
                parts: list[str] = []
                size = checked = 0
                done = False
                for line in r.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data == "[DONE]":
                        done = True
                        break
                    try:
                        choices = json.loads(data)["choices"]
                        delta = (choices[0].get("delta") or {}) if choices else {}
                    except (KeyError, IndexError, ValueError) as e:
                        raise AdapterError(f"api response off-shape: {e}") from e
                    parts.append(delta.get("content") or "")
                    size += len(parts[-1])
                    if size - checked >= 1024:
                        checked = size
                        if _looping("".join(parts)):
                            raise AdapterError(
                                f"degenerate output loop after {size} chars")
                if not done:
                    # A stream the server ended without its terminator is a
                    # failed call, never a truncated answer passed downstream.
                    raise AdapterError(f"stream ended early after {size} chars")
        except httpx.HTTPError as e:
            raise AdapterError(f"api unreachable: {e}") from e
        return "".join(parts)
