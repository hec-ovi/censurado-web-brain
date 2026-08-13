"""Prompt rendering and model-output parsing, shared by the workflow and the batch."""
import json
import re

from .errors import AdapterError

_PLACEHOLDER = re.compile(r"\{([a-z0-9_-]+)\}")


def render(template: str, context: dict) -> str:
    return _PLACEHOLDER.sub(
        lambda m: str(context[m.group(1)]) if m.group(1) in context else m.group(0), template)


def parse_json_output(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    a, b = text.find("{"), text.rfind("}")
    if a < 0 or b <= a:
        raise AdapterError("no JSON object in node output")
    try:
        return json.loads(text[a:b + 1])
    except json.JSONDecodeError as e:
        raise AdapterError(f"node output is not valid JSON: {e}") from e
