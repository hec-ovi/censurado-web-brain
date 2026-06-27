"""The collapsed evaluator: review and completion-judge in one call.

Returns ``{verdict: PASS | REVISE, feedback, failing_sections}``. PASS means done;
REVISE steers the next draft. Collapsing review and judge removes a redundant call
(pragmatism fix #5), and requiring the evaluator to resolve to a DIFFERENT endpoint
than the drafter removes the correlated-blind-spot hazard a shared-weights judge
would carry (loops fix #5).

When only one backend is configured (the evaluator endpoint equals the drafter's),
the model-driven evaluator would just be the drafter grading itself, so it DEGRADES
to a rules-grounded check: the body must ground in the ledger (CitationVerify
passes) and the ledger must be non-empty (an ungrounded article cannot PASS). In
that mode ``max_sweeps`` is the real terminator (A.4). The degrade is decided by
comparing the two resolved endpoints, never silently.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from newsroom.editorial.lexicon import banned_terms_found
from newsroom.inference import ChatRequest, chat
from newsroom.inference.provider import ProviderConfig
from newsroom.jsonio import extract_json
from newsroom.pipeline.context import ledger_text
from newsroom.pipeline.factcheck import citation_verify
from newsroom.prompts import load_prompt, render
from newsroom.research.ledger import Ledger

__all__ = ["Evaluation", "evaluate_draft"]


@dataclass
class Evaluation:
    verdict: str  # "PASS" | "REVISE"
    feedback: str = ""
    failing_sections: list[str] = field(default_factory=list)
    mode: str = "model"  # "model" (distinct endpoint) | "rules" (degraded)

    @property
    def passed(self) -> bool:
        return self.verdict == "PASS"


def _normalize(data: object) -> Evaluation:
    """Coerce a parsed model reply into an Evaluation (defensive about shape)."""
    if not isinstance(data, dict):
        return Evaluation(verdict="REVISE", feedback="unparseable evaluation", failing_sections=[])
    verdict = "PASS" if str(data.get("verdict", "")).strip().upper() == "PASS" else "REVISE"
    raw_sections = data.get("failing_sections") or []
    sections = (
        [str(s).strip() for s in raw_sections if s is not None and str(s).strip()]
        if isinstance(raw_sections, list)
        else []
    )  # a JSON null in the list is dropped, not coerced to the string "None"
    return Evaluation(verdict=verdict, feedback=str(data.get("feedback", "")), failing_sections=sections)


def _rules_evaluate(body: str, ledger: Ledger) -> Evaluation:
    """The degraded check when the evaluator shares the drafter's endpoint."""
    failing: list[str] = []
    if len(ledger) == 0:
        failing.append("grounding")  # nothing to ground against
    cv = citation_verify(body, ledger)
    if cv.unsupported_urls:
        failing.append("citations")
    if cv.todo_markers:
        failing.append("unresolved-markers")
    if failing:
        return Evaluation(
            verdict="REVISE",
            feedback="rules check: " + ", ".join(failing),
            failing_sections=failing,
            mode="rules",
        )
    return Evaluation(verdict="PASS", feedback="rules check passed", failing_sections=[], mode="rules")


def _apply_lexicon_gate(ev: Evaluation, draft: str, lexicon: dict | None) -> Evaluation:
    """A banned term in the draft forces a REVISE regardless of the model's verdict: the
    lexicon is enforced in code, not left to the model to honor (negation is unreliable
    on a local model). Adds a ``lexicon`` failing section and names the offenders."""
    hits = banned_terms_found(draft, lexicon)
    if not hits:
        return ev
    sections = list(ev.failing_sections)
    if "lexicon" not in sections:
        sections.append("lexicon")
    note = "Banned terms present: " + ", ".join(hits) + "."
    feedback = f"{ev.feedback} {note}".strip() if ev.feedback else note
    return Evaluation(verdict="REVISE", feedback=feedback, failing_sections=sections, mode=ev.mode)


def evaluate_draft(
    draft: str,
    *,
    outline: str,
    ledger: Ledger,
    drafter_cfg: ProviderConfig,
    evaluator_cfg: ProviderConfig,
    prompts_dir,
    budget=None,
    house_style: str = "",
    lexicon: dict | None = None,
    overrides: dict[str, str] | None = None,
) -> Evaluation:
    """Evaluate a draft. Model-driven on a distinct endpoint, else rules-grounded. The
    house-style rules ride into the model prompt; the banned lexicon is a deterministic
    gate applied to either path."""
    if evaluator_cfg.endpoint_id == drafter_cfg.endpoint_id:
        return _apply_lexicon_gate(_rules_evaluate(draft, ledger), draft, lexicon)

    template = load_prompt(prompts_dir, "journalist", "evaluate.md", overrides=overrides)
    prompt = render(
        template, draft=draft, outline=outline, ledger=ledger_text(ledger), style_guide=house_style
    )
    # A low temperature for grading: we want a stable verdict, not a creative one.
    response = chat(ChatRequest(messages=[{"role": "user", "content": prompt}], temperature=0.2),
                    cfg=evaluator_cfg)
    if budget is not None:
        budget.debit_response(response)
    try:
        ev = _normalize(extract_json(response.content))
    except (ValueError, KeyError):
        # An unparseable evaluation is treated as REVISE (fail safe, never a crash),
        # which the max_sweeps cap and no-progress detector still bound.
        ev = Evaluation(verdict="REVISE", feedback="unparseable evaluation", failing_sections=[])
    return _apply_lexicon_gate(ev, draft, lexicon)
