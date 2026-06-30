import "./setup.js";
import { test, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { http, HttpResponse } from "msw";
import { screen, within } from "@testing-library/dom";
import userEvent from "@testing-library/user-event";
import { installServer, ORIGIN } from "./msw.js";
import { installDom } from "./dom.js";
import { mountApp } from "../src/main.js";
import { t, currentLocale, ES } from "../src/components/i18n.js";

installDom();
const server = installServer();

// setup.js pins "en" for the whole process. These tests flip the locale per
// case and restore "en" afterwards so they never leak Spanish into other files.
beforeEach(() => {
  localStorage.setItem("admin-lang", "en");
});
afterEach(() => {
  localStorage.setItem("admin-lang", "en");
});

// The same empty-newsroom stubs the app pings on every mount.
function baseHandlers(extra = []) {
  return [
    ...extra,
    http.get(`${ORIGIN}/api/health`, () => HttpResponse.json({ ok: true })),
    http.get(`${ORIGIN}/api/personas`, () => HttpResponse.json({ personas: [] })),
    http.get(`${ORIGIN}/api/portals`, () => HttpResponse.json({ portals: [], total: 0 })),
    http.get(`${ORIGIN}/api/runs`, () => HttpResponse.json({ runs: [], total: 0 })),
    http.get(`${ORIGIN}/api/status/backend`, () =>
      HttpResponse.json({
        backend_base_url: "http://backend.local",
        reachable: true,
        authorized: true,
        author_count: 0,
        status_code: 200,
        detail: "",
      })),
    http.get(`${ORIGIN}/api/editorial/style`, () =>
      HttpResponse.json({ code: "not_found", detail: "no active style" }, { status: 404 })),
    http.get(`${ORIGIN}/api/editorial/style/lexicon`, () =>
      HttpResponse.json({ code: "not_found", detail: "no active style" }, { status: 404 })),
    http.get(`${ORIGIN}/api/editorial/style/sourcing`, () =>
      HttpResponse.json({ code: "not_found", detail: "no active style" }, { status: 404 })),
    http.get(`${ORIGIN}/api/editorial/style/versions`, () => HttpResponse.json({ versions: [], total: 0 })),
    http.get(`${ORIGIN}/api/editorial/location`, () =>
      HttpResponse.json({ region: "", ui_lang: "", language: "", gdelt_country: "", city: "", latlong: "", updated_at: "", gl: "", hl: "", ceid: "" })),
    http.get(`${ORIGIN}/api/prompts`, () => HttpResponse.json({ templates: [], total: 0 })),
  ];
}

function mount() {
  const root = document.createElement("div");
  document.body.appendChild(root);
  return mountApp(root);
}

test("t() is identity under en and substitutes named placeholders", () => {
  localStorage.setItem("admin-lang", "en");
  assert.equal(t("Authors"), "Authors");
  assert.equal(t("Could not load personas: {msg}", { msg: "boom" }), "Could not load personas: boom");

  localStorage.setItem("admin-lang", "es");
  assert.equal(t("Authors"), "Autores");
  assert.equal(t("Could not load personas: {msg}", { msg: "boom" }), "No se pudo cargar las personas: boom");
});

test("every t()/{{T}} key referenced in source has an ES catalog entry (no English leak)", async () => {
  const { readFileSync, readdirSync } = await import("node:fs");
  const { fileURLToPath } = await import("node:url");
  const srcDir = fileURLToPath(new URL("../src", import.meta.url));
  const files = [];
  (function walk(dir) {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = `${dir}/${entry.name}`;
      if (entry.isDirectory()) walk(full);
      else if (entry.name.endsWith(".js") && entry.name !== "i18n.js") files.push(full);
    }
  })(srcDir);

  // Match t("..."), t('...'), and t(`...`) single-segment string-literal keys.
  // Concatenated multi-line keys (the long help prose) are covered by the
  // rendering tests below; this scanner catches the common single-literal calls.
  const re = /\bt\(\s*("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`)/g;
  const missing = [];
  for (const file of files) {
    const code = readFileSync(file, "utf8");
    let m;
    while ((m = re.exec(code)) !== null) {
      const raw = m[1];
      // Skip template literals that interpolate (cannot be a static key).
      if (raw.startsWith("`") && raw.includes("${")) continue;
      // Skip a literal that is only the first segment of a concatenated key
      // (e.g. the long help prose): the next non-space char is `+`. The full
      // concatenated key is exercised by the rendering tests instead.
      if (/^\s*\+/.test(code.slice(re.lastIndex))) continue;
      const key = raw.slice(1, -1).replace(/\\(['"`\\])/g, "$1");
      if (!(key in ES)) missing.push(`${key}  (${file.split("/").pop()})`);
    }
  }
  assert.deepEqual(missing, [], `keys missing an ES entry:\n${missing.join("\n")}`);
});

test("mounts in Spanish by default and renders translated chrome + tabs", async () => {
  localStorage.setItem("admin-lang", "es");
  server.use(...baseHandlers());
  mount();

  // Spanish nav + chrome.
  assert.ok(screen.getByRole("tab", { name: "Autores" }));
  assert.ok(screen.getByRole("tab", { name: "Fuentes" }));
  assert.ok(screen.getByRole("tab", { name: "Estado" }));
  assert.ok(screen.getByRole("heading", { name: "Nueva persona" }));
  // The health pill flips to the Spanish "en línea".
  await screen.findByText("en línea");
  // The document chrome syncs to the active locale.
  assert.equal(document.documentElement.lang, "es");
  assert.equal(document.title, "Panel de administración");
  // The lang switch shows ES pressed, EN not.
  const esBtn = screen.getByRole("button", { name: "ES" });
  const enBtn = screen.getByRole("button", { name: "EN" });
  assert.equal(esBtn.getAttribute("aria-pressed"), "true");
  assert.equal(enBtn.getAttribute("aria-pressed"), "false");
});

test("the language switch flips the whole UI live and persists the choice", async () => {
  localStorage.setItem("admin-lang", "es");
  server.use(...baseHandlers());
  const user = userEvent.setup();
  mount();

  await screen.findByText("en línea");
  assert.ok(screen.getByRole("tab", { name: "Autores" }));

  // Click EN: the app re-mounts in English without a reload.
  await user.click(screen.getByRole("button", { name: "EN" }));
  assert.equal(currentLocale(), "en");
  assert.equal(localStorage.getItem("admin-lang"), "en");
  await screen.findByText("online");
  assert.ok(screen.getByRole("tab", { name: "Authors" }));
  assert.equal(screen.queryByRole("tab", { name: "Autores" }), null);
  assert.equal(document.documentElement.lang, "en");

  // And back to ES.
  await user.click(screen.getByRole("button", { name: "ES" }));
  assert.equal(currentLocale(), "es");
  await screen.findByText("en línea");
  assert.ok(screen.getByRole("tab", { name: "Autores" }));
});

test("Spanish help prose renders for a help (?) marker", async () => {
  localStorage.setItem("admin-lang", "es");
  server.use(...baseHandlers());
  mount();
  await screen.findByText("en línea");

  // The New persona help button carries the translated prose as its accessible name.
  const helpBtn = screen.getByRole("button", {
    name: t("Create an author persona from explicit fields. The new author joins the roster below."),
  });
  assert.match(helpBtn.getAttribute("aria-label"), /Cree una persona de autor/);
});

test("a representative panel renders Spanish field labels and empty-state copy", async () => {
  localStorage.setItem("admin-lang", "es");
  server.use(...baseHandlers());
  const user = userEvent.setup();
  mount();
  await screen.findByText("en línea");

  await user.click(screen.getByRole("tab", { name: "Fuentes" }));
  const panel = screen.getByRole("tabpanel", { name: "Fuentes" });
  assert.ok(within(panel).getByRole("heading", { name: "Agregar fuente" }));
  assert.ok(within(panel).getByLabelText("Dominio"));
  assert.ok(within(panel).getByLabelText("Grupo de propiedad"));
  await within(panel).findByText("Todavía no hay fuentes.");
});
