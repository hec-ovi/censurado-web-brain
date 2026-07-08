---
name: sources
description: >-
  Manage the outlets an author reads and the corroboration floor the editorial walk enforces:
  list the portal registry, show or replace one author's linked sources, and set MIN_SOURCES /
  MIN_PER_TYPE. Use when the user wants to change which outlets a writer follows, add a source,
  or raise/lower how many independent sources every article must cross-check.
---

# Sources and the corroboration floor

Two things live here: WHICH outlets an author reads (per-author, in the DB), and HOW MANY
independent sources every article must cross-check (the floor, a bundled recipe setting).

## The portal registry
Each portal record carries a `lean` (`left`, `neutral`, or `right`) and an `ownership_group`.
Those two fields drive the whole sourcing policy: lean balances the story across the spectrum,
and ownership_group is the independence test (two outlets in one group count as ONE source).

- List every portal id available to link: `python3 cli/censurado.py portals`
- The full records (with `lean` and `ownership_group`) come from the backend (`GET /sources`);
  a portal's own feed endpoints for freshness live in the gitignored `.research/portal-recency-feeds`.

## An author's linked outlets
- Show what an author reads: `python3 cli/censurado.py sources <id>`
- REPLACE the set (a full replace, not a merge): `python3 cli/censurado.py sources <id> --set clarin-com,pagina12-com,infobae-com`

Every id is validated against the registry, so an unknown portal is rejected rather than
stored as a dangling link. An empty `--set ""` clears the author's pool (it then falls back to
the global registry). That fallback governs only what may INFORM research, never what may be
NAMED: naming a media outlet in an article always requires it to be on the author's assigned list,
so an author with no assigned sources names no media outlet, only primary actors.

## The corroboration floor (MIN_SOURCES / MIN_PER_TYPE)
The research node enforces this on every article: cross-validate each central fact across at
least MIN_SOURCES INDEPENDENT sources, with at least MIN_PER_TYPE of EACH lean (left, neutral,
right), so no single side carries a fact. An author leads with its own side's outlets, but the
story must still stand across all three leans; if the author lacks enough outlets of a lean,
the walk fills that lean with web search.

- Read the ENFORCED floor: `python3 cli/censurado.py step 30-research --mode single-article`
  (the node the floor drives; the numbers are already substituted in), or inspect
  `cli/workflow/parameters.json` directly.
- Set it: `python3 cli/censurado.py set-floor --min-sources 6 --min-per-type 2`

`set-floor` writes the bundled recipe (`cli/workflow/parameters.json`), the SAME file the walk fills
`{{MIN_SOURCES}}` / `{{MIN_PER_TYPE}}` from, so the change takes effect on the next article.
Because the recipe travels WITH this package, commit the change to version it (it is not live
config). Do NOT read the floor from `python3 cli/censurado.py style`: that reads the separate
editorial style guide (voice/rules and its own sourcing numbers, a recipe file), which
`set-floor` does not touch, so after any `set-floor` it can disagree with the enforced floor. Sensible
defaults are 6 and 2 (two of each lean makes six); raising the floor makes every piece harder to
source, so move it deliberately.
