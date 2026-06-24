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
    "BATCH_REQUEST_SCHEMA_ID",
    "BATCH_RESPONSE_SCHEMA_ID",
    "ARTICLE_SCHEMA_PATH",
    "BATCH_REQUEST_SCHEMA_PATH",
    "BATCH_RESPONSE_SCHEMA_PATH",
    "load_article_schema",
    "load_batch_request_schema",
    "load_batch_response_schema",
]

CONTRACT_VERSION = "v1"
SCHEMA_ID = "https://censurado.local/contracts/article.schema.json"
BATCH_REQUEST_SCHEMA_ID = "https://censurado.local/contracts/batch-request.schema.json"
BATCH_RESPONSE_SCHEMA_ID = "https://censurado.local/contracts/batch-response.schema.json"

_VENDORED_DIR = Path(__file__).parent / "vendored" / CONTRACT_VERSION
ARTICLE_SCHEMA_PATH = _VENDORED_DIR / "article.schema.json"
BATCH_REQUEST_SCHEMA_PATH = _VENDORED_DIR / "batch-request.schema.json"
BATCH_RESPONSE_SCHEMA_PATH = _VENDORED_DIR / "batch-response.schema.json"


def load_article_schema() -> dict:
    """Parse and return the vendored PublishArticleInput JSON Schema."""
    return json.loads(ARTICLE_SCHEMA_PATH.read_text(encoding="utf-8"))


def load_batch_request_schema() -> dict:
    """Parse and return the vendored BatchPublishRequest JSON Schema (the
    ``{"articles": [...]}`` body POST /articles:batch accepts; each item is the
    article shape plus a required ``idempotency_key``)."""
    return json.loads(BATCH_REQUEST_SCHEMA_PATH.read_text(encoding="utf-8"))


def load_batch_response_schema() -> dict:
    """Parse and return the vendored BatchPublishResponse JSON Schema (the
    ``{"results": [{index, id, slug, status}]}`` success body)."""
    return json.loads(BATCH_RESPONSE_SCHEMA_PATH.read_text(encoding="utf-8"))
