// End-to-end tests of the serve loop against fake binaries: a scripted bridge,
// no docker, no network. Each case exercises one guarantee from REQUIREMENTS.md.

import { afterEach, describe, expect, it } from 'vitest'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { resolveConfig, Supervisor } from './lib/supervisor.mjs'

async function until(fn, ms = 8_000) {
  const t0 = Date.now()
  while (Date.now() - t0 < ms) {
    if (fn()) return
    await new Promise((r) => setTimeout(r, 25))
  }
  throw new Error(`condition not met within ${ms}ms`)
}

function sandbox(overrides = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'serve-test-'))

  // The fake bridge: logs each start with its adapter, honors a die-once
  // marker, then stays alive until SIGTERM.
  writeFileSync(join(dir, 'bridge.sh'), `#!/usr/bin/env bash
echo "$AGENT_ADAPTER" >> "${dir}/adapters.log"
if [ -f "${dir}/bridge-die-once" ]; then
  rm "${dir}/bridge-die-once"
  echo "socket hang up" >&2
  exit 1
fi
echo "@bot is polling"
trap 'exit 0' TERM
sleep 300 & wait $!
`, { mode: 0o755 })

  // The bridge checkout the supervisor reads notify credentials from.
  writeFileSync(join(dir, '.env'), 'TELEGRAM_BOT_TOKEN=tok123\n')
  writeFileSync(join(dir, 'bot-state.json'), JSON.stringify({
    users: { 7: { state: 'owner', chatId: 7, addedAt: 'x' } }, sessions: {},
  }))

  const config = resolveConfig({
    repoRoot: '.',
    bridge: {
      dir: '.', startCmd: ['bash', 'bridge.sh'], adapter: 'adapter-alpha', graceMs: 2_000,
      envCascade: ['TELEGRAM_BOT_TOKEN', 'OWNER_ID'],
    },
    docker: { checkCmd: ['true'], checkTimeoutMs: 5_000, upCmd: ['true'], upTimeoutMs: 5_000 },
    intervals: { dockerCheckMs: 60_000 },
    restartBudget: { max: 5, windowMs: 60_000 },
    notify: true,
    logFile: 'serve.log',
    lockFile: '.serve.lock',
    ...overrides,
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
  return { dir, config, supervisor, logs, notices, adapters }
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
  it('boots the bridge on the configured adapter and holds the single-instance lock', async () => {
    current = sandbox()
    await current.supervisor.start()
    await until(() => current.adapters().length === 1)
    expect(current.adapters()).toEqual(['adapter-alpha'])
    expect(existsSync(join(current.dir, '.serve.lock'))).toBe(true)

    const second = new Supervisor(current.config, { log: () => {}, fetchImpl: async () => ({ ok: true }) })
    await expect(second.start()).rejects.toThrow(/another supervisor/)
  })

  it('restarts a dead bridge on the same adapter', async () => {
    current = sandbox()
    writeFileSync(join(current.dir, 'bridge-die-once'), '')
    await current.supervisor.start()
    await until(() => current.adapters().length === 2)
    expect(current.adapters()).toEqual(['adapter-alpha', 'adapter-alpha'])
    expect(current.logs.some((l) => l.includes('bridge: exited'))).toBe(true)
  })

  it('holds with an owner alert when the restart budget is spent', async () => {
    // A bridge that dies instantly every time burns the budget; the loop must
    // stop restarting, alert the owner ONCE, and keep running.
    current = sandbox({ restartBudget: { max: 2, windowMs: 60_000 } })
    writeFileSync(join(current.dir, 'bridge.sh'), `#!/usr/bin/env bash
echo "$AGENT_ADAPTER" >> "${current.dir}/adapters.log"
echo "boom" >&2
exit 1
`, { mode: 0o755 })
    await current.supervisor.start()
    await until(() => current.notices.some((n) => n.includes('restart budget spent')))
    expect(current.adapters().length).toBe(2)
    expect(current.notices.filter((n) => n.includes('restart budget spent'))).toHaveLength(1)
  })

  it('alerts when the bridge cannot spawn at all', async () => {
    current = sandbox()
    current.config.bridge.startCmd = ['/no/such/binary']
    await current.supervisor.start()
    await until(() => current.notices.some((n) => n.includes('bridge cannot start')))
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
    await current.supervisor.start()
    await until(() => existsSync(join(dir, 'env-seen.log')))
    expect(readFileSync(join(dir, 'env-seen.log'), 'utf8')).toContain('token=main-env-tok owner=99')
  })

  it('falls back to the process env for notify credentials when no .env has them', async () => {
    current = sandbox({ restartBudget: { max: 1, windowMs: 60_000 } })
    const { dir } = current
    // No .env anywhere holds the token; only the supervisor's own process env
    // (the systemd Environment= case). The owner alert must still go out.
    rmSync(join(dir, '.env'))
    rmSync(join(dir, 'bot-state.json'))
    writeFileSync(join(dir, 'bridge.sh'), '#!/usr/bin/env bash\nexit 1\n', { mode: 0o755 })
    const saved = { token: process.env.TELEGRAM_BOT_TOKEN, owner: process.env.OWNER_ID }
    process.env.TELEGRAM_BOT_TOKEN = 'proc-env-tok'
    process.env.OWNER_ID = '7'
    try {
      await current.supervisor.start()
      await until(() => current.notices.some((n) => n.includes('restart budget spent')))
    } finally {
      if (saved.token === undefined) delete process.env.TELEGRAM_BOT_TOKEN
      else process.env.TELEGRAM_BOT_TOKEN = saved.token
      if (saved.owner === undefined) delete process.env.OWNER_ID
      else process.env.OWNER_ID = saved.owner
    }
  })
})
