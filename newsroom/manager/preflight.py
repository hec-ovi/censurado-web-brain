"""Inference-role preflight for a run: resolve the drafter, evaluator, finalize, and
manager backends once at the top of a run and report whether the evaluator is
distinct from the drafter (architecture doc A.4).

A model cannot reliably grade its own work (the self-correction blind spot), so the
evaluator role SHOULD resolve to a different endpoint than the drafter. When it
does not (only the local Gemma is configured), that is a SUPPORTED degrade: the
per-article pipeline already detects the shared endpoint per call and falls back to
the rules-grounded evaluator (Step 5). So this preflight does not fail by default;
it surfaces ``evaluator_distinct`` so the orchestrator (and an operator reading the
logs) can see which mode is active. An operator who REQUIRES a separate evaluator
sets ``require_distinct=True`` (or the env flag) and gets the fail-fast startup
assertion instead of a silent degrade.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from newsroom.inference.provider import ProviderConfig, resolve

__all__ = ["ResolvedRoles", "resolve_roles"]


@dataclass
class ResolvedRoles:
    drafter: ProviderConfig
    evaluator: ProviderConfig
    finalize: ProviderConfig
    manager: ProviderConfig
    evaluator_distinct: bool


def _env_truthy(name: str) -> bool:
    return (os.getenv(name) or "").strip().lower() == "true"


def resolve_roles(*, require_distinct: bool | None = None) -> ResolvedRoles:
    """Resolve every inference role for a run and compute ``evaluator_distinct``.

    ``require_distinct`` defaults to the ``NEWSROOM_REQUIRE_DISTINCT_EVALUATOR`` env
    flag. When True and the evaluator shares the drafter's endpoint, this raises a
    ``RuntimeError`` (the fail-fast startup assertion) rather than letting the run
    silently degrade to the rules-grounded evaluator."""
    if require_distinct is None:
        require_distinct = _env_truthy("NEWSROOM_REQUIRE_DISTINCT_EVALUATOR")

    drafter = resolve("drafter")
    evaluator = resolve("evaluator")
    finalize = resolve("finalize")
    manager = resolve("manager")
    distinct = drafter.endpoint_id != evaluator.endpoint_id

    if require_distinct and not distinct:
        raise RuntimeError(
            "evaluator role resolves to the same endpoint as the drafter "
            f"({drafter.endpoint_id!r}); configure NEWSROOM_ROLE_EVALUATOR_* to a "
            "distinct backend, or unset NEWSROOM_REQUIRE_DISTINCT_EVALUATOR to allow "
            "the rules-grounded evaluator degrade."
        )

    return ResolvedRoles(
        drafter=drafter,
        evaluator=evaluator,
        finalize=finalize,
        manager=manager,
        evaluator_distinct=distinct,
    )
