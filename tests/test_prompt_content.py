"""The workflow step-gate library is parameter-driven and gates the editorial bar.

These assertions pin the prompt TEXT of the LIVE step-gate nodes (the ``workflow/*``
prompts the CLI ``step`` verb walks one at a time) against the editorial bar: the
cross-source count, the topic cap, and the respin-pass count are client-filled
placeholders, never hardcoded numbers; the evaluate node gates all six review
dimensions; and the drafting nodes carry the anti-slop discipline. The last test reads the
manifest off disk and proves every workflow node it references (plus persona/synthesize) is
present, so the step gate never walks into a missing file.
"""

from __future__ import annotations

import json
from pathlib import Path

from newsroom.prompts import load_prompt

# The prompt recipe is on-disk files in this repo's prompts/ dir (git is their history).
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _workflow(name: str) -> str:
    return load_prompt(PROMPTS_DIR, "workflow", name)


def _flat(name: str) -> str:
    # Lowercased with whitespace collapsed to single spaces, so a phrase that wraps
    # across a markdown line break still matches as a substring.
    return " ".join(_workflow(name).lower().split())


def test_research_uses_min_sources_and_per_lean_parameters_not_hardcoded_numbers():
    text = _workflow("30-research.md")
    low = _flat("30-research.md")
    # The floor and the per-lean minimum are client-filled placeholders, never literals.
    assert "{{MIN_SOURCES}}" in text
    assert "{{MIN_PER_TYPE}}" in text
    assert "five" not in low and "six" not in low
    # The research node balances sources by political lean and names the web-search fallback.
    assert "lean" in low
    assert "right, neutral, left" in low
    assert "web search" in low


def test_finalize_uses_topic_cap_parameter_and_themes_plus_entities():
    text = _workflow("90-finalize.md")
    low = _flat("90-finalize.md")
    assert "{{TOPIC_CAP}}" in text
    assert "at most seven" not in low
    # Tags are themes plus named entities, a few sharp ones over a loose list.
    assert "themes" in low and "entities" in low
    assert "sharp tags" in low


def test_respin_states_the_pass_budget_parameter():
    text = _workflow("70-respin.md")
    low = _flat("70-respin.md")
    assert "{{RESPIN_PASSES}}" in text
    # The redundancy/wording/slop checks survive.
    assert "repeated" in low
    assert "ai-slop" in low


def test_evaluate_gates_all_six_dimensions():
    text = _workflow("60-evaluate.md")
    low = _flat("60-evaluate.md")
    # The six gated dimensions are each named.
    for dim in (
        "cross-sourcing",
        "accents",
        "entities",
        "title and bajada",
        "compression",
        "non-redundancy",
    ):
        assert dim in low, f"evaluate node is missing the {dim!r} dimension"
    # It is parameter-driven and is an explicit publish gate with a per-dimension verdict.
    assert "{{MIN_SOURCES}}" in text
    assert "gate" in low
    assert "pass" in low and "revise" in low


def test_draft_node_enforces_the_anti_slop_discipline():
    # The drafting node carries the say-each-idea-once / no-over-hedging rule that every
    # author writes under.
    low = _flat("50-draft.md")
    assert "say each idea once" in low
    assert "synonyms for the same hedge" in low
    assert "over-hedging" in low


def test_source_attribution_is_plain_text_with_no_body_links():
    # The workflow told the model to attribute every claim as a markdown link, which it filled with
    # dead/invented URLs. The rule now: name the source as PLAIN TEXT ("según X") and put NO links in
    # the body at all (any inline link is removed to its text). No "when relevant" exception.
    ban = ("no links", "no markdown links", "never as links", "remove every link", "do not add links")
    for node in ("50-draft.md", "50-draft-institucional.md", "62-semantica-calidad.md", "75-factcheck.md"):
        low = _flat(node)
        assert "plain text" in low, f"{node} must attribute the source as plain text"
        assert any(p in low for p in ban), f"{node} must ban links in the body"
        assert "titled link" not in low, f"{node} still mandates a link format"
        assert "extremely relevant" not in low and "genuinely relevant" not in low, \
            f"{node} must not keep a link exception; links are banned outright"
    # the editorial contract + style guide carry the same plain-text, no-links rule in English;
    # the per-language attribution WORDING is deferred to the `editorial-rules` DB anchors.
    rules = " ".join((PROMPTS_DIR.parent / "EDITORIAL-RULES.md").read_text(encoding="utf-8").lower().split())
    assert "plain text" in rules and "no links" in rules
    style = " ".join((PROMPTS_DIR / "editorial" / "style.md").read_text(encoding="utf-8").lower().split())
    assert "plain text" in style and "no links" in style


