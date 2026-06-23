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

// Drive the job poll with a fake clock so the pending -> done transition runs
// with no real delay.
const fastPoll = { attempts: 10, intervalMs: 0, wait: () => Promise.resolve() };

function mount() {
  const created = [];
  const form = PersonaForm({ api, onCreated: (id) => created.push(id), pollOpts: fastPoll });
  document.body.appendChild(form.element);
  return { created };
}

test("blocks submit and reports an error when required fields are empty", async () => {
  let posted = false;
  server.use(http.post(`${ORIGIN}/api/personas`, () => {
    posted = true;
    return HttpResponse.json({}, { status: 202 });
  }));
  const user = userEvent.setup();
  mount();

  await user.click(screen.getByRole("button", { name: /synthesize persona/i }));

  assert.equal(posted, false, "no request should be sent for an invalid form");
  // The error is announced assertively (role=alert), the empty field is marked
  // invalid, and focus moves to it.
  assert.match(screen.getByRole("alert").textContent, /required/i);
  const name = screen.getByLabelText(/display name/i);
  assert.equal(name.getAttribute("aria-invalid"), "true");
  assert.equal(document.activeElement, name);
});

test("disables the submit button while a create is in flight", async () => {
  let release;
  const gate = new Promise((resolve) => {
    release = resolve;
  });
  server.use(
    http.post(`${ORIGIN}/api/personas`, async () => {
      await gate;
      return HttpResponse.json({ job_id: "jb", persona_id: "p", status: "pending" }, { status: 202 });
    }),
    http.get(`${ORIGIN}/api/personas/jobs/jb`, () =>
      HttpResponse.json({ job_id: "jb", status: "done", persona_id: "p", error: "" })),
  );
  const user = userEvent.setup();
  mount();

  await user.type(screen.getByLabelText(/display name/i), "P");
  await user.type(screen.getByLabelText(/seed description/i), "x");
  const button = screen.getByRole("button", { name: /synthesize persona/i });
  await user.click(button);

  assert.equal(button.disabled, true, "disabled while the request is in flight");
  release();
  await screen.findByText(/created p/i);
  assert.equal(button.disabled, false, "re-enabled after completion");
});

test("reports still-synthesizing when the job poll times out", async () => {
  server.use(
    http.post(`${ORIGIN}/api/personas`, () =>
      HttpResponse.json({ job_id: "jt", persona_id: "slow-one", status: "pending" }, { status: 202 })),
    http.get(`${ORIGIN}/api/personas/jobs/jt`, () =>
      HttpResponse.json({ job_id: "jt", status: "pending", persona_id: "slow-one", error: "" })),
  );
  const user = userEvent.setup();
  mount(); // fastPoll exhausts while the job stays pending -> poll_timeout

  await user.type(screen.getByLabelText(/display name/i), "Slow One");
  await user.type(screen.getByLabelText(/seed description/i), "x");
  await user.click(screen.getByRole("button", { name: /synthesize persona/i }));

  await screen.findByText(/still synthesizing "slow-one"/i);
});

test("creates a persona, polls the job to done, and reports it", async () => {
  let jobCalls = 0;
  server.use(
    http.post(`${ORIGIN}/api/personas`, async ({ request }) => {
      const body = await request.json();
      assert.equal(body.display_name, "Ada Reporter");
      assert.equal(body.beat, "world");
      assert.deepEqual(body.sources, ["example.com", "another.org"]);
      return HttpResponse.json({ job_id: "j1", persona_id: "ada-reporter", status: "pending" }, { status: 202 });
    }),
    http.get(`${ORIGIN}/api/personas/jobs/j1`, () => {
      jobCalls += 1;
      const status = jobCalls >= 2 ? "done" : "pending";
      return HttpResponse.json({ job_id: "j1", status, persona_id: "ada-reporter", error: "" });
    }),
  );
  const user = userEvent.setup();
  const { created } = mount();

  await user.type(screen.getByLabelText(/display name/i), "Ada Reporter");
  await user.selectOptions(screen.getByLabelText(/^beat$/i), "world");
  await user.type(screen.getByLabelText(/seed description/i), "A dogged world-news reporter.");
  await user.type(screen.getByLabelText(/preferred sources/i), "example.com, another.org");
  await user.click(screen.getByRole("button", { name: /synthesize persona/i }));

  await screen.findByText(/created ada-reporter/i);
  assert.deepEqual(created, ["ada-reporter"]);
  assert.ok(jobCalls >= 2, "the form should poll until the job is done");
});

test("surfaces a failed synthesis job", async () => {
  server.use(
    http.post(`${ORIGIN}/api/personas`, () =>
      HttpResponse.json({ job_id: "j2", persona_id: "boom", status: "pending" }, { status: 202 })),
    http.get(`${ORIGIN}/api/personas/jobs/j2`, () =>
      HttpResponse.json({ job_id: "j2", status: "failed", persona_id: "boom", error: "model timeout" })),
  );
  const user = userEvent.setup();
  mount();

  await user.type(screen.getByLabelText(/display name/i), "Boom");
  await user.type(screen.getByLabelText(/seed description/i), "x");
  await user.click(screen.getByRole("button", { name: /synthesize persona/i }));

  await screen.findByText(/synthesis failed: model timeout/i);
});

test("shows the brain's error when the create request is rejected", async () => {
  server.use(http.post(`${ORIGIN}/api/personas`, () =>
    HttpResponse.json({ status: 422, code: "validation_failed", detail: "display_name yields no usable id" }, { status: 422 })));
  const user = userEvent.setup();
  mount();

  await user.type(screen.getByLabelText(/display name/i), "...");
  await user.type(screen.getByLabelText(/seed description/i), "x");
  await user.click(screen.getByRole("button", { name: /synthesize persona/i }));

  await screen.findByText(/could not create persona: display_name yields no usable id/i);
});
