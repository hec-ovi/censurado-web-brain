"""Prompt loading and single-pass token substitution.

``render`` must substitute ``{{TOKEN}}`` placeholders in ONE pass, so a value that
itself contains a placeholder is never re-expanded, and unknown tokens are left
alone. ``load_prompt`` reads the real on-disk prompt files.
"""

from __future__ import annotations

from pathlib import Path

from newsroom.prompts import load_prompt, render

# The prompt recipe is on-disk files in this repo's prompts/ dir (git is their history).
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def test_render_substitutes_tokens():
    out = render("You ARE {{DISPLAY_NAME}} on the {{BEAT}} beat.", display_name="Ada", beat="tech")
    assert out == "You ARE Ada on the tech beat."


def test_render_leaves_unknown_tokens_intact():
    assert render("hi {{MISSING}}", display_name="Ada") == "hi {{MISSING}}"


def test_render_does_not_cross_substitute():
    # A value containing another token must NOT be re-expanded by a later key.
    out = render("{{DISPLAY_NAME}} || {{SEED}}", display_name="{{SEED}}", seed="THE-SEED")
    assert out == "{{SEED}} || THE-SEED"


def test_render_preserves_literal_json_braces():
    # Single braces (JSON examples in a prompt) are not placeholders and survive.
    template = 'return {"who_i_am": "x"} for {{DISPLAY_NAME}}'
    assert render(template, display_name="Ada") == 'return {"who_i_am": "x"} for Ada'


def test_load_prompt_reads_the_role_play_synthesis_prompt():
    text = load_prompt(PROMPTS_DIR, "persona", "synthesize.md")
    assert "You ARE {{DISPLAY_NAME}}" in text
    assert "few_shots_neg" in text
    assert "no length limit" in text.lower()


def test_finalize_prompt_carries_headline_discipline():
    # The finalize step-gate node is where the title and dek are authored. It must ship
    # the honest-but-arresting headline rubric (promise kept, name the concrete noun, the
    # dek does not repeat the title), keep the body uncapped, and lift the standfirst into
    # the live `description` field (the retired `summary` field is gone).
    text = load_prompt(PROMPTS_DIR, "workflow", "90-finalize.md")
    # Collapse whitespace so a phrase that wraps across a markdown line break still matches.
    low = " ".join(text.lower().split())
    # The topic cap is a client-filled parameter, never a hardcoded number.
    assert "{{TOPIC_CAP}}" in text
    # The hard word cap on titles (the 5-word constraint) is present and prominent.
    assert "no more than 5 words" in low
    # The honesty gate and the pull hooks are present.
    assert "a headline is a promise" in low
    assert "promise kept" in low
    assert "no withheld subject" in low and "name the real thing" in low
    assert "no overclaim" in low
    assert "stakes" in low
    # The dek must not parrot the title.
    assert "does not repeat the title" in low
    # The standfirst is the live `description` field, and the body is never truncated.
    assert "description" in low and "standfirst" in low
    assert "do not truncate" in low


def test_draft_prompt_enforces_say_each_idea_once():
    # Every author drafts through the workflow draft node, so the anti-redundancy rule
    # lives there once and applies to all of them: a caveat/disclaimer is stated a
    # single compact time, never stacked into three-to-five synonyms, never echoed
    # in every paragraph.
    text = load_prompt(PROMPTS_DIR, "workflow", "50-draft.md")
    # Collapse whitespace so a phrase that wraps across a markdown line break still matches.
    low = " ".join(text.lower().split())
    assert "say each idea once" in low
    assert "single short compact line" in low
    # The explicit "do not stack N synonyms for the same hedge" guardrail.
    assert "synonyms for the same hedge" in low
    assert "over-hedging" in low
