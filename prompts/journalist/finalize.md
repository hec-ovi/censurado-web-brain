# Finalize the article

Structure the finished article into the publish payload. This is a formatting step:
do not rewrite or shorten the article, only place its parts into the required
fields.

The article is for the **{{SECTION}}** section, by **{{AUTHOR}}**. You do not choose
the section or the author; they are fixed.

The finished article:

{{ARTICLE}}

The article is written in attention-staged layers (a short standalone summary, then
the developing detail, then the full context). Your job is to lift its existing
parts into the fields below. Do not invent a new angle, add facts, or rewrite the
prose; if a value is not already present in the article, derive it faithfully from
what is there.

Return the article's content fields:

- `title`: the headline. Short and concrete (aim for five words or fewer), drawn
  from the article, never a new angle. It must make a reader want to open the piece.
- `subtitle`: one line under the title, the dek. It sharpens the headline and
  answers "why would someone read this?" It is DIFFERENT from the title and from the
  summary; it does not repeat the headline's words. Draw it from the article's framing.
- `summary`: the standalone dense summary, two or three sentences, that a reader in a
  hurry can stop at and still know what happened and why it matters. Self-contained:
  it must make sense without the body. Draw it from the article's opening layer; do
  not write the word "summary" into it.
- `body`: the COMPLETE article body in Markdown, exactly as finished. Do not
  truncate, summarize, or drop any part of it. There is no length limit on the body.
- `topics`: a list of short topic tags for the article (may be empty).
- `slug`: optional; lowercase words joined by hyphens, derived from the title. Omit
  it to let it be derived server-side.
