"""The embed re-check sweep: revalidate every stored tweet/youtube snapshot and write
back the ones whose availability changed.

A captured snapshot lives in an article's ``metadata`` (``tweets[]`` and
``media_checks{}``). Sources rot: a tweet gets deleted, a video gets pulled. This pass
re-queries each snapshot and updates only the availability flags (a tweet's ``erased``,
a video's ``available``), KEEPING all captured text so a dead source still renders its
archived copy. ``recheck_metadata`` is the pure transform (unit-testable with a fake
fetch); ``apply_rechecks`` is the thin HTTP layer that reads each article over the read
API and, when its metadata changed, writes it back over the operator edit lane
(``PUT /articles/{slug}`` with the four hashed fields unchanged, so the permalink and
content hash stay stable), mirroring the topic-cleanse apply.
"""

from __future__ import annotations

import httpx

from .fetch import Fetch, default_fetch
from .twitter import recheck_tweet
from .youtube import check_youtube

__all__ = ["recheck_metadata", "fetch_article_slugs", "apply_rechecks"]


def recheck_metadata(metadata: dict | None, *, fetch: Fetch | None = None) -> tuple[dict, bool]:
    """Revalidate the ``tweets[]`` and ``media_checks{}`` in one article's metadata.
    Returns ``(new_metadata, changed)``. Tweets keep their captured text; only ``erased``
    moves. Each ``media_checks[id].available`` is refreshed (and the title backfilled when
    the video is live). ``changed`` is True iff any availability flag flipped."""
    fetch = fetch or default_fetch
    meta = dict(metadata or {})
    changed = False

    tweets = meta.get("tweets")
    if isinstance(tweets, list):
        out = []
        for t in tweets:
            if isinstance(t, dict):
                rechecked = recheck_tweet(t, fetch=fetch)
                if bool(rechecked.get("erased")) != bool(t.get("erased", False)):
                    changed = True
                out.append(rechecked)
            else:
                out.append(t)
        meta["tweets"] = out

    checks = meta.get("media_checks")
    if isinstance(checks, dict):
        out_checks = {}
        for vid, entry in checks.items():
            prev = bool(entry.get("available")) if isinstance(entry, dict) else True
            snap = check_youtube(str(vid), fetch=fetch)
            avail = bool(snap and snap.get("available"))
            new_entry = dict(entry) if isinstance(entry, dict) else {}
            new_entry["available"] = avail
            if snap and snap.get("title"):
                new_entry["title"] = snap["title"]
            out_checks[vid] = new_entry
            if avail != prev:
                changed = True
        meta["media_checks"] = out_checks

    return meta, changed


def fetch_article_slugs(base_url: str, token: str, *, limit: int = 1000) -> list[str]:
    """The slugs of every published article, over ``GET /articles`` (the body-less list)."""
    resp = httpx.get(
        f"{base_url.rstrip('/')}/articles",
        params={"limit": limit},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    resp.raise_for_status()
    return [item["slug"] for item in resp.json().get("articles", []) if item.get("slug")]


def apply_rechecks(
    slugs: list[str],
    *,
    base_url: str,
    read_token: str,
    edit_token: str,
    fetch: Fetch | None = None,
    apply: bool = True,
) -> tuple[int, int, list[str]]:
    """Recheck each article's embeds and, when ``apply`` and something changed, PUT the
    article back with refreshed availability flags. ``GET /articles/{slug}`` for the full
    body, ``recheck_metadata`` over the network ``fetch``, then ``PUT /articles/{slug}``
    with the same title/body/author/section/published_at and the new metadata. Returns
    ``(changed_count, applied_count, failures)``. With ``apply=False`` it is a dry run
    that only counts the articles that would change."""
    base = base_url.rstrip("/")
    fetch = fetch or default_fetch
    changed_count = 0
    applied = 0
    failed: list[str] = []
    for slug in slugs:
        try:
            got = httpx.get(
                f"{base}/articles/{slug}",
                headers={"Authorization": f"Bearer {read_token}"},
                timeout=30.0,
            )
            got.raise_for_status()
            art = got.json()
            new_meta, changed = recheck_metadata(art.get("metadata") or {}, fetch=fetch)
            if not changed:
                continue
            changed_count += 1
            if not apply:
                continue
            payload = {
                "title": art["title"],
                "body": art["body"],
                "author": art["author"],
                "section": art["section"],
                "topics": art.get("topics") or [],
                "published_at": art.get("published_at"),
                "metadata": new_meta,
            }
            put = httpx.put(
                f"{base}/articles/{slug}",
                json=payload,
                headers={"Authorization": f"Bearer {edit_token}"},
                timeout=60.0,
            )
            put.raise_for_status()
            applied += 1
        except (httpx.HTTPError, KeyError) as exc:
            failed.append(f"{slug}: {exc}")
    return changed_count, applied, failed
