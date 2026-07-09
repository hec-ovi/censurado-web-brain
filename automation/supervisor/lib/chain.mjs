// The agent fallback chain: pure state machine, no I/O. The supervisor feeds
// it classified failures and asks who is active; it decides demotions,
// cooldowns, and when a higher-priority agent may take back over.
//
// Rules (automation/supervisor/REQUIREMENTS.md R2/R3):
// - AUTH and QUOTA demote immediately; they never heal within minutes. Each
//   gets its own cooldown before the entry is probed for promotion again.
// - TRANSIENT and UNKNOWN count in a sliding window; hitting the threshold
//   demotes with the SOFT cooldown.
// - UNSUPPORTED (missing binary, unknown bridge adapter) parks the entry for
//   the life of the process; only a restart or config change brings it back.
// - Order in the config array is priority order. Demotion moves down to the
//   first usable entry; promotion is offered only upward.

export class Chain {
  constructor(entries, opts, now = () => Date.now()) {
    if (!Array.isArray(entries) || entries.length === 0) throw new Error('chain needs at least one entry')
    this.entries = entries
    this.opts = {
      failureCount: opts?.thresholds?.failureCount ?? 3,
      failureWindowMs: opts?.thresholds?.failureWindowMs ?? 600_000,
      cooldowns: {
        AUTH: opts?.cooldowns?.AUTH ?? 1_800_000,
        QUOTA: opts?.cooldowns?.QUOTA ?? 3_600_000,
        SOFT: opts?.cooldowns?.SOFT ?? 600_000,
      },
    }
    this.now = now
    this.activeIndex = 0
    this.cooldownUntil = new Map() // name -> epoch ms
    this.unsupported = new Set() // names parked until restart
    this.softFailures = new Map() // name -> [epoch ms]
  }

  get active() {
    const e = this.entries[this.activeIndex]
    return e !== undefined && this.#usable(e.name) ? e : null
  }

  #usable(name) {
    if (this.unsupported.has(name)) return false
    const until = this.cooldownUntil.get(name)
    return until === undefined || this.now() >= until
  }

  #firstUsable() {
    // Prefer entries below the current one (the fallback direction), then wrap
    // to anything usable at all so an expired cooldown above can serve.
    const n = this.entries.length
    for (let step = 1; step <= n; step++) {
      const i = (this.activeIndex + step) % n
      if (this.#usable(this.entries[i].name)) return i
    }
    return -1
  }

  recordSuccess(name) {
    this.softFailures.delete(name)
  }

  // Returns { demoted, from, to } where to is the new active entry or null
  // when the whole chain is down.
  recordFailure(name, cls) {
    const active = this.entries[this.activeIndex]
    if (active === undefined || active.name !== name) return { demoted: false, from: name, to: this.active }

    if (cls === 'UNSUPPORTED') {
      this.unsupported.add(name)
    } else if (cls === 'AUTH' || cls === 'QUOTA') {
      this.cooldownUntil.set(name, this.now() + this.opts.cooldowns[cls])
    } else {
      // TRANSIENT / UNKNOWN: sliding-window count, demote only at threshold.
      const cutoff = this.now() - this.opts.failureWindowMs
      const hits = (this.softFailures.get(name) ?? []).filter((t) => t >= cutoff)
      hits.push(this.now())
      this.softFailures.set(name, hits)
      if (hits.length < this.opts.failureCount) return { demoted: false, from: name, to: active }
      this.cooldownUntil.set(name, this.now() + this.opts.cooldowns.SOFT)
      this.softFailures.delete(name)
    }

    const next = this.#firstUsable()
    this.activeIndex = next === -1 ? this.activeIndex : next
    return { demoted: true, from: name, to: next === -1 ? null : this.entries[next] }
  }

  // Entries strictly above the active one whose cooldown has expired: the
  // supervisor canary-probes these and calls promote() on a pass.
  promotionCandidates() {
    const out = []
    for (let i = 0; i < this.activeIndex; i++) {
      const e = this.entries[i]
      if (this.#usable(e.name)) out.push(e)
    }
    return out
  }

  // Force an entry back into service; used when the whole chain was down and
  // a probe found this one alive again. Unsupported entries stay parked.
  revive(name) {
    const i = this.entries.findIndex((e) => e.name === name)
    if (i === -1 || this.unsupported.has(name)) return false
    this.cooldownUntil.delete(name)
    this.activeIndex = i
    return true
  }

  promote(name) {
    const i = this.entries.findIndex((e) => e.name === name)
    if (i === -1 || i >= this.activeIndex || !this.#usable(name)) return false
    this.activeIndex = i
    this.softFailures.delete(name)
    return true
  }

  // Persistence across supervisor restarts; entries are matched by name so a
  // config edit (reorder, add, remove) never crashes a restore.
  state() {
    return {
      active: this.entries[this.activeIndex]?.name ?? null,
      cooldownUntil: Object.fromEntries(this.cooldownUntil),
      unsupported: [...this.unsupported],
    }
  }

  restore(saved) {
    if (saved === null || typeof saved !== 'object') return
    for (const [name, until] of Object.entries(saved.cooldownUntil ?? {})) {
      if (this.entries.some((e) => e.name === name) && typeof until === 'number') this.cooldownUntil.set(name, until)
    }
    // Deliberately NOT restoring `unsupported`: a supervisor restart is the
    // documented way to re-try an entry after installing its binary/adapter.
    const i = this.entries.findIndex((e) => e.name === saved.active)
    if (i !== -1 && this.#usable(saved.active)) this.activeIndex = i
    else if (this.active === null) {
      const first = this.#firstUsable()
      if (first !== -1) this.activeIndex = first
    }
  }
}
