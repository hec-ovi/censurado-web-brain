# Editorial style guide

The newsroom's qualitative voice and house rules: the what-it-should-read-like guide the
`style` verb prints. It is NOT the enforced numeric bar (that lives in
`cli/workflow/parameters.json` as MIN_SOURCES / MIN_PER_TYPE / TOPIC_CAP / RESPIN_PASSES,
which `step` fills into the nodes; `set-floor` sets the two source counts). It is also NOT
the per-language word lists: the banned lexicon, the preferred swaps, the orthography, the
slop phrases, and the attribution and disclaimer wording are DB rows, authored per language.
Read them for your author's language with:

    python3 cli/censurado.py editorial-rules <language>

## Voice
Write in the author's own voice, in the first person, from a clear point of view: each author
argues the side their persona declares and defends it with the evidence in hand. What stays
neutral and exact are the FACTS: report the fact before the reaction, attribute every claim to
a named source, and separate what is confirmed from what only one side asserts. The framing,
the emphasis, and the argument are the author's position, never a lukewarm middle. A piece has
to catch and hold attention, and it wins with the sharpest true detail, never with hollow
adjectives. (A politics-section piece is the one exception to the first person: it is written
in the third person, with the slant living only in the framing.)

## Examples
Good: name the concrete fact with its figure and its source, e.g. "the central bank raised
the benchmark rate to 40 percent, according to its official statement." The framing may take a
side; the datum is untouched.

Bad: an empty, sensational adjective with no figure and no source, e.g. "in a crushing
decision, they punished savers again." The edge has to come from the fact, not the adjective.

## Rules
Gate (a piece must pass these to publish):
- fact-first: open with the central fact and its concrete consequence.
- attribute: name the source as plain text (the attribution form is per-language, see
  `editorial-rules`), with no links in the body. Name a media outlet only when it is one of
  the author's ASSIGNED sources; the primary actors (people, companies, officials,
  institutions, and documents that ARE the news) are always named.
- cross-sourcing: corroborate the central fact across independent sources (the floor is
  MIN_SOURCES in parameters.json).
- confirmed vs asserted: distinguish what is confirmed from what only one party claims.
- no invention: use only facts and quotes present in the gathered sources.
- neutral facts, not neutral stance: neutrality belongs to the FACTS (exact and attributed);
  take a side in the framing and the argument, never by bending the datum.

Preference (aim for these; they sharpen the piece):
- direct title: a short, direct headline, the essential fact, no filler.
- figures with a source: pair every figure with its source and its date.
- local context: give the context the local reader needs, without assuming it is known.
- no jargon: explain any technical term the first time it appears.
- no repetition: add what is new today and link the related prior coverage.
- useful close: close with what comes next or what is still unknown, not an empty flourish.
- engage: keep the piece alive; break the monotony with a real device where the material earns
  it (a pull-quote, a short list, a lone figure, an image).

## Lexicon and orthography
The banned sensational words, the preferred swaps, and the accents and marks the author's
language requires are per-language and live in the DB, not here. Fetch and apply them with
`editorial-rules <language>`; the orthography pass (`85-accents-entities`) enforces them.

## Sourcing
Corroborate the central fact across at least MIN_SOURCES independent sources, with at least
MIN_PER_TYPE of each political lean (right, neutral, left). If the author lacks sources of one
lean, use web search at your discretion to INFORM the fact, but any outlet that surfaces there
is background and is NOT named. Name a media outlet only when it is one of the author's
ASSIGNED sources; a fact drawn from a non-assigned outlet is attributed to the primary actor
(the person, company, official, institution, or document that is the news) or reported with no
outlet named. If the author has no assigned sources, name no outlet, only primary actors.
Attribute every claim and never invent a quote.

## Structure
- Headline: short and direct, the essential fact, no filler or adjectives.
- Dateline: CITY, date, at the start of the body.
- Lede: first paragraph with what happened, who, when, and why it matters.
- Tags: up to TOPIC_CAP tags (parameters.json), naming the proper entities.
- Respin: up to RESPIN_PASSES revision passes before publishing.
