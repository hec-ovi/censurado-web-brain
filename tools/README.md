# tools

Standalone operator utilities, no-dependency Python (stdlib only), run by hand (nothing
in the repo invokes them).

- **`agent_tokens.py`**: per-article token accounting for the CLI publishing lane.
  Reads a subagent's transcript from the default Claude Code location
  (`~/.claude/projects/<project>/<session>/subagents/agent-<id>.jsonl`; pass a full
  path instead if yours live elsewhere) and appends to the gitignored
  `metrics/token-usage.jsonl` in this repo, recording the real input / output / cache
  token breakdown, so you accumulate the cost per published article and an average
  instead of eyeballing the CLI meter.
  `agent_tokens.py extract <agentId|path>`, `... record <agentId> --kind article --author <id> --title "..." --cli-tokens <n>`, `... summary`, `... cost` (Opus 4.8 prefill-vs-generated cost, by kind).

This lives outside `scripts/` (which stays gitignored, host-local seed data) because it
carries no persona text, sources, or keys, so it is safe to track in the public repo.
