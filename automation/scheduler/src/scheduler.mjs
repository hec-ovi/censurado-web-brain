// The scheduler: at configured times, run the configured agent command with a
// configured prompt, and append one schema-validated run record per attempt.
// It knows nothing about the newsroom, the supervisor, or any agent brand: the
// whole agent contract is argv (with {prompt} substituted) + exit code.

import { spawn } from 'node:child_process'
import {
  appendFileSync, closeSync, mkdirSync, openSync, readFileSync, rmSync, writeFileSync,
} from 'node:fs'
import { dirname, isAbsolute, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { validate } from './validate.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const CONFIG_SCHEMA = JSON.parse(readFileSync(join(HERE, '..', 'schema', 'schedule-config.schema.json'), 'utf8'))
const RECORD_SCHEMA = JSON.parse(readFileSync(join(HERE, '..', 'schema', 'run-record.schema.json'), 'utf8'))
const CONTRACT_VERSION = '1.0.0'

export function parseEvery(text) {
  const m = /^([1-9][0-9]*)(s|m|h)$/.exec(text)
  if (m === null) throw new Error(`invalid every: ${text}`)
  return Number(m[1]) * { s: 1_000, m: 60_000, h: 3_600_000 }[m[2]]
}

// Next occurrence of a local HH:MM strictly after `now`.
export function nextAt(hhmm, now) {
  const [h, min] = hhmm.split(':').map(Number)
  const next = new Date(now)
  next.setHours(h, min, 0, 0)
  if (next.getTime() <= now.getTime()) next.setDate(next.getDate() + 1)
  return next
}

// Validates the raw config plus the cross-field rules the schema subset cannot
// express (exactly one of at/every, unique names). Returns the violation list;
// the caller refuses to start on any entry (fail closed).
export function configViolations(raw) {
  const out = validate(raw, CONFIG_SCHEMA)
  if (out.length > 0) return out
  const seen = new Set()
  raw.schedules.forEach((entry, i) => {
    if ((entry.at === undefined) === (entry.every === undefined)) {
      out.push(`$.schedules[${i}]: exactly one of "at" or "every" is required`)
    }
    if (seen.has(entry.name)) out.push(`$.schedules[${i}]: duplicate name ${entry.name}`)
    seen.add(entry.name)
  })
  return out
}

// Loads and resolves a config file. Relative paths (logDir, agent.cwd) resolve
// against the config file's own directory, so a config travels with its layout.
export function loadConfig(path) {
  const raw = JSON.parse(readFileSync(path, 'utf8'))
  const violations = configViolations(raw)
  if (violations.length > 0) {
    const err = new Error(`invalid config ${path}:\n  ${violations.join('\n  ')}`)
    err.code = 'CONFIG_INVALID'
    throw err
  }
  const base = dirname(resolve(path))
  const abs = (p) => (isAbsolute(p) ? p : resolve(base, p))
  return {
    logDir: abs(raw.logDir ?? 'logs'),
    schedules: raw.schedules.map((entry) => ({
      name: entry.name,
      at: entry.at,
      every: entry.every,
      prompt: entry.prompt,
      agent: {
        cmd: (entry.agent ?? raw.agent).cmd,
        cwd: abs((entry.agent ?? raw.agent).cwd ?? raw.agent.cwd ?? base),
        timeoutMs: (entry.agent ?? raw.agent).timeoutMs ?? raw.agent.timeoutMs ?? 3_600_000,
      },
    })),
  }
}

export class Scheduler {
  #timers = new Map()
  #child = null
  #stopped = false

  constructor(config, { log = () => {}, now = () => new Date() } = {}) {
    this.cfg = config
    this.log = log
    this.now = now
    this.lockFile = join(config.logDir, '.scheduler.lock')
    this.runsFile = join(config.logDir, 'runs.jsonl')
  }

  // Single instance per logDir: a second scheduler over the same runs file
  // would double-fire every schedule. A lock whose pid is dead is stale and
  // gets replaced (a crashed scheduler must not need manual cleanup).
  #acquireLock() {
    mkdirSync(this.cfg.logDir, { recursive: true })
    try {
      writeFileSync(this.lockFile, String(process.pid), { flag: 'wx' })
      return
    } catch {
      const pid = Number(readFileSync(this.lockFile, 'utf8').trim())
      try {
        process.kill(pid, 0)
        const err = new Error(`another scheduler (pid ${pid}) holds ${this.lockFile}`)
        err.code = 'LOCK_HELD'
        throw err
      } catch (probe) {
        if (probe.code === 'LOCK_HELD') throw probe
        writeFileSync(this.lockFile, String(process.pid))
      }
    }
  }

  nextFire(entry) {
    return entry.at !== undefined
      ? nextAt(entry.at, this.now())
      : new Date(this.now().getTime() + parseEvery(entry.every))
  }

  // The same lock guards the loop and a manual `--once` run: both write the
  // same runs file and both count as "the one walk".
  lock() { this.#acquireLock() }

  unlock() { rmSync(this.lockFile, { force: true }) }

  start() {
    this.#acquireLock()
    for (const entry of this.cfg.schedules) this.#arm(entry)
    this.log(`scheduler: ${this.cfg.schedules.length} schedule(s) armed`)
  }

  stop() {
    this.#stopped = true
    for (const timer of this.#timers.values()) clearTimeout(timer)
    this.#timers.clear()
    if (this.#child !== null) {
      try { process.kill(-this.#child.pid, 'SIGTERM') } catch { /* already gone */ }
    }
    rmSync(this.lockFile, { force: true })
  }

  #arm(entry) {
    if (this.#stopped) return
    const dueIn = this.nextFire(entry).getTime() - this.now().getTime()
    this.#timers.set(entry.name, setTimeout(() => {
      // Re-arm BEFORE running: the cadence is fixed-rate, so a long run makes
      // the next occurrence a recorded overlap skip, never a delayed clock.
      this.#arm(entry)
      void this.#fire(entry)
    }, Math.max(dueIn, 0)))
  }

  async #fire(entry) {
    if (this.#child !== null) {
      // One walk at a time: a run still in flight wins, the newcomer records a
      // skip and waits for its next occurrence instead of double-writing.
      const at = this.now().toISOString()
      this.#record({ schedule: entry.name, startedAt: at, finishedAt: at, status: 'skipped-overlap' })
      this.log(`scheduler: ${entry.name} skipped, a run is in flight`)
      return
    }
    await this.runNow(entry)
  }

  // Runs one entry to completion and returns its record. Public so `--once`
  // can drive a single schedule without arming timers.
  async runNow(entry) {
    mkdirSync(this.cfg.logDir, { recursive: true })
    const startedAt = this.now().toISOString()
    const logFile = join(this.cfg.logDir, `${entry.name}-${startedAt.replace(/[:.]/g, '')}.log`)
    const [bin, ...args] = entry.agent.cmd.map((part) => part.replaceAll('{prompt}', entry.prompt))
    const out = openSync(logFile, 'a')
    this.log(`scheduler: ${entry.name} -> ${bin}`)

    const record = await new Promise((done) => {
      const child = spawn(bin, args, {
        cwd: entry.agent.cwd, detached: true, stdio: ['ignore', out, out],
      })
      this.#child = child
      let timedOut = false
      const killer = setTimeout(() => {
        timedOut = true
        try { process.kill(-child.pid, 'SIGKILL') } catch { /* already gone */ }
      }, entry.agent.timeoutMs)
      child.on('error', (err) => {
        clearTimeout(killer)
        appendFileSync(logFile, `spawn failed: ${err.message}\n`)
        done({ status: 'failed', exitCode: 127 })
      })
      child.on('exit', (code) => {
        clearTimeout(killer)
        done(timedOut
          ? { status: 'timeout', exitCode: code ?? -1 }
          : { status: code === 0 ? 'ok' : 'failed', exitCode: code ?? -1 })
      })
    }).finally(() => {
      this.#child = null
      closeSync(out)
    })

    const full = {
      schedule: entry.name, startedAt, finishedAt: this.now().toISOString(), logFile, ...record,
    }
    this.#record(full)
    this.log(`scheduler: ${entry.name} ${full.status} (exit ${full.exitCode})`)
    return full
  }

  #record(fields) {
    const record = { contractVersion: CONTRACT_VERSION, ...fields }
    const violations = validate(record, RECORD_SCHEMA)
    if (violations.length > 0) {
      throw new Error(`run record violates its own schema: ${violations.join('; ')}`)
    }
    appendFileSync(this.runsFile, JSON.stringify(record) + '\n')
  }
}
