# The editorial workflow loop

This is the single end-to-end loop for taking an assignment to a publishable article.
Run the steps in order. The numbers are knobs, filled from the editorial style config:
{{MIN_SOURCES}} independent sources, up to {{RESPIN_PASSES}} self-revision passes, and
at most {{TOPIC_CAP}} topic tags. Do not hardcode these; carry the filled values.

## Steps, in order

1. **Research.** Produce 3 to 6 specific, searchable sub-questions and answer them with
   sourced facts. Cross-validate the central facts across at least {{MIN_SOURCES}}
   INDEPENDENT sources. Independence is the test: two outlets in one ownership group, or
   two copies of one wire story, count as ONE source, not two.

2. **Outline.** Plan the article in attention-staged layers (hook or dek, a
   self-contained summary, the developing detail, the full context). Each layer carries
   new, dense information; no layer restates an earlier one.

3. **Draft.** Write the prose from the outline and the verified sources, in the author's
   voice. Ground every factual claim in a source from the research ledger.

4. **Evaluate.** Run the dimension review. It scores cross-sourcing, accents and
   Spanish orthography, entities, title and subtitle, compression, and non-redundancy,
   and returns an overall PASS only when every dimension passes.

5. **Respin.** Self-revise against the evaluate feedback, up to {{RESPIN_PASSES}}
   passes. Stop early the moment evaluate returns PASS. Each pass keeps every grounded
   fact and citation and changes only form, wording, and structure.

6. **Factcheck.** Correct any remaining grounding problems: remove citations not in the
   approved sources, resolve every unresolved marker, keep dates and proper names
   exactly as the sources have them.

7. **Finalize.** Lift the finished article into the publish payload: the honest and
   arresting title, the subtitle (which does not repeat the title), the standalone
   summary, and the body. Set topics at most {{TOPIC_CAP}}, the THEMES of the piece plus
   the named ENTITIES it is about (people, organizations, places).

## Stop conditions

- Stop respinning when evaluate returns PASS, or after {{RESPIN_PASSES}} passes,
  whichever comes first.
- Do NOT publish while any evaluate gate is still failing. A REVISE on any single
  dimension blocks publication.
