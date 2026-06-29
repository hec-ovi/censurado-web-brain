"""The run orchestrator: the trigger-blind run-execution path and its three entry
points (architecture doc B.7).

  * ``execute_run``   the single run-execution path: manager triage -> fan-out ->
                      publish. It never branches on the mode.
  * ``plan_run``      the trigger surface: a mode -> a ``RunScope`` (the only place
                      a mode is consulted).
  * ``run_manual`` / ``run_express`` / ``run_managed``  the three entry points, all
                      funneling through ``execute_run``.
  * ``build_run_deps``  the production assembly of the injected dependencies.
"""

from __future__ import annotations

from newsroom.runner.deps import build_run_deps, roles_for_settings
from newsroom.runner.run import (
    RUN_MODES,
    PublishOutcome,
    RunDeps,
    RunReport,
    RunScope,
    execute_batch,
    execute_direct,
    execute_run,
    plan_run,
    run_batch,
    run_direct,
    run_express,
    run_managed,
    run_manual,
    run_now,
    start_batch,
    start_direct,
    start_run,
)

__all__ = [
    "RUN_MODES",
    "RunScope",
    "RunDeps",
    "PublishOutcome",
    "RunReport",
    "plan_run",
    "start_run",
    "execute_run",
    "run_now",
    "run_manual",
    "run_express",
    "run_managed",
    "start_direct",
    "execute_direct",
    "run_direct",
    "start_batch",
    "execute_batch",
    "run_batch",
    "build_run_deps",
    "roles_for_settings",
]
