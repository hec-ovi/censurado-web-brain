# The serve loop

One resident host process that keeps the product alive 24/7. Run it with `./run.sh serve`
(or the `censurado-serve@.service` systemd template here); config in
`supervisor.config.json`; tests in `serve.e2e.test.mjs` (`npm test`). It runs on the host,
not inside docker, because the Telegram bridge's agent CLI and its auth state live there.

```
supervisor (host process, systemd)
  |- docker stack        publish + generate + site + executor (comfyui optional)
  '- telegram bridge     telegram-bot-skill, node src/bot.ts, one configured adapter
```

Scheduled edition batches are NOT this loop's job: the `executor` compose service fires
them from the backend's schedule registry (see `automation/executor/CONTRACT.md`), so the
clock runs whenever docker runs.

## What it guarantees

- **Docker keeper.** Each tick runs the config's `checkCmd` (the CLI `status` verb); on
  failure it runs `upCmd`, budget-limited so a broken stack cannot crash-loop. When the
  budget is spent it alerts the owner once and keeps checking; recovery clears the alert.
- **Bridge keeper.** Starts the telegram-bot-skill bridge from the sibling checkout with
  the ONE adapter named in `bridge.adapter`, and restarts it when it dies, budget-limited.
  A spawn failure (missing checkout/binary) is an owner alert, not a loop.
- **Credential cascade.** `bridge.envCascade` keys (`TELEGRAM_BOT_TOKEN`, `OWNER_ID`) read
  from the bridge checkout's own `.env` first, then this repo's `.env`, then the process
  env, so one main `.env` runs the whole product. Owner notices use the same lookup.
- **Single instance.** A pid lockfile with stale-pid takeover; a second `serve` refuses.
- **Boundaries.** Agents operate the site through `python3 cli/censurado.py` verbs only.
  Going live stays human-gated: no restart or recovery path may trigger a deploy.

## Tests

`serve.e2e.test.mjs` drives the loop end to end against fake binaries (a scripted bridge;
no docker, no network): boot + lock, restart of a dead bridge, the restart budget holding
with an owner alert, the credential cascade from the main `.env` and from the process env,
and the spawn-failure alert.
