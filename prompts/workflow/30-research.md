# Research: build the source ledger

Ground the story before you write a word. Produce 3 to 6 specific, searchable
sub-questions that, answered, would let you write this piece accurately: who and what,
when and where, the numbers, and the one or two competing claims. Each must be answerable
by a web search and target recent, verifiable facts.

Answer them from real sources, and build a LEDGER you keep for the rest of the walk: each
central fact paired with the source URL that carries it. Every claim in the article will
have to trace to a line in this ledger, so keep it as you go.

**Cross-validate across at least {{MIN_SOURCES}} INDEPENDENT sources, balanced by political
lean.** Read your author's own outlets first (`persona <id>` lists them, and the source view
groups them by `lean`: RIGHT, NEUTRAL, or LEFT). Your author prioritizes its own side's
outlets, but the story must still stand across the spectrum: pull at least {{MIN_PER_TYPE}}
INDEPENDENT sources of EACH lean (right, neutral, left), so no single lean carries a fact.
Independence is the test: two outlets in one ownership group, or two copies of one wire
story, count as ONE source, not two. If the author lacks enough outlets of a lean to reach
{{MIN_PER_TYPE}}, use web search at your discretion to INFORM the facts, but the outlets it
surfaces are BACKGROUND: learn the fact from them, never NAME them. Only the author's assigned
outlets (what `persona <id>` / `sources <id>` return) are nameable media. Attribute a fact from a
non-assigned outlet to the primary actor behind it (the person, company, official, institution, or
document that IS the news), or report it with no medium named; if the author has no assigned
outlets at all, name no media outlet, only primary actors. Never invent a source, a statistic, or
a quote, and never keep a URL you did not actually use.

If a named post is part of the story (a politician's announcement, a company's release, a
notable reaction, one of Trump's truths), find the real one now with a web search (a news page
that embeds it exposes its `x.com/.../status/<id>` URL) and note that status URL so you can
embed it later as a card. Some of your author's outlets are X accounts: web search what those
accounts posted about this story (the handle plus the topic), and treat a protagonist's post
as a primary source of intent, not just a quote. Establish what the
post is doing (whom it answers, what it announces, what it buries) so the article can
report that intention with the post embedded as its evidence. A post in English gets an
exact Spanish translation in the prose plus a one-time note that the original is in
English. Be surgical: a post card earns its place in maybe one story in several, never by
default.

**Sweep the author's own archive before you write.** A repeat corrodes the portal. List
what this author already published around your subject with `python3 cli/censurado.py
archive <author-id> --q "<entity or theme>"` (titles, descriptions, and dates only, cheap
on context), and judge candidates BY DATE against the event you are covering: a piece
dated before the event cannot be covering it, ignore it; a piece dated after it probably
is. Read a candidate's full body with `python3 cli/censurado.py get <slug>` only when the
title and description leave real doubt, and stage it (list first, full article last) so
the sweep never floods your context. If a prior piece already tells this story and you
hold nothing genuinely new, STOP the walk and say so: the story is covered. Only a major
new finding justifies writing on, and that piece must cite the prior one with
`{{relacionado:<its-slug>}}` and advance it, never retell it.
