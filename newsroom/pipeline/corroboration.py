"""Cross-source corroboration: a deterministic gate that the assembled ledger rests on
enough INDEPENDENT sources before a single draft token is spent.

The house style asks for the central fact to hold across "at least N independent
sources" (the seeded ``sourcing.min_sources`` of 2). Until now that was only a SOFT
evaluator hint rendered into the prompt (``editorial/render.py``): a local model under a
persona is free to ignore it, and a thin ledger (one outlet, or two outlets that are the
same company wearing two mastheads) still drafts, evaluates, enriches, fact-checks, and
finalizes before anything notices it was never corroborated. That spends the whole
per-article budget to produce an article the desk should never have started.

This module makes the hint a HARD, pure-code gate, mirroring the budget-exhausted drop:
no model call, no output-length cap, just a count over the ledger that lets the pipeline
DROP an under-corroborated assignment up front (before drafting), so the tokens are never
spent. Like the budget drop it runs BEFORE any draft, so it never touches the content
hash or the idempotency key.

GROUPING POLICY (the heart of the gate, and where co-ownership is collapsed):

  * Each ledger row contributes its registrable domain (``clarin.com``), via the same
    ``normalize_domain`` the portal registry uses, so the gate and the allowlist agree on
    what "one outlet" is. A row with no usable host contributes nothing.
  * Outlets that share a NON-EMPTY ``ownership_group`` collapse to ONE independent
    source: ``clarin.com`` and a co-owned sibling both resolve to ``grupo-clarin`` and
    count once. This is the whole reason ``ownership_group`` lives on every Portal: two
    mastheads owned by one company are not two independent confirmations of a fact.
  * An outlet with an EMPTY ownership_group, or a domain that is off the allowlist /
    unknown to the resolver, is its OWN independent source (a per-domain sentinel
    ``domain:<host>``). We deliberately do NOT collapse unknowns together: two genuinely
    independent sources the operator simply has not catalogued must still corroborate
    each other, so the gate counts them separately rather than over-dropping. The only
    thing that ever collapses two distinct domains is an explicit shared ownership_group.

The resolver is injected (``ownership_of``: domain -> group label, ``""`` when unknown),
so this module reaches nothing outside it: the single place that loads portals and builds
the resolver is ``runner.deps``, exactly like every other outside-world seam.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from newsroom.editorial.portals import normalize_domain
from newsroom.research.ledger import Ledger

__all__ = ["CorroborationResult", "corroboration_check", "independent_groups", "OwnershipOf"]

# A pure resolver: a registrable domain (``clarin.com``) -> its ownership-group label
# (``grupo-clarin``), or ``""`` when the domain is unknown / off the allowlist. Injected so
# the gate stays pure (no store, no network); ``runner.deps`` builds the concrete closure.
OwnershipOf = Callable[[str], str]


@dataclass
class CorroborationResult:
    """The verdict of one corroboration count. ``count`` distinct independent groups were
    found against a ``required`` threshold; ``groups`` is the distinct group keys in
    first-seen order (for an audit/log of WHAT corroborated). ``ok`` is the gate's
    pass/fail: True when corroboration is satisfied (or the gate is off)."""

    ok: bool
    count: int
    required: int
    groups: list[str]


def _registrable_domain(url: str) -> str:
    """The bare registrable host of a ledger row's URL (``clarin.com``), or ``""`` when the
    URL carries no host. Reuses the portal registry's ``normalize_domain`` so the gate
    groups outlets by the exact same notion of "one site" the allowlist does, rather than
    re-deriving host parsing here and risking a subtle divergence."""
    return normalize_domain(url)


def independent_groups(ledger: Ledger, *, ownership_of: OwnershipOf) -> list[str]:
    """The DISTINCT independent-source group keys backing ``ledger``, first-seen order.

    For each row: take its registrable domain (skip rows with no host); ask the resolver
    for that domain's ownership group; collapse co-owned outlets onto that shared label,
    and fall back to a per-domain sentinel (``domain:<host>``) for an empty group or an
    unknown/off-allowlist domain. The result is deduplicated preserving first-seen order,
    so its length is the number of genuinely independent sources (see the module's
    GROUPING POLICY for why unknowns stay separate)."""
    groups: list[str] = []
    for row in ledger.rows:
        domain = _registrable_domain(row.url)
        if not domain:
            continue
        group_key = ownership_of(domain).strip() or ("domain:" + domain)
        if group_key not in groups:
            groups.append(group_key)
    return groups


def corroboration_check(ledger: Ledger, *, ownership_of: OwnershipOf, required: int) -> CorroborationResult:
    """Count the independent sources in ``ledger`` and judge them against ``required``.

    ``required <= 0`` means the gate is OFF (the newsroom did not configure
    ``sourcing.min_sources``): ``ok`` is True regardless, so an unconfigured run behaves
    exactly as before. Otherwise ``ok`` is True only when the ledger rests on at least
    ``required`` distinct independent groups. Pure: no model call, no output-length cap;
    the caller uses ``ok`` to DROP before drafting, never to truncate anything."""
    groups = independent_groups(ledger, ownership_of=ownership_of)
    count = len(groups)
    ok = True if required <= 0 else count >= required
    return CorroborationResult(ok=ok, count=count, required=required, groups=groups)
