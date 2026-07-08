# Telegram bridge

A Telegram bot that fronts a headless agent CLI. An allow-listed user messages the bot,
the bridge runs the selected agent (codex or agy) against this repo and the live backend,
and the reply comes back in the chat. The CLIs keep running here; nothing is exposed to
the internet except the bot.

Why Telegram and not WhatsApp: Telegram's Bot API is official, free, and carries no ban
risk, and long polling needs no public endpoint. Full comparison and sourcing:
`.research/telegram-cli-bridge/FINDINGS.md`.

## What it does, per message

1. Checks the sender's numeric id against a default-deny allowlist (edited live from the panel).
2. Picks the active agent (codex or agy, switchable live) and a per-chat session + scratch dir.
3. Runs the agent CLI as a subprocess (argv, never a shell string), from the repo so it sees
   `AGENTS.md` / `cli/SKILL.md`, with a Telegram-mode preamble prepended.
4. Streams a "typing" indicator, then sends the reply back, split to Telegram's 4096-char limit.
5. Catches failures (not installed / logged out / nonzero exit / timeout) and reports them in
   the chat and on the panel.

One worker thread per chat: same user runs serially (so a resumed session can't be
corrupted), different users run concurrently.

## Guided commands (structured shortcuts)

Alongside the freeform agent, four slash commands run a short wizard that asks for the fields
one at a time, then hands the agent a fully-specified task instead of an open-ended prompt:

- `/crear_autor` (aliases `/crearautor`, `/nuevo_autor`, `/nuevoautor`): asks name, beat,
  who-they-are, and style, then creates the author.
- `/modificar_autor` (aliases `/editar_autor`, ...): asks a handle, a field, and the new value,
  then changes only that field.
- `/nota` (aliases `/nueva_nota`, `/articulo`, ...): asks which author signs and the topic/link,
  then walks the single-article gate and publishes to production.
- `/editar_nota` (aliases `/corregir_nota`, ...): asks for a slug/link and the change, then edits
  the body/metadata and republishes.

Inline (no wizard, no agent): `/comandos` (also `/ayuda`, `/help`, `/start`) lists the commands,
`/autores` lists the authors, `/cancelar` drops the command in progress. Any other message goes
straight to the agent as before. The wizard state is per chat, and the one-worker-per-chat rule
keeps it race-free.

## Long-run progress

A turn can take minutes, so the bridge keeps the user posted while the agent runs. It streams a
"typing" indicator on an interval, and once a turn passes `progress_after` seconds it posts a
"still working" message and refreshes it in place (via `editMessageText`) every
`progress_interval` seconds with the elapsed time, then rewrites it to "Listo." when the reply is
ready.

## Auth is external, on purpose

The bridge does NOT manage model auth. `codex` (OpenAI / ChatGPT) and `agy` (Google
Gemini) are expected to be logged in already where the bridge runs; the bridge just runs
the CLI. On the host that is your existing login. In Docker, your `~/.codex` and `~/.gemini`
are mounted in, and the container runs as your host uid so it can read them. If an agent is
logged out, the bridge says so instead of trying to re-auth.

## The admin panel

A tiny web panel, localhost-only, token-gated (`TG_ADMIN_TOKEN`). It shows the bot name and
a QR to its `t.me` link, lets you switch the agent (codex / agy), and add/remove allow-listed
Telegram user ids. Changes apply live, no restart. Reach it at
`http://127.0.0.1:<TG_PANEL_PORT>/?token=<TG_ADMIN_TOKEN>` (over the host or an SSH tunnel).
The QR renders when `segno` is installed (it is in the Docker image; on a bare host run it
degrades to the plain link).

## Run it in the stack (Docker, opt-in)

The service lives in the root `docker-compose.yml` behind the `bridge` profile, so a plain
`up` never starts it.

```bash
mkdir -p data/tg-bridge                 # writable state (config.json, sessions, scratch, HOME)
# in the repo's .env: TELEGRAM_BOT_TOKEN, TG_ADMIN_TOKEN (openssl rand -hex 16),
#   TG_ALLOWED_IDS, DEFAULT_AGENT, TG_UID/TG_GID (your id -u / id -g),
#   TG_CODEX_HOME=~/.codex, TG_GEMINI_HOME=~/.gemini
docker compose --profile bridge up -d --build tg-bridge
docker compose --profile bridge logs -f tg-bridge
```

It joins the `censurado` network (so the agent reaches `publish`), mounts this repo
read-only at `/repo`, and reads the operator token from `/repo/.env`. Hardening: non-root
(your uid), `cap_drop: [ALL]`, `no-new-privileges`, tmpfs `/tmp`, admin port bound to
`127.0.0.1` only. (`read_only` root is intentionally off: the agent CLIs write caches to the
mounted HOME.)

## Run it on the host (dev / no Docker)

Uses your already-authenticated host `codex` / `agy` directly. Set `bridge/telegram/.env`
(token, `TG_ADMIN_TOKEN`, `TG_ALLOWED_IDS`, `DEFAULT_AGENT`, `REPO_DIR`, `DATA_DIR`,
`TG_PANEL_PORT`) and run `router.py` with those vars in the environment. `router.py` is
stdlib-only, so any `python3` works.

## The agents

Two built-ins, both run from the repo (`REPO_DIR`), both switchable from the panel:

- **codex** (OpenAI / ChatGPT): `codex exec -c model_reasoning_effort=low --dangerously-bypass-approvals-and-sandbox {msg}`
- **agy** (Google Gemini): `agy --add-dir {repo} --dangerously-skip-permissions -p {msg}`

Override a template with `AGENT_CMD_CODEX` / `AGENT_CMD_AGY`. Tokens: `{repo}`, `{workdir}`,
`{session}`, `{msg}` (present -> passed as an argv element; absent -> the prompt is sent on
stdin). The bridge exports a per-chat `CENSURADO_WORK` so the newsroom write walk's scratch
files are isolated per user, and strips its own secrets (`TELEGRAM_BOT_TOKEN`,
`TG_ADMIN_TOKEN`) from the agent's environment.

## Security

Piping untrusted chat text into a tool-running agent is the "lethal trifecta", so: the
allowlist is default-deny; the agent runs non-root with the bridge's tokens stripped from
its env; the panel is localhost + token-gated; and the message is never interpolated into a
shell string. The agents run with approvals bypassed so they can operate the newsroom (same
posture as `automation/auto-batch.sh`), which is acceptable because only allow-listed ids
can trigger them; egress is not locked to an allowlist, so put an egress proxy in front if
you need that.

## Tests

`tests/test_telegram_bridge.py` (router: allowlist, agent select/switch, preamble, error
catching, token isolation, admin panel auth + edits) and the `tg-bridge` case in
`tests/test_compose.py` (opt-in profile, localhost-only, non-root, repo read-only). Run with
`make test` or `.venv/bin/pytest tests/test_telegram_bridge.py`.
