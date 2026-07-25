---
name: authors
description: >-
  Create, inspect, and remove the personas that write Censurado articles: list authors, read
  one author's private voice and public bio, synthesize a new author from a seed brief, and
  delete one. Use when the user wants to add a writer, change who covers a beat, audit who
  writes for the site, or retire an author. Authors live in the backend (the single content store), never in code.
---

# Manage authors (the personas)

Every article is written AS one author, in the first person, in that author's voice. Authors
are the newsroom's DATA: they live in the backend (via these `censurado.py` verbs, which hit
the publish backend over HTTP), and these verbs are the whole surface. Never edit code to
change an author.

## Read the author list
- List author ids: `python3 cli/censurado.py personas`
- Read ONE author in full (voice + beat + outlets): `python3 cli/censurado.py persona <id>`

The record has two halves. PRIVATE (the drafting voice, never shown to readers): `who_i_am`,
`style`, `few_shots_pos`, `few_shots_neg`. PUBLIC (the byline + "Nosotros" page): `about`,
`display_name`, `avatar_path`. Plus `beat` (the author's default `section`), `language` (the
body language, Spanish for every current author), and `sources` (the outlets it reads, see the
`sources` sub-skill).

`beat` fills an article's `section` when `preview` gets no explicit `--section`, so it must be
the section URL SLUG the site files under (`politics`, `world`, `tech`, `literatura`,
`misterio-y-conspiracion`), NOT a Spanish display label: a label like "Política" would slugify
to a NEW `/section/politica/` page split off from the real `politics` one. There is no section
registry table, so the live vocabulary is whatever articles carry: check it with
`python3 cli/censurado.py sections` before setting a beat, and reuse an existing slug so the
author lands in the section that already exists.

## Create an author
An author is a single JSON object you write yourself. Do NOT invent the shape from memory:
read the current schema and write to it.

1. Read the synthesize guide, which defines every key and how to voice it:
   `python3 cli/censurado.py prompt persona/synthesize.md`
2. From the user's seed brief (who this writer is, their beat, their politics), produce the
   JSON with these keys: `display_name`, `beat`, `who_i_am`, `style` (all four REQUIRED), plus
   `language` (a short code like "es"/"en"; it defaults to "es" if omitted, but set it so the
   walk loads the right per-language editorial rules), and the optional `about`,
   `few_shots_pos`, `few_shots_neg`, `sources`, `avatar_path`. Write
   each field as long as it needs; there is no length limit on any of them. `who_i_am` and
   `about` are first person, in the author's own voice, not described from outside.
3. Persist it: `python3 cli/censurado.py create-author --file <persona.json>` (or pipe the
   JSON on stdin with `--file -`). It returns the stored record; a missing required field is
   rejected before any write.

The voice fields are load-bearing: `few_shots_pos`/`few_shots_neg` are the pairs that stop the
drafter collapsing into generic "helpful assistant" prose, so write real, beat-specific
exemplars, not placeholders.

## Edit an existing author
The whole record is patchable in place (nothing is code). `edit-author` changes only the
fields you name and re-sends the rest untouched, so you never blank a voice by forgetting it:

    python3 cli/censurado.py edit-author <id> --set about="..." --set style="..."
    python3 cli/censurado.py edit-author <id> --meta beat=economia --meta language=es
    python3 cli/censurado.py edit-author <id> --meta-json '{"few_shots_pos": [{"prompt": "...", "good": "..."}]}'
    python3 cli/censurado.py edit-author <id> --profile-topics "milei,fmi"

- `--set` takes the public fields: `name`, `bio`, `about`, `avatar`, `gender`, `style`
  (`about` fills the public bio too). `--meta` takes the private tail: `beat`, `who_i_am`,
  `language`. The few-shot arrays are objects, so they go through `--meta-json`.
- Change the PICTURE: upload it first (`media <file>`, or render one with `image`), then
  `edit-author <id> --set avatar=/media/<hash>.png` with the path it printed.
- Replace the outlets it reads: use the `sources` sub-skill (`sources <id> --set ...`).
- Profile topics also have their own verb (`profile-topics <id> --set a,b,c`); either works.
- Add `--dry-run` to see the row that would be written before writing it.

**After changing the name, the bio or the picture, run `sync-byline`.** Every article stores
its OWN copy of those three fields, taken when it was staged, so the author page shows the new
picture while every earlier piece still carries the old one:

    python3 cli/censurado.py sync-byline <id> --dry-run   # what would change
    python3 cli/censurado.py sync-byline <id>             # push the current byline onto them

It rewrites only the articles whose copy differs, leaves the article text alone, and does not
move anything in the front-page order (the permalink covers the title and body, not the byline).

## Remove an author (destructive)
`python3 cli/censurado.py remove-author <id> --yes`

It is not undoable through the API, so confirm with the user first, then pass `--yes`. Reponses:
`204` removed, `404` no such author, `409` the author is still referenced by a queued
assignment (finish or reassign that batch item first, the store refuses to orphan a run's
authorship). Removing an author does NOT delete the articles they already published.
