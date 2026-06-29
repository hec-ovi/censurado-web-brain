"""The per-article pipeline: outline, the draft/evaluate sweep loop, enrich,
fact-check, finalize (architecture doc B.2/B.3).

Read top to bottom, this is the one model-driven loop in the harness, made bounded
by three guards that all hold at once (A.8):

  * ``for sweep in range(max_sweeps)`` caps the number of drafts.
  * The evaluator's PASS verdict is the only model-driven early exit.
  * No-progress stops the loop when the evaluator flags the IDENTICAL set of failing
    sections two sweeps running (it keeps finding the same unfixed issues): a
    genuine stall, distinct from a healthy revise that fixes one section. This is
    the failing-section-SET test, NOT whole-draft similarity (A.8).

Over all of it sits the shared per-article budget, debited by every stage. On
exhaustion the pipeline DROPS the assignment (``budget_exhausted``) and never
publishes a body: a budget is a loop bound, and dropping (rather than truncating)
keeps it cleanly separate from the no-output-cap rule. The draft generation itself
is never capped; only the NUMBER of drafts is bounded.

The persona is re-injected FRESH on every draft and the draft runs warm; enrich and
fact-check run PERSONA-BLIND; finalize structures the result via pydantic-ai. The
journalist sees the ledger and its own prior feedback, never another journalist's
draft (that closed loop is what averages voices into mush).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path

from newsroom.contracts.article import PublishArticleInput
from newsroom.contracts.hashing import content_hash, idempotency_key
from newsroom.inference import ChatRequest, chat
from newsroom.inference.provider import ProviderConfig
from newsroom.personas import Persona
from newsroom.pipeline.budget import ArticleBudget
from newsroom.pipeline.context import EditorialContext, ledger_text, persona_block
from newsroom.pipeline.corroboration import corroboration_check
from newsroom.pipeline.evaluate import Evaluation, evaluate_draft
from newsroom.pipeline.factcheck import fact_check
from newsroom.pipeline.finalize import finalize_article
from newsroom.pipeline.widgets import attach_widgets
from newsroom.prompts import load_prompt, render
from newsroom.research.ledger import Ledger
from newsroom.runs import Assignment, RunStore

__all__ = ["ArticleOutcome", "run_article_pipeline"]

# Draft runs warm to fight voice homogenization across journalists; outline and
# the persona-blind passes run cooler (structure and grounding, not voice).
_OUTLINE_TEMP = 0.4
_DRAFT_TEMP = 0.9
_ENRICH_TEMP = 0.3
# Re-spin runs in the author's voice (warmer than the persona-blind enrich) but below
# the first-draft heat: it is revising its own prose, not generating freely.
_RESPIN_TEMP = 0.6


@dataclass
class ArticleOutcome:
    """The result of running the pipeline for one assignment."""

    assignment_id: str
    status: str  # "ready" (finalized, awaiting publish) | "dropped"
    stop_reason: str  # pass | max_sweeps | stalled | budget_exhausted | uncorroborated
    drafts: int
    ledger_digest: str
    article: PublishArticleInput | None = None
    content_hash: str | None = None
    idempotency_key: str | None = None
    image_url: str | None = None  # the hero image's public URL, when the art director ran
    evaluations: list[Evaluation] = field(repr=False, default_factory=list)


def _outline(*, angle: str, section: str, ledger: Ledger, cfg: ProviderConfig,
             prompts_dir, budget: ArticleBudget, overrides: dict[str, str] | None = None) -> str:
    template = load_prompt(prompts_dir, "journalist", "outline.md", overrides=overrides)
    prompt = render(template, angle=angle, section=section, ledger=ledger_text(ledger))
    response = chat(ChatRequest(messages=[{"role": "user", "content": prompt}], temperature=_OUTLINE_TEMP), cfg=cfg)
    budget.debit_response(response)
    return response.content


def _draft(*, persona: Persona, outline: str, angle: str, ledger: Ledger, feedback: str,
           cfg: ProviderConfig, prompts_dir, budget: ArticleBudget,
           house_style: str = "", recent_coverage: str = "",
           overrides: dict[str, str] | None = None) -> str:
    template = load_prompt(prompts_dir, "journalist", "draft.md", overrides=overrides)
    prompt = render(
        template,
        persona=persona_block(persona),  # re-injected fresh every call
        outline=outline,
        angle=angle,
        ledger=ledger_text(ledger),
        feedback=feedback,
        style_guide=house_style,  # the house style; "" renders the token away
        recent_coverage=recent_coverage,  # recent published stories, so the writer does not repeat
    )
    response = chat(ChatRequest(messages=[{"role": "user", "content": prompt}], temperature=_DRAFT_TEMP), cfg=cfg)
    budget.debit_response(response)
    return response.content


def _enrich(*, body: str, ledger: Ledger, cfg: ProviderConfig, prompts_dir,
            budget: ArticleBudget, overrides: dict[str, str] | None = None) -> str:
    # Persona-blind: no persona in the prompt (persona framing degrades accuracy).
    template = load_prompt(prompts_dir, "journalist", "enrich.md", overrides=overrides)
    prompt = render(template, article=body, ledger=ledger_text(ledger))
    response = chat(ChatRequest(messages=[{"role": "user", "content": prompt}], temperature=_ENRICH_TEMP), cfg=cfg)
    budget.debit_response(response)
    return response.content


def _respin(*, persona: Persona, body: str, ledger: Ledger, pass_no: int, cfg: ProviderConfig,
            prompts_dir, budget: ArticleBudget, overrides: dict[str, str] | None = None) -> str:
    # The author re-spins its OWN article (persona re-injected, UNLIKE enrich): a voiced
    # self-revision that interrogates the draft against the staged format and an
    # anti-slop / redundancy rubric, preserving grounded facts and citations (form, not
    # new reporting). Run more than once so each pass cleans what the last left.
    template = load_prompt(prompts_dir, "journalist", "respin.md", overrides=overrides)
    prompt = render(template, persona=persona_block(persona), article=body,
                    ledger=ledger_text(ledger), pass_no=str(pass_no))
    response = chat(ChatRequest(messages=[{"role": "user", "content": prompt}], temperature=_RESPIN_TEMP), cfg=cfg)
    budget.debit_response(response)
    return response.content


def run_article_pipeline(
    *,
    assignment: Assignment,
    persona: Persona,
    ledger: Ledger,
    store: RunStore,
    budget: ArticleBudget,
    drafter_cfg: ProviderConfig,
    evaluator_cfg: ProviderConfig,
    finalize_cfg: ProviderConfig,
    prompts_dir: Path | str,
    overrides: dict[str, str] | None = None,
    max_sweeps: int = 4,
    respin_passes: int = 2,
    lock: threading.Lock | None = None,
    illustrate: Callable[..., object] | None = None,
    editorial: EditorialContext | None = None,
    widget_fetch: Callable[[str], object] | None = None,
) -> ArticleOutcome:
    """Drive one assignment through the pipeline. Returns ``ready`` with a finalized
    ``PublishArticleInput`` (persisted on the assignment, awaiting publish) or
    ``dropped`` (budget exhausted; no article, nothing to publish).

    ``lock`` (when given) serializes the brain's single shared SQLite connection
    across concurrent pipelines and the request loop, exactly like the synthesis
    seam. Only the store WRITES are held under it, never the model calls, so two
    pipelines overlap on inference but never race on the connection."""
    digest = ledger.digest()
    guard = lock if lock is not None else nullcontext()
    ed = editorial or EditorialContext()  # the operator's house style + recent coverage

    with guard:
        store.mark_drafting(assignment.id)

    def drop(reason: str) -> ArticleOutcome:
        with guard:
            store.mark_dropped(assignment.id, reason=reason, ledger_digest=digest)
        return ArticleOutcome(
            assignment_id=assignment.id, status="dropped", stop_reason=reason,
            drafts=drafts, ledger_digest=digest, evaluations=evaluations,
        )

    drafts = 0
    evaluations: list[Evaluation] = []

    if budget.exhausted():
        return drop("budget_exhausted")
    # Cross-source corroboration: a pure-code gate that the ledger rests on enough
    # INDEPENDENT sources (co-owned outlets counting once) BEFORE any draft token is
    # spent. Armed only when the house style configured it; an under-corroborated
    # assignment is DROPPED here, exactly like budget exhaustion, so the same wasted
    # spend the budget drop avoids is also avoided for a story the desk could never have
    # backed. This drops before drafting, so it never touches the content hash.
    if ed.min_independent_sources > 0:
        corr = corroboration_check(ledger, ownership_of=ed.ownership_of, required=ed.min_independent_sources)
        if not corr.ok:
            return drop("uncorroborated")
    outline = _outline(angle=assignment.angle, section=assignment.section, ledger=ledger,
                       cfg=drafter_cfg, prompts_dir=prompts_dir, budget=budget, overrides=overrides)

    body = ""
    feedback = ""
    prev_failing: set[str] | None = None
    stop_reason = "max_sweeps"
    for _sweep in range(max_sweeps):
        if budget.exhausted():
            return drop("budget_exhausted")
        body = _draft(persona=persona, outline=outline, angle=assignment.angle, ledger=ledger,
                     feedback=feedback, cfg=drafter_cfg, prompts_dir=prompts_dir, budget=budget,
                     house_style=ed.house_style_draft, recent_coverage=ed.recent_coverage,
                     overrides=overrides)
        drafts += 1
        # Exhaustion AFTER a draft: the body is complete (never truncated), but the
        # budget is spent, so we DROP rather than continue or publish.
        if budget.exhausted():
            return drop("budget_exhausted")

        evaluation = evaluate_draft(body, outline=outline, ledger=ledger, drafter_cfg=drafter_cfg,
                                    evaluator_cfg=evaluator_cfg, prompts_dir=prompts_dir, budget=budget,
                                    house_style=ed.house_style_eval, lexicon=ed.style_lexicon,
                                    overrides=overrides)
        evaluations.append(evaluation)
        if evaluation.passed:
            stop_reason = "pass"
            break
        failing = set(evaluation.failing_sections)
        # No-progress = the evaluator flags the IDENTICAL, NON-EMPTY set of failing
        # sections two sweeps running (the same unfixed issues). An empty set is
        # deliberately NOT a stall: a REVISE with no named sections (including the
        # unparseable-evaluation fallback) is too ambiguous to early-stop a possibly
        # improving draft on, so we let max_sweeps bound that case instead.
        if prev_failing is not None and failing and failing == prev_failing:
            stop_reason = "stalled"
            break
        prev_failing = failing
        feedback = evaluation.feedback
        if budget.exhausted():
            return drop("budget_exhausted")

    # Enrich (single pass, persona-blind), then deterministic fact-check + one
    # bounded revise. Both debit the same budget.
    if budget.exhausted():
        return drop("budget_exhausted")
    body = _enrich(body=body, ledger=ledger, cfg=drafter_cfg, prompts_dir=prompts_dir, budget=budget,
                   overrides=overrides)

    if budget.exhausted():
        return drop("budget_exhausted")
    body, _verify = fact_check(body, ledger=ledger, entities=assignment.entities, cfg=drafter_cfg,
                               prompts_dir=prompts_dir, budget=budget, overrides=overrides)

    # Self-respin: the author re-spins its own, now-grounded article against the staged
    # format and the anti-slop / redundancy rubric, in its own voice. Runs a fixed number
    # of passes (default two) so each pass cleans what the last left, then finalize lifts
    # the staged parts (title / subtitle / summary) out of the polished body. Each pass is
    # a stage boundary that drops on budget exhaustion, like enrich and fact-check.
    for _pass in range(max(0, respin_passes)):
        if budget.exhausted():
            return drop("budget_exhausted")
        body = _respin(persona=persona, body=body, ledger=ledger, pass_no=_pass + 1, cfg=drafter_cfg,
                       prompts_dir=prompts_dir, budget=budget, overrides=overrides)

    if budget.exhausted():
        return drop("budget_exhausted")
    try:
        article = finalize_article(
            body, section=assignment.section, author=persona.id, cfg=finalize_cfg,
            prompts_dir=prompts_dir, ledger=ledger, run_id=assignment.run_id, sweeps=drafts, budget=budget,
            overrides=overrides,
        )
    except Exception:
        # A model that cannot produce a valid payload even after the one retry, or a
        # transport error at the finalize call, is isolated to THIS article: drop it
        # (the run continues) rather than crashing, mirroring persona synthesis. The
        # body was never published, so there is nothing to undo.
        return drop("finalize_failed")

    # Inline widgets (deterministic, best-effort): connect the finished article to the
    # rich cards the static generator renders. From the grounding ledger we capture any
    # CITED tweet / YouTube video (keyless) and emit a {{tweet}}/{{video}} marker only
    # when a real snapshot was captured, storing it in metadata.tweets / media_checks
    # (the recheck sweep later flips a deleted one). From prior coverage we emit one
    # {{relacionado:slug}} card for the best-matching earlier article. The markers ride in
    # the body, so they ARE part of the article identity, inserted BEFORE the content hash
    # below; snapshots ride in metadata, outside it. Never raises: a widget failure never
    # drops the article (like the art-director step). This runs BEFORE _stamp_author /
    # _art_direct so the body edit lands ahead of the hash, and the metadata merges compose.
    attach_widgets(
        article, ledger=ledger, fetch=widget_fetch,
        related_coverage=ed.related_coverage, entities=assignment.entities,
    )

    # Stamp the author's identity into the open metadata map so the static portal can
    # render bylines, author pages, and an About page from the publish contract alone
    # (the portal's article schema carries only an ``author`` slug). This is
    # UNCONDITIONAL: it does not ride on image generation, so an article published with
    # images disabled still carries its byline. metadata is OUTSIDE the content hash, so
    # this leaves the article identity and idempotency key untouched.
    _stamp_author_identity(article, persona)

    # Art-director image step (best-effort). The article is already finalized, so a
    # failed or skipped image NEVER drops it: we publish without one. The image rides
    # in metadata.image, which is OUTSIDE the content hash, so it does not change the
    # article identity or the idempotency key. The art-director LLM call debits the
    # same budget; the render respects its wall clock via the client timeout.
    image_url = _art_direct_image(
        illustrate, article=article, persona=persona, ledger=ledger, budget=budget
    )

    # Finalize spends tokens too; if that tipped the budget over, we still have a
    # complete article. Persist and hand it to publish (the spend is recorded, the
    # NEXT article's research/draft will see the exhausted budget and not start).
    chash = content_hash(article.title, article.body, article.author, article.section)
    idem = idempotency_key(assignment.id, chash)
    with guard:
        store.finalize_assignment(
            assignment.id, final_body=article.body, content_hash=chash,
            idempotency_key=idem, ledger_digest=digest,
            image_url=image_url,
            image_prompt=(article.metadata or {}).get("newsroom", {}).get("image", {}).get("prompt"),
        )
    return ArticleOutcome(
        assignment_id=assignment.id, status="ready", stop_reason=stop_reason, drafts=drafts,
        ledger_digest=digest, article=article, content_hash=chash, idempotency_key=idem,
        image_url=image_url, evaluations=evaluations,
    )


def _stamp_author_identity(article, persona: Persona) -> None:
    """Stamp the persona's public identity into the article's open ``metadata`` map.

    Sets ``author_name`` (the persona's ``display_name``) and ``author_bio`` (the
    persona's ``about``, the third-person byline produced by synthesis), and
    ``author_avatar`` ONLY when the persona has a non-empty ``avatar_path`` (the key is
    omitted otherwise, so the portal never renders a broken avatar). Unconditional and
    never raises: every finalized article carries its byline. ``metadata`` is not part
    of the content hash, so this leaves the idempotency key untouched."""
    metadata = dict(article.metadata or {})
    metadata["author_name"] = persona.display_name
    metadata["author_bio"] = persona.about
    if persona.avatar_path:
        metadata["author_avatar"] = persona.avatar_path
    article.metadata = metadata


def _art_direct_image(illustrate, *, article, persona, ledger, budget) -> str | None:
    """Run the art-director image step and stamp the result into the article's metadata.

    Returns the public image URL (also stamped into ``metadata.image``) or None when no
    illustrator is configured, the budget is spent, or generation fails. NEVER raises:
    a finalized article is always published, with or without a picture. ``metadata`` is
    not part of the content hash, so this leaves the idempotency key untouched."""
    if illustrate is None or budget.exhausted():
        return None
    try:
        image = illustrate(article=article, persona=persona, ledger=ledger, budget=budget)
    except Exception:
        return None
    if image is None:
        return None
    metadata = dict(article.metadata or {})
    metadata["image"] = image.url
    if getattr(image, "alt", ""):
        metadata["image_alt"] = image.alt
    newsroom_ns = dict(metadata.get("newsroom") or {})
    newsroom_ns["image"] = {
        "prompt": image.prompt,
        "seed": image.seed,
        "workflow": image.workflow,
        "references": list(image.references),
    }
    metadata["newsroom"] = newsroom_ns
    article.metadata = metadata
    return image.url
