# Institutional lane: the unsigned house byline

This is the LIGHTER workflow for an institutional or unsigned piece: an official government
or municipal release, a "gacetilla", a formal notice. It is NOT written in a fictional
persona's voice and carries NO personal byline. It is signed by the newsroom itself and
reads in the most formal, objective register.

Do NOT load a persona voice here. There is one reserved institutional byline, and every
piece in this lane is signed by it.

## Ensure the institutional author exists

The house byline is the author handle `redaccion`, display name "Redacción". List the
registry and check it is there:

    python3 cli/censurado.py personas

If `redaccion` is NOT listed, create it ONCE (it is reused for every institutional piece
after that). It carries a FORMAL house style and a neutral, empty persona, no opinion and no
voice of its own. Write its record to a file and persist it:

    {
      "id": "redaccion",
      "display_name": "Redacción",
      "beat": "actualidad",
      "who_i_am": "Soy la redacción institucional de Censurado. No tengo voz personal ni opinión: informo hechos oficiales en tercera persona, con registro formal y neutral, y atribuyo cada dato a su fuente.",
      "style": "Registro formal y objetivo. Tercera persona siempre, nunca primera. Sin idiosincrasia de autor, sin opinión, sin humor. Oraciones claras y verbos finitos, con el mínimo de gerundios. Cada afirmación factica atribuida a su fuente.",
      "about": "Redacción de Censurado. Firma institucional para comunicados y notas oficiales sin autor personal."
    }

    python3 cli/censurado.py create-author --file <path-to-that-json>

Sign the piece with `--author redaccion` at preview: the byline "Redacción" and its bio fill
from this record. Do NOT relax or omit the author field. The piece IS signed, by the
institution rather than by a person.

## Confirm the provided image, the source, and the section

Institutional pieces use a PROVIDED picture, never an AI-generated hero: this lane NEVER runs
the `image` (ComfyUI) verb. Confirm with the human before you write:

- the supplied image, a `/media/...` path you pass later as `--image` (upload it first with
  `python3 cli/censurado.py media <file>` if it is not on the server yet, then use the
  returned path);
- optionally an `--image-caption` (the epígrafe the site renders as text under the image) and
  an `--image-credit` (e.g. `Prensa Municipalidad de X`); both are optional;
- the official source (the release, the bulletin, the notice) and the topic it covers;
- the section the piece files under. Pass `--section` at preview; `redaccion`'s default beat
  is only a fallback when you give none.

Hold the image path, its caption and credit, the official source, and the section for the
rest of the walk. Then move on to ground the official facts.
