"""Tweet (X) erased detection (no API key).

The official X API has no real free read tier, so tweets are read over FixTweet
(``api.fxtwitter.com``), a public keyless JSON mirror that reads the guest API.
The authoring-time capture (URL -> the self-contained snapshot the pipeline stores
under ``metadata.tweets[]``) lives in the stdlib-only CLI, ``cli/censurado.py
tweet``; this module keeps the sweep side: ``recheck_tweet`` re-queries the same
endpoint later, and a 404 means the post is gone, so it flips ``erased`` to True
while KEEPING the captured text, and the generator's card then shows the
"publicación eliminada" note plus the retained original link.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from .fetch import Fetch, default_fetch

__all__ = ["parse_tweet_ref", "recheck_tweet", "FX_BASE"]

FX_BASE = "https://api.fxtwitter.com"

_TWEET_HOSTS = {
    "x.com", "twitter.com", "mobile.twitter.com",
    "fxtwitter.com", "vxtwitter.com", "fixupx.com", "nitter.net",
}
# /<handle>/status/<id>, /i/status/<id>, /web/status/<id>; tolerate "statuses". The
# reserved words i|web come first so they match WITHOUT capturing a handle; a real
# handle that merely starts with "i" still binds the capturing group via backtracking.
_STATUS_RE = re.compile(r"/(?:i|web|([A-Za-z0-9_]{1,15}))/status(?:es)?/(\d+)")


def parse_tweet_ref(url: str) -> tuple[str | None, str] | None:
    """Parse a tweet URL into ``(handle, id)``; handle is None for an ``/i/status/<id>``
    or ``/web/status/<id>`` form. Returns None when the URL is not a recognizable tweet
    on a known X/Twitter (or mirror) host."""
    url = (url or "").strip()
    if not url:
        return None
    try:
        u = urlparse(url)
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host not in _TWEET_HOSTS:
        return None
    m = _STATUS_RE.search(u.path)
    if not m:
        return None
    return (m.group(1), m.group(2))


def _fx_url(handle: str | None, tweet_id: str, base: str) -> str:
    return f"{base.rstrip('/')}/{handle or 'i'}/status/{tweet_id}"


def recheck_tweet(snapshot: dict, *, fetch: Fetch | None = None, base: str = FX_BASE) -> dict:
    """Re-query a stored snapshot's tweet and return a copy with ``erased`` refreshed:
    True when the post can no longer be read (deleted/suspended), False when it is still
    live. The captured text/author/date are never overwritten, so a deleted tweet keeps
    its archived copy. A snapshot whose url cannot be parsed is returned unchanged."""
    fetch = fetch or default_fetch
    snap = dict(snapshot)
    ref = parse_tweet_ref(str(snap.get("url") or ""))
    if not ref:
        return snap
    handle, tweet_id = ref
    res = fetch(_fx_url(handle, tweet_id, base))
    live = res.ok and isinstance(res.json, dict) and isinstance(res.json.get("tweet"), dict)
    snap["erased"] = not live
    return snap
