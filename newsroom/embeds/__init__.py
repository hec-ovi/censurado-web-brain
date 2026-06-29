"""Embed capture + removal detection for cited tweets and YouTube videos.

The pipeline calls these at generation time to turn a cited URL into a self-contained
snapshot it stores in article metadata (so the static site renders a tweet card or a
YouTube embed that survives the source being deleted), and a periodic sweep
(``censurado-brain embeds recheck``) re-validates those snapshots and flips the
availability flags. Everything takes an injectable ``fetch`` so the logic is testable
without a network.
"""

from __future__ import annotations

from .fetch import Fetch, FetchResult, default_fetch
from .recheck import apply_rechecks, fetch_article_slugs, recheck_metadata
from .twitter import capture_tweet, parse_tweet_ref, recheck_tweet
from .youtube import check_youtube, parse_youtube_id

__all__ = [
    "Fetch",
    "FetchResult",
    "default_fetch",
    "parse_youtube_id",
    "check_youtube",
    "parse_tweet_ref",
    "capture_tweet",
    "recheck_tweet",
    "recheck_metadata",
    "fetch_article_slugs",
    "apply_rechecks",
]
