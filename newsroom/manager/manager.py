"""The manager: a BOUNDED ReAct loop that turns today's news into a clamped set of
assignments (architecture doc A.6/B.2). It is the SOLE fan-out point in the harness
(journalists never spawn agents), so this is the one place a "how many articles"
decision is made, and it is fenced on every side:

  * ``max_steps`` (``max_manager_steps``)  caps the number of model turns.
  * a tool-call budget                      caps the number of news searches.
  * a circuit breaker                       stops on consecutive no-progress turns
                                            (a malformed action, an identical query,
                                            a search that adds nothing, or a failure),
                                            which also catches K distinct-but-useless
                                            searches the way research's stall detector
                                            does.
  * a forced terminal                       when a cap or the breaker fires WITHOUT an
                                            emit, the manager degrades to whatever it
                                            has ranked (the candidates its searches
                                            surfaced) rather than failing the run.
  * the ``N_MAX`` output clamp              ``len(assignments) <= n_max``, always.

Each turn the model returns ONE JSON action: ``{"action": "search", "query": ...}``
or ``{"action": "assign", "assignments": [...]}``. The emitted assignments pass
through the coverage triage (drop duplicates, mark follow-ups) and the clamp before
they become the manifest. The model's reasoning runs uncapped, like every
generation in the harness; only the NUMBER of turns and searches is bounded.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from newsroom.contracts.sections import SECTION_ENUM
from newsroom.inference import ChatRequest, chat
from newsroom.inference.provider import ProviderConfig
from newsroom.jsonio import extract_json
from newsroom.manager.coverage import CoverageItem, classify, fingerprint, similarity
from newsroom.manager.types import AssignmentSpec, Candidate, Manifest, Triage
from newsroom.personas import Persona
from newsroom.prompts import load_prompt, render

__all__ = ["run_manager", "NewsSearch"]

# A news search is any callable: a query string -> a list of candidate stories.
# In production it wraps the research tool; tests inject a recording double.
NewsSearch = Callable[[str], list[Candidate]]

_MANAGER_TEMP = 0.5  # judgment over live news; uncapped length, like every call


@dataclass
class _Action:
    kind: str  # "search" | "assign" | "malformed"
    query: str = ""
    assignments: list[dict] = field(default_factory=list)


def _parse_action(content: str) -> _Action:
    """Tolerant action parse: a code-fenced or prose-wrapped JSON object with an
    ``action`` field, or a bare assignments array. Anything else is malformed (the
    circuit breaker counts it)."""
    try:
        data = extract_json(content)
    except json.JSONDecodeError:
        return _Action("malformed")
    if isinstance(data, list):
        return _Action("assign", assignments=[a for a in data if isinstance(a, dict)])
    if not isinstance(data, dict):
        return _Action("malformed")
    action = str(data.get("action", "")).strip().lower()
    if action == "assign" or (not action and "assignments" in data):
        raw = data.get("assignments")
        return _Action("assign", assignments=[a for a in raw if isinstance(a, dict)] if isinstance(raw, list) else [])
    if action == "search" or (not action and data.get("query")):
        query = str(data.get("query", "")).strip()
        return _Action("search", query=query) if query else _Action("malformed")
    return _Action("malformed")


def _normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", query.strip().lower())


def _personas_block(personas: list[Persona]) -> str:
    """Each journalist's id, beat, language, identity, and a short voice cue, so the
    manager assigns by FIT (a story to the persona whose beat and voice match), not by
    beat alone. The section a persona writes is still authoritative from its beat; this
    only enriches the model's choice of who covers what."""
    if not personas:
        return "(no personas available)"
    lines = []
    for p in personas:
        line = f"- {p.id} (beat: {p.beat}, writes in {p.language}): {p.who_i_am}"
        if p.style:
            line += f" Voice: {p.style}"
        lines.append(line)
    return "\n".join(lines)


def _coverage_block(coverage: list[CoverageItem]) -> str:
    if not coverage:
        return "(nothing published yet)"
    lines = []
    for c in coverage:
        ref = f" [{c.slug}]" if c.slug else ""
        when = f" ({c.published_at[:10]})" if c.published_at else ""
        lines.append(f"- {c.section}: {c.headline}{ref}{when}")
    return "\n".join(lines)


def _findings_block(candidates: list[Candidate]) -> str:
    if not candidates:
        return "(no stories found yet)"
    return "\n".join(
        f"- {c.headline}" + (f" ({c.url})" if c.url else "") for c in candidates
    )


