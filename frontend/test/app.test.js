import "./setup.js";
import { test } from "node:test";
import assert from "node:assert/strict";
import { http, HttpResponse } from "msw";
import { screen, within } from "@testing-library/dom";
import userEvent from "@testing-library/user-event";
import { installServer, ORIGIN } from "./msw.js";
import { installDom } from "./dom.js";
import { mountApp } from "../src/main.js";

installDom();
const server = installServer();

// The handlers every mount needs: health, an empty roster, and a backend
// status. The app pings all three on mount, so a test that does not care about
// them still has to stub them (MSW is in onUnhandledRequest: "error" mode).
// `extra` is spread FIRST so a test-specific handler overrides the default for
// the same route (MSW matches earlier handlers first).
function baseHandlers(extra = []) {
  return [
    ...extra,
    http.get(`${ORIGIN}/api/health`, () => HttpResponse.json({ ok: true })),
    http.get(`${ORIGIN}/api/personas`, () => HttpResponse.json({ personas: [] })),
    http.get(`${ORIGIN}/api/portals`, () => HttpResponse.json({ portals: [], total: 0 })),
    http.get(`${ORIGIN}/api/runs`, () => HttpResponse.json({ runs: [], total: 0 })),
    http.get(`${ORIGIN}/api/status/backend`, () =>
      HttpResponse.json({
        backend_base_url: "http://backend.local",
        reachable: true,
        authorized: true,
        author_count: 0,
        status_code: 200,
        detail: "",
      })),
    ...editorialHandlers(),
  ];
}

// The Editorial tab loads style + lexicon + sourcing + location + versions on
// mount, so any mount has to stub all five (MSW is in onUnhandledRequest:
// "error" mode). The defaults model an empty newsroom: no published style yet
// (404 on style/lexicon/sourcing), an empty version history, and a blank
// location. A test that exercises the editor overrides these per route.
function editorialHandlers() {
  const notFound = () => HttpResponse.json({ code: "not_found", detail: "no active style" }, { status: 404 });
  return [
    http.get(`${ORIGIN}/api/editorial/style`, notFound),
    http.get(`${ORIGIN}/api/editorial/style/lexicon`, notFound),
    http.get(`${ORIGIN}/api/editorial/style/sourcing`, notFound),
    http.get(`${ORIGIN}/api/editorial/style/versions`, () => HttpResponse.json({ versions: [], total: 0 })),
    http.get(`${ORIGIN}/api/editorial/location`, () =>
      HttpResponse.json({
        region: "",
        ui_lang: "",
        language: "",
        gdelt_country: "",
        city: "",
        latlong: "",
        updated_at: "",
        gl: "",
        hl: "",
        ceid: "",
      })),
  ];
}

function mount() {
  const root = document.createElement("div");
  document.body.appendChild(root);
  return mountApp(root);
}

// Drive the whole app through its real entry point (mountApp) against the
// network mock: health turns online, the roster loads on the default Authors
// tab, and creating a persona refreshes the list. The job returns done on the
// first poll, so no real delay.
test("mounts the app, shows health, and refreshes the roster after a create", async () => {
  let created = false;
  server.use(
    http.get(`${ORIGIN}/api/health`, () => HttpResponse.json({ ok: true })),
    http.get(`${ORIGIN}/api/status/backend`, () =>
      HttpResponse.json({ backend_base_url: "http://backend.local", reachable: true, authorized: true, author_count: 0, status_code: 200, detail: "" })),
    http.get(`${ORIGIN}/api/personas`, () =>
      HttpResponse.json({
        personas: created
          ? [{ id: "ada-reporter", display_name: "Ada Reporter", beat: "world", who_i_am: "world desk" }]
          : [],
      })),
    http.get(`${ORIGIN}/api/portals`, () => HttpResponse.json({ portals: [], total: 0 })),
    http.get(`${ORIGIN}/api/runs`, () => HttpResponse.json({ runs: [], total: 0 })),
    http.post(`${ORIGIN}/api/personas`, () =>
      HttpResponse.json({ job_id: "j", persona_id: "ada-reporter", status: "pending" }, { status: 202 })),
    http.get(`${ORIGIN}/api/personas/jobs/j`, () => {
      created = true;
      return HttpResponse.json({ job_id: "j", status: "done", persona_id: "ada-reporter", error: "" });
    }),
    ...editorialHandlers(),
  );
  const user = userEvent.setup();
  mount();

  await screen.findByText("online");
  await screen.findByText(/no personas yet/i);

  // The default tab is Authors, and it is selected.
  assert.equal(screen.getByRole("tab", { name: /authors/i }).getAttribute("aria-selected"), "true");

  await user.type(screen.getByLabelText(/display name/i), "Ada Reporter");
  await user.selectOptions(screen.getByLabelText(/^beat$/i), "world");
  await user.type(screen.getByLabelText(/seed description/i), "covers the world desk");
  await user.click(screen.getByRole("button", { name: /synthesize persona/i }));

  await screen.findByText("Ada Reporter");
  // The roster actually replaced its empty state, not just appended.
  assert.equal(screen.queryByText(/no personas yet/i), null);
});

