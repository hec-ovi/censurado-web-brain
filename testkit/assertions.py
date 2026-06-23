"""The global no-output-cap guard, shared by the fake and every test.

The harness must NEVER cap the length of a single model generation. This module
centralizes the forbidden length-cap keys and the assertion. The fake chat server
(``testkit/fake_server.py``) rejects any request carrying one of these keys with
HTTP 422, so a cap fails loudly at the wire even if a test forgets to assert; the
helpers here are the belt-and-suspenders a fixture runs over every recorded
request.

Matching is normalized (case-insensitive, ignoring ``_`` and ``-``) so the snake,
camel, and kebab spellings of each cap collapse to one (``max_tokens``,
``maxTokens``, ``max-tokens`` are all caught), and the scan walks nested objects,
not just the top level, so a cap buried in a sub-object cannot slip through.
"""

from __future__ import annotations

from typing import Any, Mapping

__all__ = ["LENGTH_CAP_KEYS", "length_cap_keys_in", "assert_no_length_cap"]

# Every output-length cap we refuse to ever send, across providers and dialects.
# Kept in canonical spelling for messages; matching is normalized (see _normalize).
LENGTH_CAP_KEYS: frozenset[str] = frozenset(
    {
        "max_tokens",
        "max_completion_tokens",
        "max_output_tokens",
        "max_new_tokens",
        "n_predict",
        "num_predict",
        "max_length",
        "max_words",
    }
)


def _normalize(key: object) -> str:
    return str(key).replace("_", "").replace("-", "").lower()


_NORMALIZED_CAPS: frozenset[str] = frozenset(_normalize(k) for k in LENGTH_CAP_KEYS)


def length_cap_keys_in(payload: Mapping[str, Any]) -> list[str]:
    """Return every forbidden length-cap key found ANYWHERE in ``payload`` (nested)."""
    found: list[str] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, Mapping):
            for key, value in obj.items():
                if _normalize(key) in _NORMALIZED_CAPS:
                    found.append(key)
                walk(value)
        elif isinstance(obj, (list, tuple)):
            for item in obj:
                walk(item)

    walk(payload)
    return found


def assert_no_length_cap(payload: Mapping[str, Any]) -> None:
    """Raise AssertionError if ``payload`` carries any output-length cap key."""
    offenders = length_cap_keys_in(payload)
    assert not offenders, (
        f"request carries a forbidden output-length cap: {offenders}. "
        "The harness must never cap a single generation's length."
    )
