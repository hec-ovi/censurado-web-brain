# The 24/7 supervisor loop: requirements

Status: v1 SHIPPED 2026-07-09, in this directory. Run it with `./run.sh serve` (or the
`censurado-serve@.service` systemd template unit here); config in `supervisor.config.json`; tests in
`*.test.mjs` (`npm test`). Verified live: it raised the down docker stack, detected a real
AUTH failure on the then-primary CLI through the canary, and demoted to the next lane on
real evidence. Still open against this spec: the remaining cloud-CLI adapters upstream in
telegram-bot-skill (R5 gap;
until then the bridge lane settles on `claude-code`), routing auto-batch through the chain
(R7 phase 2), and the induced-failure soak (R8 gate, needs days of wall-clock). The supervisor
runs on the host, not inside docker: the agent CLIs and their auth state live there, out of
a container's reach.

Written 2026-07-09. The loop is our own lean code, no node-graph orchestrator: we keep the
moving parts we already own.

## Decisions locked

- **Telegram layer = [telegram-bot-skill](https://github.com/hec-ovi/telegram-bot-skill).**
  It is tested and working (zero-dep Node bridge, tier gate, per-chat queue, session resume).
  The in-repo `bridge/telegram/` (router.py, its Dockerfile, the `bridge` compose profile)
  retires when this ships. Less code carried here; the bridge's own repo owns that surface.
  Retiring the profile also removes the known `CENSURADO_PUBLISH: publish:8082` port bug that
  only exists on that path.
- **The loop is plain host code; no node-graph orchestrator.** Graph tools are too heavy
  for this flow, and containerized they cannot reach the host CLIs. Revisit only if the flow
  becomes a branching multi-integration graph.
- **The loop runs on the host, outside docker**, as one plain process (systemd unit). The
  agents are host CLIs; that is why it cannot live in a container.
- **README lists telegram-bot-skill as a requirement and nothing more.** Its setup docs live
  in its own repo.

## What it is

One resident service, "the supervisor", that keeps the whole product alive 24/7 and re-spins
any layer that dies:

```
supervisor (host process, systemd)
  |- docker stack        publish + generate + site (comfyui optional)
  |- telegram bridge     telegram-bot-skill, node src/bot.ts
  '- active agent        fallback chain: cloud CLIs in config order -> pi (local model)
```

If the active CLI breaks (login expired, quota, hang), the supervisor kills that lane,
promotes the next entry, and the in-flight work continues; that one breaks, the next takes
it. From Telegram the worst a
user ever sees is one reconnect-style status line and some extra latency.

## R1: serve loop, 24/7

- One entry point (`run.sh serve` or the systemd unit directly) brings everything up from a
  cold boot: compose fast lane, bridge, agent chain. No manual step in between.
- Watchdog per layer: docker services via their health endpoints (`/healthz`, `status` verb),
  bridge via its process plus a Telegram API liveness check, agent via R3.
- A dead layer restarts automatically with exponential backoff and a restart budget. Blowing
  the budget stops the crash-loop, alerts the owner (Telegram if the bridge is up, always the
  log), and keeps probing at a slow interval.
- Survives host reboot (systemd `enable`). All state on disk; nothing precious in memory.
- "24/7 proven" is a shipping gate: a soak run of at least several days unattended, with
  induced failures at every layer, before this is called done.

## R2: agent fallback chain

- Ordered chain, config-driven: whatever headless cloud CLIs the host has, in preference
  order, ending in `pi (local)` (the shipped chain lives in `supervisor.config.json`). Per entry:
  binary, bridge adapter name, canary probe; the failure patterns (see R3) are one shared
  table in the same config, not per entry. Adding an agent is config plus an adapter, not
  supervisor code.
- **The terminal fallback is a local model: pi + llama.cpp on the Strix box.** The cloud
  agents all share the failure modes this doc exists for (login request, credit exhausted,
  rate limit); a local model has none of them, it fails only with the machine itself. The
  pieces already exist: the skill ships a live-verified `pi` adapter, and the rig is the
  `examples/pi-gemma` compose (llama.cpp Vulkan serving a GGUF, mirroring
  `llama-vulkan-strix`). Degraded capability is accepted and explicit: pi's job at the end of
  the chain is to keep the bot answering (status, light verbs, holding the queue), not to
  guarantee full article walks; a mid-piece walk waits at its ledger for a cloud agent to
  heal unless the local model proves it can clear the gates.
- The active agent is demoted on a classified failure; the next one is promoted; the
  in-flight task resumes per R4.
- Recovery upward: the supervisor probes the demoted, preferred agent on an interval and
  returns to it when healthy, but only at a task boundary, never mid-article.
- Whole chain down: the bridge stays up, the per-chat queue holds messages, the owner is
  alerted, and the probe loop keeps trying. Messages are never dropped.

## R3: failure detection (the actual engineering problem)

The challenge: know from one level above that the active CLI is failing login or exceeded
its usage limit, without a human reading the chat. Signals, in order of trust:

1. **Process signals.** Spawn failure (binary missing), non-zero exit, wall-clock timeout,
   and a silence timeout (no stream events for N minutes; `auto-batch.sh` already does the
   wall-clock version with `timeout`).
2. **Stream signals.** The bridge adapter contract already emits `{kind: 'error', reason}`.
   The supervisor must be able to see those events; the exact surface (structured log tail,
   bridge exit code, or a small status endpoint added upstream) is a design decision at build
   time.
3. **Pattern classification.** Raw stderr / error reason is mapped to a class by a per-CLI
   pattern table that lives in a config file, not in code, so a new error string is a config
   edit, not a redeploy:
   - `AUTH`: login or oauth expired. Demote immediately; it never heals on
     its own within minutes.
   - `QUOTA`: usage or rate limit exceeded (429s). Demote immediately;
     record a cool-down before re-probing.
   - `TRANSIENT`: network, 5xx, overloaded. Retry the same agent with backoff; never fall
     back on the first hit. Repeated transients inside the window count toward the same
     N-in-M demotion threshold as `UNKNOWN`, so a lane that stays flaky does demote.
   - `UNKNOWN`: crash, unclassified. Counts toward the demotion threshold: N
     failures within M minutes demote.
4. **Canary probe.** A cheap fixed prompt ("reply OK") with a short timeout distinguishes
   "agent is broken" from "task is hard". Run it before demoting on ambiguous evidence and
   before promoting an agent back up.

Every classification decision is logged with the raw evidence line, so the pattern table can
be improved from real incidents.

## R4: work recovery across agents

- The unit of recovery is the gated step workflow. All durable state already lives outside
  the agent process: `scratch/article-N/` (`ledger.md`, `draft.md`, `image.json`) plus the
  step gate position. Hard requirement: a fresh agent of any brand must be able to continue
  from the ledger and the `step` verb alone, with zero dependence on the dead agent's session
  memory.
- On failover mid-piece the supervisor starts the successor with a standard resume prompt:
  bind the repo (some CLIs need an explicit `--add-dir`), read the ledger, ask `step` for the current node,
  continue the walk. Same operator-skill boundary as any run.
- Prompt-side requirement: audit the workflow nodes so the ledger always records enough to
  resume (mode, chosen author, current node, sources gathered so far). If a node can pass its
  gate without recording that, add it to the gate.
- Telegram continuity: the bridge's per-chat queue holds messages during the swap; after
  promotion the bot posts one line ("reconnected, continuing") and works the queue. Publish
  stays safe against double-fire because a re-POST of the same piece is an idempotency replay
  on the backend.

## R5: bridge integration

- telegram-bot-skill is the only Telegram code. It ships `claude-code` and `pi` adapters
  today (both with session resume), so the top and the bottom of the chain are covered;
  **adapters for the other cloud CLIs are the gap**. Preferred route: contribute them upstream in that
  repo (it is ours), keeping this repo adapter-free. Each adapter must pass the raw error
  text through its error events so R3 can classify.
- Agent swap mechanism: restart the bridge with a different `AGENT_ADAPTER` (simple, uses
  what exists) unless in-process adapter switching lands upstream first. The bridge state
  file survives restarts (users, tiers, offsets); agent sessions do not, which is exactly
  what the R4 resume prompt covers.
- Credential cascade (shipped with v1): the keys in `bridge.envCascade` (default
  `TELEGRAM_BOT_TOKEN`, `OWNER_ID`) are read from the bridge checkout's own `.env` first,
  then from this repo's `.env`, and injected as real environment (which the bridge gives
  precedence over its file). One main `.env` runs the whole product; a bridge-local `.env`
  still wins when present. The owner notice uses the same lookup, falling back to
  `OWNER_ID` when no owner is claimed in the bridge state file yet.
