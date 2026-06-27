"""Persona synthesis: a seed brief becomes a full persona via one model call.

The model is asked to BE the persona (direct role-play, the framing local
Gemma-class models need to hold a voice) and to return a JSON object with the
persona's identity, style, contrastive positive/negative few-shots, and sources.
This module parses and validates that object and persists it through the Step 2
store. It is deliberately synchronous: the HTTP layer runs it OFF the request, so
the route can answer 202 without waiting for the model (see ``brain.app``).

The single generation is uncapped, as everywhere in the harness: ``chat`` sends no
length field and the prompt states there is no length limit.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path

from newsroom.inference import ChatRequest, chat
from newsroom.inference.provider import ProviderConfig
from newsroom.jsonio import extract_json
from newsroom.personas import Persona, PersonaStore
from newsroom.prompts import load_prompt, render

__all__ = ["PersonaSeed", "SynthesisError", "synthesize_persona"]

# Keys the model is asked to return; the seed supplies display_name and beat.
_REQUIRED_JSON_KEYS = ("who_i_am", "style")


class SynthesisError(Exception):
    """Synthesis could not produce a valid persona (bad model output, etc.)."""


@dataclass
class PersonaSeed:
    """The brief a caller posts to grow into a persona. ``beat`` is validated by
    the HTTP layer against the harness section enum before synthesis starts."""

    display_name: str
    beat: str
    seed: str
    sources: list[str] | None = None


def _extract_json(content: str) -> dict:
    """Parse the model's reply as a JSON object, tolerating a Markdown code fence."""
    try:
        parsed = extract_json(content)
    except json.JSONDecodeError as exc:
        raise SynthesisError(f"model did not return valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SynthesisError("model returned JSON that is not an object")
    return parsed


def _as_list(value: object) -> list:
    return value if isinstance(value, list) else []


def synthesize_persona(
    seed: PersonaSeed,
    *,
    cfg: ProviderConfig,
    store: PersonaStore,
    prompts_dir: Path | str,
    lock: threading.Lock | None = None,
    overrides: dict[str, str] | None = None,
) -> str:
    """Run one synthesis: prompt the model, parse it, persist the persona, return
    the new persona id. Raises ``SynthesisError`` on bad output or a store
    conflict. ``lock`` (when given) serializes the single shared DB connection.
    ``overrides`` is the active-prompt map so an edited ``persona/synthesize.md`` is what
    synthesis uses (resolved by the caller under the lock from the prompt store)."""
    template = load_prompt(prompts_dir, "persona", "synthesize.md", overrides=overrides)
    prompt = render(template, display_name=seed.display_name, beat=seed.beat, seed=seed.seed)
    response = chat(ChatRequest(messages=[{"role": "user", "content": prompt}]), cfg=cfg)

    data = _extract_json(response.content)
    missing = [k for k in _REQUIRED_JSON_KEYS if not str(data.get(k, "")).strip()]
    if missing:
        raise SynthesisError(f"synthesis omitted required field(s): {missing}")

    persona = Persona(
        display_name=seed.display_name,
        beat=seed.beat,
        who_i_am=str(data["who_i_am"]),
        style=str(data["style"]),
        about=str(data.get("about", "")),
        few_shots_pos=_as_list(data.get("few_shots_pos")),
        few_shots_neg=_as_list(data.get("few_shots_neg")),
        sources=_as_list(data.get("sources")) or list(seed.sources or []),
    )
    try:
        if lock is not None:
            with lock:
                created = store.create(persona)
        else:
            created = store.create(persona)
    except ValueError as exc:
        # A duplicate id or invalid field from the store is a synthesis failure,
        # not a crash; surface it through the job's error contract.
        raise SynthesisError(str(exc)) from exc
    return created.id
