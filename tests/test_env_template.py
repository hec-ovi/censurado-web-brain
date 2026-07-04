"""Drift guard: .env.example must stay the clean, current template.

The live .env accumulated dead sections over the project's phases (the old admin console and
in-brain inference) that the current compose never reads. They were removed in the env
normalization; this pins .env.example so they cannot creep back and reintroduce the "I edited a
var and nothing happened" confusion. The admin console was replaced by the panel, and the brain
is no longer a service (it is a host-run CLI), so its config-plane port is gone too.

The local-model / crush agentic layer (the combined `make up-all` stack, the sibling
llama-vulkan-strix `llm` service, and the paid X API MCP) was removed from the newsroom, so the
model vars (LLM_MODEL, ...) and X_BEARER_TOKEN are now dead too and must never reappear.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = (ROOT / ".env.example").read_text()

# Vars the current newsroom docker-compose.yml actually interpolates.
LIVE_VARS = [
    "RENDER_GID", "VIDEO_GID", "COMFYUI_MODELS_PATH", "CENSURADO_BASE_URL",
    "SITE_PORT", "PUBLISH_PORT", "COMFYUI_PORT",
    "NEWSROOM_OPERATOR_TOKEN", "PANEL_LOGIN_TOKEN", "PANEL_LOGIN_TOKEN_HASH",
    "PANEL_SESSION_KEY", "PANEL_SECURE_COOKIES",
]

# Dead sections that must never reappear in the template.
DEAD_VARS = [
    # the brain is a host-run CLI now, not a service, so its config-plane port is gone
    "BRAIN_PORT",
    # the legacy admin console was replaced by the panel
    "ADMIN_PORT", "CONSOLE_PORT",
    "CENSURADO_ADMIN_PUBLISH_TOKEN", "CENSURADO_ADMIN_TOKEN", "CENSURADO_ADMIN_TOKEN_HASH",
    "CENSURADO_ADMIN_SESSION_KEY", "CENSURADO_ADMIN_SECURE_COOKIES",
    # the brain runs no model; agents author
    "NEWSROOM_INFERENCE_PROVIDER", "NEWSROOM_INFERENCE_BASE_URL", "NEWSROOM_INFERENCE_MODEL",
    "NEWSROOM_INFERENCE_API_KEY", "NEWSROOM_INFERENCE_COOLDOWN",
    # the local-model / crush agentic layer was removed
    "MODELS_DIR", "LLM_MODEL", "LLM_ALIAS", "LLM_PORT", "LLM_NGL", "LLM_CTX", "LLM_PARALLEL",
    # the paid X API MCP was removed
    "X_BEARER_TOKEN",
]


def test_env_example_declares_every_compose_var():
    for v in LIVE_VARS:
        assert f"{v}=" in EXAMPLE, f".env.example is missing the compose var {v}"


def test_env_example_has_no_dead_vars():
    for v in DEAD_VARS:
        assert v not in EXAMPLE, (
            f".env.example reintroduced the dead var {v}: the admin console is gone and the "
            "brain runs no model"
        )
