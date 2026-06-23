"""The publish boundary: raw HTTP to the platform's ``/articles`` seam.

  * ``publish_article``    pure transport: POST a finalized ``PublishArticleInput``,
                           return a typed ``PublishResult`` (validates the section
                           locally first; never raises on a transport fault).
  * ``publish_assignment`` the COLLECT -> PUBLISH step with side effects: publish,
                           then mark the assignment published and record one coverage
                           row for the manager's freshness memory.
"""

from __future__ import annotations

from newsroom.publish.client import PublishResult, build_payload, publish_article
from newsroom.publish.service import publish_assignment

__all__ = ["PublishResult", "publish_article", "build_payload", "publish_assignment"]
