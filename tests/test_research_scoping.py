"""Per-author source pools + place scoping reach the search seam.

The data model already carried ``Persona.sources`` and a ``PortalStore``; these
pin the WIRING the deep-dive found missing: each author's research is confined to
its OWN sources (else the enabled portals), and discovery is scoped to the
publication's location. Drives the production seams (``_candidate_search`` /
``_research_ledger``) over a real ``PortalStore``/``LocationStore`` with a recording
search tool, so the scoping is asserted at the request, not assumed.
"""

from __future__ import annotations

import json

from newsroom.config import load_settings
from newsroom.db import open_db
from newsroom.editorial import LocationStore, PortalStore
from newsroom.editorial.portals import Portal
from newsroom.inference.provider import DIALECTS, ProviderConfig
from newsroom.manager.types import AssignmentSpec
from newsroom.personas import Persona
from newsroom.pipeline import ArticleBudget
from newsroom.runner.deps import _candidate_search, _research_ledger


class _RecordingTool:
    """A ResearchTool stand-in: records the scoping each ``search`` call carries and
    returns no hits (so the research loop stalls out after one step)."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def search(self, query, *, sites=None, country=None, language=None):
        self.calls.append({"query": query, "sites": sites, "country": country, "language": language})
        return []


def _drafter(fake) -> ProviderConfig:
    return ProviderConfig(
        role="drafter", provider="local", base_url=f"{fake.base_url}/v1",
        model="drafter-model", **DIALECTS["local"],
    )


class _Roles:
    def __init__(self, drafter: ProviderConfig) -> None:
        self.drafter = drafter


def _budget() -> ArticleBudget:
    return ArticleBudget(token_budget=10_000_000, wall_clock_s=1e9, clock=lambda: 0.0)


def _persona(sources: list[str]) -> Persona:
    return Persona(id="lara", display_name="Lara", beat="politics",
                   who_i_am="x", style="y", sources=sources)


def _spec() -> AssignmentSpec:
    return AssignmentSpec(persona_id="lara", section="politics", angle="el ajuste", headline="el ajuste")


# ----- discovery: location scopes the manager's news search -----


def test_candidate_search_scopes_discovery_by_location():
    conn = open_db(":memory:")
    LocationStore(conn).set(region="AR", ui_lang="es-419", language="es")
    tool = _RecordingTool()
    search = _candidate_search(tool, LocationStore(conn))

    search("noticias de hoy")

    call = tool.calls[-1]
    assert call["country"] == "AR"
    assert call["language"] == "es"


def test_candidate_search_is_unscoped_without_a_location_store():
    tool = _RecordingTool()
    search = _candidate_search(tool)  # no location -> open web, as before

    search("q")

    call = tool.calls[-1]
    assert call["country"] is None and call["language"] is None


# ----- research: each author searches its OWN source pool -----


def test_research_confines_an_author_to_its_own_sources(fake):
    fake.state.script_chat(json.dumps(["what happened"]))  # plan -> one sub-question
    conn = open_db(":memory:")
    # A portal exists as the global fallback; it must NOT be used here, because the
    # persona has its own pool.
    PortalStore(conn).create(Portal(domain="portal-fallback.test"))
    LocationStore(conn).set(region="AR", ui_lang="es-419", language="es")
    tool = _RecordingTool()
    make_ledger = _research_ledger(
        tool, _Roles(_drafter(fake)), load_settings(),
        portal_store=PortalStore(conn), location_store=LocationStore(conn),
    )

    persona = _persona(["clarin.com", "https://www.lanacion.com.ar/seccion"])
    make_ledger(_spec(), _spec(), persona, _budget())

    sites = tool.calls[-1]["sites"]
    assert "clarin.com" in sites
    assert "lanacion.com.ar" in sites  # normalized from the URL
    assert "portal-fallback.test" not in sites  # the author's pool wins over the fallback
    assert tool.calls[-1]["country"] == "AR" and tool.calls[-1]["language"] == "es"


def test_research_falls_back_to_enabled_portals_without_author_sources(fake):
    fake.state.script_chat(json.dumps(["what happened"]))
    conn = open_db(":memory:")
    PortalStore(conn).create(Portal(domain="clarin.com"))
    PortalStore(conn).create(Portal(domain="lanacion.com.ar"))
    PortalStore(conn).create(Portal(domain="off.test", enabled=False))  # disabled -> excluded
    tool = _RecordingTool()
    make_ledger = _research_ledger(
        tool, _Roles(_drafter(fake)), load_settings(),
        portal_store=PortalStore(conn), location_store=LocationStore(conn),
    )

    make_ledger(_spec(), _spec(), _persona([]), _budget())  # no own sources

    sites = tool.calls[-1]["sites"]
    assert "clarin.com" in sites and "lanacion.com.ar" in sites
    assert "off.test" not in sites  # only the ENABLED portals scope the search


def test_research_is_unscoped_with_no_sources_no_portals_no_location(fake):
    fake.state.script_chat(json.dumps(["what happened"]))
    tool = _RecordingTool()
    make_ledger = _research_ledger(tool, _Roles(_drafter(fake)), load_settings())

    make_ledger(_spec(), _spec(), _persona([]), _budget())

    call = tool.calls[-1]
    assert call["sites"] == []
    assert call["country"] is None and call["language"] is None
