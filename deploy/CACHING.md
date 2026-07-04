# Cache policy

The generator emits assets (`/assets/style.css`, `/assets/app.js`) at stable URLs
with no content hash, on purpose: a constant href keeps every sealed HTML page
byte-identical when only the asset bytes change, so the CDN never re-uploads pages
it already has. The trade-off is that the browser cannot treat those URLs as
immutable (the bytes behind them do change on a redeploy), so it must revalidate.

Two surfaces serve the same policy:

| Path | Cache-Control | Why |
| --- | --- | --- |
| `/media/*` | `public, max-age=31536000, immutable` | Content-addressed (`/media/<sha256>.<ext>`); the filename changes when the bytes do, so it is safe to cache for a year. |
| everything else (HTML, JSON shards, `/assets/*.css`, `/assets/*.js`) | `no-store` | Stable URL, mutable bytes. `no-store` forces a fresh fetch every time. |

`no-store`, not `no-cache`, on purpose. iOS WebKit (Safari, and Brave/Chrome on iOS,
which are all WebKit) does not reliably revalidate a `no-cache` resource on reload: it
serves a stale disk copy, so a freshly published or edited article stayed frozen on an
iPhone even after a manual pull-to-refresh, and the poller that shows the "Actualizar"
button never saw the new `version.json`. `no-store` is the only directive iOS honors.
Desktop was always fine (it revalidates `no-cache` correctly), which is why the bug was
mobile-only. The cost is one extra round trip per HTML/JSON load, negligible for a news
portal.

The client also cache-busts the freshness-critical fetches (the `version.json` sentinel
and the newest shard) with a per-request query and `cache: "no-store"` (see `app.js`
`bust()` / `LiveRefresh`), because iOS WebKit's `fetch()` cache handling is itself
unreliable; a distinct URL sidesteps its disk cache entirely.

Without a fresh-fetch rule the browser also served a stale `style.css` against fresh
markup after a deploy: the symptom was the tweet card rendering with giant icons and an
oversized avatar until a manual hard reload.

## Where it is set

- Local stack: `nginx/site.conf` (the `site` service). `/media/` gets the immutable
  header; the root `location /` adds `Cache-Control: no-store`.
- Production (Cloudflare Pages): `deploy/deploy-cdn.sh` (run it with `make deploy`)
  writes `dist/_headers` with the same two rules. Pages defaults CSS to `max-age=14400`
  (4h), so the `_headers` override is what prevents a 4-hour stale-CSS window after each
  deploy.
