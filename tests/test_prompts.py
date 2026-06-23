"""Prompt loading and single-pass token substitution.

``render`` must substitute ``{{TOKEN}}`` placeholders in ONE pass, so a value that
itself contains a placeholder is never re-expanded, and unknown tokens are left
alone. ``load_prompt`` reads the real versioned prompt files.
"""

from __future__ import annotations

from newsroom.config import load_settings
from newsroom.prompts import load_prompt, render


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
    text = load_prompt(load_settings().prompts_dir, "persona", "synthesize.md")
    assert "You ARE {{DISPLAY_NAME}}" in text
    assert "few_shots_neg" in text
    assert "no length limit" in text.lower()