def test_named_outlets_must_be_assigned_only():
    # A media outlet may be NAMED only when it is one of the author's ASSIGNED sources; a
    # web-found outlet informs the facts but is never named (attribute to the primary actor
    # instead). This narrows the plain-text "según X" rule to WHICH names are allowed, and an
    # author with no assigned sources names no medium at all, only primary actors.
    checks = {
        "30-research.md": ("assigned", "background"),
        "50-draft.md": ("assigned", "primary actor"),
        "50-draft-institucional.md": ("assigned", "primary actor"),
        "75-factcheck.md": ("assigned", "primary actor"),
    }
    not_named = ("not name", "never name", "no media outlet", "not assigned")
    for node, needles in checks.items():
        low = _flat(node)
        for needle in needles:
            assert needle in low, f"{node} must carry the assigned-only naming rule ({needle!r})"
        assert any(p in low for p in not_named), \
            f"{node} must say a non-assigned/web-found outlet is not named"
    # The same assigned-only rule lives in the editorial contract and the style guide (English now).
    rules = " ".join((PROMPTS_DIR.parent / "EDITORIAL-RULES.md").read_text(encoding="utf-8").lower().split())
    assert "assigned" in rules and "primary actor" in rules
    style = " ".join((PROMPTS_DIR / "editorial" / "style.md").read_text(encoding="utf-8").lower().split())
    assert "assigned" in style and "primary actor" in style


def test_language_anchors_are_deferred_to_editorial_rules():
    # The per-language anchors (banned lexicon, orthography, slop phrases, attribution and
    # disclaimer wording) moved to the DB editorial catalog; each node states the language-
    # agnostic RULE and points at `editorial-rules` for the concrete list, so a non-Spanish
    # author gets its own anchors. Pin both halves: the pointer is present, and the old inline
    # Spanish exemplars are gone from every workflow node.
    assert "editorial-rules" in _flat("15-pick-author.md"), \
        "15-pick-author must tell the walk to load the language anchors"
    assert "editorial-rules" in _flat("10-institucional.md"), \
        "the institucional lane (no 15-pick-author) must load the anchors itself"
    for node in ("50-draft.md", "50-draft-institucional.md", "60-evaluate.md",
                 "62-semantica-calidad.md", "85-accents-entities.md", "70-respin.md",
                 "75-factcheck.md", "90-finalize.md"):
        assert "editorial-rules" in _flat(node), f"{node} must defer to the editorial-rules anchors"
    # The old inline Spanish exemplars now live in the DB, not the prompts. (This scans every
    # node; the redaccion persona JSON in 10-institucional is legitimate Spanish DATA and carries
    # none of these phrases.)
    # Every category of removed exemplar: candor tics, slop phrases, the negation-inversion
    # pattern, plain-text attribution wording, banned lexicon, lexicon-swap sources, the satire
    # disclaimer, and the Spanish gerund suffixes. (All Spanish-specific; none appears in the
    # legit redaccion persona JSON in 10-institucional, which is checked to stay clean.)
    dead = ("la verdad es que", "seamos honestos", "hay que decirlo",
            "en el mundo actual", "es importante notar", "no es x, es y",
            "según x", "según la municipalidad", "según el comunicado",
            "demoledor", "escandaloso", "sin precedentes", "fulminó", "castigó",
            "cuentillo de ciencia", "-ando", "-iendo")
    for node_path in sorted((PROMPTS_DIR / "workflow").glob("*.md")):
        low = " ".join(node_path.read_text(encoding="utf-8").lower().split())
        for phrase in dead:
            assert phrase not in low, \
                f"{node_path.name} still hardcodes the Spanish exemplar {phrase!r}; move it to editorial-rules"


def test_loop_nodes_carry_global_spanish_format_rules():
    # The Spanish editorial format rules live in the shared workflow loop (they apply to every
    # author), not in any per-persona prompt: politics is written in the third person, gerunds
    # are minimized, titles are objective, and the bajada is bounded.
    draft = _flat("50-draft.md")
    assert "third person" in draft and "politics" in draft
    assert "gerund" in draft
    ev = _flat("60-evaluate.md")
    assert "third person" in ev and "gerund" in ev
    enrich = _flat("80-enrich.md")
    assert "gerund" in enrich
    fin = _flat("90-finalize.md")
    assert "objective" in fin and "20 and 30 words" in fin


