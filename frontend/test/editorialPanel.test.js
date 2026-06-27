import "./setup.js";
import { test } from "node:test";
import assert from "node:assert/strict";
import { http, HttpResponse } from "msw";
import { screen, within, waitFor } from "@testing-library/dom";
import userEvent from "@testing-library/user-event";
import { installServer, ORIGIN } from "./msw.js";
import { installDom } from "./dom.js";
import { EditorialPanel } from "../src/components/editorialPanel.js";
import { api } from "../src/api.js";

installDom();
const server = installServer();

// A full StyleGuideOut, so the style + versions sections have every field they
// render. The lexicon and sourcing dicts are embedded (the publish path carries
// them forward). Overrides let a test tweak version/is_active without restating.
function styleOut(overrides = {}) {
  return {
    version: 3,
    voice: "Plain and direct.",
    exemplars: ["Lead with the fact."],
    rules: ["No adjectives in the lede."],
    lexicon: { banned_terms: ["clearly"], preferred_swaps: { utilize: "use" } },
    sourcing: { min_sources: 2, require_attribution: true, no_fabricated_quotes: true },
    structure: { sections: ["lede", "body"] },
    created_by: "editor",
    created_at: "2026-06-01T00:00:00Z",
    is_active: true,
    ...overrides,
  };
}

function locationOut(overrides = {}) {
  return {
    region: "US",
    ui_lang: "en",
    language: "en",
    gdelt_country: "US",
    city: "New York",
    latlong: "40.7,-74.0",
    updated_at: "2026-06-01T00:00:00Z",
    gl: "US",
    hl: "en",
    ceid: "US:en",
    ...overrides,
  };
}

function notFound() {
  return HttpResponse.json({ code: "not_found", detail: "no active style" }, { status: 404 });
}

// The five GETs reload() fires, all resolving against an active style. A test
// places its own route handler BEFORE these so it overrides the default (MSW
// matches earlier handlers first).
function edGets() {
  const s = styleOut();
  return [
    http.get(`${ORIGIN}/api/editorial/style`, () => HttpResponse.json(s)),
    http.get(`${ORIGIN}/api/editorial/style/lexicon`, () => HttpResponse.json(s.lexicon)),
    http.get(`${ORIGIN}/api/editorial/style/sourcing`, () => HttpResponse.json(s.sourcing)),
    http.get(`${ORIGIN}/api/editorial/location`, () => HttpResponse.json(locationOut())),
    http.get(`${ORIGIN}/api/editorial/style/versions`, () => HttpResponse.json({ versions: [s], total: 1 })),
  ];
}

function mount() {
  const panel = EditorialPanel({ api });
  document.body.appendChild(panel.element);
  return { panel };
}

test("the style section loads the active version and prefills the fields", async () => {
  server.use(...edGets());
  const { panel } = mount();
  await panel.reload();

  await screen.findByText(/active version 3/i);
  assert.equal(screen.getByLabelText(/^voice$/i).value, "Plain and direct.");
  // Exact label: a regex like /exemplars/i would also match the (?) help span,
  // whose aria-label sentence mentions "Exemplars".
  assert.match(screen.getByLabelText("Exemplars (JSON array)").value, /Lead with the fact/);
});

test("publishing a new version POSTs the voice and carries the existing lexicon and sourcing", async () => {
  let posted = null;
  server.use(
    http.post(`${ORIGIN}/api/editorial/style`, async ({ request }) => {
      posted = await request.json();
      return HttpResponse.json(styleOut({ version: 4 }), { status: 201 });
    }),
    ...edGets(),
  );
  const user = userEvent.setup();
  const { panel } = mount();
  await panel.reload();
  await screen.findByText(/active version 3/i);

  const voice = screen.getByLabelText(/^voice$/i);
  await user.clear(voice);
  await user.type(voice, "New voice.");
  await user.click(screen.getByRole("button", { name: /publish new version/i }));

  await waitFor(() => assert.ok(posted, "a POST should have been sent"));
  assert.equal(posted.voice, "New voice.");
  assert.equal(posted.activate, true);
  // The active version's lexicon + sourcing ride along, not wiped.
  assert.deepEqual(posted.lexicon, { banned_terms: ["clearly"], preferred_swaps: { utilize: "use" } });
  assert.deepEqual(posted.sourcing, { min_sources: 2, require_attribution: true, no_fabricated_quotes: true });
});

