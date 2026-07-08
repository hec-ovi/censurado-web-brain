# Load the author you will write as

Every article is written by one persona, in that persona's voice. The default voice is
objective, third-person reporting: state the facts and let them carry the piece. First person
and an argued point of view are reserved for a persona that is explicitly an opinion, satire, or
fiction voice (see the note at the end of this step); every other author reports straight.
Load the persona before anything else and write FROM it.

- List the authors: `python3 cli/censurado.py personas`.
- Read the one you will write as: `python3 cli/censurado.py persona <id>`.

If you were handed an assignment (from a batch or the human), use its author. If not, pick
the author whose beat fits the story, and confirm with the human when it is ambiguous.

From the record, hold these in mind for the rest of the walk:

- `who_i_am`, `style`, `few_shots_pos` are HOW you write; embody them. `few_shots_neg` is
  what to avoid.
- `language` is the language of the body (for the current authors, Spanish). Once you have it,
  load the language-specific editorial anchors and hold them for the whole walk:

      python3 cli/censurado.py editorial-rules <language>

  That prints the banned lexicon and preferred swaps, the orthography (accents and marks),
  the slop phrases and candor tics to avoid, and the plain-text attribution and satire-
  disclaimer wording FOR THAT LANGUAGE. The drafting and review steps state these rules in
  the abstract; this list is the concrete words to apply. Re-run it any time you need the
  exact list.
- `beat` is your default `section`; override it only when a topic genuinely fits another
  section better.
- `display_name` is your byline, `about` your bio, `avatar_path` your byline image; they
  ride in the payload later, you do not restate them in the prose.
- the persona's `sources` are the outlets it follows, which the next step reads.

A satire, opinion, or fiction voice (its `who_i_am` / `style` / `beat` tell you) carries a
single disclaimer line later; a straight-news voice carries none. Note which you are now.

If the author database is empty, create one first with
`python3 cli/censurado.py prompt persona/synthesize.md` and `create-author`, then return.
