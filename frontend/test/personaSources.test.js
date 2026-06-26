import "./setup.js";
import { test } from "node:test";
import assert from "node:assert/strict";
import { http, HttpResponse } from "msw";
import { screen, within, waitFor } from "@testing-library/dom";
import userEvent from "@testing-library/user-event";
import { installServer, ORIGIN } from "./msw.js";
import { installDom } from "./dom.js";
import { PersonaList } from "../src/components/personaList.js";
import { api } from "../src/api.js";

installDom();
const server = installServer();

function persona(overrides = {}) {
  return {
    id: "ada",
    display_name: "Ada Lovelace",
    beat: "tech",
    who_i_am: "Computing pioneer",
    avatar_path: "",
    active: true,
    ...overrides,
  };
}

function portal(id, domain) {
  return { id, domain, enabled: true };
}

function mount() {
  const changed = [];
  const list = PersonaList({ api, onChanged: () => changed.push(true) });
  document.body.appendChild(list.element);
  return { list, changed };
}

test("links sources: portals as checkboxes pre-checked, toggle one, Save PUTs the full checked set", async () => {
  let put = null;
  server.use(
    http.get(`${ORIGIN}/api/personas`, () => HttpResponse.json({ personas: [persona()] })),
    http.get(`${ORIGIN}/api/portals`, () =>
      HttpResponse.json({ portals: [portal("p1", "alpha.com"), portal("p2", "beta.com")], total: 2 })),
    http.get(`${ORIGIN}/api/personas/ada/sources`, () =>
      HttpResponse.json({ persona_id: "ada", sources: ["p1"], portals: [] })),
    http.put(`${ORIGIN}/api/personas/ada/sources`, async ({ request }) => {
      put = await request.json();
      return HttpResponse.json({ persona_id: "ada", sources: put.sources, portals: [] });
    }),
  );
  const user = userEvent.setup();
  const { list, changed } = mount();
  await list.reload();

  const card = (await screen.findByText("Ada Lovelace")).closest(".persona-card");
  await user.click(within(card).getByRole("button", { name: /^sources$/i }));

  // alpha is linked (pre-checked), beta is not.
  const alpha = await within(card).findByLabelText("alpha.com");
  const beta = within(card).getByLabelText("beta.com");
  assert.equal(alpha.checked, true);
  assert.equal(beta.checked, false);

  // Link beta too, then save: the PUT body carries the whole checked set.
  await user.click(beta);
  await user.click(within(card).getByRole("button", { name: /save sources/i }));

  await waitFor(() => assert.ok(put, "a PUT should have been sent"));
  assert.deepEqual(put, { sources: ["p1", "p2"] });
  await waitFor(() => assert.ok(changed.length >= 1, "onChanged should fire after linking"));
});

test("unchecking every source sends an empty set so the pool clears", async () => {
  let put = null;
  server.use(
    http.get(`${ORIGIN}/api/personas`, () => HttpResponse.json({ personas: [persona()] })),
    http.get(`${ORIGIN}/api/portals`, () => HttpResponse.json({ portals: [portal("p1", "alpha.com")], total: 1 })),
    http.get(`${ORIGIN}/api/personas/ada/sources`, () =>
      HttpResponse.json({ persona_id: "ada", sources: ["p1"], portals: [] })),
    http.put(`${ORIGIN}/api/personas/ada/sources`, async ({ request }) => {
      put = await request.json();
      return HttpResponse.json({ persona_id: "ada", sources: [], portals: [] });
    }),
  );
  const user = userEvent.setup();
  const { list } = mount();
  await list.reload();

  const card = (await screen.findByText("Ada Lovelace")).closest(".persona-card");
  await user.click(within(card).getByRole("button", { name: /^sources$/i }));

  const alpha = await within(card).findByLabelText("alpha.com");
  await user.click(alpha); // uncheck the only linked source
  await user.click(within(card).getByRole("button", { name: /save sources/i }));

  await waitFor(() => assert.ok(put, "a PUT should have been sent"));
  assert.deepEqual(put, { sources: [] });
});

test("surfaces the unknown_source detail when a sources save is rejected with 422", async () => {
  server.use(
    http.get(`${ORIGIN}/api/personas`, () => HttpResponse.json({ personas: [persona()] })),
    http.get(`${ORIGIN}/api/portals`, () => HttpResponse.json({ portals: [portal("p1", "alpha.com")], total: 1 })),
    http.get(`${ORIGIN}/api/personas/ada/sources`, () =>
      HttpResponse.json({ persona_id: "ada", sources: [], portals: [] })),
    http.put(`${ORIGIN}/api/personas/ada/sources`, () =>
      HttpResponse.json({ code: "unknown_source", detail: "unknown source ids: p9" }, { status: 422 })),
  );
  const user = userEvent.setup();
  const { list } = mount();
  await list.reload();

  const card = (await screen.findByText("Ada Lovelace")).closest(".persona-card");
  await user.click(within(card).getByRole("button", { name: /^sources$/i }));
  await within(card).findByLabelText("alpha.com");
  await user.click(within(card).getByRole("button", { name: /save sources/i }));

  const alert = await within(card).findByRole("alert");
  assert.match(alert.textContent, /unknown source ids: p9/i);
  assert.match(alert.textContent, /unknown_source/i);
});

test("shows an 'add sources first' note and disables Save when there are no portals", async () => {
  server.use(
    http.get(`${ORIGIN}/api/personas`, () => HttpResponse.json({ personas: [persona()] })),
    http.get(`${ORIGIN}/api/portals`, () => HttpResponse.json({ portals: [], total: 0 })),
    http.get(`${ORIGIN}/api/personas/ada/sources`, () =>
      HttpResponse.json({ persona_id: "ada", sources: [], portals: [] })),
  );
  const user = userEvent.setup();
  const { list } = mount();
  await list.reload();

  const card = (await screen.findByText("Ada Lovelace")).closest(".persona-card");
  await user.click(within(card).getByRole("button", { name: /^sources$/i }));

  await within(card).findByText(/add sources in the sources tab first/i);
  assert.equal(within(card).getByRole("button", { name: /save sources/i }).disabled, true);
});
