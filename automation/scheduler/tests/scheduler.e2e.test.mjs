// The scheduler end to end through the real entry point (run.mjs, a child
// node process) against fake agent binaries, exactly as an operator runs it.
// Every record read back is re-validated against the layer's run-record schema.

import { afterEach, describe, expect, it } from 'vitest'
import { spawn } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { validate } from '../src/validate.mjs'

const LAYER = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const RUN = join(LAYER, 'run.mjs')
const RECORD_SCHEMA = JSON.parse(readFileSync(join(LAYER, 'schema', 'run-record.schema.json'), 'utf8'))

let dir
afterEach(() => {
  if (dir !== undefined) rmSync(dir, { recursive: true, force: true })
  dir = undefined
})

function sandbox(agentScript, schedules, agentOverrides = {}) {
  dir = mkdtempSync(join(tmpdir(), 'sched-e2e-'))
  writeFileSync(join(dir, 'agent.sh'), `#!/usr/bin/env bash\n${agentScript}`, { mode: 0o755 })
  writeFileSync(join(dir, 'cfg.json'), JSON.stringify({
    logDir: 'logs',
    agent: { cmd: ['bash', join(dir, 'agent.sh'), '{prompt}'], timeoutMs: 1000, ...agentOverrides },
    schedules,
  }))
  return dir
}

function exec(args, { timeoutMs = 10_000 } = {}) {
  return new Promise((done) => {
    const child = spawn(process.execPath, [RUN, ...args])
    let out = ''
    let err = ''
    child.stdout.on('data', (d) => { out += d })
    child.stderr.on('data', (d) => { err += d })
    const killer = setTimeout(() => child.kill('SIGTERM'), timeoutMs)
    child.on('exit', (code) => { clearTimeout(killer); done({ code, out, err }) })
  })
}

function records() {
  const path = join(dir, 'logs', 'runs.jsonl')
  if (!existsSync(path)) return []
  return readFileSync(path, 'utf8').trim().split('\n').map((line) => {
    const record = JSON.parse(line)
    expect(validate(record, RECORD_SCHEMA)).toEqual([])
    return record
  })
}

describe('scheduler e2e', { timeout: 20_000 }, () => {
  it('--once runs the agent with the substituted prompt and records ok', async () => {
    sandbox('echo "got: $1"; exit 0',
      [{ name: 'sweep', at: '07:00', prompt: 'daily sweep of latest news' }])
    const r = await exec([join(dir, 'cfg.json'), '--once', 'sweep'])
    expect(r.code).toBe(0)
    const [rec] = records()
    expect(rec.status).toBe('ok')
    expect(rec.schedule).toBe('sweep')
    expect(readFileSync(rec.logFile, 'utf8')).toContain('got: daily sweep of latest news')
  })

  it('--once propagates an agent failure as exit 1 and records the code', async () => {
    sandbox('exit 7', [{ name: 'sweep', at: '07:00', prompt: 'p' }])
    const r = await exec([join(dir, 'cfg.json'), '--once', 'sweep'])
    expect(r.code).toBe(1)
    expect(records()[0]).toMatchObject({ status: 'failed', exitCode: 7 })
  })

  it('kills a hung agent at timeoutMs and records timeout', async () => {
    sandbox('sleep 30', [{ name: 'sweep', at: '07:00', prompt: 'p' }])
    const r = await exec([join(dir, 'cfg.json'), '--once', 'sweep'])
    expect(r.code).toBe(1)
    expect(records()[0].status).toBe('timeout')
  })

  it('an unspawnable agent records failed with exit 127', async () => {
    sandbox('exit 0', [{ name: 'sweep', at: '07:00', prompt: 'p' }])
    const cfg = JSON.parse(readFileSync(join(dir, 'cfg.json'), 'utf8'))
    cfg.agent.cmd = [join(dir, 'no-such-binary')]
    writeFileSync(join(dir, 'cfg.json'), JSON.stringify(cfg))
    const r = await exec([join(dir, 'cfg.json'), '--once', 'sweep'])
    expect(r.code).toBe(1)
    expect(records()[0]).toMatchObject({ status: 'failed', exitCode: 127 })
  })

  it('the loop fires an every-interval schedule on its own', async () => {
    sandbox('echo ran; exit 0', [{ name: 'tick', every: '1s', prompt: 'p' }])
    const r = await exec([join(dir, 'cfg.json')], { timeoutMs: 3_500 })
    expect(r.code).toBe(0) // clean SIGTERM shutdown
    expect(records().filter((rec) => rec.status === 'ok').length).toBeGreaterThanOrEqual(1)
    expect(existsSync(join(dir, 'logs', '.scheduler.lock'))).toBe(false)
  })

  it('overlapping fires are skipped, not double-run', async () => {
    // The agent outlives two 1s intervals, so at least one firing must land as
    // skipped-overlap while exactly one run is in flight.
    sandbox('sleep 30', [{ name: 'tick', every: '1s', prompt: 'p' }],
      { timeoutMs: 60_000 })
    await exec([join(dir, 'cfg.json')], { timeoutMs: 4_000 })
    const skipped = records().filter((rec) => rec.status === 'skipped-overlap')
    expect(skipped.length).toBeGreaterThanOrEqual(1)
    expect(skipped[0].exitCode).toBeUndefined()
  })

  it('a second scheduler over the same logDir is refused with exit 3', async () => {
    sandbox('exit 0', [{ name: 'tick', every: '1h', prompt: 'p' }])
    const first = exec([join(dir, 'cfg.json')], { timeoutMs: 4_000 })
    await new Promise((wait) => setTimeout(wait, 500))
    const second = await exec([join(dir, 'cfg.json')])
    expect(second.code).toBe(3)
    expect(second.err).toContain('holds')
    await first
  })

  it('a stale lock from a dead pid is replaced, not fatal', async () => {
    sandbox('exit 0', [{ name: 'sweep', at: '07:00', prompt: 'p' }])
    const { mkdirSync } = await import('node:fs')
    mkdirSync(join(dir, 'logs'), { recursive: true })
    writeFileSync(join(dir, 'logs', '.scheduler.lock'), '999999')
    const r = await exec([join(dir, 'cfg.json'), '--once', 'sweep'])
    expect(r.code).toBe(0)
  })

  it('--check prints one next-fire line per schedule and runs nothing', async () => {
    sandbox('exit 0', [
      { name: 'sweep', at: '07:00', prompt: 'p' },
      { name: 'tick', every: '2h', prompt: 'p' },
    ])
    const r = await exec([join(dir, 'cfg.json'), '--check'])
    expect(r.code).toBe(0)
    expect(r.out).toMatch(/sweep: next \d{4}-/)
    expect(r.out).toMatch(/tick: next \d{4}-/)
    expect(records()).toEqual([])
  })

  it('an invalid config exits 2 with the violations, an unknown --once name exits 4', async () => {
    sandbox('exit 0', [{ name: 'sweep', prompt: 'p' }]) // neither at nor every
    const r = await exec([join(dir, 'cfg.json'), '--check'])
    expect(r.code).toBe(2)
    expect(r.err).toContain('exactly one of')
    sandbox('exit 0', [{ name: 'sweep', at: '07:00', prompt: 'p' }])
    const missing = await exec([join(dir, 'cfg.json'), '--once', 'nope'])
    expect(missing.code).toBe(4)
  })
})
