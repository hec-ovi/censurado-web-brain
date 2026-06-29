"""Tweet (X) capture + erased detection (no API key).

The official X API has no real free read tier, so a single cited tweet is captured
over FixTweet (``api.fxtwitter.com``), a public keyless JSON mirror that reads the
guest API. ``capture_tweet`` turns a tweet URL into a self-contained snapshot
(author, handle, avatar, text, date, canonical url) that the pipeline stores under
``metadata.tweets[]``; the generator renders an X-style card from that snapshot, so
the quote SURVIVES the original being deleted. ``recheck_tweet`` re-queries the same
endpoint later: a 404 means the post is gone, so it flips ``erased`` to True while
KEEPING the captured text, and the card then shows the "publicación eliminada" note
plus the retained original link.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from .fetch import Fetch, default_fetch

__all__ = ["parse_tweet_ref", "capture_tweet", "recheck_tweet", "FX_BASE"]

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


def _display_date(tweet: dict) -> str:
    """A locale-neutral ISO date (YYYY-MM-DD) from the tweet's unix timestamp, falling
    back to the raw ``created_at`` string when no timestamp is present."""
    ts = tweet.get("created_timestamp")
    if isinstance(ts, (int, float)) and ts > 0:
        return datetime.fromtimestamp(ts, tz=timezone.utc).date().isoformat()
    raw = tweet.get("created_at")
    return str(raw) if raw else ""


def capture_tweet(url: str, *, fetch: Fetch | None = None, base: str = FX_BASE) -> dict | None:
    """Capture a single tweet into a self-contained snapshot, or None when the URL is
    not a tweet or the post cannot be read. The snapshot's keys match exactly what the
    generator's tweet card reads: ``id, handle, name, text, url, avatar, created_at,
    erased``."""
    fetch = fetch or default_fetch
    ref = parse_tweet_ref(url)
    if not ref:
        return None
    handle, tweet_id = ref
    res = fetch(_fx_url(handle, tweet_id, base))
    if not res.ok or not isinstance(res.json, dict):
        return None
    tweet = res.json.get("tweet")
    if not isinstance(tweet, dict):
        return None
    author = tweet.get("author") if isinstance(tweet.get("author"), dict) else {}
    return {
        "id": str(tweet.get("id") or tweet_id),
        "handle": str(author.get("screen_name") or (handle or "")),
        "name": str(author.get("name") or ""),
        "text": str(tweet.get("text") or ""),
        "url": str(tweet.get("url") or url),
        "avatar": str(author.get("avatar_url") or ""),
        "created_at": _display_date(tweet),
        "erased": False,
    }


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
