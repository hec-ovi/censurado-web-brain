---
name: censurado
description: >-
  Operate a running Censurado AI news portal entirely by tool-calling the cli/censurado.py
  verbs: write and publish news articles in a fictional persona's voice through a gated
  editorial walk, run the daily or weekly news sweep, create/edit/remove authors and manage
  each author's source outlets, reorder the front page (portada), art-direct and attach hero
  images, edit live articles, tune the editorial prompts, and deploy the site. Use whenever the user wants to write,
  publish, update, or manage content on their Censurado news site. Never read or edit the
  codebase; every action is a censurado.py verb.
---

# Censurado control skill (resolver)

Run a live Censurado news portal by calling ONE small CLI: `python3 cli/censurado.py <verb>`.
Do not read source code and do not hand-build HTTP requests, every operation is a verb. This
file is the DISPATCHER: match the request to a row below, open the sub-skill it names (when
there is one), then run the verbs. If two rows match, read both. Run any verb with `--help`
for its exact flags.

## Always, on every task
- **Preflight.** The stack must be up: `curl -s http://127.0.0.1:8082/healthz` returns `ok`
  (that is the backend, which owns all content data and serves the panel). If not, ask the
  user to start it (from the repo root, no GPU needed):
  `docker compose up -d publish generate site`. ComfyUI (images) is optional. The workflow
  and persona prompts are on-disk files in this repo's `prompts/` (no server to start). One
  command self-checks all of this at once: `python3 cli/censurado.py doctor` prints an
  [OK]/[WARN]/[FAIL] report over the stack, the skill package, and the on-disk recipe.
- **Auth is automatic.** `censurado.py` reads the operator token from `.env`. Never print,
  invent, or pass a token.
- **Preview is local, deploy is public.** `censurado.py preview` stages an article to the
  LOCAL preview site (`localhost:8080`), NOT the public internet, so use it freely to let the
  user SEE the piece. Going public is a separate `make deploy` (production). Always show the
  draft and get a yes before you `deploy`, unless told to run unattended.
- **Compliance.** The site is openly AI-generated with fictional personas: keep the footer
  "Aviso editorial", mark opinion/satire, and never impersonate real people.

## Route the request
| The user wants to ... | Do this |
|---|---|
| write / create / cover a news article ("nota", "write up this news") | read `cli/skills/write-article/SKILL.md`, then walk it |
| search the web, find real sources, read a page | read `cli/skills/websearch/SKILL.md` |
| run the daily / weekly batch, sweep the day, refresh the portal | read `cli/skills/daily-batch/SKILL.md` |
| add / remove / inspect an author (create a writer, retire one, read a voice) | read `cli/skills/authors/SKILL.md` |
| change which outlets an author reads, or the sourcing floor (how many sources per article) | read `cli/skills/sources/SKILL.md` |
| reorder the front page, feature/unfeature a story (portada) | read `cli/skills/portada/SKILL.md` |
| art-direct and attach a hero image, or upload media | read `cli/skills/media/SKILL.md` |
| list authors / read one author's voice and beat (quick) | `python3 cli/censurado.py personas` ; `python3 cli/censurado.py persona <id>` |
| show / set an author's public profile topics | `python3 cli/censurado.py profile-topics <id> [--set ...]` |
| edit an article already on the preview site | `python3 cli/censurado.py edit <slug> --meta k=v --body-file ...` |
| take an article down / remove it from the site (soft, restorable) | `python3 cli/censurado.py unpublish <slug> --yes` |
| read the editorial voice/rules or a prompt | `python3 cli/censurado.py style` (voice/lexicon/rules) ; `python3 cli/censurado.py prompt <key>` |
| read or change the enforced numeric bar (how many sources, tag cap) | read `cli/skills/sources/SKILL.md`; the floor/cap live in `cli/workflow/parameters.json` (`set-floor`, `step`), NOT in `style` |
| change how the newsroom writes: a workflow WALK node or a library prompt (dev) | read `cli/skills/prompts/SKILL.md` |
| go live / publish to production (public internet) | read `cli/skills/deploy/SKILL.md` |

## The one rule for writing
Writing an article is a GATED WALK served ONE step at a time, never one shot. Always go
through `cli/skills/write-article/SKILL.md`: it walks `censurado.py step` node by node so the
sourcing floor, the honest-headline gate, and the evaluate/respin loop all fire. Never draft
a whole piece from memory and never skip a gate.
