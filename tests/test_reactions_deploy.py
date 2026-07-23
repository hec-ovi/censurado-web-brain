"""Drift guard: the reactions lane (Pages Function + D1) stays wired end to end.

The article like/dislike bar only works if four pieces agree: the function exists
with both handlers, the deploy script generates the wrangler.toml that binds D1 as
``DB``, .env.example documents the parameters the script reads, and the generated file
stays out of git. Behavioral tests for the function itself are JS (`npm test`,
functions/api/reactions.test.js); this suite pins the wiring between the pieces.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FUNCTION = ROOT / "functions" / "api" / "reactions.js"
DEPLOY = ROOT / "deploy" / "deploy-cdn.sh"


def test_function_exports_both_handlers():
    text = FUNCTION.read_text()
    assert "export async function onRequestGet" in text
    assert "export async function onRequestPost" in text


def test_deploy_generates_the_d1_binding_wrangler_toml():
    text = DEPLOY.read_text()
    assert "D1_REACTIONS_ID" in text
    assert 'pages_build_output_dir = "dist"' in text
    assert 'binding = "DB"' in text, "the function reads env.DB; the binding name must stay DB"
    assert "REACTIONS_SALT" in text


def test_deploy_wires_the_optional_per_voter_cap():
    # The function reads env.REACTIONS_MAX_PER_IP; without this emission the cap
    # would be stuck at the 500 default in production with no way to tune it.
    text = DEPLOY.read_text()
    assert "REACTIONS_MAX_PER_IP" in text
    assert "must be a plain integer" in text, "a non-numeric cap must hard-stop the deploy"


def test_deploy_copies_every_media_type_not_just_png():
    # The media store keys by sha with any image extension (jpg/webp land via
    # POST /media); a png-only glob silently 404s those on the live site.
    text = DEPLOY.read_text()
    assert "cp /srcmedia/* /out/media/" in text
    assert "/srcmedia/*.png" not in text


def test_deploy_refuses_a_d1_binding_without_a_salt():
    # Bare sha256(IP) keys would de-anonymize voters; the script must hard-stop.
    text = DEPLOY.read_text()
    assert "REACTIONS_SALT is empty but D1_REACTIONS_ID is set" in text


def test_env_example_documents_the_reactions_parameters():
    keys = set()
    for line in (ROOT / ".env.example").read_text().splitlines():
        m = re.match(r"\s*#?\s*([A-Z][A-Z0-9_]*)=", line)
        if m:
            keys.add(m.group(1))
    assert {"D1_REACTIONS_ID", "D1_REACTIONS_NAME", "REACTIONS_SALT",
            "REACTIONS_MAX_PER_IP"} <= keys


def test_generated_wrangler_toml_is_gitignored():
    lines = [line.strip() for line in (ROOT / ".gitignore").read_text().splitlines()]
    assert "wrangler.toml" in lines
