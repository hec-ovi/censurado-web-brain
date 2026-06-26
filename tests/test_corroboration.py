"""The cross-source corroboration gate: a pure count over the ledger that the central
fact rests on at least N INDEPENDENT sources, before any draft token is spent.

The grouping policy under test (mirrors the module docstring):

  * Outlets that share a non-empty ownership_group collapse to ONE independent source.
  * An outlet with an empty group OR a domain unknown to the resolver is its OWN
    independent source (a per-domain sentinel), so genuinely-independent uncatalogued
    sources still corroborate each other instead of being over-collapsed.

The resolver is a plain dict-backed function here, exactly the shape ``runner.deps``
builds from the portal registry, so the gate is exercised without any store or network.
"""

from __future__ import annotations

from datetime import datetime, timezone

from newsroom.pipeline.corroboration import (
    CorroborationResult,
    corroboration_check,
    independent_groups,
)
from newsroom.research.ledger import Ledger


# The registered ownership groups, keyed by registrable domain, exactly like the map
# build_run_deps assembles from the seeded portals.
_OWNERSHIP = {
    "clarin.com": "grupo-clarin",
    "tn.com.ar": "grupo-clarin",  # co-owned sibling of clarin
    "lanacion.com.ar": "grupo-la-nacion",
}


def _resolver(mapping=None):
    table = _OWNERSHIP if mapping is None else mapping

    def ownership_of(domain: str) -> str:
        return table.get(domain, "")

    return ownership_of


def _ledger(*urls: str) -> Ledger:
    led = Ledger(clock=lambda: datetime(2026, 6, 23, tzinfo=timezone.utc))
    for i, url in enumerate(urls):
        led.add(claim=f"claim {i}", url=url, snippet=f"snippet {i}")
    return led


def test_two_co_owned_urls_count_as_one_independent_source():
    # clarin.com and tn.com.ar are both grupo-clarin -> a single independent source.
    led = _ledger("https://www.clarin.com/a", "https://tn.com.ar/b")
    groups = independent_groups(led, ownership_of=_resolver())
    assert groups == ["grupo-clarin"]
    assert len(groups) == 1


def test_two_different_groups_count_as_two():
    led = _ledger("https://clarin.com/a", "https://www.lanacion.com.ar/b")
    groups = independent_groups(led, ownership_of=_resolver())
    assert groups == ["grupo-clarin", "grupo-la-nacion"]
    assert len(groups) == 2


def test_two_unknown_domains_each_count_independently():
    # Off-allowlist / unknown domains are NOT collapsed: each is its own per-domain
    # sentinel, so two genuinely independent uncatalogued sources still corroborate.
    led = _ledger("https://blog-uno.test/x", "https://otro-medio.example/y")
    groups = independent_groups(led, ownership_of=_resolver())
    assert groups == ["domain:blog-uno.test", "domain:otro-medio.example"]
    assert len(groups) == 2


def test_known_co_owned_plus_unknown_count_as_two():
    led = _ledger("https://clarin.com/a", "https://desconocido.test/z")
    groups = independent_groups(led, ownership_of=_resolver())
    assert groups == ["grupo-clarin", "domain:desconocido.test"]
    assert len(groups) == 2


def test_multiple_rows_same_unknown_domain_collapse_to_one_sentinel():
    # Two URLs on the SAME unknown host are the same outlet -> one per-domain sentinel.
    led = _ledger("https://solo.test/a", "https://www.solo.test/b", "https://solo.test/c")
    groups = independent_groups(led, ownership_of=_resolver())
    assert groups == ["domain:solo.test"]
    assert len(groups) == 1


def test_empty_ownership_group_is_treated_as_unknown_per_domain():
    # A domain the resolver maps to "" (empty group) is its OWN source, like an unknown.
    resolver = _resolver({"medio.test": "", "clarin.com": "grupo-clarin"})
    led = _ledger("https://medio.test/a", "https://clarin.com/b")
    groups = independent_groups(led, ownership_of=resolver)
    assert groups == ["domain:medio.test", "grupo-clarin"]


def test_rows_without_a_host_are_skipped():
    # A scheme-only URL reduces to an empty host (no registrable domain), so the gate
    # skips it; only the row with a real host contributes a source.
    led = _ledger("https://", "https://clarin.com/a")
    groups = independent_groups(led, ownership_of=_resolver())
    assert groups == ["grupo-clarin"]


def test_empty_ledger_has_zero_groups():
    led = _ledger()
    result = corroboration_check(led, ownership_of=_resolver(), required=2)
    assert result.count == 0
    assert result.groups == []
    assert result.ok is False  # zero sources cannot meet a positive requirement


def test_count_meets_or_misses_the_requirement():
    two_groups = _ledger("https://clarin.com/a", "https://lanacion.com.ar/b")
    one_group = _ledger("https://clarin.com/a", "https://tn.com.ar/b")  # both grupo-clarin

    met = corroboration_check(two_groups, ownership_of=_resolver(), required=2)
    assert met.ok is True and met.count == 2 and met.required == 2
    assert isinstance(met, CorroborationResult)

    missed = corroboration_check(one_group, ownership_of=_resolver(), required=2)
    assert missed.ok is False and missed.count == 1 and missed.required == 2


def test_required_zero_or_negative_turns_the_gate_off():
    # required <= 0 means unconfigured: ok is True regardless of how thin the ledger is.
    thin = _ledger("https://clarin.com/a")  # one source only
    off = corroboration_check(thin, ownership_of=_resolver(), required=0)
    assert off.ok is True and off.count == 1
    negative = corroboration_check(_ledger(), ownership_of=_resolver(), required=-1)
    assert negative.ok is True and negative.count == 0
