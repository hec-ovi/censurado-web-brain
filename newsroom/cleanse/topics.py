"""Topic cleanse: remap synonymous / overlapping topic tags onto a small canonical
set so the tag list stops sprawling.

Topics live in the backend (the ``article_topics`` join), not the brain. This pass
reads the corpus over the read API, takes a canonical ``{raw_tag: canonical_tag}`` map
supplied by the operator (a CLI agent decides the clustering and hands it in via
``--map-file``), then remaps each changed article in place over the operator edit lane
(``PUT /articles/{slug}``, which needs the ``admin:write`` scope). The article's body
and its four hashed fields (title/body/author/section) plus ``published_at`` are sent
back UNCHANGED, so the permalink, the content hash, and the publish date are all stable;
only the topic set moves.

The remap arithmetic (``remap_plan``) is pure given the canonical map, so it is
unit-testable without a network; the ``--apply`` step is a thin HTTP layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

__all__ = [
    "ArticleTopics",
    "TopicRemap",
    "CleanseResult",
    "collect_topics",
    "remap_plan",
    "fetch_articles",
    "apply_remap",
]


@dataclass
class ArticleTopics:
    """The minimal per-article view the cleanse needs to plan: slug + current tags."""

    slug: str
    topics: list[str]


@dataclass
class TopicRemap:
    """One article whose tag set changes under the canonical map."""

    slug: str
    before: list[str]
    after: list[str]


@dataclass
class CleanseResult:
    canonical: dict  # raw tag -> canonical tag
    plan: list[TopicRemap]
    applied: int = 0
    failed: list[str] = field(default_factory=list)


def collect_topics(articles: list[ArticleTopics]) -> list[str]:
    """The distinct tags across the corpus, in stable (sorted) order."""
    seen: set[str] = set()
    out: list[str] = []
    for a in articles:
        for raw in a.topics:
            tag = (raw or "").strip()
            if tag and tag not in seen:
                seen.add(tag)
                out.append(tag)
    return sorted(out)


def _normalize(topics: list[str], canon: dict[str, str]) -> list[str]:
    """Map a tag list through the canonical map, dropping blanks and deduping while
    preserving first-seen order."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in topics:
        tag = (canon.get(raw, raw) or raw).strip()
        if tag and tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out


def remap_plan(articles: list[ArticleTopics], canon: dict[str, str]) -> list[TopicRemap]:
    """The set of articles whose tag list actually changes under the canonical map."""
    plan: list[TopicRemap] = []
    for a in articles:
        after = _normalize(a.topics, canon)
        if after != list(a.topics):
            plan.append(TopicRemap(slug=a.slug, before=list(a.topics), after=after))
    return plan


def fetch_articles(base_url: str, token: str, *, limit: int = 1000) -> list[ArticleTopics]:
    """Read the corpus over ``GET /articles`` (body-less list) as the cleanse view."""
    resp = httpx.get(
        f"{base_url.rstrip('/')}/articles",
        params={"limit": limit},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return [
        ArticleTopics(slug=item["slug"], topics=list(item.get("topics") or []))
        for item in data.get("articles", [])
    ]


def apply_remap(
    plan: list[TopicRemap], *, base_url: str, read_token: str, edit_token: str
) -> tuple[int, list[str]]:
    """Apply each remap over the operator edit lane: ``GET /articles/{slug}`` for the
    full article (it carries the body), then ``PUT /articles/{slug}`` with the same
    title/body/author/section/published_at/metadata and the new canonical topics. The
    four hashed fields and the date are unchanged, so the permalink and content hash
    stay stable. Returns (applied_count, failures)."""
    base = base_url.rstrip("/")
    applied = 0
    failed: list[str] = []
    for rm in plan:
        try:
            got = httpx.get(
                f"{base}/articles/{rm.slug}",
                headers={"Authorization": f"Bearer {read_token}"},
                timeout=30.0,
            )
            got.raise_for_status()
            art = got.json()
            payload = {
                "title": art["title"],
                "body": art["body"],
                "author": art["author"],
                "section": art["section"],
                "topics": rm.after,
                "published_at": art.get("published_at"),
                "metadata": art.get("metadata") or {},
            }
            put = httpx.put(
                f"{base}/articles/{rm.slug}",
                json=payload,
                headers={"Authorization": f"Bearer {edit_token}"},
                timeout=60.0,
            )
            put.raise_for_status()
            applied += 1
        except (httpx.HTTPError, KeyError) as exc:
            failed.append(f"{rm.slug}: {exc}")
    return applied, failed
