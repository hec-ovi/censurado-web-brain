import "./setup.js";
import { test } from "node:test";
import assert from "node:assert/strict";
import { http, HttpResponse } from "msw";
import { screen } from "@testing-library/dom";
import userEvent from "@testing-library/user-event";
import { installServer, ORIGIN } from "./msw.js";
import { installDom } from "./dom.js";
import { mountApp } from "../src/main.js";

installDom();
const server = installServer();

// Drive the whole app through its real entry point (mountApp) against the
// network mock: health turns online, the roster loads, and creating a persona
// refreshes the list. The job returns done on the first poll, so no real delay.
test("mounts the app, shows health, and refreshes the roster after a create", async () => {
  let created = false;
  server.use(
    http.get(`${ORIGIN}/api/health`, () => HttpResponse.json({ ok: true })),
    http.get(`${ORIGIN}/api/personas`, () =>
      HttpResponse.json({
        personas: created
          ? [{ id: "ada-reporter", display_name: "Ada Reporter", beat: "world", who_i_am: "world desk" }]
          : [],
      })),
    http.post(`${ORIGIN}/api/personas`, () =>
      HttpResponse.json({ job_id: "j", persona_id: "ada-reporter", status: "pending" }, { status: 202 })),
    http.get(`${ORIGIN}/api/personas/jobs/j`, () => {
      created = true;
      return HttpResponse.json({ job_id: "j", status: "done", persona_id: "ada-reporter", error: "" });
    }),
  );
  const user = userEvent.setup();
  const root = document.createElement("div");
  document.body.appendChild(root);

  mountApp(root);

  await screen.findByText("online");
  await screen.findByText(/no personas yet/i);

  await user.type(screen.getByLabelText(/display name/i), "Ada Reporter");
  await user.selectOptions(screen.getByLabelText(/^beat$/i), "world");
  await user.type(screen.getByLabelText(/seed description/i), "covers the world desk");
  await user.click(screen.getByRole("button", { name: /synthesize persona/i }));

  await screen.findByText("Ada Reporter");
  // The roster actually replaced its empty state, not just appended.
  assert.equal(screen.queryByText(/no personas yet/i), null);
});