test("clicking the Runs tab reveals the runs panel and updates aria-selected", async () => {
  server.use(...baseHandlers());
  const user = userEvent.setup();
  mount();
  await screen.findByText("online");

  // Before selecting Runs, its Start-run control is hidden (role queries skip it).
  assert.equal(screen.queryByRole("button", { name: /start run/i }), null);

  const authorsTab = screen.getByRole("tab", { name: /authors/i });
  const runsTab = screen.getByRole("tab", { name: /runs/i });
  await user.click(runsTab);

  assert.equal(runsTab.getAttribute("aria-selected"), "true");
  assert.equal(authorsTab.getAttribute("aria-selected"), "false");
  // The runs panel is now shown, so its control is reachable.
  assert.ok(screen.getByRole("button", { name: /start run/i }), "the run trigger should be visible");
});

test("the Status tab renders backend status from /api/status/backend", async () => {
  server.use(
    ...baseHandlers([
      http.get(`${ORIGIN}/api/status/backend`, () =>
        HttpResponse.json({
          backend_base_url: "http://backend.local",
          reachable: true,
          authorized: false,
          author_count: 12,
          status_code: 403,
          detail: "missing api key",
        })),
    ]),
  );
  const user = userEvent.setup();
  mount();
  await screen.findByText("online");

  await user.click(screen.getByRole("tab", { name: /status/i }));

  const panel = screen.getByRole("tabpanel", { name: /status/i });
  await within(panel).findByText("http://backend.local");
  assert.ok(within(panel).getByText("missing api key"));
  assert.ok(within(panel).getByText("12"));
  assert.ok(within(panel).getByText("403"));
});

test("placeholder tabs show their heading", async () => {
  server.use(...baseHandlers());
  const user = userEvent.setup();
  mount();
  await screen.findByText("online");

  // Editorial is now a real editor; only Prompts is still a placeholder.
  for (const name of ["Prompts"]) {
    await user.click(screen.getByRole("tab", { name }));
    const heading = screen.getByRole("heading", { name });
    assert.ok(heading, `${name} heading should render`);
    assert.match(heading.closest(".panel").textContent, /built in a later step/i);
  }
});

test("the Editorial tab renders the style editor, not a placeholder", async () => {
  server.use(...baseHandlers());
  const user = userEvent.setup();
  mount();
  await screen.findByText("online");

  await user.click(screen.getByRole("tab", { name: /editorial/i }));
  const panel = screen.getByRole("tabpanel", { name: /editorial/i });
  // The five section headings are mounted, and the no-active-style affordance
  // resolved, so this is the real editor, not the stub.
  assert.ok(within(panel).getByRole("heading", { name: "Style" }));
  assert.ok(within(panel).getByRole("heading", { name: "Versions" }));
  assert.ok(within(panel).getByLabelText(/^voice$/i), "the style form is present");
  await within(panel).findByText(/no active style yet/i);
  assert.equal(within(panel).queryByText(/built in a later step/i), null);
});

test("the Sources tab renders the portals manager, not a placeholder", async () => {
  server.use(...baseHandlers());
  const user = userEvent.setup();
  mount();
  await screen.findByText("online");

  await user.click(screen.getByRole("tab", { name: /sources/i }));
  const panel = screen.getByRole("tabpanel", { name: /sources/i });
  // The create form is mounted (its domain field is reachable) and the list
  // resolved to its empty state, so this is the real panel, not the stub.
  assert.ok(within(panel).getByRole("heading", { name: "Add source" }));
  assert.ok(within(panel).getByLabelText(/^domain$/i), "the create form is present");
  await within(panel).findByText("No sources yet.");
  assert.equal(within(panel).queryByText(/built in a later step/i), null);
});
