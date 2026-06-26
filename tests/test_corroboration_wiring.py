"""The REAL corroboration seam, end to end: deps -> active style guide.sourcing ->
EditorialContext -> the pure gate, with ``ownership_of`` built from the portal registry.

The unit coverage in ``test_corroboration.py`` exercises the gate with a hand-built
dict resolver, so it proves the COUNTING logic but NOT the production wiring. The gate's
critical failure mode is being silently UN-armed: a future change that breaks the
``build_run_deps`` -> ``style_store.active().sourcing["min_sources"]`` -> ``ownership_of``
seam would disarm the drop with every unit test still green. These tests drive the
PRODUCTION assembly (``build_run_deps`` over an ``open_db`` connection) through
``execute_run`` / ``execute_direct``, so they fail loudly if the seam ever comes apart:

  * the resolver really is built from the registered portals, with co-ownership
    collapsing and the right domain normalization;
  * an armed managed run DROPS an under-corroborated assignment ``uncorroborated`` and
    spends ZERO draft tokens (the only model call is the manager's triage), while a
    corroborated story is NOT false-dropped and goes on to draft + publish;
  * the direct-from-brief path does NOT honor the corroboration floor: the operator
    vouched for the source, so a single-source brief drafts and publishes even with the
    same gate armed (the gate is forced off for the direct path, never for managed).

Only the two network seams (web search + the research/seed ledger) are doubled; the
store, the style store, the portal registry, the resolver, and the resolved roles are
the real production wiring.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from newsroom.config import Settings
from newsroom.db import open_db
from newsroom.editorial.portals import Portal, PortalStore
from newsroom.editorial.style import StyleGuide, StyleStore
from newsroom.personas import Persona, PersonaStore
from newsroom.research.ledger import Ledger
from newsroom.runner import build_run_deps, run_direct, run_managed


# ----- builders -----


def _settings(fake, tmp_path, **over) -> Settings:
    base = dict(
        persona_db_path=tmp_path / "brain.db",
        inference_base_url=f"{fake.base_url}/v1",
        publish_base_url=fake.base_url,
        operator_token="op-token",
    )
    base.update(over)
    return Settings(**base)


def _ada() -> Persona:
    return Persona(id="ada", display_name="Ada", beat="tech", who_i_am="I cover chips.", style="dry")


# Two co-owned portals sharing ONE ownership_group, plus one in a DIFFERENT group, mirroring
# the seeded registry (clarin.com -> grupo-clarin, lanacion.com.ar -> grupo-la-nacion).
# tn.com.ar is a real co-owned sibling of clarin (same group) so two of its mastheads
# count as a single independent source.
_PORTALS: tuple[Portal, ...] = (
    Portal(domain="clarin.com", ownership_group="grupo-clarin"),
    Portal(domain="tn.com.ar", ownership_group="grupo-clarin"),
    Portal(domain="lanacion.com.ar", ownership_group="grupo-la-nacion"),
)


def _seed_portals(conn) -> None:
    store = PortalStore(conn)
    for portal in _PORTALS:
        store.create(portal)


def _seed_min_sources(conn, n: int = 2) -> None:
    """Seed an ACTIVE house style whose sourcing requires ``n`` independent sources, the
    same way the editorial code does (a new immutable version, promoted active). This is
    the value the seam reads via ``deps.style_store.active().sourcing["min_sources"]``."""
    StyleStore(conn).add_version(
        StyleGuide(sourcing={"min_sources": n}), created_by="test", activate=True
    )


def _no_search(_query):
    return []


def _ledger_from(*urls: str):
    """A make_ledger double that grounds the article on exactly ``urls`` (a fixed clock so
    the rows are deterministic). The gate counts the INDEPENDENT groups behind them."""

    def make(_assignment, _spec, _persona, _budget) -> Ledger:
        led = Ledger(clock=lambda: datetime(2026, 6, 23, tzinfo=timezone.utc))
        for i, url in enumerate(urls):
            led.add(claim=f"claim {i}", url=url, snippet=f"snippet {i}")
        return led

    return make


def _empty_ledger(_assignment, _spec, _persona, _budget) -> Ledger:
    return Ledger()


def _assign(persona_id: str, headline: str = "A fresh story") -> str:
    return json.dumps(
        {"action": "assign", "assignments": [
            {"persona_id": persona_id, "headline": headline, "angle": "cover it", "triage": "new"}
        ]}
    )


def _finalize_ok(title: str = "Title", body: str = "Final body.") -> str:
    return json.dumps({"title": title, "body": body, "topics": ["chips"]})


def _deps(fake, settings, conn, *, make_ledger, fetch=None):
    """Assemble run deps through the REAL production path (``build_run_deps``), seeding one
    persona. Only the two network seams are doubled; the style store, the portal registry,
    the corroboration resolver, and the resolved roles (pointed at the fake) are real."""
    persona_store = PersonaStore(conn)
    persona_store.create(_ada())
    return build_run_deps(
        settings,
        conn=conn,
        lock=threading.Lock(),
        persona_store=persona_store,
        search_news=_no_search,
        make_ledger=make_ledger,
        fetch=fetch,
    )


# ----- 1) the resolver really is built from the registry -----


def test_build_run_deps_ownership_resolver_is_built_from_the_registry(fake, tmp_path):
    # build_run_deps is the SINGLE place that loads portals and builds ownership_of. Pin
    # that the resolver it produces really reflects the registry: co-owned outlets share a
    # group, a different owner does not collapse onto it, and an unknown domain is "".
    conn = open_db(":memory:", check_same_thread=False)
    _seed_portals(conn)
    deps = _deps(fake, _settings(fake, tmp_path), conn, make_ledger=_empty_ledger)

    assert deps.ownership_of("clarin.com") == "grupo-clarin"
    # A co-owned sibling resolves to the SAME group (so two of its mastheads count once).
    assert deps.ownership_of("tn.com.ar") == "grupo-clarin"
    assert deps.ownership_of("tn.com.ar") == deps.ownership_of("clarin.com")
    # A portal owned by someone else is NOT collapsed onto grupo-clarin.
    assert deps.ownership_of("lanacion.com.ar") == "grupo-la-nacion"
    # An unknown / off-allowlist domain resolves to "" -> the gate falls back to a
    # per-domain sentinel rather than a real group.
    assert deps.ownership_of("desconocido.test") == ""


# ----- 2) the armed managed run: drop without drafting, and no false-drop -----


def test_execute_run_drops_an_uncorroborated_assignment_without_drafting(fake, tmp_path):
    # The important one: the gate is ARMED through the real seam (min_sources=2 from the
    # active style guide + ownership_of from the registry). The manager assigns one story
    # whose ledger rests on TWO CO-OWNED outlets (clarin + its sibling tn) = exactly ONE
    # independent group, below the requirement. The assignment must DROP "uncorroborated"
    # and spend ZERO draft tokens.
    conn = open_db(":memory:", check_same_thread=False)
    _seed_portals(conn)
    _seed_min_sources(conn, 2)
    deps = _deps(
        fake, _settings(fake, tmp_path), conn,
        make_ledger=_ledger_from("https://www.clarin.com/a", "https://tn.com.ar/b"),
    )
    fake.state.script_chat(_assign("ada", headline="Una sola empresa, dos mascaras"))

    report = run_managed(deps=deps)  # funnels through execute_run

    assert [o.status for o in report.outcomes] == ["dropped"]
    assert report.outcomes[0].stop_reason == "uncorroborated"
    assert report.published == []
    # The proof the gate spends zero draft tokens: the pipeline made NO drafts, and the
    # ONLY model call in the whole run was the manager's triage (outline / draft / enrich /
    # finalize were never reached). A disarmed gate would draft and finalize the article,
    # pushing chat_requests well past one.
    assert report.outcomes[0].drafts == 0
    assert len(fake.state.chat_requests) == 1


def test_execute_run_does_not_drop_a_corroborated_assignment(fake, tmp_path):
    # The contrast: SAME armed setup, but the ledger rests on TWO DIFFERENT owners
    # (clarin -> grupo-clarin, lanacion -> grupo-la-nacion) = 2 independent groups, meeting
    # the requirement. The gate must NOT drop it: the article drafts and publishes. This
    # proves the gate does not false-drop a genuinely corroborated story.
    conn = open_db(":memory:", check_same_thread=False)
    _seed_portals(conn)
    _seed_min_sources(conn, 2)
    deps = _deps(
        fake, _settings(fake, tmp_path), conn,
        make_ledger=_ledger_from("https://www.clarin.com/a", "https://www.lanacion.com.ar/b"),
    )
    fake.state.script_chat(_assign("ada", headline="Dos fuentes independientes"))
    for body in ("an outline", "a clean draft", "an enriched body"):
        fake.state.script_chat(body)
    fake.state.script_chat(_finalize_ok("Dos Fuentes", "El cuerpo de la nota."))

    report = run_managed(deps=deps)

    assert [o.status for o in report.outcomes] == ["ready"]
    assert report.outcomes[0].stop_reason != "uncorroborated"
    assert report.outcomes[0].drafts >= 1  # the drafter WAS called (the gate let it through)
    assert [p.ok for p in report.published] == [True]
    assert len(fake.state.chat_requests) > 1  # more than just the manager's one call


# ----- 3) the direct-from-brief path does NOT honor the corroboration floor -----


def test_direct_link_single_source_is_not_dropped(fake, tmp_path):
    # The operator vouched for the source: a direct-from-brief article is NOT held to the
    # managed corroboration floor. SAME armed setup as the managed drop test (min_sources=2
    # from the active style guide + the registry resolver), but the link seeds just ONE
    # source (clarin -> grupo-clarin) and the outward research finds nothing more, so the
    # ledger rests on a single independent group. A managed run would DROP this; the direct
    # path forces the gate OFF, so the article instead DRAFTS and PUBLISHES.
    url = "https://clarin.com/nota"
    conn = open_db(":memory:", check_same_thread=False)
    _seed_portals(conn)
    _seed_min_sources(conn, 2)
    deps = _deps(
        fake, _settings(fake, tmp_path), conn,
        make_ledger=_empty_ledger, fetch=lambda _u: "Cuerpo de la nota.",
    )
    # The pipeline runs in full: outline, a draft that CITES the seed url (so the rules
    # evaluator passes), an enriched body, then a valid finalize.
    fake.state.script_chat("an outline")
    fake.state.script_chat(f"Grounded prose citing {url}.")
    fake.state.script_chat("an enriched body")
    fake.state.script_chat(_finalize_ok("Una Fuente", "El cuerpo de la nota."))

    report = run_direct(deps=deps, links=[url], persona_id="ada", brief="cubrilo")

    assert [o.status for o in report.outcomes] == ["ready"]  # NOT dropped: the gate is off
    assert report.outcomes[0].stop_reason != "uncorroborated"
    assert report.outcomes[0].drafts >= 1  # the drafter WAS reached
    assert [p.ok for p in report.published] == [True]
