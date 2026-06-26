import "./setup.js";
import { test } from "node:test";
import assert from "node:assert/strict";
import { http, HttpResponse } from "msw";
import { screen, within } from "@testing-library/dom";
import userEvent from "@testing-library/user-event";
import { installServer, ORIGIN } from "./msw.js";
import { installDom } from "./dom.js";
import { RunPanel } from "../src/components/runPanel.js";
import { api } from "../src/api.js";

installDom();
const server = installServer();

const fastPoll = { attempts: 10, intervalMs: 0, wait: () => Promise.resolve() };

function mount() {
  const panel = RunPanel({ api, pollOpts: fastPoll });
  document.body.appendChild(panel.element);
  return panel;
}

// An empty history list so the post-trigger history refresh has a stub. Tests
// that care about history contents live in runsHistory.test.js.
function emptyHistory() {
  return http.get(`${ORIGIN}/api/runs`, () => HttpResponse.json({ runs: [], total: 0 }));
}

function personas(list) {
  return http.get(`${ORIGIN}/api/personas`, () => HttpResponse.json({ personas: list }));
}

test("Full manager triggers a managed run and renders the assignment outcomes", async () => {
  let body;
  let runCalls = 0;
  server.use(
    emptyHistory(),
    http.post(`${ORIGIN}/api/runs`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ run_id: "r1", mode: "managed", status: "running" }, { status: 202 });
    }),
    http.get(`${ORIGIN}/api/runs/r1`, () => {
      runCalls += 1;
      if (runCalls < 2) {
        return HttpResponse.json({ run_id: "r1", mode: "managed", status: "running", assignments: [] });
      }
      return HttpResponse.json({
        run_id: "r1",
        mode: "managed",
        status: "done_with_errors",
        assignments: [
          { id: "a1", persona_id: "ada", section: "tech", status: "published", published_id: "42", drop_reason: null },
          { id: "a2", persona_id: "ida", section: "world", status: "dropped", published_id: null, drop_reason: "budget_exhausted" },
        ],
      });
    }),
  );
  const user = userEvent.setup();
  mount();

  await user.click(screen.getByRole("button", { name: /start run/i }));

  await screen.findByText("ada");
  assert.equal(body.mode, "managed", "Full manager submits the managed mode");
  assert.equal(body.images, true, "images on by default");
  assert.ok(!("persona_ids" in body), "Full manager scopes no authors");
  assert.ok(screen.getByText("42"), "the published id should show");
  assert.ok(screen.getByText("budget_exhausted"), "the drop reason should show");
  assert.match(screen.getByRole("status").textContent, /done_with_errors/i);
});

test("Full manager forwards an integer count override", async () => {
  let body;
  server.use(
    emptyHistory(),
    http.post(`${ORIGIN}/api/runs`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ run_id: "rn", mode: "managed", status: "running" }, { status: 202 });
    }),
    http.get(`${ORIGIN}/api/runs/rn`, () => HttpResponse.json({ run_id: "rn", status: "done", assignments: [] })),
  );
  const user = userEvent.setup();
  mount();

  await user.type(screen.getByLabelText("Count (n)"), "3");
  await user.click(screen.getByRole("button", { name: /start run/i }));

  await screen.findByText(/no assignments produced/i);
  assert.equal(body.mode, "managed");
  assert.equal(body.n, 3);
});

test("Author batch scopes the manager to the checked authors and carries the sub-mode", async () => {
  let body;
  server.use(
    emptyHistory(),
    personas([
      { id: "ada", display_name: "Ada", beat: "tech", who_i_am: "" },
      { id: "ida", display_name: "Ida", beat: "world", who_i_am: "" },
    ]),
    http.post(`${ORIGIN}/api/runs`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ run_id: "rb", mode: "express", status: "running" }, { status: 202 });
    }),
    http.get(`${ORIGIN}/api/runs/rb`, () => HttpResponse.json({ run_id: "rb", status: "done", assignments: [] })),
  );
  const user = userEvent.setup();
  const panel = mount();
  await panel.loadAuthors();

  await user.selectOptions(screen.getByLabelText("Trigger"), "batch");
  await user.click(await screen.findByLabelText("Ada")); // check one author
  await user.selectOptions(screen.getByLabelText(/manager mode/i), "express");
  await user.click(screen.getByRole("button", { name: /start run/i }));

  await screen.findByText(/no assignments produced/i);
  assert.equal(body.mode, "express", "the batch sub-mode is forwarded");
  assert.deepEqual(body.persona_ids, ["ada"], "only the checked author is scoped");
  assert.equal(body.images, true);
});

