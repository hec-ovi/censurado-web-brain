---
name: deploy
description: >-
  Push the local portal snapshot to the LIVE public site (elcensuradoweb.com, on Cloudflare
  Pages). Use ONLY when the user explicitly asks to go live, publish to production, or deploy.
  This is the one irreversible, public, outward-facing action, so always show what will go live
  and get a yes first. Everything else stays on the private local preview.
---

# Deploy to production (go public)

`preview` stages a piece to the LOCAL site at `localhost:8080`, which only you and the user can
see. Deploy is a DIFFERENT, public action: it publishes to the open internet at
`elcensuradoweb.com`. Treat the two as separate steps with different stakes.

## Confirm first, every time
Deploy publishes the WHOLE current snapshot of the local database (every published article and
the current front page), not just the last piece you wrote. So before deploying:
1. Show the user what will go live: the current front page and any new pieces.
2. Get an explicit yes. Never deploy on your own unless the user told you to run unattended.

## One-time setup (a human does this, not you)
- `wrangler login` once (Cloudflare OAuth, no API token needed).
- `CLOUDFLARE_ACCOUNT_ID` in `.env` (the account id from the dashboard, a host identifier).
- The article like/dislike bar is optional. Leave BOTH `D1_REACTIONS_ID` and `REACTIONS_SALT`
  unset and the deploy still succeeds; the bar just stays hidden. But if you set
  `D1_REACTIONS_ID` you MUST also set a valid `REACTIONS_SALT` (chars `[A-Za-z0-9_-]` only, mint
  one with `openssl rand -hex 16` and keep it stable), or the deploy hard-fails: a half-set bar
  would store enumerable voter keys, so the script refuses it.

## Run it
    python3 cli/censurado.py deploy --yes

Deploy is a `censurado.py` verb like every other action: you never run a `.sh` script or a
`make` target. The verb wraps the infra step (`deploy/deploy-cdn.sh`): it regenerates a fresh
site snapshot from the local DB at the production page size, copies the media, writes the root
redirect and the cache headers, regenerates `wrangler.toml` (the reactions D1 binding) from
`.env`, and runs `wrangler pages deploy` to the Pages project on branch `main`. The `--yes` is
required because this is the one public, irreversible action. The cache policy (pages refetch
with `no-store`, hashed media is immutable) is in `deploy/CACHING.md`.

## Verify the deploy landed
On success the verb prints the public origin. Confirm the site (and a specific piece) is
actually live with a VERB, never by deploying again or opening the generator:

    python3 cli/censurado.py status --slug <slug>

That reports whether `elcensuradoweb.com` is serving and whether that article's public
permalink returns 200. Hand the human the link it prints.

## When it stops
The verb relays the script's `FATAL:` line and exits non-zero, before anything public changes,
in two cases: `CLOUDFLARE_ACCOUNT_ID` unset, or `D1_REACTIONS_ID` set without a valid
`REACTIONS_SALT`. A missing `wrangler login` surfaces later as wrangler's own error at the final
deploy step (the snapshot was already rebuilt by then, which is harmless). Either way, DO NOT
work around it: do not run `init-perms`, `chmod`, `generate`, or read the deploy/generator
source. Relay the exact message to the user, let them complete that one setup step, then re-run
`python3 cli/censurado.py deploy --yes`. The local preview is untouched, so a failed deploy
changes nothing that is live.
