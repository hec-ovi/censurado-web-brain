---
name: daily-batch
description: >-
  Refresh the front page with several fresh, real news stories at once: sweep the day's
  headlines, cluster and assign them to authors, then write each as its own gated article.
  Use when the user asks to run the daily/weekly sweep, cover the day's news, or refresh the
  portal with multiple pieces.
---

# The daily sweep (a batch of real news)

A sweep is the single-article walk applied to several fresh stories at once. YOU fetch the
day's headlines, pick the leads, assign each to the author whose beat fits, and write them one
by one. No cloud model runs; you are the newsroom.

## Run it through the gate
The batch is itself a gated walk. Start it and let the first node plan the sweep:

    python3 cli/censurado.py step --mode daily

The `10-batch-plan` node sweeps trending news, clusters it, assigns each story to an author,
and emits the queue. Then write EACH queued item as its own full `single-article` walk via the
`write-article` sub-skill: `python3 cli/censurado.py step --mode single-article`, node by node,
through to `preview`. Do not shortcut a queued item; every piece still passes its own sourcing
floor, accurate-headline gate, and evaluate/respin loop.

## Do it yourself, serially. No subagents, no scripts.
Walk the queue ONE piece at a time, yourself: finish a piece through `preview` before you start
the next. Do NOT spawn subagents and do NOT write a shell script (a `stage.sh`, a loop) to
"parallelize" or "orchestrate" the batch. That path is what causes duplicate slugs, lost
pieces, and a front page you can no longer reason about. If the batch is large (more than about
four or five pieces), do it in chunks: finish a chunk, hand the human the links, and continue.
Slower and correct beats fast and scrambled.

## Verifying a piece is the PREVIEW link, not a rebuild or a re-check
The `preview` step prints the live `PREVIEW:` and `NEWEST:` links (its own host and port)
for each piece; hand those to the human, that IS how they verify it, and it is the ONLY
confirmation you need. A successful `preview` already staged the piece; the generate watcher
repaints the local site on its own within a couple of seconds, so just open the `PREVIEW:` link.
Do NOT re-check a piece in a loop, and NEVER run `./run.sh generate`, `make generate`,
`./deploy/*.sh`, or the test suite (`pytest` / `make test`) to make a piece appear or to check
it: those touch the tooling, not your articles, and only burn time and loop. Use plain
`python3 cli/censurado.py status` only to answer "is the stack up". If a piece will not resolve,
relay that to the human; do not debug the generator.

## Fix a piece in place; never unpublish to "fix" it
If a published piece is wrong (title, body, tags, section), correct it with `edit <slug>` in
place. Do NOT `unpublish` it and re-`preview` under a new slug: the slug is the piece's identity,
and changing it orphans every portada entry and `{{relacionado:}}` link that pointed at the old
slug, so the piece looks like it "disappeared" from the front page. Keep the slug stable.

## What a good sweep looks like
- **Scope:** a freshness window ("today", "last few hours") and 3 to 6 pieces is a healthy
  sweep. List authors with `personas`, read each with `persona <id>` for its beat and outlets,
  and map each fresh story to the author whose beat fits. Empty DB? Create an author first (see
  the `authors` sub-skill).
- **Fresh titulars need feeds, not generic search.** Each author reads its own outlets
  (`sources <id>`; `portals` for the full registry). A news sitemap is newest-first and
  self-windowed to about 48h, so its top entries are freshest. Treat "newest by timestamp" as
  latest, not as the editorial lead; use a portada/home feed for the true lead where one exists.
- **Pick the leads and DEDUP** against what is already on the front page and against the
  author's own archive (`archive <author-id> --q "<entity>"`, then `get <slug>` only on real
  doubt). If a prior piece already tells the story and you hold nothing new, STOP that item and
  say so; only a genuinely new finding justifies a follow-up, and it must cite the prior piece
  with `{{relacionado:<slug>}}`.
- **Tags:** keep the small canonical THEME set small (draw one theme: `inteligencia
  artificial`, `política argentina`, `economía`, `internacionales`,
  `literatura`, `universidades`, `corrupción`), then always add the proper-noun ENTITY tags the
  piece is about (people, orgs, places), up to the tag ceiling `TOPIC_CAP` in the bundled recipe
  `cli/workflow/parameters.json` (the same `{{TOPIC_CAP}}` the finalize node enforces; not `style`).
  Note: "misterio y conspiración" is now a SECTION (its own beat), not a theme tag, so file those
  pieces under the `misterio-y-conspiracion` section instead of tagging them with it.

## After the sweep
If tags drifted, you have two tools. For a ONE-OFF fix on a single article, edit its tags in
place with the `edit` verb, which re-sends the whole topics set (admin:write); read a piece
first with `get <slug>` if you need its current tags:

    python3 cli/censurado.py edit <slug> --set "topics=inteligencia artificial,Milei,economía"

For a corpus-wide MERGE of naming variants of one entity (e.g. `Javier-Milei` into `milei`)
across many articles and the authors' profile chips, run the topic-cleanse walk, which drives
the hash-safe `censurado-brain topics cleanse` plus `profile-topics`:

    python3 cli/censurado.py step --mode topic-cleanse

You supply the canonical form for each tag yourself; nothing runs a model. See the tag surface
first with `python3 cli/censurado.py topics`. The operator can also curate tags from the
panel's Articles and Temas tabs.

Then arrange the front page. Once every queued item is published, lay out each UTC day the
batch touched with the portada arrange walk:

    python3 cli/censurado.py step --mode portal-review

Do it one day at a time (a piece's day is its `published_at` UTC day). Load the day in one read
with `archive --day <YYYY-MM-DD>`, then follow the node to write the plan with `portada <date>
--set-json` (lead first by position, alternate media/text, promote a lone trailing piece to
`"important"`, a full-row double card, so no row is left half empty). Skip a day with fewer than 3 pieces; its default order is fine.
Going live stays a separate, human-gated verb: `python3 cli/censurado.py publicar --yes`.
