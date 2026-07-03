# Pull the fresh headlines from this author's outlets

Get minute-fresh titulars from the author's OWN outlets, not a generic search.

- `python3 cli/censurado.py sources <id>` lists the portals linked to this author.
- `python3 cli/censurado.py portals` lists every portal available.

Fetch each portal's machine-readable feed or news sitemap directly. A news sitemap is
newest-first and self-windowed to about 48 hours, so the top entries are the freshest.
Treat "newest by timestamp" as latest, not automatically as the editorial lead; use a
portada or home feed for the true lead where one exists.

Some outlets in the list are X accounts, not portals. Pull those accounts' latest posts
with your X tools (the X API MCP) instead of a feed; a fresh post by an account your
author follows is a titular too, and often the earliest one.

From the fresh items, pick the few stories that matter most for this beat, and dedup
against what is already on the front page. Carry the lead story (or stories) you chose into
the research step. In a single-article walk, one is enough.
