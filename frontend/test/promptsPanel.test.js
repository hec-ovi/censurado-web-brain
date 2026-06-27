import "./setup.js";
import { test } from "node:test";
import assert from "node:assert/strict";
import { http, HttpResponse, delay } from "msw";
import { screen, within, waitFor } from "@testing-library/dom";
import userEvent from "@testing-library/user-event";
import { installServer, ORIGIN } from "./msw.js";
import { installDom } from "./dom.js";
import { PromptsPanel } from "../src/components/promptsPanel.js";
import { api } from "../src/api.js";

installDom();
const server = installServer();

// A PromptOut: one immutable version of a template keyed by a path. Overrides let
// a test tweak version/is_active/body without restating the rest.
function promptOut(overrides = {}) {
  return {
    key: "journalist/research.md",
    version: 2,
    body: "Research {{TOPIC}} thoroughly.",
    created_by: "editor",
    created_at: "2026-06-01T00:00:00Z",
    is_active: true,
    ...overrides,
  };
}

function keyOf(request) {
  return new URL(request.url).searchParams.get("key");
}

function mount() {
  const panel = PromptsPanel({ api });
  document.body.appendChild(panel.element);
  return { panel };
}

test("reload lists the keys, auto-selects the first, and loads its body and versions", async () => {
  const t1 = promptOut({ key: "journalist/research.md", version: 2, body: "Research {{TOPIC}}." });
  const t2 = promptOut({ key: "editor/review.md", version: 5, body: "Review the draft." });
  server.use(
    http.get(`${ORIGIN}/api/prompts`, () => HttpResponse.json({ templates: [t1, t2], total: 2 })),
    http.get(`${ORIGIN}/api/prompts/template`, ({ request }) =>
      HttpResponse.json(keyOf(request) === t2.key ? t2 : t1)),
    http.get(`${ORIGIN}/api/prompts/versions`, ({ request }) =>
      HttpResponse.json({ versions: keyOf(request) === t2.key ? [t2] : [t1], total: 1 })),
  );
  const { panel } = mount();
  await panel.reload();

  // The select carries both keys, with the first auto-selected.
  const select = screen.getByLabelText("Prompt template");
  assert.deepEqual([...select.options].map((o) => o.value), [t1.key, t2.key]);
  assert.equal(select.value, t1.key);

  // The editor loaded the first key's active body, and the versions list rendered.
  await screen.findByText(/active version 2 for journalist\/research\.md/i);
  assert.equal(screen.getByLabelText(/^body$/i).value, "Research {{TOPIC}}.");
  assert.ok(screen.getByText("v2"));
});

test("selecting a different key loads that key's template", async () => {
  const t1 = promptOut({ key: "journalist/research.md", version: 2, body: "Research." });
  const t2 = promptOut({ key: "editor/review.md", version: 5, body: "Review." });
  let lastTemplateKey = null;
  server.use(
    http.get(`${ORIGIN}/api/prompts`, () => HttpResponse.json({ templates: [t1, t2], total: 2 })),
    http.get(`${ORIGIN}/api/prompts/template`, ({ request }) => {
      lastTemplateKey = keyOf(request);
      return HttpResponse.json(lastTemplateKey === t2.key ? t2 : t1);
    }),
    http.get(`${ORIGIN}/api/prompts/versions`, ({ request }) =>
      HttpResponse.json({ versions: keyOf(request) === t2.key ? [t2] : [t1], total: 1 })),
  );
  const user = userEvent.setup();
  const { panel } = mount();
  await panel.reload();
  await screen.findByText(/active version 2 for journalist\/research\.md/i);

  const select = screen.getByLabelText("Prompt template");
  await user.selectOptions(select, t2.key);

  // getPrompt was re-asked with the newly selected key (riding as ?key=).
  await waitFor(() => assert.equal(lastTemplateKey, t2.key));
  await screen.findByText(/active version 5 for editor\/review\.md/i);
  assert.equal(screen.getByLabelText(/^body$/i).value, "Review.");
});

test("publishing POSTs {key, body, created_by, activate:true} and refreshes", async () => {
  const t1 = promptOut({ key: "journalist/research.md", version: 2, body: "Old body." });
  let posted = null;
  server.use(
    http.post(`${ORIGIN}/api/prompts/template`, async ({ request }) => {
      posted = await request.json();
      return HttpResponse.json(promptOut({ version: 3, body: posted.body }), { status: 201 });
    }),
    http.get(`${ORIGIN}/api/prompts`, () => HttpResponse.json({ templates: [t1], total: 1 })),
    http.get(`${ORIGIN}/api/prompts/template`, () => HttpResponse.json(t1)),
    http.get(`${ORIGIN}/api/prompts/versions`, () => HttpResponse.json({ versions: [t1], total: 1 })),
  );
  const user = userEvent.setup();
  const { panel } = mount();
  await panel.reload();
  await screen.findByText(/active version 2 for journalist\/research\.md/i);

  const body = screen.getByLabelText(/^body$/i);
  await user.clear(body);
  await user.type(body, "New research instructions.");
  await user.type(screen.getByLabelText(/published by/i), "hector");
  await user.click(screen.getByRole("button", { name: /publish new version/i }));

  await waitFor(() => assert.ok(posted, "a POST should have been sent"));
  assert.equal(posted.key, "journalist/research.md");
  assert.equal(posted.body, "New research instructions.");
  assert.equal(posted.created_by, "hector");
  assert.equal(posted.activate, true);
  await screen.findByText(/published a new version/i);
});

