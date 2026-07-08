# Curate the front page per day (maintenance)

This is a standalone maintenance walk, not part of writing an article. You curate the
FRONT PAGE for a given day: which story leads, in what order the rest of the day's pieces
run, and an optional short list of recommended pieces for the sidebar. Left alone, the day
falls back to a default order; a portada plan REPLACES that with an editor's ordering. Run
this after a batch, or whenever a day's front page reads badly.

Do it one day at a time. For each day under review:

1. Read the current plan first: `python3 cli/censurado.py portada <YYYY-MM-DD>` lists every
   saved plan, so you can see what (if anything) is already set for the day.

2. Load the day's pieces in one read: `python3 cli/censurado.py archive --day <YYYY-MM-DD>`
   lists every piece published that UTC day across all authors, LIGHT: slug, title,
   description (the dek), section, topics, and a `card_type` label (what the piece's CARD
   shows: `text`, `image`, `youtube`, or `video`). That is the raw material you are ordering,
   and the whole day's card map in one cheap read, so you never open a body. For each piece note
   the two things you arrange on: its news WEIGHT (how much the story matters) and whether its
   card carries MEDIA (`card_type` is not `text`) or is TEXT-forward, because the layout
   alternates the two. Also note when two pieces are DELIBERATELY related (opposing takes on the
   same event, a claim and its rebuttal), which you may pair on purpose (step 4).

3. Pick the LEAD. Evaluate the day's pieces and put the single most important one first: the
   story with the most news weight (impact, reach, how many readers it matters to,
   exclusivity, freshness). The lead renders as a full-bleed hero across the top of the page,
   so when two stories are comparably important prefer the one with a strong image. Weight
   still wins: a text-only story that is clearly the day's biggest still leads, the image is
   only a tiebreaker.

4. Order the rest for the DESKTOP grid. The front page is a two-column grid that fills in
   YOUR order, left to right, two pieces per row. The lead and any `"important"` piece span
   the FULL row on their own; every other piece takes half a row, so the rest pair up (the
   2nd and 3rd entries share a row, the 4th and 5th share the next, and so on). On phones the
   SAME order is one single column top to bottom, so you only ever arrange for desktop and
   mobile follows for free (1, 2, 3, 4, 5 ... in index order is already fine there).

   Alternate MEDIA and TEXT like a checkerboard. Write `o` for a text piece and `x` for a
   media piece. Within each pair put one of each, and flip which side carries the media from
   one row to the next, so media never stacks down a column or sits doubled across a row. The
   target pattern:

       [o,x] [x,o] [o,x] [x,o] ...

   Start the first pair under the lead with TEXT on the left (`[o,x]`), so a second big image
   never lands directly beneath the lead's hero. When only a few pieces carry media, spread
   them into the middle rows instead of clustering them, e.g. a single media piece over three
   rows:

       [o,o] [x,o] [o,o]

   A full-row `"important"` piece breaks the pairing and starts a fresh row after it, so
   mixed shapes are fine and all render correctly, e.g.:

       [x] [o,x] [x,o] [o] [o,x]

   NEVER LEAVE A GAP. Both cells of every two-column row must be filled. If the pieces that
   pair up come out ODD so the last one would sit alone in a half-row (`[o,null]`), desktop
   shows an empty column beside it. Fix it by promoting that lone trailing piece to
   `role:"important"` so it renders as a full single row (`[o]`) with no gap. So a full row is
   earned for two reasons: a story that deserves a full-row callout (a strong second lead, a
   standout media piece), OR a lone trailing piece that would otherwise leave a gap. Beyond
   those, keep `"important"` rare, never half the day.

   CONTENT FIRST, then visuals. The checkerboard is the DEFAULT, not a hard rule. When two
   pieces are DELIBERATELY related (opposing op-eds on the same event, a claim and its
   rebuttal, two sides of one debate), you MAY set them side by side even if both are text
   (`[o,o]`), because that juxtaposition is the point and it wins over the media checkerboard.
   Otherwise keep VARIETY: never place two pieces by the same AUTHOR adjacent (side by side or
   stacked), and keep same-TOPIC pieces apart too, EXCEPT the deliberate opposing-takes pair
   above.

5. Optionally choose a SHORT `recomendado` list (a handful of slugs) for the day's sidebar:
   worthwhile pieces that are not in the lead run but deserve a second look.

6. Write the plan with the CLI. The FIRST entry is the day's lead:

       python3 cli/censurado.py portada <YYYY-MM-DD> --set-json '{"entries":[{"slug":"the-lead","role":"important"},{"slug":"second","role":""},{"slug":"third","role":""}],"recomendado":["extra-one","extra-two"]}'

   Read it back any time with `python3 cli/censurado.py portada <YYYY-MM-DD>` (lists all).

The change shows up on the site only after a regenerate. In the running stack the generate
service repaints the front pages on its own; the sealed pages are rewritten on the next full
build. If you want it live now, regenerate (or, only when the human asks, publish with `publicar
--yes`). Then open the day's front page and confirm the lead, the order, and the
recommended list are what you set.
