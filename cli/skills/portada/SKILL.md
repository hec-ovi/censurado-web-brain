---
name: portada
description: >-
  Read or set the front-page plan (the portada) for a given day: the ordered list of articles,
  which ones are featured, and the "recomendado" rail. Use when the user wants to reorder the
  home page, feature or unfeature a story, or curate what leads on a date.
---

# Curate the front page (portada)

The portada is the per-day plan for the home page: which articles appear, in what ORDER, which
are featured, and an optional "recomendado" rail. It lives in the publish backend (Bearer-authed,
handled for you), keyed by date. Setting a day's portada replaces that day's plan.

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
      ],
      "recomendado": ["sonnet-5-fable-5-vuelve"]
    }'

- `entries` is the ORDERED list of `{slug, role}`. Order in the array is order on the page.
- `role` is `"important"` for a featured slot (it renders larger/leads) or `""` for a normal
  card. Keep featured slots few so they still read as featured.
- `recomendado` is an optional list of slugs for the recommendation rail; omit it or pass `[]`
  for none.

Every slug must be a real published article; a typo puts an empty card on the page, so pull the
slugs from `archive` / the site rather than typing them from memory. The change is local (the
preview site regenerates in a couple of seconds); going public is still a separate `make deploy`.
