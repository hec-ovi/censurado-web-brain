"""Agentic topic cleanse: cluster synonymous / overlapping topic tags into a small
canonical set and remap existing articles to it, so the tag list stops sprawling.

Topics live in the backend (the ``article_topics`` join), not the brain. This pass
reads the corpus over the read API, asks the model to cluster the DISTINCT tags into
canonical labels (the agentic step), then remaps each changed article in place over
the operator edit lane (``PUT /articles/{slug}``, which needs the ``admin:write``
scope). The article's body and its four hashed fields (title/body/author/section)
plus ``published_at`` are sent back UNCHANGED, so the permalink, the content hash,
and the publish date are all stable; only the topic set moves.

The agentic decision (``cluster_topics``) and the remap arithmetic (``remap_plan``)
are pure given the model reply, so they are unit-testable without a network; the
``--apply`` step is a thin HTTP layer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx

from newsroom.inference.adapter import ChatRequest, chat
from newsroom.inference.provider import ProviderConfig

__all__ = [
    "ArticleTopics",
    "TopicRemap",
    "CleanseResult",
    "collect_topics",
    "cluster_topics",
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


_CLUSTER_PROMPT = (
    "You are normalizing the topic tags of a Spanish-language news site. The tag list "
    "below has grown unbounded: many tags are synonyms, near-duplicates, or over-specific "
    "variants of the same idea. Merge them into the SMALLEST set of clear, general, "
    "reusable canonical topics (in Spanish, lowercase). Map EVERY input tag to exactly one "
    "canonical tag. Prefer broad reusable topics over narrow one-offs (for example map "
    "'inteligencia-artificial', 'ia', 'modelos-de-frontera' and 'modelos-abiertos' all to "
    "'inteligencia artificial'). Do NOT invent a canonical topic that no input maps to, and "
    "do NOT force unrelated tags together. Return ONLY a JSON object mapping each input tag "
    "(verbatim, as the key) to its canonical tag (the value). No prose, no code fence.\n\n"
    "Tags:\n"
)


def cluster_topics(topics: list[str], *, cfg: ProviderConfig | None = None) -> dict[str, str]:
    """Ask the model to cluster the tags into canonical labels and return a
    ``{raw_tag: canonical_tag}`` map. Every input tag is present in the result; a tag
    the model omits or blanks maps to itself (identity), so the cleanse never loses a
    tag to a parse gap. No output-length cap is set."""
    topics = [t for t in topics if t]
    if not topics:
        return {}
    prompt = _CLUSTER_PROMPT + "\n".join(f"- {t}" for t in topics)
    resp = chat(ChatRequest(messages=[{"role": "user", "content": prompt}], temperature=0.1), cfg=cfg)
    mapping = _parse_map(resp.content)
    out: dict[str, str] = {}
    for tag in topics:
        canon = (mapping.get(tag) or "").strip()
        out[tag] = canon or tag
    return out


def _parse_map(text: str) -> dict[str, str]:
    """Extract the first ``{...}`` JSON object from the reply (tolerating a code fence
    or stray prose) and keep only string->string entries; ``{}`` on any failure."""
    text = (text or "").strip()
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j <= i:
        return {}
    try:
        data = json.loads(text[i : j + 1])
    except (json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}


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