test("Author batch with no author checked shows an error and sends nothing", async () => {
  let posted = false;
  server.use(
    personas([{ id: "ada", display_name: "Ada", beat: "tech", who_i_am: "" }]),
    http.post(`${ORIGIN}/api/runs`, () => {
      posted = true;
      return HttpResponse.json({ run_id: "x", mode: "managed", status: "running" }, { status: 202 });
    }),
  );
  const user = userEvent.setup();
  const panel = mount();
  await panel.loadAuthors();

  await user.selectOptions(screen.getByLabelText("Trigger"), "batch");
  await user.click(screen.getByRole("button", { name: /start run/i }));

  await screen.findByText(/pick at least one author/i);
  assert.equal(posted, false, "no run is started without an author");
});

test("Single article posts the direct path with persona, brief and links", async () => {
  let body;
  let path;
  server.use(
    emptyHistory(),
    personas([{ id: "ada", display_name: "Ada", beat: "tech", who_i_am: "" }]),
    http.post(`${ORIGIN}/api/articles/from-link`, async ({ request }) => {
      path = new URL(request.url).pathname;
      body = await request.json();
      return HttpResponse.json({ run_id: "rd", mode: "direct", status: "running" }, { status: 202 });
    }),
    http.get(`${ORIGIN}/api/runs/rd`, () =>
      HttpResponse.json({
        run_id: "rd",
        status: "done",
        assignments: [{ id: "a1", persona_id: "ada", section: "tech", status: "published", published_id: "9" }],
      })),
  );
  const user = userEvent.setup();
  const panel = mount();
  await panel.loadAuthors();

  await user.selectOptions(screen.getByLabelText("Trigger"), "single");
  await user.selectOptions(await screen.findByLabelText("Author"), "ada");
  await user.type(screen.getByLabelText("Brief"), "Cover the new bill");
  await user.type(screen.getByLabelText("Links"), "https://a.test/1, https://b.test/2");
  await user.click(screen.getByRole("button", { name: /start run/i }));

  await screen.findByText("ada");
  assert.equal(path, "/api/articles/from-link", "single article uses the direct route");
  assert.equal(body.persona_id, "ada");
  assert.equal(body.brief, "Cover the new bill");
  assert.deepEqual(body.links, ["https://a.test/1", "https://b.test/2"]);
  assert.equal(body.images, true);
});

test("Single article with neither brief nor link is blocked client-side", async () => {
  let posted = false;
  server.use(
    personas([{ id: "ada", display_name: "Ada", beat: "tech", who_i_am: "" }]),
    http.post(`${ORIGIN}/api/articles/from-link`, () => {
      posted = true;
      return HttpResponse.json({ run_id: "x", mode: "direct", status: "running" }, { status: 202 });
    }),
  );
  const user = userEvent.setup();
  const panel = mount();
  await panel.loadAuthors();

  await user.selectOptions(screen.getByLabelText("Trigger"), "single");
  await user.selectOptions(await screen.findByLabelText("Author"), "ada");
  await user.click(screen.getByRole("button", { name: /start run/i }));

  await screen.findByText(/give a brief or at least one link/i);
  assert.equal(posted, false, "no direct article without a brief or a link");
});

