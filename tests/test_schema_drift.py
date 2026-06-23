"""Schema drift guard.

The vendored article schema MUST stay byte-equal (structurally) to the live
platform schema. If the platform adds a required field or tightens a constraint,
this test fails loudly so the harness is updated deliberately rather than breaking
silently at publish time.

Platform schema source resolution:
  1. ``NEWSROOM_PLATFORM_SCHEMA`` env var (a filesystem path or an http(s) URL), or
  2. the sibling ``censurado-web`` checkout next to this repo.
If neither resolves, the test FAILS loudly unless ``NEWSROOM_SKIP_DRIFT=1`` is set
(an explicit, documented escape for environments without the platform checkout).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest

from newsroom.contracts.schema import SCHEMA_ID, load_article_schema

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SIBLING_DEFAULT = _REPO_ROOT.parent / "censurado-web" / "contracts" / "article.schema.json"


def _load_platform_schema() -> dict:
    source = os.environ.get("NEWSROOM_PLATFORM_SCHEMA", "").strip()
    if source.startswith(("http://", "https://")):
        return httpx.get(source, timeout=10).json()

    path = Path(source) if source else _SIBLING_DEFAULT
    if not path.is_absolute():
        path = (_REPO_ROOT / path).resolve()
    if not path.exists():
        if os.environ.get("NEWSROOM_SKIP_DRIFT") == "1":
            pytest.skip(f"platform schema not found at {path}; NEWSROOM_SKIP_DRIFT=1 set")
        pytest.fail(
            f"platform schema not found at {path}. Point NEWSROOM_PLATFORM_SCHEMA at the "
            f"platform's contracts/article.schema.json (path or URL), or set "
            f"NEWSROOM_SKIP_DRIFT=1 to skip this check in environments without the platform."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def test_vendored_id_is_pinned():
    assert load_article_schema()["$id"] == SCHEMA_ID


def test_vendored_matches_platform():
    assert load_article_schema() == _load_platform_schema()


def test_drift_is_actually_detected():
    # Guard the guard: prove the PRODUCTION comparison (vendored == platform) would
    # catch real drift. Drive the same vendored load and the same equality the
    # production assertion uses, against two realistic platform changes.
    platform = _load_platform_schema()
    vendored = load_article_schema()

    # Sanity: today they agree (so the inequalities below are meaningful).
    assert vendored == platform

    tightened = json.loads(json.dumps(platform))
    tightened["properties"]["title"]["maxLength"] = 100  # platform tightens 300 -> 100
    assert vendored != tightened

    added_required = json.loads(json.dumps(platform))
    added_required["required"] = sorted(set(added_required.get("required", [])) | {"summary"})
    assert vendored != added_required
