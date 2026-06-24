import "./setup.js";
import { test } from "node:test";
import assert from "node:assert/strict";
import { http, HttpResponse } from "msw";
import { screen } from "@testing-library/dom";
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

test("starts a managed run and renders the assignment outcomes", async () => {
  let runCalls = 0;
  server.use(
    http.post(`${ORIGIN}/api/runs`, async ({ request }) => {
      const body = await request.json();
      assert.equal(body.mode, "managed");
      assert.ok(!("n" in body), "no n override when the field is blank");
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
  assert.ok(screen.getByText("42"), "the published id should show");
  assert.ok(screen.getByText("budget_exhausted"), "the drop reason should show");
  assert.match(screen.getByRole("status").textContent, /done_with_errors/i);
});

test("forwards n and persona_ids overrides in the request body", async () => {
  let body;
  server.use(
    http.post(`${ORIGIN}/api/runs`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ run_id: "r2", mode: "manual", status: "running" }, { status: 202 });
    }),
    http.get(`${ORIGIN}/api/runs/r2`, () => HttpResponse.json({ run_id: "r2", status: "done", assignments: [] })),
  );
  const user = userEvent.setup();
  mount();

  await user.selectOptions(screen.getByLabelText(/mode/i), "manual");
  await user.type(screen.getByLabelText(/count/i), "3");
  await user.type(screen.getByLabelText(/persona ids/i), "ada, ida");
  await user.click(screen.getByRole("button", { name: /start run/i }));

  await screen.findByText(/no assignments produced/i);
  assert.equal(body.mode, "manual");
  assert.equal(body.n, 3);
  assert.deepEqual(body.persona_ids, ["ada", "ida"]);
});

test("reports a failed run", async () => {
  server.use(
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
    http.post(`${ORIGIN}/api/runs`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ run_id: "rn", mode: "managed", status: "running" }, { status: 202 });
    }),
    http.get(`${ORIGIN}/api/runs/rn`, () =>
      HttpResponse.json({
        run_id: "rn",
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
  assert.equal(screen.queryByRole("img"), null, "no hero image when image_url is absent");
});

test("reports still-running when the run poll times out", async () => {
  server.use(
    http.post(`${ORIGIN}/api/runs`, () =>
      HttpResponse.json({ run_id: "rt", mode: "managed", status: "running" }, { status: 202 })),
    http.get(`${ORIGIN}/api/runs/rt`, () => HttpResponse.json({ run_id: "rt", status: "running", assignments: [] })),
  );
  const user = userEvent.setup();
  mount(); // fastPoll exhausts while the run stays "running" -> poll_timeout

  await user.click(screen.getByRole("button", { name: /start run/i }));

  await screen.findByText(/run rt is still running/i);
});
