# Censurado editorial rules (writing one article)

A compact contract for writing ONE publish-ready article. It covers only the CONTENT (text);
out of scope: X/tweet cards, related notes, and the other `{{...}}` widgets. The live,
enforced version of these rules is the step-gate workflow (`python3 cli/censurado.py step`);
the qualitative voice guide is `style`; and the per-language word lists (banned lexicon,
preferred swaps, orthography, slop phrases, attribution and disclaimer wording) are DB rows
you read with `editorial-rules <language>`, not literals in this file.

## 1. Voice and objectivity
- Write in the author's voice but OBJECTIVELY and in the third person: report the facts and let
  them carry the piece. Do not defend a position, editorialize, or write in the first person. No
  false balance either: report the strongest true account plainly, and separate what is confirmed
  from what one party asserts.
- Any lean shows ONLY in which sources and facts the author foregrounds, never in editorializing
  framing, verbs, or adjectives, and never by bending a datum: every date, figure, and name stays
  exact and attributed.
- First person and an argued point of view are reserved for a persona that is explicitly an
  opinion, satire, or fiction voice (for the current authors, the literature/culture writer and
  the disclaimed misterio voice); every other author reports straight. Politics-section pieces
  are third person always, even from those personas.
- The piece has to catch and hold attention. The edge comes from the sharpest true detail,
  never from empty adjectives.
- Write to be READ, not filed: entertain while informing. A flat wall of identical paragraphs
  is a failure even when the facts are right.

## 2. Facts and attribution (non-negotiable)
- Open with the central fact and its concrete consequence (fact before reaction).
- Name every source as plain text (the exact attribution form is per-language, see
  `editorial-rules`). Put NO links in the body: no hyperlinks. Images and `{{...}}` widgets are
  not links.
- Name a media outlet only when it is one of the author's ASSIGNED sources (what `persona <id>`
  / `sources <id>` return). Web search may INFORM a fact, but any outlet that surfaces there is
  background and is NEVER named. The PRIMARY ACTORS are always named and are not "media": the
  people, companies, officials, institutions, and documents that ARE the news. A fact from a
  non-assigned outlet is attributed to the primary actor or reported with no outlet named. If
  the author has no assigned sources, name no outlet, only primary actors.
- Distinguish what is confirmed from what only one party asserts.
- Pair every figure with its source and its date. Invent no data and no quotes.
- Give the local context the reader needs; explain any technical term the first time.

## 3. Title, dek, and standfirst (distinct layers, none repeating another)
- Title: the most compact, magnetic headline the story can accurately carry, no more than five
  words. It is a promise the body keeps. Name the real thing (never "this" or "what happened"),
  no overclaim, figures in context, no fake urgency. ONE whole thought: never two halves
  spliced by a `;`, a `:`, or a dash.
- Dek (the bajada): one line, 20 to 30 words, the strongest true fact the piece holds. It does
  NOT repeat the title's words or subject. Same ban on splices. No separate subtitle.
- Standfirst (the entradilla): a dense, objective opening paragraph that delivers the whole
  story. Never a review of the author.

## 4. Body structure
- Dateline at the start: CITY, date.
- Lede: first paragraph with what happened, who, when, and why it matters.
- Layers: self-contained summary, then the detail, then the full context. Each layer is NEW and
  dense; none restates an earlier one.
- Write the complete piece: no length limit, no placeholders.

## 5. No redundancy
- Say each idea ONCE. A caveat or framing note is stated one time, in a short line, never
  repeated.
- Do not stack synonyms of the same point. No layer restates another. The close does NOT
  restate the opening.

## 6. Cleanup and density
- Cut filler and repetition. Every line earns its place (high information per word).
- Full orthography for the author's language: apply the accents, marks, and letters
  `editorial-rules` lists. No ASCII-stripped spelling in a language that needs the marks.
- Clean Markdown: well-formed headings, lists, and links; no stray syntax.

## 7. Enrich (break the monotony)
Where the material earns it (never forced): a `> blockquote` pull-quote for the sharpest line,
a short list, a lone figure on its own line, a GFM table only for a real comparison, a second
mid-body image with plain Markdown `![alt](url)`. Vary the rhythm; do not run three identical
gray paragraphs in a row.

## 8. Banned (AI tells and sensationalism)
- Em and en dashes: NEVER. Use commas, periods, parentheses, or a colon.
- The banned words, the preferred swaps, the slop phrases, and the candor tics are per-language:
  apply the ones `editorial-rules` lists. No negation-inversion aphorism ("not X, but Y"). No
  thing (a market, a law, a country) given feelings or will. No close that only restates the
  opening, no false balance.

## 9. Satire, opinion, fiction
If the voice is satire, opinion, or fiction, open the body with ONE italic disclaimer line
(the wording is the one `editorial-rules` prints for the language) and nothing more. A
straight-news voice carries no disclaimer and no "opinion"/"analysis" label, and never names
its own byline.

## 10. Tags and keywords
- topics: up to the enforced tag cap (`TOPIC_CAP` in `cli/workflow/parameters.json`,
  currently 12), the most specific: the THEMES plus the proper ENTITIES (people,
  organizations, places), lowercase and accented where the language requires. A few sharp tags
  beat a full cap of loose ones.
- keywords: the narrower search terms proper to THIS article, drawn only from its text.

## Final checklist (before you hand it in)
1. Title accurate and magnetic, one whole thought, no splices, at most five words.
2. Dek that does not repeat the title; standfirst that tells the whole story objectively.
3. Central fact attributed to named sources as plain text; figures with dates; confirmed is not
   one party's version.
4. Each idea once; no layer repeats another; the close does not restate the opening.
5. No em or en dashes, none of the banned words, no AI tells.
6. Orthography correct for the language; clean Markdown.
7. topics and keywords specific; complete piece, no placeholders.
