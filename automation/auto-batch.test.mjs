import { describe, expect, it } from 'vitest'
import { spawnSync } from 'node:child_process'
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const SCRIPT = join(dirname(fileURLToPath(import.meta.url)), 'auto-batch.sh')

// Real entry point, stubbed edges: a fake python3 answers the stack preflight and a fake
// agent binary records the argv it was launched with.
function run(args, { preflightExit = 0, agentExit = 0, env = {} } = {}) {
  const dir = mkdtempSync(join(tmpdir(), 'auto-batch-'))
  const bin = join(dir, 'bin')
  mkdirSync(bin)
  const argvFile = join(dir, 'agent-argv.txt')

  writeFileSync(join(bin, 'python3'), `#!/bin/sh\nexit ${preflightExit}\n`)
  // The prompt argument spans lines, so record argv with a separator no arg can contain.
  writeFileSync(
    join(bin, 'fake-agent'),
    `#!/bin/sh\nprintf '%s<<ARG>>' "$@" > '${argvFile}'\nexit ${agentExit}\n`,
  )
  chmodSync(join(bin, 'python3'), 0o755)
  chmodSync(join(bin, 'fake-agent'), 0o755)

  const result = spawnSync('bash', [SCRIPT, ...args], {
    encoding: 'utf8',
    env: {
      ...process.env,
      PATH: `${bin}:${process.env.PATH}`,
      AUTO_BATCH_AGENT_CMD: 'fake-agent --flag-a --flag-b -p {prompt}',
      AUTO_BATCH_LOG_DIR: join(dir, 'logs'),
      ...env,
    },
  })
  const argv = existsSync(argvFile)
    ? readFileSync(argvFile, 'utf8').split('<<ARG>>').slice(0, -1)
    : null
  return { result, argv }
}

describe('auto-batch.sh', () => {
  it('launches the templated agent command with the prompt substituted', () => {
    const { result, argv } = run(['daily', '1'])
    expect(result.status).toBe(0)
    expect(argv).not.toBeNull()
    expect(argv.slice(0, 3)).toEqual(['--flag-a', '--flag-b', '-p'])
    const prompt = argv[3]
    expect(prompt).toContain("cover today's news")
    expect(prompt).toContain('Write EXACTLY 1 article(s) this run')
    expect(result.stdout).toContain('done (agent exit 0)')
  })

  it('builds the sweep prompt for the weekly window when no cap is set', () => {
    const { argv } = run(['weekly'])
    const prompt = argv[3]
    expect(prompt).toContain("cover this week's news")
    expect(prompt).toContain('Write a healthy sweep of 3 to 6 articles')
  })

  it('skips without launching the agent when the stack preflight fails', () => {
    const { result, argv } = run(['daily', '1'], { preflightExit: 1 })
    expect(result.status).toBe(1)
    expect(result.stdout).toContain('SKIP: stack is not up')
    expect(argv).toBeNull()
  })

  it('propagates the agent exit code', () => {
    const { result } = run(['daily', '1'], { agentExit: 3 })
    expect(result.status).toBe(3)
  })

  it('rejects an unknown mode before touching anything', () => {
    const { result, argv } = run(['hourly'])
    expect(result.status).toBe(2)
    expect(argv).toBeNull()
  })
})
