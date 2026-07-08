---
name: redactor
description: >-
  Act as editor-in-chief: scan the freshest news across the whole landscape with plain web
  search (no assigned feeds), rank what broke in roughly the last 60 minutes, assign each story
  to the author whose beat fits, and emit an assignment queue. Use when the user asks who should
  cover what right now, wants the latest-hour news triaged, or wants stories assigned to authors.
---

# The redactor jefe (assign the freshest news)

This is the assignment desk, not the writing desk. You read the whole newsroom, sweep the
freshest news with plain web search, and hand each story to the author whose beat fits. You do
NOT write any article here; each assignment is written afterward as its own gated walk.

## How it differs from the daily sweep
The daily-batch sweep pulls each author's OWN outlets (`sources`, `portals`) for minute-fresh
feeds, scoped to their beats. The redactor does the opposite: a source-agnostic PLAIN web-search
sweep of the whole landscape, biased to the last ~60 minutes, so you see every fresh story before
you decide who covers it. Reach for this when the ask is "what just broke and who takes it",
reach for `daily-batch` when the ask is "run the daily/weekly sweep of our beats".

## Run it through the gate
The redactor is a gated walk with one planning node. Start it:

    python3 cli/censurado.py step --mode redactor

The `redactor` node reads every author (`personas`, then `persona <id>`), runs recency-tuned web
searches for what broke in the last hour, ranks by importance and freshness, clusters re-reports,
dedups against the archive, and assigns each story to the best-fit author. It emits the
assignment queue and stops. Then write EACH queued item as its own full `single-article` walk via
the `write-article` sub-skill: `python3 cli/censurado.py step --mode single-article`, node by
node, through to `preview`. Every piece still passes its own sourcing floor, accurate-headline
gate, and evaluate/respin loop.

## Assign on fit, not on slant
Match each story to the author whose beat and profile topics are most central to it. An author's
leaning belongs later, only in which sources they choose to cite; it never decides whether they
are handed a story. Give the human the queue first if they are watching, so they can drop or
reorder before anything is written.

## Do it yourself, serially. No subagents, no scripts.
Walk the queue ONE piece at a time, yourself: finish a piece through `preview` before you start
the next. Do NOT spawn subagents and do NOT write a shell script to "parallelize" the batch; that
path causes duplicate slugs and lost pieces. If the queue is large (more than about four or five
pieces), do it in chunks: finish a chunk, hand the human the `PREVIEW:` links, and continue.

## After the sweep
Verifying a piece is the `PREVIEW:` link the `preview` step printed, not a rebuild or a re-check;
the generate watcher repaints the local site on its own within a couple of seconds. Once every
queued item is published, arrange each UTC day the batch touched with the portada walk, one day
at a time, loading the day with `archive --day <YYYY-MM-DD>`:

    python3 cli/censurado.py step --mode portal-review

Going live stays a separate, human-gated verb: `python3 cli/censurado.py publicar --yes`.
