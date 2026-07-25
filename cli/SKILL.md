---
name: censurado
description: >-
  Operate a running Censurado AI news portal entirely by tool-calling the cli/censurado.py
  verbs: write and publish news articles in a fictional persona's voice through a gated
  editorial walk, run the daily or weekly news sweep, create/edit/remove authors and manage
  each author's source outlets, reorder the front page (portada), art-direct and attach hero
  images, edit live articles, tune the editorial prompts, and publish the site to production (publicar). Use whenever the user wants to write,
  publish, update, or manage content on their Censurado news site. Never read or edit the
  codebase; every action is a censurado.py verb.
---

# Censurado control skill (resolver)

Run a live Censurado news portal by calling ONE small CLI: `python3 cli/censurado.py <verb>`.
Do not read source code and do not hand-build HTTP requests, every operation is a verb. This
file is the DISPATCHER: match the request to a row below, open the sub-skill it names (when
there is one), then run the verbs. If two rows match, read both. Run any verb with `--help`
for its exact flags.

## The hard boundary: you OPERATE this site, you do not build it (read first)
You are the operator of a running news portal, not a developer of it. Your ENTIRE surface is
`python3 cli/censurado.py <verb>`. There is a verb for every legitimate action (run `python3
cli/censurado.py --help` to see them all).

You DO write the walk's own scratch artifacts, and you must: when a `step` prints a `WORK DIR:`
and an `ARTIFACT:` (or "save ... to ...") line naming a file such as `ledger.md` or `draft.md`,
save that file to `$CENSURADO_WORK` at the exact path it names, or the gate will not let you
advance. That is expected and required; it is not "editing the repo". The boundary below is
about the REPO's own source files, never your scratch dir.

You must NEVER:
- edit, create, or delete any file in this repo or any sibling repo (its SOURCE CODE). The only
  files you ever write are the walk's scratch artifacts under `$CENSURADO_WORK` described just
  above; nothing else on disk is yours to touch.
- read, `grep`, `cat`, or `sed` the source code, this repo's or the sibling `../censurado-web*`
  repos. You never need the code to operate the site.
- run ANY shell command that is not `python3 cli/censurado.py ...`: no `make`, no `./run.sh`,
  no `./deploy/*.sh`, no `docker`, no `chmod` / `mkdir` / `sed` / `grep`, no `sqlite3` or any
  other direct database access, no `pytest` / `make test`. Starting the stack is ALSO a verb
  now (`up` / `up-gpu`), so even "the site is down" never justifies a raw script or docker.
  The ONE other sanctioned binary is `censurado-brain ...` (the maintenance CLI), and only
  when the walk node you are on names the exact command (the topic-cleanse walk does); never
  improvise other `censurado-brain` calls.
- regenerate or publish to "make a change appear" or "to check" it: the local site repaints
  itself within a few seconds, and you confirm with `status`, never by rebuilding.
- spawn subagents or write a script to orchestrate the work. You do the walk yourself, in
  order, one piece at a time (a big batch is done in small chunks, not by a fleet of agents).

The content data lives in the backend and you reach it ONLY through verbs (`personas`,
`persona`, `archive`, `get`, `sources`, ...). The prompts are files but you read and change
them ONLY through `prompt` / `set-prompt`. Going public is itself a verb now (`publicar --yes`),
so even the one infra action needs no `.sh` and no `make`.

### When something seems to need a non-verb action, STOP. Do not improvise.
If you catch yourself about to edit a file, run a script, read the source, query the database,
or rerun generate/publicar in a loop, you are already off the rails. Instead:
1. Run `python3 cli/censurado.py status` to see what is actually online (backend, local site,
   ComfyUI, the public site). It answers "is the stack up" and "did my publish land" without any
   shell. To confirm a SPECIFIC piece, open the `PREVIEW:` link that `preview` already printed;
   that link IS the confirmation, no extra verb and no re-checking loop needed.
2. If a verb failed, relay its exact `ERROR:` / `FATAL:` line to the human and ask how to
   proceed. A missing capability or a broken build is the human's call, never a shell
   workaround, a source dive, or a chmod. Looping on `generate` / `publicar` / `chmod` while an
   article "won't appear" is the exact failure this rule exists to stop.

## Always, on every task
- **Preflight.** The stack must be up. Check with a verb, not a raw `curl`:
  `python3 cli/censurado.py status` prints an [OK]/[WARN]/[FAIL] liveness report over the
  backend, the local site, ComfyUI (optional, images only), and the public site, and exits 0
  when the core is serving. `python3 cli/censurado.py doctor` is the deeper preflight (it also
  checks the skill package and the on-disk recipe). If the core is down, bring it up YOURSELF
  with the verb: `python3 cli/censurado.py up` (no GPU needed; waits until it serves). Use
  `python3 cli/censurado.py up-gpu` INSTEAD only when the task renders NEW hero images (it adds
  ComfyUI; heavy, GPU box only). Run `up` at most ONCE: if it fails, relay its exact error line
  to the human and STOP (never fall back to `docker`, `./run.sh`, or `make`). The workflow and
  persona prompts are on-disk files in this repo's `prompts/` (no server to start).
- **Auth is automatic.** `censurado.py` reads the operator token from `.env`. Never print,
  invent, or pass a token.
