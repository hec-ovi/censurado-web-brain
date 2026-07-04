// End-to-end tests for the reactions Pages Function: the real onRequestGet /
// onRequestPost handlers, real Request objects, and the real SQL running on an
// actual SQLite engine (node:sqlite) behind a minimal D1-shaped adapter, so the
// schema, the upsert, and the (slug, iphash) dedup are what production executes.
import { DatabaseSync } from "node:sqlite";
import { beforeEach, describe, expect, test } from "vitest";
import { onRequestGet, onRequestPost } from "./reactions.js";

// The subset of the D1 client API the function uses: prepare().bind().all()/first()/run().
function d1(db) {
  return {
    prepare(sql) {
      return {
        args: [],
        bind(...args) {
          this.args = args;
          return this;
        },
        async all() {
          return { results: db.prepare(sql).all(...this.args) };
        },
        async first() {
          return db.prepare(sql).get(...this.args) ?? null;
        },
        async run() {
          db.prepare(sql).run(...this.args);
          return { success: true };
        },
      };
    },
  };
}

let env;
beforeEach(() => {
  env = { DB: d1(new DatabaseSync(":memory:")), REACTIONS_SALT: "test-salt" };
});

function get(slug, ip = "203.0.113.7") {
  const request = new Request(`https://site.example/api/reactions?slug=${slug}`, {
    headers: { "CF-Connecting-IP": ip },
  });
  return onRequestGet({ request, env });
}

function post(body, ip = "203.0.113.7") {
  const request = new Request("https://site.example/api/reactions", {
    method: "POST",
    headers: { "CF-Connecting-IP": ip, "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  return onRequestPost({ request, env });
}

describe("GET /api/reactions", () => {
  test("answers zero counts and no own vote for a fresh article", async () => {
    const res = await get("author-a-una-nota-2026");
    expect(res.status).toBe(200);
    expect(res.headers.get("Cache-Control")).toBe("no-store");
    expect(await res.json()).toEqual({
      slug: "author-a-una-nota-2026",
      up: 0,
      down: 0,
      vote: null,
    });
  });

  test("rejects a malformed slug", async () => {
    const res = await get("../../etc/passwd");
    expect(res.status).toBe(400);
  });

  test("answers 503 when the D1 binding is absent", async () => {
    env = {};
    const res = await get("author-a-una-nota-2026");
    expect(res.status).toBe(503);
  });
});

describe("POST /api/reactions", () => {
  test("records a vote and returns the new counts with the own vote", async () => {
    const res = await post({ slug: "nota-1", vote: "up" });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ slug: "nota-1", up: 1, down: 0, vote: "up" });
  });

  test("a repeat vote from the same IP replaces, never accumulates", async () => {
    await post({ slug: "nota-1", vote: "up" });
    await post({ slug: "nota-1", vote: "up" });
    const flipped = await (await post({ slug: "nota-1", vote: "down" })).json();
    expect(flipped).toEqual({ slug: "nota-1", up: 0, down: 1, vote: "down" });
  });

  test("'none' withdraws the vote", async () => {
    await post({ slug: "nota-1", vote: "up" });
    const res = await (await post({ slug: "nota-1", vote: "none" })).json();
    expect(res).toEqual({ slug: "nota-1", up: 0, down: 0, vote: null });
  });

  test("different IPs count separately and see their own vote only", async () => {
    await post({ slug: "nota-1", vote: "up" }, "203.0.113.7");
    await post({ slug: "nota-1", vote: "up" }, "203.0.113.8");
    await post({ slug: "nota-1", vote: "down" }, "203.0.113.9");
    const seen = await (await get("nota-1", "203.0.113.9")).json();
    expect(seen).toEqual({ slug: "nota-1", up: 2, down: 1, vote: "down" });
    const fresh = await (await get("nota-1", "203.0.113.10")).json();
    expect(fresh.vote).toBeNull();
  });

  test("votes are scoped per article", async () => {
    await post({ slug: "nota-1", vote: "up" });
    const other = await (await get("nota-2")).json();
    expect(other).toEqual({ slug: "nota-2", up: 0, down: 0, vote: null });
  });

  test("rejects an unknown vote value and a bad body", async () => {
    expect((await post({ slug: "nota-1", vote: "love" })).status).toBe(400);
    expect((await post({ slug: "bad slug!", vote: "up" })).status).toBe(400);
    const raw = new Request("https://site.example/api/reactions", {
      method: "POST",
      body: "not json",
    });
    expect((await onRequestPost({ request: raw, env })).status).toBe(400);
  });

  test("the IP hash is salted: same IP, different salt, different voter key", async () => {
    await post({ slug: "nota-1", vote: "up" });
    env.REACTIONS_SALT = "rotated";
    const after = await (await get("nota-1")).json();
    expect(after.up).toBe(1);
    expect(after.vote).toBeNull();
  });

  test("a raw 'null' JSON body answers 400, never a crash", async () => {
    const raw = new Request("https://site.example/api/reactions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "null",
    });
    expect((await onRequestPost({ request: raw, env })).status).toBe(400);
  });

  test("two IPv6 addresses in the same /64 are ONE voter", async () => {
    await post({ slug: "nota-1", vote: "up" }, "2001:db8:1:2:aaaa:bbbb:cccc:1");
    await post({ slug: "nota-1", vote: "up" }, "2001:db8:1:2::9");
    const seen = await (await get("nota-1", "2001:0DB8:0001:0002:ffff::5")).json();
    expect(seen).toEqual({ slug: "nota-1", up: 1, down: 0, vote: "up" });
  });

  test("different IPv6 /64 prefixes stay separate voters", async () => {
    await post({ slug: "nota-1", vote: "up" }, "2001:db8:1:2::1");
    await post({ slug: "nota-1", vote: "up" }, "2001:db8:1:3::1");
    const seen = await (await get("nota-1", "203.0.113.7")).json();
    expect(seen.up).toBe(2);
  });

  test("a voter's distinct-slug capacity is capped, but updates still pass", async () => {
    env.REACTIONS_MAX_PER_IP = "2";
    await post({ slug: "nota-1", vote: "up" });
    await post({ slug: "nota-2", vote: "up" });
    expect((await post({ slug: "nota-3", vote: "up" })).status).toBe(429);
    // Changing the vote on an already-voted slug writes no new row: allowed.
    expect((await post({ slug: "nota-1", vote: "down" })).status).toBe(200);
    // Withdrawing frees a slot for a new article.
    await post({ slug: "nota-2", vote: "none" });
    expect((await post({ slug: "nota-3", vote: "up" })).status).toBe(200);
    // Another voter is unaffected by this voter's cap.
    expect((await post({ slug: "nota-9", vote: "up" }, "203.0.113.99")).status).toBe(200);
  });
});