def test_finalize_titles_are_objective_and_bajada_is_bounded():
    # Titles are objective and informative and word-capped (max 5 words); the piece carries a
    # title and a single bajada of 20 to 30 words, and no separate subtitle.
    low = _flat("90-finalize.md")
    assert "5 words" in low
    assert "objective" in low
    assert "three authored layers" in low
    assert "bajada" in low
    assert "20 and 30 words" in low
    assert "subtitle" in low  # only to forbid it ("do not write a separate subtitle")


def test_draft_node_demands_entertaining_varied_texture():
    # The draft node pushes an entertaining, alive read that breaks monotony with the
    # renderer's real devices (blockquote pull-quote, list, mid-body image), not a flat wall.
    low = _flat("50-draft.md")
    assert "entertain" in low
    assert "monotony" in low
    assert "blockquote" in low


def test_enrich_node_adds_a_monotony_check():
    # The plain enrich pass also breaks up a flat wall (adding devices drawn from existing
    # text, no new facts), so a dull-shaped piece does not slip through.
    low = _flat("80-enrich.md")
    assert "monotony" in low


def test_publish_and_batch_forbid_running_generate_and_tests():
    # The publish node and the daily-batch skill both tell the driver NOT to run the generate
    # one-shot or the test suite during a walk; the generate watcher rebuilds on its own and
    # the printed preview link is the verification step.
    pub = _flat("99-preview.md")
    assert "watcher rebuilds" in pub
    assert "test suite" in pub
    skill = " ".join((PROMPTS_DIR.parent / "cli" / "skills" / "daily-batch" / "SKILL.md").read_text().lower().split())
    assert "never run" in skill and "test suite" in skill


def test_portal_review_carries_the_day_loader_and_arrange_rules():
    # The standalone arrange walk must name its day loader and the two layout rules the roadmap
    # spec added on top of the existing lead + media/text checkerboard.
    low = _flat("portal-review.md")
    # Step 2 loads the whole day (all authors) in one read.
    assert "archive --day" in low
    # The no-gap rule: a lone trailing piece is promoted to a full single row (role important).
    assert "never leave a gap" in low
    assert "important" in low
    # The editor's vocabulary is mapped: "double card" = role important, and the lead is the
    # first entry by POSITION, never marked important (the write example must not either).
    assert "double card" in low
    assert "by position" in low
    assert '{"slug":"the-lead","role":""}' in low
    # Content-first pairing overrides the media checkerboard for deliberately related pieces.
    assert "content first" in low
    assert "checkerboard" in low
    # The write shape stays.
    assert "--set-json" in low


def test_portada_skill_maps_the_layout_vocabulary():
    # An agent reading ONLY the portada skill must get the layout right: the lead is the
    # first entry by position (distinct from a double card), "double card"/"double column"
    # means role important (a full row), singles pair up two per row in index order, and a
    # typo'd slug is silently dropped at render (it does not put an empty card on the page).
    skill = " ".join(
        (PROMPTS_DIR.parent / "cli" / "skills" / "portada" / "SKILL.md").read_text().lower().split()
    )
    assert "double card" in skill
    assert "double column" in skill
    assert "by position" in skill
    assert '"important"' in skill
    assert "index order" in skill
    assert "never leave a gap" in skill
    assert "dropped" in skill and "empty card" in skill


def test_batch_plan_forward_points_to_the_portada_arrange():
    # The batch-plan node closes by pointing the sweep's last move at the portada arrange walk,
    # so a driver knows to arrange each day after the queued articles publish.
    low = _flat("10-batch-plan.md")
    assert "step --mode portal-review" in low
    assert "archive --day" in low


