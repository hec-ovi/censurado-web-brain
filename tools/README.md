# tools

Standalone operator utilities. Each is no-dependency Python (stdlib only) and reads
its config from the environment, so nothing here hardcodes a host path or a secret.

- **`agent_tokens.py`**: per-article token accounting for the CLI publishing lane.
  Reads a subagent's transcript (`~/.claude/projects/<project>/<session>/subagents/agent-<id>.jsonl`)
  and records the real input / output / cache token breakdown, so you accumulate the
  cost per published article and an average instead of eyeballing the CLI meter.
  `agent_tokens.py extract <agentId|path>`, `... record <agentId> --kind article --author <id> --title "..." --cli-tokens <n>`, `... summary`, `... cost` (Opus 4.8 prefill-vs-generated cost, by kind).

This lives outside `scripts/` (which stays gitignored, host-local seed data) because it
carries no persona text, sources, or keys, so it is safe to track in the public repo.
