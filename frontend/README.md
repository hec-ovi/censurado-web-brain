# Frontend: the author-manager console

The presentation layer of censurado-web-brain. A small console for an operator to create and review author personas and to trigger runs, over the brain's HTTP API. It is an isolated layer: it holds no business logic, never imports brain code, and talks to the brain only through `/api`.

## Stack

Buildless. Plain HTML, CSS, and vanilla ES modules, served by nginx. No bundler, no framework, no build step. nginx serves the static app and reverse-proxies `/api` to the brain, so the browser makes same-origin calls and there is no CORS. The only dependencies are a dev test runner (`node --test` with jsdom, Testing Library, and MSW), and none of it ships in the image.

## What it talks to

It consumes exactly these brain endpoints, and nothing else:

- `GET /health`
- `GET /personas`, `GET /personas/{id}`
- `POST /personas` returns `202` and a job to poll at `GET /personas/jobs/{id}`
- `POST /runs` with `{ mode, n?, persona_ids? }` returns `202`, then `GET /runs/{id}`

Every call goes to `/api/...`, which nginx maps to the brain. That HTTP contract is the only coupling; swap the brain implementation and the console is unaffected as long as the contract holds.

## Layout

```
frontend/
  Dockerfile         nginx image: copy the static app and config, no node
  nginx.conf         serve /, reverse-proxy /api -> brain:8000
  src/
    index.html       loads the app (links the external bootstrap, no inline JS)
    bootstrap.js     browser entry point; kept external so a strict CSP applies
    main.js          mountApp(root): builds the layout and wires the components
    api.js           the brain client (fetch, error normalization)
    poll.js          poll-until helper for the 202-then-poll endpoints
    components/
      el.js          a tiny DOM builder and a labeled-field helper
      health.js      the GET /health status badge
      personaForm.js create a persona, then poll the synthesis job
      personaList.js the roster, with a beat filter and avatar fallback
      runPanel.js    start a run, poll it, render the assignment outcomes
    styles.css       one stylesheet, themeable through CSS variables
  test/              node --test suites (jsdom + Testing Library + MSW)
```

## Run

The image is buildless, so the build just copies the static files and the nginx config:

```
docker build -t brain-frontend frontend
```

It expects a `brain` service reachable at `brain:8000` (the reverse-proxy target in `nginx.conf`). The compose that runs the console next to the brain is in `../deploy`: `cd deploy && cp .env.example .env && docker compose up --build` serves the console on `http://localhost:8080`.

## Test

```
cd frontend
npm install
npm test
```

Each test renders a component (or the whole app through `mountApp`), drives it with real user interaction via `@testing-library/user-event`, and mocks the brain at the network layer with MSW. Queries go by role, label, and text. Invalid states are covered too: empty-form validation, a failed synthesis job, a rejected create, list and run errors. A simulated DOM (jsdom) is enough, so no browser is needed.

## Security

nginx serves a Content-Security-Policy (`script-src 'self'`, no inline script, which is why the page boots from an external `bootstrap.js`), plus `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`, hides its version (`server_tokens off`), and caps request bodies. The console has no auth of its own and the API it proxies has state-changing POSTs, so run it on an internal network or put an auth layer in front of both `/` and `/api` before exposing it.

## Theming

Colors and spacing are CSS custom properties at the top of `styles.css`. A theme is a variable swap, not a rewrite. The default is a dark console palette; the markup carries no inline colors.