def test_topic_cleanse_walk_covers_both_halves_and_agent_detection():
    # The topic-normalization walk must drive BOTH merge halves and state that detection is
    # the operator's (no inference backend), so a driver does not wait on a model that never runs.
    low = _flat("topic-cleanse.md")
    # Article half: the hash-safe brain cleanse writer, dry-run then apply.
    assert "topics cleanse" in low
    assert "--map-file" in low and "--apply" in low
    # Author half: the separate profile-chip write.
    assert "profile-topics" in low
    # Uses the phase-3 inventory verb; cross-references (does not duplicate) normalize-topics.
    assert "cli/censurado.py topics" in low
    assert "normalize-topics" in low
    # Registry reconcile is automated with the remove-topic verb (v1 default), not manual-only.
    assert "remove-topic" in low
    # Agent-side detection + the safety rails: dry-run gate, over-merge hazard, hash stability.
    assert "no model runs" in low
    assert "dry run" in low or "dry-run" in low
    assert "over-merg" in low
    assert "hash" in low


def test_institucional_mode_is_a_lighter_unsigned_lane():
    # The institutional lane is a NEW mode: a lighter walk than the full persona single-article,
    # for unsigned/official pieces. It drops the persona-voice load, the outline, the respin loop,
    # the accents-entities pass, and the AI image step, and reuses research/finalize/preview/publicar.
    manifest = json.loads((PROMPTS_DIR / "workflow" / "manifest.json").read_text())
    modes = manifest["modes"]
    assert "institucional" in modes, "the institucional mode is missing from the manifest"
    inst = modes["institucional"]
    single = modes["single-article"]
    # Fewer nodes than the full persona walk (the whole point: less pass).
    assert len(inst) < len(single)
    # No AI image generation and no respin loop (plus no outline / accents-entities pass).
    for dropped in ("95-image", "70-respin", "40-outline", "85-accents-entities", "60-evaluate"):
        assert dropped not in inst, f"institucional must not include {dropped}"
    # The lighter lane's own front nodes, then the reused tail.
    assert inst[:4] == ["10-institucional", "30-research", "50-draft-institucional", "62-semantica-calidad"]
    for reused in ("30-research", "90-finalize", "99-preview", "publicar"):
        assert reused in inst
    # publicar is gated inside the mode, mirroring the other article modes.
    assert "publicar" in (manifest.get("disabled") or {}).get("institucional", [])


def test_institucional_first_node_picks_the_house_byline_and_provided_image():
    # The lane's first node signs with the reserved institutional byline (redaccion / "Redacción"),
    # creates it once if missing, and confirms the PROVIDED image instead of an AI hero.
    low = _flat("10-institucional.md")
    assert "redaccion" in low and "redacción" in low   # the reserved handle + its display name
    assert "create-author" in low                        # created once if absent
    assert "provided" in low                             # a supplied /media image, not generated
    assert "never runs" in low and "comfyui" in low      # this lane skips the ComfyUI image verb
    assert "--image-caption" in low and "--image-credit" in low  # optional site-rendered caption/credit


def test_institucional_draft_enforces_the_formal_register():
    # The institutional draft node is the fallback to the MOST FORMAL voice: third person,
    # objective, minimal gerunds, provided image, and no persona-voice load.
    low = _flat("50-draft-institucional.md")
    assert "third person" in low
    assert "objective" in low
    assert "formal" in low
    assert "institutional" in low
    assert "gerund" in low
    assert "provided" in low                             # provided image, never generated
    assert "say each idea once" in low                   # the anti-slop discipline survives
    # It strips the persona-voice load: it is NOT the signed draft node.
    assert "persona's language and voice" not in low


def test_semantica_calidad_is_the_single_combined_pass():
    # The 62 node is the ONE combined revision pass that replaces evaluate + respin + enrich for
    # the lighter lane: fix the semantics, improve the quality.
    low = _flat("62-semantica-calidad.md")
    assert "single combined pass" in low
    assert "fix the semantics" in low
    assert "improve the quality" in low
    assert "clarity" in low
    assert "accuracy against the sources" in low
    assert "no redundancy" in low
    assert "gate" in low                                 # still the publish gate
    # It explicitly stands in for the three heavier passes.
    for replaced in ("evaluate", "respin", "enrich"):
        assert replaced in low


