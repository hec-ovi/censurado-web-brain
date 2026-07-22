import { beforeEach, describe, expect, it } from 'vitest'
import { Chain } from './lib/chain.mjs'

const ENTRIES = [
  { name: 'alpha', adapter: 'alpha-cli' },
  { name: 'bravo', adapter: 'bravo-cli' },
  { name: 'charlie', adapter: 'charlie-cli' },
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
    expect(makeChain().active.name).toBe('alpha')
  })

  it('demotes immediately on AUTH and cools the entry down', () => {
    const chain = makeChain()
    const move = chain.recordFailure('alpha', 'AUTH')
    expect(move).toMatchObject({ demoted: true, from: 'alpha' })
    expect(move.to.name).toBe('bravo')
    expect(chain.promotionCandidates()).toEqual([]) // alpha is cooling
    clock += OPTS.cooldowns.AUTH
    expect(chain.promotionCandidates().map((e) => e.name)).toEqual(['alpha'])
  })

  it('demotes immediately on QUOTA with the longer cooldown', () => {
    const chain = makeChain()
    chain.recordFailure('alpha', 'QUOTA')
    clock += OPTS.cooldowns.AUTH
    expect(chain.promotionCandidates()).toEqual([]) // still cooling
    clock += OPTS.cooldowns.QUOTA - OPTS.cooldowns.AUTH
    expect(chain.promotionCandidates().map((e) => e.name)).toEqual(['alpha'])
  })

  it('needs the threshold of soft failures inside the window to demote', () => {
    const chain = makeChain()
    expect(chain.recordFailure('alpha', 'TRANSIENT').demoted).toBe(false)
    expect(chain.recordFailure('alpha', 'UNKNOWN').demoted).toBe(false)
    const third = chain.recordFailure('alpha', 'TRANSIENT')
    expect(third.demoted).toBe(true)
    expect(third.to.name).toBe('bravo')
  })

  it('lets old soft failures slide out of the window', () => {
    const chain = makeChain()
    chain.recordFailure('alpha', 'TRANSIENT')
    chain.recordFailure('alpha', 'TRANSIENT')
    clock += OPTS.thresholds.failureWindowMs + 1
    expect(chain.recordFailure('alpha', 'TRANSIENT').demoted).toBe(false)
  })

  it('clears the soft counter on success', () => {
    const chain = makeChain()
    chain.recordFailure('alpha', 'TRANSIENT')
    chain.recordFailure('alpha', 'TRANSIENT')
    chain.recordSuccess('alpha')
    expect(chain.recordFailure('alpha', 'TRANSIENT').demoted).toBe(false)
  })

  it('ignores failures reported for a non-active entry', () => {
    const chain = makeChain()
    expect(chain.recordFailure('charlie', 'AUTH').demoted).toBe(false)
    expect(chain.active.name).toBe('alpha')
  })

  it('parks UNSUPPORTED entries for the life of the process', () => {
    const chain = makeChain()
    chain.recordFailure('alpha', 'UNSUPPORTED')
    expect(chain.active.name).toBe('bravo')
    clock += 10 * OPTS.cooldowns.QUOTA
    expect(chain.promotionCandidates()).toEqual([]) // never comes back
    expect(chain.revive('alpha')).toBe(false)
  })

  it('walks the whole chain down and reports null when everything is out', () => {
    const chain = makeChain()
    chain.recordFailure('alpha', 'AUTH')
    chain.recordFailure('bravo', 'QUOTA')
    chain.recordFailure('charlie', 'AUTH')
    const last = chain.recordFailure('local', 'AUTH')
    expect(last.demoted).toBe(true)
    expect(last.to).toBeNull()
    expect(chain.active).toBeNull()
  })

  it('promotes only upward and only past the cooldown', () => {
    const chain = makeChain()
    chain.recordFailure('alpha', 'AUTH')
    expect(chain.promote('alpha')).toBe(false) // still cooling
    clock += OPTS.cooldowns.AUTH
    expect(chain.promote('charlie')).toBe(false) // below active, not a promotion
    expect(chain.promote('alpha')).toBe(true)
    expect(chain.active.name).toBe('alpha')
  })

  it('revive clears a cooldown and reactivates the entry', () => {
    const chain = makeChain()
    chain.recordFailure('alpha', 'QUOTA')
    expect(chain.revive('alpha')).toBe(true)
    expect(chain.active.name).toBe('alpha')
  })

  it('round-trips its state, matching entries by name', () => {
    const chain = makeChain()
    chain.recordFailure('alpha', 'AUTH')
    const saved = chain.state()
    const restored = makeChain()
    restored.restore(saved)
    expect(restored.active.name).toBe('bravo')
    expect(restored.promotionCandidates()).toEqual([]) // alpha cooldown survived
  })

  it('does not resurrect unsupported entries across a restore (restart retries them)', () => {
    const chain = makeChain()
    chain.recordFailure('alpha', 'UNSUPPORTED')
    const restored = makeChain()
    restored.restore(chain.state())
    expect(restored.active.name).toBe('bravo') // saved active honored
    clock += OPTS.cooldowns.AUTH
    expect(restored.promotionCandidates().map((e) => e.name)).toEqual(['alpha'])
  })

  it('survives a restore that names entries no longer in the config', () => {
    const chain = makeChain()
    chain.restore({ active: 'gone', cooldownUntil: { alsoGone: 99 }, unsupported: ['gone'] })
    expect(chain.active.name).toBe('alpha')
  })
})