test("a bad JSON exemplars field aborts the publish before any POST", async () => {
  let posted = false;
  server.use(
    http.post(`${ORIGIN}/api/editorial/style`, () => {
      posted = true;
      return HttpResponse.json(styleOut(), { status: 201 });
    }),
    ...edGets(),
  );
  const user = userEvent.setup();
  const { panel } = mount();
  await panel.reload();
  await screen.findByText(/active version 3/i);

  const exemplars = screen.getByLabelText("Exemplars (JSON array)");
  await user.clear(exemplars);
  await user.type(exemplars, "nope");
  await user.click(screen.getByRole("button", { name: /publish new version/i }));

  assert.equal(posted, false, "no POST should be sent for invalid JSON");
  await screen.findByText(/exemplars must be valid json/i);
});

test("saving the lexicon PUTs newline-split banned terms and the swaps object", async () => {
  let put = null;
  server.use(
    http.put(`${ORIGIN}/api/editorial/style/lexicon`, async ({ request }) => {
      put = await request.json();
      return HttpResponse.json({ ok: true });
    }),
    ...edGets(),
  );
  const user = userEvent.setup();
  const { panel } = mount();
  await panel.reload();

  const banned = await screen.findByLabelText("Banned terms (one per line)");
  await user.clear(banned);
  await user.type(banned, "foo{Enter}bar{Enter}");
  await user.click(screen.getByRole("button", { name: /save lexicon/i }));

  await waitFor(() => assert.ok(put, "a PUT should have been sent"));
  assert.deepEqual(put.banned_terms, ["foo", "bar"]);
  assert.deepEqual(put.preferred_swaps, { utilize: "use" });
});

test("saving sourcing sends min_sources null when the field is blank", async () => {
  let put = null;
  server.use(
    http.put(`${ORIGIN}/api/editorial/style/sourcing`, async ({ request }) => {
      put = await request.json();
      return HttpResponse.json({ ok: true });
    }),
    ...edGets(),
  );
  const user = userEvent.setup();
  const { panel } = mount();
  await panel.reload();

  const min = await screen.findByLabelText("Minimum sources (blank = gate off)");
  await user.clear(min); // edGets prefills it to 2; clearing turns the gate off
  await user.click(screen.getByRole("button", { name: /save sourcing/i }));

  await waitFor(() => assert.ok(put, "a PUT should have been sent"));
  assert.equal(put.min_sources, null);
  assert.equal(put.require_attribution, true);
  assert.equal(put.no_fabricated_quotes, true);
});

test("saving sourcing sends min_sources as an integer when set", async () => {
  let put = null;
  server.use(
    http.put(`${ORIGIN}/api/editorial/style/sourcing`, async ({ request }) => {
      put = await request.json();
      return HttpResponse.json({ ok: true });
    }),
    ...edGets(),
  );
  const user = userEvent.setup();
  const { panel } = mount();
  await panel.reload();

  const min = await screen.findByLabelText("Minimum sources (blank = gate off)");
  await user.clear(min);
  await user.type(min, "3");
  await user.click(screen.getByRole("button", { name: /save sourcing/i }));

  await waitFor(() => assert.ok(put, "a PUT should have been sent"));
  assert.equal(put.min_sources, 3);
  assert.equal(typeof put.min_sources, "number");
});

test("saving location PUTs only the changed fields", async () => {
  let put = null;
  server.use(
    http.put(`${ORIGIN}/api/editorial/location`, async ({ request }) => {
      put = await request.json();
      return HttpResponse.json(locationOut({ city: "Boston" }));
    }),
    ...edGets(),
  );
  const user = userEvent.setup();
  const { panel } = mount();
  await panel.reload();

  // Derived gl/hl/ceid render read-only.
  await screen.findByText("US:en");
  const city = screen.getByLabelText(/^city$/i);
  await user.clear(city);
  await user.type(city, "Boston");
  await user.click(screen.getByRole("button", { name: /save location/i }));

  await waitFor(() => assert.ok(put, "a PUT should have been sent"));
  assert.deepEqual(Object.keys(put), ["city"], "only the changed field is sent");
  assert.equal(put.city, "Boston");
});

test("a 422 invalid_location is surfaced on save", async () => {
  server.use(
    http.put(`${ORIGIN}/api/editorial/location`, () =>
      HttpResponse.json({ code: "invalid_location", detail: "region is required" }, { status: 422 })),
    ...edGets(),
  );
  const user = userEvent.setup();
  const { panel } = mount();
  await panel.reload();

  const region = await screen.findByLabelText(/^region$/i);
  await user.clear(region);
  await user.click(screen.getByRole("button", { name: /save location/i }));

  const alert = await screen.findByRole("alert");
  assert.match(alert.textContent, /invalid_location/i);
  assert.match(alert.textContent, /region is required/i);
});

