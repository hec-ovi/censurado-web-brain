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

import re
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
    "strip_source_links",
    "plan_link_strip",
    "apply_link_strip",
    "SECTION_CANONICAL",
    "plan_section_remap",
    "apply_section_remap",
]

# Legacy section values remapped to their canonical form. Mirrors the generator's
# canonicalSectionSlug, which folds these at RENDER time; this re-stores the canonical value in the
# DB so the stored section matches the pinned vocabulary (contracts.sections.SECTION_ENUM) and the
# normalize check stops flagging it.
SECTION_CANONICAL = {"economics": "misterio-y-conspiracion"}

# An inline Markdown link [text](url) that is NOT an image embed ![alt](url). The negative
# lookbehind on the "!" leaves images alone; {{widget}} markers are not [ ]( ) so they never match.
_MD_LINK = re.compile(r"(?<!\!)\[([^\]]+)\]\(([^)]+)\)")


def strip_source_links(body: str) -> tuple[str, int]:
    """Convert inline source-citation links ``[text](url)`` to their plain text (the source name),
    the retroactive form of the name-only attribution rule. Image embeds ``![alt](url)`` and widget
    markers ``{{...}}`` are left untouched. Returns ``(new_body, n_stripped)``. This exists because
    the old workflow told the model to attribute every claim as a titled link, and it filled those
    with broken/invented URLs; naming the source in prose is the correct, verifiable form."""
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        count += 1
        return m.group(1)  # keep the link text (the source name), drop the URL

    return _MD_LINK.sub(repl, body), count


def plan_link_strip(articles: list[dict]) -> list[dict]:
    """The per-article change plan: for every article whose body carries at least one inline source
    link, the stripped body and how many links it drops. Articles with none are skipped."""
    plan: list[dict] = []
    for a in articles:
        new_body, n = strip_source_links(a.get("body", ""))
        if n:
            plan.append({"slug": a["slug"], "stripped": n, "new_body": new_body})
    return plan


def plan_section_remap(articles: list[dict]) -> list[dict]:
    """The per-article change plan for the legacy-section remap: every article stored under a
    section in ``SECTION_CANONICAL`` and the canonical value it should carry. Articles already on a
    canonical section are skipped."""
    return [
        {"slug": a["slug"], "from": a["section"], "to": SECTION_CANONICAL[a["section"]]}
        for a in articles
        if a.get("section") in SECTION_CANONICAL
    ]


def apply_section_remap(plan: list[dict], *, base_url: str, read_token: str, edit_token: str) -> tuple[int, list[str]]:
    """Write each canonical section back over the operator edit lane: ``GET /articles/{slug}`` for
    the full record, then ``PUT`` it with only the section replaced. The PUT recomputes the content
    hash (section is hashed) and preserves slug + created_at, so the permalink is stable. Returns
    ``(applied_count, failures)``."""
    base = base_url.rstrip("/")
    applied = 0
    failed: list[str] = []
    for item in plan:
        try:
            got = httpx.get(
                f"{base}/articles/{item['slug']}",
                headers={"Authorization": f"Bearer {read_token}"},
                timeout=30.0,
            )
            got.raise_for_status()
            art = got.json()
            payload = {
                "title": art["title"],
                "body": art["body"],
                "author": art["author"],
                "section": item["to"],
                "topics": art.get("topics") or [],
                "published_at": art.get("published_at"),
                "metadata": art.get("metadata") or {},
            }
            put = httpx.put(
                f"{base}/articles/{item['slug']}",
                json=payload,
                headers={"Authorization": f"Bearer {edit_token}"},
                timeout=60.0,
            )
            put.raise_for_status()
            applied += 1
        except (httpx.HTTPError, KeyError) as exc:
            failed.append(f"{item['slug']}: {exc}")
    return applied, failed


def apply_link_strip(plan: list[dict], *, base_url: str, read_token: str, edit_token: str) -> tuple[int, list[str]]:
    """Write each stripped body back over the operator edit lane: ``GET /articles/{slug}`` for the
    full record, then ``PUT`` it with the body replaced and every other field unchanged. The PUT
    recomputes the content hash and preserves slug + created_at, so the permalink is stable.
    Returns ``(applied_count, failures)``."""
    base = base_url.rstrip("/")
    applied = 0
    failed: list[str] = []
    for item in plan:
        try:
            got = httpx.get(
                f"{base}/articles/{item['slug']}",
                headers={"Authorization": f"Bearer {read_token}"},
                timeout=30.0,
            )
            got.raise_for_status()
            art = got.json()
            payload = {
                "title": art["title"],
                "body": item["new_body"],
                "author": art["author"],
                "section": art["section"],
                "topics": art.get("topics") or [],
                "published_at": art.get("published_at"),
                "metadata": art.get("metadata") or {},
            }
            put = httpx.put(
                f"{base}/articles/{item['slug']}",
                json=payload,
                headers={"Authorization": f"Bearer {edit_token}"},
                timeout=60.0,
            )
            put.raise_for_status()
            applied += 1
        except (httpx.HTTPError, KeyError) as exc:
            failed.append(f"{item['slug']}: {exc}")
    return applied, failed

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
