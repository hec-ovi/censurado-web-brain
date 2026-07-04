"""Seam C: the agent-facing contract freeze (CLI verbs + resolver/skill routing + the gate).

A small or remote driver operates this newsroom by tool-calling `python3 cli/censurado.py <verb>`
off `cli/SKILL.md` alone, never by reading code. That makes the verb set, the routes that name
verbs, and the workflow gate's knobs a CONTRACT: if a verb is renamed or a skill points an agent
at a verb or recipe file that no longer exists, the driver dead-ends with no compile error to
catch it. These tests pin that surface. The companion prose is cli/CONTRACT.md; the rule is the
same as the backend's Seam A/B: to change a verb, a routed reference, or a knob, edit
cli/CONTRACT.md AND the frozen set in this file in the SAME commit.

Stdlib only. The CLI is loaded by file path (it is a script, not an installed console entry).
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "cli" / "censurado.py"
SKILLS_DIR = ROOT / "cli" / "skills"
RESOLVER = ROOT / "cli" / "SKILL.md"
PROMPTS_DIR = ROOT / "prompts"
PARAMS = ROOT / "cli" / "workflow" / "parameters.json"


def _load():
    spec = importlib.util.spec_from_file_location("censurado_contract", CLI)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cz = _load()


def _argparse_verbs():
    """The verb names the CLI actually registers (argparse sub-parser keys), from the live parser."""
    parser = cz.build_parser()
    subs = [a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction"]
    assert subs, "cli/censurado.py exposes no sub-commands"
    return set(subs[0]._name_parser_map.keys())


# The FROZEN verb set. `publish` is the documented alias of `preview`. Changing this set is a
# contract change: update cli/CONTRACT.md in the same commit. (Add/rename/remove all land here.)
FROZEN_VERBS = {
    "get", "archive", "preview", "publish", "edit", "unpublish", "media", "image", "tweet", "truth",
    "personas", "persona", "create-author", "remove-author", "sources", "profile-topics",
    "portada", "portals", "prompt", "set-prompt", "style", "set-floor", "doctor", "step",
}

# Tokens that legitimately follow `censurado.py ` in prose without being a verb (the generic
# placeholder the resolver uses to introduce the CLI). Everything else must be a real verb.
PLACEHOLDERS = {"verb"}

# The gate's enforced numeric knobs. parameters.json holds exactly these; a workflow node may
# only reference these as an ALL-CAPS {{...}} placeholder.
FROZEN_KNOBS = {"MIN_SOURCES", "MIN_PER_TYPE", "RESPIN_PASSES", "TOPIC_CAP"}

# Recipe files the agent-facing surface binds a verb to; each must ship or a driver dead-ends.
# `style` READS its file at runtime (the verb fails soft if absent: the shipped `style`
# regression). `create-author` POINTS the agent at its file (its docstring and the authors
# skill say `prompt persona/synthesize.md`), so the fetch dead-ends if absent. Value is
# (prompts/-relative file, how the surface consumes it).
WIRED_RECIPE_FILES = {
    "style": ("editorial/style.md", "reads at runtime"),
    "create-author": ("persona/synthesize.md", "points the agent at via `prompt`"),
}


def test_the_verb_set_is_frozen():
    live = _argparse_verbs()
    assert live == FROZEN_VERBS, (
        "cli/censurado.py verb set drifted from the frozen contract.\n"
        f"  added (in CLI, not in contract): {sorted(live - FROZEN_VERBS)}\n"
        f"  removed (in contract, not in CLI): {sorted(FROZEN_VERBS - live)}\n"
        "If this is intentional, update FROZEN_VERBS here AND cli/CONTRACT.md in the same commit."
    )


def test_publish_is_an_alias_of_preview():
    # The two names must dispatch to the same handler, so a skill may say either.
    parser = cz.build_parser()
    fn = {}
    for name in ("preview", "publish"):
        fn[name] = parser.parse_args([name, "--author", "x", "--title", "y"]).fn
    assert fn["preview"] is fn["publish"] is cz.cmd_publish


def _referenced_verbs(text):
    """Every `censurado.py <token>` reference in an agent-facing doc, minus the `<verb>`
    placeholder form (a literal `<` after the space) which is generic, not a real verb."""
    return set(re.findall(r"censurado\.py (?!<)([a-z][a-z-]+)", text))


def _mentioned_tokens(text):
    """A looser sweep for reachability: the `censurado.py <verb>` command forms PLUS bare
    backtick-quoted tokens (`truth`, `get <slug>`), so a verb a skill names in prose (not as a
    full `censurado.py` command) still counts as discoverable. The token is the word right after
    an opening backtick, whether the backtick closes immediately or an argument follows."""
    return _referenced_verbs(text) | set(re.findall(r"`([a-z][a-z-]+)\b", text))


def test_resolver_and_subskills_reference_only_real_verbs():
    # THE cross-layer freeze (closes the audit's CLI gap): a driver runs whatever verb a skill
    # names. Every verb named in the resolver or any sub-skill must be a registered sub-command.
    live = _argparse_verbs()
    surfaces = [RESOLVER] + sorted(SKILLS_DIR.glob("*/SKILL.md"))
    offenders = {}
    for path in surfaces:
        for tok in _referenced_verbs(path.read_text(encoding="utf-8")):
            if tok in PLACEHOLDERS or tok in live:
                continue
            offenders.setdefault(path.relative_to(ROOT).as_posix(), set()).add(tok)
    assert not offenders, (
        "an agent-facing doc tells the driver to run a verb that cli/censurado.py does not have: "
        + json.dumps({k: sorted(v) for k, v in offenders.items()})
    )


def test_every_frozen_verb_is_reachable_from_the_documented_surface():
    # No orphan verbs: each verb must be discoverable from the resolver or a sub-skill (else an
    # agent driving off SKILL.md is never told it exists). Read verbs the resolver names are fine
    # too; `publish` rides on `preview`.
    live = _argparse_verbs()
    named = set()
    for path in [RESOLVER] + sorted(SKILLS_DIR.glob("*/SKILL.md")):
        named |= _mentioned_tokens(path.read_text(encoding="utf-8"))
    reachable = named | {"publish"} if "preview" in named else named
    unreachable = live - reachable - PLACEHOLDERS
    assert not unreachable, (
        f"verbs exist but no resolver row or sub-skill mentions them (unreachable to a driver): "
        f"{sorted(unreachable)}"
    )


def test_wired_verbs_have_their_backing_recipe_file():
    # The `style` regression guard: a recipe file a verb reads (style) or directs a driver to
    # fetch (create-author) must ship non-empty, or that path dead-ends.
    for verb, (rel, how) in WIRED_RECIPE_FILES.items():
        f = PROMPTS_DIR / rel
        assert f.is_file() and f.read_text(encoding="utf-8").strip(), (
            f"the `{verb}` verb {how} prompts/{rel}, which is missing or empty in the repo; "
            f"that path dead-ends for every driver."
        )


def test_style_verb_succeeds_against_the_shipped_repo():
    # Stronger than file-presence: drive the real verb against the real PROMPTS_DIR (no tmp
    # recipe) and confirm it exits 0. This is the end-to-end twin of the dead-end the audit found.
    from types import SimpleNamespace

    import io
    import contextlib

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cz.cmd_style(SimpleNamespace())
    out = buf.getvalue()
    assert rc == 0, "the `style` verb failed against the shipped prompts/ recipe"
    assert "espanol neutro" in out.lower(), "the style verb did not print the editorial voice guide"


def test_parameters_json_holds_exactly_the_frozen_knobs():
    data = json.loads(PARAMS.read_text(encoding="utf-8"))
    assert set(data) == FROZEN_KNOBS, (
        f"cli/workflow/parameters.json knob set drifted: got {sorted(data)}, "
        f"expected {sorted(FROZEN_KNOBS)}. Update FROZEN_KNOBS + cli/CONTRACT.md together."
    )
    for k, v in data.items():
        assert isinstance(v, int) and v >= 0, f"knob {k} must be a non-negative int, got {v!r}"


def test_no_workflow_node_references_an_unknown_knob():
    # The gate fills {{KNOB}} placeholders from parameters.json; an ALL-CAPS placeholder that is
    # NOT a known knob would ship to the agent unfilled. Content markers ({{tweet:...}},
    # {{relacionado:}}, {{video:}}) are lowercase and are intentionally left untouched.
    knob_ref = re.compile(r"\{\{([A-Z][A-Z_]+)\}\}")
    dangling = {}
    for node in sorted((PROMPTS_DIR / "workflow").glob("*.md")):
        for name in set(knob_ref.findall(node.read_text(encoding="utf-8"))):
            if name not in FROZEN_KNOBS:
                dangling.setdefault(node.name, set()).add(name)
    assert not dangling, (
        "workflow node(s) reference an ALL-CAPS placeholder that is not a parameters.json knob, "
        "so it ships unfilled: " + json.dumps({k: sorted(v) for k, v in dangling.items()})
    )
