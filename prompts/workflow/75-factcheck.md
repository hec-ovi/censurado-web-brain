# Fact-check: fix the grounding

Work plainly here, not in a persona voice; this pass is grounding, not style. Correct any
remaining grounding problems against your ledger, and change nothing else:

- Remove or replace any citation that is not in your ledger. The ledger sources are the
  only ones the article may cite.
- Every NAMED media outlet must be one of the author's ASSIGNED sources (`persona <id>` /
  `sources <id>`). Strip any named outlet that is not assigned but KEEP its fact: reattribute it
  to the primary actor behind the news (the person, company, official, institution, or document)
  or drop the medium and state the fact plainly. If the author has no assigned sources, no media
  outlet is named, only primary actors.
- Cite sources by name as plain text (use the attribution form `editorial-rules` printed),
  never as links. Remove every link in the body, keeping only its text. Images
  `![](/media/...)` and `{{...}}` widgets are not links.
- Resolve every unresolved marker (TODO, FIXME, TK, "citation needed") by grounding the
  claim in a ledger source or removing the claim.
- Keep every date EXACTLY as the sources have it; never invent a date or shift a year.
- Restore any altered proper name to its exact spelling in the sources.

Make no other changes. The result is the same article, correctly grounded.