- Access stays owner-only until the skill's per-tier tool enforcement (its phase 6) lands;
  its runner already refuses non-owner tiers on adapters without hard tool gating, keep that.

## R6: boundaries that do not move

- Agents operate the site through `python3 cli/censurado.py` verbs only; the operator-skill
  boundary holds on every agent in the chain.
- Going live stays human-gated. No restart, fallback, or recovery path may trigger a deploy;
  `publicar`/deploy runs only from an explicit owner message.
- One writer per lane today: `auto-batch.sh` and the supervisor each hold their own flock,
  so each lane is single-instance. The cross-lane guarantee (a scheduled batch and the
  bridge agent never walking the same scratch workspace) is NOT enforced yet; it lands when
  auto-batch routes through the chain (R7 phase 2). Until then, do not schedule auto-batch
  while the serve loop is answering an owner walk.

## R7: repo changes when this ships

- Delete `bridge/telegram/` and the `bridge` compose profile; drop their README/layout
  mentions. DONE 2026-07-09 (compose now carries a guard comment; `test_compose.py` guards
  against the service creeping back).
- README prerequisites gain one line: telegram-bot-skill checked out as a sibling (or
  installed as a skill). That is it. DONE.
- `automation/auto-batch.sh` keeps working (one headless CLI run, `AUTO_BATCH_AGENT_CMD`). Phase 2 (open): route
  it through the same supervisor chain so scheduled batches inherit the fallback for free.
- Chain order, thresholds, and probe intervals live in `supervisor.config.json` here, not
  in `.env` (they are wiring, not machine secrets).

## R8: tests (required before "done")

- Local suite, no CI. Fake agent binaries (telegram-bot-skill already tests its adapters
  this way) that simulate: auth-failure stderr, quota-exceeded output, a hang (silence), a
  clean run. Assert: classification per R3, demotion order, cool-down and re-promotion,
  resume prompt contents per R4.
- A soak/chaos script that kills each layer in turn (a docker service, the bridge, the
  active agent) and asserts the supervisor restores it within its budget, with the Telegram
  queue intact.

## Open questions (answer at design time)

- The exact surface where the supervisor reads adapter error events (log tail vs exit codes
  vs a status endpoint in the bridge).
- Whether `comfyui` joins the watched set (GPU box only) or stays opt-in manual.
- Whether the resume prompt lives here (`prompts/` or `automation/`) or ships as part of the
  bridge adapter contract.
