// The serve loop (automation/supervisor/REQUIREMENTS.md): one resident host
// process that keeps the docker stack and the Telegram bridge alive 24/7. The
// bridge answers through the ONE adapter named in the config; scheduled edition
// batches are the executor compose service's job, not this loop's. All commands
// and intervals come from the config file; this module owns only the wiring.

import { appendFileSync, mkdirSync } from 'node:fs'
import { dirname, isAbsolute, resolve } from 'node:path'
import {
  acquireLock, findOwnerChatId, parseEnvFile, releaseLock, runCommand, startChild, telegramNotify,
} from './procs.mjs'

// Resolve every relative path in the raw config against the config file's
// directory, so the file can say "../.." and mean this repo.
export function resolveConfig(raw, baseDir) {
  const abs = (p) => (isAbsolute(p) ? p : resolve(baseDir, p))
  const cfg = structuredClone(raw)
  cfg.repoRoot = abs(raw.repoRoot ?? '../..')
  cfg.bridge.dir = abs(raw.bridge.dir)
  cfg.bridge.stateFile = resolve(cfg.bridge.dir, raw.bridge.stateFile ?? 'bot-state.json')
  cfg.bridge.envFile = resolve(cfg.bridge.dir, raw.bridge.envFile ?? '.env')
  cfg.logFile = abs(raw.logFile ?? '../logs/serve.log')
  cfg.lockFile = abs(raw.lockFile ?? '../logs/.serve.lock')
  return cfg
}

class Budget {
  constructor(max, windowMs, now) {
    this.max = max
    this.windowMs = windowMs
    this.now = now
    this.hits = []
  }

  // Records an attempt; false means the budget is exhausted for this window.
  hit() {
    const cutoff = this.now() - this.windowMs
    this.hits = this.hits.filter((t) => t >= cutoff)
    if (this.hits.length >= this.max) return false
    this.hits.push(this.now())
    return true
  }
}

export class Supervisor {
  constructor(config, { log, now = () => Date.now(), fetchImpl = fetch } = {}) {
    this.cfg = config
    this.now = now
    this.fetchImpl = fetchImpl
    this.log = log ?? ((line) => {
      const stamped = `${new Date(this.now()).toISOString()} ${line}`
      console.log(stamped)
      try { appendFileSync(this.cfg.logFile, stamped + '\n') } catch { /* log dir gone */ }
    })
    this.bridge = null
    this.stopping = false
    this.busy = new Set() // reentrancy guards per tick kind
    this.timers = []
    this.bridgeBudget = new Budget(config.restartBudget.max, config.restartBudget.windowMs, now)
    this.dockerBudget = new Budget(config.restartBudget.max, config.restartBudget.windowMs, now)
    this.alerted = new Set() // one alert per stuck condition, cleared on recovery
  }

