"""Parsing a model reply as JSON, tolerating a local model's sloppiness.

Local models routinely wrap JSON in a ```` ```json ```` fence, and just as often
bracket it with prose ("Sure, here is the result:" before, "Hope that helps!"
after) even when asked for raw JSON. This one helper is shared by every place that
reads structured output from the in-house ``chat`` adapter (research planning,
persona synthesis, the evaluator), so that brittleness is fixed once.

Strategy, in order: strip a code fence, try a direct parse, and if that fails fall
back to extracting the first balanced JSON value (``{...}`` or ``[...]``) embedded
in surrounding prose. A genuinely JSON-free reply still raises
``json.JSONDecodeError`` (a ``ValueError`` subclass), which callers map to their
own error contract. The finalize seam does NOT use this: it goes through
pydantic-ai's typed validate-and-retry instead (B.3).
"""

from __future__ import annotations

import json

__all__ = ["extract_json"]


def _strip_fence(text: str) -> str:
    if text.startswith("```"):
        # Drop the opening fence (``` or ```json) and the closing fence.
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[: -len("```")]
    return text.strip()


def _first_json_span(text: str) -> str | None:
    """The first balanced ``{...}`` or ``[...]`` substring, or None.

    Scans from the first opening bracket to its matching close, skipping brackets
    that appear inside JSON strings (so a ``}`` in a value does not end it early).
    A heuristic for prose-wrapped JSON; it does not defend against a stray bracket
    that appears in the PREAMBLE before the real JSON (rare in model output)."""
    opener = next((i for i, ch in enumerate(text) if ch in "{["), None)
    if opener is None:
        return None
    open_ch = text[opener]
    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    in_string = False
    escaped = False
    for i in range(opener, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[opener : i + 1]
    return None


def extract_json(content: str):
    """Parse ``content`` as JSON. Strips a code fence, then tries a direct parse,
    then falls back to the first balanced JSON value embedded in prose. Returns
    whatever JSON value was parsed (object, array, ...); raises
    ``json.JSONDecodeError`` when no JSON can be found."""
    text = _strip_fence(content.strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        span = _first_json_span(text)
        if span is None:
            raise  # genuinely no JSON: preserve the original decode error
        return json.loads(span)
