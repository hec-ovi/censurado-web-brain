import { beforeEach, describe, expect, it } from 'vitest'
import { Chain } from './lib/chain.mjs'

const ENTRIES = [
  { name: 'agy', adapter: 'agy' },
  { name: 'codex', adapter: 'codex' },
  { name: 'claude', adapter: 'claude-code' },
  { name: 'local', adapter: 'pi' },
]

const OPTS = {
  thresholds: { failureCount: 3, failureWindowMs: 10_000 },
  cooldowns: { AUTH: 60_000, QUOTA: 120_000, SOFT: 30_000 },
}

let clock
const now = () => clock
const makeChain = () => new Chain(ENTRIES, OPTS, now)

beforeEach(() => { clock = 1_000_000 })

describe('chain', () => {
  it('starts on the first entry', () => {
    expect(makeChain().active.name).toBe('agy')
  })

  it('demotes immediately on AUTH and cools the entry down', () => {
    const chain = makeChain()
    const move = chain.recordFailure('agy', 'AUTH')
    expect(move).toMatchObject({ demoted: true, from: 'agy' })
    expect(move.to.name).toBe('codex')
    expect(chain.promotionCandidates()).toEqual([]) // agy is cooling
    clock += OPTS.cooldowns.AUTH
    expect(chain.promotionCandidates().map((e) => e.name)).toEqual(['agy'])
  })

  it('demotes immediately on QUOTA with the longer cooldown', () => {
    const chain = makeChain()
    chain.recordFailure('agy', 'QUOTA')
    clock += OPTS.cooldowns.AUTH
    expect(chain.promotionCandidates()).toEqual([]) // still cooling
    clock += OPTS.cooldowns.QUOTA - OPTS.cooldowns.AUTH
    expect(chain.promotionCandidates().map((e) => e.name)).toEqual(['agy'])
  })

  it('needs the threshold of soft failures inside the window to demote', () => {
    const chain = makeChain()
    expect(chain.recordFailure('agy', 'TRANSIENT').demoted).toBe(false)
    expect(chain.recordFailure('agy', 'UNKNOWN').demoted).toBe(false)
    const third = chain.recordFailure('agy', 'TRANSIENT')
    expect(third.demoted).toBe(true)
    expect(third.to.name).toBe('codex')
  })

  it('lets old soft failures slide out of the window', () => {
    const chain = makeChain()
    chain.recordFailure('agy', 'TRANSIENT')
    chain.recordFailure('agy', 'TRANSIENT')
    clock += OPTS.thresholds.failureWindowMs + 1
    expect(chain.recordFailure('agy', 'TRANSIENT').demoted).toBe(false)
  })

  it('clears the soft counter on success', () => {
    const chain = makeChain()
    chain.recordFailure('agy', 'TRANSIENT')
    chain.recordFailure('agy', 'TRANSIENT')
    chain.recordSuccess('agy')
    expect(chain.recordFailure('agy', 'TRANSIENT').demoted).toBe(false)
  })

  it('ignores failures reported for a non-active entry', () => {
    const chain = makeChain()
    expect(chain.recordFailure('claude', 'AUTH').demoted).toBe(false)
    expect(chain.active.name).toBe('agy')
  })

  it('parks UNSUPPORTED entries for the life of the process', () => {
    const chain = makeChain()
    chain.recordFailure('agy', 'UNSUPPORTED')
    expect(chain.active.name).toBe('codex')
    clock += 10 * OPTS.cooldowns.QUOTA
    expect(chain.promotionCandidates()).toEqual([]) // never comes back
    expect(chain.revive('agy')).toBe(false)
  })

  it('walks the whole chain down and reports null when everything is out', () => {
    const chain = makeChain()
    chain.recordFailure('agy', 'AUTH')
    chain.recordFailure('codex', 'QUOTA')
    chain.recordFailure('claude', 'AUTH')
    const last = chain.recordFailure('local', 'AUTH')
    expect(last.demoted).toBe(true)
    expect(last.to).toBeNull()
    expect(chain.active).toBeNull()
  })

  it('promotes only upward and only past the cooldown', () => {
    const chain = makeChain()
    chain.recordFailure('agy', 'AUTH')
    expect(chain.promote('agy')).toBe(false) // still cooling
    clock += OPTS.cooldowns.AUTH
    expect(chain.promote('claude')).toBe(false) // below active, not a promotion
    expect(chain.promote('agy')).toBe(true)
    expect(chain.active.name).toBe('agy')
  })

  it('revive clears a cooldown and reactivates the entry', () => {
    const chain = makeChain()
    chain.recordFailure('agy', 'QUOTA')
    expect(chain.revive('agy')).toBe(true)
    expect(chain.active.name).toBe('agy')
  })

  it('round-trips its state, matching entries by name', () => {
    const chain = makeChain()
    chain.recordFailure('agy', 'AUTH')
    const saved = chain.state()
    const restored = makeChain()
    restored.restore(saved)
    expect(restored.active.name).toBe('codex')
    expect(restored.promotionCandidates()).toEqual([]) // agy cooldown survived
  })

  it('does not resurrect unsupported entries across a restore (restart retries them)', () => {
    const chain = makeChain()
    chain.recordFailure('agy', 'UNSUPPORTED')
    const restored = makeChain()
    restored.restore(chain.state())
    expect(restored.active.name).toBe('codex') // saved active honored
    clock += OPTS.cooldowns.AUTH
    expect(restored.promotionCandidates().map((e) => e.name)).toEqual(['agy'])
  })

  it('survives a restore that names entries no longer in the config', () => {
    const chain = makeChain()
    chain.restore({ active: 'gone', cooldownUntil: { alsoGone: 99 }, unsupported: ['gone'] })
    expect(chain.active.name).toBe('agy')
  })
})
