// Cloudflare Pages Function: the article like/dislike counter.
//
// Routes (same-origin from the static article pages):
//   GET  /api/reactions?slug=<article-slug>          -> {slug, up, down, vote}
//   POST /api/reactions {slug, vote: up|down|none}   -> {slug, up, down, vote}
//
// One vote per (article, voter): the voter key is sha256(REACTIONS_SALT + voter
// unit), enforced by the primary key, so a repeat vote replaces the previous one
// and "none" withdraws it. The voter unit is the full IPv4 address, but only the
// /64 prefix for IPv6 (every subscriber holds at least a /64, so hashing the full
// address would hand each visitor 2^64 votes). The address never touches the
// database, only the salted hash, and a per-voter row cap (REACTIONS_MAX_PER_IP,
// default 500) bounds how many distinct slugs one voter can write. The schema is
// created lazily (memoized per isolate), so the D1 database needs no migration
// step, just the binding. Without the binding (env.DB unset) every route answers
// 503 and the frontend keeps the bar hidden.

const SLUG_RE = /^[a-z0-9][a-z0-9-]{0,199}$/;
const VOTES = new Set(["up", "down", "none"]);
const DEFAULT_MAX_ROWS_PER_VOTER = 500;

const SCHEMA = `CREATE TABLE IF NOT EXISTS reactions (
  slug TEXT NOT NULL,
  iphash TEXT NOT NULL,
  vote TEXT NOT NULL CHECK (vote IN ('up','down')),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (slug, iphash)
)`;
const IPHASH_INDEX = "CREATE INDEX IF NOT EXISTS reactions_iphash ON reactions (iphash)";

const ensured = new WeakSet();
async function ensureSchema(db) {
  if (ensured.has(db)) return;
  await db.prepare(SCHEMA).run();
  await db.prepare(IPHASH_INDEX).run();
  ensured.add(db);
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}

// The dedup unit for one voter: the address for IPv4, the /64 prefix for IPv6
// (zone id stripped, "::" expanded, so equal prefixes compare equal).
function voterUnit(ip) {
  if (!ip.includes(":")) return ip;
  const head = ip.split("%")[0];
  const [left, right = ""] = head.split("::");
  const leftGroups = left ? left.split(":") : [];
  const rightGroups = right ? right.split(":") : [];
  const missing = Math.max(8 - leftGroups.length - rightGroups.length, 0);
  const groups = [...leftGroups, ...Array(missing).fill("0"), ...rightGroups];
  return groups
    .slice(0, 4)
    .map((g) => (g || "0").toLowerCase().replace(/^0+(?=.)/, ""))
    .join(":") + "::/64";
}

async function ipHash(request, env) {
  const ip =
    request.headers.get("CF-Connecting-IP") ||
    (request.headers.get("X-Forwarded-For") || "").split(",")[0].trim() ||
    "0.0.0.0";
  const bytes = new TextEncoder().encode((env.REACTIONS_SALT || "") + voterUnit(ip));
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

async function state(db, slug, iphash) {
  const rows = await db
    .prepare("SELECT vote, COUNT(*) AS n FROM reactions WHERE slug = ?1 GROUP BY vote")
    .bind(slug)
    .all();
  let up = 0;
  let down = 0;
  for (const row of rows.results || []) {
    if (row.vote === "up") up = Number(row.n);
    if (row.vote === "down") down = Number(row.n);
  }
  const own = await db
    .prepare("SELECT vote FROM reactions WHERE slug = ?1 AND iphash = ?2")
    .bind(slug, iphash)
    .first();
  return { slug, up, down, vote: own ? own.vote : null };
}

export async function onRequestGet({ request, env }) {
  if (!env.DB) return json({ error: "reactions_unconfigured" }, 503);
  const slug = new URL(request.url).searchParams.get("slug") || "";
  if (!SLUG_RE.test(slug)) return json({ error: "invalid_slug" }, 400);
  await ensureSchema(env.DB);
  return json(await state(env.DB, slug, await ipHash(request, env)));
}

export async function onRequestPost({ request, env }) {
  if (!env.DB) return json({ error: "reactions_unconfigured" }, 503);
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_json" }, 400);
  }
  const slug = typeof body?.slug === "string" ? body.slug : "";
  const vote = body?.vote;
  if (!SLUG_RE.test(slug)) return json({ error: "invalid_slug" }, 400);
  if (!VOTES.has(vote)) return json({ error: "invalid_vote" }, 400);
  await ensureSchema(env.DB);
  const iphash = await ipHash(request, env);
  if (vote === "none") {
    await env.DB
      .prepare("DELETE FROM reactions WHERE slug = ?1 AND iphash = ?2")
      .bind(slug, iphash)
      .run();
  } else {
    // Capacity guard: a new slug for this voter must stay under the cap; updating
    // an already-voted slug is always allowed (it writes no new row).
    const max = Number(env.REACTIONS_MAX_PER_IP) || DEFAULT_MAX_ROWS_PER_VOTER;
    const owned = await env.DB
      .prepare("SELECT COUNT(*) AS n FROM reactions WHERE iphash = ?1 AND slug <> ?2")
      .bind(iphash, slug)
      .first();
    if (Number(owned?.n ?? 0) >= max) return json({ error: "too_many_reactions" }, 429);
    await env.DB
      .prepare(
        `INSERT INTO reactions (slug, iphash, vote) VALUES (?1, ?2, ?3)
         ON CONFLICT (slug, iphash) DO UPDATE SET vote = excluded.vote, created_at = datetime('now')`
      )
      .bind(slug, iphash, vote)
      .run();
  }
  return json(await state(env.DB, slug, iphash));
}
