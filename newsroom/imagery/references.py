"""Collect candidate reference images from the article's research sources.

The art director can steer a FLUX.2 illustration with imagery from the same sources
the journalist used. The research ledger holds the source URLs but no images, so this
module fetches the top sources and pulls their social-preview image (``og:image`` /
``twitter:image``) - the one image a page advertises as its representative picture.

This is deliberately lightweight and best-effort: a bounded number of sources, a
short timeout, no HTML parser dependency (a small meta-tag scan), and every failure
degrades to "no reference from this source" rather than raising. The fetched page is
untrusted input, so only the image URL is taken (never any page text as an
instruction), and only ``http(s)`` image URLs are kept.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx

from newsroom.research.ledger import Ledger

__all__ = ["ReferenceImage", "collect_reference_images"]

# Match a <meta ...> tag, allowing a literal '>' inside a quoted attribute value (a
# greedy [^>]* would truncate the tag at the first '>' and drop the content attribute).
_META = re.compile(r"""<meta\b(?:"[^"]*"|'[^']*'|[^>])*>""", re.IGNORECASE)
_ATTR = re.compile(r"""(\w[\w:-]*)\s*=\s*("([^"]*)"|'([^']*)')""")
_HEAD_END = re.compile(r"</head>", re.IGNORECASE)


@dataclass(frozen=True)
class ReferenceImage:
    """A candidate reference image lifted from a source page."""

    image_url: str   # absolute http(s) URL of the source's preview image
    source_url: str  # the page it came from (a ledger row)
    context: str     # the ledger claim/snippet, so the art director knows what it depicts


def collect_reference_images(
    ledger: Ledger, *, limit: int = 2, timeout: float = 8.0,
    fetch: "callable | None" = None,
) -> list[ReferenceImage]:
    """Return up to ``limit`` reference images from the ledger's top sources.

    ``fetch`` (page_url -> html_text, or None on failure) is injectable so a test
    drives this without network. Stops once ``limit`` images are found; never raises."""
    if limit <= 0:
        return []
    getter = fetch or _default_fetch(timeout)
    found: list[ReferenceImage] = []
    seen: set[str] = set()
    for row in ledger.rows:
        if len(found) >= limit:
            break
        url = (row.url or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        try:
            html = getter(url)
        except Exception:
            html = None
        if not html:
            continue
        image_url = _extract_preview_image(html, base_url=url)
        if image_url:
            context = (row.claim or row.snippet or "").strip()
            found.append(ReferenceImage(image_url=image_url, source_url=url, context=context))
    return found


def _default_fetch(timeout: float):
    def fetch(url: str) -> str | None:
        headers = {"User-Agent": "censurado-web-brain/art-director (+https://github.com/hec-ovi)"}
        resp = httpx.get(url, timeout=timeout, follow_redirects=True, headers=headers)
        if resp.status_code != 200:
            return None
        ctype = resp.headers.get("content-type", "")
        if "html" not in ctype.lower():
            return None
        # Only the <head> carries og/twitter meta; cap the scanned text either way.
        head = _HEAD_END.split(resp.text, 1)[0]
        return head[:200_000]

    return fetch


def _extract_preview_image(html: str, *, base_url: str) -> str | None:
    """Scan <meta> tags for og:image / twitter:image and return an absolute URL."""
    candidates: dict[str, str] = {}
    for tag in _META.findall(html):
        attrs = {m.group(1).lower(): (m.group(3) or m.group(4) or "") for m in _ATTR.finditer(tag)}
        key = attrs.get("property") or attrs.get("name") or ""
        key = key.lower()
        content = attrs.get("content", "").strip()
        if key in ("og:image", "og:image:url", "og:image:secure_url", "twitter:image",
                   "twitter:image:src") and content:
            candidates.setdefault(key, content)
    for key in ("og:image:secure_url", "og:image", "og:image:url", "twitter:image",
                "twitter:image:src"):
        raw = candidates.get(key)
        if not raw:
            continue
        absolute = urljoin(base_url, raw)
        if absolute.lower().startswith(("http://", "https://")):
            return absolute
    return None
