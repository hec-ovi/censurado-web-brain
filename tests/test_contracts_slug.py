"""The slug port must match the platform's domain.Slugify byte-for-byte.

contracts/slug.py predicts the permalink the Go platform (domain.Slugify) will assign,
and it is now the ONE canonical slugify the persona store and the test fake reuse. The
platform transliterates the common Latin accents to ASCII (a->a, n->n, u->u, ...) before
collapsing the rest to hyphens, so an accented title like "Politica Economica" slugs to
"politica-economica", never "pol-tica-econ-mica". These golden cases pin that behavior;
the expected values are exactly what domain.Slugify (censurado-web-backend/domain) and the
panel's slugify.js produce, so the three implementations stay in agreement.
"""

from __future__ import annotations

import pytest

from newsroom.contracts.hashing import content_hash
from newsroom.contracts.slug import derive_slug, slugify


@pytest.mark.parametrize(
    "text, expected",
    [
        # The accent fold: the whole point of the port.
        ("Política Económica", "politica-economica"),
        ("Piñera", "pinera"),
        ("José Müller", "jose-muller"),
        ("Ñoño", "nono"),
        ("España", "espana"),
        ("Café con Leche", "cafe-con-leche"),
        ("Ámbito Financiero", "ambito-financiero"),
        ("Über Alles", "uber-alles"),
        # Non-folded, non-ASCII characters are dropped (like Go), not kept.
        ("Привет Mundo", "mundo"),
        # ASCII punctuation and whitespace collapse to single hyphens, trimmed.
        ("  Hola, Mundo!  ", "hola-mundo"),
        ("already-a-slug", "already-a-slug"),
        ("Mara Vance", "mara-vance"),
        # Nothing usable remains.
        ("", ""),
        ("!!!", ""),
        ("   ", ""),
    ],
)
def test_slugify_matches_the_platform(text, expected):
    assert slugify(text) == expected


def test_slugify_is_idempotent_on_its_own_output():
    for raw in ("José Müller", "Política Económica", "Hola, Mundo!"):
        once = slugify(raw)
        assert slugify(once) == once


def test_derive_slug_prefers_explicit_then_title_then_hash():
    # Explicit slug wins, transliterated.
    assert derive_slug(
        title="ignored", body="b", author="a", section="mundo", slug="Mi Título Exácto"
    ) == "mi-titulo-exacto"
    # No explicit slug: fall back to the transliterated title.
    assert derive_slug(
        title="Política", body="b", author="a", section="mundo"
    ) == "politica"
    # Nothing sluggable: fall back to the content hash prefix (12 chars).
    only_hash = derive_slug(title="!!!", body="b", author="a", section="mundo", slug="   ")
    assert only_hash == content_hash("!!!", "b", "a", "mundo")[:12]
    assert len(only_hash) == 12
