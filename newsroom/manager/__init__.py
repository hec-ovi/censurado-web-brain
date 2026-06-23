"""The manager and the deterministic fan-out: the harness's orchestrator-workers
seam (architecture doc A.3/A.6/B.2).

  * ``run_manager``   the bounded ReAct loop that triages today's news against the
                      coverage memory and emits a clamped ``Manifest`` of assignments.
  * ``dispatch_run``  the SOLE fan-out point: persists the manifest's assignments and
                      runs the per-article pipeline for each.
  * coverage memory   ``CoverageStore`` / ``classify`` for fresh, non-repeating,
                      continuous news.
  * ``resolve_roles`` the inference-role preflight (evaluator-distinct check).
"""

from __future__ import annotations

from newsroom.manager.coverage import (
    CoverageItem,
    CoverageStore,
    CoverageVerdict,
    classify,
    fingerprint,
    similarity,
)
from newsroom.manager.dispatch import DispatchResult, dispatch_run
from newsroom.manager.manager import run_manager
from newsroom.manager.preflight import ResolvedRoles, resolve_roles
from newsroom.manager.types import AssignmentSpec, Candidate, Manifest, Triage

__all__ = [
    "run_manager",
    "dispatch_run",
    "DispatchResult",
    "resolve_roles",
    "ResolvedRoles",
    "CoverageStore",
    "CoverageItem",
    "CoverageVerdict",
    "classify",
    "fingerprint",
    "similarity",
    "Triage",
    "Candidate",
    "AssignmentSpec",
    "Manifest",
]
