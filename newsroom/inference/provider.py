"""Per-role inference provider config, resolved AT CALL TIME (env -> default).

The harness speaks ONE wire dialect, OpenAI Chat-Completions, against per-role
backends. A "role" (drafter, evaluator, manager, ...) is the unit of resolution:
each role's provider, base URL, model, key, and capability flags come from env,
falling back to the shared default inference backend (the local llama.cpp/Vulkan
Gemma). This is what lets the evaluator resolve to a DIFFERENT endpoint than the
drafter (a model cannot reliably grade its own work); see ``endpoints_distinct``.

Resolution order per setting: ``NEWSROOM_ROLE_<ROLE>_<KEY>`` -> shared
``NEWSROOM_INFERENCE_<KEY>`` -> dialect default. Adapted from the proven gamentic
provider layer, trimmed to the one text modality the brain needs and with every
output-length knob removed (the harness never caps a generation).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

__all__ = ["ProviderConfig", "resolve", "endpoints_distinct", "DIALECTS"]

DEFAULT_PROVIDER = "local"
DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"
DEFAULT_MODEL = "local-gemma"

# The recognized OpenAI-dialect backends and their capability defaults. "local"
# is llama.cpp (a generous stop budget, hybrid thinking, GBNF grammar). "openai"
# is any cloud OpenAI-dialect endpoint (stop capped at 4, no thinking kwarg, no
# server-side grammar). "hermes" is a CLI agent reached through a
# /v1/chat/completions shim; conservative, like the cloud dialect.
DIALECTS: dict[str, dict] = {
    "local": {"supports_thinking": True, "supports_grammar": True, "max_stops": 8},
    "openai": {"supports_thinking": False, "supports_grammar": False, "max_stops": 4},
    "hermes": {"supports_thinking": False, "supports_grammar": False, "max_stops": 4},
}


@dataclass
class ProviderConfig:
    role: str
    provider: str
    base_url: str
    model: str
    api_key: str = ""
    supports_thinking: bool = False
    supports_grammar: bool = False
    max_stops: int = 4

    @property
    def endpoint_id(self) -> str:
        """Identity of the backend this role talks to, for distinctness checks."""
        return f"{self.base_url}|{self.model}"


def _env(*names: str, default: str = "") -> str:
    """First NON-EMPTY env var among names, else default."""
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return default


def _as_bool(value: str, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() == "true"


def _as_int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def resolve(role: str = "default") -> ProviderConfig:
    """The active provider config for a role: env -> shared default -> dialect default."""
    key = role.upper().replace("-", "_")
    provider = _env(f"NEWSROOM_ROLE_{key}_PROVIDER", "NEWSROOM_INFERENCE_PROVIDER", default=DEFAULT_PROVIDER)
    base_url = _env(
        f"NEWSROOM_ROLE_{key}_BASE_URL", "NEWSROOM_INFERENCE_BASE_URL", default=DEFAULT_BASE_URL
    ).rstrip("/")
    model = _env(f"NEWSROOM_ROLE_{key}_MODEL", "NEWSROOM_INFERENCE_MODEL", default=DEFAULT_MODEL)
    api_key = _env(f"NEWSROOM_ROLE_{key}_API_KEY", "NEWSROOM_INFERENCE_API_KEY")

    caps = DIALECTS.get(provider, DIALECTS["openai"])
    return ProviderConfig(
        role=role,
        provider=provider,
        base_url=base_url,
        model=model,
        api_key=api_key,
        supports_thinking=_as_bool(
            _env(f"NEWSROOM_ROLE_{key}_SUPPORTS_THINKING", "NEWSROOM_INFERENCE_SUPPORTS_THINKING"),
            caps["supports_thinking"],
        ),
        supports_grammar=_as_bool(
            _env(f"NEWSROOM_ROLE_{key}_SUPPORTS_GRAMMAR", "NEWSROOM_INFERENCE_SUPPORTS_GRAMMAR"),
            caps["supports_grammar"],
        ),
        max_stops=_as_int(
            _env(f"NEWSROOM_ROLE_{key}_MAX_STOPS", "NEWSROOM_INFERENCE_MAX_STOPS"), caps["max_stops"]
        ),
    )


def endpoints_distinct(role_a: str, role_b: str) -> bool:
    """True if two roles resolve to different backends.

    The pipeline uses this at startup: the evaluator must not share the drafter's
    endpoint (correlated blind spot). When they DO match (only the local Gemma is
    configured), the evaluator degrades to a rules-grounded check instead.
    """
    return resolve(role_a).endpoint_id != resolve(role_b).endpoint_id
