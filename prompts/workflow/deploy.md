# Deploy the live site (production push)

This publishes the whole static portal to the live website (elcensuradoweb.com, Cloudflare
Pages). It is outward-facing and irreversible in the sense that visitors see the result at
once, so treat it like the publish gate: confirm with the human before you push, unless you
were explicitly told to run unattended.

**Deploy ONCE, not per article.** This regenerates the entire site from the local DB and
pushes it, so a batch does NOT deploy after every piece. Publish all the articles first,
then run this a single time (its own `deploy` mode: `python3 cli/censurado.py step deploy
--mode deploy`). For a single on-demand article, one deploy at the end is fine.

Preconditions (best-effort: if either is missing, tell the human and STOP here rather than
failing the article, which is already safely published locally):

- `CLOUDFLARE_ACCOUNT_ID` is set in the harness `.env` (a host identifier, kept out of git).
- wrangler is authenticated once on this host (`wrangler login`); the deploy uses that
  OAuth session, no API token.

Then run the deploy from the harness repo:

    make deploy        # or: ./deploy/deploy-cdn.sh

It regenerates a fresh snapshot at the production page size (6), copies the media, writes
the root redirect and the cache headers, and runs `npx -y wrangler pages deploy dist
--project-name elcensuradoweb --branch main`. Node.js 20+ must be on the host for wrangler.

When wrangler prints the deployment URL, verify the live site answers (the deployment URL,
or https://elcensuradoweb.com/latest/) before you call it done. If the push fails, report
the wrangler error to the human; the article is already live locally regardless.
