"""The publish boundary: raw HTTP to the platform's ``/articles`` seam.

  * ``publish_article``    pure transport: POST a finalized ``PublishArticleInput``,
                           return a typed ``PublishResult`` (validates the section
                           locally first; never raises on a transport fault).
  * ``publish_batch``      pure transport for the atomic batch seam: POST N finalized
                           articles to ``/articles:batch`` and map the per-item results
                           (all-or-nothing; each item carries its own idempotency key).
  * ``publish_assignment`` the COLLECT -> PUBLISH step with side effects: publish one,
                           then mark it published and record one coverage row.
  * ``publish_batch_assignments`` the same step for a whole run in ONE batch: publish
                           together, then fan the mark-published + coverage side effects
                           back to each assignment (idempotent across a replay).
"""

from __future__ import annotations

from newsroom.publish.client import (
    BatchItem,
    BatchItemResult,
    BatchResult,
    PublishResult,
    build_payload,
    publish_article,
    publish_batch,
)
from newsroom.publish.media import MediaAsset, MediaUploadError, upload_media
from newsroom.publish.service import publish_assignment, publish_batch_assignments

__all__ = [
    "PublishResult",
    "publish_article",
    "build_payload",
    "publish_assignment",
    "BatchItem",
    "BatchItemResult",
    "BatchResult",
    "publish_batch",
    "publish_batch_assignments",
    "MediaAsset",
    "MediaUploadError",
    "upload_media",
]
