---
name: publicar
description: >-
  Publish the local portal snapshot to the LIVE public site (elcensuradoweb.com, on Cloudflare
  Pages). This is what "publicar" / "publish" / "go live" mean: the one irreversible, public,
  outward-facing action. Use it whenever the user asks to publish, go live, or put a piece on the
  real internet. Always show what will go live and get a yes first. `preview` is the SEPARATE,
  local, debug-only staging on localhost; it never reaches the public.
---

# Publicar (go live to production)

`preview` stages a piece to the LOCAL site, whose link it prints, and which only you and the user can
see, and it is DEBUG-only: it never touches the public internet. `publicar` is the DIFFERENT,
public action: it publishes to the open internet at `elcensuradoweb.com`. Treat the two as
separate steps with different stakes. When the user says "publicá" / "publish" / "go live",
they mean `publicar`, never the local preview.

## Confirm first, every time
`publicar` publishes the WHOLE current snapshot of the local database (every previewed article and
the current front page), not just the last piece you wrote. So before you publish:
1. Show the user what will go live: the current front page and any new pieces.
2. Get an explicit yes. Never publish on your own unless the user told you to run unattended.

## One-time setup (a human does this, not you)
- `wrangler login` once (Cloudflare OAuth, no API token needed).
- `CLOUDFLARE_ACCOUNT_ID` in `.env` (the account id from the dashboard, a host identifier).
- The article like/dislike bar is optional. Leave BOTH `D1_REACTIONS_ID` and `REACTIONS_SALT`
  unset and the publish still succeeds; the bar just stays hidden. But if you set
  `D1_REACTIONS_ID` you MUST also set a valid `REACTIONS_SALT` (chars `[A-Za-z0-9_-]` only, mint
  one with `openssl rand -hex 16` and keep it stable), or it hard-fails: a half-set bar
  would store enumerable voter keys, so the script refuses it.

## Run it
    python3 cli/censurado.py publicar --yes

`publicar` is a `censurado.py` verb like every other action: you never run a `.sh` script or a
`make` target. The verb wraps the infra step: it regenerates a fresh site snapshot from the local
DB at the production page size, copies the media, writes the root redirect and the cache headers,
regenerates `wrangler.toml` (the reactions D1 binding) from `.env`, and runs `wrangler pages
deploy` to the Pages project on branch `main`. The `--yes` is required because this is the one
public, irreversible action. The cache policy (pages refetch with `no-store`, hashed media is
immutable) is in `deploy/CACHING.md`.

## Verify it landed
On success the verb prints the public origin. Confirm it is serving with plain `status`, never by
publishing again or opening the generator:

    python3 cli/censurado.py status

To confirm a SPECIFIC piece went public, open its link: the same permalink `preview` printed for
it, on `elcensuradoweb.com`. Hand the human that link.

## When it stops
The verb relays the script's `FATAL:` line and exits non-zero, before anything public changes,
in two cases: `CLOUDFLARE_ACCOUNT_ID` unset, or `D1_REACTIONS_ID` set without a valid
`REACTIONS_SALT`. A missing `wrangler login` surfaces later as wrangler's own error at the final
step (the snapshot was already rebuilt by then, which is harmless). Either way, DO NOT work around
it: do not run `init-perms`, `chmod`, `generate`, or read the generator source. Relay the exact
message to the user, let them complete that one setup step, then re-run `python3 cli/censurado.py
publicar --yes`. The local preview is untouched, so a failed publish changes nothing that is live.
