"""R1: the manager triage prompt describes each journalist's voice and language, so the
manager assigns a story by fit (beat plus voice), not by beat alone. The section a
persona writes is still authoritative from its beat; this only enriches the choice."""

from __future__ import annotations

import json

from newsroom.config import load_settings
from newsroom.inference.provider import DIALECTS, ProviderConfig
from newsroom.manager import run_manager
from newsroom.personas import Persona


def _cfg(fake) -> ProviderConfig:
    return ProviderConfig(
        role="manager", provider="local", base_url=f"{fake.base_url}/v1",
        model="manager-model", **DIALECTS["local"],
    )


def test_triage_prompt_describes_voice_and_language(fake):
    fake.state.script_chat(json.dumps({"action": "assign", "assignments": [
        {"persona_id": "ada", "headline": "Chips", "angle": "cover it", "triage": "new"}
    ]}))
    personas = [Persona(id="ada", display_name="Ada", beat="tech",
                        who_i_am="I cover chips.", style="dry and precise", language="neutral-es")]

    run_manager(personas=personas, coverage=[], search_news=lambda q: [],
                cfg=_cfg(fake), prompts_dir=load_settings().prompts_dir, n_max=2, max_steps=2)

    prompt = fake.state.chat_requests[0]["body"]["messages"][0]["content"]
    assert "writes in neutral-es" in prompt
    assert "Voice: dry and precise" in prompt
