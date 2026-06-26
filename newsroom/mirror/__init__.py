"""The author mirror: pull the platform's owned author registry into local personas.

The platform owns author existence + public profile; the brain keeps only the
private prompt. ``client`` transports the list (one GET), ``reconcile`` is the pure,
network-free policy that creates shells, refreshes public fields, and soft-
deactivates handles that left the platform, never touching a prompt.
"""

from __future__ import annotations

from newsroom.mirror.backfill import (
    AuthorPush,
    BackfillReport,
    backfill_web_authors,
)
from newsroom.mirror.client import (
    DEFAULT_TIMEOUT,
    PushResult,
    WebAuthor,
    fetch_web_authors,
    push_web_author,
)
from newsroom.mirror.reconcile import SHELL_BEAT, ReconcileResult, reconcile_personas

__all__ = [
    "WebAuthor",
    "fetch_web_authors",
    "PushResult",
    "push_web_author",
    "DEFAULT_TIMEOUT",
    "ReconcileResult",
    "reconcile_personas",
    "SHELL_BEAT",
    "BackfillReport",
    "backfill_web_authors",
    "AuthorPush",
]
