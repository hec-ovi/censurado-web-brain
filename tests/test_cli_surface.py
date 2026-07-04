"""CLI surface consistency guards.

The brain is driven by a small agent that tool-calls `python3 cli/censurado.py <verb>` off
cli/SKILL.md alone, never by reading code. The brain is the MORPHABLE INNER: you reshape it by
editing the prompt/skill/tool FILES by hand, so this file does NOT freeze that surface. It only
guards the coherence a hand edit can break with no compiler to catch it: a skill naming a verb
that does not exist, a verb whose recipe file is missing, or a workflow node referencing a
{{PARAMETER}} that parameters.json does not define. Each fires on a real driver dead-end.

Stdlib only. The CLI is loaded by file path (it is a script, not an installed console entry).
"""

import contextlib
import importlib.util
import io
import json
import re
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "cli" / "censurado.py"
SKILLS_DIR = ROOT / "cli" / "skills"
RESOLVER = ROOT / "cli" / "SKILL.md"
PROMPTS_DIR = ROOT / "prompts"
PARAMS = ROOT / "cli" / "workflow" / "parameters.json"


def _load():
    spec = importlib.util.spec_from_file_location("censurado_surface", CLI)
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


# Tokens that legitimately follow `censurado.py ` in prose without being a verb (the generic
# placeholder the resolver uses to introduce the CLI). Everything else must be a real verb.
PLACEHOLDERS = {"verb"}

# Recipe files the agent-facing surface binds a verb to; each must ship or a driver dead-ends.
# `style` READS its file at runtime (the verb fails soft if absent). `create-author` POINTS the
# agent at its file (its docstring and the authors skill say `prompt persona/synthesize.md`), so
# the fetch dead-ends if absent. Value is (prompts/-relative file, how the surface consumes it).
WIRED_RECIPE_FILES = {
    "style": ("editorial/style.md", "reads at runtime"),
    "create-author": ("persona/synthesize.md", "points the agent at via `prompt`"),
}


def _parameters():
    """The parameter names parameters.json defines (the {{NAME}} tokens `step` fills into nodes)."""
    return set(json.loads(PARAMS.read_text(encoding="utf-8")))


def test_publish_is_an_alias_of_preview():
    # The two names must dispatch to the same handler, so a skill may say either (SKILL.md docs it).
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
    full `censurado.py` command) still counts as discoverable."""
    return _referenced_verbs(text) | set(re.findall(r"`([a-z][a-z-]+)\b", text))


def test_resolver_and_subskills_reference_only_real_verbs():
    # A driver runs whatever verb a skill names. Every verb named in the resolver or a sub-skill
    # must be a registered sub-command, else the driver dead-ends at runtime with nothing to catch it.
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


def test_every_verb_is_reachable_from_the_documented_surface():
    # No orphan verbs: each registered verb must be discoverable from the resolver or a sub-skill
    # (else an agent driving off SKILL.md is never told it exists). `publish` rides on `preview`.
    live = _argparse_verbs()
    named = set()
    for path in [RESOLVER] + sorted(SKILLS_DIR.glob("*/SKILL.md")):
        named |= _mentioned_tokens(path.read_text(encoding="utf-8"))
    reachable = named | {"publish"} if "preview" in named else named
    unreachable = live - reachable - PLACEHOLDERS
    assert not unreachable, (
        "verbs exist but no resolver row or sub-skill mentions them (unreachable to a driver): "
        f"{sorted(unreachable)}"
    )


def test_wired_verbs_have_their_backing_recipe_file():
    # A recipe file a verb reads (style) or directs a driver to fetch (create-author) must ship
    # non-empty, or that path dead-ends.
    for verb, (rel, how) in WIRED_RECIPE_FILES.items():
        f = PROMPTS_DIR / rel
        assert f.is_file() and f.read_text(encoding="utf-8").strip(), (
            f"the `{verb}` verb {how} prompts/{rel}, which is missing or empty in the repo; "
            f"that path dead-ends for every driver."
        )


def test_style_verb_succeeds_against_the_shipped_repo():
    # Drive the real verb against the real PROMPTS_DIR and confirm it exits 0 and prints the guide
    # (the end-to-end twin of a dead-end the audit found: a `style` route with no backing file).
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cz.cmd_style(SimpleNamespace())
    out = buf.getvalue()
    assert rc == 0, "the `style` verb failed against the shipped prompts/ recipe"
    assert "## Lexicon" in out, "the style verb did not print the editorial guide"


def test_no_workflow_node_references_an_undefined_parameter():
    # The gate fills {{PARAMETER}} placeholders from parameters.json; an ALL-CAPS placeholder that
    # parameters.json does not define would ship to the agent unfilled. Content markers
    # ({{tweet:...}}, {{relacionado:}}, {{video:}}) are lowercase and are left untouched.
    defined = _parameters()
    ref = re.compile(r"\{\{([A-Z][A-Z_]+)\}\}")
    dangling = {}
    for node in sorted((PROMPTS_DIR / "workflow").glob("*.md")):
        for name in set(ref.findall(node.read_text(encoding="utf-8"))):
            if name not in defined:
                dangling.setdefault(node.name, set()).add(name)
    assert not dangling, (
        "workflow node(s) reference an ALL-CAPS placeholder that parameters.json does not define, "
        "so it ships unfilled: " + json.dumps({k: sorted(v) for k, v in dangling.items()})
    )
