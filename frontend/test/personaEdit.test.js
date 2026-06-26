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

// A full PersonaOut so a card and its edit form have every field they render.
function persona(overrides = {}) {
  return {
    id: "ada",
    display_name: "Ada Lovelace",
    beat: "tech",
    who_i_am: "Computing pioneer",
    style: "precise and dry",
    about: "wrote the first algorithm",
    language: "en",
    few_shots_pos: ["a good lede"],
    few_shots_neg: ["a bad lede"],
    sources: [],
    avatar_path: "",
    active: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

function mount() {
  const changed = [];
  const list = PersonaList({ api, onChanged: () => changed.push(true) });
  document.body.appendChild(list.element);
  return { list, changed };
}

test("edits a persona: opens the form, changes who-i-am + beat, and PATCHes those fields", async () => {
  let patched = null;
  let updated = false;
  server.use(
    http.get(`${ORIGIN}/api/personas`, () =>
      HttpResponse.json({
        personas: [persona({ who_i_am: updated ? "Newly described" : "Computing pioneer", beat: updated ? "world" : "tech" })],
      })),
    http.patch(`${ORIGIN}/api/personas/ada`, async ({ request }) => {
      patched = await request.json();
      updated = true;
      return HttpResponse.json(persona({ who_i_am: "Newly described", beat: "world" }));
    }),
  );
  const user = userEvent.setup();
  const { list, changed } = mount();
  await list.reload();

  const card = (await screen.findByText("Ada Lovelace")).closest(".persona-card");
  await user.click(within(card).getByRole("button", { name: /^edit$/i }));

  const who = within(card).getByLabelText(/who i am/i);
  await user.clear(who);
  await user.type(who, "Newly described");
  await user.selectOptions(within(card).getByLabelText(/^beat$/i), "world");
  await user.click(within(card).getByRole("button", { name: /^save$/i }));

  await waitFor(() => assert.ok(patched, "a PATCH should have been sent"));
  assert.equal(patched.who_i_am, "Newly described");
  assert.equal(patched.beat, "world");
  // The few-shot lists ride along, prefilled and parsed back to arrays.
  assert.deepEqual(patched.few_shots_pos, ["a good lede"]);
  assert.deepEqual(patched.few_shots_neg, ["a bad lede"]);
  // Source linking is its own panel; the edit form never sends `sources`.
  assert.equal(patched.sources, undefined, "the edit form must not carry sources");

  // The roster reflects the new who-i-am after the reload.
  await screen.findByText("Newly described");
  await waitFor(() => assert.ok(changed.length >= 1, "onChanged should fire after a successful edit"));
});

test("a few-shots field with invalid JSON shows an error and sends no PATCH", async () => {
  let patchHit = false;
  server.use(
    http.get(`${ORIGIN}/api/personas`, () => HttpResponse.json({ personas: [persona()] })),
    http.patch(`${ORIGIN}/api/personas/ada`, () => {
      patchHit = true;
      return HttpResponse.json(persona());
    }),
  );
  const user = userEvent.setup();
  const { list } = mount();
  await list.reload();

  const card = (await screen.findByText("Ada Lovelace")).closest(".persona-card");
  await user.click(within(card).getByRole("button", { name: /^edit$/i }));

  const pos = within(card).getByLabelText(/positive examples/i);
  await user.clear(pos);
  await user.type(pos, "notjson");
  await user.click(within(card).getByRole("button", { name: /^save$/i }));

  const alert = await within(card).findByRole("alert");
  assert.match(alert.textContent, /positive examples must be a valid json array/i);
  assert.equal(patchHit, false, "an invalid examples list must abort before any request");
});

test("a few-shots field that is valid JSON but not an array is rejected before any PATCH", async () => {
  let patchHit = false;
  server.use(
    http.get(`${ORIGIN}/api/personas`, () => HttpResponse.json({ personas: [persona()] })),
    http.patch(`${ORIGIN}/api/personas/ada`, () => {
      patchHit = true;
      return HttpResponse.json(persona());
    }),
  );
  const user = userEvent.setup();
  const { list } = mount();
  await list.reload();

  const card = (await screen.findByText("Ada Lovelace")).closest(".persona-card");
  await user.click(within(card).getByRole("button", { name: /^edit$/i }));

  const neg = within(card).getByLabelText(/negative examples/i);
  await user.clear(neg);
  await user.type(neg, "42");
  await user.click(within(card).getByRole("button", { name: /^save$/i }));

  const alert = await within(card).findByRole("alert");
  assert.match(alert.textContent, /negative examples must be a json array/i);
  assert.equal(patchHit, false, "a non-array examples list must abort before any request");
});

test("deletes a persona after a two-click confirm", async () => {
  let deleted = false;
  let delHit = false;
  server.use(
    http.get(`${ORIGIN}/api/personas`, () => HttpResponse.json({ personas: deleted ? [] : [persona()] })),
    http.delete(`${ORIGIN}/api/personas/ada`, () => {
      delHit = true;
      deleted = true;
      return new HttpResponse(null, { status: 204 });
    }),
  );
  const user = userEvent.setup();
  const { list } = mount();
  await list.reload();

  const card = (await screen.findByText("Ada Lovelace")).closest(".persona-card");
  // First click only arms the confirm.
  await user.click(within(card).getByRole("button", { name: /^delete$/i }));
  assert.equal(delHit, false, "the first click should not delete");
  // The same button now reads Confirm; the second click deletes.
  await user.click(within(card).getByRole("button", { name: /^confirm$/i }));

  await screen.findByText(/no personas yet/i);
  assert.equal(delHit, true);
});

test("surfaces the persona_in_use detail when a delete is rejected with 409", async () => {
  server.use(
    http.get(`${ORIGIN}/api/personas`, () => HttpResponse.json({ personas: [persona()] })),
    http.delete(`${ORIGIN}/api/personas/ada`, () =>
      HttpResponse.json({ code: "persona_in_use", detail: "Ada is still assigned to run r-12" }, { status: 409 })),
  );
  const user = userEvent.setup();
  const { list } = mount();
  await list.reload();

  const card = (await screen.findByText("Ada Lovelace")).closest(".persona-card");
  await user.click(within(card).getByRole("button", { name: /^delete$/i }));
  await user.click(within(card).getByRole("button", { name: /^confirm$/i }));

  const alert = await within(card).findByRole("alert");
  assert.match(alert.textContent, /still assigned to run r-12/i);
  assert.match(alert.textContent, /persona_in_use/i);
  // After a failed delete the button disarms back to "Delete" so a stray next
  // click re-arms instead of silently re-firing the delete.
  await within(card).findByRole("button", { name: /^delete$/i });
});

test("clearing a few-shots field saves an empty list, not an error", async () => {
  let patched = null;
  server.use(
    http.get(`${ORIGIN}/api/personas`, () => HttpResponse.json({ personas: [persona()] })),
    http.patch(`${ORIGIN}/api/personas/ada`, async ({ request }) => {
      patched = await request.json();
      return HttpResponse.json(persona({ few_shots_pos: [] }));
    }),
  );
  const user = userEvent.setup();
  const { list } = mount();
  await list.reload();

  const card = (await screen.findByText("Ada Lovelace")).closest(".persona-card");
  await user.click(within(card).getByRole("button", { name: /^edit$/i }));
  await user.clear(within(card).getByLabelText(/positive examples/i));
  await user.click(within(card).getByRole("button", { name: /^save$/i }));

  await waitFor(() => assert.ok(patched, "a PATCH should have been sent"));
  assert.deepEqual(patched.few_shots_pos, [], "a blanked examples field clears to an empty list");
});