test("Single article surfaces persona_not_found from the brain", async () => {
  server.use(
    emptyHistory(),
    personas([{ id: "ada", display_name: "Ada", beat: "tech", who_i_am: "" }]),
    http.post(`${ORIGIN}/api/articles/from-link`, () =>
      HttpResponse.json({ status: 404, code: "persona_not_found", detail: "no such persona" }, { status: 404 })),
  );
  const user = userEvent.setup();
  const panel = mount();
  await panel.loadAuthors();

  await user.selectOptions(screen.getByLabelText("Trigger"), "single");
  await user.selectOptions(await screen.findByLabelText("Author"), "ada");
  await user.type(screen.getByLabelText("Brief"), "anything");
  await user.click(screen.getByRole("button", { name: /start run/i }));

  await screen.findByText(/could not start run: no such persona/i);
});

test("reports a failed run", async () => {
  server.use(
    emptyHistory(),
    http.post(`${ORIGIN}/api/runs`, () =>
      HttpResponse.json({ run_id: "r3", mode: "managed", status: "running" }, { status: 202 })),
    http.get(`${ORIGIN}/api/runs/r3`, () => HttpResponse.json({ run_id: "r3", status: "failed", assignments: [] })),
  );
  const user = userEvent.setup();
  mount();

  await user.click(screen.getByRole("button", { name: /start run/i }));

  await screen.findByText(/run failed/i);
});

test("reports an error when the run request itself is rejected", async () => {
  server.use(
    http.post(`${ORIGIN}/api/runs`, () =>
      HttpResponse.json({ status: 422, code: "invalid_mode", detail: "mode must be one of (managed, express, manual)" }, { status: 422 })),
  );
  const user = userEvent.setup();
  mount();

  await user.click(screen.getByRole("button", { name: /start run/i }));

  await screen.findByText(/could not start run: mode must be one of/i);
});

test("generates images by default and renders the hero thumbnail", async () => {
  let body;
  server.use(
    emptyHistory(),
    http.post(`${ORIGIN}/api/runs`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ run_id: "ri", mode: "managed", status: "running" }, { status: 202 });
    }),
    http.get(`${ORIGIN}/api/runs/ri`, () =>
      HttpResponse.json({
        run_id: "ri",
        status: "done",
        assignments: [
          { id: "a1", persona_id: "ada", section: "tech", status: "published", published_id: "7", image_url: "/media/hero.png" },
        ],
      })),
  );
  const user = userEvent.setup();
  mount();

  await user.click(screen.getByRole("button", { name: /start run/i }));

  const img = await screen.findByAltText(/hero image for ada/i);
  assert.match(img.getAttribute("src"), /\/media\/hero\.png$/);
  assert.equal(body.images, true, "images on by default");
});

test("skips images when the auto-generate toggle is unchecked", async () => {
  let body;
  server.use(
    emptyHistory(),
    http.post(`${ORIGIN}/api/runs`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ run_id: "rn2", mode: "managed", status: "running" }, { status: 202 });
    }),
    http.get(`${ORIGIN}/api/runs/rn2`, () =>
      HttpResponse.json({
        run_id: "rn2",
        status: "done",
        assignments: [
          { id: "a1", persona_id: "ada", section: "tech", status: "published", published_id: "7", image_url: null },
        ],
      })),
  );
  const user = userEvent.setup();
  mount();

  await user.click(screen.getByLabelText(/auto-generate images/i)); // uncheck (default on)
  await user.click(screen.getByRole("button", { name: /start run/i }));

  await screen.findByText("ada");
  assert.equal(body.images, false, "images off when unchecked");
  // The run-result region carries no hero image; the trigger help marker still has its (?).
  const result = document.querySelector(".run-result");
  assert.equal(within(result).queryByRole("img"), null, "no hero image when image_url is absent");
});

test("reports still-running and points at history when the run poll times out", async () => {
  server.use(
    emptyHistory(),
    http.post(`${ORIGIN}/api/runs`, () =>
      HttpResponse.json({ run_id: "rt", mode: "managed", status: "running" }, { status: 202 })),
    http.get(`${ORIGIN}/api/runs/rt`, () => HttpResponse.json({ run_id: "rt", status: "running", assignments: [] })),
  );
  const user = userEvent.setup();
  mount(); // fastPoll exhausts while the run stays "running" -> poll_timeout

  await user.click(screen.getByRole("button", { name: /start run/i }));

  await screen.findByText(/run rt is still running; check history/i);
});
