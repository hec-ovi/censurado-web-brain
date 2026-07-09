// Failure classifier: maps raw agent/bridge output to a failure class using
// the per-class regex tables from supervisor.config.json. Patterns live in
// config, not here, so a new error string is a config edit, not a code change.

// Precedence: a text that matches several classes takes the first one here.
// UNSUPPORTED beats everything (a missing binary/adapter also prints noise),
// AUTH and QUOTA beat TRANSIENT (a login page may arrive over a flaky pipe).
export const CLASS_ORDER = ['UNSUPPORTED', 'AUTH', 'QUOTA', 'TRANSIENT']

export function compilePatterns(tables) {
  const compiled = {}
  for (const cls of CLASS_ORDER) {
    compiled[cls] = (tables?.[cls] ?? []).map((src) => new RegExp(src, 'i'))
  }
  return compiled
}

// Returns { cls, evidence }. cls is one of CLASS_ORDER or 'UNKNOWN'.
// evidence is the first matching line, capped, so every decision is loggable.
export function classify(text, compiled) {
  const lines = String(text ?? '').split('\n').filter((l) => l.trim().length > 0)
  for (const cls of CLASS_ORDER) {
    for (const re of compiled[cls] ?? []) {
      const hit = lines.find((l) => re.test(l))
      if (hit !== undefined) return { cls, evidence: hit.trim().slice(0, 300) }
    }
  }
  return { cls: 'UNKNOWN', evidence: lines[lines.length - 1]?.trim().slice(0, 300) ?? '' }
}
