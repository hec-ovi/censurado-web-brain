"""The ComfyUI client: raw HTTP to the local image-generation server.

ComfyUI's HTTP API is asynchronous on the wire (submit a graph, then poll for the
result). This client hides that submit -> poll -> fetch loop behind one synchronous
``generate`` call, the same typed-in/typed-out shape the publish transport uses. It
is sync and runs off the event loop (and off the SQLite lock), like ``publish_article``.

Flow (verified against upstream ComfyUI):
  * ``POST /prompt`` with ``{"prompt": <graph>, "client_id": ...}`` -> ``{"prompt_id"}``;
    a malformed graph returns 400 with ``{error, node_errors}`` (surfaced as a typed
    ``ComfyError``, never silently swallowed).
  * ``GET /history/{prompt_id}`` until the entry's ``status.completed`` is true; the
    SaveImage node's ``outputs`` carry ``images: [{filename, subfolder, type}]``.
  * ``GET /view?filename=&subfolder=&type=`` returns the raw PNG bytes.
  * ``POST /upload/image`` (multipart ``image`` field) stores a reference image and
    returns its ``name`` for a LoadImage node.

No output-length cap is involved anywhere; image size and step count are render
parameters set in the graph, not generation-length caps.
"""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass

import httpx

__all__ = ["ComfyClient", "ComfyError", "DEFAULT_TIMEOUT", "DEFAULT_POLL_INTERVAL"]

# Diffusion is slower than text; a generous default ceiling. The per-article budget's
# wall clock is the real bound at the pipeline level (this is the transport timeout).
DEFAULT_TIMEOUT = float(os.getenv("NEWSROOM_IMAGE_TIMEOUT", "600"))
DEFAULT_POLL_INTERVAL = float(os.getenv("NEWSROOM_IMAGE_POLL_INTERVAL", "1.0"))


class ComfyError(RuntimeError):
    """A ComfyUI render failed (bad graph, execution error, timeout, or transport)."""


@dataclass
class ComfyClient:
    """A thin synchronous ComfyUI HTTP client.

    ``base_url`` is the server root (e.g. ``http://127.0.0.1:8188``). ``timeout`` is
    the overall wall-clock ceiling for one ``generate`` (submit + poll + fetch);
    ``poll_interval`` is the gap between ``/history`` polls."""

    base_url: str
    timeout: float = DEFAULT_TIMEOUT
    poll_interval: float = DEFAULT_POLL_INTERVAL

    def _root(self) -> str:
        return self.base_url.rstrip("/")

    def upload_image(self, data: bytes, *, filename: str = "reference.png",
                     image_type: str = "input") -> str:
        """Upload a reference image to the ComfyUI server and return the stored name a
        LoadImage node can reference. Retries once on a connection-level fault only."""
        url = f"{self._root()}/upload/image"
        files = {"image": (filename, data, "image/png")}
        payload = {"type": image_type, "overwrite": "true"}
        resp = self._post_with_retry(url, files=files, data=payload)
        resp.raise_for_status()
        body = resp.json()
        name = body.get("name") or filename
        subfolder = body.get("subfolder") or ""
        return f"{subfolder}/{name}" if subfolder else name

    def generate(self, graph: dict, *, client_id: str | None = None) -> bytes:
        """Submit an API-format graph and return the first rendered image's PNG bytes.
        Blocks (polling ``/history``) until the render completes or the wall-clock
        ``timeout`` is hit. Raises ``ComfyError`` on a bad graph, a reported execution
        error, an empty result, or the poll deadline. Transport faults on the poll/fetch
        GETs (a read timeout or a non-2xx on ``/history`` or ``/view``) propagate as the
        underlying ``httpx`` error; the only caller wraps the whole step in a broad
        catch and degrades to no image, so both surface the same way upstream."""
        client_id = client_id or uuid.uuid4().hex
        deadline = time.monotonic() + self.timeout
        with httpx.Client(timeout=self.timeout) as client:
            prompt_id = self._submit(client, graph, client_id)
            entry = self._await_history(client, prompt_id, deadline)
            filename, subfolder, ftype = self._first_image(entry, prompt_id)
            view = client.get(
                f"{self._root()}/view",
                params={"filename": filename, "subfolder": subfolder, "type": ftype},
            )
            view.raise_for_status()
            return view.content

    def _submit(self, client: httpx.Client, graph: dict, client_id: str) -> str:
        try:
            resp = client.post(
                f"{self._root()}/prompt", json={"prompt": graph, "client_id": client_id}
            )
        except (httpx.ConnectError, httpx.RemoteProtocolError):
            time.sleep(0.5)
            resp = client.post(
                f"{self._root()}/prompt", json={"prompt": graph, "client_id": client_id}
            )
        if resp.status_code == 400:
            # A malformed graph (wrong node name, bad link) fails HERE, never on poll.
            raise ComfyError(f"comfyui rejected the graph: {resp.text}")
        resp.raise_for_status()
        prompt_id = resp.json().get("prompt_id")
        if not prompt_id:
            raise ComfyError("comfyui did not return a prompt_id")
        return prompt_id

    def _await_history(self, client: httpx.Client, prompt_id: str, deadline: float) -> dict:
        while True:
            resp = client.get(f"{self._root()}/history/{prompt_id}")
            resp.raise_for_status()
            history = resp.json() or {}
            entry = history.get(prompt_id)
            if entry:
                status = entry.get("status", {})
                if status.get("status_str") == "error":
                    raise ComfyError(f"comfyui execution error: {status.get('messages')}")
                if status.get("completed") or entry.get("outputs"):
                    return entry
            if time.monotonic() >= deadline:
                raise ComfyError(f"comfyui render timed out after {self.timeout}s")
            time.sleep(self.poll_interval)

    @staticmethod
    def _first_image(entry: dict, prompt_id: str) -> tuple[str, str, str]:
        for node_output in (entry.get("outputs") or {}).values():
            images = node_output.get("images") or []
            for image in images:
                if image.get("filename"):
                    return (
                        image["filename"],
                        image.get("subfolder", ""),
                        image.get("type", "output"),
                    )
        raise ComfyError(f"comfyui produced no image for prompt {prompt_id}")

    def _post_with_retry(self, url: str, **kwargs) -> httpx.Response:
        try:
            return httpx.post(url, timeout=self.timeout, **kwargs)
        except (httpx.ConnectError, httpx.RemoteProtocolError):
            time.sleep(0.5)
            return httpx.post(url, timeout=self.timeout, **kwargs)
