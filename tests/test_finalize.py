"""FINALIZE: pydantic-ai prompted-JSON validation into the publish payload.

Drives the real finalize seam (pydantic-ai -> the fake's /v1/chat/completions) with
scripted JSON. Proves: a valid reply becomes a PublishArticleInput; author and
section come from the assignment, NOT the model; the provenance namespace is
stamped; and a validation failure re-prompts exactly once. The conftest teardown
guard additionally proves the finalize request carried no length cap.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from newsroom.config import load_settings
from newsroom.inference.provider import DIALECTS, ProviderConfig
from newsroom.pipeline import ArticleBudget
from newsroom.pipeline.finalize import finalize_article
from newsroom.research.ledger import Ledger


def _cfg(fake) -> ProviderConfig:
    return ProviderConfig(
        role="finalizer", provider="local", base_url=f"{fake.base_url}/v1",
        model="finalize-model", **DIALECTS["local"],
    )


def _ledger() -> Ledger:
    led = Ledger(clock=lambda: datetime(2026, 6, 23, tzinfo=timezone.utc))
    led.add(claim="c", url="https://src.test/a", snippet="s")
    return led


def _prompts_dir():
    return load_settings().prompts_dir


def _finalize(fake, **overrides):
    kwargs = dict(
        section="tech", author="ada-reporter", cfg=_cfg(fake), prompts_dir=_prompts_dir(),
        ledger=_ledger(), run_id="run-1", sweeps=2,
    )
    kwargs.update(overrides)
    return finalize_article("the finished article text", **kwargs)


def test_valid_reply_becomes_publish_input_with_forced_author_and_section(fake):
    fake.state.script_chat(json.dumps({
        "title": "A Clear Headline", "body": "# The full body\n\nText.",
        "topics": ["ai", "ai", "policy"], "slug": "a-clear-headline",
    }))
    article = _finalize(fake)
    assert article.title == "A Clear Headline"
    assert article.body == "# The full body\n\nText."
    # author and section are authoritative (the model cannot set them).
    assert article.author == "ada-reporter"
    assert article.section == "tech"
    assert article.slug == "a-clear-headline"
    assert article.topics == ["ai", "policy"]  # deduped


def test_subtitle_and_summary_land_in_metadata(fake):
    # The dek and standalone summary the model authors are stamped into
    # metadata.subtitle / metadata.description (what the public generator renders
    # as the card/article subtitle + standfirst), and stay out of the content hash.
    fake.state.script_chat(json.dumps({
        "title": "A Clear Headline",
        "subtitle": "Por qué esto importa hoy.",
        "summary": "Lo esencial en tres frases, autónomo.",
        "body": "# Body\n\nText.",
    }))
    article = _finalize(fake)
    assert article.metadata["subtitle"] == "Por qué esto importa hoy."
    assert article.metadata["description"] == "Lo esencial en tres frases, autónomo."


def test_absent_subtitle_and_summary_leave_keys_off(fake):
    # Optional: a model that omits them must not fail, and must not stamp empty keys
    # (the generator falls back cleanly when the keys are absent).
    fake.state.script_chat(json.dumps({"title": "T", "body": "B"}))
    article = _finalize(fake)
    assert "subtitle" not in article.metadata
    assert "description" not in article.metadata


def test_provenance_namespace_is_stamped(fake):
    fake.state.script_chat(json.dumps({"title": "T", "body": "B"}))
    article = _finalize(fake)
    ns = article.metadata["newsroom"]
    assert ns["run_id"] == "run-1"
    assert ns["persona_id"] == "ada-reporter"
    assert ns["sweeps"] == 2
    assert ns["model"] == "finalize-model"
    assert ns["ledger_digest"] == _ledger().digest()


def test_validation_failure_reprompts_exactly_once(fake):
    # First reply has an empty title (fails min_length); the retry is valid.
    fake.state.script_chat(json.dumps({"title": "", "body": "B"}))
    fake.state.script_chat(json.dumps({"title": "Recovered", "body": "B2"}))
    article = _finalize(fake)
    assert article.title == "Recovered"
    assert len(fake.state.chat_requests) == 2  # original + one re-prompt


def test_budget_is_debited_without_crashing(fake):
    fake.state.script_chat(json.dumps({"title": "T", "body": "B"}))
    budget = ArticleBudget(token_budget=1_000_000, wall_clock_s=1e9, clock=lambda: 0.0)
    _finalize(fake, budget=budget)
    assert budget.spent_tokens >= 0  # fake reports 0 usage; debit must not raise