def test_shared_surface_defaults_to_objective_reporting():
    # De-opinionation: objective, third-person reporting is the GLOBAL default across the shared
    # (non-persona) prompt surface. First-person opinion is a per-persona exception (a persona
    # whose profile is explicitly an opinion, satire, or fiction voice); the author's lean shows
    # ONLY in which sources and facts they foreground, never in editorializing prose.
    def objective(x):
        return "objective" in x and ("third person" in x or "third-person" in x)
    for node in ("15-pick-author.md", "50-draft.md", "60-evaluate.md"):
        assert objective(_flat(node)), f"{node} must carry the objective third-person default"
    # The outline plans the summary layer objectively, not "from your point of view".
    assert "objectively" in _flat("40-outline.md")
    # The draft node frames first person / opinion as a RESERVED per-persona exception, tells the
    # writer not to editorialize, and puts the lean in source selection.
    draft = _flat("50-draft.md")
    assert "reserved for a persona" in draft
    assert "opinion, satire, or fiction" in draft
    assert "do not editorialize" in draft
    assert "which sources and facts" in draft
    # The reference docs (voice guide + one-article contract) were inverted the same way.
    style = " ".join((PROMPTS_DIR / "editorial" / "style.md").read_text(encoding="utf-8").lower().split())
    assert "objectively and in the third person" in style
    rules = " ".join((PROMPTS_DIR.parent / "EDITORIAL-RULES.md").read_text(encoding="utf-8").lower().split())
    assert "voice and objectivity" in rules


def test_global_opinion_mandates_are_gone_from_the_shared_surface():
    # The old newsroom-wide opinion mandates ("opinionated and magnetic", "out loud and convinced",
    # "take a clear position", "argues the side", "with stance") must NOT survive on the shared
    # surface; opinion now lives per-persona, not as a global default.
    gone = {
        "40-outline.md": ("opinionated and magnetic", "where you stand", "from your point"),
        "50-draft.md": ("out loud and convinced", "defending their politics", "take a clear position"),
    }
    for node, phrases in gone.items():
        low = _flat(node)
        for p in phrases:
            assert p not in low, f"{node} still carries the removed global-opinion phrase {p!r}"
    style = " ".join((PROMPTS_DIR / "editorial" / "style.md").read_text(encoding="utf-8").lower().split())
    assert "argues the side their persona declares" not in style
    assert "take a side in the framing and the argument" not in style
    rules = " ".join((PROMPTS_DIR.parent / "EDITORIAL-RULES.md").read_text(encoding="utf-8").lower().split())
    assert "voice and stance" not in rules
    assert "with stance" not in rules


def test_redactor_mode_is_registered_as_a_single_planning_node():
    # The redactor assignment walk is a NEW mode: one planning node that sweeps and assigns, like
    # the daily/last-hour batch modes, and its node exists on disk.
    manifest = json.loads((PROMPTS_DIR / "workflow" / "manifest.json").read_text())
    modes = manifest["modes"]
    assert modes.get("redactor") == ["redactor"], "the redactor mode must be a single 'redactor' node"
    node = PROMPTS_DIR / "workflow" / "redactor.md"
    assert node.is_file() and node.read_text().strip(), "missing/empty redactor node"


def test_redactor_node_is_a_plain_websearch_sweep_that_assigns():
    # The redactor jefe reads every author, sweeps the FRESHEST news (last ~60 minutes) with PLAIN
    # web search and NO assigned feeds, ranks, assigns each story to the best-fit author, emits the
    # queue, and hands each item to its own single-article walk. It writes no article itself.
    low = _flat("redactor.md")
    assert "writes no article" in low
    assert "plain web search" in low
    assert "no assigned sources" in low          # the deliberate contrast with the daily feed sweep
    assert "60 minutes" in low                     # the critical freshness window
    assert "personas" in low                       # reads every author first
    assert "assign" in low and "queue" in low      # ranks and assigns into an assignment queue
    assert "step --mode single-article" in low     # each assignment becomes its own walk


def test_every_manifest_workflow_node_and_persona_synthesize_exist_on_disk():
    # The step gate serves nodes straight off disk, so every node the manifest references
    # (plus persona/synthesize, the author-synthesis prompt) must actually be present or a
    # walk hits a missing file. This is the filesystem twin of the old GET /prompts listing.
    manifest = json.loads((PROMPTS_DIR / "workflow" / "manifest.json").read_text())
    need = {"00-mode"} | {k for keys in (manifest.get("modes") or {}).values() for k in keys}
    assert len(need) >= 12, "the manifest lists suspiciously few workflow nodes"
    for key in sorted(need):
        node = PROMPTS_DIR / "workflow" / f"{key}.md"
        assert node.is_file() and node.read_text().strip(), f"missing/empty workflow node {key}.md"
    synth = PROMPTS_DIR / "persona" / "synthesize.md"
    assert synth.is_file() and synth.read_text().strip(), "missing persona/synthesize.md"