test("the versions list shows the active marker and Promote calls the promote endpoint", async () => {
  let promoted = null;
  const v3 = styleOut({ version: 3, is_active: true, created_by: "editor" });
  const v2 = styleOut({ version: 2, is_active: false, created_by: "founder" });
  server.use(
    http.post(`${ORIGIN}/api/editorial/style/versions/2/promote`, () => {
      promoted = 2;
      return HttpResponse.json(styleOut({ version: 2, is_active: true }));
    }),
    http.get(`${ORIGIN}/api/editorial/style/versions`, () => HttpResponse.json({ versions: [v3, v2], total: 2 })),
    ...edGets(),
  );
  const user = userEvent.setup();
  const { panel } = mount();
  await panel.reload();

  const v2row = (await screen.findByText("v2")).closest(".version-row");
  assert.ok(v2row, "the inactive version row should render");
  assert.ok(within(v2row).getByText("inactive"));

  const v3row = screen.getByText("v3").closest(".version-row");
  assert.ok(within(v3row).getByText("active"));
  // The active row carries no Promote button (you cannot promote the active one).
  assert.equal(within(v3row).queryByRole("button", { name: /promote/i }), null);

  await user.click(within(v2row).getByRole("button", { name: /promote/i }));
  await waitFor(() => assert.equal(promoted, 2));
});

test("the no-active-style 404 path shows a publish-first affordance and publishes from empty defaults", async () => {
  server.use(
    http.get(`${ORIGIN}/api/editorial/style`, notFound),
    http.get(`${ORIGIN}/api/editorial/style/lexicon`, notFound),
    http.get(`${ORIGIN}/api/editorial/style/sourcing`, notFound),
    http.get(`${ORIGIN}/api/editorial/style/versions`, () => HttpResponse.json({ versions: [], total: 0 })),
    http.get(`${ORIGIN}/api/editorial/location`, () => HttpResponse.json(locationOut())),
  );
  const user = userEvent.setup();
  const { panel } = mount();
  await panel.reload();

  // The style section shows the publish-first note rather than crashing, and the
  // lexicon/sourcing sections each show their own affordance.
  await screen.findByText(/no active style yet/i);
  assert.ok(screen.getByText(/publish a style first to edit the lexicon/i));
  assert.ok(screen.getByText(/publish a style first to edit sourcing/i));
  await screen.findByText(/no versions yet/i);

  // Publishing from the empty state goes out with empty lexicon + sourcing
  // defaults (nothing to carry forward yet).
  let posted = null;
  server.use(
    http.post(`${ORIGIN}/api/editorial/style`, async ({ request }) => {
      posted = await request.json();
      return HttpResponse.json(styleOut({ version: 1 }), { status: 201 });
    }),
  );
  await user.click(screen.getByRole("button", { name: /publish new version/i }));

  await waitFor(() => assert.ok(posted, "a POST should have been sent"));
  assert.deepEqual(posted.lexicon, {});
  assert.deepEqual(posted.sourcing, {});
  assert.deepEqual(posted.exemplars, []);
  assert.deepEqual(posted.rules, []);
  assert.deepEqual(posted.structure, {});
  assert.equal(posted.activate, true);
});

test("a non-404 style read failure disables Publish so an empty form cannot overwrite the active version", async () => {
  let posted = false;
  server.use(
    http.get(`${ORIGIN}/api/editorial/style`, () => HttpResponse.json({ code: "store_error" }, { status: 500 })),
    http.post(`${ORIGIN}/api/editorial/style`, () => {
      posted = true;
      return HttpResponse.json(styleOut(), { status: 201 });
    }),
    http.get(`${ORIGIN}/api/editorial/style/lexicon`, () => HttpResponse.json({ banned_terms: [], preferred_swaps: {} })),
    http.get(`${ORIGIN}/api/editorial/style/sourcing`, () =>
      HttpResponse.json({ min_sources: 2, require_attribution: true, no_fabricated_quotes: true })),
    http.get(`${ORIGIN}/api/editorial/location`, () => HttpResponse.json(locationOut())),
    http.get(`${ORIGIN}/api/editorial/style/versions`, () => HttpResponse.json({ versions: [], total: 0 })),
  );
  const user = userEvent.setup();
  const { panel } = mount();
  await panel.reload();

  await screen.findByText(/could not load style/i);
  const publishBtn = screen.getByRole("button", { name: /publish new version/i });
  assert.equal(publishBtn.disabled, true, "a failed style read must disable Publish");

  // A click on the disabled button must not POST a blind empty version.
  await user.click(publishBtn);
  assert.equal(posted, false, "a disabled Publish must not overwrite the active style");
});
