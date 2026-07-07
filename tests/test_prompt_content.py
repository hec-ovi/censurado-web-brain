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
        "title and bajada",
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


def test_loop_nodes_carry_global_spanish_format_rules():
    # The Spanish editorial format rules live in the shared workflow loop (they apply to every
    # author), not in any per-persona prompt: politics is written in the third person, gerunds
    # are minimized, titles are objective, and the bajada is bounded.
    draft = _flat("50-draft.md")
    assert "third person" in draft and "politics" in draft
    assert "gerund" in draft
    ev = _flat("60-evaluate.md")
    assert "third person" in ev and "gerund" in ev
    enrich = _flat("80-enrich.md")
    assert "gerund" in enrich
    fin = _flat("90-finalize.md")
    assert "objective" in fin and "20 and 30 words" in fin


def test_finalize_titles_are_objective_and_bajada_is_bounded():
    # Titles are objective and informative and word-capped (max 5 words); the piece carries a
    # title and a single bajada of 20 to 30 words, and no separate subtitle.
    low = _flat("90-finalize.md")
    assert "5 words" in low
    assert "objective" in low
    assert "three authored layers" in low
    assert "bajada" in low
    assert "20 and 30 words" in low
    assert "subtitle" in low  # only to forbid it ("do not write a separate subtitle")


def test_draft_node_demands_entertaining_varied_texture():
    # The draft node pushes an entertaining, alive read that breaks monotony with the
    # renderer's real devices (blockquote pull-quote, list, mid-body image), not a flat wall.
    low = _flat("50-draft.md")
    assert "entertain" in low
    assert "monotony" in low
    assert "blockquote" in low


def test_enrich_node_adds_a_monotony_check():
    # The plain enrich pass also breaks up a flat wall (adding devices drawn from existing
    # text, no new facts), so a dull-shaped piece does not slip through.
    low = _flat("80-enrich.md")
    assert "monotony" in low


def test_publish_and_batch_forbid_running_generate_and_tests():
    # The publish node and the daily-batch skill both tell the driver NOT to run the generate
    # one-shot or the test suite during a walk; the generate watcher rebuilds on its own and
    # the printed preview link is the verification step.
    pub = _flat("99-publish.md")
    assert "watcher rebuilds" in pub
    assert "test suite" in pub
    skill = " ".join((PROMPTS_DIR.parent / "cli" / "skills" / "daily-batch" / "SKILL.md").read_text().lower().split())
    assert "never run" in skill and "test suite" in skill


def test_portal_review_carries_the_day_loader_and_arrange_rules():
    # The standalone arrange walk must name its day loader and the two layout rules the roadmap
    # spec added on top of the existing lead + media/text checkerboard.
    low = _flat("portal-review.md")
    # Step 2 loads the whole day (all authors) in one read.
    assert "archive --day" in low
    # The no-gap rule: a lone trailing piece is promoted to a full single row (role important).
    assert "never leave a gap" in low
    assert "important" in low
    # Content-first pairing overrides the media checkerboard for deliberately related pieces.
    assert "content first" in low
    assert "checkerboard" in low
    # The write shape stays.
    assert "--set-json" in low


def test_batch_plan_forward_points_to_the_portada_arrange():
    # The batch-plan node closes by pointing the sweep's last move at the portada arrange walk,
    # so a driver knows to arrange each day after the queued articles publish.
    low = _flat("10-batch-plan.md")
    assert "step --mode portal-review" in low
    assert "archive --day" in low


def test_topic_cleanse_walk_covers_both_halves_and_agent_detection():
    # The topic-normalization walk must drive BOTH merge halves and state that detection is
    # the operator's (no inference backend), so a driver does not wait on a model that never runs.
    low = _flat("topic-cleanse.md")
    # Article half: the hash-safe brain cleanse writer, dry-run then apply.
    assert "topics cleanse" in low
    assert "--map-file" in low and "--apply" in low
    # Author half: the separate profile-chip write.
    assert "profile-topics" in low
    # Uses the phase-3 inventory verb; cross-references (does not duplicate) normalize-topics.
    assert "cli/censurado.py topics" in low
    assert "normalize-topics" in low
    # Registry reconcile is automated with the remove-topic verb (v1 default), not manual-only.
    assert "remove-topic" in low
    # Agent-side detection + the safety rails: dry-run gate, over-merge hazard, hash stability.
    assert "no model runs" in low
    assert "dry run" in low or "dry-run" in low
    assert "over-merg" in low
    assert "hash" in low


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
