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

from newsroom.config import Settings
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
    )


def _candidate_search(tool: ResearchTool) -> NewsSearch:
    """The manager's news search: a web search mapped to candidate stories. A hit's
    title becomes the candidate headline (or its snippet when the engine gave none);
    the manager reasons over these and writes its own assignment headlines."""

    def search(query: str) -> list[Candidate]:
        return [
            Candidate(headline=(hit.title or hit.snippet), url=hit.url, snippet=hit.snippet)
            for hit in tool.search(query)
        ]

    return search


def _research_ledger(tool: ResearchTool, roles: ResolvedRoles, settings: Settings) -> LedgerBuilder:
    """Build the grounding ledger for one assignment by running the bounded research
    loop (Step 4) over its story. Bounded by ``max_research_steps`` + the ledger-stall
    detector, so it always terminates; the single planning call uses the drafter's
    backend (the journalist planning their own research). Research DEBITS the article's
    shared budget (the plan call's tokens + the per-search gate), so it charges the same
    per-article ceiling as the pipeline (architecture A.8/B.2), not a separate one."""

    def make_ledger(assignment, spec, budget) -> Ledger:
        topic = spec.headline or spec.angle or assignment.angle
        ledger = Ledger()
        run_research(
            topic,
            tool.search,
            cfg=roles.drafter,
            prompts_dir=settings.prompts_dir,
            ledger=ledger,
            max_steps=settings.max_research_steps,
            stall_limit=settings.research_stall_limit,
            budget=budget,
        )
        return ledger

    return make_ledger


def build_run_deps(
    settings: Settings,
    *,
    conn,
    lock: threading.Lock,
    persona_store: PersonaStore,
    roles: ResolvedRoles | None = None,
    search_news: NewsSearch | None = None,
    make_ledger: LedgerBuilder | None = None,
) -> RunDeps:
    """Assemble the run dependencies over the brain's shared connection.

    ``conn``, ``lock``, and ``persona_store`` come from ``create_app`` so runs share
    the one connection the synthesis path already uses. The two network seams
    (``search_news``, ``make_ledger``) can be overridden for tests; by default they
    are wired to one shared research tool (its web-search backend is built lazily on
    first use)."""
    roles = roles or roles_for_settings(settings)
    tool = ResearchTool(freshness="month")
    return RunDeps(
        store=RunStore(conn),
        persona_store=persona_store,
        coverage_store=CoverageStore(conn),
        roles=roles,
        search_news=search_news or _candidate_search(tool),
        make_ledger=make_ledger or _research_ledger(tool, roles, settings),
        publish_base_url=str(settings.publish_base_url).rstrip("/"),
        operator_token=settings.operator_token,
        prompts_dir=settings.prompts_dir,
        settings=settings,
        lock=lock,
    )
