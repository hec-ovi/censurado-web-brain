"""Runs and assignments: the harness's record of what each invocation attempted.

A ``run`` is one harness invocation; an ``assignment`` is one article a run tries
to produce, and it is the idempotency anchor at the publish seam. This package
ships the typed CRUD over those two tables (the schema itself lives in
``newsroom.db``). The per-article pipeline (Step 5) drives an assignment through
``assigned -> drafting -> ready`` (finalized, body persisted, awaiting publish) or
``-> dropped`` (e.g. budget exhausted, never published). The manager (Step 6)
creates runs and assignments; the publish client (Step 7) sets ``published``.
"""

from __future__ import annotations

from newsroom.runs.store import Assignment, Run, RunStore, open_runs_store

__all__ = ["Assignment", "Run", "RunStore", "open_runs_store"]