  async start() {
    mkdirSync(dirname(this.cfg.logFile), { recursive: true })
    if (!acquireLock(this.cfg.lockFile)) throw new Error(`another supervisor holds ${this.cfg.lockFile}`)
    this.log(`serve: up, adapter=${this.cfg.bridge.adapter}`)
    await this.#dockerTick()
    await this.#exclusive(() => this.#startBridge())
    const t = setInterval(() => this.#guarded('docker', async () => {
      await this.#dockerTick()
      // Reconcile: a bridge that stayed down (budget hold) retries once the
      // window has rolled over.
      if (this.bridge === null) await this.#exclusive(() => this.#startBridge())
    }), this.cfg.intervals.dockerCheckMs)
    this.timers.push(t)
  }

  async stop() {
    this.stopping = true
    for (const t of this.timers) clearInterval(t)
    this.timers = []
    await this.#exclusive(() => this.#stopBridge())
    releaseLock(this.cfg.lockFile)
    this.log('serve: stopped')
  }

  async #guarded(kind, fn) {
    if (this.stopping || this.busy.has(kind)) return
    this.busy.add(kind)
    try {
      await fn()
    } catch (err) {
      this.log(`serve: ${kind} tick error: ${err?.message ?? err}`)
    } finally {
      this.busy.delete(kind)
    }
  }

  // Every bridge stop/start goes through here so an exit event and a reconcile
  // tick can never race the bridge into two live children.
  #exclusive(fn) {
    const run = this.#serial.then(fn, fn)
    this.#serial = run.catch(() => {})
    return run
  }

  #serial = Promise.resolve()

  // ---- docker -------------------------------------------------------------

  async #dockerTick() {
    const check = await runCommand(this.cfg.docker.checkCmd, {
      cwd: this.cfg.repoRoot, timeoutMs: this.cfg.docker.checkTimeoutMs,
    })
    if (check.code === 0) {
      if (this.alerted.delete('docker')) this.log('docker: healthy again')
      return
    }
    if (!this.dockerBudget.hit()) {
      await this.#alertOnce('docker', `docker stack is down and the restart budget is spent; holding. Last check: ${check.stderr.trim().slice(0, 200)}`)
      return
    }
    this.log('docker: check failed, running up')
    const up = await runCommand(this.cfg.docker.upCmd, {
      cwd: this.cfg.repoRoot, timeoutMs: this.cfg.docker.upTimeoutMs,
    })
    if (up.code !== 0) this.log(`docker: up failed (exit ${up.code}): ${(up.stderr || up.stdout).trim().slice(0, 300)}`)
  }

  // ---- bridge -------------------------------------------------------------

  async #startBridge() {
    if (this.bridge !== null || this.stopping) return // idempotent
    if (!this.bridgeBudget.hit()) {
      await this.#alertOnce('bridge', 'bridge keeps dying; restart budget spent, holding until the window rolls over')
      return
    }
    this.log(`bridge: starting with adapter=${this.cfg.bridge.adapter}`)
    // The handle-identity check makes an intentional stop (shutdown) silent:
    // only the CURRENT bridge dying unexpectedly reaches onBridgeExit.
    const handle = startChild(this.cfg.bridge.startCmd, {
      cwd: this.cfg.bridge.dir,
      graceMs: this.cfg.bridge.graceMs ?? 10_000,
      env: { ...process.env, ...this.#cascadeEnv(), AGENT_ADAPTER: this.cfg.bridge.adapter, AGENT_CWD: this.cfg.repoRoot },
      onLine: () => {
        this.alerted.delete('bridge')
        this.alerted.delete('bridge-bin')
      },
      onExit: (exit) => {
        if (this.stopping || this.bridge !== handle) return
        this.bridge = null
        void this.#onBridgeExit(exit).catch((err) => this.log(`serve: bridge-exit error: ${err?.message ?? err}`))
      },
    })
    this.bridge = handle
  }

  async #onBridgeExit(exit) {
    if (exit.spawnError !== undefined) {
      await this.#alertOnce('bridge-bin', `bridge cannot start (${exit.spawnError.code ?? exit.spawnError.message}); check bridge.dir/startCmd in the config`)
      return
    }
    const evidence = exit.recentOutput.trim().split('\n').at(-1)?.slice(0, 200) ?? ''
    this.log(`bridge: exited (code ${exit.code}) last="${evidence}"`)
    await this.#exclusive(() => this.#startBridge())
  }

  async #stopBridge() {
    const old = this.bridge
    this.bridge = null // detach first so onExit sees a stale handle and stays silent
    if (old !== null) await old.stop()
  }

  // ---- env cascade ----------------------------------------------------------

  // A cascaded key reads from the bridge checkout's own .env first, then falls
  // back to this repo's .env, then to the supervisor's own process env (a systemd
  // Environment= line), so one main .env can hold the bot token for the whole
  // product. The bridge child inherits process.env anyway; without the last hop
  // the supervisor's OWN notices would silently no-op on an env-only setup.
  #envValue(key) {
    const own = parseEnvFile(this.cfg.bridge.envFile)[key]
    if (own !== undefined && own !== '') return own
    const main = parseEnvFile(resolve(this.cfg.repoRoot, '.env'))[key]
    if (main !== undefined && main !== '') return main
    const proc = process.env[key]
    return proc === undefined || proc === '' ? undefined : proc
  }

  #cascadeEnv() {
    const out = {}
    for (const key of this.cfg.bridge.envCascade ?? []) {
      const value = this.#envValue(key)
      if (value !== undefined) out[key] = value
    }
    return out
  }

  // ---- owner notices --------------------------------------------------------

  async #alertOnce(key, text) {
    if (this.alerted.has(key)) return
    this.alerted.add(key)
    this.log(`alert: ${text}`)
    await this.#notifyOwner(`supervisor: ${text}`)
  }

  async #notifyOwner(text) {
    if (this.cfg.notify !== true) return
    const token = this.#envValue('TELEGRAM_BOT_TOKEN')
    const ownerFromEnv = Number(this.#envValue('OWNER_ID'))
    const chatId = findOwnerChatId(this.cfg.bridge.stateFile)
      ?? (Number.isInteger(ownerFromEnv) && ownerFromEnv > 0 ? ownerFromEnv : undefined)
    if (token === undefined || chatId === undefined) return
    await telegramNotify(token, chatId, text, this.fetchImpl)
  }
}
