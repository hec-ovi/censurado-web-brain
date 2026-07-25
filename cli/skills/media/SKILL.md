---
name: media
description: >-
  Art-direct and attach a hero illustration for an article, or upload a media file directly.
  Use when a piece needs a lead image: you write the brief, render it through ComfyUI, and
  attach it on publish or to a live article. Rendering is best-effort and needs the GPU lane.
---

# Art-direct the hero image

When a piece earns a hero, YOU are its art director: you brief ONE lead illustration, render
it, and attach it. Treat the image as a rule for tech/AI pieces (it lifts every page) and
optional elsewhere (reach for it on a lead, a literary piece, or a strong opinion column). A
piece publishes fine text-only, and a video or a quoted post can stand in for art.

This is the HERO (the image at the top of the article page). It is a SEPARATE slot from the
front-page CARD (the small listing preview), which you choose with `--card-type` at publish (see
write-article). A rendered hero can double as the card's image, but they are independent: setting
a hero no longer silently changes the card.

## Brief an illustration, not a photo
One image, top of the article, that reads as ART: stylized and conceptual (screen-print,
collage, ink-and-wash, painterly, isometric, surrealism), composed with symbols, objects,
metaphor, silhouettes, abstracted figures. Never the identifiable face of a named real person,
and never a real news event staged as if photographed. Match it to the SECTION and the author's
voice (`persona <id>`), so the picture could only belong to that story:

- **politics / investigation:** documentary and evidentiary, documents and the machinery of
  power laid bare, sober, never sensational.
- **mystery / conspiracy / satire:** occult and chiaroscuro, hidden hands, all-seeing eyes,
  esoteric symbols, dark surreal vintage register.
- **world / geopolitics:** maps, borders, weathered terrain, the somber old-world weight of
  nations.
- **tech / AI:** clean and futuristic, circuitry and silicon as landscape, neon accents, data
  made physical, legible not cluttered.
- **literature / fiction:** oneiric and dreamlike, soft surrealism.
- **anything else:** brief whatever the story's idea calls for.

## Write the prompt the way image models read
Order it: **subject -> action or arrangement -> style and medium -> context** (lighting,
setting, color, mood). Describe ONLY what you want; these models take no negative prompts, so
never write "no people" or "without text", describe the scene you want instead. Keep embedded
text to none or a few quoted words; the page's own typography carries the headline. Also write a
short one-line Spanish `alt` for accessibility. No length limit, write as much as the image needs.

An epígrafe or a source credit does NOT belong in the pixels either. If the hero needs one, pass
`--image-caption "<epígrafe>"` and/or `--image-credit "<Prensa Municipalidad de X>"` at `preview`
and the site renders them as text under the hero. Both are optional; omit them and nothing shows.

## Author portraits (a different job from a hero)

An author's profile picture follows ONE house recipe, and every author on the site uses it: a
head-and-shoulders portrait on a pure black background where the FACE IS NEVER READABLE. The
personas are fictional, so their pictures must not read as photographs of a real person. What
carries the image is the light, not the features:

- head and shoulders, centered, filling the frame, on a black studio void
- brief it as a SILHOUETTE LIT FROM BEHIND, never as a portrait lit from the front: the front
  of the face is a black shape with no readable eyes, nose or mouth, and the only light is a
  thin rim tracing the contour of the head and shoulders (a neon line in any single color, or
  a hard backlight glowing through the hair)
- dark clothing, low key, high contrast, cinematic, a single light source
- PORTRAIT orientation, not landscape: pass `--width 768 --height 1024`
- LOOK at what came back if you can (a terminal operator can open the file; an agent driving
  the MCP cannot see images at all, so it must brief dark enough that a legible face is
  impossible and have the human confirm the first portrait of a new author). A face that reads
  as a real person's photo is the one outcome to avoid, since the personas are openly
  fictional.

Vary the light color, the hair, the clothing, the age read and the posture per author so no
two authors look like the same picture, but keep the black void, the hidden face, and the
aspect. Example:

    python3 cli/censurado.py image \
      --prompt "backlit silhouette of a figure from the chest up on a pure black background, the
      front of the face in complete darkness with no visible features, a thin amber neon rim light
      tracing only the contour of the head and the shoulders, short dark hair, dark collared shirt,
      one light source behind the subject, low key, very high contrast, cinematic studio
      photography" \
      --alt "Retrato en penumbra de la autora" --width 768 --height 1024

Then attach it to the author (the render is NOT auto-attached to an author, only to an article):

    python3 cli/censurado.py edit-author <id> --set avatar=<the /media path it printed>

## Render and attach
    python3 cli/censurado.py image --prompt "<subject -> arrangement -> style -> context>" --alt "<one line in Spanish>"

It renders wide (landscape, ~16:9) to fill the hero band, uploads the bytes, and saves the hero
for you. Run the normal `preview` next and it attaches on its own. (To add art to a piece that is
already live, copy the full path the command printed: `python3 cli/censurado.py edit <slug> --meta
image=<full /media path> --meta image_alt="..."`.) A stable seed per prompt reproduces the same
image; pass `--seed` to vary it.

**Best-effort.** Rendering needs ComfyUI up on `:8188` (the GPU box). On the GPU-free lane `image`
prints `IMAGE SKIPPED` and exits cleanly; when it does, publish the piece text-only. To upload a
file you already have, use `python3 cli/censurado.py media <file>`.