test("an empty body is blocked client-side with an inline error and no POST", async () => {
  const t1 = promptOut({ key: "journalist/research.md", version: 2, body: "Old body." });
  let posted = false;
  server.use(
    http.post(`${ORIGIN}/api/prompts/template`, () => {
      posted = true;
      return HttpResponse.json(promptOut({ version: 3 }), { status: 201 });
    }),
    http.get(`${ORIGIN}/api/prompts`, () => HttpResponse.json({ templates: [t1], total: 1 })),
    http.get(`${ORIGIN}/api/prompts/template`, () => HttpResponse.json(t1)),
    http.get(`${ORIGIN}/api/prompts/versions`, () => HttpResponse.json({ versions: [t1], total: 1 })),
  );
  const user = userEvent.setup();
  const { panel } = mount();
  await panel.reload();
  await screen.findByText(/active version 2 for journalist\/research\.md/i);

  await user.clear(screen.getByLabelText(/^body$/i));
  await user.click(screen.getByRole("button", { name: /publish new version/i }));

  await screen.findByText(/body cannot be empty/i);
  assert.equal(posted, false, "no POST should be sent for an empty body");
});

test("a 422 invalid_prompt from the brain is surfaced", async () => {
  const t1 = promptOut({ key: "journalist/research.md", version: 2, body: "Old body." });
  server.use(
    http.post(`${ORIGIN}/api/prompts/template`, () =>
      HttpResponse.json({ code: "invalid_prompt", detail: "body must not be blank" }, { status: 422 })),
    http.get(`${ORIGIN}/api/prompts`, () => HttpResponse.json({ templates: [t1], total: 1 })),
    http.get(`${ORIGIN}/api/prompts/template`, () => HttpResponse.json(t1)),
    http.get(`${ORIGIN}/api/prompts/versions`, () => HttpResponse.json({ versions: [t1], total: 1 })),
  );
  const user = userEvent.setup();
  const { panel } = mount();
  await panel.reload();
  await screen.findByText(/active version 2 for journalist\/research\.md/i);

  const body = screen.getByLabelText(/^body$/i);
  await user.clear(body);
  await user.type(body, "Some non-empty body.");
  await user.click(screen.getByRole("button", { name: /publish new version/i }));

  const alert = await screen.findByRole("alert");
  assert.match(alert.textContent, /invalid_prompt/i);
  assert.match(alert.textContent, /body must not be blank/i);
});

test("the versions list shows the active marker and Promote calls the promote endpoint", async () => {
  const key = "journalist/research.md";
  const v2 = promptOut({ key, version: 2, is_active: true, created_by: "editor" });
  const v1 = promptOut({ key, version: 1, is_active: false, created_by: "founder" });
  let promoted = null;
  server.use(
    http.post(`${ORIGIN}/api/prompts/versions/1/promote`, () => {
      promoted = 1;
      return HttpResponse.json(promptOut({ key, version: 1, is_active: true }));
    }),
    http.get(`${ORIGIN}/api/prompts`, () => HttpResponse.json({ templates: [v2], total: 1 })),
    http.get(`${ORIGIN}/api/prompts/template`, () => HttpResponse.json(v2)),
    http.get(`${ORIGIN}/api/prompts/versions`, () => HttpResponse.json({ versions: [v2, v1], total: 2 })),
  );
  const user = userEvent.setup();
  const { panel } = mount();
  await panel.reload();

  const v1row = (await screen.findByText("v1")).closest(".version-row");
  assert.ok(v1row, "the inactive version row should render");
  assert.ok(within(v1row).getByText("inactive"));

  const v2row = screen.getByText("v2").closest(".version-row");
  assert.ok(within(v2row).getByText("active"));
  // The active row carries no Promote button (you cannot promote the active one).
  assert.equal(within(v2row).queryByRole("button", { name: /promote/i }), null);

  await user.click(within(v1row).getByRole("button", { name: /promote/i }));
  await waitFor(() => assert.equal(promoted, 1));
});

test("the empty state shows 'No prompt templates.'", async () => {
  server.use(http.get(`${ORIGIN}/api/prompts`, () => HttpResponse.json({ templates: [], total: 0 })));
  const { panel } = mount();
  await panel.reload();

  await screen.findByText("No prompt templates.");
  // No key is selected, so the editor shows its empty affordance.
  assert.ok(screen.getByText(/no template selected/i));
});

test("out-of-order template loads never clobber the editor with another key's body", async () => {
  const t1 = promptOut({ key: "journalist/research.md", version: 2, body: "A-body" });
  const t2 = promptOut({ key: "editor/review.md", version: 5, body: "B-body" });
  let bResolved = false;
  server.use(
    http.get(`${ORIGIN}/api/prompts`, () => HttpResponse.json({ templates: [t1, t2], total: 2 })),
    http.get(`${ORIGIN}/api/prompts/template`, async ({ request }) => {
      const key = keyOf(request);
      if (key === t2.key) {
        await delay(40); // B is the SLOW load
        bResolved = true;
        return HttpResponse.json(t2);
      }
      return HttpResponse.json(t1); // A is fast
    }),
    http.get(`${ORIGIN}/api/prompts/versions`, ({ request }) =>
      HttpResponse.json({ versions: keyOf(request) === t2.key ? [t2] : [t1], total: 1 })),
  );
  const user = userEvent.setup();
  const { panel } = mount();
  await panel.reload();
  await screen.findByText(/active version 2 for journalist\/research\.md/i);

  // Select B (slow) then immediately A (fast): A resolves first, then B's stale
  // response arrives late and must be dropped, not written into the editor.
  const select = screen.getByLabelText("Prompt template");
  await user.selectOptions(select, t2.key);
  await user.selectOptions(select, t1.key);

  await waitFor(() => assert.ok(bResolved, "the slow B load should have resolved"));
  // The editor still shows the current selection (A), never B's late body.
  assert.equal(select.value, t1.key);
  assert.equal(screen.getByLabelText(/^body$/i).value, "A-body", "a stale load must not clobber the editor");
});
