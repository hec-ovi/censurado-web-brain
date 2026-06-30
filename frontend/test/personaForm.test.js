import "./setup.js";
import { test } from "node:test";
import assert from "node:assert/strict";
import { http, HttpResponse } from "msw";
import { screen } from "@testing-library/dom";
import userEvent from "@testing-library/user-event";
import { installServer, ORIGIN } from "./msw.js";
import { installDom } from "./dom.js";
import { PersonaForm } from "../src/components/personaForm.js";
import { api } from "../src/api.js";

installDom();
const server = installServer();

function mount() {
  const created = [];
  const form = PersonaForm({ api, onCreated: (id) => created.push(id) });
  document.body.appendChild(form.element);
  return { created };
}

test("blocks submit and reports an error when required fields are empty", async () => {
  let posted = false;
  server.use(http.post(`${ORIGIN}/api/personas/direct`, () => {
    posted = true;
    return HttpResponse.json({}, { status: 201 });
  }));
  const user = userEvent.setup();
  mount();

  await user.click(screen.getByRole("button", { name: /create persona/i }));

  assert.equal(posted, false, "no request should be sent for an invalid form");
  // The error is announced assertively (role=alert), the empty field is marked
  // invalid, and focus moves to it.
  assert.match(screen.getByRole("alert").textContent, /required/i);
  const name = screen.getByLabelText(/display name/i);
  assert.equal(name.getAttribute("aria-invalid"), "true");
  assert.equal(document.activeElement, name);
});

test("blocks submit and flags the beat field when the beat is blank", async () => {
  let posted = false;
  server.use(http.post(`${ORIGIN}/api/personas/direct`, () => {
    posted = true;
    return HttpResponse.json({}, { status: 201 });
  }));
  const user = userEvent.setup();
  mount();

  // Fill everything BUT the beat (it defaults to the blank placeholder option).
  await user.type(screen.getByLabelText(/display name/i), "Ada Reporter");
  await user.type(screen.getByLabelText(/who i am/i), "world desk");
  await user.type(screen.getByLabelText(/^style$/i), "dry and precise");
  await user.click(screen.getByRole("button", { name: /create persona/i }));

  assert.equal(posted, false, "a blank beat must not POST");
  assert.match(screen.getByRole("alert").textContent, /required/i);
  const beat = screen.getByLabelText(/^beat$/i);
  assert.equal(beat.getAttribute("aria-invalid"), "true");
});

test("disables the submit button while a create is in flight", async () => {
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  server.use(
    http.post(`${ORIGIN}/api/personas/direct`, async () => {
      await gate;
      return HttpResponse.json({ id: "p", display_name: "P", beat: "world" }, { status: 201 });
    }),
  );
  const user = userEvent.setup();
  mount();

  await user.type(screen.getByLabelText(/display name/i), "P");
  await user.selectOptions(screen.getByLabelText(/^beat$/i), "world");
  await user.type(screen.getByLabelText(/who i am/i), "x");
  await user.type(screen.getByLabelText(/^style$/i), "y");
  const button = screen.getByRole("button", { name: /create persona/i });
  await user.click(button);

  assert.equal(button.disabled, true, "disabled while the request is in flight");
  release();
  await screen.findByText(/created p/i);
  assert.equal(button.disabled, false, "re-enabled after completion");
});

test("creates a persona by POSTing the explicit fields to /personas/direct", async () => {
  let received = null;
  server.use(
    http.post(`${ORIGIN}/api/personas/direct`, async ({ request }) => {
      received = await request.json();
      return HttpResponse.json(
        { id: "ada-reporter", display_name: received.display_name, beat: received.beat },
        { status: 201 },
      );
    }),
  );
  const user = userEvent.setup();
  const { created } = mount();

  await user.type(screen.getByLabelText(/display name/i), "Ada Reporter");
  await user.selectOptions(screen.getByLabelText(/^beat$/i), "world");
  await user.type(screen.getByLabelText(/who i am/i), "A dogged world-news reporter.");
  await user.type(screen.getByLabelText(/^style$/i), "Plain and direct.");
  await user.type(screen.getByLabelText(/about/i), "Veteran correspondent.");
  await user.type(screen.getByLabelText(/avatar path/i), "/media/ada.png");
  await user.click(screen.getByRole("button", { name: /create persona/i }));

  await screen.findByText(/created ada-reporter/i);
  assert.deepEqual(created, ["ada-reporter"]);
  // The body carries the persona field NAMES the publish byline mapping needs.
  assert.equal(received.display_name, "Ada Reporter");
  assert.equal(received.beat, "world");
  assert.equal(received.who_i_am, "A dogged world-news reporter.");
  assert.equal(received.style, "Plain and direct.");
  assert.equal(received.about, "Veteran correspondent.");
  assert.equal(received.avatar_path, "/media/ada.png");
  // No synthesis-era fields ride along.
  assert.equal(received.seed, undefined);
});

test("shows the brain's error when the create request is rejected", async () => {
  server.use(http.post(`${ORIGIN}/api/personas/direct`, () =>
    HttpResponse.json({ code: "invalid_persona", detail: "display_name yields no usable id" }, { status: 422 })));
  const user = userEvent.setup();
  mount();

  await user.type(screen.getByLabelText(/display name/i), "...");
  await user.selectOptions(screen.getByLabelText(/^beat$/i), "tech");
  await user.type(screen.getByLabelText(/who i am/i), "x");
  await user.type(screen.getByLabelText(/^style$/i), "y");
  await user.click(screen.getByRole("button", { name: /create persona/i }));

  await screen.findByText(/could not create persona: display_name yields no usable id/i);
});
