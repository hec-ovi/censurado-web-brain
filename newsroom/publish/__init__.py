"""The publish boundary: raw HTTP to the platform's ``/articles`` seam.

  * ``publish_article``    pure transport: POST a finalized ``PublishArticleInput``,
                           return a typed ``PublishResult`` (validates the section
                           locally first; never raises on a transport fault).
  * ``publish_batch``      pure transport for the atomic batch seam: POST N finalized
                           articles to ``/articles:batch`` and map the per-item results
                           (all-or-nothing; each item carries its own idempotency key).
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

__all__ = [
    "PublishResult",
    "publish_article",
    "build_payload",
    "BatchItem",
    "BatchItemResult",
    "BatchResult",
    "publish_batch",
    "MediaAsset",
    "MediaUploadError",
    "upload_media",
]
