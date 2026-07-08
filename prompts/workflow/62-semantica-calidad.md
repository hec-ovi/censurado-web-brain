# Fix the semantics and improve the quality (the single combined pass)

This is the ONE combined revision pass for the institutional lane: fix the semantics and
improve the quality in a single read. It replaces the heavier evaluate + respin + enrich loop
of the signed workflow, so do the work of all three here, once, and carry a clean draft
forward. This is still the publish gate: do not advance a piece while any of the checks below
still fails.

## Fix the semantics

- **Accuracy against the sources.** Every factual claim matches the official source and the
  ledger line behind it: names, titles, offices, dates, and figures are exact and named as plain
  text ("según X"), with no links in the body: remove every link (keep only its text). Correct any
  claim that drifts from its source; cut any claim no ledger line supports. Name a media outlet
  only when it is one of the byline's ASSIGNED sources; a web-found outlet is never named. The
  house byline carries no assigned sources, so it names no medium, only the primary actor (the
  official source itself is a primary actor, not a medium).
- **Meaning is unambiguous.** Fix wording that is unclear, that could be read two ways, or
  that misstates what the source actually said. The reader must take away exactly what the
  official act means, no more and no less.
- **Register holds.** The piece stays third person, objective, and neutral, with minimal
  gerunds (rewrite `-ando` / `-iendo` to finite verbs). No opinion or slant crept in.

## Improve the quality

- **Clarity and flow.** Tighten every line so it earns its place: high information per word,
  no filler, no coasting layer. Improve structure where it genuinely helps the reader (a
  subheading, a short list) without changing the meaning or adding a fact.
- **No redundancy.** No layer restates an earlier one, no wording repeats, and the close does
  not restate the opening. Say each idea once.
- **House format.** Full Spanish orthography: accents (á, é, í, ó, ú, ü), the ñ, and the
  opening marks ¿ and ¡. Clean Markdown (well-formed headings, lists, and links; each widget
  marker on its own line). No em or en dash, no vetoed words, no AI-slop tell.

Add no new factual claim and no source outside the ledger. There is no length limit; shorten
only by removing noise, never by dropping substance. When every check passes, the piece is
clean; carry it to finalize.
