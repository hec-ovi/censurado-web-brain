# Re-spin: self-revise against the evaluation

This is your article, in your voice, and you are revising it yourself. Address every
REVISE note from the evaluation. Keep it in your voice; do not hand it to a generic editor
tone.

You get up to {{RESPIN_PASSES}} self-revision passes. Each pass keeps every grounded fact
and every citation and changes only form, wording, and structure. Interrogate the draft
honestly:

- What is repeated across the sections? Cut it.
- Which sources are noise, off-topic, or a duplicate of another? Use the signal, drop the
  rest. Two outlets in one group are ONE confirmation, never two; do not pad by citing the
  same fact from near-identical sources.
- Where is the wording loose or repeated? Tighten and vary it.
- Any AI-slop cliché, throwaway "como si" metaphor, hollow hedge, padding, three
  adjectives for a fact, a closing line that restates the opening? Remove every one.
  Same for an em or en dash, a "no es X, es Y" inversion, an aphorism built on a
  negation, a candor tic ("la verdad es que"), or a thing given feelings or will:
  remove every one.
- Does each layer stay dense and distinct, and does the piece still read as a senior
  professional wrote it?

After a pass, run the evaluation again (`python3 cli/censurado.py step 60-evaluate --mode
<mode>`). Stop the moment it returns PASS, or when you have used all {{RESPIN_PASSES}}
passes, whichever comes first. When you stop, continue to the NEXT step below.
