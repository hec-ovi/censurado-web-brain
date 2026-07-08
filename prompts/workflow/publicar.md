# Publicar: go live to production

This publishes the whole static portal to the live website (elcensuradoweb.com, Cloudflare
Pages). It is outward-facing and irreversible in the sense that visitors see the result at
once, so treat it like the last gate: confirm with the human before you publish, unless you
were explicitly told to run unattended. "Publicar" / "publish" / "go live" all mean THIS; the
local `preview` (localhost, debug) never reaches the public.

**Publish ONCE, not per article.** This regenerates the entire site from the local DB and
pushes it, so a batch does NOT publish after every piece. Preview all the articles first,
then run this a single time (its own `publicar` mode: `python3 cli/censurado.py step
--mode publicar`). For a single article (a `single-article` walk), one publish at the end is fine.

Preconditions (best-effort: if either is missing, tell the human and STOP here rather than
failing the article, which is already safely previewed locally):

- `CLOUDFLARE_ACCOUNT_ID` is set in this repo's `.env` (a host identifier, kept out of git).
- wrangler is authenticated once on this host (`wrangler login`); the publish uses that
  OAuth session, no API token.

Then publish with the verb (never a `.sh` script or a `make` target):

    python3 cli/censurado.py publicar --yes

It regenerates a fresh snapshot at the production page size, copies the media, writes the root
redirect and the cache headers, and pushes to Cloudflare Pages on branch `main`. The `--yes`
is required because this is the one public, irreversible action.

When the verb prints the public origin, verify the live site answers (that origin, or
https://elcensuradoweb.com/latest/) before you call it done, with plain `status`. If the push
fails, relay the verb's exact message to the human; the article is already live locally regardless.
