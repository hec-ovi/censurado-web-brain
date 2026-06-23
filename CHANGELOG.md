# Changelog

All notable changes to censurado-web-brain are recorded here. This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial repository scaffold: README, MIT license, Python `.gitignore`, and this changelog.
- Plan 1 architecture research: `docs/research/stage-2-newsroom-architecture.md`. Answers the eight agentic-workflow and agentic-loop design questions, pins the platform publish seam against the live `censurado-web` code, chooses plain Python with pydantic-ai only at the finalize seam, maps reuse of gamentic / websearch-skill / hermes, and lays out a ten-step, independently-testable Plan 2 build.
- Step 0 (the brain build begins): the `newsroom` package skeleton (isolated `brain`, `personas`, `inference`, `research`, `publish`, `contracts` sub-packages) and the versioned `prompts/` tree; the one shared in-repo test fake (`testkit`) serving a scriptable OpenAI-dialect `/v1/chat/completions` that rejects any output-length cap and a faithful `/articles` publish stand-in (both scopes including the 403 path, content-hash idempotency, author binding, strict unknown-field rejection); the vendored `article.schema.json` (byte-identical to the platform) with a CI drift guard; the harness section enum; a content-hash port proven byte-equal to the platform's Go implementation via an independent Go reference vector; and a green pytest suite driven over real HTTP. Toolchain is uv (pyproject, Makefile, .env.example).

### Changed
- Re-questioned Plan 1 under the precise "agentic workflow" term (not a generic workflow) and audited the research doc section by section. Verdict: the substance was already agentic-native and no design decision was skewed. Tightened A.2 (separated the halting proof, a monotonic harness bound, from the quality guards) and appended a Plan 2 refinements section (local-model persona handling, persona-blind fact-check, context-rot handling, why a small output cap can cause a runaway loop).
