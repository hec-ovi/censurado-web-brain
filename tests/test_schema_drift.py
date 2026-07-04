"""Schema drift guard.

The vendored platform contracts MUST stay byte-equal (structurally) to the live
platform schemas. If the platform adds a required field or tightens a constraint,
this test fails loudly so the newsroom is updated deliberately rather than breaking
silently at publish time. It covers the single-article contract and the batch
publish request/response contracts (``POST /articles:batch``).

Platform schema source resolution, per contract file:
  1. ``NEWSROOM_PLATFORM_SCHEMA`` (a path or http(s) URL) for ``article.schema.json``,
  2. ``NEWSROOM_PLATFORM_CONTRACTS`` (a directory path or http(s) base URL) for any
     contract file, or
  3. the sibling ``censurado-web-backend/contracts`` checkout next to this repo
     (the publish/write contracts moved there in the 2026-06 backend split).
If none resolves, the test FAILS loudly unless ``NEWSROOM_SKIP_DRIFT=1`` is set
(an explicit, documented escape for environments without the platform checkout).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest

from newsroom.contracts.schema import (
    BATCH_REQUEST_SCHEMA_ID,
    BATCH_RESPONSE_SCHEMA_ID,
    SCHEMA_ID,
    load_article_schema,
    load_batch_request_schema,
    load_batch_response_schema,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SIBLING_CONTRACTS = _REPO_ROOT.parent / "censurado-web-backend" / "contracts"


def _load_platform_contract(filename: str) -> dict:
    # article.schema.json keeps its dedicated single-file override for back-compat.
    if filename == "article.schema.json":
        source = os.environ.get("NEWSROOM_PLATFORM_SCHEMA", "").strip()
        if source.startswith(("http://", "https://")):
            return httpx.get(source, timeout=10).json()
        if source:
            path = Path(source)
            if not path.is_absolute():
                path = (_REPO_ROOT / path).resolve()
            return _read_or_fail(path)

    base = os.environ.get("NEWSROOM_PLATFORM_CONTRACTS", "").strip()
    if base.startswith(("http://", "https://")):
        return httpx.get(f"{base.rstrip('/')}/{filename}", timeout=10).json()
    base_dir = Path(base) if base else _SIBLING_CONTRACTS
    if not base_dir.is_absolute():
        base_dir = (_REPO_ROOT / base_dir).resolve()
    return _read_or_fail(base_dir / filename)


def _read_or_fail(path: Path) -> dict:
    if not path.exists():
        if os.environ.get("NEWSROOM_SKIP_DRIFT") == "1":
            pytest.skip(f"platform contract not found at {path}; NEWSROOM_SKIP_DRIFT=1 set")
        pytest.fail(
            f"platform contract not found at {path}. Point NEWSROOM_PLATFORM_CONTRACTS at the "
            f"platform's contracts/ directory (path or base URL), or set NEWSROOM_SKIP_DRIFT=1 "
            f"to skip this check in environments without the platform."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def test_vendored_ids_are_pinned():
    assert load_article_schema()["$id"] == SCHEMA_ID
    assert load_batch_request_schema()["$id"] == BATCH_REQUEST_SCHEMA_ID
    assert load_batch_response_schema()["$id"] == BATCH_RESPONSE_SCHEMA_ID


def test_vendored_article_matches_platform():
    assert load_article_schema() == _load_platform_contract("article.schema.json")


def test_vendored_batch_request_matches_platform():
    assert load_batch_request_schema() == _load_platform_contract("batch-request.schema.json")


def test_vendored_batch_response_matches_platform():
    assert load_batch_response_schema() == _load_platform_contract("batch-response.schema.json")


def test_drift_is_actually_detected():
    # Guard the guard: prove the PRODUCTION comparison (vendored == platform) would
    # catch real drift. Drive the same vendored load and the same equality the
    # production assertion uses, against two realistic platform changes.
    platform = _load_platform_contract("article.schema.json")
    vendored = load_article_schema()

    # Sanity: today they agree (so the inequalities below are meaningful).
    assert vendored == platform

    tightened = json.loads(json.dumps(platform))
    tightened["properties"]["title"]["maxLength"] = 100  # platform tightens 300 -> 100
    assert vendored != tightened

    added_required = json.loads(json.dumps(platform))
    added_required["required"] = sorted(set(added_required.get("required", [])) | {"summary"})
    assert vendored != added_required


def test_batch_request_item_requires_an_idempotency_key():
    # The one structural fact the brain's batch client depends on: each item carries
    # its own idempotency_key (the single endpoint takes it as a header; the batch
    # cannot, so it moves into the body). If the platform ever drops that requirement
    # this catches it.
    item = load_batch_request_schema()["properties"]["articles"]["items"]
    assert "idempotency_key" in item["required"]
    assert item["additionalProperties"] is False
