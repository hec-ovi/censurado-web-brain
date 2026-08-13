"""Fetch and parse portal feeds (RSS, Atom, news sitemap) into fresh titulars."""
import email.utils
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree

import httpx

from .errors import AdapterError

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) censurado-newsroom/1.0"}


@dataclass
class Entry:
    title: str
    link: str
    published: datetime | None


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        return email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        pass
    try:
        d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_feed(content: bytes) -> tuple[str, list[Entry]]:
    """Parse feed bytes; returns (kind, entries) with kind one of rss|atom|news_sitemap|sitemap.

    Raises AdapterError when the bytes are not a recognizable feed.
    """
    try:
        root = ElementTree.fromstring(re.sub(rb"^\s+", b"", content))
    except ElementTree.ParseError as e:
        raise AdapterError(f"not xml: {e}") from e
    tag = _strip_ns(root.tag)
    if tag == "rss":
        return "rss", _rss_entries(root)
    if tag == "feed":
        return "atom", _atom_entries(root)
    if tag == "urlset":
        return _sitemap_entries(root)
    if tag == "sitemapindex":
        return "sitemap_index", _sitemap_index_entries(root)
    raise AdapterError(f"unrecognized feed root <{tag}>")


def _sitemap_index_entries(root) -> list[Entry]:
    out = []
    for sm in root.iter():
        if _strip_ns(sm.tag) != "sitemap":
            continue
        loc = date = None
        for ch in sm:
            t = _strip_ns(ch.tag)
            if t == "loc":
                loc = (ch.text or "").strip()
            elif t == "lastmod":
                date = ch.text
        if loc:
            out.append(Entry(loc, loc, _parse_date(date)))
    return out


def _rss_entries(root) -> list[Entry]:
    out = []
    for item in root.iter():
        if _strip_ns(item.tag) != "item":
            continue
        title = link = date = None
        for ch in item:
            t = _strip_ns(ch.tag)
            if t == "title":
                title = (ch.text or "").strip()
            elif t == "link":
                link = (ch.text or "").strip()
            elif t in ("pubdate", "date"):
                date = ch.text
        if title and link:
            out.append(Entry(title, link, _parse_date(date)))
    return out


def _atom_entries(root) -> list[Entry]:
    out = []
    for entry in root.iter():
        if _strip_ns(entry.tag) != "entry":
            continue
        title = link = date = None
        for ch in entry:
            t = _strip_ns(ch.tag)
            if t == "title":
                title = (ch.text or "").strip()
            elif t == "link" and ch.get("rel") in (None, "alternate"):
                link = ch.get("href", "").strip()
            elif t in ("published", "updated"):
                date = date or ch.text
        if title and link:
            out.append(Entry(title, link, _parse_date(date)))
    return out


def _sitemap_entries(root) -> tuple[str, list[Entry]]:
    out, has_news = [], False
    for url in root.iter():
        if _strip_ns(url.tag) != "url":
            continue
        loc = title = date = None
        for ch in url.iter():
            t = _strip_ns(ch.tag)
            if t == "loc" and loc is None:
                loc = (ch.text or "").strip()
            elif t == "title" and title is None:
                title = (ch.text or "").strip()
                has_news = True
            elif t in ("publication_date", "lastmod") and date is None:
                date = ch.text
        if loc:
            out.append(Entry(title or loc, loc, _parse_date(date)))
    return ("news_sitemap" if has_news else "sitemap"), out


def fetch_feed(url: str, timeout: float = 15.0, _depth: int = 0) -> tuple[str, list[Entry]]:
    """Fetch and parse one feed URL. A sitemap index resolves one level down,
    into its most recently modified child sitemap."""
    try:
        r = httpx.get(url, headers=UA, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError as e:
        raise AdapterError(f"feed {url} unreachable: {e}") from e
    if r.status_code != 200:
        raise AdapterError(f"feed {url} -> {r.status_code}")
    kind, entries = parse_feed(r.content)
    if kind == "sitemap_index" and entries and _depth == 0:
        newest = max(entries, key=lambda e: e.published or datetime.min.replace(tzinfo=timezone.utc))
        return fetch_feed(newest.link, timeout, _depth=1)
    return kind, entries


def fresh(entries: list[Entry], hours: float) -> list[Entry]:
    """Entries within the window, newest first. Undated entries keep feed order at the end."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    dated = sorted((e for e in entries if e.published and e.published >= cutoff),
                   key=lambda e: e.published, reverse=True)
    undated = [e for e in entries if not e.published]
    return dated + undated
