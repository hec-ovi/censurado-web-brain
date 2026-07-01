"""Slug derivation: a faithful port of the platform's ``domain.Slugify`` and the
``NewArticle`` slug fallback chain.

The platform dedups slugs WITHIN a batch on the DERIVED permalink slug, not on the
raw ``slug`` field: ``domain/article.go`` computes

    slug := Slugify(in.Slug)            // Slugify("") == ""
    if slug == "" { slug = Slugify(title) }
    if slug == "" { slug = hash[:12] }   // hash = ContentHash(title, body, author, section)

where ``Slugify`` lowercases, TRANSLITERATES the common Latin (mostly Spanish) accents
to ASCII (a->a, n->n, u->u, ...), replaces every remaining non ``[a-z0-9]`` run with a
single hyphen, and trims leading/trailing hyphens. The transliteration is load-bearing:
"Politica Economica" (with accents) becomes ``politica-economica`` on the platform, NOT
``pol-tica-econ-mica``. Any accented letter not in the fold, and any other non-ASCII
character, is dropped by the ``[^a-z0-9]+`` pass, exactly like Go.

The brain reproduces this byte-for-byte so a caller can predict the final slug the
platform will assign (e.g. to de-collide a within-batch duplicate-slug rejection that
would 422 the whole atomic batch), and so it is the ONE canonical slugify the persona
store and the test fake reuse. The table below mirrors ``domain.slugTranslit``.
"""

from __future__ import annotations

import re

from newsroom.contracts.hashing import content_hash

__all__ = ["slugify", "derive_slug"]

# Mirror of the Go platform's domain.slugTranslit: fold the common Latin (mostly
# Spanish) accented letters to ASCII so a derived slug matches the permalink the
# platform assigns. Keyed on the lowercase form (slugify lowercases first).
_TRANSLIT = str.maketrans({
    "á": "a", "à": "a", "ä": "a", "â": "a", "ã": "a", "å": "a",
    "é": "e", "è": "e", "ë": "e", "ê": "e",
    "í": "i", "ì": "i", "ï": "i", "î": "i",
    "ó": "o", "ò": "o", "ö": "o", "ô": "o", "õ": "o",
    "ú": "u", "ù": "u", "ü": "u", "û": "u",
    "ñ": "n", "ç": "c", "ý": "y", "ÿ": "y",
})
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Lowercase, transliterate the common Latin accents to ASCII, replace every other
    non-alphanumeric run with a single hyphen, and strip leading/trailing hyphens. A
    byte-for-byte mirror of the platform's ``domain.Slugify`` (lower, translit, then the
    ``[^a-z0-9]+`` replacement), so "Munoz" or "Pinera" (accented) slug the same here as
    on the platform."""
    out = _NON_ALNUM.sub("-", text.strip().lower().translate(_TRANSLIT))
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