- **Never confuse `preview` with `publicar`.** They are different verbs for different worlds:
  - `preview` = stage to the LOCAL site at `localhost:8080`. Debug-only, only this machine sees
    it, it NEVER reaches the internet. Use it freely to let the user SEE a piece.
  - `publicar --yes` = DEPLOY to the real public site (elcensuradoweb.com). Irreversible and
    outward-facing; this and ONLY this is what "publicar" / "publish" / "deploy" / "go live"
    mean. Always show the draft and get a yes first, unless told to run unattended.
- **Compliance.** The site is openly AI-generated with fictional personas: keep the footer
  "Aviso editorial", mark opinion/satire, and never impersonate real people.

## Route the request
| The user wants to ... | Do this |
|---|---|
| check the stack is up / "did my publish land?" / "you online?" | `python3 cli/censurado.py status` |
| the stack is down / start the portal / spin it up ("levanta el sitio") | `python3 cli/censurado.py up` (LOCAL core stack, no GPU; waits until it serves) |
| start the stack WITH the image lane (ComfyUI, for NEW hero images) | `python3 cli/censurado.py up-gpu` (heavy, GPU box only) |
| verify a specific piece is live / "where is my article?" | open the `PREVIEW:` link `preview` printed for it (that link is the confirmation; the site repaints within a few seconds, do not re-check in a loop) |
| write / create / cover a news article ("nota", "write up this news") | read `cli/skills/write-article/SKILL.md`, then walk it |
| write an institutional / unsigned piece (an official release or "gacetilla", no persona byline) | read `cli/skills/write-article/SKILL.md`, then walk the lighter formal lane: `python3 cli/censurado.py step --mode institucional` (signed by "Redacción", provided image, no AI hero) |
| search the web, find real sources, read a page | read `cli/skills/websearch/SKILL.md` |
| run the daily / weekly batch, sweep the day, refresh the portal | read `cli/skills/daily-batch/SKILL.md` |
| assign the freshest news to authors ("who covers what right now", triage the last-hour news, the editor-in-chief sweep) | read `cli/skills/redactor/SKILL.md`, then `python3 cli/censurado.py step --mode redactor` |
| add / remove / inspect an author (create a writer, retire one, read a voice) | read `cli/skills/authors/SKILL.md` |
| change an existing author (rewrite the voice/bio, swap the profile picture, move the beat) | read `cli/skills/authors/SKILL.md`, then `python3 cli/censurado.py edit-author <id> ...` |
| change which outlets an author reads, or the sourcing floor (how many sources per article) | read `cli/skills/sources/SKILL.md` |
| reorder the front page, change the day's lead, make a card double / full row / single (portada) | read `cli/skills/portada/SKILL.md` |
| set the front-page "Recomendado" rail (a global fixed list of up to 10, persists across days) | `python3 cli/censurado.py recomendado [--set "slug-a,slug-b,..."] [--clear]` (see `cli/skills/portada/SKILL.md`) |
| art-direct and attach a hero image, or upload media | read `cli/skills/media/SKILL.md` |
| list authors / read one author's voice and beat (quick) | `python3 cli/censurado.py personas` ; `python3 cli/censurado.py persona <id>` |
| show / set an author's public profile topics | `python3 cli/censurado.py profile-topics <id> [--set ...]` |
| curate one author's short profile-topic list (guided) | `python3 cli/censurado.py step --mode normalize-topics` |
| merge topic-tag variants of one entity across the whole corpus (e.g. `Javier-Milei` into `milei`), on articles and author chips | `python3 cli/censurado.py step --mode topic-cleanse` (a gated walk) |
| see the tag surface / inventory distinct article tags with counts | `python3 cli/censurado.py topics` |
| consult the LIVE section vocabulary / "what sections do we actually have?" (distinct section values on live articles, with counts; also `--authors`/`--topics`) | `python3 cli/censurado.py sections` |
| drop a stale topic from the `/topics` index (reconcile after a merge; soft, restorable) | `python3 cli/censurado.py remove-topic <slug> --yes` |
| edit an article already on the preview site | `python3 cli/censurado.py edit <slug> --meta k=v --body-file ...` |
| take an article down / remove it from the site (soft, restorable) | `python3 cli/censurado.py unpublish <slug> --yes` |
| read the editorial voice/rules or a prompt | `python3 cli/censurado.py style` (voice/house rules) ; `python3 cli/censurado.py editorial-rules <lang>` (a language's banned lexicon, orthography, slop phrases, and attribution/disclaimer anchors, from the DB) ; `python3 cli/censurado.py prompt <key>` |
| read or change the enforced numeric bar (how many sources, tag cap) | read `cli/skills/sources/SKILL.md`; the floor/cap live in `cli/workflow/parameters.json` (`set-floor`, `step`), NOT in `style` |
| change how the newsroom writes: a workflow WALK node or a library prompt (dev) | read `cli/skills/prompts/SKILL.md` |
| localize / translate the whole site into a new language (one-time, per language) | read `cli/skills/translate/SKILL.md`, then `python3 cli/censurado.py translate <lang>` |
| go live / publish to production (public internet) | read `cli/skills/publicar/SKILL.md`, then `python3 cli/censurado.py publicar --yes` |

## The one rule for writing
Writing an article is a GATED WALK served ONE step at a time, never one shot. Always go
through `cli/skills/write-article/SKILL.md`: it walks `censurado.py step` node by node so the
sourcing floor, the accurate-headline gate, and the evaluate/respin loop all fire. Never draft
a whole piece from memory and never skip a gate.
