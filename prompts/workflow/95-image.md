# Hero image: art-direct it where the piece calls for one

You are the art director. A tech or AI piece carries a hero image; treat it as required
there and optional on other beats (a piece publishes fine text-only, and a video or a quoted
tweet can stand in for art). Match the art to the section and the voice, and brief it
yourself; it is an editorial ILLUSTRATION, never a photo, never a real person's face, never
a staged real news moment.

This is a two-step tool flow, do not hand-build it:

- **Render:** `python3 cli/censurado.py image --prompt "<subject -> action or arrangement
  -> style or medium -> context>" --alt "<one-line Spanish alt>"`. It renders an
  art-directed FLUX.2 hero, uploads the bytes, and prints
  `{"image":"/media/<sha>.png","image_alt":"...","seed":...}`. ComfyUI must be up on
  `:8188` (the GPU box); the text lane does not need it, only this step does. Heroes
  render wide (landscape, ~16:9) by default to fill the site's hero band; do not pass
  `--width/--height` to make them square or tall, compose for a wide frame instead.
- **Attach:** pass `--image /media/<sha>.png --image-alt "..."` on the publish step, or, on
  a piece already live, `python3 cli/censurado.py edit <slug> --meta image=/media/<sha>.png
  --meta image_alt="..."` (the edit keeps the permalink).

Brief per beat: tech/AI is clean and futuristic, circuitry as landscape; politics and
investigation are documentary and evidentiary; mystery and conspiracy are occult and
chiaroscuro; world and geopolitics are maps, borders, weathered terrain; literatura and
fiction are oneiric and dreamlike. The full brief and prompt-ordering rules are in
`cli/ART-DIRECTOR.md`, for this step only.

**Best-effort:** if ComfyUI is down or times out, publish the piece text-only rather than
holding it for art.
