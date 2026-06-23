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
  server.use(http.get(`${ORIGIN}/api/runs/r9`, () => new HttpResponse(null, { status: 502 })));
  await assert.rejects(
    () => api.getRun("r9"),
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
