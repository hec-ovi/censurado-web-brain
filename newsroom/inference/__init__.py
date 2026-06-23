"""The inference layer: an OpenAI-dialect completion adapter with per-backend shims.

Public surface:
  * ``chat(ChatRequest, role=...) -> ChatResponse`` - the one completion call.
  * ``resolve(role) -> ProviderConfig`` and ``endpoints_distinct(a, b)`` - role-based
    backend resolution and the distinctness check the evaluator relies on.

The adapter never caps output length.
"""

from __future__ import annotations

from .adapter import ChatRequest, ChatResponse, ToolCall, chat
from .provider import ProviderConfig, endpoints_distinct, resolve

__all__ = [
    "chat",
    "ChatRequest",
    "ChatResponse",
    "ToolCall",
    "resolve",
    "endpoints_distinct",
    "ProviderConfig",
]
