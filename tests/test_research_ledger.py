"""The claim-source ledger deduplicates by URL, reports new-row counts (the
research stall signal), hands off a plain artifact, and digests its sources
stably (order- and clock-independent).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from newsroom.research.ledger import Ledger


class _Clock:
    def __init__(self) -> None:
        self._t = datetime(2026, 6, 23, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        self._t += timedelta(seconds=1)
        return self._t


def test_add_returns_true_for_new_url_false_for_duplicate():
    led = Ledger(clock=_Clock())
    assert led.add(claim="who won", url="https://apnews.com/a", snippet="X won") is True
    assert led.add(claim="who won again", url="https://apnews.com/a", snippet="dup") is False
    assert len(led) == 1  # the duplicate URL was not stored


def test_blank_url_is_not_added():
    led = Ledger(clock=_Clock())
    assert led.add(claim="c", url="   ", snippet="s") is False
    assert len(led) == 0


def test_add_results_counts_only_new_urls():
    led = Ledger(clock=_Clock())
    results = [
        {"url": "https://a.com", "snippet": "a"},
        {"url": "https://b.com", "snippet": "b"},
        {"url": "https://a.com", "snippet": "a-again"},  # duplicate URL
    ]
    assert led.add_results(results, claim="q1") == 2
    # A second distinct query returning only already-seen URLs adds nothing.
    assert led.add_results([{"url": "https://b.com", "snippet": "b2"}], claim="q2") == 0
    assert len(led) == 2


def test_add_results_accepts_objects_with_attributes():
    class R:
        def __init__(self, url, snippet):
            self.url = url
            self.snippet = snippet

    led = Ledger(clock=_Clock())
    assert led.add_results([R("https://x.com", "x")], claim="q") == 1


def test_rows_record_claim_and_fetched_at():
    led = Ledger(clock=_Clock())
    led.add(claim="who won", url="https://apnews.com/a", snippet="X won")
    row = led.rows[0]
    assert row.claim == "who won"
    assert row.url == "https://apnews.com/a"
    assert row.snippet == "X won"
    assert row.fetched_at == "2026-06-23T00:00:01+00:00"


def test_explicit_fetched_at_overrides_clock():
    led = Ledger(clock=_Clock())
    led.add(claim="c", url="https://a.com", snippet="s", fetched_at="2020-01-01T00:00:00+00:00")
    assert led.rows[0].fetched_at == "2020-01-01T00:00:00+00:00"


def test_to_artifact_is_plain_data():
    led = Ledger(clock=_Clock())
    led.add(claim="c", url="https://a.com", snippet="s")
    art = led.to_artifact()
    assert art == [
        {"claim": "c", "url": "https://a.com", "snippet": "s", "fetched_at": "2026-06-23T00:00:01+00:00"}
    ]


def test_digest_is_stable_across_order_and_clock():
    a = Ledger(clock=_Clock())
    a.add(claim="c1", url="https://one.com", snippet="s1")
    a.add(claim="c2", url="https://two.com", snippet="s2")

    b = Ledger(clock=_Clock())  # different fetched_at values, reversed insertion
    b.add(claim="c2", url="https://two.com", snippet="s2")
    b.add(claim="c1", url="https://one.com", snippet="s1")

    assert a.digest() == b.digest()
    assert a.digest().startswith("sha256:")


def test_digest_changes_when_a_source_changes():
    a = Ledger(clock=_Clock())
    a.add(claim="c", url="https://one.com", snippet="s")
    b = Ledger(clock=_Clock())
    b.add(claim="c", url="https://one.com", snippet="DIFFERENT")
    assert a.digest() != b.digest()
