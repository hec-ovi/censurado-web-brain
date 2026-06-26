import "./setup.js";
import { test } from "node:test";
import assert from "node:assert/strict";
import { http, HttpResponse } from "msw";
import { screen, within } from "@testing-library/dom";
import { installServer, ORIGIN } from "./msw.js";
import { installDom } from "./dom.js";
import { BackendStatus } from "../src/components/backendStatus.js";
import { api } from "../src/api.js";

installDom();
const server = installServer();

function mount() {
  const panel = BackendStatus({ api });
  document.body.appendChild(panel.element);
  return panel;
}

test("renders the backend connection flags from GET /status/backend", async () => {
  server.use(
    http.get(`${ORIGIN}/api/status/backend`, () =>
      HttpResponse.json({
        backend_base_url: "http://backend.local",
        reachable: true,
        authorized: false,
        author_count: 7,
        status_code: 403,
        detail: "missing api key",
      })),
  );
  const panel = mount();
  await panel.refresh();

  await screen.findByText("http://backend.local");
  assert.ok(screen.getByText("7"), "author_count should render");
  assert.ok(screen.getByText("403"), "status_code should render");
  assert.ok(screen.getByText("missing api key"), "detail should render");

  // reachable/authorized carry an online/offline data-state like the health dot.
  const root = panel.element;
  const reachableRow = within(root).getByText("reachable").closest(".backend-row");
  assert.equal(within(reachableRow).getByText("yes").dataset.state, "online");
  const authorizedRow = within(root).getByText("authorized").closest(".backend-row");
  assert.equal(within(authorizedRow).getByText("no").dataset.state, "offline");
});

test("shows an error alert when the status request fails", async () => {
  server.use(
    http.get(`${ORIGIN}/api/status/backend`, () =>
      HttpResponse.json({ code: "backend_down", detail: "no route to backend" }, { status: 502 })),
  );
  const panel = mount();
  await panel.refresh();

  const alert = await screen.findByRole("alert");
  assert.match(alert.textContent, /could not load backend status: no route to backend/i);
});
