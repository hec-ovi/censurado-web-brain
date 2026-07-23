// The fail-closed validator and the config loader, driven through the layer's
// real schema files (the contract, not a copy).

import { describe, expect, it } from 'vitest'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { validate } from '../src/validate.mjs'
import { configViolations, loadConfig, nextAt, parseEvery } from '../src/scheduler.mjs'

const LAYER = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const CONFIG_SCHEMA = JSON.parse(readFileSync(join(LAYER, 'schema', 'schedule-config.schema.json'), 'utf8'))

const good = () => ({
  agent: { cmd: ['agent', '-p', '{prompt}'] },
  schedules: [{ name: 'daily-sweep', at: '07:00', prompt: 'sweep the news' }],
})

describe('validator against the config schema', () => {
  it('accepts the shipped example fixture', () => {
    const example = JSON.parse(readFileSync(join(LAYER, 'fixtures', 'schedules.example.json'), 'utf8'))
    expect(configViolations(example)).toEqual([])
  })

  it('accepts a minimal conforming config', () => {
    expect(configViolations(good())).toEqual([])
  })

  it('rejects unknown properties (fail closed)', () => {
    const bad = { ...good(), surprise: true }
    expect(validate(bad, CONFIG_SCHEMA).join()).toContain('unknown property surprise')
  })

  it('rejects a malformed time and a malformed interval', () => {
    const c = good()
    c.schedules[0].at = '7:00'
    expect(validate(c, CONFIG_SCHEMA).join()).toContain('does not match')
    const d = good()
    delete d.schedules[0].at
    d.schedules[0].every = '10x'
    expect(validate(d, CONFIG_SCHEMA).join()).toContain('does not match')
  })

  it('enforces the cross-field rules the schema cannot express', () => {
    const both = good()
    both.schedules[0].every = '5m'
    expect(configViolations(both).join()).toContain('exactly one of')
    const neither = good()
    delete neither.schedules[0].at
    expect(configViolations(neither).join()).toContain('exactly one of')
    const dup = good()
    dup.schedules.push({ ...good().schedules[0], every: undefined, at: '08:00' })
    expect(configViolations(dup).join()).toContain('duplicate name')
  })

  it('refuses schemas that use keywords it does not implement', () => {
    // Silent under-validation is the failure mode this guards: a new keyword in
    // a schema must break loudly until the validator learns it.
    expect(() => validate({}, { maxProperties: 3 })).toThrow('unsupported schema keyword')
  })
})

describe('time math', () => {
  it('nextAt picks today before the time and tomorrow at or after it', () => {
    const now = new Date(2026, 6, 23, 8, 0, 0)
    expect(nextAt('09:30', now).getDate()).toBe(23)
    expect(nextAt('09:30', now).getHours()).toBe(9)
    expect(nextAt('07:00', now).getDate()).toBe(24)
    expect(nextAt('08:00', now).getDate()).toBe(24) // exactly now -> strictly after
  })

  it('parseEvery reads s/m/h and rejects everything else', () => {
    expect(parseEvery('90s')).toBe(90_000)
    expect(parseEvery('15m')).toBe(900_000)
    expect(parseEvery('2h')).toBe(7_200_000)
    expect(() => parseEvery('0s')).toThrow('invalid every')
    expect(() => parseEvery('5d')).toThrow('invalid every')
  })
})

describe('loadConfig', () => {
  it('resolves relative paths against the config file directory and applies defaults', () => {
    const dir = mkdtempSync(join(tmpdir(), 'sched-'))
    try {
      const cfg = { ...good(), logDir: 'my-logs' }
      writeFileSync(join(dir, 'cfg.json'), JSON.stringify(cfg))
      const loaded = loadConfig(join(dir, 'cfg.json'))
      expect(loaded.logDir).toBe(join(dir, 'my-logs'))
      expect(loaded.schedules[0].agent.cwd).toBe(dir)
      expect(loaded.schedules[0].agent.timeoutMs).toBe(3_600_000)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })

  it('throws CONFIG_INVALID listing every violation', () => {
    const dir = mkdtempSync(join(tmpdir(), 'sched-'))
    try {
      writeFileSync(join(dir, 'cfg.json'), JSON.stringify({ schedules: [] }))
      expect(() => loadConfig(join(dir, 'cfg.json'))).toThrow(/missing required agent[\s\S]*minItems/)
    } finally {
      rmSync(dir, { recursive: true, force: true })
    }
  })
})
