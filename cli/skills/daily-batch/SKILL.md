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
floor, honest-headline gate, and evaluate/respin loop.

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
  artificial`, `política argentina`, `economía`, `internacionales`, `misterio y conspiración`,
  `literatura`, `universidades`, `corrupción`), then always add the proper-noun ENTITY tags the
  piece is about (people, orgs, places), up to the tag ceiling `TOPIC_CAP` in the bundled recipe
  `cli/workflow/parameters.json` (the same `{{TOPIC_CAP}}` the finalize node enforces; not `style`).

## After the sweep
If tags drifted, tidy them through the backend (there is no separate brain and no bulk cleanse
pass anymore). Fix an article's tags in place with the `edit` verb, which re-sends the whole
topics set (admin:write); read a piece first with `get <slug>` if you need its current tags:

    python3 cli/censurado.py edit <slug> --set "topics=inteligencia artificial,Milei,economía"

You supply the canonical form for each tag yourself; nothing runs a model. The operator can
also curate tags from the panel's Articles and Temas tabs.

Then arrange the front page. Once every queued item is published, lay out each UTC day the
batch touched with the portada arrange walk:

    python3 cli/censurado.py step --mode portal-review

Do it one day at a time (a piece's day is its `published_at` UTC day). Load the day in one read
with `archive --day <YYYY-MM-DD>`, then follow the node to write the plan with `portada <date>
--set-json` (lead first, alternate media/text, promote a lone trailing piece to `"important"`
so no row is left half empty). Skip a day with fewer than 3 pieces; its default order is fine.
Going live stays a separate, human-gated `make deploy`.
