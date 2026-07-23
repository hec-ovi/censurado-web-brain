"""Embed recheck: tweet (fxtwitter) and youtube (oEmbed) availability, plus the
per-article recheck transform. The ``embeds recheck`` CLI verb is covered in
``test_cli.py``. Every test injects a fake ``fetch`` so the logic runs without a
network."""

from __future__ import annotations

import pytest

from newsroom.embeds import (
    check_youtube,
    parse_tweet_ref,
    parse_youtube_id,
    recheck_metadata,
    recheck_tweet,
)
from newsroom.embeds.fetch import FetchResult


def route(table, default=None):
    """A fake fetch: ``table`` is a list of (url-substring, FetchResult); first match
    wins. Unmatched URLs return ``default`` (a 404 by default)."""
    default = default or FetchResult(404, None, False)

    def _f(url):
        for sub, res in table:
            if sub in url:
                return res
        return default

    return _f


# ----- youtube -----

@pytest.mark.parametrize(
    "ref,expected",
    [
        ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://music.youtube.com/watch?v=dQw4w9WgXcQ&list=RDx", "dQw4w9WgXcQ"),
        ("https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("not a real url", None),
        ("https://example.com/watch?v=dQw4w9WgXcQ", None),
    ],
)
def test_parse_youtube_id(ref, expected):
    assert parse_youtube_id(ref) == expected


def test_check_youtube_available():
    f = route([("dQw4w9WgXcQ", FetchResult(200, {
        "title": "Never Gonna Give You Up", "author_name": "Rick Astley",
        "thumbnail_url": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
    }, True))])
    snap = check_youtube("https://youtu.be/dQw4w9WgXcQ", fetch=f)
    assert snap == {
        "id": "dQw4w9WgXcQ", "available": True,
        "title": "Never Gonna Give You Up", "author": "Rick Astley",
        "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg",
    }


def test_check_youtube_removed():
    snap = check_youtube("dQw4w9WgXcQ", fetch=route([], default=FetchResult(404, None, False)))
    assert snap == {"id": "dQw4w9WgXcQ", "available": False}


def test_check_youtube_not_a_video():
    assert check_youtube("https://example.com/page", fetch=route([])) is None


# ----- twitter -----

@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://x.com/jack/status/20", ("jack", "20")),
        ("https://twitter.com/jack/status/20", ("jack", "20")),
        ("https://www.x.com/Foo_Bar/status/12345", ("Foo_Bar", "12345")),
        ("https://x.com/i/status/999", (None, "999")),
        ("https://example.com/jack/status/20", None),
        ("https://x.com/jack", None),
    ],
)
def test_parse_tweet_ref(url, expected):
    assert parse_tweet_ref(url) == expected


def test_recheck_tweet_live_keeps_text():
    snap = {"id": "20", "url": "https://x.com/jack/status/20", "text": "hello", "erased": True}
    f = route([("/jack/status/20", FetchResult(200, {"tweet": {"id": "20"}}, True))])
    out = recheck_tweet(snap, fetch=f)
    assert out["erased"] is False
    assert out["text"] == "hello"


def test_recheck_tweet_gone_marks_erased_keeps_text():
    snap = {"id": "20", "url": "https://x.com/jack/status/20", "text": "hello", "erased": False}
    out = recheck_tweet(snap, fetch=route([], default=FetchResult(404, None, False)))
    assert out["erased"] is True
    assert out["text"] == "hello"


# ----- per-article recheck -----

def test_recheck_metadata_flips_flags():
    meta = {
        "tweets": [{"id": "20", "url": "https://x.com/jack/status/20", "text": "x", "erased": False}],
        "media_checks": {"dQw4w9WgXcQ": {"available": True}},
    }
    new, changed = recheck_metadata(meta, fetch=route([], default=FetchResult(404, None, False)))
    assert changed is True
    assert new["tweets"][0]["erased"] is True
    assert new["media_checks"]["dQw4w9WgXcQ"]["available"] is False
    # the input is not mutated
    assert meta["tweets"][0]["erased"] is False
    assert meta["media_checks"]["dQw4w9WgXcQ"]["available"] is True


def test_recheck_metadata_no_change():
    meta = {
        "tweets": [{"id": "20", "url": "https://x.com/jack/status/20", "text": "x", "erased": False}],
        "media_checks": {"dQw4w9WgXcQ": {"available": True}},
    }
    f = route([
        ("/jack/status/20", FetchResult(200, {"tweet": {"id": "20"}}, True)),
        ("dQw4w9WgXcQ", FetchResult(200, {"title": "T"}, True)),
    ])
    new, changed = recheck_metadata(meta, fetch=f)
    assert changed is False
    assert new["tweets"][0]["erased"] is False
    assert new["media_checks"]["dQw4w9WgXcQ"]["available"] is True


