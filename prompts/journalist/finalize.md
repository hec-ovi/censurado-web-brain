# Finalize the article

Structure the finished article into the publish payload. This is a formatting and
titling step: do not rewrite the body, add facts, or change the angle. You lift the
article's existing parts into the fields below, and you craft the sharpest honest
headline and dek the article's own facts will support.

The article is for the **{{SECTION}}** section, by **{{AUTHOR}}**. You do not choose
the section or the author; they are fixed.

The finished article:

{{ARTICLE}}

The article is written in attention-staged layers (a short standalone summary, then
the developing detail, then the full context). Derive every field faithfully from
what is already there; never invent a fact, a number, or an angle the body does not
support.

## How to write the title and the subtitle

A headline is a promise: the article must deliver exactly what the title implies.
Within that rule, make it arresting, not flat. Before you settle, draft several
headline options and keep the one that is both the most compelling AND fully honest.

A keeper passes ALL of these honesty gates:

- Promise kept: the body delivers what the headline implies.
- No withheld subject: name the real thing. Never "esto", "lo que pasó", "una
  sorpresa". The concrete noun goes in the title; the pull comes from its stakes,
  not from hiding it.
- No overclaim: every superlative, number, and cause is backed by the body.
- Numbers in context: a figure carries its comparison or its scope.
- No fake urgency: "urgente" only for genuinely time-bound stakes.
- Earned emotion: the feeling the title evokes is the one the facts justify.

And it lands at least two of these hooks:

- Front-load the most surprising concrete fact (a name, a number, an action).
- A strong active verb in the present ("recorta", "obliga", "esconde"), not the
  limp "se producen cambios".
- Visible stakes: why it touches the reader (who gains, who pays, what is lost).
- Tension or reversal ("prometió X, entregó Y"), a first-ever, or genuine awe.

The `subtitle` (the dek, the bajada) does the second half of the work: it does NOT
repeat the title's words or its subject; it pays off the stakes the title teases
(the consequence, the number, the twist), adds a second hook, and sets up the
opening line.

Spanish conventions: título in active voice and present tense, no final period, no
ALL-CAPS, no unfamiliar acronyms, roughly under 65 characters; bajada about 10 to 15
words. Read the título and the bajada aloud together: if they sound like a promise
you cannot keep, rewrite; if they sound flat, you missed the hooks, so add a
concrete fact and the stakes, never hype.

Return the article's content fields:

- `title`: the headline, crafted as above. Arresting and concrete, drawn from the
  article, never a new angle.
- `subtitle`: the dek, one line under the title, crafted as above. Different from the
  title and from the summary; it does not repeat the title's words.
- `summary`: the standalone dense summary, two or three sentences, that a reader in a
  hurry can stop at and still know what happened and why it matters. Self-contained:
  it must make sense without the body. Draw it from the article's opening layer; do
  not write the word "summary" into it.
- `body`: the COMPLETE article body in Markdown, exactly as finished. Do not
  truncate, summarize, or drop any part of it. There is no length limit on the body.
- `topics`: a list of short topic tags for the article, at most {{TOPIC_CAP}} and the most
  specific and optimized for the piece (may be empty). The tags are the THEMES of the
  piece plus the named ENTITIES it is about (people, organizations, places), lowercase
  and accented where Spanish requires it. Specific beats vague: a precise tag beats a
  loose one, and three sharp ones beat a longer list of loose ones.
- `keywords`: the search terms specific to THIS article, what a reader would type to
  find exactly this story. Include the named people, places, and organizations the
  article actually names, plus the distinctive concepts central to it. Draw every one
  from the article's own text; leave out generic filler and anything the body does not
  mention. These are narrower and more specific than `topics`; an article with no
  distinctive named entities may return an empty list.
- `slug`: optional; lowercase words joined by hyphens, derived from the title. Omit
  it to let it be derived server-side.
