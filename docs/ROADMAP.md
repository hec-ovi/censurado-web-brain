# Roadmap / pending features

Backlog of agreed-but-not-built work. Each entry is a contract sketch, not a final
design; point an implementing agent at it.

## Persona avatar at creation (console)

When an operator creates a persona in the newsroom console, the form must require an
avatar, with two ways to supply it:

1. **Upload an image.** A file input on the create-persona form. The console proxies
   the bytes to the platform image store (`POST /media`, the same content-addressed
   endpoint hero images use), gets back `/media/<sha256>.<ext>`, and stores it as the
   persona's `avatar_path`.
2. **Generate a portrait.** A "generate portrait" option with a prompt field. The art
   director renders a portrait on the local ComfyUI (reuse `newsroom/imagery`:
   `build_graph` + `ComfyClient`, a portrait-oriented prompt, e.g. square 1024x1024),
   uploads the PNG via `newsroom/publish/media.upload_media`, and stores the returned
   URL as `avatar_path`. The default prompt can be derived from the persona's
   `who_i_am` / `about` so the operator can accept or edit it.

### Why
`Persona.avatar_path` already exists and already flows to the public site: the
article publish path stamps `metadata.author_avatar` from it, and the portal renders
it on each author page and the `/about/` page. Today nothing populates `avatar_path`
(synthesis leaves it empty and the console has no avatar field), so avatars must be
set out of band. This feature closes that gap at the point of creation.

### Wire-up notes
- `Persona.avatar_path` is a mutable `TEXT` field; `PersonaStore.update(id, avatar_path=...)`
  already supports setting it.
- There is currently NO HTTP endpoint to set a persona's avatar: `POST /personas`
  does not accept one, and there is no update route. Add either an avatar argument to
  the create flow or a small `PATCH /personas/{id}` (avatar only), plus a media-upload
  proxy on the console (it already proxies `/media` for hero-image previews).
- Validation: enforce that a created persona ends with a non-empty `avatar_path`
  (required), whichever path was used.
