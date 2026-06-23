"""The coverage memory: the fingerprint/similarity rules, the rules-first triage
band, and the ``coverage`` table round-trip over the real brain DB.

These pin the deterministic FLOOR the manager's freshness depends on: a
near-identical story is always a DUPLICATE (never republished), a topically related
story is a FOLLOW_UP, and an unrelated story is NEW, all without a model call.
"""

from __future__ import annotations

from datetime import datetime, timezone

from newsroom.db import open_db
from newsroom.manager.coverage import (
    CoverageStore,
    classify,
    fingerprint,
    similarity,
)
from newsroom.manager.types import Triage


def _clock():
    return datetime(2026, 6, 23, 12, 0, tzinfo=timezone.utc)


# ----- fingerprint + similarity -----


def test_fingerprint_drops_stopwords_and_short_fragments():
    fp = fingerprint(headline="The new OpenAI model is here today")
    assert "openai" in fp and "model" in fp
    assert "the" not in fp and "is" not in fp  # stopwords
    assert "new" not in fp  # in the stoplist (news boilerplate)


def test_fingerprint_folds_in_entities_whole_and_tokenized():
    fp = fingerprint(headline="Chipmaker ships a part", entities=["Acme Corp"])
    assert "acme corp" in fp  # whole entity
    assert "acme" in fp and "corp" in fp  # and tokenized


def test_similarity_is_jaccard_and_zero_on_empty():
    a = fingerprint(headline="alpha bravo charlie")
    b = fingerprint(headline="alpha bravo delta")
    assert 0.0 < similarity(a, b) < 1.0
    assert similarity(a, a) == 1.0
    assert similarity(a, frozenset()) == 0.0


# ----- the triage bands -----


def test_classify_new_when_nothing_overlaps():
    store = CoverageStore(open_db(":memory:"), clock=_clock)
    store.record(section="tech", headline="Quantum startup raises funding", entities=["Qubitly"])
    fp = fingerprint(headline="Senate passes a sweeping budget bill", entities=["Senate"])
    v = classify(fp, store.recent(), duplicate_threshold=0.6, followup_threshold=0.3)
    assert v.triage is Triage.NEW
    assert v.matched is None


def test_classify_duplicate_on_near_identical_story():
    store = CoverageStore(open_db(":memory:"), clock=_clock)
    store.record(section="tech", headline="OpenAI releases GPT-6 to developers",
                 entities=["OpenAI", "GPT-6"], slug="openai-gpt6")
    fp = fingerprint(headline="OpenAI releases GPT-6 to developers", entities=["OpenAI", "GPT-6"])
    v = classify(fp, store.recent(), duplicate_threshold=0.6, followup_threshold=0.3)
    assert v.triage is Triage.DUPLICATE
    assert v.matched is not None and v.matched.slug == "openai-gpt6"
    assert v.score >= 0.6


def test_classify_follow_up_on_related_developments():
    store = CoverageStore(open_db(":memory:"), clock=_clock)
    store.record(section="tech", headline="OpenAI releases GPT-6 to developers",
                 entities=["OpenAI", "GPT-6"], slug="openai-gpt6")
    # Same subject, clearly new development -> overlaps in the follow-up band.
    fp = fingerprint(headline="GPT-6 pricing draws complaints from startups over costs",
                     entities=["GPT-6"])
    v = classify(fp, store.recent(), duplicate_threshold=0.6, followup_threshold=0.2)
    assert v.triage is Triage.FOLLOW_UP
    assert v.matched is not None and v.matched.slug == "openai-gpt6"


# ----- the store round-trips over the real DB -----


def test_coverage_store_round_trip_and_recency_order():
    conn = open_db(":memory:")
    store = CoverageStore(conn)
    store.record(section="tech", headline="First story", topics=["ai"], entities=["Acme"],
                 slug="first", published_id="art_1", published_at="2026-06-20T00:00:00+00:00")
    store.record(section="world", headline="Second story", slug="second", published_id="art_2",
                 published_at="2026-06-22T00:00:00+00:00")

    recent = store.recent(limit=10)
    assert [c.slug for c in recent] == ["second", "first"]  # newest first
    first = recent[1]
    assert first.section == "tech"
    assert first.topics == ["ai"] and first.entities == ["Acme"]
    assert first.published_id == "art_1"
    # the persisted fingerprint reconstructs into a usable token set
    assert "acme" in first.fingerprint_set


def test_coverage_recent_respects_limit():
    store = CoverageStore(open_db(":memory:"), clock=_clock)
    for i in range(5):
        store.record(section="tech", headline=f"story {i}", published_at=f"2026-06-2{i}T00:00:00+00:00")
    assert len(store.recent(limit=3)) == 3


def test_multi_word_entity_token_survives_the_round_trip_and_keeps_its_band():
    # The persisted fingerprint must keep space-bearing tokens (e.g. "acme corp"),
    # or a near-identical multi-word-entity story would drop out of the DUPLICATE
    # band on reload and get republished.
    store = CoverageStore(open_db(":memory:"), clock=_clock)
    in_mem = store.record(section="tech", headline="Acme Corp meets New York Times",
                          entities=["Acme Corp", "New York Times"], slug="acme-nyt")
    reloaded = store.recent(limit=1)[0]
    assert "acme corp" in reloaded.fingerprint_set  # whole entity preserved, not split
    assert reloaded.fingerprint_set == in_mem.fingerprint_set  # exact round-trip

    candidate = fingerprint(headline="Acme Corp meets New York Times again",
                            entities=["Acme Corp", "New York Times"])
    # Same band whether scored against the in-memory or the reloaded fingerprint.
    band_in_mem = classify(candidate, [in_mem], duplicate_threshold=0.6, followup_threshold=0.3).triage
    band_reloaded = classify(candidate, [reloaded], duplicate_threshold=0.6, followup_threshold=0.3).triage
    assert band_in_mem is band_reloaded is Triage.DUPLICATE


def test_degenerate_headline_gets_a_non_empty_fingerprint():
    # A headline of only stopwords / short tokens must not fingerprint to empty
    # (empty matches nothing -> the duplicate floor would wave it through).
    assert fingerprint(headline="EU") == frozenset({"eu"})
    assert fingerprint(headline="AI") == frozenset({"ai"})
    # Two identical degenerate stories still collide exactly.
    assert similarity(fingerprint(headline="EU"), fingerprint(headline="EU")) == 1.0
    # Distinct degenerate stories do not.
    assert similarity(fingerprint(headline="EU"), fingerprint(headline="AI")) == 0.0
