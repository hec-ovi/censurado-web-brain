---
name: prompts
description: >-
  Change how the newsroom writes by editing its editorial PROMPTS (dev-facing): the workflow
  WALK nodes the step gate serves and the library prompts like the author-synthesis guide.
  Every prompt is a plain .md/.json file the CLI reads and writes directly from this repo's
  prompts/ dir (git is the history, never a database and no server), so a read returns the
  file and an edit rewrites it in place. Use to reshape the
  drafting/evaluation instructions or a library prompt, NOT to change an author's voice (that is
  the authors sub-skill) or a numeric floor (that is the sources one).
---

# Edit the editorial prompts (dev)

The newsroom keeps its instructions as PROMPTS, and every prompt is a FILE. There is no database
copy, no version store, and no server: the CLI reads each prompt directly from this repo's
on-disk library (`prompts/`, resolved from `CENSURADO_PROMPTS_DIR`) and git is the
history. So editing a prompt is editing a file in place, and you commit it in this repo.

Two kinds of prompt, edited the exact same way:
- **Workflow WALK nodes** (`workflow/<node>.md`, e.g. `workflow/50-draft.md`, plus
  `workflow/manifest.json`): the step-by-step instructions the `step` gate hands the agent. An
  edit changes what every future walk is told at that step.
- **Library prompts** (e.g. `persona/synthesize.md`): reusable helper prompts, like the
  author-synthesis guide the `authors` sub-skill reads.

## Read a prompt
    python3 cli/censurado.py prompt workflow/50-draft.md      # a walk node
    python3 cli/censurado.py prompt persona/synthesize.md     # a library prompt

List the walk node keys for a mode with `python3 cli/censurado.py step --list` (or
`step --list --mode single-article`). The prompts are on-disk files in this repo's `prompts/`, so
reading or editing them needs no server, just this repo (set `CENSURADO_PROMPTS_DIR` if it lives
elsewhere).

## Edit a prompt (any key, in place)
    python3 cli/censurado.py set-prompt workflow/50-draft.md --body-file new-50.md
    python3 cli/censurado.py set-prompt persona/synthesize.md --body-file new-synth.md

`set-prompt` writes the `.md`/`.json` file in place, so the very next read or `step` walk serves your
edit. There are no versions, no staging, and no promote: to change a prompt you rewrite its file,
and to undo you edit it back (or `git checkout` it). Because these are tracked files in this
repo, COMMIT the change here. `--body` takes short text inline; use `--body-file` for anything
real. Write the prompt as long as it needs, there is no length limit. Adding a brand-new prompt is
a code change (create the file, and for a workflow node a manifest entry too), not an API edit.

## What is NOT a prompt (do not reach here for these)
- **An author's voice** (`who_i_am`, `style`, `few_shots_*`): that is author DATA in the backend,
  edited by re-running `create-author` (the `authors` sub-skill). `persona/synthesize.md` is only
  the GUIDE for writing those fields, a different thing from the fields themselves.
- **The numeric floor and caps**: `MIN_SOURCES` and `MIN_PER_TYPE` are set with `set-floor`;
  `TOPIC_CAP` and `RESPIN_PASSES` have no setter flag, so hand-edit `cli/workflow/parameters.json`.
  Read the ENFORCED values with `step` (or inspect the file), NOT with `style` (which shows the
  editorial style guide's separate numbers that can diverge). See the `sources` sub-skill.

Editing a workflow node changes live editorial behavior for the next walk, so make the change
deliberately and commit it.
