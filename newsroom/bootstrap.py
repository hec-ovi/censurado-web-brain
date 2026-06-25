"""One command that makes a fresh box a working newsroom: seed, then run.

``python -m newsroom bootstrap`` (or ``censurado-brain bootstrap``) opens the brain
database, idempotently seeds the editorial config and the default author personas, and
then runs one managed batch through the same path the automation trigger uses. Safe to
run on every container start: seeding is find-or-create, so a re-run never clobbers an
operator's edits, and the run is the normal idempotent publish.

Seeding and the run share ONE database connection, so the manager sees the personas the
seed just wrote (a fresh box would otherwise hit the ``not personas`` early-return and
publish nothing). The run step is injectable (``runner=``) so the seed logic is tested
without standing up the full inference pipeline.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from sqlite3 import Connection

from newsroom.config import Settings
from newsroom.db import open_db
from newsroom.editorial.seeds import seed_all
from newsroom.personas import PersonaStore
from newsroom.runner import RunReport, build_run_deps, execute_run, start_run

__all__ = ["bootstrap", "Runner"]

# A runner takes the shared connection and seeded persona store and runs one batch,
# returning a machine-readable summary. Injectable so the seed path tests on its own.
Runner = Callable[..., dict]


def _run_summary(report: RunReport) -> dict:
    """The same shape ``newsroom.cli`` prints for a run, so a bootstrapped run reads
    identically to a triggered one."""
    return {
        "run_id": report.run_id,
        "mode": report.mode,
        "status": report.status,
        "assigned": len(report.manifest.assignments),
        "published": sum(1 for outcome in report.published if outcome.ok),
        "failed": sum(1 for outcome in report.published if not outcome.ok),
        "dropped": sum(1 for outcome in report.outcomes if outcome.status == "dropped"),
    }


def _default_runner(
    settings: Settings, conn: Connection, persona_store: PersonaStore, *, mode: str, n: int | None
) -> dict:
    """Run one batch over the shared connection through the production assembly, the
    same way ``cli.build_deps_from_env`` does."""
    deps = build_run_deps(settings, conn=conn, lock=threading.Lock(), persona_store=persona_store)
    run, scope = start_run(mode, deps=deps, n=n)
    report = execute_run(run=run, scope=scope, deps=deps)
    return _run_summary(report)


def bootstrap(
    settings: Settings,
    *,
    run: bool = True,
    mode: str = "managed",
    n: int | None = None,
    runner: Runner | None = None,
    **seed_overrides,
) -> dict:
    """Seed the newsroom idempotently, then optionally run one batch. Returns a summary
    with the per-category seed result and (when run) the run summary.

    ``runner`` defaults to the production run; a test injects a double to assert the
    run is invoked AFTER seeding without driving the full pipeline. ``seed_overrides``
    are forwarded to ``seed_all`` (location/portals/personas/style) for tests."""
    conn = open_db(settings.persona_db_path, check_same_thread=False)
    seeded = seed_all(conn, **seed_overrides)
    result: dict = {"seeded": seeded.as_dict(), "ran": False}
    if run:
        persona_store = PersonaStore(conn)
        result["run"] = (runner or _default_runner)(
            settings, conn, persona_store, mode=mode, n=n
        )
        result["ran"] = True
    return result
