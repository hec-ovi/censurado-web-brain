"""FINALIZE: structure the finished article into the publish payload (B.3).

This is the ONE place the harness uses pydantic-ai, for its earned weight: typed
structured-output validation with a single re-prompt on failure. A dedicated
prompted-JSON call asks the model for the article's CONTENT fields (title, body,
topics, optional slug) as a ``FinalizedDraft``; pydantic-ai validates the reply and,
on a parse or validation failure, re-prompts exactly once (``retries={'output': 1}``)
with the error. No output-length cap is ever set (the body is unbounded), so
structure comes from validate-and-retry, never from truncation.

``author`` and ``section`` are NOT taken from the model: ``author`` is the persona
id and ``section`` is the assignment's section, both authoritative. This keeps a
hallucinated byline or a stray section out of the published payload by
construction. A reserved ``metadata.newsroom`` provenance namespace is stamped (it
does not affect the content hash, which is over title/body/author/section, so it is
safe for idempotency).

The model is reached over the same OpenAI-dialect endpoint as the rest of the
harness via pydantic-ai's ``OpenAIChatModel`` + ``OpenAIProvider``, so the finalize
call hits the same backend (and, in tests, the same recording fake) as every other
generation.
"""

from __future__ import annotations

from pydantic_ai import Agent, PromptedOutput
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from newsroom.contracts.article import FinalizedDraft, PublishArticleInput
from newsroom.inference.adapter import retry_transient
from newsroom.inference.provider import ProviderConfig
from newsroom.prompts import load_prompt, render
from newsroom.research.ledger import Ledger

__all__ = ["finalize_article"]


def _agent(cfg: ProviderConfig) -> Agent:
    """A pydantic-ai agent that returns a validated ``FinalizedDraft`` via prompted
    JSON, re-prompting exactly once on failure. No ``max_tokens`` is configured."""
    model = OpenAIChatModel(
        cfg.model,
        provider=OpenAIProvider(base_url=cfg.base_url, api_key=cfg.api_key or "none"),
    )
    return Agent(model, output_type=PromptedOutput(FinalizedDraft), retries={"output": 1})


def finalize_article(
    article_text: str,
    *,
    section: str,
    author: str,
    cfg: ProviderConfig,
    prompts_dir,
    ledger: Ledger,
    run_id: str,
    sweeps: int,
    budget=None,
) -> PublishArticleInput:
    """Structure ``article_text`` into a validated ``PublishArticleInput``. The
    model authors only the content fields; ``author`` and ``section`` are set here.
    Raises pydantic-ai's error if validation still fails after the one retry (the
    caller treats that as a finalize failure, never a truncated publish)."""
    template = load_prompt(prompts_dir, "journalist", "finalize.md")
    prompt = render(template, article=article_text, section=section, author=author)

    result = retry_transient(lambda: _agent(cfg).run_sync(prompt))
    if budget is not None:
        budget.debit_tokens(getattr(result.usage, "total_tokens", 0) or 0)
    draft: FinalizedDraft = result.output

    metadata = {
        "newsroom": {
            "run_id": run_id,
            "persona_id": author,
            "model": cfg.model,
            "sweeps": sweeps,
            "ledger_digest": ledger.digest(),
        }
    }
    return PublishArticleInput(
        title=draft.title,
        body=draft.body,
        author=author,
        section=section,
        topics=draft.topics,
        slug=draft.slug,
        metadata=metadata,
    )
