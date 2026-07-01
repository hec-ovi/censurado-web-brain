"""The workflow step-gate library is knob-driven and gates the editorial bar.

These assertions pin the prompt TEXT of the LIVE step-gate nodes (the ``workflow/*``
prompts the CLI ``step`` verb walks one at a time) against the editorial bar: the
cross-source count, the topic cap, and the respin-pass count are client-filled
placeholders, never hardcoded numbers; the evaluate node gates all six review
dimensions; and the drafting nodes carry the anti-slop discipline. The last test starts
the real brain app over a FRESH personas.db with the shipped prompts dir and proves
GET /prompts lists the workflow nodes among the keys.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from newsroom.brain import create_app
from newsroom.config import Settings, load_settings
from newsroom.prompts import load_prompt


def _workflow(name: str) -> str:
    return load_prompt(load_settings().prompts_dir, "workflow", name)


def _flat(name: str) -> str:
    # Lowercased with whitespace collapsed to single spaces, so a phrase that wraps
    # across a markdown line break still matches as a substring.
    return " ".join(_workflow(name).lower().split())


def test_research_uses_min_sources_and_per_lean_knobs_not_hardcoded_numbers():
    text = _workflow("30-research.md")
    low = _flat("30-research.md")
    # The floor and the per-lean minimum are client-filled placeholders, never literals.
    assert "{{MIN_SOURCES}}" in text
    assert "{{MIN_PER_TYPE}}" in text
    assert "five" not in low and "six" not in low
    # The research node balances sources by political lean and names the web-search fallback.
    assert "lean" in low
    assert "right, neutral, left" in low
    assert "web search" in low


def test_finalize_uses_topic_cap_knob_and_themes_plus_entities():
    text = _workflow("90-finalize.md")
    low = _flat("90-finalize.md")
    assert "{{TOPIC_CAP}}" in text
    assert "at most seven" not in low
    # Tags are themes plus named entities, a few sharp ones over a loose list.
    assert "themes" in low and "entities" in low
    assert "sharp tags" in low


def test_respin_states_the_pass_budget_knob():
    text = _workflow("70-respin.md")
    low = _flat("70-respin.md")
    assert "{{RESPIN_PASSES}}" in text
    # The redundancy/wording/slop checks survive.
    assert "repeated" in low
    assert "ai-slop" in low


def test_evaluate_gates_all_six_dimensions():
    text = _workflow("60-evaluate.md")
    low = _flat("60-evaluate.md")
    # The six gated dimensions are each named.
    for dim in (
        "cross-sourcing",
        "accents",
        "entities",
        "title and subtitle",
        "compression",
        "non-redundancy",
    ):
        assert dim in low, f"evaluate node is missing the {dim!r} dimension"
    # It is knob-driven and is an explicit publish gate with a per-dimension verdict.
    assert "{{MIN_SOURCES}}" in text
    assert "gate" in low
    assert "pass" in low and "revise" in low


def test_draft_node_enforces_the_anti_slop_discipline():
    # The drafting node carries the say-each-idea-once / no-over-hedging rule that every
    # author writes under.
    low = _flat("50-draft.md")
    assert "say each idea once" in low
    assert "synonyms for the same hedge" in low
    assert "over-hedging" in low


def test_get_prompts_lists_the_workflow_nodes_on_a_fresh_box(tmp_path):
    # Fresh personas.db, shipped prompts dir: the union lists the workflow step-gate nodes
    # and persona/synthesize, each served from disk (version 0, created_by "disk").
    settings = Settings(persona_db_path=tmp_path / "brain.db")
    client = TestClient(create_app(settings=settings))
    listing = client.get("/prompts").json()
    assert listing["total"] >= 12
    by_key = {t["key"]: t for t in listing["templates"]}
    for key in ("workflow/50-draft.md", "workflow/90-finalize.md", "persona/synthesize.md"):
        assert key in by_key, f"fresh-box listing dropped {key}"
        assert by_key[key]["version"] == 0
        assert by_key[key]["created_by"] == "disk"
