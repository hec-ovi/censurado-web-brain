# Merge topic-tag variants across the corpus (maintenance)

This is a standalone maintenance walk, not part of writing an article. You merge naming
VARIANTS of one entity or theme into a single canonical tag, on the ARTICLES (their `topics`)
AND on the affected AUTHORS' profile chips, so a topic like Milei stops fragmenting into
`milei` / `Milei` / `Javier-Milei`. This is the corpus-wide cleanup. To only curate one
author's short profile-chip list (not the article tags), use the `normalize-topics` walk
instead. Detection is YOURS: no model runs in the backend, so you cluster the variants and
hand the mapping to the writer.

Casing already collapses, do not chase it. The public site slugifies tags, so `milei`,
`Milei`, and `POLÍTICA` / `política` / `politica` already resolve to the same
`/topic/<slug>/` page. The real targets are NON-casing variants that slugify differently:
`Javier-Milei` vs `milei`, singular vs plural, abbreviations, alternate spellings, and near
duplicates.

Do it one entity or theme at a time:

1. **Preflight.** `censurado-brain status` (the maintenance CLI reaches the same backend the
   authoring CLI does). If it fails, ask the human to start the stack.

2. **See the tag surface.** `python3 cli/censurado.py topics` lists every distinct tag with
   its article count and the slugs that carry it, most-used first. Scan it for variants of the
   same thing.

3. **Build the canonical map.** Pick ONE canonical form per cluster (the clearest, most-used,
   already-slugified form) and write a JSON `{variant: canonical}` object mapping each VARIANT
   to it. Map only what is truly the same entity; leave everything else out, because an
   unmapped tag passes through untouched. The one real hazard is OVER-merging: never fold two
   distinct entities (`milei` and `karina-milei` are different people) into one. Save it to a
   file, e.g. `map.json`:

       {"Javier-Milei": "milei", "javier milei": "milei", "mileis": "milei"}

4. **Dry-run and READ the plan.** `censurado-brain topics cleanse --map-file map.json`. The
   default is a dry run: it writes NOTHING and prints, per article, which tags would change.
   Read it as a hard gate. Confirm every rewrite is one you intend and that no distinct entity
   got swept in. If the plan is wrong, fix `map.json` and dry-run again.

5. **Apply to the ARTICLES.** `censurado-brain topics cleanse --map-file map.json --apply`. For
   each changed article it re-sends the SAME title, body, author, section, and published_at
   with only the topic set rewritten, so the content hash, slug, and permalink stay put; only
   the tags move. Exit 0 is done; exit 2 means some applies failed, so rerun.

6. **Fix the AUTHORS' profile chips.** The cleanse rewrites article tags only; an author's
   curated `profile_topics` is a SEPARATE list that does not auto-update. For each author whose
   chips still show a merged-away variant, set the clean list:
   `python3 cli/censurado.py profile-topics <id> --set "milei, economia, cultura"` (a full
   REPLACE; the `normalize-topics` walk covers how to choose the set). Skip authors whose chips
   fall back to the computed union, since they self-heal from the rewritten article tags.

7. **Reconcile the topic registry (manual).** The operator `/topics` facet registry can still
   list a merged-away slug, because the cleanse never touches that table. Open the panel's
   Temas tab and delete the stale variant row so the topic index matches the corpus.

8. **Regenerate.** The change reaches the site after a regenerate: the generate service
   repaints on its own, or regenerate now. Going live is a separate, human-gated `make deploy`.
   Then open a `/topic/<slug>/` page and confirm the variants collapsed into the one canonical
   tag.
