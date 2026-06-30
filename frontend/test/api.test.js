import "./setup.js";
import { test } from "node:test";
import assert from "node:assert/strict";
import { http, HttpResponse } from "msw";
import { installServer, ORIGIN } from "./msw.js";
import { installDom } from "./dom.js";
import { api } from "../src/api.js";

installDom();
const server = installServer();

test("listPersonas parses the personas array", async () => {
  server.use(
    http.get(`${ORIGIN}/api/personas`, () =>
      HttpResponse.json({ personas: [{ id: "ada", display_name: "Ada", beat: "tech" }] })),
  );
  const data = await api.listPersonas();
  assert.equal(data.personas[0].id, "ada");
});

test("listPersonas forwards the beat as a query param", async () => {
  let seen;
  server.use(
    http.get(`${ORIGIN}/api/personas`, ({ request }) => {
      seen = new URL(request.url).searchParams.get("beat");
      return HttpResponse.json({ personas: [] });
    }),
  );
  await api.listPersonas("world");
  assert.equal(seen, "world");
});

test("a problem+json error becomes an Error carrying code and status", async () => {
  server.use(
    http.post(`${ORIGIN}/api/personas`, () =>
      HttpResponse.json({ status: 422, code: "invalid_beat", detail: "beat must be one of (tech, world)" }, { status: 422 })),
  );
  await assert.rejects(
    () => api.createPersona({ display_name: "x", beat: "bad", seed: "s" }),
    (err) => {
      assert.equal(err.code, "invalid_beat");
      assert.equal(err.status, 422);
      assert.match(err.message, /beat must be one of/);
      return true;
    },
  );
});

test("a body-less error still throws with an http_<status> code", async () => {
  server.use(http.get(`${ORIGIN}/api/personas/r9`, () => new HttpResponse(null, { status: 502 })));
  await assert.rejects(
    () => api.getPersona("r9"),
    (err) => {
      assert.equal(err.status, 502);
      assert.equal(err.code, "http_502");
      return true;
    },
  );
});

test("a non-JSON error body is captured as raw text", async () => {
  server.use(
    http.get(`${ORIGIN}/api/personas/x`, () =>
      new HttpResponse("<html>Bad Gateway</html>", { status: 502, headers: { "content-type": "text/html" } })),
  );
  await assert.rejects(
    () => api.getPersona("x"),
    (err) => {
      assert.equal(err.status, 502);
      assert.equal(err.code, "http_502");
      assert.match(err.body.raw, /Bad Gateway/);
      return true;
    },
  );
});

test("listPortals hits /portals and forwards the enabled filter, omitting null filters", async () => {
  let url;
  server.use(
    http.get(`${ORIGIN}/api/portals`, ({ request }) => {
      url = new URL(request.url);
      return HttpResponse.json({ portals: [{ id: "p1", enabled: true }] });
    }),
  );
  const data = await api.listPortals({ enabled: true });
  assert.equal(data.portals[0].id, "p1");
  assert.equal(url.searchParams.get("enabled"), "true");
  assert.equal(url.searchParams.has("limit"), false, "null/absent filters are omitted");
  assert.equal(url.searchParams.has("offset"), false);
});

test("deletePortal issues a DELETE and resolves to null on 204", async () => {
  let method;
  server.use(
    http.delete(`${ORIGIN}/api/portals/p%201`, ({ request }) => {
      method = request.method;
      return new HttpResponse(null, { status: 204 });
    }),
  );
  const result = await api.deletePortal("p 1"); // id is percent-encoded in the path
  assert.equal(method, "DELETE");
  assert.equal(result, null);
});

test("putPersonaSources sends PUT with a {sources} body", async () => {
  let body;
  server.use(
    http.put(`${ORIGIN}/api/personas/ada/sources`, async ({ request }) => {
      body = await request.json();
      return HttpResponse.json({ sources: body.sources });
    }),
  );
  const data = await api.putPersonaSources("ada", ["example.com", "another.org"]);
  assert.deepEqual(body, { sources: ["example.com", "another.org"] });
  assert.deepEqual(data.sources, ["example.com", "another.org"]);
});

test("getPrompt forwards the key as a query param on /prompts/template", async () => {
  let key;
  server.use(
    http.get(`${ORIGIN}/api/prompts/template`, ({ request }) => {
      key = new URL(request.url).searchParams.get("key");
      return HttpResponse.json({ key, body: "tmpl" });
    }),
  );
  const data = await api.getPrompt("manager.system");
  assert.equal(key, "manager.system");
  assert.equal(data.body, "tmpl");
});

test("promoteStyleVersion POSTs to the version's promote endpoint", async () => {
  let method;
  server.use(
    http.post(`${ORIGIN}/api/editorial/style/versions/5/promote`, ({ request }) => {
      method = request.method;
      return HttpResponse.json({ version: 5, active: true });
    }),
  );
  const data = await api.promoteStyleVersion(5);
  assert.equal(method, "POST");
  assert.equal(data.version, 5);
});
