"""Assignment: the in-memory carrier for one article at the publish seam.

An ``assignment`` is one article to publish; it is the idempotency anchor at the
publish boundary (its ``content_hash`` / ``idempotency_key`` dedup a replay). The
publish client reads these fields. Callers construct an ``Assignment`` in memory;
there is no run-records database.
"""

from __future__ import annotations

from newsroom.runs.store import Assignment

__all__ = ["Assignment"]
