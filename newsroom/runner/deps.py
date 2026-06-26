"""Production assembly of ``RunDeps``: turn settings + the environment into the
concrete seams ``execute_run`` drives.

This is the one place that reaches the outside world for a run:

  * ``roles_for_settings``  resolves every inference role to the brain's configured
                            backend (the local Gemma). All roles share one endpoint
                            by default, so the evaluator degrades to the rules-grounded
                            check (the supported single-backend mode). A deployment
                            that wants a distinct evaluator uses per-role env vars via
                            ``manager.resolve_roles`` on the standalone path.
  * ``_candidate_search``   the manager's news search: the research tool's web search,
                            mapped to candidate stories.
  * ``_research_ledger``    the per-assignment grounding: runs the bounded research
                            loop (Step 4) for the story and returns its filled ledger.

A test does NOT call this; it injects in-process doubles for the two network seams
so a run never leaves the box. ``build_run_deps`` accepts overrides for exactly
that reason.
"""

from __future__ import annotations

import os
import threading
from contextlib import nullcontext

from newsroom.config import Settings
from newsroom.editorial import LocationStore, PortalStore, StyleStore
from newsroom.editorial.portals import normalize_domain
from newsroom.imagery.comfy_client import ComfyClient
from newsroom.imagery.illustrator import Illustrator
from newsroom.inference.provider import DEFAULT_MODEL, DIALECTS, ProviderConfig, resolve
from newsroom.manager.coverage import CoverageStore
from newsroom.manager.dispatch import LedgerBuilder
from newsroom.manager.manager import NewsSearch
from newsroom.manager.preflight import ResolvedRoles
from newsroom.manager.types import Candidate
from newsroom.personas import PersonaStore
from newsroom.research.ledger import Ledger
from newsroom.research.loop import run_research
from newsroom.research.tool import ResearchTool
from newsroom.runner.run import RunDeps
from newsroom.runs import RunStore

__all__ = ["roles_for_settings", "build_run_deps"]


def _role_base_url(role: str, default_base: str) -> str:
    """The backend a role talks to: a per-role ``NEWSROOM_ROLE_<role>_BASE_URL``
    override if set, else the brain's configured inference endpoint. A shared
    ``NEWSROOM_INFERENCE_BASE_URL`` override already flows through ``default_base``
    (Settings reads the same var), so only the per-role override is consulted here."""
    key = role.upper().replace("-", "_")
    override = os.getenv(f"NEWSROOM_ROLE_{key}_BASE_URL")
    return override.rstrip("/") if override else default_base


def _role_cfg(role: str, default_base: str) -> ProviderConfig:
    """A provider config for one role. Model/key/caps come from the env cascade
    (``resolve``); the base URL is the brain's configured backend unless a per-role
    ``NEWSROOM_ROLE_<role>_BASE_URL`` override is set. So the HTTP brain reaches
    whatever ``inference_base_url`` points at (the fake in tests) while still allowing
    a distinct evaluator endpoint via env."""
    caps = DIALECTS["local"]
    resolved = resolve(role)  # picks up NEWSROOM_ROLE_<role>_MODEL / API_KEY if set
    return ProviderConfig(
        role=role,
        provider="local",
        base_url=_role_base_url(role, default_base),
        model=resolved.model or DEFAULT_MODEL,
        api_key=resolved.api_key,
        **caps,
    )


def roles_for_settings(settings: Settings) -> ResolvedRoles:
    """Resolve the four inference roles to the brain's configured backend.

    By default all roles share the one endpoint ``inference_base_url`` names (the local
    Gemma), so the evaluator is NOT distinct from the drafter and the per-article
    pipeline uses the rules-grounded evaluator (the documented single-backend degrade).
    ``evaluator_distinct`` is COMPUTED from the resolved endpoints (not hardcoded), so a
    per-role evaluator override that diverges the endpoint is reported honestly, exactly
    like ``manager.resolve_roles``."""
    base = str(settings.inference_base_url).rstrip("/")
    drafter = _role_cfg("drafter", base)
    evaluator = _role_cfg("evaluator", base)
    return ResolvedRoles(
        drafter=drafter,
        evaluator=evaluator,
        finalize=_role_cfg("finalize", base),
        manager=_role_cfg("manager", base),
        evaluator_distinct=drafter.endpoint_id != evaluator.endpoint_id,
        art_director=_role_cfg("art_director", base),
    )


def _author_domains(persona) -> list[str]:
    """The bare domains a persona researches: its own ``sources`` normalized (a URL or
    host reduced to ``clarin.com``), de-duplicated, order preserved. Empty when the
    persona has no own pool, so the caller falls back to the enabled portals."""
    out: list[str] = []
    for raw in getattr(persona, "sources", None) or []:
        domain = normalize_domain(str(raw))
        if domain and domain not in out:
            out.append(domain)
    return out


def _candidate_search(
    tool: ResearchTool,
    location_store: LocationStore | None = None,
    lock: threading.Lock | None = None,
) -> NewsSearch:
    """The manager's news search: a web search mapped to candidate stories. A hit's
    title becomes the candidate headline (or its snippet when the engine gave none);
    the manager reasons over these and writes its own assignment headlines.

    Discovery is PLACE-scoped: the operator-editable location supplies the search
    country (its region / Google-News ``gl``) and language, read fresh per call under
    the shared connection lock so a console edit takes effect on the next run without a
    restart. No location store means an unscoped, open-web search (unchanged)."""
    guard = lock if lock is not None else nullcontext()

    def search(query: str) -> list[Candidate]:
        country = language = None
        if location_store is not None:
            with guard:
                loc = location_store.get()
            country, language = loc.region, loc.language
        return [
            Candidate(headline=(hit.title or hit.snippet), url=hit.url, snippet=hit.snippet)
            for hit in tool.search(query, country=country, language=language)
        ]

    return search


