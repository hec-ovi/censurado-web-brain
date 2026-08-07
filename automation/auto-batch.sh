#!/usr/bin/env bash
# auto-batch.sh: drive the Censurado news batch UNATTENDED via a headless agent CLI.
#
# It is the single entry point a scheduler (systemd timer, cron) calls. It preflights the
# stack, then runs the configured agent CLI HEADLESS (one prompt, auto-approved) bound to this
# repo so the agent sees the censurado operator skill and walks the gated step workflow to
# preview. It never deploys to production (going live stays a human-gated action).
#
# Usage:  auto-batch.sh <mode> [max_articles]
#   mode          daily | last-hour | weekly   (the freshness window the batch covers)
#   max_articles  cap for THIS run; 0 = a normal sweep (3-6). Use 1 for a smoke/test run.
#
# Env overrides:
#   AUTO_BATCH_TIMEOUT    hard wall-clock kill (default 40m)
#   AUTO_BATCH_AGENT_CMD  the agent command as a space-split template; {prompt} is replaced
#                         with the batch prompt. Any CLI with a headless one-prompt mode
#                         slots in. Default: claude --dangerously-skip-permissions -p {prompt}
#                         A template with no {prompt} gets the prompt on stdin instead,
#                         flattened to one line (for line-oriented REPL CLIs, e.g. noob --yolo)
#   AUTO_BATCH_LOG_DIR    where run logs and the lock live (default automation/logs)
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-daily}"
MAX="${2:-0}"
TIMEOUT="${AUTO_BATCH_TIMEOUT:-40m}"
# Default kept out of ${VAR:-...}: the braces in {prompt} break bash's expansion parsing.
AGENT_CMD_TPL="${AUTO_BATCH_AGENT_CMD:-}"
[ -n "$AGENT_CMD_TPL" ] || AGENT_CMD_TPL='claude --dangerously-skip-permissions -p {prompt}'
LOG_DIR="${AUTO_BATCH_LOG_DIR:-$REPO/automation/logs}"
LOCK="$LOG_DIR/.batch.lock"

case "$MODE" in
  daily)     WINDOW="today's news" ;;
  last-hour) WINDOW="the news from the last hour" ;;
  weekly)    WINDOW="this week's news" ;;
  *) echo "FATAL: mode must be daily | last-hour | weekly (got '$MODE')" >&2; exit 2 ;;
esac

mkdir -p "$LOG_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="$LOG_DIR/batch-$MODE-$STAMP.log"
# Send everything to the log AND the caller, without a subshell (so `exit` still works here).
exec > >(tee -a "$LOG") 2>&1

echo "== $(date -Is) auto-batch mode=$MODE max=$MAX repo=$REPO =="

# Single instance: a slow run must never overlap the next scheduler tick.
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "SKIP: another auto-batch run holds the lock; leaving this tick to it."
  exit 0
fi

# Preflight with the VERB, not a raw curl: status exits 0 only when the core (backend + local
# site) is serving. No point spending an agent run against a down stack.
if ! python3 "$REPO/cli/censurado.py" status >/dev/null 2>&1; then
  echo "SKIP: stack is not up (censurado.py status failed). Start it with"
  echo "      docker compose up -d publish generate site   (from $REPO), then it runs next tick."
  exit 1
fi

if [ "$MAX" -gt 0 ] 2>/dev/null; then
  CAP="Write EXACTLY $MAX article(s) this run, then stop."
else
  CAP="Write a healthy sweep of 3 to 6 articles, one at a time."
fi

read -r -d '' PROMPT <<EOF || true
Operate the Censurado news portal (you are the operator, not a developer). Run the ${MODE} news
batch: cover ${WINDOW}. ${CAP}

Use ONLY the censurado operator skill: every action is \`python3 cli/censurado.py <verb>\`. Walk
the gated step workflow ONE node at a time through to \`preview\`, honoring the sourcing floor and
the evaluate/respin gates. Do NOT run any other shell command, do NOT edit repo files, do NOT
spawn subagents, and do NOT deploy to production (stop at the local preview). When each piece is
staged, report its PREVIEW link.
EOF

echo "-- launching agent headless (timeout $TIMEOUT): $AGENT_CMD_TPL --"
cd "$REPO"
read -r -a TPL <<<"$AGENT_CMD_TPL"
CMD=()
VIA_ARGV=0
for arg in "${TPL[@]}"; do
  if [ "$arg" = "{prompt}" ]; then CMD+=("$PROMPT"); VIA_ARGV=1; else CMD+=("$arg"); fi
done

if [ "$VIA_ARGV" -eq 1 ]; then
  timeout "$TIMEOUT" "${CMD[@]}"
else
  # stdin lane: one flattened line, so line-oriented REPL CLIs read it as a single task.
  printf '%s\n' "${PROMPT//$'\n'/ }" | timeout "$TIMEOUT" "${CMD[@]}"
fi
rc=$?

if [ "$rc" -eq 124 ]; then
  echo "== $(date -Is) TIMED OUT after $TIMEOUT (agent killed). Partial work may be staged; check the log. =="
else
  echo "== $(date -Is) done (agent exit $rc) =="
fi
exit "$rc"
