---
name: portada
description: >-
  Read or set the front-page plan (the portada) for a given day: the ordered list of articles
  and which ones are featured. Use when the user wants to reorder the home page, feature or
  unfeature a story, or curate what leads on a date. The front-page "Recomendado" rail is a
  separate global list (see the recomendado verb below).
---

# Curate the front page (portada)

The portada is the per-day plan for the home page: which articles appear, in what ORDER, and
which are featured. It lives in the publish backend (Bearer-authed, handled for you), keyed by
date. Setting a day's portada replaces that day's plan.

## Read the current plans
    python3 cli/censurado.py portada <YYYY-MM-DD>

With no `--set-json` this lists the stored portadas (there is no single-date GET; listing is
fine). Get the slugs to arrange from the day's articles: `python3 cli/censurado.py archive
<author-id>` per author, or the site's `/latest/`.

## Set a day's plan
Pass a JSON object; the positional date is merged in and wins over any date inside the JSON:

    python3 cli/censurado.py portada 2026-07-03 --set-json '{
      "entries": [
        {"slug": "el-decreto-que-concentra-poder", "role": "important"},
        {"slug": "cerrojo-al-banco-central-mas-ajuste", "role": ""},
        {"slug": "la-maquina-que-devuelve-la-mirada", "role": ""}
      ]
    }'

- `entries` is the ORDERED list of `{slug, role}`. Order in the array is order on the page.
- `role` is `"important"` for a featured slot (it renders larger/leads) or `""` for a normal
  card. Keep featured slots few so they still read as featured.

Every slug must be a real published article; a typo puts an empty card on the page, so pull the
slugs from `archive` / the site rather than typing them from memory. The change is local (the
preview site regenerates in a couple of seconds); going public is still a separate `publicar --yes`.

## Recomendado rail (global, front page)

The "Recomendado" rail on the front page is NOT part of a day's portada. It is ONE global list
of up to 10 article slugs that persists across days: it stays on the front page every day until
you change it, and it is the only source for that rail (there is no auto-computed fallback). Any
slug may be recommended, including pieces from previous days; a slug whose article does not exist
is dropped at render. When the list is empty the rail widget still renders (with no items), so
the front-page layout holds.

    python3 cli/censurado.py recomendado                       # read the current list
    python3 cli/censurado.py recomendado --set "slug-a,slug-b,slug-c"   # replace it (in order)
    python3 cli/censurado.py recomendado --clear               # empty it (widget stays, no items)

Order is preserved; blanks and duplicates are dropped; more than 10 is rejected. Writes need
admin:write (handled for you). Pull the slugs from `archive` / the site, not from memory.
