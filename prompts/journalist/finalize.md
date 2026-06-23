# Finalize the article

Structure the finished article into the publish payload. This is a formatting step:
do not rewrite or shorten the article, only place its parts into the required
fields.

The article is for the **{{SECTION}}** section, by **{{AUTHOR}}**. You do not choose
the section or the author; they are fixed.

The finished article:

{{ARTICLE}}

Return the article's content fields:

- `title`: the headline (do not invent a new angle; draw it from the article).
- `body`: the COMPLETE article body in Markdown, exactly as finished. Do not
  truncate, summarize, or drop any part of it. There is no length limit on the body.
- `topics`: a list of short topic tags for the article (may be empty).
- `slug`: optional; lowercase words joined by hyphens, derived from the title. Omit
  it to let it be derived server-side.
