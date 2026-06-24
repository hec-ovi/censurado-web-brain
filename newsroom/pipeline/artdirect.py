"""ART DIRECT: turn a finished article into one illustration brief.

The art director pairs with the journalist. After an article is finalized, this step
asks a model (persona-AWARE, so the visual carries the author's register) to write a
single FLUX.2-shaped image prompt plus alt text and a choice of which source
reference images, if any, should steer the picture. It mirrors ``finalize.py``: a
dedicated pydantic-ai prompted-JSON call validated into a typed object, re-prompting
exactly once on failure. No output-length cap is ever set (the brief is unbounded);
``ArtDirection.prompt`` is free-form prose the model writes to whatever length the
image needs.

This is the LLM half of the imagery seam. The rendering half (ComfyUI) and the upload
(the platform media endpoint) live in ``newsroom.imagery``; the orchestration that
ties them together is ``newsroom.imagery.illustrator``.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, Field
from pydantic_ai import Agent, PromptedOutput
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from newsroom.inference.provider import ProviderConfig
from newsroom.personas import Persona
from newsroom.pipeline.context import persona_block
from newsroom.prompts import load_prompt, render

__all__ = ["ArtDirection", "art_direct", "render_references"]


class ArtDirection(BaseModel):
    """The art director's brief for one lead illustration. ``prompt`` has no maximum
    length (it is never truncated); ``references`` are indices into the reference list
    the director was shown."""

    model_config = {"extra": "ignore"}

    prompt: str = Field(min_length=1)  # the FLUX.2 image prompt; no maximum length
    alt: str = ""
    references: list[int] = Field(default_factory=list)


def _agent(cfg: ProviderConfig) -> Agent:
    """A pydantic-ai agent returning a validated ``ArtDirection`` via prompted JSON,
    re-prompting once on failure. No ``max_tokens`` is configured."""
    model = OpenAIChatModel(
        cfg.model,
        provider=OpenAIProvider(base_url=cfg.base_url, api_key=cfg.api_key or "none"),
    )
    return Agent(model, output_type=PromptedOutput(ArtDirection), retries={"output": 1})


def render_references(references: Sequence) -> str:
    """Render reference images as a numbered list the model can pick from by index.
    Only the index and the source context are shown (never as instructions); the image
    URLs stay out of the prompt and are mapped back from the chosen indices in code."""
    if not references:
        return "(none)"
    lines = []
    for i, ref in enumerate(references):
        context = (getattr(ref, "context", "") or "").strip() or "a source image"
        lines.append(f"[{i}] depicts: {context}")
    return "\n".join(lines)


def art_direct(
    *,
    title: str,
    body: str,
    section: str,
    persona: Persona,
    references: Sequence,
    cfg: ProviderConfig,
    prompts_dir,
    budget=None,
) -> ArtDirection:
    """Produce one illustration brief for the finished article. Raises pydantic-ai's
    error if validation still fails after the one retry; the caller treats any failure
    as "no image" and publishes the article without one."""
    template = load_prompt(prompts_dir, "art_director", "illustrate.md")
    article_text = f"# {title}\n\n{body}"
    prompt = render(
        template,
        author=persona.display_name or persona.id,
        section=section,
        persona=persona_block(persona),
        article=article_text,
        references=render_references(references),
    )
    result = _agent(cfg).run_sync(prompt)
    if budget is not None:
        budget.debit_tokens(getattr(result.usage, "total_tokens", 0) or 0)
    return result.output
