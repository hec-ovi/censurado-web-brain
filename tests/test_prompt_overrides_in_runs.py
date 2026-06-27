"""Phase B2: an EDITED prompt version takes effect in a RUN, across the fan-out pool.

Phase B1 shipped the versioned prompt store and ``load_prompt(..., overrides=...)``, but
nothing threaded the active bodies into a run, so runs still read the on-disk ``.md``
files. B2 resolves the active overrides once at run start and threads them through the
manager, dispatch, and the per-article pipeline.

The proof here drives the REAL run path: seed the prompt library, publish an edited
``journalist/draft.md`` carrying a unique marker through the ``PromptStore`` (the console
edit), then run a managed run whose per-article pipeline fans out under a
``ThreadPoolExecutor``. The draft-stage prompt the journalist sent (recorded by the fake)
must carry the marker -- proving the DB body, not the file, reached the journalist running
in a POOL WORKER (a ContextVar set at run start would not have crossed that boundary). A
non-edited stage (outline) must still send its on-disk body, so the B1 file-fallback path
stays intact for every key the operator did not edit.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

from newsroom.config import Settings
from newsroom.db import open_db
from newsroom.editorial import PromptStore
from newsroom.editorial.seeds import seed_prompts
from newsroom.inference.provider import DIALECTS, ProviderConfig
from newsroom.manager.coverage import CoverageStore
from newsroom.manager.preflight import ResolvedRoles
from newsroom.manager.types import Candidate
from newsroom.personas import Persona, PersonaStore
from newsroom.research.ledger import Ledger
from newsroom.runner import RunDeps, run_managed
from newsroom.runs import RunStore

MARKER = "MARKER-XYZZY-EDIT-9f3a1c"


def _cfg(fake, model: str) -> ProviderConfig:
    return ProviderConfig(
        role="x", provider="local", base_url=f"{fake.base_url}/v1", model=model, **DIALECTS["local"]
    )


def _roles(fake) -> ResolvedRoles:
    # drafter == evaluator endpoint -> the rules-grounded evaluator degrade (no eval model
    # call), so the scripted chat queue stays short and the draft prompt is unambiguous.
    drafter = _cfg(fake, "drafter-model")
    return ResolvedRoles(
        drafter=drafter, evaluator=drafter, finalize=_cfg(fake, "finalize-model"),
        manager=drafter, evaluator_distinct=False,
    )


def _ready_ledger(_assignment, _spec, _persona, _budget) -> Ledger:
    """A make_ledger double: a non-empty grounded ledger (so the rules evaluator PASSes)
    WITHOUT running the research loop, so the only model calls are the pipeline stages."""
    led = Ledger(clock=lambda: datetime(2026, 6, 23, tzinfo=timezone.utc))
    led.add(claim="grounding", url="https://src.test/a", snippet="a source")
    return led


def _no_search(_query: str) -> list[Candidate]:
    return []


def _ada() -> Persona:
    return Persona(id="ada", display_name="Ada", beat="tech", who_i_am="I cover chips.", style="dry")


def _assign(persona_id: str, headline: str = "Chips ship early") -> str:
    return json.dumps(
        {"action": "assign", "assignments": [
            {"persona_id": persona_id, "headline": headline, "angle": "Cover it", "triage": "new"}
        ]}
    )


def _finalize_ok(title: str = "Chips Ship Early", body: str = "The chips shipped.") -> str:
    return json.dumps({"title": title, "body": body, "topics": ["chips"]})


def _deps_with_prompts(fake, tmp_path) -> tuple[RunDeps, PromptStore]:
    """A real RunDeps over an in-memory connection with the prompt library seeded and a
    live ``PromptStore`` wired in, so a published edit is what the run resolves."""
    conn = open_db(":memory:", check_same_thread=False)
    settings = Settings(
        persona_db_path=tmp_path / "brain.db",
        inference_base_url=f"{fake.base_url}/v1",
        publish_base_url=fake.base_url,
        operator_token="op-token",
    )
    seed_prompts(conn, settings.prompts_dir)  # lift the .md files into the store as v1
    prompt_store = PromptStore(conn)
    persona_store = PersonaStore(conn)
    persona_store.create(_ada())
    deps = RunDeps(
        store=RunStore(conn),
        persona_store=persona_store,
        coverage_store=CoverageStore(conn),
        roles=_roles(fake),
        search_news=_no_search,
        make_ledger=_ready_ledger,
        publish_base_url=fake.base_url,
        operator_token="op-token",
        prompts_dir=settings.prompts_dir,
        settings=settings,
        lock=threading.Lock(),
        prompt_store=prompt_store,
    )
    return deps, prompt_store


def _prompt_text(record: dict) -> str:
    """The full user/system text of one recorded chat request (pydantic-ai stages may
    send more than one message, so join them)."""
    messages = record["body"].get("messages", [])
    return "\n".join(str(m.get("content", "")) for m in messages)


def test_edited_draft_prompt_reaches_the_journalist_in_a_pool_worker(fake, tmp_path):
    deps, prompt_store = _deps_with_prompts(fake, tmp_path)

    # The operator's console edit: a NEW active version of draft.md carrying the marker,
    # prepended to the seeded file body so the rendered tokens still resolve normally.
    file_body = (Path(deps.settings.prompts_dir) / "journalist" / "draft.md").read_text(encoding="utf-8")
    prompt_store.add_version("journalist/draft.md", f"{MARKER}\n\n{file_body}", created_by="op", activate=True)

    # A full managed run: assign -> outline -> draft -> enrich -> finalize (rules-degraded
    # eval and a URL-free draft add no further calls).
    fake.state.script_chat(_assign("ada"))
    for body in ("an outline", "a clean draft", "an enriched body", "a respin 1", "a respin 2"):
        fake.state.script_chat(body)
    fake.state.script_chat(_finalize_ok())

    report = run_managed(deps=deps)

    assert report.status == "done"
    assert [o.status for o in report.outcomes] == ["ready"]

    prompts = [_prompt_text(r) for r in fake.state.chat_requests]
    # The marker rode into exactly one stage's prompt: the draft. Its arrival proves the
    # edited DB body crossed the ThreadPoolExecutor boundary into the worker thread.
    carrying_marker = [p for p in prompts if MARKER in p]
    assert len(carrying_marker) == 1
    draft_prompt = carrying_marker[0]
    # And it really is the draft stage: it re-injects the persona and follows the outline.
    assert "I cover chips." in draft_prompt  # the persona block
    assert "an outline" in draft_prompt      # the outline rendered into {{OUTLINE}}

    # The fallback: a stage the operator did NOT edit (outline) still sends its on-disk
    # body -- the file heading is present and the marker never is.
    outline_prompts = [p for p in prompts if "Outline the article" in p]
    assert len(outline_prompts) == 1
    assert MARKER not in outline_prompts[0]


def test_unedited_run_uses_only_file_bodies(fake, tmp_path):
    # Control: with the library seeded but NOTHING edited, the active overrides equal the
    # files, so no run prompt carries the marker and the run still completes normally.
    deps, _prompt_store = _deps_with_prompts(fake, tmp_path)
    fake.state.script_chat(_assign("ada"))
    for body in ("an outline", "a clean draft", "an enriched body", "a respin 1", "a respin 2"):
        fake.state.script_chat(body)
    fake.state.script_chat(_finalize_ok())

    report = run_managed(deps=deps)

    assert report.status == "done"
    assert [o.status for o in report.outcomes] == ["ready"]
    assert all(MARKER not in _prompt_text(r) for r in fake.state.chat_requests)
