#!/usr/bin/env node
// Entry point. See CONTRACT.md for the exit codes and the record stream.
//
//   node automation/scheduler/run.mjs <config.json>            the loop
//   node automation/scheduler/run.mjs <config.json> --check    validate, print next fires
//   node automation/scheduler/run.mjs <config.json> --once <name>   fire one now

import { loadConfig, Scheduler } from './src/scheduler.mjs'

function usage() {
  process.stderr.write('usage: run.mjs <config.json> [--check | --once <name>]\n')
  return 2
}

async function main(argv) {
  const [configPath, flag, name] = argv
  if (configPath === undefined) return usage()

  let config
  try {
    config = loadConfig(configPath)
  } catch (err) {
    process.stderr.write(`${err.message}\n`)
    return err.code === 'CONFIG_INVALID' ? 2 : 1
  }
  const scheduler = new Scheduler(config, { log: (line) => process.stdout.write(`${line}\n`) })

  if (flag === '--check') {
    for (const entry of config.schedules) {
      process.stdout.write(`${entry.name}: next ${scheduler.nextFire(entry).toISOString()}\n`)
    }
    return 0
  }

  if (flag === '--once') {
    const entry = config.schedules.find((s) => s.name === name)
    if (entry === undefined) {
      process.stderr.write(`unknown schedule ${name ?? '(none)'}; names: ${config.schedules.map((s) => s.name).join(', ')}\n`)
      return 4
    }
    try {
      scheduler.lock()
    } catch (err) {
      process.stderr.write(`${err.message}\n`)
      return 3
    }
    try {
      const record = await scheduler.runNow(entry)
      return record.status === 'ok' ? 0 : 1
    } finally {
      scheduler.unlock()
    }
  }

  if (flag !== undefined) return usage()

  try {
    scheduler.start()
  } catch (err) {
    process.stderr.write(`${err.message}\n`)
    return err.code === 'LOCK_HELD' ? 3 : 1
  }
  await new Promise((done) => {
    const shutdown = () => { scheduler.stop(); done() }
    process.once('SIGINT', shutdown)
    process.once('SIGTERM', shutdown)
  })
  return 0
}

process.exitCode = await main(process.argv.slice(2))
