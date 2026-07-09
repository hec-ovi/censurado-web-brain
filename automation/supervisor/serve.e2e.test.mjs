// End-to-end tests of the serve loop against fake binaries: a scripted bridge
// and scripted agent canaries, the same approach telegram-bot-skill uses for
// its adapters. No docker, no network, no real agents.

import { afterEach, describe, expect, it } from 'vitest'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { resolveConfig, Supervisor } from './lib/supervisor.mjs'

const realPatterns = JSON.parse(
  readFileSync(new URL('./supervisor.config.json', import.meta.url), 'utf8'),
).patterns

async function until(fn, ms = 8_000) {
  const t0 = Date.now()
  while (Date.now() - t0 < ms) {
    if (fn()) return
    await new Promise((r) => setTimeout(r, 25))
  }
  throw new Error(`condition not met within ${ms}ms`)
}

function sandbox() {
  const dir = mkdtempSync(join(tmpdir(), 'serve-test-'))

  // The fake bridge: logs which adapter it was started with, honors two
  // scripted behaviors (an adapter the bridge refuses, a die-once marker),
  // then stays alive until SIGTERM.
  writeFileSync(join(dir, 'bridge.sh'), `#!/usr/bin/env bash
echo "$AGENT_ADAPTER" >> "${dir}/adapters.log"
case "$AGENT_ADAPTER" in refused*)
  echo "Unknown AGENT_ADAPTER \\"$AGENT_ADAPTER\\". Use claude-code or pi." >&2
  exit 1
esac
if [ -f "${dir}/bridge-die-once" ]; then
  rm "${dir}/bridge-die-once"
  echo "socket hang up" >&2
  exit 1
fi
echo "@bot is polling"
trap 'exit 0' TERM
sleep 300 & wait $!
`, { mode: 0o755 })

  // One fake canary per agent, mode-switched through a control file:
  // ok | auth | quota | flaky.
  for (const name of ['alpha', 'beta']) {
    writeFileSync(join(dir, `canary-${name}.sh`), `#!/usr/bin/env bash
mode=$(cat "${dir}/canary-${name}.mode" 2>/dev/null || echo ok)
case "$mode" in
  ok)    echo OK ;;
  auth)  echo "Error: please login again" >&2; exit 1 ;;
  quota) echo "429 rate limit reached" >&2; exit 1 ;;
  flaky) echo "fetch failed: ECONNRESET" >&2; exit 1 ;;
esac
`, { mode: 0o755 })
  }

  // The bridge checkout the supervisor reads notify credentials from.
  writeFileSync(join(dir, '.env'), 'TELEGRAM_BOT_TOKEN=tok123\n')
  writeFileSync(join(dir, 'bot-state.json'), JSON.stringify({
    users: { 7: { state: 'owner', chatId: 7, addedAt: 'x' } }, sessions: {},
  }))

  const config = resolveConfig({
    repoRoot: '.',
    bridge: { dir: '.', startCmd: ['bash', 'bridge.sh'] },
    docker: { checkCmd: ['true'], checkTimeoutMs: 5_000, upCmd: ['true'], upTimeoutMs: 5_000 },
    intervals: { dockerCheckMs: 60_000, activeCanaryMs: 80, promotionProbeMs: 100_000 },
    swap: { quietMs: 0, graceMs: 2_000 },
    thresholds: { failureCount: 3, failureWindowMs: 5_000 },
    cooldowns: { AUTH: 600_000, QUOTA: 600_000, SOFT: 600_000 },
    restartBudget: { max: 5, windowMs: 60_000 },
    canaryPrompt: 'Health check: respond with the single word OK.',
    canary: { timeoutMs: 5_000, expect: 'OK' },
    notify: true,
    logFile: 'serve.log',
    lockFile: '.serve.lock',
    stateFile: '.serve-state.json',
    chain: [
      { name: 'alpha', adapter: 'adapter-alpha', canaryCmd: ['bash', join(dir, 'canary-alpha.sh'), '{prompt}'] },
      { name: 'beta', adapter: 'adapter-beta', canaryCmd: ['bash', join(dir, 'canary-beta.sh'), '{prompt}'] },
    ],
    patterns: realPatterns,
  }, dir)

  const logs = []
  const notices = []
  const fetchImpl = async (url, init) => {
    notices.push(JSON.parse(init.body).text)
    return { ok: true }
  }
  const supervisor = new Supervisor(config, { log: (l) => logs.push(l), fetchImpl })
  const adapters = () => (existsSync(join(dir, 'adapters.log'))
    ? readFileSync(join(dir, 'adapters.log'), 'utf8').trim().split('\n')
    : [])
  const setCanary = (name, mode) => writeFileSync(join(dir, `canary-${name}.mode`), mode)
  return { dir, config, supervisor, logs, notices, adapters, setCanary }
}

let current
afterEach(async () => {
  if (current !== undefined) {
    await current.supervisor.stop()
    rmSync(current.dir, { recursive: true, force: true })
    current = undefined
  }
})

