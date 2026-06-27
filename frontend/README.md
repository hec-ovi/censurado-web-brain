# Frontend: the operator console

The presentation layer of censurado-web-brain. A single console where an operator runs the whole newsroom over the brain's HTTP API: authors, sources, runs, editorial config, and the prompt templates. It is an isolated layer: it holds no business logic, never imports brain code, and talks to the brain only through `/api`.

## Stack

Buildless. Plain HTML, CSS, and vanilla ES modules, served by nginx. No bundler, no framework, no build step. nginx serves the static app and reverse-proxies `/api` to the brain, so the browser makes same-origin calls and there is no CORS. The only dependencies are a dev test runner (`node --test` with jsdom, Testing Library, and MSW), and none of it ships in the image.

## The six tabs

The app is one ARIA tablist. Switching a tab toggles `hidden` (it hides, never unmounts), so controls stay in the DOM for assistive tech and tests. Each section carries a small `(?)` help marker explaining what it does.

- **Authors**: synthesize a persona from a seed, then edit any author (voice fields, beat, the few-shot example lists), delete one, or link it to its source pool. An author only researches its linked sources, and cross-source corroboration across them drives relevance.
- **Sources**: the portal registry. Create, edit, enable/disable, or delete a source. `ownership_group` is first-class because co-owned mastheads collapse to one independent source for the corroboration gate.
- **Runs**: the three triggers (Full manager over all authors; Author batch scoped to chosen authors with a managed/express/manual sub-mode; Single article from a link, which bypasses the manager), plus a run history with a status filter and per-run assignment drill-in.
- **Editorial**: the house style, versioned. Edit voice/exemplars/rules/structure (each edit publishes a new immutable version), the lexicon gate, the sourcing floor (`min_sources`), and the location. Promote any past version to roll back.
- **Prompts**: edit the brain's stage instructions (the journalist/manager/persona/art-director templates). Pick a template, edit the body, publish a new version, revert from history. `{{TOKEN}}` placeholders are substituted at runtime and must stay.
- **Status**: the brain health and a probe of the publishing backend it talks to.

## What it talks to

Every call goes to `/api/...`, which nginx maps to the brain. The client (`api.js`) covers the brain's persona, portal, run (incl. the direct from-link path), editorial (style/lexicon/sourcing/versions/location), status, and prompt endpoints. That HTTP contract is the only coupling.

## Layout

```
frontend/
  Dockerfile         nginx image: copy the static app and config, no node
  nginx.conf         serve /, reverse-proxy /api -> brain:8000
  src/
    index.html       loads the app (links the external bootstrap, no inline JS)
    bootstrap.js     browser entry point; kept external so a strict CSP applies
    main.js          mountApp(root): builds the tab shell and wires the components
    api.js           the brain client (fetch, error normalization)
    poll.js          poll-until helper for the 202-then-poll endpoints
    components/
      el.js          a tiny DOM builder, a labeled-field helper, and the (?) help marker
      health.js      the GET /health status badge
      backendStatus.js  the GET /status/backend diagnostics panel
      personaForm.js create a persona, then poll the synthesis job
      personaList.js the roster: beat filter, per-author edit/delete/source-linking
      sourcesPanel.js the portal registry CRUD
      runPanel.js    the three triggers + run history
      editorialPanel.js style/lexicon/sourcing/location + version history
      promptsPanel.js the prompt-template editor + versions
    styles.css       one stylesheet, themeable through CSS variables
  test/              node --test suites (jsdom + Testing Library + MSW)
```

## Run

The image is buildless, so the build just copies the static files and the nginx config:

```
docker build -t brain-frontend frontend
```

It expects a `brain` service reachable at `brain:8000` (the reverse-proxy target in `nginx.conf`). The compose that runs the console next to the brain is in `../deploy`: `cd deploy && cp .env.example .env && docker compose up --build`.

## Test

```
cd frontend
npm install
npm test
```

Each test renders a component (or the whole app through `mountApp`), drives it with real user interaction via `@testing-library/user-event`, and mocks the brain at the network layer with MSW. Queries go by role, label, and text. Invalid states are covered: empty-form validation, a failed synthesis job, rejected writes, list/run errors, a wrong-key publish race, and the cold-start where no style is published yet. A simulated DOM (jsdom) is enough, so no browser is needed.

## Security

nginx serves a Content-Security-Policy (`script-src 'self'`, no inline script, which is why the page boots from an external `bootstrap.js`), plus `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`, hides its version (`server_tokens off`), and caps request bodies. The console has no auth of its own and the API it proxies has state-changing POSTs, so run it on an internal network or put an auth layer in front of both `/` and `/api` before exposing it.

## Theming

Colors and spacing are CSS custom properties at the top of `styles.css`. A theme is a variable swap, not a rewrite. The default is a dark console palette; the markup carries no inline colors.
