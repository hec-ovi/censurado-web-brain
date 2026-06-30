"""WL2: the journalist prompt library is knob-driven and ships the loop spec.

These assertions pin the prompt TEXT (Bucket 2) against the editorial bar: the
cross-source count, the topic cap, and the respin-pass count are client-filled
placeholders, never hardcoded numbers; the evaluate prompt scores all six review
dimensions; and a new workflow.md states the end-to-end loop. The last test starts
the real brain app over a FRESH personas.db with the shipped prompts dir and proves
GET /prompts lists workflow.md among the keys.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from newsroom.brain import create_app
from newsroom.config import Settings, load_settings
from newsroom.prompts import load_prompt


def _journalist(name: str) -> str:
    return load_prompt(load_settings().prompts_dir, "journalist", name)


def test_research_uses_min_sources_knob_not_the_word_five():
    text = _journalist("research.md")
    assert "{{MIN_SOURCES}}" in text
    assert "five" not in text.lower()
    # The prefer-different-platforms guidance is kept.
    assert "PREFER different platforms" in text


def test_finalize_uses_topic_cap_knob_and_themes_plus_entities():
    text = _journalist("finalize.md")
    low = text.lower()
    assert "{{TOPIC_CAP}}" in text
    assert "at most seven" not in low
    # Tags are themes plus named entities, specific over vague.
    assert "themes" in low and "entities" in low
    assert "specific beats vague" in low


def test_respin_states_pass_of_total_passes():
    text = _journalist("respin.md")
    assert "{{PASS_NO}}" in text
    assert "{{RESPIN_PASSES}}" in text
    # The redundancy/compression/wording/slop checks survive.
    low = text.lower()
    assert "redundant" in low
    assert "ai-slop" in low


def test_evaluate_scores_all_six_dimensions():
    text = _journalist("evaluate.md")
    low = text.lower()
    # The six gated dimensions are each named.
    for dim in (
        "cross-sourcing",
        "accents",
        "entities",
        "title and subtitle",
        "compression",
        "non-redundancy",
    ):
        assert dim in low, f"evaluate prompt is missing the {dim!r} dimension"
    # It is knob-driven and returns the structured per-dimension verdict.
    assert "{{MIN_SOURCES}}" in text
    for token in ("{{OUTLINE}}", "{{LEDGER}}", "{{DRAFT}}", "{{STYLE_GUIDE}}"):
        assert token in text, f"evaluate prompt dropped {token}"
    assert "failing_dimensions" in text
    assert '"dimensions"' in text


def test_workflow_lists_the_step_order_with_knobs():
    text = _journalist("workflow.md")
    low = text.lower()
    # The steps appear in order. Anchor on the bold step headings so the {{RESPIN_PASSES}}
    # knob placeholder in the intro does not match the "respin" step early.
    order = [
        "**research",
        "**outline",
        "**draft",
        "**evaluate",
        "**respin",
        "**factcheck",
        "**finalize",
    ]
    positions = [low.index(step) for step in order]
    assert positions == sorted(positions), "workflow steps are out of order"
    # All three knob placeholders are present.
    for token in ("{{MIN_SOURCES}}", "{{RESPIN_PASSES}}", "{{TOPIC_CAP}}"):
        assert token in text, f"workflow prompt dropped {token}"
    # The stop conditions are explicit.
    assert "stop respinning" in low
    assert "do not publish" in low


def test_get_prompts_lists_workflow_on_a_fresh_box(tmp_path):
    # Fresh personas.db, shipped prompts dir: the union now lists workflow.md too.
    settings = Settings(persona_db_path=tmp_path / "brain.db")
    client = TestClient(create_app(settings=settings))
    listing = client.get("/prompts").json()
    assert listing["total"] >= 12
    by_key = {t["key"]: t for t in listing["templates"]}
    assert "journalist/workflow.md" in by_key
    assert by_key["journalist/workflow.md"]["version"] == 0
    assert by_key["journalist/workflow.md"]["created_by"] == "disk"
