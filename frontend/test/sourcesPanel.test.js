import "./setup.js";
import { test, beforeEach } from "node:test";
import assert from "node:assert/strict";
import { http, HttpResponse } from "msw";
import { screen, within, waitFor } from "@testing-library/dom";
import userEvent from "@testing-library/user-event";
import { installServer, ORIGIN } from "./msw.js";
import { installDom } from "./dom.js";
import { SourcesPanel } from "../src/components/sourcesPanel.js";
import { api } from "../src/api.js";

installDom();
const server = installServer();

// reload() now also fetches /personas to invert their pools into per-source
// author chips. Default it to an empty roster so tests that only care about the
// source list need not restate it; tests about the chips override this.
beforeEach(() => {
  server.use(http.get(`${ORIGIN}/api/personas`, () => HttpResponse.json({ personas: [] })));
});

// A full PortalOut, so a card has every field it might render. Overrides let a
// test tweak one field (enabled, description, ...) without restating the rest.
function portal(overrides = {}) {
  return {
    id: "p1",
    domain: "example.com",
    homepage: "https://example.com",
    description: "A daily",
    feed_urls: ["https://example.com/feed.xml"],
    feed_type: "auto",
    language: "es",
    ownership_group: "Grupo X",
    enabled: true,
    status: "ok",
    last_checked: null,
    last_ok: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function mount() {
  const changed = [];
  const panel = SourcesPanel({ api, onChanged: () => changed.push(true) });
  document.body.appendChild(panel.element);
  return { panel, changed };
}

test("renders a card per portal with domain, description, ownership badge, and an online pill", async () => {
  server.use(http.get(`${ORIGIN}/api/portals`, () => HttpResponse.json({ portals: [portal()], total: 1 })));
  const { panel } = mount();
  await panel.reload();

  const card = (await screen.findByText("example.com")).closest(".source-row");
  assert.ok(card, "the source card should render");
  assert.ok(within(card).getByText("A daily"));
  assert.ok(within(card).getByText("Grupo X"));
  const pill = within(card).getByText("online");
  assert.equal(pill.dataset.state, "online");
});

test("shows an empty state when there are no sources", async () => {
  server.use(http.get(`${ORIGIN}/api/portals`, () => HttpResponse.json({ portals: [], total: 0 })));
  const { panel } = mount();
  await panel.reload();

  await screen.findByText("No sources yet.");
});

test("shows an error when the list request fails", async () => {
  server.use(http.get(`${ORIGIN}/api/portals`, () => HttpResponse.json({ code: "boom" }, { status: 500 })));
  const { panel } = mount();
  await panel.reload();

  await screen.findByText(/could not load sources/i);
});

test("creates a source: POSTs the form (feed_urls split) and shows the new card on reload", async () => {
  let created = false;
  let posted = null;
  server.use(
    http.get(`${ORIGIN}/api/portals`, () =>
      HttpResponse.json({ portals: created ? [portal({ id: "n1", domain: "newsite.com", description: "" })] : [], total: created ? 1 : 0 })),
    http.post(`${ORIGIN}/api/portals`, async ({ request }) => {
      posted = await request.json();
      created = true;
      return HttpResponse.json(portal({ id: "n1", domain: "newsite.com" }), { status: 201 });
    }),
  );
  const user = userEvent.setup();
  const { panel, changed } = mount();
  await panel.reload();
  await screen.findByText("No sources yet.");

  await user.type(screen.getByLabelText(/^domain$/i), "newsite.com");
  await user.type(screen.getByLabelText(/feed urls/i), "https://a.com/feed, https://b.com/feed");
  await user.click(screen.getByRole("button", { name: /add source/i }));

  await screen.findByText("newsite.com");
  assert.equal(posted.domain, "newsite.com");
  assert.deepEqual(posted.feed_urls, ["https://a.com/feed", "https://b.com/feed"]);
  assert.equal(posted.enabled, true);
  assert.equal(posted.feed_type, "auto");
  assert.equal(posted.language, "es");
  assert.ok(changed.length >= 1, "onChanged should fire after a successful create");
});

test("blocks create and flags the domain field when it is empty", async () => {
  let posted = false;
  server.use(
    http.get(`${ORIGIN}/api/portals`, () => HttpResponse.json({ portals: [], total: 0 })),
    http.post(`${ORIGIN}/api/portals`, () => {
      posted = true;
      return HttpResponse.json(portal(), { status: 201 });
    }),
  );
  const user = userEvent.setup();
  const { panel } = mount();
  await panel.reload();

  await user.click(screen.getByRole("button", { name: /add source/i }));

  assert.equal(posted, false, "no request should be sent for an empty domain");
  const domain = screen.getByLabelText(/^domain$/i);
  assert.equal(domain.getAttribute("aria-invalid"), "true");
  assert.equal(document.activeElement, domain);
  assert.match(screen.getByRole("alert").textContent, /domain is required/i);
});

test("surfaces the brain's code and detail when a create is rejected", async () => {
  server.use(
    http.get(`${ORIGIN}/api/portals`, () => HttpResponse.json({ portals: [], total: 0 })),
    http.post(`${ORIGIN}/api/portals`, () =>
      HttpResponse.json({ code: "duplicate_domain", detail: "example.com is already a source" }, { status: 409 })),
  );
  const user = userEvent.setup();
  const { panel } = mount();
  await panel.reload();
  await screen.findByText("No sources yet.");

  await user.type(screen.getByLabelText(/^domain$/i), "example.com");
  await user.click(screen.getByRole("button", { name: /add source/i }));

  const alert = await screen.findByRole("alert");
  assert.match(alert.textContent, /already a source/i);
  assert.match(alert.textContent, /duplicate_domain/i);
});

test("toggles a source between enabled and disabled, flipping the pill and hitting both endpoints", async () => {
  let state = true;
  const hits = [];
  server.use(
    http.get(`${ORIGIN}/api/portals`, () => HttpResponse.json({ portals: [portal({ enabled: state })], total: 1 })),
    http.post(`${ORIGIN}/api/portals/p1/disable`, () => {
      hits.push("disable");
      state = false;
      return HttpResponse.json(portal({ enabled: state }));
    }),
    http.post(`${ORIGIN}/api/portals/p1/enable`, () => {
      hits.push("enable");
      state = true;
      return HttpResponse.json(portal({ enabled: state }));
    }),
  );
  const user = userEvent.setup();
  const { panel } = mount();
  await panel.reload();

  let card = (await screen.findByText("example.com")).closest(".source-row");
  assert.ok(within(card).getByText("online"));
  await user.click(within(card).getByRole("button", { name: /disable/i }));

  await waitFor(() => {
    const c = screen.getByText("example.com").closest(".source-row");
    assert.ok(within(c).getByText("offline"));
  });

  card = screen.getByText("example.com").closest(".source-row");
  await user.click(within(card).getByRole("button", { name: /enable/i }));

  await waitFor(() => {
    const c = screen.getByText("example.com").closest(".source-row");
    assert.ok(within(c).getByText("online"));
  });
  assert.deepEqual(hits, ["disable", "enable"]);
});

test("deletes a source after a two-click confirm", async () => {
  let deleted = false;
  let delHit = false;
  server.use(
    http.get(`${ORIGIN}/api/portals`, () => HttpResponse.json({ portals: deleted ? [] : [portal()], total: deleted ? 0 : 1 })),
    http.delete(`${ORIGIN}/api/portals/p1`, () => {
      delHit = true;
      deleted = true;
      return new HttpResponse(null, { status: 204 });
    }),
  );
  const user = userEvent.setup();
  const { panel } = mount();
  await panel.reload();

  const card = (await screen.findByText("example.com")).closest(".source-row");
  // First click only arms the confirm.
  await user.click(within(card).getByRole("button", { name: /^delete$/i }));
  assert.equal(delHit, false, "the first click should not delete");
  // The same button now reads Confirm; the second click deletes.
  await user.click(within(card).getByRole("button", { name: /confirm/i }));

  await screen.findByText("No sources yet.");
  assert.equal(delHit, true);
});

test("edits a source via the inline form and PATCHes without the domain", async () => {
  let patched = null;
  let updated = false;
  server.use(
    http.get(`${ORIGIN}/api/portals`, () =>
      HttpResponse.json({ portals: [portal({ description: updated ? "Updated desc" : "Old desc" })], total: 1 })),
    http.patch(`${ORIGIN}/api/portals/p1`, async ({ request }) => {
      patched = await request.json();
      updated = true;
      return HttpResponse.json(portal({ description: "Updated desc" }));
    }),
  );
  const user = userEvent.setup();
  const { panel } = mount();
  await panel.reload();

  const card = (await screen.findByText("example.com")).closest(".source-row");
  await user.click(within(card).getByRole("button", { name: /^edit$/i }));
  // Edit expands a full-width row beneath; the form has no domain field at all.
  const editRow = document.querySelector('.source-edit-row[data-id="p1"]');
  assert.equal(within(editRow).queryByLabelText(/^domain$/i), null, "the edit form must not edit the domain");

  const descField = within(editRow).getByLabelText(/^description$/i);
  await user.clear(descField);
  await user.type(descField, "Updated desc");
  await user.click(within(editRow).getByRole("button", { name: /save/i }));

  await waitFor(() => assert.ok(patched, "a PATCH should have been sent"));
  assert.equal(patched.domain, undefined, "the PATCH body must not carry a domain");
  assert.equal(patched.description, "Updated desc");
  // The list reflects the patched value after the reload.
  await screen.findByText("Updated desc");
});

test("edit can CLEAR ownership group (sends empty string so the masthead un-groups)", async () => {
  let patched = null;
  server.use(
    http.get(`${ORIGIN}/api/portals`, () =>
      HttpResponse.json({ portals: [portal({ ownership_group: "Grupo X" })], total: 1 })),
    http.patch(`${ORIGIN}/api/portals/p1`, async ({ request }) => {
      patched = await request.json();
      return HttpResponse.json(portal({ ownership_group: "" }));
    }),
  );
  const user = userEvent.setup();
  const { panel } = mount();
  await panel.reload();

  const card = (await screen.findByText("example.com")).closest(".source-row");
  await user.click(within(card).getByRole("button", { name: /^edit$/i }));
  const editRow = document.querySelector('.source-edit-row[data-id="p1"]');
  await user.clear(within(editRow).getByLabelText(/ownership group/i));
  await user.click(within(editRow).getByRole("button", { name: /save/i }));

  await waitFor(() => assert.ok(patched, "a PATCH should have been sent"));
  // A blanked-then-saved field is sent as "" so the brain clears it (exclude_none keeps "").
  assert.equal(patched.ownership_group, "", "clearing ownership group must send an empty string, not omit it");
});

test("each source row lists the authors that read it (reverse of author->sources)", async () => {
  server.use(
    http.get(`${ORIGIN}/api/portals`, () =>
      HttpResponse.json({
        portals: [portal({ id: "p1", domain: "alpha.com" }), portal({ id: "p2", domain: "beta.com" })],
        total: 2,
      })),
    // alpha (p1) is read by Ada AND Ida; beta (p2) only by Ida.
    http.get(`${ORIGIN}/api/personas`, () =>
      HttpResponse.json({
        personas: [
          { id: "ada", display_name: "Ada Lovelace", beat: "politics", sources: ["p1"] },
          { id: "ida", display_name: "Ida Riverss", beat: "economics", sources: ["p1", "p2"] },
        ],
      })),
  );
  const { panel } = mount();
  await panel.reload();

  const alpha = (await screen.findByText("alpha.com")).closest(".source-row");
  assert.ok(within(alpha).getByText("Ada Lovelace"), "alpha should list Ada");
  assert.ok(within(alpha).getByText("Ida Riverss"), "alpha should list Ida");

  const beta = screen.getByText("beta.com").closest(".source-row");
  assert.ok(within(beta).getByText("Ida Riverss"), "beta should list Ida");
  assert.equal(within(beta).queryByText("Ada Lovelace"), null, "beta should NOT list Ada");
});

test("a source no author reads shows the unassigned hint", async () => {
  server.use(
    http.get(`${ORIGIN}/api/portals`, () =>
      HttpResponse.json({ portals: [portal({ id: "p9", domain: "orphan.com" })], total: 1 })),
    http.get(`${ORIGIN}/api/personas`, () =>
      HttpResponse.json({ personas: [{ id: "ada", display_name: "Ada", beat: "politics", sources: ["other"] }] })),
  );
  const { panel } = mount();
  await panel.reload();

  const orphan = (await screen.findByText("orphan.com")).closest(".source-row");
  assert.ok(within(orphan).getByText(/no authors assigned/i));
});

test("renders the sources even when the personas fetch fails (no chips, not an error)", async () => {
  server.use(
    http.get(`${ORIGIN}/api/portals`, () => HttpResponse.json({ portals: [portal()], total: 1 })),
    http.get(`${ORIGIN}/api/personas`, () => HttpResponse.json({ code: "boom" }, { status: 500 })),
  );
  const { panel } = mount();
  await panel.reload();

  // The source list still renders; the author lookup degraded to "unassigned".
  const card = (await screen.findByText("example.com")).closest(".source-row");
  assert.ok(card, "the source row should render despite the personas failure");
  assert.ok(within(card).getByText(/no authors assigned/i));
});

test("the create form's Enabled is an inline checkbox, not a full-width box", async () => {
  server.use(http.get(`${ORIGIN}/api/portals`, () => HttpResponse.json({ portals: [], total: 0 })));
  const { panel } = mount();
  await panel.reload();

  const enabled = screen.getByLabelText("Enabled");
  assert.equal(enabled.type, "checkbox");
  assert.ok(enabled.closest(".field-check"), "Enabled should use the inline check layout, not the stacked text-field one");
});

test("renders sources as a table with the Portal / Actions / Assigned to / Description columns", async () => {
  server.use(http.get(`${ORIGIN}/api/portals`, () => HttpResponse.json({ portals: [portal()], total: 1 })));
  const { panel } = mount();
  await panel.reload();

  await screen.findByText("example.com");
  assert.ok(document.querySelector("table.source-table"), "the sources render in a table");
  const headers = [...document.querySelectorAll(".source-table thead th")].map((h) => h.textContent);
  assert.deepEqual(headers, ["Portal", "Actions", "Assigned to", "Description"]);
  // The data row is a table row, and its cells hold the parts.
  const dataRow = document.querySelector("tr.source-row");
  assert.equal(dataRow.tagName, "TR");
  assert.ok(within(dataRow).getByText("A daily"), "the description sits in its cell");
});
