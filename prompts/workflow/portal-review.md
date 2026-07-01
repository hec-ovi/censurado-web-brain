# Curate the front page per day (maintenance)

This is a standalone maintenance walk, not part of writing an article. You curate the
FRONT PAGE for a given day: which story leads, in what order the rest of the day's pieces
run, and an optional short list of recommended pieces for the sidebar. Left alone, the day
falls back to a default order; a portada plan REPLACES that with an editor's ordering. Run
this after a batch, or whenever a day's front page reads badly.

Do it one day at a time. For each day under review:

1. Read the current plan first: `python3 cli/censurado.py portada <YYYY-MM-DD>` lists every
   saved plan, so you can see what (if anything) is already set for the day.
2. Look at the day's published articles: the corpus, the recent pieces, what actually ran
   that day. That is the raw material you are ordering.
3. Decide the day's ORDER. Pick the LEAD (the strongest, most important story) as the first
   entry, then order the rest. Each entry carries a role: `"important"` marks a full-row
   callout that runs prominent, and `""` is a normal slot. Good-portada judgement:
   - Alternate media-heavy pieces (hero image, video, embedded posts) with text-forward
     pieces, so the page does not stack three photo blocks or three grey walls in a row.
   - Do not put two adjacent pieces on the same topic; space same-topic stories apart.
   - Keep the lead and any `"important"` pieces genuinely prominent: reserve `"important"`
     for the few stories that earn a full-row callout, not half the page.
4. Optionally choose a SHORT `recomendado` list (a handful of slugs) for the day's sidebar:
   worthwhile pieces that are not in the lead run but deserve a second look.
5. Write the plan with the CLI. The FIRST entry is the day's lead:

       python3 cli/censurado.py portada <YYYY-MM-DD> --set-json '{"entries":[{"slug":"the-lead","role":"important"},{"slug":"second","role":""},{"slug":"third","role":""}],"recomendado":["extra-one","extra-two"]}'

   Read it back any time with `python3 cli/censurado.py portada <YYYY-MM-DD>` (lists all).

The change shows up on the site only after a regenerate. In the running stack the generate
service repaints the front pages on its own; the sealed pages are rewritten on the next full
build. If you want it live now, regenerate (or, only when the human asks, deploy with `make
deploy`). Then open the day's front page and confirm the lead, the order, and the
recommended list are what you set.
