"""Bias-on-demand: the newsroom writes from a declared viewpoint when asked, and the
pipeline never sanitizes that stance into forced neutrality.

Two layers are pinned:

  * a PROMPT regression lock on ``journalist/draft.md``: the drafter is told to write
    from a point of view (stance lives in framing, never in the facts), and is NEVER
    told to be neutral / objective / impartial / unbiased. A future prompt edit that
    quietly re-introduces forced balance would break the product's whole premise (the
    authors are deliberately slanted), so this guard fails loudly if it creeps back in;
  * the FLOW: an operator's slanted direct brief reaches the journalist verbatim at the
    draft stage (rendered into the angle the writer covers), with corroboration off, so
    a one-off biased piece is a first-class request, not something neutralized en route.

The persona voice that carries each author's standing bias is exercised elsewhere
(synthesis + the voiced respin); here the focus is the on-demand operator slant and the
prompt's anti-neutralization contract.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from newsroom.config import Settings
from newsroom.db import open_db
from newsroom.inference.provider import DIALECTS, ProviderConfig
from newsroom.manager.coverage import CoverageStore
from newsroom.manager.preflight import ResolvedRoles
from newsroom.personas import Persona, PersonaStore
from newsroom.research.ledger import Ledger
from newsroom.runner import RunDeps, run_direct
from newsroom.runs import RunStore


# ----- (a) the prompt regression lock -----


def _norm(text: str) -> str:
    """Collapse the prompt's hard line wraps so a phrase split across a newline (the .md
    is wrapped at ~80 cols, and ``render`` keeps those breaks) still matches as a string."""
    return " ".join(text.split())


def _draft_prompt_text() -> str:
    return (Path("prompts") / "journalist" / "draft.md").read_text(encoding="utf-8")


def test_draft_prompt_tells_the_writer_to_take_a_stance():
    text = _norm(_draft_prompt_text().lower())
    # The drafter is told to write from a viewpoint and argue it from the evidence.
    assert "point of view" in text
    assert "stance" in text
    assert "declared viewpoint is the job" in text
    # And the bright line: the slant is in framing, never in the facts.
    assert "never in the facts" in text


def test_draft_prompt_never_forces_neutrality():
    # The authors are deliberately slanted (left oposicion, conspiracy, anti-imperial),
    # so the drafter must never be told to neutralize. These are the imperatives that
    # would sanitize a viewpoint into forced balance; none may appear. (The prompt may
    # still REJECT false balance, e.g. "both-sides debate the facts do not support" --
    # that is the opposite instruction and is allowed.)
    text = _norm(_draft_prompt_text().lower())
    forbidden = [
        "be neutral", "remain neutral", "stay neutral", "neutral tone",
        "be objective", "remain objective", "stay objective",
        "be impartial", "remain impartial", "impartial tone",
        "unbiased", "without bias", "free of bias",
        "present both sides", "give both sides", "tell both sides",
        "avoid taking a side", "do not take a side", "take no side",
        "balanced perspective", "balanced view",
    ]
    leaked = [phrase for phrase in forbidden if phrase in text]
    assert leaked == [], f"draft prompt forces neutrality: {leaked}"


# ----- (b) the flow: a slanted brief reaches the writer -----


def _cfg(fake, model: str) -> ProviderConfig:
    return ProviderConfig(
        role="x", provider="local", base_url=f"{fake.base_url}/v1", model=model, **DIALECTS["local"]
    )


def _roles(fake) -> ResolvedRoles:
    drafter = _cfg(fake, "drafter-model")
    return ResolvedRoles(
        drafter=drafter, evaluator=drafter, finalize=_cfg(fake, "finalize-model"),
        manager=drafter, evaluator_distinct=False,
    )


def _ready_ledger(_assignment, _spec, _persona, _budget) -> Ledger:
    led = Ledger(clock=lambda: datetime(2026, 6, 23, tzinfo=timezone.utc))
    led.add(claim="grounding", url="https://src.test/a", snippet="a source")
    return led


def _settings(fake, tmp_path, **over) -> Settings:
    base = dict(
        persona_db_path=tmp_path / "brain.db",
        inference_base_url=f"{fake.base_url}/v1",
        publish_base_url=fake.base_url,
        operator_token="op-token",
    )
    base.update(over)
    return Settings(**base)


def _lara() -> Persona:
    # A deliberately slanted desk (the real newsroom's left/oposicion author).
    return Persona(id="lara", display_name="Lara", beat="politics",
                   who_i_am="Cubro la politica argentina con una mirada opositora.", style="acida")


def _deps(fake, settings, *, persona) -> RunDeps:
    conn = open_db(":memory:", check_same_thread=False)
    persona_store = PersonaStore(conn)
    persona_store.create(persona)
    return RunDeps(
        store=RunStore(conn),
        persona_store=persona_store,
        coverage_store=CoverageStore(conn),
        roles=_roles(fake),
        search_news=lambda _q: [],
        make_ledger=_ready_ledger,
        publish_base_url=fake.base_url,
        operator_token="op-token",
        prompts_dir=settings.prompts_dir,
        settings=settings,
        lock=threading.Lock(),
        fetch=lambda _u: "fuente citada",
    )


def _finalize_ok(title="Titulo", body="Cuerpo final.") -> str:
    return json.dumps({"title": title, "body": body, "topics": ["politica"]})


def _prompt_text(record) -> str:
    return "\n".join(str(m.get("content", "")) for m in record["body"].get("messages", []))


def test_a_slanted_direct_brief_reaches_the_journalist_at_the_draft_stage(fake, tmp_path):
    settings = _settings(fake, tmp_path)
    deps = _deps(fake, settings, persona=_lara())

    slant = "Cubri esto como una catastrofe del gobierno: deja clara la responsabilidad oficial."
    focus = "el impacto en los jubilados"

    # The direct pipeline: outline -> draft -> enrich -> respin x2 -> finalize (rules-
    # degraded eval, a clean URL-free draft add no further calls).
    fake.state.script_chat("un esquema")
    for body in ("un borrador citando https://src.test/a", "un cuerpo enriquecido",
                 "respin uno", "respin dos"):
        fake.state.script_chat(body)
    fake.state.script_chat(_finalize_ok())

    report = run_direct(deps=deps, links=["https://src.test/a"], persona_id="lara",
                        brief=slant, focus=focus)

    assert report.mode == "direct"

    prompts = [_prompt_text(r) for r in fake.state.chat_requests]
    # The draft stage re-injects the persona and follows the outline; find it.
    draft_prompts = [p for p in prompts if "un esquema" in p and "Cubro la politica" in p]
    assert len(draft_prompts) == 1
    draft_prompt = draft_prompts[0]
    # The operator's slant AND focus rode into the angle the writer covers, verbatim.
    assert slant in draft_prompt
    assert focus in draft_prompt
    # And the writer was told to honor it, not neutralize it.
    assert "declared viewpoint is the job" in _norm(draft_prompt)
