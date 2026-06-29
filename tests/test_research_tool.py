"""ResearchTool over the REAL websearch-skill library, with the search backend
faked (no network).

websearch-skill's documented test seam is ``build_agent_io(ddgs_factory=...)`` to
inject a fake DDGS plus ``enable_curl_cffi=False`` so no fetch tier is touched.
These tests prove the harness's thin wrapper maps the Envelope's hits into
``SearchResult`` rows, that time-filtering (``freshness``) actually reaches the
backend, that ``max_results`` trims, and that an error Envelope degrades to [].
"""

from __future__ import annotations

from types import SimpleNamespace

# websearch-skill is a hard dependency (pyproject [project.dependencies]); a plain
# import means a missing/broken install fails loudly here rather than skipping.
from websearch.layer3_agentio import build_agent_io

from newsroom.research import ResearchTool, SearchResult


class FakeDDGS:
    """A fake DDGS that records the kwargs it received and returns canned rows."""

    last_kwargs: dict = {}

    def __init__(self, rows):
        self._rows = rows

    def text(self, query, **kwargs):
        FakeDDGS.last_kwargs = {"query": query, **kwargs}
        return list(self._rows)


def _agent_returning(rows):
    return build_agent_io(ddgs_factory=lambda *a, **k: FakeDDGS(rows), enable_curl_cffi=False)


_ROWS = [
    {"title": "AP report", "href": "https://apnews.com/a", "body": "the count was certified"},
    {"title": "Reuters", "href": "https://reuters.com/b", "body": "officials confirmed"},
]


def test_search_maps_hits_to_search_results():
    tool = ResearchTool(agent=_agent_returning(_ROWS))
    results = tool.search("who certified the count")
    assert all(isinstance(r, SearchResult) for r in results)
    urls = {r.url for r in results}
    assert urls == {"https://apnews.com/a", "https://reuters.com/b"}
    ap = next(r for r in results if r.url == "https://apnews.com/a")
    assert ap.snippet == "the count was certified"
    assert ap.title == "AP report"
    assert ap.rank >= 1


def test_freshness_reaches_the_backend_as_a_time_filter():
    tool = ResearchTool(agent=_agent_returning(_ROWS), freshness="month")
    tool.search("recent developments")
    # websearch-skill maps freshness -> ddgs timelimit ("month" -> "m").
    assert FakeDDGS.last_kwargs.get("timelimit") == "m"
    assert FakeDDGS.last_kwargs.get("query") == "recent developments"


def test_freshness_any_sends_no_time_filter():
    tool = ResearchTool(agent=_agent_returning(_ROWS), freshness="any")
    tool.search("q")
    assert "timelimit" not in FakeDDGS.last_kwargs


def test_per_call_freshness_overrides_the_instance_default():
    # A batch sweep narrows the default month to "day" for THIS call only; the instance
    # default is untouched for later calls.
    tool = ResearchTool(agent=_agent_returning(_ROWS), freshness="month")
    tool.search("todays news", freshness="day")
    assert FakeDDGS.last_kwargs.get("timelimit") == "d"  # day -> "d"
    tool.search("default call")
    assert FakeDDGS.last_kwargs.get("timelimit") == "m"  # falls back to the month default


def test_max_results_trims_the_returned_hits():
    many = [{"title": f"t{i}", "href": f"https://s{i}.test/x", "body": "b"} for i in range(10)]
    tool = ResearchTool(agent=_agent_returning(many), max_results=3)
    assert len(tool.search("q")) == 3


def test_error_envelope_degrades_to_empty_with_a_warning():
    # No engines enabled -> web_search returns an error Envelope (ok=False).
    tool = ResearchTool(agent=build_agent_io(enable_ddgs=False, enable_curl_cffi=False))
    results = tool.search("anything")
    assert results == []
    assert tool.warnings and "anything" in tool.warnings[0]


# ----- scoping: source domains + place reach the search request -----


class _RecordingAgent:
    """An AgentIO double that records each AgentSearchRequest and returns an empty,
    OK envelope, so a scoping call can be inspected without a network round-trip."""

    def __init__(self):
        self.requests = []

    def web_search(self, req):
        self.requests.append(req)
        return SimpleNamespace(ok=True, data={"results": []}, error=None)


def test_single_site_uses_the_structured_site_field():
    agent = _RecordingAgent()
    ResearchTool(agent=agent).search("milei ajuste", sites=["clarin.com"])
    req = agent.requests[-1]
    assert req.site == "clarin.com"
    assert req.query == "milei ajuste"  # not OR-folded for a single source


def test_multiple_sites_fold_into_an_or_group_on_the_query():
    agent = _RecordingAgent()
    ResearchTool(agent=agent).search("milei ajuste", sites=["clarin.com", "lanacion.com.ar"])
    req = agent.requests[-1]
    assert req.site is None
    assert "(site:clarin.com OR site:lanacion.com.ar)" in req.query


def test_country_and_language_reach_the_request():
    agent = _RecordingAgent()
    ResearchTool(agent=agent).search("q", country="AR", language="es")
    req = agent.requests[-1]
    assert req.country == "AR" and req.language == "es"


def test_unscoped_search_carries_no_scoping():
    agent = _RecordingAgent()
    ResearchTool(agent=agent).search("q")
    req = agent.requests[-1]
    assert req.site is None and req.country is None and req.language is None
    assert req.query == "q"