def _research_ledger(
    tool: ResearchTool,
    roles: ResolvedRoles,
    settings: Settings,
    *,
    portal_store: PortalStore | None = None,
    location_store: LocationStore | None = None,
    lock: threading.Lock | None = None,
) -> LedgerBuilder:
    """Build the grounding ledger for one assignment by running the bounded research
    loop (Step 4) over its story. Bounded by ``max_research_steps`` + the ledger-stall
    detector, so it always terminates; the single planning call uses the drafter's
    backend (the journalist planning their own research). Research DEBITS the article's
    shared budget (the plan call's tokens + the per-search gate), so it charges the same
    per-article ceiling as the pipeline (architecture A.8/B.2), not a separate one.

    The author's research is SOURCE-scoped: each persona searches its OWN source pool
    (``persona.sources``) when it has one, else the globally enabled portals, so the
    conspiracy persona and the tech persona never read the same web. The portal
    allowlist and the location are operator-editable, so they are read fresh per
    assignment under the shared connection lock (a brief read, released before the
    search/inference runs)."""

    def make_ledger(assignment, spec, persona, budget) -> Ledger:
        topic = spec.headline or spec.angle or assignment.angle
        sites = _author_domains(persona) if persona is not None else []
        country = language = None
        # Fall back to the enabled portals when the author has no own pool, and read
        # the editable portal/location config fresh, under the lock (worker thread).
        with (lock if lock is not None else nullcontext()):
            if not sites and portal_store is not None:
                sites = portal_store.domains()
            if location_store is not None:
                loc = location_store.get()
                country, language = loc.region, loc.language

        def scoped(query: str):
            return tool.search(query, sites=sites, country=country, language=language)

        ledger = Ledger()
        run_research(
            topic,
            scoped,
            cfg=roles.drafter,
            prompts_dir=settings.prompts_dir,
            ledger=ledger,
            max_steps=settings.max_research_steps,
            stall_limit=settings.research_stall_limit,
            budget=budget,
        )
        return ledger

    return make_ledger


def _build_illustrator(settings: Settings, roles: ResolvedRoles) -> Illustrator | None:
    """Assemble the art-director image seam from settings, or None when no art-director
    role resolved. The media endpoint is the article publish host unless
    ``NEWSROOM_MEDIA_BASE_URL`` overrides it. Whether it actually runs for a given run is
    decided later (settings.auto_generate_image + the per-run flag), in ``execute_run``."""
    if roles.art_director is None:
        return None
    media_base = str(settings.media_base_url or settings.publish_base_url).rstrip("/")
    return Illustrator(
        comfy=ComfyClient(base_url=str(settings.comfyui_base_url).rstrip("/")),
        art_director_cfg=roles.art_director,
        prompts_dir=settings.prompts_dir,
        media_base_url=media_base,
        operator_token=settings.operator_token,
        workflow=settings.image_workflow,
        width=settings.image_width,
        height=settings.image_height,
        steps=settings.image_steps,
        reference_limit=settings.reference_image_limit,
    )


def build_run_deps(
    settings: Settings,
    *,
    conn,
    lock: threading.Lock,
    persona_store: PersonaStore,
    roles: ResolvedRoles | None = None,
    search_news: NewsSearch | None = None,
    make_ledger: LedgerBuilder | None = None,
    fetch=None,
    illustrate=None,
) -> RunDeps:
    """Assemble the run dependencies over the brain's shared connection.

    ``conn``, ``lock``, and ``persona_store`` come from ``create_app`` so runs share
    the one connection the synthesis path already uses. The network seams
    (``search_news``, ``make_ledger``, ``illustrate``) can be overridden for tests; by
    default they are wired to one shared research tool (its web-search backend is built
    lazily on first use) and the production art-director illustrator."""
    roles = roles or roles_for_settings(settings)
    tool = ResearchTool(freshness="month")
    # The operator-editable sourcing config (portal allowlist + location), read fresh
    # per run inside the seams so a console edit takes effect without a restart.
    portal_store = PortalStore(conn)
    location_store = LocationStore(conn)
    # The cross-source corroboration resolver, built ONCE here (the single place that
    # reaches the store): a domain -> ownership_group map over the registered portals, so
    # the pure corroboration gate can collapse co-owned outlets to one independent source
    # without itself touching the database. Only portals that declare an ownership_group
    # are mapped; everything else resolves to "" and the gate falls back to a per-domain
    # sentinel. Snapshotted at deps-build time (a console edit takes effect next run).
    ownership_map = {
        normalize_domain(p.domain): p.ownership_group
        for p in portal_store.list()
        if p.ownership_group
    }

    def ownership_of(domain: str) -> str:
        return ownership_map.get(domain, "")

    return RunDeps(
        store=RunStore(conn),
        persona_store=persona_store,
        coverage_store=CoverageStore(conn),
        roles=roles,
        search_news=search_news or _candidate_search(tool, location_store, lock),
        make_ledger=make_ledger
        or _research_ledger(
            tool, roles, settings,
            portal_store=portal_store, location_store=location_store, lock=lock,
        ),
        publish_base_url=str(settings.publish_base_url).rstrip("/"),
        operator_token=settings.operator_token,
        prompts_dir=settings.prompts_dir,
        settings=settings,
        lock=lock,
        illustrate=illustrate if illustrate is not None else _build_illustrator(settings, roles),
        style_store=StyleStore(conn),
        # The direct-from-link seed seam: the same research tool's page fetch (its
        # backend builds lazily on first use), overridable for tests.
        fetch=fetch if fetch is not None else tool.fetch,
        ownership_of=ownership_of,
    )
