"""The HTTP fetch seam for embed capture.

Capturing a tweet (fxtwitter) or checking a YouTube video (oEmbed) is a single GET
that returns JSON. The whole capture/recheck layer takes a ``fetch`` callable so the
pure logic (parse a ref, build a snapshot, flip an erased flag) is unit-testable
without a network: a test injects a fake fetch that maps a URL to a canned result.
In production ``default_fetch`` does the real GET over httpx, swallowing transport
errors into a not-ok result so a dead endpoint degrades to "unavailable" rather than
raising into the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import httpx

__all__ = ["FetchResult", "Fetch", "default_fetch", "DEFAULT_TIMEOUT"]

DEFAULT_TIMEOUT = 10.0


@dataclass
class FetchResult:
    """The minimal result of one GET: the HTTP status, the parsed JSON (or None when
    the body was absent/not JSON), and ``ok`` (a 200). Capture treats any non-200 as
    "the resource is not available right now"."""

    status: int
    json: Any | None
    ok: bool


# A fetch maps a URL to a FetchResult. Injected by tests; default does a real GET.
Fetch = Callable[[str], FetchResult]


def default_fetch(url: str, *, timeout: float = DEFAULT_TIMEOUT) -> FetchResult:
    """GET ``url`` and return a FetchResult. A transport error (DNS, refused, timeout)
    becomes ``FetchResult(0, None, False)`` so the caller sees "unavailable" instead of
    an exception; a non-JSON body leaves ``json`` None while keeping the status."""
    try:
        resp = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "censurado-newsroom/1.0"},
        )
    except httpx.HTTPError:
        return FetchResult(status=0, json=None, ok=False)
    data: Any | None
    try:
        data = resp.json()
    except ValueError:
        data = None
    return FetchResult(status=resp.status_code, json=data, ok=resp.status_code == 200)
