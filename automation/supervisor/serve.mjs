#!/usr/bin/env node
// Entry point for the 24/7 serve loop. Everything it does is driven by
// supervisor.config.json next to this file (or --config <path>).
//
//   node automation/supervisor/serve.mjs            # or: ./run.sh serve
//
// See REQUIREMENTS.md in this directory for what it guarantees.

import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { resolveConfig, Supervisor } from './lib/supervisor.mjs'

const here = dirname(fileURLToPath(import.meta.url))
const args = process.argv.slice(2)
const flagIndex = args.indexOf('--config')
const configPath = flagIndex !== -1 && args[flagIndex + 1] !== undefined
  ? resolve(args[flagIndex + 1])
  : resolve(here, 'supervisor.config.json')

let raw
try {
  raw = JSON.parse(readFileSync(configPath, 'utf8'))
} catch (err) {
  console.error(`serve: cannot read config ${configPath}: ${err.message}`)
  process.exit(2)
}

const supervisor = new Supervisor(resolveConfig(raw, dirname(configPath)))

let shuttingDown = false
async function shutdown(signal) {
  if (shuttingDown) return
  shuttingDown = true
  console.log(`serve: ${signal}, shutting down`)
  await supervisor.stop()
  process.exit(0)
}
process.on('SIGINT', () => void shutdown('SIGINT'))
process.on('SIGTERM', () => void shutdown('SIGTERM'))

supervisor.start().catch((err) => {
  console.error(`serve: ${err.message}`)
  process.exit(2)
})
