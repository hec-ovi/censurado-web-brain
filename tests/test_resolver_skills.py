"""Drift guard: every path the resolver hands an agent must resolve from the repo root.

cli/SKILL.md is the always-loaded dispatcher: it routes an intent to a sub-skill by telling the
agent to `read <path>/SKILL.md`. Agents run from the repo root (the same cwd where
`python3 cli/censurado.py` works), so those paths must be repo-root-relative, i.e. begin with
`cli/skills/`. A bare `skills/daily-batch/SKILL.md` resolves to the non-existent
`/work/skills/daily-batch` and the sub-skill silently fails to load (the daily-batch 404). This
test pins every reference to a real file and guards against the bare-path regression, and checks
no sub-skill is orphaned (present on disk but unrouted).
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESOLVER = ROOT / "cli" / "SKILL.md"
SKILLS_DIR = ROOT / "cli" / "skills"

RESOLVER_TEXT = RESOLVER.read_text()

# Backtick-quoted references to a SKILL.md file, e.g. `cli/skills/daily-batch/SKILL.md`.
REFERENCED = sorted(set(re.findall(r"`([^`]*SKILL\.md)`", RESOLVER_TEXT)))


def test_the_resolver_references_at_least_the_sub_skills():
    # sanity: the dispatcher must actually route somewhere
    assert REFERENCED, "cli/SKILL.md references no sub-skill files"


def test_every_referenced_skill_path_resolves_from_repo_root():
    for rel in REFERENCED:
        assert (ROOT / rel).is_file(), (
            f"cli/SKILL.md points an agent at {rel!r}, which does not exist from the repo root"
        )


def test_no_bare_skills_path_regression():
    # the bug: a path missing the cli/ prefix resolves to /work/skills/... (404). Every
    # referenced SKILL.md must live under cli/skills/.
    for rel in REFERENCED:
        assert rel.startswith("cli/skills/"), (
            f"cli/SKILL.md references {rel!r} without the cli/skills/ prefix; it will 404 from "
            "the repo root"
        )
    assert "`skills/" not in RESOLVER_TEXT, "a bare `skills/... path crept back into cli/SKILL.md"


def test_no_orphan_sub_skill():
    on_disk = {f"cli/skills/{d.name}/SKILL.md" for d in SKILLS_DIR.iterdir()
               if (d / "SKILL.md").is_file()}
    unrouted = on_disk - set(REFERENCED)
    assert not unrouted, f"sub-skills exist but the resolver never routes to them: {sorted(unrouted)}"
