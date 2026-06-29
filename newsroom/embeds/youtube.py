"""YouTube embed capture + removal detection (no API key).

Given a YouTube reference (a bare id or a watch / youtu.be / shorts / embed URL),
``check_youtube`` calls the public oEmbed endpoint: a 200 means the video is live and
carries its title/author/thumbnail; any non-200 (404 deleted, 401 private) means it is
unavailable. The result is a snapshot the pipeline stores under
``metadata.media_checks[<id>]`` so the static generator can render the embed (when
available) or the "este video fue eliminado" placeholder (when not), without ever
fetching at build time. ``parse_youtube_id`` mirrors the generator's own id parser so
the brain and the site agree on what a video id is.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlparse

from .fetch import Fetch, default_fetch

__all__ = ["parse_youtube_id", "check_youtube", "YT_OEMBED_BASE"]

YT_OEMBED_BASE = "https://www.youtube.com/oembed"

# Mirror the generator's youtubeIDRe (media.go): a YouTube id is 6-32 url-safe chars.
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,32}$")
_YT_HOSTS = {"youtube.com", "m.youtube.com", "music.youtube.com", "youtube-nocookie.com"}


def parse_youtube_id(ref: str) -> str | None:
    """Extract a YouTube video id from a bare id or a watch/youtu.be/shorts/embed URL,
    or None when the reference is not a recognizable video. Matches the generator so a
    captured id resolves to the same embed the site renders."""
    ref = (ref or "").strip()
    if not ref:
        return None
    if _ID_RE.match(ref):
        return ref
    try:
        u = urlparse(ref)
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    vid = ""
    if host == "youtu.be":
        vid = u.path.strip("/").split("/")[0]
    elif host in _YT_HOSTS:
        if u.path == "/watch":
            vid = (parse_qs(u.query).get("v") or [""])[0]
        elif u.path.startswith("/embed/"):
            vid = u.path[len("/embed/") :].split("/")[0]
        elif u.path.startswith("/shorts/"):
            vid = u.path[len("/shorts/") :].split("/")[0]
    vid = vid.strip("/").split("/")[0]
    return vid if _ID_RE.match(vid) else None


def check_youtube(ref: str, *, fetch: Fetch | None = None, base: str = YT_OEMBED_BASE) -> dict | None:
    """Resolve a YouTube reference and check availability via oEmbed. Returns a snapshot
    ``{"id", "available", ["title"], ["author"], ["thumbnail"]}`` or None when the
    reference is not a video. ``available`` is True only on a 200; a deleted/private
    video is ``available=False`` with no title (the pipeline then renders the removed
    placeholder)."""
    fetch = fetch or default_fetch
    vid = parse_youtube_id(ref)
    if not vid:
        return None
    watch = f"https://www.youtube.com/watch?v={vid}"
    url = f"{base}?{urlencode({'url': watch, 'format': 'json'})}"
    res = fetch(url)
    snap: dict = {"id": vid, "available": bool(res.ok)}
    if res.ok and isinstance(res.json, dict):
        if res.json.get("title"):
            snap["title"] = str(res.json["title"])
        if res.json.get("author_name"):
            snap["author"] = str(res.json["author_name"])
        if res.json.get("thumbnail_url"):
            snap["thumbnail"] = str(res.json["thumbnail_url"])
    return snap
