"""Loader for the vendored platform article schema.

The harness does NOT read the platform's schema file across repos (that would be
filesystem coupling and break the isolated-repo claim) and does NOT keep an
unmanaged copy (that drifts silently). It VENDORS a pinned copy under a contract
version directory, anchored to the schema's ``$id``. A CI drift test
(``tests/test_schema_drift.py``) asserts the vendored copy still matches the live
platform schema and fails loudly on drift, so the seam is a governed, versioned
artifact rather than a hidden copy.
"""

from __future__ import annotations

import json
from pathlib import Path

__all__ = [
    "CONTRACT_VERSION",
    "SCHEMA_ID",
    "ARTICLE_SCHEMA_PATH",
    "load_article_schema",
]

CONTRACT_VERSION = "v1"
SCHEMA_ID = "https://censurado.local/contracts/article.schema.json"

_VENDORED_DIR = Path(__file__).parent / "vendored" / CONTRACT_VERSION
ARTICLE_SCHEMA_PATH = _VENDORED_DIR / "article.schema.json"


def load_article_schema() -> dict:
    """Parse and return the vendored PublishArticleInput JSON Schema."""
    return json.loads(ARTICLE_SCHEMA_PATH.read_text(encoding="utf-8"))
