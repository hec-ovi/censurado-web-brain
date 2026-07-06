#!/usr/bin/env bash
# auto-batch.sh: drive the Censurado news batch UNATTENDED via the agy (Gemini) content agent.
#
# It is the single entry point a scheduler (systemd timer, cron, n8n exec node) calls. It
# preflights the stack, then runs agy HEADLESS (one prompt, auto-approved) bound to this repo so
# the agent sees the censurado operator skill and walks the gated step workflow to preview. It
# never deploys to production (going live stays a human-gated action).
#
# Usage:  auto-batch.sh <mode> [max_articles]
#   mode          daily | last-hour | weekly   (the freshness window the batch covers)
#   max_articles  cap for THIS run; 0 = a normal sweep (3-6). Use 1 for a smoke/test run.
#
# Env overrides:
#   AUTO_BATCH_TIMEOUT        hard wall-clock kill (default 40m)
#   AUTO_BATCH_PRINT_TIMEOUT  agy --print-timeout (default 35m)
#   AUTO_BATCH_MODEL          agy --model (default: agy's own default)
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:-daily}"
MAX="${2:-0}"
TIMEOUT="${AUTO_BATCH_TIMEOUT:-40m}"
PRINT_TIMEOUT="${AUTO_BATCH_PRINT_TIMEOUT:-35m}"
LOG_DIR="$REPO/automation/logs"
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
# site) is serving. No point spending a Gemini run against a down stack.
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

echo "-- launching agy headless (timeout $TIMEOUT, print-timeout $PRINT_TIMEOUT) --"
cd "$REPO"
MODEL_ARG=()
[ -n "${AUTO_BATCH_MODEL:-}" ] && MODEL_ARG=(--model "$AUTO_BATCH_MODEL")

timeout "$TIMEOUT" agy \
  --add-dir "$REPO" \
  --dangerously-skip-permissions \
  --print-timeout "$PRINT_TIMEOUT" \
  "${MODEL_ARG[@]}" \
  -p "$PROMPT"
rc=$?

if [ "$rc" -eq 124 ]; then
  echo "== $(date -Is) TIMED OUT after $TIMEOUT (agy killed). Partial work may be staged; check the log. =="
else
  echo "== $(date -Is) done (agy exit $rc) =="
fi
exit "$rc"
