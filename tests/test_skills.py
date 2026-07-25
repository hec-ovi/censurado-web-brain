"""Structural tests for the unified skill package (cli/SKILL.md resolver + cli/skills/*).

These guard the agent-facing contract, not Python behavior: every sub-skill the resolver
routes to must actually exist, and every sub-skill must carry valid agentskills.io frontmatter
whose `name` matches its directory. A driving agent reads these files by path, so a dangling
route or a name/dir mismatch is a real break even though no code imports them. Stdlib only."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "cli"
RESOLVER = CLI / "SKILL.md"
SKILLS_DIR = CLI / "skills"
WORKFLOW_DIR = ROOT / "prompts" / "workflow"


def test_no_skill_or_prompt_names_the_deploy_verb():
    # Regression guard for the demo-day bug: the go-live verb is `publicar`, and the word
    # "deploy" must never name a command/mode on any agent-facing surface (it made the agent
    # confuse going-public with the localhost preview). Infra file paths (deploy/deploy-cdn.sh,
    # deploy/CACHING.md, ./deploy/) and the external `wrangler pages deploy` command are allowed.
    surfaces = [RESOLVER] + sorted(SKILLS_DIR.glob("*/SKILL.md")) + sorted(WORKFLOW_DIR.glob("*.md"))
    banned = ("censurado.py deploy", "make deploy", "--mode deploy", "skills/deploy/", "step deploy")
    offenders = {}
    for path in surfaces:
        text = path.read_text(encoding="utf-8")
        hits = [b for b in banned if b in text]
        if hits:
            offenders[path.relative_to(ROOT).as_posix()] = hits
    assert not offenders, f"agent-facing surfaces still name the old deploy verb/mode: {offenders}"


def _frontmatter(md_path):
    """The YAML frontmatter block of a SKILL.md as a {key: value} map, values as raw strings.
    Minimal (name/description are simple scalars here), so no YAML dependency is needed."""
    text = md_path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, f"{md_path} has no --- frontmatter block"
    block = m.group(1)
    out, key = {}, None
    for line in block.splitlines():
        top = re.match(r"^([A-Za-z_][\w-]*):\s?(.*)$", line)
        if top:
            key = top.group(1)
            out[key] = top.group(2).strip()
        elif key and line.strip():                      # folded/continued scalar (e.g. >- blocks)
            out[key] = (out[key] + " " + line.strip()).strip()
    return out


def test_every_subskill_has_valid_frontmatter_matching_its_dir():
    skill_files = sorted(SKILLS_DIR.glob("*/SKILL.md"))
    assert skill_files, "no sub-skills found under cli/skills/"
    for sk in skill_files:
        fm = _frontmatter(sk)
        assert fm.get("name") == sk.parent.name, \
            f"{sk}: frontmatter name {fm.get('name')!r} != dir {sk.parent.name!r}"
        assert fm.get("description"), f"{sk}: missing/empty description"
        assert len(fm["description"]) > 30, f"{sk}: description too thin to route on"


def test_resolver_routes_only_to_subskills_that_exist():
    refs = set(re.findall(r"skills/([\w-]+)/SKILL\.md", RESOLVER.read_text(encoding="utf-8")))
    assert refs, "the resolver routes to no sub-skills"
    for name in refs:
        assert (SKILLS_DIR / name / "SKILL.md").is_file(), \
            f"resolver routes to skills/{name}/ but it does not exist"


def test_the_operations_surface_is_present_and_routed():
    # The acceptance surface: a fresh agent given only cli/SKILL.md can reach each of these.
    # deploy + prompts complete the plan's target layout (go-live and prompt-editing).
    expected = {"write-article", "daily-batch", "authors", "sources", "portada", "media",
                "websearch", "publicar", "prompts"}
    on_disk = {p.parent.name for p in SKILLS_DIR.glob("*/SKILL.md")}
    assert expected <= on_disk, f"missing sub-skills: {expected - on_disk}"
    routed = set(re.findall(r"skills/([\w-]+)/SKILL\.md", RESOLVER.read_text(encoding="utf-8")))
    assert expected <= routed, f"present but not routed from the resolver: {expected - routed}"


def _body(md_path):
    """A SKILL.md's content BELOW the frontmatter block, so a rail is verified in the prose the
    agent acts on, not merely satisfied by the one-line frontmatter description (which would let a
    gutted body still pass)."""
    text = md_path.read_text(encoding="utf-8")
    return re.sub(r"^---\n.*?\n---\n", "", text, count=1, flags=re.S)


def test_publicar_skill_carries_its_safety_rails():
    # publicar (go live) is the one public, irreversible action. Assert the rails in the BODY, not
    # the whole file: the frontmatter description alone must not satisfy these, or a gutted body
    # would pass. The verb is `publicar`; the word "deploy" no longer names it on any skill surface.
    body = _body(SKILLS_DIR / "publicar" / "SKILL.md").lower()
    # The contrast is what matters, not the port: the local site's port comes from the host's
    # SITE_PORT, so a doc that hardcodes 8080 is wrong on any box that moved it.
    assert "local site" in body, "publicar body must contrast the local preview"
    assert "localhost:8080" not in body, "the local site's port is host config, do not hardcode it"
    assert "censurado.py publicar --yes" in body, "publicar body must name the real command (the verb)"
    assert "elcensuradoweb.com" in body, "publicar body must name the public target"
    assert "explicit yes" in body, "publicar body must require an explicit yes"
    assert any(p in body for p in ("unattended", "never publish on your own")), \
        "publicar body must keep the do-not-self-publish rail"
    assert any(p in body for p in ("front page", "what will go live")), \
        "publicar body must keep the show-what-goes-live step"


def test_resolver_carries_the_public_publish_rail():
    # cli/SKILL.md is always loaded; an agent can go live from the resolver row alone, so the
    # preview-is-local + get-a-yes-before-going-public rail must live on the always-loaded surface.
    low = RESOLVER.read_text(encoding="utf-8").lower()
    assert "local site" in low, "resolver must say preview is local"
    assert "site_port" in low, "resolver must point at SITE_PORT rather than hardcode a port"
    assert "publicar" in low and any(p in low for p in ("get a yes", "confirm", "explicit yes")), \
        "resolver must carry the confirm-before-public-publish rail"


def test_prompts_skill_treats_every_prompt_as_a_file_not_a_db():
    # Prompts are MD files edited in place (git is the history); there is NO DB version store, no
    # staging, no promote. The skill must reflect that single-store, file-only model.
    text = (SKILLS_DIR / "prompts" / "SKILL.md").read_text(encoding="utf-8")
    low = text.lower()
    assert "workflow/" in text and "persona/" in text, "must name both prompt key shapes"
    assert "set-prompt" in text, "must show the edit verb"
    assert "in place" in low and "git" in low, "edits rewrite the file in place; git is the history"
    assert any(p in low for p in ("no database", "never a database", "no version")), \
        "must state there is no DB version store for prompts"
    assert "--stage" not in text, "the --stage flag is removed; prompts are files, not DB versions"
    # and steer numeric-floor edits away from prompts to the right place
    assert "parameters.json" in text or "set-floor" in text, \
        "prompts skill must send numeric-floor edits to parameters.json / set-floor, not a prompt"


def test_daily_batch_skill_tail_wires_the_portada_arrange_walk():
    # After a sweep publishes its items, the batch skill's tail arranges each day's front page
    # via the standalone portal-review walk, one UTC day at a time, loading the day with
    # `archive --day`. Assert it in the BODY so a gutted body cannot pass on the frontmatter.
    body = _body(SKILLS_DIR / "daily-batch" / "SKILL.md").lower()
    assert "step --mode portal-review" in body, "batch tail must run the arrange walk"
    assert "archive --day" in body, "batch tail must name the day loader"
    assert "one day at a time" in body, "arrange is per UTC day"
    assert "publicar --yes" in body, "going live stays a separate human-gated publicar verb"


def test_daily_batch_routes_bulk_topic_merges_to_the_cleanse_walk():
    # The stale claim that the brain/cleanse "no longer exists" is gone. Bulk entity-variant
    # normalization routes to the topic-cleanse walk; single-article fixes stay the edit verb.
    body = _body(SKILLS_DIR / "daily-batch" / "SKILL.md").lower()
    assert "no bulk cleanse pass anymore" not in body, "stale claim that the cleanse is gone must be removed"
    assert "no separate brain" not in body, "stale claim that there is no brain must be removed"
    assert "step --mode topic-cleanse" in body, "batch skill must route bulk merges to the cleanse walk"
    assert "edit <slug>" in body, "the per-article edit fix must survive"


def test_redactor_skill_describes_the_assignment_sweep():
    # The redactor sub-skill is the assignment desk: a plain web-search sweep of the freshest news
    # (last ~60 minutes, no assigned feeds), assign each story to the best-fit author, then write
    # each as its own single-article walk. Assert in the BODY so the frontmatter alone cannot pass.
    body = _body(SKILLS_DIR / "redactor" / "SKILL.md").lower()
    assert "step --mode redactor" in body, "redactor body must name the mode verb"
    assert "plain web-search" in body or "plain web search" in body, "must be a plain web-search sweep"
    assert "60 minutes" in body, "must carry the last-hour freshness window"
    assert "single-article" in body, "each assignment becomes its own single-article walk"
    assert "assign" in body, "the redactor assigns stories to authors"
    assert "publicar --yes" in body, "going live stays the separate human-gated verb"


def test_redactor_skill_is_routed_from_the_resolver():
    # A fresh agent given only cli/SKILL.md must be able to reach the redactor desk.
    routed = set(re.findall(r"skills/([\w-]+)/SKILL\.md", RESOLVER.read_text(encoding="utf-8")))
    assert "redactor" in routed, "the resolver must route to the redactor sub-skill"


def test_resolver_routes_the_topic_cleanse_and_topics_verbs():
    # cli/SKILL.md is always loaded, so the corpus-wide topic merge and the tag inventory must
    # be reachable from the dispatcher, not just from the daily-batch tail.
    low = RESOLVER.read_text(encoding="utf-8").lower()
    assert "step --mode topic-cleanse" in low, "resolver must route the topic-cleanse walk"
    assert "censurado.py topics" in low, "resolver must expose the tag inventory verb"


def test_retired_flat_docs_are_gone_and_unreferenced():
    # ART-DIRECTOR / DAILY-SWEEP / TOOLKIT were folded into the sub-skills (media / daily-batch /
    # write-article) and DELETED. Guard both that they stay gone AND that nothing under cli/ still
    # points at them (a dangling reference the agent surface would follow into nothing).
    gone = ("ART-DIRECTOR.md", "DAILY-SWEEP.md", "TOOLKIT.md")
    for name in gone:
        assert not (CLI / name).exists(), f"cli/{name} should be deleted, not resurrected"
    pattern = re.compile("|".join(re.escape(n) for n in gone))
    for path in CLI.rglob("*"):
        if not path.is_file() or path.suffix not in (".py", ".md", ".json"):
            continue
        hits = set(pattern.findall(path.read_text(encoding="utf-8", errors="ignore")))
        assert not hits, f"{path} still references a retired doc: {hits}"
