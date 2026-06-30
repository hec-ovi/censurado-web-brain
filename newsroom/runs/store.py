"""The ``Assignment`` carrier: one article at the publish seam.

An assignment is the idempotency anchor at the publish boundary: its
``content_hash`` and ``idempotency_key`` let a replay POST the byte-identical
body without creating a duplicate. The publish client reads these fields off an
``Assignment`` the caller constructs in memory; there is no run-records database.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["Assignment"]


@dataclass
class Assignment:
    id: str
    run_id: str
    persona_id: str
    section: str
    angle: str
    status: str
    created_at: str
    entities: list[str] = field(default_factory=list)
    drop_reason: str | None = None
    final_body: str | None = None
    content_hash: str | None = None
    idempotency_key: str | None = None
    ledger_digest: str | None = None
    published_id: str | None = None
    image_url: str | None = None  # the hero image's public URL (stamped into metadata.image)
    image_prompt: str | None = None  # the art-director brief that produced the image (audit)
