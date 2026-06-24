"""The media-upload client against the fake platform ``POST /media`` endpoint.

The brain uploads a generated PNG's raw bytes with its operator key (the
``articles:write`` scope it already holds for publishing) and gets back a
content-addressed ``/media/<sha256>.png`` URL it stamps into ``metadata.image``.
"""

from __future__ import annotations

import hashlib

import httpx
import pytest

from newsroom.publish import media as media_mod
from newsroom.publish.media import MediaUploadError, upload_media


def test_upload_returns_a_content_addressed_url(fake):
    data = b"\x89PNG fake image bytes"
    asset = upload_media(data, base_url=fake.media_url, token="op-token")

    sha = hashlib.sha256(data).hexdigest()
    assert asset.url == f"/media/{sha}.png"
    assert asset.sha256 == sha
    assert asset.size == len(data)
    assert len(fake.state.media_requests) == 1
    assert fake.state.media_requests[0]["url"] == asset.url


def test_upload_requires_the_write_scope(fake):
    with pytest.raises(MediaUploadError):
        upload_media(b"bytes", base_url=fake.media_url, token="noscope-token")
    # A rejected upload stored nothing.
    assert fake.state.media_requests == []


def test_upload_rejects_empty_bytes(fake):
    with pytest.raises(MediaUploadError):
        upload_media(b"", base_url=fake.media_url, token="op-token")


# ----- the typed error catches a platform that changed its response shape -----


def test_upload_maps_a_transport_fault(monkeypatch):
    def boom(*_a, **_k):
        raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(media_mod.httpx, "post", boom)
    with pytest.raises(MediaUploadError):
        upload_media(b"x", base_url="http://portal", token="op-token")


def test_upload_maps_a_2xx_with_no_url(monkeypatch):
    monkeypatch.setattr(media_mod.httpx, "post", lambda *_a, **_k: httpx.Response(201, json={}))
    with pytest.raises(MediaUploadError):
        upload_media(b"x", base_url="http://portal", token="op-token")


def test_upload_maps_a_non_json_success_body(monkeypatch):
    monkeypatch.setattr(media_mod.httpx, "post", lambda *_a, **_k: httpx.Response(201, text="<html>"))
    with pytest.raises(MediaUploadError):
        upload_media(b"x", base_url="http://portal", token="op-token")
