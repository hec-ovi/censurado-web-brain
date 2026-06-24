"""The media-upload client: raw HTTP to the platform's image endpoint.

This is the companion of the article publish client. The platform serves uploaded
images from a content-addressed store: ``POST /media`` takes the RAW image bytes as
the request body (not multipart), authenticated with the same operator key and the
``articles:write`` scope the brain already holds, with no idempotency key (identical
bytes always map to the same URL). It returns the stored asset, whose root-relative
``url`` (e.g. ``/media/<sha256>.png``) the brain puts into the article's
``metadata.image`` so the portal renders it as the hero image.

No output-length cap is involved anywhere; this layer only transports image bytes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import httpx

__all__ = ["MediaAsset", "MediaUploadError", "upload_media", "DEFAULT_TIMEOUT"]

DEFAULT_TIMEOUT = float(os.getenv("NEWSROOM_MEDIA_TIMEOUT", "30"))


class MediaUploadError(RuntimeError):
    """An image upload to the platform media endpoint failed."""


@dataclass
class MediaAsset:
    """A stored image as the platform reports it. ``url`` is what goes into
    ``metadata.image`` (root-relative, e.g. ``/media/<sha256>.png``)."""

    url: str
    sha256: str = ""
    content_type: str = ""
    size: int = 0


def upload_media(
    data: bytes,
    *,
    base_url: str,
    token: str,
    content_type: str = "image/png",
    timeout: float = DEFAULT_TIMEOUT,
) -> MediaAsset:
    """POST raw image bytes to ``{base_url}/media`` and return the stored asset.

    Raises ``MediaUploadError`` on a transport fault or a non-2xx response (the caller
    treats that as "no image" and publishes the article without one)."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": content_type}
    try:
        resp = httpx.post(
            f"{base_url.rstrip('/')}/media", headers=headers, content=data, timeout=timeout
        )
    except httpx.HTTPError as exc:
        raise MediaUploadError(f"media upload transport error: {exc}") from exc

    if resp.status_code not in (200, 201):
        try:
            body = resp.json()
        except ValueError:
            body = {}
        code = body.get("code") or body.get("title") or ""
        raise MediaUploadError(
            f"media upload failed: {resp.status_code} {code or resp.text[:200]}"
        )

    try:
        body = resp.json()
    except ValueError as exc:
        raise MediaUploadError("media upload returned a non-JSON body") from exc
    url = body.get("url")
    if not url:
        raise MediaUploadError("media upload returned no url")
    return MediaAsset(
        url=url,
        sha256=body.get("sha256", ""),
        content_type=body.get("content_type", ""),
        size=int(body.get("size", 0) or 0),
    )
