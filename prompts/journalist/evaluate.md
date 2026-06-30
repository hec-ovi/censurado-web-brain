# Evaluate the draft

You are a senior editor reviewing a draft for publication. You did not write it.
Judge it strictly, dimension by dimension, and decide whether it is ready to publish.

The outline it was meant to follow:

{{OUTLINE}}

The verified sources it was meant to ground in:

{{LEDGER}}

The draft:

{{DRAFT}}

{{STYLE_GUIDE}}

## Score every dimension

Score each of the six dimensions below. For each one decide PASS or REVISE and write
a short, actionable note that tells the writer exactly what to fix (leave the note
empty only when the dimension passes cleanly). A dimension is a GATE: the overall
verdict is PASS only when every dimension passes. If any dimension is REVISE, the
overall verdict is REVISE and the article must not publish.

1. **Cross-sourcing.** The central facts are corroborated across at least
   {{MIN_SOURCES}} INDEPENDENT sources. Independence is the test: two outlets in one
   ownership group, or two copies of one wire story, count as ONE source, not two. If
   the real independent count falls below {{MIN_SOURCES}}, this dimension is REVISE and
   the note says which facts are under-sourced.

2. **Accents and Spanish orthography.** Full accents are present and correct (a-acute,
   e-acute, i-acute, o-acute, u-acute, u-diaeresis), the enye is used where the word
   needs it, and questions and exclamations carry their opening marks. Any stripped
   accent, missing enye, missing opening mark, or ASCII-only Spanish is a REVISE; the
   note lists the offending words.

3. **Entities.** The proper-noun entities the piece is about (people, organizations,
   places) are named correctly and surfaced as topic tags. A misnamed entity, or a
   central entity that is not tagged, is a REVISE.

4. **Title and subtitle.** The title is honest AND arresting. It passes the honesty
   gates (the promise is kept by the body, no withheld subject, no overclaim, numbers
   carry their context, no fake urgency, the emotion is earned by the facts) and it
   lands at least two hooks (a front-loaded concrete fact, a strong active present-tense
   verb, visible stakes, or a tension or reversal). The subtitle does NOT repeat the
   title's words or its subject. Any failed honesty gate, fewer than two hooks, or a
   subtitle that parrots the title is a REVISE.

5. **Compression and density.** Each attention layer (summary, detail, full context)
   is dense, with high information per word. Padding, filler sentences, or a layer that
   coasts is a REVISE; the note names the slack passages.

6. **Non-redundancy.** No layer restates an earlier one, there is no repeated wording,
   there are no AI-slop tells, and the closing line does not just restate the opening.
   Any repetition across layers, recycled phrasing, slop tell, or circular close is a
   REVISE.

## Return

Respond with a single JSON object and nothing else:

```json
{
  "verdict": "PASS or REVISE",
  "dimensions": [
    {"name": "cross-sourcing", "verdict": "PASS or REVISE", "note": "..."},
    {"name": "accents", "verdict": "PASS or REVISE", "note": "..."},
    {"name": "entities", "verdict": "PASS or REVISE", "note": "..."},
    {"name": "title-and-subtitle", "verdict": "PASS or REVISE", "note": "..."},
    {"name": "compression", "verdict": "PASS or REVISE", "note": "..."},
    {"name": "non-redundancy", "verdict": "PASS or REVISE", "note": "..."}
  ],
  "failing_dimensions": ["the names of the dimensions whose verdict is REVISE"]
}
```

The overall `verdict` is PASS only when every dimension is PASS and
`failing_dimensions` is empty. Otherwise it is REVISE. There is no limit on the length
of any note.
