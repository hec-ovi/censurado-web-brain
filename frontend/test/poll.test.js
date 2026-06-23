import { test } from "node:test";
import assert from "node:assert/strict";
import { pollUntil } from "../src/poll.js";

// pollUntil is a pure helper, so these run without a DOM or network.
const noWait = { wait: () => Promise.resolve() };

test("returns the result as soon as the predicate holds", async () => {
  let calls = 0;
  const out = await pollUntil(
    () => {
      calls += 1;
      return { status: calls >= 2 ? "done" : "pending" };
    },
    (r) => r.status === "done",
    { attempts: 10, ...noWait },
  );
  assert.equal(out.status, "done");
  assert.equal(calls, 2, "stops polling once satisfied");
});

test("throws a poll_timeout error when the budget is exhausted", async () => {
  let calls = 0;
  await assert.rejects(
    () =>
      pollUntil(
        () => {
          calls += 1;
          return { status: "pending" };
        },
        (r) => r.status === "done",
        { attempts: 3, ...noWait },
      ),
    (err) => {
      assert.equal(err.code, "poll_timeout");
      assert.deepEqual(err.last, { status: "pending" });
      return true;
    },
  );
  assert.equal(calls, 3, "uses the whole attempt budget");
});

test("always polls at least once, even if attempts is zero", async () => {
  let calls = 0;
  await assert.rejects(
    () =>
      pollUntil(
        () => {
          calls += 1;
          return { status: "pending" };
        },
        (r) => r.status === "done",
        { attempts: 0, ...noWait },
      ),
    (err) => err.code === "poll_timeout",
  );
  assert.equal(calls, 1);
});
