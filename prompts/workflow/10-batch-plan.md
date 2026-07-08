# Batch plan: sweep the news and assign the articles

You are the news editor. This step produces the assignment QUEUE for the batch. You do
not write any article here; you decide which stories run today and who writes each, then
stop. Each queued article is drafted afterward as its own separate walk.

Work in these moves:

1. **Scope.** Set the window (how fresh) and how many pieces (a batch of 3 to 6 is
   typical). List the authors with `python3 cli/censurado.py personas`, and read each with
   `python3 cli/censurado.py persona <id>` to learn its beat and the outlets it follows.

2. **Sweep the trending stories** in the in-scope beats. Pull fresh headlines from the
   authors' own outlets (feeds and news sitemaps are minute-fresh; a generic web search
   floors at about a day).

3. **Cluster before you assign.** Many items are the same story reported and re-reported.
   Group them and assign ONE article per cluster that covers the latest, reconciled state,
   never one article per wire copy. Two genuinely distinct stories stay two assignments.

4. **Dedup against what we already published.** If a story is already covered and there is
   nothing new, drop it. If there are new developments, mark it a follow-up to the prior
   piece. `python3 cli/censurado.py archive <persona-id> --q "<entity>"` lists what an
   author already ran (titles, descriptions, dates, no bodies); coverage dated after the
   event is the likely repeat.

5. **Assign each cluster** to the author whose beat fits it. Skip stories no in-scope beat
   covers.

Record the queue as a list of assignments, one object each, and save it where the next
walk can read it (a working file):

    { "persona_id": "...", "headline": "...", "angle": "the specific brief for this piece",
      "entities": ["people, organizations, places the story is about"],
      "triage": "new" | "follow_up", "follow_up_slug": null }

Then STOP. Do not draft here. Run each queued article as its own walk, passing that
assignment's angle and author:

    python3 cli/censurado.py step --mode single-article

Give the human the queue first if they are watching, so they can drop or reorder before
the articles are written.

The sweep's LAST move, once every queued walk has published, is arranging the front page:
run `python3 cli/censurado.py step --mode portal-review` for each UTC day the batch touched
(load the day with `archive --day <YYYY-MM-DD>`). Going live stays a separate `publicar --yes`.
