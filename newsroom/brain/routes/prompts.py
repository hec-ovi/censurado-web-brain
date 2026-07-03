"""The prompt library API: read and edit the shipped prompt files.

Every prompt (the agentic-workflow step-gate nodes under ``workflow/`` AND the author-voice /
house-style prompts like ``persona/synthesize.md``) is a plain FILE in the shipped prompt
library (``settings.prompts_dir``). A read serves the file; a publish WRITES the file in place.
Git is the version history: there is NO database copy of any prompt. A prompt's key is its
relative path with forward slashes (e.g. ``workflow/30-research.md``, ``persona/synthesize.md``);
because a key contains a slash it rides as a ``key`` query param (reads) or in the body (POST).

Adding a brand-new prompt is a code change (create the file, and for a workflow node a manifest
entry too), not an API write: a publish only edits a key that already exists on disk. Auth is
wired app-wide in ``create_app``, so there is none here.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel

from newsroom.brain.problems import _problem

__all__ = ["router"]

router = APIRouter(tags=["prompts"])


class PromptIn(BaseModel):
    """The POST /prompts/template body: the new ``body`` for an EXISTING prompt ``key`` (its
    relative path, e.g. ``workflow/30-research.md``). The write lands on the .md/.json file in
    place; git is the version history, so there is no version/activate concept here."""

    key: str
    body: str = ""


class PromptOut(BaseModel):
    """One prompt: its ``key`` and current ``body``, read from / written to the on-disk file."""

    key: str
    body: str


class PromptListOut(BaseModel):
    """The GET /prompts response: one row per shipped prompt key plus ``total``, the library
    overview an operator browses."""

    templates: list[PromptOut]
    total: int


def _list_shipped_keys(prompts_dir: Path | str) -> list[str]:
    """Every shipped prompt key under ``prompts_dir``, each its relative path with forward
    slashes (e.g. ``workflow/95-image.md``). Globs ``*.md`` and ``*.json`` (the step-gate's
    ``workflow/manifest.json`` node-order config rides in the same library), so a stray file of
    another extension is never a key."""
    root = Path(prompts_dir)
    keys: list[str] = []
    for pattern in ("*.md", "*.json"):
        for path in root.rglob(pattern):
            if path.is_file():
                keys.append(path.relative_to(root).as_posix())
    return keys


def _read_shipped_prompt(prompts_dir: Path | str, key: str) -> str | None:
    """Read a shipped prompt ``key`` from the on-disk library, or ``None`` if absent. Guards
    path traversal: the key must be a ``.md``/``.json`` file that resolves INSIDE ``prompts_dir``
    (a key with ``..`` or pointing elsewhere is treated as absent, never a file read)."""
    if not (key.endswith(".md") or key.endswith(".json")):
        return None
    root = Path(prompts_dir).resolve()
    candidate = root.joinpath(*key.split("/")).resolve()
    if not candidate.is_relative_to(root):
        return None
    try:
        return candidate.read_text(encoding="utf-8")
    except OSError:
        return None


def _write_shipped_prompt(prompts_dir: Path | str, key: str, body: str) -> None:
    """Write a prompt ``key`` back to the on-disk library (an edit in place; the file is
    git-versioned). Same path-traversal guard as the read side, and the key must be an EXISTING
    ``.md``/``.json`` file: adding a brand-new prompt is a code change, not an API write. Raises
    ``ValueError`` on a bad key, which the route maps to 422."""
    if not (key.endswith(".md") or key.endswith(".json")):
        raise ValueError(f"prompt key must be a .md or .json file: {key!r}")
    root = Path(prompts_dir).resolve()
    candidate = root.joinpath(*key.split("/")).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(f"key escapes the prompt library: {key!r}")
    if not candidate.is_file():
        raise ValueError(f"no prompt {key!r} to edit; adding a new one is a code change")
    candidate.write_text(body, encoding="utf-8")


@router.get("/prompts", status_code=200, response_model=PromptListOut)
async def list_prompts(request: Request):
    """The prompt library: one row per shipped key with its current on-disk body, sorted for
    stable output. ``total`` is the count of keys."""
    prompts_dir = request.app.state.settings.prompts_dir
    templates: list[PromptOut] = []
    for key in sorted(set(_list_shipped_keys(prompts_dir))):
        body = _read_shipped_prompt(prompts_dir, key)
        if body is not None:
            templates.append(PromptOut(key=key, body=body))
    return PromptListOut(templates=templates, total=len(templates))


@router.get("/prompts/template", status_code=200, response_model=PromptOut)
async def get_prompt(request: Request, key: str):
    """One prompt's current body, read from the on-disk file. 404 ``prompt_not_found`` when the
    key is not a file in the library."""
    body = _read_shipped_prompt(request.app.state.settings.prompts_dir, key)
    if body is None:
        return _problem(404, "prompt_not_found", detail=f"no prompt for key {key!r}")
    return PromptOut(key=key, body=body)


@router.post("/prompts/template", status_code=201, response_model=PromptOut)
async def publish_prompt(body: PromptIn, request: Request):
    """Publish a prompt edit: WRITE the ``.md``/``.json`` in place (git is the version history).
    422 ``invalid_prompt`` on a blank key or body, an unknown file, or a traversing key. Returns
    201 + the written body."""
    prompts_dir = request.app.state.settings.prompts_dir
    if not body.key.strip():
        return _problem(422, "invalid_prompt", detail="a prompt key is required")
    if not body.body.strip():
        return _problem(422, "invalid_prompt", detail="a prompt body cannot be blank")
    try:
        _write_shipped_prompt(prompts_dir, body.key, body.body)
    except ValueError as exc:
        return _problem(422, "invalid_prompt", detail=str(exc))
    return PromptOut(key=body.key, body=body.body)
