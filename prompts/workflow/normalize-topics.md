# Normalize author profile topics (maintenance)

This is a standalone maintenance walk, not part of writing an article. You curate the
short list of domain topics shown on each author's PUBLIC profile page (`/author/<id>/`).
Left alone, that page lists the union of every tag every one of the author's pieces ever
carried, which sprawls into dozens of chips. A curated `profile_topics` list REPLACES that
union with the handful of beats the author is actually known for. Run this after a batch,
or whenever the profiles have drifted.

Do it author by author. For each active author:

1. Read who they are: `python3 cli/censurado.py persona <id>` (beat, `who_i_am`, `style`,
   `about`). That is the identity the topics should reflect.
2. Look at what they actually cover. Skim the author's recent pieces and the topic pages
   that exist on the site (`python3 cli/censurado.py portals` lists sources, not topics, so
   judge topics from the corpus). Only pick topics that already have published articles,
   so every chip links to a real page.
3. Choose a SMALL canonical set (aim for three to six) of normalized domain topics: the
   author's real beats, lowercased, de-accented the way the site slugifies them (so
   "Politica", "política" and "politica" all mean the one topic `politica`). Prefer broad
   beats (`politica`, `economia`, `cultura`) over one-off story tags. No duplicates.
4. Write it: `python3 cli/censurado.py profile-topics <id> --set "politica, economia, cultura"`.
   To clear a bad set and fall back to the computed union, pass `--set ""`.
   Read the current value any time with `python3 cli/censurado.py profile-topics <id>`.

When every author is done, push the curated topics to the platform so the generator can
read them (they travel in the author registry, not in the articles):

    curl -fsS -X POST "$CENSURADO_BRAIN/mirror/authors"

(`$CENSURADO_BRAIN` defaults to http://127.0.0.1:8085, the brain config plane, which
upserts every author to the backend using its own operator token.) A dry run that lists
the handles without pushing is `.../mirror/authors?dry_run=true`.

The change shows up on the site only after a regenerate. In the running stack the generate
service repaints the front pages on its own; the sealed author pages are rewritten on the
next full build. If you want it live now, regenerate (or, only when the human asks, deploy
with `make deploy`). Then open an author's profile and confirm the chips are the curated
set, not the old union.
