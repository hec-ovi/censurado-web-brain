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


def test_finalize_prompt_carries_headline_discipline():
    # The finalize prompt is where the title and dek are authored. It must ship the
    # honest-but-arresting headline rubric (promise kept, name the concrete noun, the
    # dek does not repeat the title) AND keep the body uncapped and the fixed tokens.
    text = load_prompt(load_settings().prompts_dir, "journalist", "finalize.md")
    low = text.lower()
    # The token contract the pipeline fills.
    for token in ("{{SECTION}}", "{{AUTHOR}}", "{{ARTICLE}}"):
        assert token in text, f"finalize prompt dropped {token}"
    # The honesty gate and the pull hooks are present.
    assert "a headline is a promise" in low
    assert "honesty gate" in low
    assert "no withheld subject" in low or "name the real thing" in low
    assert "stakes" in low
    # The dek must not parrot the title.
    assert "does not repeat the title" in low
    # Bodies are never length-capped.
    assert "no length limit on the body" in low


def test_draft_prompt_enforces_say_each_idea_once():
    # Every author drafts through journalist/draft.md, so the anti-redundancy rule
    # lives there once and applies to all of them: a caveat/disclaimer is stated a
    # single compact time, never stacked into three-to-five synonyms, never echoed
    # in every paragraph.
    text = load_prompt(load_settings().prompts_dir, "journalist", "draft.md")
    low = text.lower()
    assert "say each idea once" in low
    assert "single short compact line" in low
    # The explicit "do not stack N synonyms for the same hedge" guardrail.
    assert "synonyms for the same hedge" in low
    assert "over-hedging" in low


def test_load_prompt_overrides_returns_override_and_falls_back_to_fs():
    prompts_dir = load_settings().prompts_dir
    overrides = {"persona/synthesize.md": "OVERRIDDEN BODY"}

    # A key present in the overrides map short-circuits the file read.
    assert load_prompt(prompts_dir, "persona", "synthesize.md", overrides=overrides) == (
        "OVERRIDDEN BODY"
    )

    # A key NOT in the map falls back to the real on-disk prompt.
    journalist = load_prompt(prompts_dir, "journalist", "research.md", overrides=overrides)
    assert journalist == load_prompt(prompts_dir, "journalist", "research.md")

    # No overrides at all behaves exactly as before (FS read).
    assert load_prompt(prompts_dir, "persona", "synthesize.md", overrides=None) == load_prompt(
        prompts_dir, "persona", "synthesize.md"
    )