def _degrade_to_candidates(
    candidates: list[Candidate], personas: list[Persona], n_max: int
) -> list[dict]:
    """The forced-terminal fallback: pair available personas with the stories the
    searches surfaced (the manager's de-facto ranking is search arrival order),
    preferring a candidate whose section matches the persona's beat. Deterministic,
    no model call: it degrades to what was already gathered."""
    raw: list[dict] = []
    used: set[int] = set()
    for persona in personas:
        pick = _next_candidate(candidates, used, beat=persona.beat)
        if pick is None:
            pick = _next_candidate(candidates, used, beat=None)
        if pick is None:
            break  # no stories left to assign
        i, c = pick
        used.add(i)
        raw.append({"persona_id": persona.id, "headline": c.headline, "angle": c.headline,
                    "entities": list(c.entities)})
        if len(raw) >= n_max:
            break
    return raw


def _next_candidate(candidates, used, *, beat):
    for i, c in enumerate(candidates):
        if i in used:
            continue
        if beat is not None and c.section and c.section != beat:
            continue
        return i, c
    return None


def _triage_and_clamp(
    raw: list[dict],
    *,
    persona_index: dict[str, Persona],
    coverage: list[CoverageItem],
    n_max: int,
    duplicate_threshold: float,
    followup_threshold: float,
) -> tuple[list[AssignmentSpec], int]:
    """Turn the model's (or the fallback's) raw assignments into validated specs:
    only real personas, section authoritative from the persona's beat, coverage
    DUPLICATEs dropped (the rules floor), near-identical specs deduped within the
    manifest, and the whole thing clamped to ``n_max``."""
    accepted: list[AssignmentSpec] = []
    accepted_fps: list[frozenset[str]] = []
    dropped = 0
    for item in raw:
        if len(accepted) >= n_max:
            break
        persona = persona_index.get(str(item.get("persona_id", "")).strip())
        if persona is None:
            continue  # the manager can only assign real personas
        angle = str(item.get("angle", "")).strip() or str(item.get("headline", "")).strip()
        if not angle:
            continue
        headline = str(item.get("headline", "")).strip() or angle
        entities = [str(e).strip() for e in (item.get("entities") or []) if str(e).strip()]
        fp = fingerprint(headline=headline, entities=entities)

        verdict = classify(
            fp, coverage, duplicate_threshold=duplicate_threshold, followup_threshold=followup_threshold
        )
        triage = _resolve_triage(verdict, item)
        if triage is Triage.DUPLICATE:
            dropped += 1  # rules floor (or a model-flagged dup): never republish
            continue
        if any(similarity(fp, prev) >= duplicate_threshold for prev in accepted_fps):
            dropped += 1  # the same story assigned twice in one manifest
            continue

        follow_up_slug = None
        if triage is Triage.FOLLOW_UP:
            follow_up_slug = _follow_up_slug(item, verdict, coverage)
            if follow_up_slug is None:
                # A follow-up with no real prior article to attach: cover it as NEW
                # rather than brief the journalist to cite a story that does not exist.
                triage = Triage.NEW
            else:
                angle = _with_follow_up(angle, follow_up_slug)

        accepted.append(
            AssignmentSpec(
                persona_id=persona.id,
                section=persona.beat,  # authoritative: a persona writes their own beat
                angle=angle,
                triage=triage,
                follow_up_slug=follow_up_slug,
                headline=headline,
                entities=entities,  # carried to the article's coverage row for next-run de-dup
            )
        )
        accepted_fps.append(fp)
    return accepted, dropped


def _resolve_triage(verdict, item: dict) -> Triage:
    """Two-layer triage (architecture coverage-memory appendix): the rules are the
    floor, the manager model makes the final call. A rules-DUPLICATE is a HARD floor
    the model cannot override (never republish). Otherwise the model's per-candidate
    ``triage`` verdict wins when it is one of new/follow_up/duplicate, so it can
    demote a rules-FOLLOW_UP to NEW (a genuinely new story that merely shares tokens)
    or flag a duplicate the fingerprint missed; absent a usable hint, the rules
    verdict stands."""
    if verdict.triage is Triage.DUPLICATE:
        return Triage.DUPLICATE
    hint = str(item.get("triage", "")).strip().lower()
    if hint in (Triage.NEW.value, Triage.FOLLOW_UP.value, Triage.DUPLICATE.value):
        return Triage(hint)
    return verdict.triage


