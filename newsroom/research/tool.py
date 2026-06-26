"""The journalists' research tool: a thin wrapper over the ``websearch-skill``
library (github.com/hec-ovi/websearch-skill), reused whole.

The harness does NOT reimplement search. It builds one ``AgentIO`` from
websearch-skill and calls ``web_search``, mapping the result Envelope's hits into
the harness's own ``SearchResult`` rows. Search is time-filtered (``freshness``)
toward recent sources, which is what keeps coverage fresh (architecture doc,
coverage-memory appendix). websearch-skill is imported LAZILY, inside the call, so
nothing else in the harness pulls the dependency until research actually runs (the
"wire it in just in time" rule).

An error Envelope (no engines, all engines failed, a blocked fetch) degrades to an
empty result list with the reason recorded in ``warnings`` rather than raising, so
the research loop treats a dud search as a no-progress step (it stalls and stops)
instead of crashing the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["SearchResult", "ResearchTool"]


@dataclass
class SearchResult:
    """One search hit, mapped from a websearch-skill ``AgentSearchHit``."""

    url: str
    snippet: str = ""
    title: str = ""
    published: str | None = None
    rank: int = 0


class ResearchTool:
    """Time-filtered web search over websearch-skill. Pass an explicit ``agent``
    (a built ``AgentIO``) in tests to inject a faked search backend; in production
    it builds the default keyless agent lazily on first use."""

    def __init__(self, agent=None, *, freshness: str = "month", max_results: int = 8) -> None:
        self._agent = agent
        self._freshness = freshness
        self._max_results = max_results
        self.warnings: list[str] = []

    def _ensure_agent(self):
        if self._agent is None:
            from websearch.layer3_agentio import build_agent_io

            self._agent = build_agent_io()
        return self._agent

    def search(
        self,
        query: str,
        *,
        sites: list[str] | None = None,
        country: str | None = None,
        language: str | None = None,
    ) -> list[SearchResult]:
        """Run one time-filtered search, optionally SCOPED to a set of source
        domains and a place. ``sites`` confines results to those domains; ``country``
        and ``language`` scope the place. All are optional, so the unscoped call is
        byte-for-byte the previous behavior. Returns mapped hits, or [] on an error
        Envelope (the reason is appended to ``warnings``).

        The backend's ``site`` field takes a SINGLE host, so one domain uses it
        directly (the engine adds a ``site:`` operator) while several are folded into
        a ``(site:a OR site:b)`` group on the query, the portable way to confine a
        search to multiple sources in one call (one call keeps the research loop's
        per-step budget bound intact, vs one search per domain)."""
        from websearch.layer3_agentio import AgentSearchRequest

        domains = [d for d in (sites or []) if d and d.strip()]
        scoped_query = query
        site: str | None = None
        if len(domains) == 1:
            site = domains[0]
        elif len(domains) > 1:
            group = " OR ".join(f"site:{d}" for d in domains)
            scoped_query = f"{query} ({group})"

        agent = self._ensure_agent()
        env = agent.web_search(
            AgentSearchRequest(
                query=scoped_query,
                max_results=self._max_results,
                freshness=self._freshness,
                site=site,
                country=country or None,
                language=language or None,
            )
        )
        if not env.ok:
            self.warnings.append(f"search failed for {query!r}: {env.error}")
            return []
        data = env.data
        if hasattr(data, "model_dump"):
            data = data.model_dump()
        data = data or {}
        return [
            SearchResult(
                url=hit.get("url", ""),
                snippet=hit.get("snippet") or "",
                title=hit.get("title") or "",
                published=hit.get("published"),
                rank=hit.get("rank", 0),
            )
            for hit in data.get("results", [])
        ]
