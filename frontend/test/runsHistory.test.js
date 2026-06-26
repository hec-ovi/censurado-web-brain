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

test("lists runs from GET /runs with a status pill, mode, and counts", async () => {
  server.use(
    http.get(`${ORIGIN}/api/runs`, () =>
      HttpResponse.json({
        runs: [
          { run_id: "r1", mode: "managed", status: "done", n_requested: 4, created_at: "2026-06-26T10:00:00Z", finished_at: "2026-06-26T10:05:00Z" },
          { run_id: "r2", mode: "direct", status: "running", n_requested: 1, created_at: "2026-06-26T11:00:00Z", finished_at: null },
        ],
        total: 2,
      })),
  );
  const panel = mount();
  await panel.reloadHistory();

  const r1 = (await screen.findByText("r1")).closest("tr");
  assert.ok(within(r1).getByText("managed"));
  const pill = within(r1).getByText("done");
  assert.equal(pill.dataset.state, "done", "the status renders as a data-state pill");
  assert.ok(within(r1).getByText("4"));
  assert.ok(screen.getByText("r2"), "a second run renders");
});

test("shows the empty state when there are no runs", async () => {
  server.use(http.get(`${ORIGIN}/api/runs`, () => HttpResponse.json({ runs: [], total: 0 })));
  const panel = mount();
  await panel.reloadHistory();

  await screen.findByText(/no runs yet/i);
});

test("shows an error when the run list request fails", async () => {
  server.use(http.get(`${ORIGIN}/api/runs`, () => HttpResponse.json({ code: "boom", detail: "kaboom" }, { status: 500 })));
  const panel = mount();
  await panel.reloadHistory();

  await screen.findByText(/could not load runs: kaboom/i);
});

test("the status filter re-requests GET /runs with ?status=", async () => {
  const seen = [];
  server.use(
    http.get(`${ORIGIN}/api/runs`, ({ request }) => {
      const status = new URL(request.url).searchParams.get("status");
      seen.push(status);
      const all = [
        { run_id: "r1", mode: "managed", status: "done", n_requested: 4, created_at: "", finished_at: "" },
        { run_id: "r2", mode: "managed", status: "failed", n_requested: 2, created_at: "", finished_at: "" },
      ];
      const runs = status ? all.filter((r) => r.status === status) : all;
      return HttpResponse.json({ runs, total: runs.length });
    }),
  );
  const user = userEvent.setup();
  const panel = mount();
  await panel.reloadHistory();
  await screen.findByText("r1");

  await user.selectOptions(screen.getByLabelText(/filter runs by status/i), "failed");

  await screen.findByText("r2");
  assert.equal(screen.queryByText("r1"), null, "the filtered-out run is gone");
  assert.deepEqual(seen, [null, "failed"], "first an unfiltered load, then ?status=failed");
});

test("View loads a run's assignments via GET /runs/{id}", async () => {
  server.use(
    http.get(`${ORIGIN}/api/runs`, () =>
      HttpResponse.json({
        runs: [{ run_id: "r9", mode: "managed", status: "done", n_requested: 1, created_at: "", finished_at: "" }],
        total: 1,
      })),
    http.get(`${ORIGIN}/api/runs/r9`, () =>
      HttpResponse.json({
        run_id: "r9",
        mode: "managed",
        status: "done",
        assignments: [
          { id: "a1", persona_id: "ada", section: "tech", status: "published", published_id: "55", drop_reason: null },
        ],
      })),
  );
  const user = userEvent.setup();
  const panel = mount();
  await panel.reloadHistory();

  const row = (await screen.findByText("r9")).closest("tr");
  await user.click(within(row).getByRole("button", { name: /view/i }));

  const detail = document.querySelector(".history-detail");
  await within(detail).findByText("ada");
  assert.ok(within(detail).getByText("55"), "the published id of the viewed run shows in the detail region");
});
