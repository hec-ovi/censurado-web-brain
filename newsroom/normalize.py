"""A single, contract-validated pass over the WHOLE corpus (authors + articles).

Isolating the author and article WRITE contracts (``contracts.author.AuthorInput`` and
``contracts.article.PublishArticleInput``, each mirroring a vendored JSON schema) buys one thing:
a whole-DB change can be reasoned about in ONE place. This module is that place. It iterates every
author and every article, projects each onto its write shape, and validates it against the isolated
contract. It is the entry point behind ``censurado-brain normalize``.

By default it is a DRY-RUN conformance report: it reads the corpus and lists every record whose
current write projection would not satisfy its contract, so a schema tightening or a data drift is
caught before it bites a re-publish (e.g. a legacy section value that is no longer in the pinned
vocabulary, or an author missing a now-required field). Rewriting the corpus is the same loop with a
transform applied before each record is re-sent over the operator write lane; that transform is a
deliberate, separate step, so the read-only pass is always safe to run.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
from pydantic import ValidationError

from newsroom.contracts.article import PublishArticleInput
from newsroom.contracts.author import AuthorInput, author_write_payload

__all__ = [
    "fetch_authors",
    "fetch_articles_full",
    "article_write_payload",
    "validate_author",
    "validate_article",
    "normalize_corpus",
]

# The article write-shape keys (mirrors PublishArticleInput). A read record also carries
# server-owned fields (id, content_hash, created_at, slug, card_type, ...) that the strict write
# envelope rejects, so a read record is projected onto these before validation / re-write.
_ARTICLE_WRITE_FIELDS = ("title", "body", "author", "section", "topics", "slug", "published_at", "metadata")


def article_write_payload(record: dict) -> dict:
    """Project a full article READ record onto just the write-shape keys (dropping None values so an
    absent optional does not trip a type check)."""
    return {k: record[k] for k in _ARTICLE_WRITE_FIELDS if record.get(k) is not None}


def fetch_authors(base_url: str, token: str) -> list[dict]:
    """Every author over ``GET /authors``."""
    resp = httpx.get(
        f"{base_url.rstrip('/')}/authors",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    resp.raise_for_status()
    return list(resp.json().get("authors", []))


def fetch_articles_full(base_url: str, token: str, *, limit: int = 1000) -> list[dict]:
    """Every article as a FULL record. The list endpoint is body-less, so each slug is re-read over
    ``GET /articles/{slug}`` to carry the body the write contract requires."""
    base = base_url.rstrip("/")
    resp = httpx.get(
        f"{base}/articles",
        params={"limit": limit},
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    resp.raise_for_status()
    out: list[dict] = []
    for item in resp.json().get("articles", []):
        got = httpx.get(
            f"{base}/articles/{item['slug']}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )
        got.raise_for_status()
        out.append(got.json())
    return out


def _errs(exc: ValidationError) -> list[str]:
    return [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]


def validate_author(record: dict) -> list[str]:
    """The contract violations for one author's write projection, or [] if it conforms."""
    try:
        AuthorInput(**author_write_payload(record))
        return []
    except ValidationError as exc:
        return _errs(exc)


def validate_article(record: dict) -> list[str]:
    """The contract violations for one article's write projection, or [] if it conforms."""
    try:
        PublishArticleInput(**article_write_payload(record))
        return []
    except ValidationError as exc:
        return _errs(exc)


def normalize_corpus(
    *,
    fetch_authors_fn: Callable[[], list[dict]],
    fetch_articles_fn: Callable[[], list[dict]],
) -> dict:
    """One pass over the whole DB: validate every author and every article against its isolated
    write contract. Returns a structured verdict; the fetchers are injected so a test (or a caller
    with its own client) drives it without a network."""
    authors = fetch_authors_fn()
    author_violations = [
        {"handle": rec.get("handle"), "errors": errs}
        for rec in authors
        if (errs := validate_author(rec))
    ]
    articles = fetch_articles_fn()
    article_violations = [
        {"slug": rec.get("slug"), "errors": errs}
        for rec in articles
        if (errs := validate_article(rec))
    ]
    return {
        "authors": {"checked": len(authors), "violations": author_violations},
        "articles": {"checked": len(articles), "violations": article_violations},
        "ok": not author_violations and not article_violations,
    }
