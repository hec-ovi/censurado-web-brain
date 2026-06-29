"""The post-finalize widget step: emit {{tweet}}/{{video}}/{{relacionado}} markers into
a finished article's body and snapshots into its metadata, grounded in the ledger (cited
tweets/videos) and prior coverage (one related card). Every test injects a fake ``fetch``
so capture runs without a network; the article is a light stand-in exposing exactly the
attributes the step reads/writes (title, body, topics, metadata)."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from newsroom.embeds.fetch import FetchResult
from newsroom.manager.coverage import CoverageItem
from newsroom.pipeline.widgets import attach_widgets, select_related_slug
from newsroom.research.ledger import Ledger


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


def counting(table, default=None):
    """A fake fetch that records every URL it was asked for, so a test can prove the
    step never reaches the network for a non-social ledger."""
    calls: list[str] = []
    inner = route(table, default)

    def _f(url):
        calls.append(url)
        return inner(url)

    return _f, calls


def _ledger(*rows: tuple[str, str]) -> Ledger:
    """A ledger from (url, claim) pairs (snippet mirrors the claim)."""
    led = Ledger(clock=lambda: datetime(2026, 6, 23, tzinfo=timezone.utc))
    for url, claim in rows:
        led.add(claim=claim, url=url, snippet=claim)
    return led


def _article(*, title="T", body="", topics=None, metadata=None):
    return SimpleNamespace(title=title, body=body, topics=list(topics or []), metadata=metadata)


def _tweet_ok(tweet_id="20", handle="jack", name="Jack", text="just setting up my twttr"):
    return FetchResult(200, {"tweet": {
        "id": tweet_id,
        "text": text,
        "url": f"https://twitter.com/{handle}/status/{tweet_id}",
        "created_timestamp": 1142974214,
        "author": {"screen_name": handle, "name": name,
                   "avatar_url": f"https://pbs.twimg.com/{handle}.jpg"},
    }}, True)


def _yt_ok(title="Una entrevista clave"):
    return FetchResult(200, {"title": title, "author_name": "Canal", "thumbnail_url": "https://i.ytimg.com/x.jpg"}, True)


# ----- cited tweet -----


def test_cited_tweet_emits_marker_and_snapshot():
    art = _article(body="Para uno sin nada.\n\nEsto cita https://x.com/jack/status/20 directo.\n\nPara tres final.")
    led = _ledger(("https://x.com/jack/status/20", "el tuit fundacional"),
                  ("https://news.test/a", "una nota normal"))
    attach_widgets(art, ledger=led, fetch=route([("/status/20", _tweet_ok())]))

    assert "{{tweet:20}}" in art.body
    tweets = art.metadata["tweets"]
    assert len(tweets) == 1
    snap = tweets[0]
    # The snapshot carries exactly the keys the generator's tweet card reads.
    assert snap["id"] == "20" and snap["handle"] == "jack" and snap["name"] == "Jack"
    assert snap["text"] == "just setting up my twttr"
    assert snap["created_at"] == "2006-03-21" and snap["erased"] is False
    # Placed right where the prose cited it: after the citing block, before the next.
    assert art.body.index("cita") < art.body.index("{{tweet:20}}") < art.body.index("Para tres")


def test_failed_tweet_capture_emits_no_marker():
    art = _article(body="Una nota.\n\nMenciona https://x.com/jack/status/20 que ya no existe.")
    led = _ledger(("https://x.com/jack/status/20", "tuit"))
    # The fxtwitter read 404s (deleted/suspended at capture time): no snapshot, no marker.
    attach_widgets(art, ledger=led, fetch=route([], default=FetchResult(404, None, False)))

    assert "{{tweet:" not in art.body
    assert "tweets" not in (art.metadata or {})


def test_same_tweet_cited_twice_emits_one_marker():
    # Two mirror URLs resolve to the same tweet id 20; only the first emits a card.
    art = _article(body="Cita https://x.com/jack/status/20 y tambien https://twitter.com/jack/status/20.")
    led = _ledger(("https://x.com/jack/status/20", "a"), ("https://twitter.com/jack/status/20", "b"))
    attach_widgets(art, ledger=led, fetch=route([("/status/20", _tweet_ok())]))

    assert art.body.count("{{tweet:20}}") == 1
    assert len(art.metadata["tweets"]) == 1


# ----- cited youtube -----


def test_cited_available_video_emits_marker_and_media_check():
    art = _article(body="Intro.\n\nEl video https://youtu.be/dQw4w9WgXcQ lo muestra.\n\nCierre.")
    led = _ledger(("https://youtu.be/dQw4w9WgXcQ", "la grabacion"))
    attach_widgets(art, ledger=led, fetch=route([("dQw4w9WgXcQ", _yt_ok())]))

    assert "{{video:dQw4w9WgXcQ}}" in art.body
    check = art.metadata["media_checks"]["dQw4w9WgXcQ"]
    assert check["available"] is True and check["id"] == "dQw4w9WgXcQ"
    assert art.body.index("muestra") < art.body.index("{{video:dQw4w9WgXcQ}}") < art.body.index("Cierre")


def test_unavailable_video_emits_no_marker():
    # A video already removed at capture time gets no embed (the removed-state is the
    # recheck sweep's job on a PREVIOUSLY-live video, not invented here).
    art = _article(body="Texto.\n\nCita https://youtu.be/dQw4w9WgXcQ que no carga.")
    led = _ledger(("https://youtu.be/dQw4w9WgXcQ", "video"))
    attach_widgets(art, ledger=led, fetch=route([], default=FetchResult(404, None, False)))

    assert "{{video:" not in art.body
    assert "media_checks" not in (art.metadata or {})


# ----- non-social ledger never touches the network -----


def test_non_social_ledger_never_calls_fetch():
    art = _article(body="Cuerpo con dos parrafos.\n\nNada social aqui.")
    led = _ledger(("https://clarin.com/a", "x"), ("https://lanacion.com.ar/b", "y"))
    fetch, calls = counting([])
    attach_widgets(art, ledger=led, fetch=fetch)

    assert calls == []  # parse_* short-circuits before any fetch
    assert "{{" not in art.body  # body untouched
    assert art.metadata in (None, {})  # no tweets / media_checks added


# ----- related card from prior coverage -----


def _cov(headline, *, slug, topics=None):
    return CoverageItem(section="politics", headline=headline, topics=list(topics or []), slug=slug)


def test_related_card_emitted_for_best_coverage_match():
    art = _article(title="Reforma tributaria aprobada", topics=["reforma", "congreso"],
                   body="El Congreso aprobo la reforma tributaria hoy.\n\nLos detalles del proyecto.")
    coverage = [
        _cov("Resultados del futbol local", slug="futbol-x", topics=["deportes"]),
        _cov("Reforma tributaria en debate", slug="reforma-debate-1a2b", topics=["reforma", "congreso"]),
    ]
    attach_widgets(art, ledger=_ledger(), related_coverage=coverage)

    assert "{{relacionado:reforma-debate-1a2b}}" in art.body
    assert "{{relacionado:futbol-x}}" not in art.body


def test_no_related_card_below_threshold():
    art = _article(title="Reforma tributaria aprobada", topics=["reforma"],
                   body="Una nota sobre la reforma tributaria.")
    coverage = [_cov("Resultados del futbol local", slug="futbol-x", topics=["deportes"])]
    attach_widgets(art, ledger=_ledger(), related_coverage=coverage)

    assert "{{relacionado:" not in art.body


def test_related_skips_items_without_a_slug():
    art = _article(title="Reforma tributaria aprobada", topics=["reforma", "congreso"],
                   body="La reforma tributaria avanza en el Congreso.")
    coverage = [_cov("Reforma tributaria en debate", slug=None, topics=["reforma", "congreso"])]
    attach_widgets(art, ledger=_ledger(), related_coverage=coverage)

    assert "{{relacionado:" not in art.body


def test_select_related_slug_picks_highest_overlap():
    art = _article(title="Crisis energetica en la capital", topics=["energia", "cortes"])
    coverage = [
        _cov("Otra cosa", slug="otra", topics=["varios"]),
        _cov("Crisis energetica golpea la capital", slug="energia-1", topics=["energia", "cortes"]),
    ]
    assert select_related_slug(art, coverage, entities=[]) == "energia-1"
    assert select_related_slug(art, [], entities=[]) is None


# ----- robustness: never raise, never drop -----


def test_attach_is_a_noop_on_empty_inputs():
    art = _article(body="Solo texto.", metadata=None)
    attach_widgets(art, ledger=_ledger())
    assert art.body == "Solo texto."  # unchanged
    assert art.metadata in (None, {})


def test_existing_snapshots_are_preserved_and_not_duplicated():
    # An article already carrying a tweet snapshot (e.g. an operator edit) keeps it; a
    # ledger re-citing the same id does not double it.
    art = _article(
        body="Cita https://x.com/jack/status/20 de nuevo.",
        metadata={"tweets": [{"id": "20", "handle": "jack", "text": "kept", "erased": False}]},
    )
    led = _ledger(("https://x.com/jack/status/20", "tuit"))
    attach_widgets(art, ledger=led, fetch=route([("/status/20", _tweet_ok())]))

    assert len(art.metadata["tweets"]) == 1
    assert art.metadata["tweets"][0]["text"] == "kept"  # the pre-existing snapshot wins
    assert "{{tweet:20}}" not in art.body  # already known -> no new marker
