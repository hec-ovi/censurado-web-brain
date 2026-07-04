"""The workflow step-gate library is parameter-driven and gates the editorial bar.

These assertions pin the prompt TEXT of the LIVE step-gate nodes (the ``workflow/*``
prompts the CLI ``step`` verb walks one at a time) against the editorial bar: the
cross-source count, the topic cap, and the respin-pass count are client-filled
placeholders, never hardcoded numbers; the evaluate node gates all six review
dimensions; and the drafting nodes carry the anti-slop discipline. The last test reads the
manifest off disk and proves every workflow node it references (plus persona/synthesize) is
present, so the step gate never walks into a missing file.
"""

from __future__ import annotations

import json
from pathlib import Path

from newsroom.prompts import load_prompt

# The prompt recipe is on-disk files in this repo's prompts/ dir (git is their history).
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _workflow(name: str) -> str:
    return load_prompt(PROMPTS_DIR, "workflow", name)


def _flat(name: str) -> str:
    # Lowercased with whitespace collapsed to single spaces, so a phrase that wraps
    # across a markdown line break still matches as a substring.
    return " ".join(_workflow(name).lower().split())


def test_research_uses_min_sources_and_per_lean_parameters_not_hardcoded_numbers():
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


def test_finalize_uses_topic_cap_parameter_and_themes_plus_entities():
    text = _workflow("90-finalize.md")
    low = _flat("90-finalize.md")
    assert "{{TOPIC_CAP}}" in text
    assert "at most seven" not in low
    # Tags are themes plus named entities, a few sharp ones over a loose list.
    assert "themes" in low and "entities" in low
    assert "sharp tags" in low


def test_respin_states_the_pass_budget_parameter():
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
    # It is parameter-driven and is an explicit publish gate with a per-dimension verdict.
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


def test_every_manifest_workflow_node_and_persona_synthesize_exist_on_disk():
    # The step gate serves nodes straight off disk, so every node the manifest references
    # (plus persona/synthesize, the author-synthesis prompt) must actually be present or a
    # walk hits a missing file. This is the filesystem twin of the old GET /prompts listing.
    manifest = json.loads((PROMPTS_DIR / "workflow" / "manifest.json").read_text())
    need = {"00-mode"} | {k for keys in (manifest.get("modes") or {}).values() for k in keys}
    assert len(need) >= 12, "the manifest lists suspiciously few workflow nodes"
    for key in sorted(need):
        node = PROMPTS_DIR / "workflow" / f"{key}.md"
        assert node.is_file() and node.read_text().strip(), f"missing/empty workflow node {key}.md"
    synth = PROMPTS_DIR / "persona" / "synthesize.md"
    assert synth.is_file() and synth.read_text().strip(), "missing persona/synthesize.md"
