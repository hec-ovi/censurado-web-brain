"""Inline body widgets: connect a FINISHED article to the rich cards the static
generator renders (X/tweet, YouTube, related-article), without spending a model token.

This is a deterministic, best-effort post-finalize stage (B.3, the widget step). It
emits three kinds of inline body markers that ``censurado-web``'s generator expands
into trusted, sanitizer-proof HTML:

  * ``{{tweet:<id>}}``       an X-style card from a snapshot in ``metadata.tweets[]``
  * ``{{video:<id>}}``       a YouTube embed, checked live and recorded in
                             ``metadata.media_checks{}``
  * ``{{relacionado:<slug>}}`` a "Ver artículo relacionado" card for a prior article

Two passes, both grounded in real data so a widget is never invented:

  1. SOCIAL EMBEDS from the grounding LEDGER. Every cited tweet / YouTube URL the
     journalist actually used is captured KEYLESS (fxtwitter / YouTube oEmbed). A
     marker is emitted ONLY when a real snapshot is captured (a live tweet, an
     embeddable video), so an emitted widget always resolves on the generator; a URL
     that fails to capture simply gets no card. A source going dead later (a deleted
     tweet, a pulled video) is detected by the out-of-band recheck sweep
     (``newsroom.embeds.recheck``), never guessed here.
  2. ONE RELATED CARD from prior COVERAGE. The single best fingerprint match (the same
     fingerprint the dedup triage uses) becomes a ``{{relacionado:slug}}`` mid-body. The
     generator self-drops it if the slug resolves to this same article or a forward
     reference, so a wrong guess is harmless.

The markers ride in ``article.body`` (so they are part of the article's identity and the
content hash the caller computes next); the snapshots ride in ``metadata`` (outside it).
This stage NEVER raises and NEVER drops the article: any capture or placement failure
just leaves that widget off, exactly like the art-director image step. No model call, no
output-length cap: the body is rewritten by deterministic insertion only.
"""

from __future__ import annotations

import re

from newsroom.embeds.fetch import Fetch, default_fetch
from newsroom.embeds.twitter import capture_tweet, parse_tweet_ref
from newsroom.embeds.youtube import check_youtube, parse_youtube_id
from newsroom.research.ledger import Ledger

__all__ = ["attach_widgets", "select_related_slug"]

# A story block must share at least this many distinctive tokens with a widget's subject
# before we anchor the card to it; below that the overlap is noise, and we fall back to a
# neutral position rather than parking the card next to an unrelated paragraph.
_MIN_ANCHOR_OVERLAP = 2
# A related card is emitted only when the prior article's fingerprint overlaps the new
# one's by at least this Jaccard score: enough shared subject to be a genuine "related
# read", not a stray token collision.
_RELATED_MIN_SCORE = 0.12

_BLANKLINE = re.compile(r"\n[ \t]*\n")
_WORD = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    """Distinctive word tokens (lowercase alphanumerics longer than two chars)."""
    return {w for w in _WORD.findall((text or "").lower()) if len(w) > 2}


def _blocks(body: str) -> list[str]:
    """The body's paragraph blocks (split on blank lines), empties dropped so an index
    always points at real prose."""
    return [b for b in _BLANKLINE.split(body or "") if b.strip()]


def _anchor_index(blocks: list[str], *, url: str = "", subject: str = "") -> int | None:
    """The block to drop a widget AFTER: the block that cites ``url`` if any block does
    (the embed sits exactly where the journalist referenced the source), else the block
    sharing the most distinctive tokens with ``subject`` when that overlap clears the
    noise floor. None when nothing anchors (the caller picks a neutral fallback)."""
    if url:
        for i, b in enumerate(blocks):
            if url in b:
                return i
    subj = _tokens(subject)
    if subj:
        best_i, best_overlap = None, 0
        for i, b in enumerate(blocks):
            overlap = len(subj & _tokens(b))
            if overlap > best_overlap:
                best_i, best_overlap = i, overlap
        if best_i is not None and best_overlap >= _MIN_ANCHOR_OVERLAP:
            return best_i
    return None


