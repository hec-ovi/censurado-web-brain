"""Embed removal detection for cited tweets and YouTube videos.

Articles store self-contained embed snapshots in their metadata (captured at
authoring time by the CLI's ``tweet`` / ``truth`` verbs, so the static site renders
a card that survives the source being deleted). The periodic sweep
(``censurado-brain embeds recheck``) re-validates those snapshots and flips the
availability flags. Everything takes an injectable ``fetch`` so the logic is
testable without a network.
"""

from __future__ import annotations

from .fetch import Fetch, FetchResult, default_fetch
from .recheck import apply_rechecks, fetch_article_slugs, recheck_metadata
from .twitter import parse_tweet_ref, recheck_tweet
from .youtube import check_youtube, parse_youtube_id

__all__ = [
    "Fetch",
    "FetchResult",
    "default_fetch",
    "parse_youtube_id",
    "check_youtube",
    "parse_tweet_ref",
    "recheck_tweet",
    "recheck_metadata",
    "fetch_article_slugs",
    "apply_rechecks",
]
