// A generic poll-until helper for the brain's 202-then-poll surface (synthesis
// jobs and runs). It calls `fetchFn` until `predicate(result)` holds, then
// returns that result. If the attempt budget runs out first it THROWS a
// distinguishable timeout error (code "poll_timeout") rather than returning the
// last, non-terminal value: a caller must be able to tell "reached a terminal
// state" from "still running, gave up waiting" and not report one as the other.
//
// `wait` is injectable so a test drives the loop with a fake clock and asserts
// the polling behavior without sleeping in real time.
export async function pollUntil(fetchFn, predicate, options = {}) {
  const { intervalMs = 1000, wait } = options;
  const attempts = Math.max(1, options.attempts ?? 120);
  const sleep = wait || ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));

  let last;
  for (let i = 0; i < attempts; i++) {
    last = await fetchFn();
    if (predicate(last)) return last;
    if (i < attempts - 1) await sleep(intervalMs);
  }

  const err = new Error("timed out waiting for a terminal state");
  err.code = "poll_timeout";
  err.last = last;
  throw err;
}