def _insert(body: str, marker: str, *, url: str = "", subject: str = "", fallback: str) -> str:
    """Return ``body`` with ``marker`` placed on its own block. Anchor it after the
    citing/best-overlap block; with no anchor use ``fallback``: ``"end"`` (after the last
    block, for an embed whose citation was not found in the prose) or ``"middle"``
    (mid-body, for a related card with no strong textual hook)."""
    blocks = _blocks(body)
    if not blocks:
        return marker
    idx = _anchor_index(blocks, url=url, subject=subject)
    if idx is None:
        idx = len(blocks) - 1 if fallback == "end" else max(0, (len(blocks) - 1) // 2)
    blocks.insert(idx + 1, marker)
    return "\n\n".join(blocks)


def _safe(thunk):
    """Run a capture thunk, swallowing any error to None: a flaky network or a malformed
    payload leaves the widget off, it never breaks finalize."""
    try:
        return thunk()
    except Exception:
        return None


def _subject_tokens(headline: str, terms) -> set[str]:
    """A story's token signature: the headline's distinctive tokens plus each topic/entity
    folded in BOTH whole (a multi-word entity matches exactly) and tokenized (a partial
    mention still overlaps). A self-contained mirror of the coverage fingerprint's SHAPE,
    deliberately kept here rather than imported from the manager: the dependency direction
    is one-way (manager -> pipeline, never the reverse), and a related CARD is a softer
    presentation hint than the dedup triage, so it does not need the identical signature."""
    toks = _tokens(headline)
    for term in terms or []:
        t = str(term).strip().lower()
        if not t:
            continue
        toks.add(t)
        toks |= _tokens(t)
    return toks


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard overlap of two token sets in [0, 1]; 0 when either side is empty."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def select_related_slug(article, related_coverage, entities, *, min_score: float = _RELATED_MIN_SCORE):
    """The slug of the single best prior-coverage match for this finished ``article``, or
    None when nothing clears ``min_score`` (or no coverage was supplied). Matches the new
    article's title/topics/entities against each coverage item's headline/topics/entities
    by token-set Jaccard."""
    if not related_coverage:
        return None
    fp = _subject_tokens(
        getattr(article, "title", "") or "",
        list(getattr(article, "topics", None) or []) + list(entities or []),
    )
    if not fp:
        return None
    best_slug, best_score = None, 0.0
    for item in related_coverage:
        slug = getattr(item, "slug", None)
        if not slug:
            continue
        item_fp = _subject_tokens(
            getattr(item, "headline", "") or "",
            list(getattr(item, "topics", None) or []) + list(getattr(item, "entities", None) or []),
        )
        score = _jaccard(fp, item_fp)
        if score > best_score:
            best_slug, best_score = slug, score
    return best_slug if best_score >= min_score else None


def _related_subject(related_coverage, slug: str) -> str:
    """Headline + topics of the related item, for anchoring its card near a matching
    paragraph."""
    for item in related_coverage or []:
        if getattr(item, "slug", None) == slug:
            parts = [getattr(item, "headline", "") or ""]
            parts += [str(t) for t in (getattr(item, "topics", None) or [])]
            return " ".join(parts)
    return ""


def attach_widgets(article, *, ledger: Ledger, fetch: Fetch | None = None,
                   related_coverage=None, entities=None) -> None:
    """Emit inline body widgets into a finished ``article``, mutating it in place.

    Captures cited tweets / YouTube videos from ``ledger`` (keyless) and one related
    card from ``related_coverage``, writing markers into ``article.body`` and snapshots
    into ``article.metadata``. See the module docstring for the grounding rules. NEVER
    raises: a finished article is published with or without its widgets."""
    try:
        _attach(article, ledger=ledger, fetch=fetch or default_fetch,
                related_coverage=related_coverage, entities=entities)
    except Exception:
        return


def _attach(article, *, ledger: Ledger, fetch: Fetch, related_coverage, entities) -> None:
    metadata = dict(getattr(article, "metadata", None) or {})
    body = getattr(article, "body", "") or ""

    tweets = list(metadata.get("tweets") or [])
    media_checks = dict(metadata.get("media_checks") or {})
    seen_tweets = {str(t.get("id")) for t in tweets if isinstance(t, dict)}
    seen_videos = set(media_checks.keys())

    # Pass 1: social embeds, one per distinct cited tweet / embeddable video. A marker is
    # emitted together with its snapshot so the generator always has something to render.
    for row in ledger.rows:
        url = (row.url or "").strip()
        if not url:
            continue
        if parse_tweet_ref(url):
            snap = _safe(lambda u=url: capture_tweet(u, fetch=fetch))
            if snap and str(snap.get("id")) not in seen_tweets:
                tid = str(snap["id"])
                tweets.append(snap)
                seen_tweets.add(tid)
                body = _insert(body, "{{tweet:" + tid + "}}", url=url,
                               subject=(snap.get("text") or row.claim), fallback="end")
        elif parse_youtube_id(url):
            snap = _safe(lambda u=url: check_youtube(u, fetch=fetch))
            if snap and snap.get("available") and str(snap.get("id")) not in seen_videos:
                vid = str(snap["id"])
                media_checks[vid] = snap
                seen_videos.add(vid)
                body = _insert(body, "{{video:" + vid + "}}", url=url,
                               subject=(snap.get("title") or row.claim), fallback="end")

    if tweets:
        metadata["tweets"] = tweets
    if media_checks:
        metadata["media_checks"] = media_checks

    # Pass 2: one related card from prior coverage, mid-body when it has no textual hook.
    slug = select_related_slug(article, related_coverage, entities)
    if slug:
        body = _insert(body, "{{relacionado:" + slug + "}}",
                       subject=_related_subject(related_coverage, slug), fallback="middle")

    article.body = body
    article.metadata = metadata
