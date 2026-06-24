"""Slug derivation: a faithful port of the platform's ``domain.Slugify`` and the
``NewArticle`` slug fallback chain.

The platform dedups slugs WITHIN a batch on the DERIVED permalink slug, not on the
raw ``slug`` field: ``internal/domain/article.go`` computes

    slug := Slugify(in.Slug)            // Slugify("") == ""
    if slug == "" { slug = Slugify(title) }
    if slug == "" { slug = hash[:12] }   // hash = ContentHash(title, body, author, section)

where

    func Slugify(s string) string {
        s = strings.ToLower(strings.TrimSpace(s))
        s = regexp.MustCompile(`[^a-z0-9]+`).ReplaceAllString(s, "-")
        return strings.Trim(s, "-")
    }

The brain reproduces this so its batch slug de-collision (see
``newsroom.publish.service.publish_batch_assignments``) can predict the final slug the
platform will assign and avoid a within-batch duplicate-slug rejection that would 422
the whole atomic batch. Any Unicode lowercasing difference between Go and Python is
erased by the ``[^a-z0-9]+`` replacement, so the slug output is byte-equal for the
fields a journalist writes.
"""

from __future__ import annotations

import re

from newsroom.contracts.hashing import content_hash

__all__ = ["slugify", "derive_slug"]

_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Lowercase, trim, replace every non-alphanumeric run with a single hyphen, and
    strip leading/trailing hyphens. Mirrors the platform's ``Slugify``."""
    out = _NON_ALNUM.sub("-", text.strip().lower())
    return out.strip("-")


def derive_slug(*, title: str, body: str, author: str, section: str, slug: str | None = None) -> str:
    """The permalink slug the platform will assign for this article: the slugified
    explicit slug, else the slugified title, else the first 12 chars of the content
    hash. Mirrors ``domain.NewArticle``'s fallback chain."""
    derived = slugify(slug or "")
    if not derived:
        derived = slugify(title)
    if not derived:
        derived = content_hash(title, body, author, section)[:12]
    return derived
