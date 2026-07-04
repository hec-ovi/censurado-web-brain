"""Drift guard: .env.example must document every variable the stack reads.

The newsroom is configured through .env: bootstrap.sh writes the secrets, and the
operator fills the rest by copying .env.example. If docker-compose.yml interpolates a
${VAR} or bootstrap.sh writes a key that .env.example never mentions, an operator who
copies .env.example gets a silently under-configured stack. This test parses the three
files and asserts .env.example is a superset of what compose interpolates and what
bootstrap writes, so a new variable cannot be wired in without being documented.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "docker-compose.yml"
BOOTSTRAP = ROOT / "bootstrap.sh"
ENV_EXAMPLE = ROOT / ".env.example"


def _env_example_keys() -> set[str]:
    # A key is "documented" if it appears as KEY= at the start of a line, whether it is
    # set (KEY=value) or shown as an optional commented default (# KEY=value).
    keys = set()
    for line in ENV_EXAMPLE.read_text().splitlines():
        m = re.match(r"\s*#?\s*([A-Z][A-Z0-9_]*)=", line)
        if m:
            keys.add(m.group(1))
    return keys


def _compose_vars() -> set[str]:
    # Every ${VAR} / ${VAR:-default} interpolation compose reads from the environment.
    return set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)", COMPOSE.read_text()))


def _bootstrap_set_env_keys() -> set[str]:
    # Every key bootstrap.sh writes into .env through the set_env helper.
    return set(re.findall(r"set_env\s+([A-Z][A-Z0-9_]*)", BOOTSTRAP.read_text()))


def test_env_example_documents_every_compose_variable():
    missing = sorted(_compose_vars() - _env_example_keys())
    assert not missing, f".env.example is missing compose ${{VAR}}s: {missing}"


def test_env_example_documents_every_bootstrap_written_key():
    missing = sorted(_bootstrap_set_env_keys() - _env_example_keys())
    assert not missing, f".env.example is missing bootstrap-written keys: {missing}"
