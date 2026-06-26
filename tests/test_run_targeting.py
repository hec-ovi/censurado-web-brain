"""Manual-mode targeting drives the per-author research sweep (infra chunk 4, part A).

An operator can target a manual run at a SUBSET of authors (over HTTP: ``POST /runs``
with ``{mode: manual, persona_ids: [...], n}``; over CLI: ``--mode manual --persona-ids``).
The manager then allocates only across those authors, and each targeted author's research
is confined to ITS OWN linked source pool (``persona.sources`` from chunk 3), not the
global portal registry. ``test_runner.test_manual_run_honors_a_persona_subset`` already
pins that targeting narrows the assignable set; this pins the deeper wiring the targeting
exists for: targeting author X drives the research SWEEP over X's linked sources.

It drives the real run path (``run_manual`` -> ``execute_run`` -> ``dispatch_run`` -> the
production ``_research_ledger`` seam) assembled by ``build_run_deps`` over a real
``open_db`` connection. Only the two network seams (the manager's candidate search and the
author's research tool) are doubled; the run/persona/portal/style/location stores and the
resolved roles are the real production wiring. The recording research tool captures the
``sites`` each search was scoped to, so the per-author scoping is asserted at the request,
not assumed.
"""

from __future__ import annotations

import json
import threading

from newsroom.config import Settings
from newsroom.db import open_db
from newsroom.editorial import LocationStore, PortalStore
from newsroom.editorial.portals import Portal
from newsroom.editorial.style import StyleGuide, StyleStore
from newsroom.manager.types import Candidate
from newsroom.personas import Persona, PersonaStore
from newsroom.runner import build_run_deps, roles_for_settings, run_manual
from newsroom.runner.deps import _research_ledger


class _RecordingTool:
    """A ResearchTool stand-in: records the scoping each ``search`` carries and returns no
    hits (so the research loop stalls after one step, leaving an empty ledger)."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def search(self, query, *, sites=None, country=None, language=None):
        self.calls.append({"query": query, "sites": sites, "country": country, "language": language})
        return []

    def fetch(self, url):  # the direct-mode seed seam; unused on the managed path
        return ""


def _settings(fake, tmp_path, **over) -> Settings:
    base = dict(
        persona_db_path=tmp_path / "brain.db",
        inference_base_url=f"{fake.base_url}/v1",
        publish_base_url=fake.base_url,
        operator_token="op-token",
    )
    base.update(over)
    return Settings(**base)


def _candidate(_query):
    return [Candidate(headline="seed", url="", snippet="")]


def _assign(persona_id: str, headline: str = "A targeted story") -> str:
    return json.dumps(
        {"action": "assign", "assignments": [
            {"persona_id": persona_id, "headline": headline, "angle": "cover it", "triage": "new"}
        ]}
    )


def test_manual_targeting_sweeps_the_targeted_authors_own_source_pool(fake, tmp_path):
    conn = open_db(":memory:", check_same_thread=False)
    # Two authors with DISTINCT source pools, plus a global fallback portal that must
    # never be consulted while a targeted author has its own pool.
    portal_store = PortalStore(conn)
    portal_store.create(Portal(domain="clarin.com"))
    portal_store.create(Portal(domain="lanacion.com.ar"))
    portal_store.create(Portal(domain="portal-fallback.test"))
    persona_store = PersonaStore(conn)
    persona_store.create(Persona(id="lara", display_name="Lara", beat="politics",
                                 who_i_am="x", style="y", sources=["clarin.com"]))
    persona_store.create(Persona(id="bea", display_name="Bea", beat="world",
                                 who_i_am="x", style="y", sources=["lanacion.com.ar"]))
    # Arm corroboration so the targeted article DROPS cleanly right after research (the
    # recording tool returns no hits -> an empty ledger), keeping the run short and
    # deterministic. The point under test is the SITES the research seam was swept over.
    StyleStore(conn).add_version(
        StyleGuide(sourcing={"min_sources": 2}), created_by="test", activate=True
    )

    settings = _settings(fake, tmp_path)
    tool = _RecordingTool()
    lock = threading.Lock()
    # The REAL production research seam, but over the recording tool, so the per-author
    # scoping that build_run_deps wires is exercised end to end.
    make_ledger = _research_ledger(
        tool, roles_for_settings(settings), settings,
        portal_store=PortalStore(conn), location_store=LocationStore(conn), lock=lock,
    )
    deps = build_run_deps(
        settings, conn=conn, lock=lock, persona_store=persona_store,
        search_news=_candidate, make_ledger=make_ledger,
    )

    # FIFO: the manager's triage (assign to lara) is the first chat; the research plan
    # (one sub-question) is the second, which makes the loop issue ONE scoped search.
    fake.state.script_chat(_assign("lara"))
    fake.state.script_chat(json.dumps(["what happened"]))

    report = run_manual(deps=deps, persona_ids=["lara"])

    # Only lara was assignable (the run was targeted at her); her article dropped
    # uncorroborated (empty ledger) before any draft.
    assert [a.persona_id for a in report.manifest.assignments] == ["lara"]
    assert [o.status for o in report.outcomes] == ["dropped"]
    assert report.outcomes[0].stop_reason == "uncorroborated"
    assert report.outcomes[0].drafts == 0
    # The research seam WAS consulted, and ONLY over lara's own pool: clarin, never bea's
    # lanacion, never the global fallback. Targeting drove the sweep over her linked pool.
    assert tool.calls, "the research seam was never consulted"
    sites = tool.calls[-1]["sites"]
    assert "clarin.com" in sites
    assert "lanacion.com.ar" not in sites
    assert "portal-fallback.test" not in sites
