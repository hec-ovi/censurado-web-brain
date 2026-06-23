"""The inference-role preflight (Step 5's deferred ``endpoints_distinct`` startup
wiring, landed at the run orchestrator).

The evaluator should resolve to a different endpoint than the drafter (the
self-correction blind spot). When it does not, that is a supported degrade (the
pipeline falls back to the rules-grounded evaluator), so the preflight reports
``evaluator_distinct`` rather than failing, UNLESS the operator demands a distinct
evaluator, in which case it fails fast.
"""

from __future__ import annotations

import pytest

from newsroom.manager import resolve_roles


def _clear_role_env(monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith("NEWSROOM_"):
            monkeypatch.delenv(key, raising=False)


def test_same_endpoint_is_the_supported_degrade(monkeypatch):
    _clear_role_env(monkeypatch)
    roles = resolve_roles()
    # Nothing configured: every role falls back to the one default backend.
    assert roles.evaluator_distinct is False
    assert roles.drafter.endpoint_id == roles.evaluator.endpoint_id


def test_distinct_evaluator_endpoint_is_detected(monkeypatch):
    _clear_role_env(monkeypatch)
    monkeypatch.setenv("NEWSROOM_ROLE_EVALUATOR_MODEL", "a-second-model")
    roles = resolve_roles()
    assert roles.evaluator_distinct is True
    assert roles.drafter.endpoint_id != roles.evaluator.endpoint_id


def test_require_distinct_fails_fast_when_shared(monkeypatch):
    _clear_role_env(monkeypatch)
    with pytest.raises(RuntimeError, match="same endpoint"):
        resolve_roles(require_distinct=True)


def test_require_distinct_env_flag_fails_fast(monkeypatch):
    _clear_role_env(monkeypatch)
    monkeypatch.setenv("NEWSROOM_REQUIRE_DISTINCT_EVALUATOR", "true")
    with pytest.raises(RuntimeError):
        resolve_roles()


def test_require_distinct_passes_when_evaluator_is_separate(monkeypatch):
    _clear_role_env(monkeypatch)
    monkeypatch.setenv("NEWSROOM_ROLE_EVALUATOR_BASE_URL", "http://evaluator.local/v1")
    roles = resolve_roles(require_distinct=True)
    assert roles.evaluator_distinct is True
