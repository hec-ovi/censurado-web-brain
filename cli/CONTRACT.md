# Seam C: the agent-facing contract

This repo is driven by an agent that tool-calls the CLI off `cli/SKILL.md` alone, never by
reading code. The target driver is a lean skill-loading agent harness on a small model (for
example Pi, `earendil-works/pi`, driving a local Gemma model; see `../pi-gemma/`), or a phone
agent over MCP. So the surface it sees has to be stable: the set of verbs, the routes that name
those verbs, the recipe files the verbs read, and the workflow gate's knobs. That surface is the
contract frozen here.

There is no compiler to catch a break. If a verb is renamed, or a sub-skill points the driver at
a verb or a recipe file that no longer exists, the agent dead-ends at runtime with a soft error.
So the freeze is enforced by tests: `tests/test_cli_contract.py` (this seam), plus
`tests/test_skills.py` and `tests/test_resolver_skills.py` (the resolver and sub-skill files),
`tests/test_censurado_cli.py` (verb behavior + the step gate), and `tests/test_prompt_content.py`
(the node prompt text and manifest coverage).

**The rule (same as the backend's Seam A/B): to change a verb, a routed reference, a recipe file
binding, or a knob, edit this file AND the frozen set in `tests/test_cli_contract.py` in the SAME
commit.** A change that touches only one of the two fails the suite.

## Entrypoints

- `python3 cli/censurado.py <verb>` is the authoring CLI: stdlib only, run as a script (there is
  no installed console entry for it). It reads content over HTTP from the backend
  (127.0.0.1:8082) with the operator bearer from `.env`, and reads the on-disk recipe under
  `prompts/`.
- `censurado-brain <sweep>` is the installed maintenance CLI (`newsroom.cli:main`, needs httpx +
  pydantic). Verbs: `status`, `topics cleanse [--map-file P] [--apply]`, `embeds recheck [--apply]`.

Run any verb with `--help` for its flags. `python3 cli/censurado.py doctor` self-checks the whole
surface (stack, skill package, on-disk recipe) and exits non-zero if anything is off.

## Frozen verbs (23)

`preview` and `publish` are the same handler (either name works). All are backend-HTTP except the
recipe/local-file and capture verbs noted.

| Verb | What it does |
|---|---|
| `get <slug>` | Read one article as JSON. |
| `archive <author>` | List an author's articles (light; for the repeat-news sweep). |
| `preview` / `publish` | Stage an article to the LOCAL preview site (POST /articles). Not public. |
| `edit <slug>` | Read-modify-write an existing article in place. |
| `unpublish <slug> --yes` | Soft-delete (tombstone) an article; it leaves the site on regen, restorable. |
| `media <file>` | Upload an image/video, print its URL. |
| `image` | Render an art-directed FLUX.2 hero through ComfyUI and attach it (best-effort). |
| `tweet <ref>` | Capture an X post as a `{{tweet:id}}` snapshot (keyless, fxtwitter). |
| `truth <ref>` | Capture a Truth Social post as a `{{tweet:id}}` snapshot (keyless). |
| `personas` | List author ids. |
| `persona <id>` | One author's full record as JSON. |
| `create-author` | Persist an agent-authored persona JSON (POST /authors). |
| `remove-author <id> --yes` | Soft-delete (tombstone) an author; restorable. |
| `sources <id> [--set ...]` | Show or replace an author's attached source slugs. |
| `profile-topics <id> [--set ...]` | Show or replace an author's public profile topics. |
| `portada <date> [--set-json ...]` | Read or write the per-day front-page plan. |
| `portals` | List available source slugs to attach. |
| `prompt <key>` | Read a recipe prompt by key (local file). |
| `set-prompt <key>` | Overwrite an existing recipe prompt in place (local file). |
| `style` | Print the editorial style guide (`prompts/editorial/style.md`). |
| `set-floor` | Set MIN_SOURCES / MIN_PER_TYPE in `cli/workflow/parameters.json`. |
| `doctor` | Self-check the stack + skill package + recipe. |
| `step` | Serve ONE workflow node at a time (the gate); prints the NEXT command. |

## Routing (cli/SKILL.md)

The resolver is always loaded. It routes an intent to a sub-skill (`read cli/skills/<x>/SKILL.md`)
or straight to a verb. The nine sub-skills: `write-article`, `daily-batch`, `authors`, `sources`,
`portada`, `media`, `websearch`, `deploy`, `prompts`. Every verb a route or a sub-skill names must
be one of the 23 above; every sub-skill the resolver names must exist on disk with valid
agentskills.io frontmatter.

## The workflow gate (cli/censurado.py step)

Writing is a gated walk served one node at a time so a model cannot collapse the steps and skip
the editorial checks. `step` reads the node bodies and `manifest.json` from `prompts/workflow/`,
and fills the numeric placeholders from `cli/workflow/parameters.json`; no server is involved.
When the driver exports `$CENSURADO_WORK`, the gate enforces the walk (an artifact gate plus a
loop shield) instead of only describing it.

- Modes (from the manifest): `single-article`, `single-author`, `authors` (the full write walk),
  `daily` / `weekly` / `last-hour` (batch planners), and the standalone `deploy`,
  `normalize-topics`, `portal-review`.
- Knobs (in `parameters.json`, filled as `{{NAME}}` in nodes): `MIN_SOURCES`, `MIN_PER_TYPE`,
  `RESPIN_PASSES`, `TOPIC_CAP`. A node may reference only these as an ALL-CAPS placeholder;
  content markers (`{{tweet:...}}`, `{{relacionado:}}`, `{{video:}}`) pass through untouched.

## Recipe files the surface binds a verb to

Each must ship or the path dead-ends:

- `style` reads `prompts/editorial/style.md` at runtime (the qualitative voice/rules/lexicon
  guide; the enforced numbers live in `parameters.json`, not here). Absent, the verb fails soft.
- `create-author` points the agent at `prompts/persona/synthesize.md` (its docstring and the
  authors skill say `prompt persona/synthesize.md`). Absent, that fetch dead-ends.

## tools/

Standalone stdlib operator utilities, safe in the public repo (no persona text, sources, or keys).
`agent_tokens.py` does per-article token accounting from Claude Code subagent transcripts
(extract / record / summary / cost). Not part of the driven surface.