def _follow_up_slug(item: dict, verdict, coverage: list[CoverageItem]) -> str | None:
    """The slug of the prior article a FOLLOW_UP extends. Honor the slug the model
    named ONLY when it is a REAL recent coverage slug (a local model can hallucinate
    one, and we must not brief the journalist to cite a non-existent article);
    otherwise fall back to the rules' matched item. Returns None when there is no
    real prior article to attach (the caller then covers the story as NEW)."""
    real_slugs = {c.slug for c in coverage if c.slug}
    matched_slug = verdict.matched.slug if verdict.matched else None
    model_slug = str(item.get("follow_up_slug") or "").strip() or None
    if model_slug and model_slug in real_slugs:
        return model_slug
    return matched_slug


def _with_follow_up(angle: str, slug: str | None) -> str:
    if not slug:
        return angle
    return f"{angle}\n\nThis is a FOLLOW-UP to prior coverage ({slug}): cover only what is new and cite it."


def run_manager(
    *,
    personas: list[Persona],
    coverage: list[CoverageItem],
    search_news: NewsSearch,
    cfg: ProviderConfig,
    prompts_dir: Path | str,
    overrides: dict[str, str] | None = None,
    n_max: int = 4,
    max_steps: int = 8,
    tool_call_budget: int | None = None,
    circuit_breaker: int = 3,
    duplicate_threshold: float = 0.6,
    followup_threshold: float = 0.3,
    budget=None,
) -> Manifest:
    """Run the bounded manager loop and return a clamped, triaged ``Manifest``. The
    run NEVER fails here: a cap, the circuit breaker, or an exhausted budget forces a
    terminal that degrades to whatever the manager has ranked."""
    tool_call_budget = max_steps if tool_call_budget is None else tool_call_budget
    persona_index = {p.id: p for p in personas}
    template = load_prompt(prompts_dir, "manager", "triage.md", overrides=overrides)

    candidates: list[Candidate] = []
    seen: set[str] = set()
    prev_query: str | None = None
    strikes = 0
    tool_calls = 0
    steps = 0
    raw_assignments: list[dict] | None = None
    stop_reason = "max_steps"

    for step in range(max_steps):
        if budget is not None and budget.exhausted():
            stop_reason = "budget_exhausted"
            break
        steps += 1
        prompt = render(
            template,
            beats=", ".join(SECTION_ENUM),
            personas=_personas_block(personas),
            coverage=_coverage_block(coverage),
            findings=_findings_block(candidates),
            n_max=str(n_max),
            step=str(step + 1),
            max_steps=str(max_steps),
        )
        try:
            response = chat(
                ChatRequest(messages=[{"role": "user", "content": prompt}], temperature=_MANAGER_TEMP), cfg=cfg
            )
            if budget is not None:
                budget.debit_response(response)
            action = _parse_action(response.content)
        except Exception:
            # A broken or hostile backend (a 5xx, a timeout, a dropped connection, a
            # body missing "choices") must NOT crash the run: a failed model turn is
            # no progress, symmetric with the guarded search below. It counts toward
            # the circuit breaker and forces a terminal rather than escaping.
            action = _Action("malformed")

        if action.kind == "assign":
            raw_assignments = action.assignments
            stop_reason = "emitted"
            break

        if action.kind == "malformed":
            strikes += 1
            if strikes >= circuit_breaker:
                stop_reason = "circuit_breaker"
                break
            continue

        # search
        if tool_calls >= tool_call_budget:
            stop_reason = "tool_budget"
            break
        normalized = _normalize_query(action.query)
        identical = normalized == prev_query
        prev_query = normalized
        try:
            hits = list(search_news(action.query))
            failed = False
        except Exception:
            hits, failed = [], True
        tool_calls += 1

        new = 0
        for hit in hits:
            key = (hit.url or hit.headline).strip()
            if key and key not in seen:
                seen.add(key)
                candidates.append(hit)
                new += 1
        # No-progress = a failed search, an identical query, or a query that adds no
        # new story. Consecutive no-progress trips the breaker; any progress resets.
        if failed or identical or new == 0:
            strikes += 1
        else:
            strikes = 0
        if strikes >= circuit_breaker:
            stop_reason = "circuit_breaker"
            break

    forced = stop_reason != "emitted"
    if raw_assignments is None:
        raw_assignments = _degrade_to_candidates(candidates, personas, n_max)

    accepted, dropped = _triage_and_clamp(
        raw_assignments,
        persona_index=persona_index,
        coverage=coverage,
        n_max=n_max,
        duplicate_threshold=duplicate_threshold,
        followup_threshold=followup_threshold,
    )
    return Manifest(
        assignments=accepted,
        stop_reason=stop_reason,
        forced=forced,
        steps=steps,
        dropped_duplicates=dropped,
        candidates_considered=len(candidates),
    )
