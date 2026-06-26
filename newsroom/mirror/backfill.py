"""The one-time author backfill: push existing local personas to the platform.

Before the platform owns authors, the brain already has the personas (the seeded or
operator-authored identities). This backfill seeds the platform author registry FROM
those personas' public fields, so the platform becomes authoritative and the mirror
(``newsroom.mirror.reconcile``) has something to reconcile against. It is the inverse,
one-shot complement to the reconcile and is idempotent: the platform upserts on handle,
so re-running changes nothing.

Only the PUBLIC fields cross the boundary (handle == persona id, name, bio, avatar);
the private prompt never leaves the brain. The orchestration here is pure (the per-
author push is injected), so it is tested without a real platform.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from newsroom.mirror.client import PushResult
from newsroom.personas.store import Persona

__all__ = ["BackfillReport", "backfill_web_authors", "AuthorPush"]

# A push takes one persona and upserts its public fields to the platform, returning a
# typed result. Injected so the backfill is tested without a real platform.
AuthorPush = Callable[[Persona], PushResult]


@dataclass
class BackfillReport:
    """What the backfill did: handles successfully upserted, and per-handle failures
    with their reason. ``dry_run`` marks a preview that pushed nothing."""

    pushed: list[str] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    dry_run: bool = False

    def as_dict(self) -> dict:
        return {"pushed": self.pushed, "failed": self.failed, "dry_run": self.dry_run}


def backfill_web_authors(personas: list[Persona], push: AuthorPush) -> BackfillReport:
    """Push each persona's public fields to the platform, collecting a per-handle
    result. A failure is recorded and the loop continues, so one unreachable upsert
    never aborts the rest."""
    report = BackfillReport()
    for persona in personas:
        result = push(persona)
        if result.ok:
            report.pushed.append(persona.id)
        else:
            report.failed.append(
                {
                    "handle": persona.id,
                    "code": result.code or str(result.status),
                    "detail": result.detail,
                }
            )
    return report
