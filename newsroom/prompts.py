"""Versioned prompt loading.

Prompts are plain ``.md`` files under ``prompts/`` (architecture doc B.6). They
carry no output-length cap and no "in N words" phrasing; a generation finishes on
its own. This loader reads a prompt by path parts and substitutes ``{{TOKEN}}``
placeholders. Tokens use double braces (not ``str.format``) so a prompt can show
literal JSON braces in its instructions without escaping.
"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = ["load_prompt", "render"]

_TOKEN = re.compile(r"\{\{(\w+)\}\}")


def load_prompt(
    prompts_dir: Path | str, *parts: str, overrides: dict[str, str] | None = None
) -> str:
    """Read a prompt's raw text, e.g. ``load_prompt(dir, "persona", "synthesize.md")``.

    ``overrides`` is an optional ``{key: body}`` map keyed by the prompt's path joined with
    ``/`` (e.g. ``persona/synthesize.md``); when a key is present its body is returned in
    place of the file, so a versioned store can override the on-disk prompt. A missing key
    (or no ``overrides`` at all) falls back to reading the file."""
    if overrides is not None:
        body = overrides.get("/".join(parts))
        if body is not None:
            return body
    return Path(prompts_dir).joinpath(*parts).read_text(encoding="utf-8")


def render(template: str, **values: str) -> str:
    """Substitute ``{{KEY}}`` placeholders (KEY upper-cased) with the given values.

    A SINGLE pass over the template: a substituted value is never rescanned, so a
    value that itself contains ``{{OTHER}}`` cannot be re-expanded by a later key.
    An unknown ``{{TOKEN}}`` is left untouched."""
    lookup = {key.upper(): value for key, value in values.items()}
    return _TOKEN.sub(lambda m: lookup.get(m.group(1).upper(), m.group(0)), template)
