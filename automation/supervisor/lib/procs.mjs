// Process and I/O helpers for the supervisor: run-to-completion with a hard
// timeout, long-lived children with line streaming, the single-instance lock,
// .env parsing, and the owner Telegram notice. No dependencies.

import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'

const OUTPUT_CAP = 64 * 1024 // keep the tail; error lines arrive last

function capTail(buf, chunk) {
  const joined = buf + chunk
  return joined.length > OUTPUT_CAP ? joined.slice(joined.length - OUTPUT_CAP) : joined
}

// Children are spawned detached (own process group) so a stop or timeout can
// signal the WHOLE tree: agent CLIs and bridges spawn their own children, and
// killing only the direct child leaves grandchildren alive. Completion keys on
// 'exit', not 'close': a leaked grandchild holding the stdio pipes must never
// wedge supervision. One setImmediate after exit lets buffered output land.
function signalTree(child, sig) {
  try {
    process.kill(-child.pid, sig)
  } catch {
    try { child.kill(sig) } catch { /* already gone */ }
  }
}

// Run a command to completion. Resolves { code, timedOut, spawnError, stdout,
// stderr }; never rejects. A spawn failure (missing binary) sets spawnError.
export function runCommand(cmd, { timeoutMs, cwd, env } = {}) {
  return new Promise((resolve) => {
    const [bin, ...args] = cmd
    let stdout = ''
    let stderr = ''
    let timedOut = false
    let settled = false
    const child = spawn(bin, args, { cwd, env: env ?? process.env, stdio: ['ignore', 'pipe', 'pipe'], detached: true })
    const timer = timeoutMs !== undefined
      ? setTimeout(() => {
          timedOut = true
          signalTree(child, 'SIGTERM')
          setTimeout(() => signalTree(child, 'SIGKILL'), 5_000).unref()
        }, timeoutMs)
      : undefined
    const done = (code, spawnError) => {
      if (settled) return
      settled = true
      if (timer !== undefined) clearTimeout(timer)
      setImmediate(() => resolve({ code, timedOut, spawnError, stdout, stderr }))
    }
    child.stdout.on('data', (d) => { stdout = capTail(stdout, String(d)) })
    child.stderr.on('data', (d) => { stderr = capTail(stderr, String(d)) })
    child.on('error', (err) => done(null, err))
    child.on('exit', (code) => done(code, undefined))
  })
}

// Start a long-lived child. onLine fires per output line (stdout and stderr),
// onExit once with { code, spawnError, recentOutput }. stop() is graceful:
// SIGTERM, then SIGKILL after graceMs.
export function startChild(cmd, { cwd, env, graceMs = 10_000, onLine, onExit } = {}) {
  const [bin, ...args] = cmd
  const child = spawn(bin, args, { cwd, env: env ?? process.env, stdio: ['ignore', 'pipe', 'pipe'], detached: true })
  let recent = ''
  let exited = false
  let killTimer
  const feed = (chunk) => {
    recent = capTail(recent, String(chunk))
    for (const line of String(chunk).split('\n')) {
      if (line.trim().length > 0) onLine?.(line)
    }
  }
  const finish = (code, spawnError) => {
    if (exited) return
    exited = true
    if (killTimer !== undefined) clearTimeout(killTimer)
    setImmediate(() => onExit?.({ code, spawnError, recentOutput: recent }))
  }
  child.stdout.on('data', feed)
  child.stderr.on('data', feed)
  child.on('error', (err) => finish(null, err))
  child.on('exit', (code) => finish(code, undefined))
  return {
    get exited() { return exited },
    get recentOutput() { return recent },
    stop() {
      return new Promise((resolve) => {
        if (exited) return resolve()
        child.once('exit', () => resolve())
        signalTree(child, 'SIGTERM')
        killTimer = setTimeout(() => signalTree(child, 'SIGKILL'), graceMs)
        killTimer.unref()
      })
    },
  }
}

// Single instance: a pid lockfile with stale-pid takeover, same job as the
// flock in auto-batch.sh but portable to a long-lived node process.
export function acquireLock(path) {
  mkdirSync(dirname(path), { recursive: true })
  try {
    writeFileSync(path, String(process.pid), { flag: 'wx' })
    return true
  } catch (err) {
    if (err.code !== 'EEXIST') throw err
    const pid = Number(readFileSync(path, 'utf8').trim())
    if (Number.isInteger(pid) && pid > 0) {
      try {
        process.kill(pid, 0) // throws when the pid is gone
        return false
      } catch { /* stale */ }
    }
    rmSync(path, { force: true })
    writeFileSync(path, String(process.pid), { flag: 'wx' })
    return true
  }
}

export function releaseLock(path) {
  try {
    const pid = Number(readFileSync(path, 'utf8').trim())
    if (pid === process.pid) rmSync(path, { force: true })
  } catch { /* already gone */ }
}

// Minimal KEY=VALUE .env reader (comments and blank lines skipped, optional
// surrounding quotes stripped). Read-only: never mutates process.env.
export function parseEnvFile(path) {
  if (!existsSync(path)) return {}
  const out = {}
  for (const raw of readFileSync(path, 'utf8').split('\n')) {
    const line = raw.trim()
    if (line.length === 0 || line.startsWith('#')) continue
    const eq = line.indexOf('=')
    if (eq <= 0) continue
    const key = line.slice(0, eq).trim()
    let value = line.slice(eq + 1).trim()
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1)
    }
    out[key] = value
  }
  return out
}

// The owner's chat id, from the bridge's own state file (users keyed by id,
// each { state, chatId }). Returns undefined when unclaimed or unreadable.
export function findOwnerChatId(stateFilePath) {
  try {
    const data = JSON.parse(readFileSync(stateFilePath, 'utf8'))
    for (const user of Object.values(data.users ?? {})) {
      if (user?.state === 'owner' && typeof user.chatId === 'number') return user.chatId
    }
  } catch { /* no state yet */ }
  return undefined
}

// Fire-and-forget owner notice over the plain Bot API. Failures are returned,
// not thrown, and the call is hard-bounded: the supervisor must keep running
// when Telegram is down, and a hanging notice must never stall the loop.
export async function telegramNotify(token, chatId, text, fetchImpl = fetch) {
  try {
    const res = await fetchImpl(`https://api.telegram.org/bot${token}/sendMessage`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ chat_id: chatId, text }),
      signal: AbortSignal.timeout(10_000),
    })
    return res.ok
  } catch {
    return false
  }
}
