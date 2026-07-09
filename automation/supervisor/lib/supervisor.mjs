// The serve loop (automation/supervisor/REQUIREMENTS.md): one resident
// process that keeps the docker stack, the Telegram bridge, and the active
// agent alive 24/7, and walks the agent fallback chain when the active one
// breaks. All commands, patterns, and intervals come from the config file;
// this module owns only the wiring.

import { appendFileSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, isAbsolute, resolve } from 'node:path'
import { classify, compilePatterns } from './classify.mjs'
import { Chain } from './chain.mjs'
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
  cfg.stateFile = abs(raw.stateFile ?? '../logs/.serve-state.json')
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
    this.patterns = compilePatterns(config.patterns)
    this.chain = new Chain(config.chain, config, now)
    this.log = log ?? ((line) => {
      const stamped = `${new Date(this.now()).toISOString()} ${line}`
      console.log(stamped)
      try { appendFileSync(this.cfg.logFile, stamped + '\n') } catch { /* log dir gone */ }
    })
    this.bridge = null
    this.stopping = false
    this.lastActivity = 0
    this.busy = new Set() // reentrancy guards per tick kind
    this.timers = []
    this.bridgeBudget = new Budget(config.restartBudget.max, config.restartBudget.windowMs, now)
    this.dockerBudget = new Budget(config.restartBudget.max, config.restartBudget.windowMs, now)
    this.alerted = new Set() // one alert per stuck condition, cleared on recovery
  }

  async start() {
    mkdirSync(dirname(this.cfg.logFile), { recursive: true })
    if (!acquireLock(this.cfg.lockFile)) throw new Error(`another supervisor holds ${this.cfg.lockFile}`)
    this.#restoreState()
    this.log(`serve: up, chain=[${this.cfg.chain.map((e) => e.name).join(' -> ')}], active=${this.chain.active?.name ?? 'none'}`)
    await this.#dockerTick()
    await this.#exclusive(() => this.#startBridge())
    const every = (ms, kind, fn) => {
      const t = setInterval(() => this.#guarded(kind, fn), ms)
      this.timers.push(t)
    }
    every(this.cfg.intervals.dockerCheckMs, 'docker', () => this.#dockerTick())
    every(this.cfg.intervals.activeCanaryMs, 'canary', () => this.#canaryTick())
    every(this.cfg.intervals.promotionProbeMs, 'promotion', () => this.#promotionTick())
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

  // Every bridge stop/start goes through here. Ticks run under different
  // #guarded keys, so without this two of them could race the bridge into two
  // live children.
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
    if (this.bridge !== null) return // idempotent: concurrent exit processors may both ask
    const entry = this.chain.active
    if (entry === null) {
      await this.#alertOnce('chain', 'every agent in the chain is down or cooling; the bot is offline until one heals')
      return
    }
    this.alerted.delete('chain')
    this.log(`bridge: starting with agent=${entry.name} adapter=${entry.adapter}`)
    // The handle-identity check makes an intentional stop (swap, shutdown)
    // silent: only the CURRENT bridge dying unexpectedly reaches onBridgeExit.
    const handle = startChild(this.cfg.bridge.startCmd, {
      cwd: this.cfg.bridge.dir,
      graceMs: this.cfg.swap.graceMs,
      env: { ...process.env, ...this.#cascadeEnv(), AGENT_ADAPTER: entry.adapter, AGENT_CWD: this.cfg.repoRoot },
      onLine: () => {
        this.lastActivity = this.now()
        this.alerted.delete('bridge')
        this.alerted.delete('bridge-bin')
      },
      onExit: (exit) => {
        if (this.stopping || this.bridge !== handle) return
        this.bridge = null
        // Every exit is processed, never skip-guarded: a dropped exit event is
        // a dead bot until the next reconcile tick. Bridge mutations inside
        // stay safe through #exclusive plus the idempotent #startBridge.
        void this.#onBridgeExit(entry, exit).catch((err) => this.log(`serve: bridge-exit error: ${err?.message ?? err}`))
      },
    })
    this.bridge = handle
  }

  async #onBridgeExit(entry, exit) {
    if (exit.spawnError !== undefined) {
      await this.#alertOnce('bridge-bin', `bridge cannot start (${exit.spawnError.code ?? exit.spawnError.message}); check bridge.dir/startCmd in the config`)
      return
    }
    const verdict = classify(exit.recentOutput, this.patterns)
    this.log(`bridge: exited (code ${exit.code}) class=${verdict.cls} evidence="${verdict.evidence}"`)

    if (verdict.cls === 'UNSUPPORTED') {
      // The bridge itself refused the adapter; no canary needed.
      await this.#handleAgentFailure(entry, verdict)
      return
    }
    // Ambiguous: the exit may be the bridge's own problem (telegram token,
    // network) or the agent's. The canary decides before anyone is demoted.
    const canary = await this.#runCanary(entry)
    if (canary.ok) {
      if (!this.bridgeBudget.hit()) {
        await this.#alertOnce('bridge', 'bridge keeps dying with a healthy agent; restart budget spent, holding')
        return
      }
      this.alerted.delete('bridge')
      await this.#exclusive(() => this.#startBridge())
      return
    }
    await this.#handleAgentFailure(entry, canary.verdict)
  }

  // ---- agent canaries and the chain ---------------------------------------

  async #runCanary(entry) {
    const cmd = entry.canaryCmd.map((a) => a.replace('{prompt}', this.cfg.canaryPrompt))
    const res = await runCommand(cmd, { cwd: this.cfg.repoRoot, timeoutMs: this.cfg.canary.timeoutMs })
    if (res.spawnError !== undefined) {
      return { ok: false, verdict: { cls: 'UNSUPPORTED', evidence: `spawn: ${res.spawnError.code ?? res.spawnError.message}` } }
    }
    if (res.timedOut) return { ok: false, verdict: { cls: 'UNKNOWN', evidence: 'canary timed out' } }
    const output = res.stdout + '\n' + res.stderr
    if (res.code === 0) {
      const expect = this.cfg.canary.expect
      if (expect === undefined || output.includes(expect)) return { ok: true }
      return { ok: false, verdict: { cls: 'UNKNOWN', evidence: `canary exit 0 without "${expect}"` } }
    }
    return { ok: false, verdict: classify(output, this.patterns) }
  }

  async #canaryTick() {
    const entry = this.chain.active
    if (entry === null) {
      await this.#guarded('reconnect', () => this.#reconnectDownChain())
      return
    }
    const res = await this.#runCanary(entry)
    if (res.ok) {
      this.chain.recordSuccess(entry.name)
      return
    }
    this.log(`canary: ${entry.name} failed class=${res.verdict.cls} evidence="${res.verdict.evidence}"`)
    await this.#handleAgentFailure(entry, res.verdict)
  }

  async #handleAgentFailure(entry, verdict) {
    const move = this.chain.recordFailure(entry.name, verdict.cls)
    this.#persistState()
    if (!move.demoted) return
    if (move.to === null) {
      await this.#exclusive(() => this.#stopBridge())
      await this.#alertOnce('chain', `every agent is down (${entry.name} fell last, ${verdict.cls}); the bot is offline until one heals`)
      return
    }
    this.log(`chain: ${move.from} -> ${move.to.name} (${verdict.cls}: ${verdict.evidence})`)
    await this.#swapTo(move.to, `${move.from} is unavailable (${verdict.cls.toLowerCase()})`)
  }

  async #promotionTick() {
    if (this.chain.active === null) {
      await this.#guarded('reconnect', () => this.#reconnectDownChain())
      return
    }
    if (this.bridge === null) {
      await this.#exclusive(() => this.#startBridge())
      return
    }
    for (const candidate of this.chain.promotionCandidates()) {
      const res = await this.#runCanary(candidate)
      if (!res.ok) continue
      // Only swap at a quiet moment, never under a running conversation.
      if (this.now() - this.lastActivity < this.cfg.swap.quietMs) return
      this.chain.promote(candidate.name)
      this.#persistState()
      this.log(`chain: promoted back to ${candidate.name}`)
      await this.#swapTo(candidate, `${candidate.name} is healthy again`)
      return
    }
  }

  // When the whole chain was down, cooldowns expire silently; probe the
  // priority order and revive the first agent that answers.
  async #reconnectDownChain() {
    for (const entry of this.cfg.chain) {
      const res = await this.#runCanary(entry)
      if (!res.ok) continue
      if (!this.chain.revive(entry.name)) continue
      this.#persistState()
      this.log(`chain: revived on ${entry.name}`)
      await this.#swapTo(entry, `${entry.name} is back`)
      return
    }
  }

  async #stopBridge() {
    const old = this.bridge
    this.bridge = null // detach first so onExit sees a stale handle and stays silent
    if (old !== null) await old.stop()
  }

  async #swapTo(entry, reason) {
    await this.#exclusive(async () => {
      await this.#stopBridge()
      await this.#startBridge()
    })
    // Deliberately not awaited: the swap must never wait on Telegram.
    void this.#notifyOwner(`reconnected: ${reason}, now answering through ${entry.name}. A piece that was mid-walk resumes from its ledger; just say continue.`)
  }

  // ---- env cascade ----------------------------------------------------------

  // A cascaded key reads from the bridge checkout's own .env first, then falls
  // back to this repo's .env, so one main .env can hold the bot token for the
  // whole product. Injected as real env, which the bridge gives precedence.
  #envValue(key) {
    const own = parseEnvFile(this.cfg.bridge.envFile)[key]
    if (own !== undefined && own !== '') return own
    const main = parseEnvFile(resolve(this.cfg.repoRoot, '.env'))[key]
    return main === undefined || main === '' ? undefined : main
  }

  #cascadeEnv() {
    const out = {}
    for (const key of this.cfg.bridge.envCascade ?? []) {
      const value = this.#envValue(key)
      if (value !== undefined) out[key] = value
    }
    return out
  }

  // ---- owner notices and state --------------------------------------------

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

  #persistState() {
    try {
      writeFileSync(this.cfg.stateFile, JSON.stringify(this.chain.state(), null, 2))
    } catch (err) {
      this.log(`serve: cannot persist state: ${err.message}`)
    }
  }

  #restoreState() {
    try {
      this.chain.restore(JSON.parse(readFileSync(this.cfg.stateFile, 'utf8')))
    } catch { /* first boot */ }
  }
}
