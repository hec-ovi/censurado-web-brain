import "./setup.js";
import { test } from "node:test";
import assert from "node:assert/strict";
import { http, HttpResponse } from "msw";
import { screen, within } from "@testing-library/dom";
import userEvent from "@testing-library/user-event";
import { installServer, ORIGIN } from "./msw.js";
import { installDom } from "./dom.js";
import { PersonaList } from "../src/components/personaList.js";
import { api } from "../src/api.js";

installDom();
const server = installServer();

function mount() {
  const list = PersonaList({ api });
  document.body.appendChild(list.element);
  return list;
}

test("renders a card per persona with beat badge and who-i-am", async () => {
  server.use(http.get(`${ORIGIN}/api/personas`, () =>
    HttpResponse.json({
      personas: [{ id: "ada", display_name: "Ada Lovelace", beat: "tech", who_i_am: "Computing pioneer", avatar_path: "" }],
    })));
  const list = mount();
  await list.reload();

  // Scope to the card so the beat badge is not confused with the filter's
  // <option>tech</option>, which carries the same text.
  const heading = await screen.findByText("Ada Lovelace");
  const card = heading.closest(".persona-card");
  assert.ok(card, "the persona card should render");
  assert.ok(within(card).getByText("tech"));
  assert.ok(within(card).getByText("Computing pioneer"));
});

test("shows an empty state when there are no personas", async () => {
  server.use(http.get(`${ORIGIN}/api/personas`, () => HttpResponse.json({ personas: [] })));
  const list = mount();
  await list.reload();

  await screen.findByText(/no personas yet/i);
});

test("re-requests with the selected beat when the filter changes", async () => {
  const seen = [];
  server.use(http.get(`${ORIGIN}/api/personas`, ({ request }) => {
    const beat = new URL(request.url).searchParams.get("beat");
    seen.push(beat);
    const all = [
      { id: "ada", display_name: "Ada", beat: "tech", who_i_am: "" },
      { id: "ida", display_name: "Ida", beat: "world", who_i_am: "" },
    ];
    const personas = beat ? all.filter((p) => p.beat === beat) : all;
    return HttpResponse.json({ personas });
  }));
  const user = userEvent.setup();
  const list = mount();
  await list.reload();
  await screen.findByText("Ada");

  await user.selectOptions(screen.getByLabelText(/filter personas by beat/i), "world");

  await screen.findByText("Ida");
  assert.equal(screen.queryByText("Ada"), null);
  assert.deepEqual(seen, [null, "world"]);
});

test("shows an error when the list request fails", async () => {
  server.use(http.get(`${ORIGIN}/api/personas`, () => HttpResponse.json({ code: "boom" }, { status: 500 })));
  const list = mount();
  await list.reload();

  await screen.findByText(/could not load personas/i);
});

test("uses an initial-letter fallback avatar when none is set", async () => {
  server.use(http.get(`${ORIGIN}/api/personas`, () =>
    HttpResponse.json({ personas: [{ id: "ada", display_name: "ada", beat: "tech", who_i_am: "", avatar_path: "" }] })));
  const list = mount();
  await list.reload();

  // Scope to the card and assert the initial by text, not by the avatar class.
  const heading = await screen.findByText("ada");
  const card = heading.closest(".persona-card");
  assert.ok(card, "the persona card should render");
  assert.equal(within(card).getByText("A").textContent, "A");
});
