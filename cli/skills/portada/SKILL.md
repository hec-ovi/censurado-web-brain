---
name: portada
description: >-
  Read or set the front-page plan (the portada) for a given day: which article leads, the
  order of the rest, and which cards render double (full row) vs single (half row). Use when
  the user wants to reorder the home page, change the day's lead, make a card double / full
  width / destacada, or curate what runs on a date. The front-page "Recomendado" rail is a
  separate global list (see the recomendado verb below).
---

# Curate the front page (portada)

The portada is the per-day plan for the home page: which articles appear, in what ORDER, and
which cards span the full row. It lives in the publish backend (Bearer-authed, handled for
you), keyed by date. Setting a day's portada replaces that day's plan.

## The layout model (read this before writing a plan)

Three card sizes exist, and people name them loosely. Map the words like this:

| the user says | what it is | how you set it |
|---|---|---|
| the lead, the day's portada, the main story | the full-bleed hero across the top of the day | make it the FIRST entry; role changes nothing there |
| double card, double column, full width, destacada | a card spanning BOTH columns on its own row | `"role": "important"` |
| single card, normal card | half a row; singles pair up two per row | `"role": ""` |

- `entries` is the ORDERED list of `{slug, role}`. Array order IS page order (index order).
- The FIRST entry (`entries[0]`) is the day's LEAD: it renders full width BY POSITION, role
  ignored there. Leave its role `""`. The lead is NOT just a double card: there is exactly
  one lead per day, always on top, styled as the hero; double cards are extra full-row
  slots below it.
- Desktop is a two-column grid that fills in index order, left to right, top to bottom.
  Singles take half a row and pair up; a double card takes a whole row and forces the next
  entry onto a fresh row. Entries indexed 0-5 with entry 3 marked `"important"`:

      row 1  [ 0  the lead (hero) .............. ]
      row 2  [ 1 single ]  [ 2 single ]
      row 3  [ 3  double card ................... ]
      row 4  [ 4 single ]  [ 5 single ]

- NEVER LEAVE A GAP: the singles between two full-row cards must come out EVEN, or the last
  one sits beside an empty cell. Fix a lone trailing single by promoting it to
  `"important"`. Beyond that, keep doubles few so they still read as featured.
- Mobile is one single column in the same index order. Arrange for desktop; mobile follows.
- Same-day articles you leave out of the plan still show: they append AFTER your entries,
  in default order, as singles.

## Read the current plans
    python3 cli/censurado.py portada <YYYY-MM-DD>

With no `--set-json` this lists the stored portadas (there is no single-date GET; listing is
fine). Get the slugs to arrange from the day's articles: `python3 cli/censurado.py archive
--day <YYYY-MM-DD>` (the whole day in one read), or `archive <author-id>` / the site's `/latest/`.

## Set a day's plan
Pass a JSON object; the positional date is merged in and wins over any date inside the JSON:

    python3 cli/censurado.py portada 2026-07-03 --set-json '{
      "entries": [
        {"slug": "el-decreto-que-concentra-poder", "role": ""},
        {"slug": "cerrojo-al-banco-central-mas-ajuste", "role": ""},
        {"slug": "la-maquina-que-devuelve-la-mirada", "role": ""},
        {"slug": "el-apagon-que-nadie-explico", "role": "important"}
      ]
    }'

That renders: row 1 the lead (first entry, hero), row 2 the two singles paired, row 3 the
`"important"` closer as a double card. No gaps.

Every slug must be an article published THAT day. A typo'd or unlisted slug does NOT render
an empty card: it is silently dropped at render and everything shifts up, so your intended
lead can silently change. Pull slugs from `archive --day` / the site, never from memory. The
change is local (the preview site regenerates in a couple of seconds); going public is still
a separate `publicar --yes`.

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
