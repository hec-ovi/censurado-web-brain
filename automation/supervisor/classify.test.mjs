import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { classify, compilePatterns } from './lib/classify.mjs'

// The real tables ship in the config; test against them, not a fixture, so a
// config edit that breaks classification fails here.
const config = JSON.parse(readFileSync(new URL('./supervisor.config.json', import.meta.url), 'utf8'))
const compiled = compilePatterns(config.patterns)

describe('classify', () => {
  it.each([
    ['Error: please login again to continue', 'AUTH'],
    ['HTTP 401 Unauthorized', 'AUTH'],
    ['OAuth token expired, run the auth flow', 'AUTH'],
    ['could not load credentials', 'AUTH'],
    ['You exceeded your current quota, please check your plan', 'QUOTA'],
    ['429 Too Many Requests', 'QUOTA'],
    ["You've hit your usage limit. Try again later.", 'QUOTA'],
    ['Your credit balance is too low to access the API', 'QUOTA'],
    ['rate limit reached for requests', 'QUOTA'],
    ['fetch failed: ECONNRESET', 'TRANSIENT'],
    ['upstream returned 503 Service Unavailable', 'TRANSIENT'],
    ['the model is overloaded, retry shortly', 'TRANSIENT'],
    ['Unknown AGENT_ADAPTER "acme". Use claude-code or pi.', 'UNSUPPORTED'],
    ['bash: acme: command not found', 'UNSUPPORTED'],
  ])('%s -> %s', (text, cls) => {
    expect(classify(text, compiled).cls).toBe(cls)
  })

  it('returns UNKNOWN with the last line as evidence when nothing matches', () => {
    const verdict = classify('something odd\nfinal line here', compiled)
    expect(verdict.cls).toBe('UNKNOWN')
    expect(verdict.evidence).toBe('final line here')
  })

  it('handles empty output', () => {
    expect(classify('', compiled)).toEqual({ cls: 'UNKNOWN', evidence: '' })
  })

  it('prefers AUTH over TRANSIENT regardless of line order', () => {
    const text = 'ECONNRESET while talking upstream\nplease login again'
    expect(classify(text, compiled).cls).toBe('AUTH')
  })

  it('prefers UNSUPPORTED over everything', () => {
    const text = 'please login\nUnknown AGENT_ADAPTER "x"'
    expect(classify(text, compiled).cls).toBe('UNSUPPORTED')
  })

  it('reports the matching line as evidence', () => {
    const verdict = classify('noise\nHTTP 401 Unauthorized\nmore noise', compiled)
    expect(verdict.evidence).toBe('HTTP 401 Unauthorized')
  })
})