describe('serve loop', { timeout: 20_000 }, () => {
  it('boots the bridge on the first agent and holds the single-instance lock', async () => {
    current = sandbox()
    await current.supervisor.start()
    await until(() => current.adapters().length === 1)
    expect(current.adapters()).toEqual(['adapter-alpha'])
    expect(existsSync(join(current.dir, '.serve.lock'))).toBe(true)

    const second = new Supervisor(current.config, { log: () => {}, fetchImpl: async () => ({ ok: true }) })
    await expect(second.start()).rejects.toThrow(/another supervisor/)
  })

  it('demotes on an AUTH canary failure, swaps the bridge, and tells the owner', async () => {
    current = sandbox()
    current.setCanary('alpha', 'auth')
    await current.supervisor.start()
    await until(() => current.adapters().includes('adapter-beta'))
    expect(current.adapters()[0]).toBe('adapter-alpha')
    expect(current.logs.some((l) => l.includes('chain: alpha -> beta') && l.includes('AUTH'))).toBe(true)
    await until(() => current.notices.some((n) => n.includes('reconnected')))
    expect(current.notices.find((n) => n.includes('reconnected'))).toContain('beta')
    const state = JSON.parse(readFileSync(join(current.dir, '.serve-state.json'), 'utf8'))
    expect(state.active).toBe('beta')
    expect(state.cooldownUntil.alpha).toBeTypeOf('number')
  })

  it('restarts a dead bridge without demoting when the agent canary passes', async () => {
    current = sandbox()
    writeFileSync(join(current.dir, 'bridge-die-once'), '')
    await current.supervisor.start()
    await until(() => current.adapters().length === 2)
    expect(current.adapters()).toEqual(['adapter-alpha', 'adapter-alpha'])
    expect(current.logs.some((l) => l.startsWith('chain:'))).toBe(false)
  })

  it('skips an adapter the bridge refuses and settles on the next agent', async () => {
    current = sandbox()
    current.config.chain[0].adapter = 'refused'
    await current.supervisor.start()
    await until(() => current.adapters().includes('adapter-beta'))
    expect(current.adapters()[0]).toBe('refused')
    expect(current.logs.some((l) => l.includes('UNSUPPORTED'))).toBe(true)
  })

  it('alerts when the whole chain is down, then revives on the first agent that heals', async () => {
    current = sandbox()
    current.config.intervals.promotionProbeMs = 80
    current.setCanary('alpha', 'auth')
    current.setCanary('beta', 'quota')
    await current.supervisor.start()
    await until(() => current.notices.some((n) => n.includes('every agent is down')))

    current.setCanary('alpha', 'ok')
    await until(() => current.logs.some((l) => l.includes('chain: revived on alpha')))
    await until(() => current.adapters().filter((a) => a === 'adapter-alpha').length >= 2)
  })

  it('processes back-to-back bridge exits even while an owner notice hangs', async () => {
    // Regression: a hanging Telegram call inside the first exit's processing
    // must not swallow the next bridge exit (that was a dead bot until the
    // next reconcile tick). Both adapters get refused; both refusals must be
    // walked even though every notice attempt hangs forever.
    current = sandbox()
    current.supervisor.fetchImpl = () => new Promise(() => {}) // notify never resolves
    current.config.chain[0].adapter = 'refused-a'
    current.config.chain[1].adapter = 'refused-b'
    await current.supervisor.start()
    await until(() => current.logs.some((l) => l.includes('every agent is down')))
    expect(current.adapters()).toEqual(['refused-a', 'refused-b'])
  })

  it('cascades TELEGRAM_BOT_TOKEN and OWNER_ID from the main .env when the bridge has none', async () => {
    current = sandbox()
    const { dir, config } = current
    // Split the layout: the bridge lives in a subdir WITHOUT its own .env; the
    // main repo .env (at the sandbox root) holds the credentials.
    const bridgeDir = join(dir, 'bridge-checkout')
    mkdirSync(bridgeDir)
    writeFileSync(join(bridgeDir, 'bridge.sh'), `#!/usr/bin/env bash
echo "token=$TELEGRAM_BOT_TOKEN owner=$OWNER_ID" >> "${dir}/env-seen.log"
trap 'exit 0' TERM
sleep 300 & wait $!
`, { mode: 0o755 })
    writeFileSync(join(dir, '.env'), 'TELEGRAM_BOT_TOKEN=main-env-tok\nOWNER_ID=99\n')
    config.bridge.dir = bridgeDir
    config.bridge.envFile = join(bridgeDir, '.env') // does not exist
    config.bridge.stateFile = join(bridgeDir, 'bot-state.json') // no owner claimed yet
    config.bridge.startCmd = ['bash', 'bridge.sh']
    config.bridge.envCascade = ['TELEGRAM_BOT_TOKEN', 'OWNER_ID']
    await current.supervisor.start()
    await until(() => existsSync(join(dir, 'env-seen.log')))
    expect(readFileSync(join(dir, 'env-seen.log'), 'utf8')).toContain('token=main-env-tok owner=99')

    // The owner notice also cascades: no state file, so OWNER_ID is the chat id.
    current.setCanary('alpha', 'auth')
    await until(() => current.notices.some((n) => n.includes('reconnected')))
  })

  it('counts transient failures and only demotes at the threshold', async () => {
    current = sandbox()
    current.setCanary('alpha', 'flaky')
    await current.supervisor.start()
    await until(() => current.adapters().includes('adapter-beta'))
    const demotions = current.logs.filter((l) => l.startsWith('chain: alpha -> beta'))
    expect(demotions).toHaveLength(1)
    expect(demotions[0]).toContain('TRANSIENT')
    const canaryFailures = current.logs.filter((l) => l.startsWith('canary: alpha failed'))
    expect(canaryFailures.length).toBeGreaterThanOrEqual(3)
  })
})
