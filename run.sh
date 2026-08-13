#!/usr/bin/env bash
# Censurado stack runner. Needs only bash + docker (no make, no apt install).
# This is the no-dependency entry point; the Makefile just mirrors these for `make` users.
#
# ONE command: `./run.sh up` creates .env + mints secrets if missing, brings the stack up,
# and WAITS until the site is actually serving (so you never hit the bare nginx 404 while
# the generator is still doing its slow first build). Add --console to watch the logs live.
set -eo pipefail
cd "$(dirname "$0")"

# Ports come from .env (defaults match .env.example); needed for the readiness probe.
[ -f .env ] && { set -a; . ./.env; set +a; } || true
PUB_PORT="${PUBLISH_PORT:-8082}"
WEB_PORT="${SITE_PORT:-8123}"

usage() {
  cat <<EOF
Censurado stack runner (needs only bash + docker):
  ./run.sh up             ONE command: config + secrets if missing, up, WAIT until it serves
  ./run.sh up --console   ...in the foreground with the logs attached (no readiness wait)
  ./run.sh up-gpu         the whole stack incl. comfyui (heavy, needs the GPU box)
  ./run.sh comfyui        add ONLY comfyui later, once the fast lane is already up
  ./run.sh down           stop the stack (keeps data)
  ./run.sh ps | logs      status / follow logs
  ./run.sh generate       one-shot rebuild of the static site, then exit
  ./run.sh serve          the 24/7 loop: stack + telegram bridge (needs node)
  ./run.sh build | config | init-perms | stack-clean | bootstrap | deploy
  ./run.sh start          alias of 'up'
Once up: site http://localhost:${WEB_PORT}  ·  API + panel http://localhost:${PUB_PORT}
EOF
}

# DRY_RUN=1 echoes the command instead of running it (used by the test suite).
run() { if [ "${DRY_RUN:-}" = 1 ]; then printf '+ %s\n' "$*"; else "$@"; fi; }

ensure_config() {
  if [ "${DRY_RUN:-}" = 1 ]; then
    run "ensure .env (cp .env.example if missing)"
    run "ensure secrets (./bootstrap.sh if keys.json missing)"
    return
  fi
  [ -f .env ] || { echo "[.env] creating from .env.example"; cp .env.example .env; }
  [ -s keys.json ] || { echo "[secrets] minting (keys.json missing)"; ./bootstrap.sh; }
}

# Block until the generator has actually written the site (nginx serves /404.html, which
# generate always emits regardless of how empty the corpus is). Dumps the generate log and
# fails if it never comes up, so a crash or a stuck first-compile is visible, not silent.
wait_ready() {
  [ "${DRY_RUN:-}" = 1 ] && return 0
  printf 'backend'
  for _ in $(seq 1 60); do
    curl -sf "http://localhost:${PUB_PORT}/healthz" >/dev/null 2>&1 && break
    printf '.'; sleep 2
  done
  echo ' up'
  printf 'building the site (first run downloads Go modules, ~1-2 min)'
  for _ in $(seq 1 150); do
    code=$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:${WEB_PORT}/404.html" 2>/dev/null || true)
    if [ "$code" = 200 ]; then
      echo; echo "READY -> http://localhost:${WEB_PORT}  (empty until the first publish)"
      return 0
    fi
    printf '.'; sleep 2
  done
  echo; echo "the site is not serving yet. last 30 lines of the generate log:"
  docker compose logs --tail=30 generate || true
  echo "(re-run './run.sh logs' to keep watching, or './run.sh generate' to force a one-shot build)"
  return 1
}

bring_up() { # $1 = extra compose args (e.g. "--profile comfyui")
  ensure_config
  run docker compose $1 up $detach
  # detached: block until it actually serves. --console (detach empty) already streams logs.
  if [ -n "$detach" ]; then wait_ready; fi
}

detach=-d
cmd="${1:-help}"
[ $# -gt 0 ] && shift
for a in "$@"; do
  case "$a" in --console | -f | --foreground) detach= ;; esac
done

case "$cmd" in
  up | start)  bring_up "" ;;
  up-gpu)      bring_up "--profile comfyui" ;;
  comfyui)     run docker compose --profile comfyui up $detach comfyui ;;
  down)        run docker compose down ;;
  ps)          run docker compose ps ;;
  logs)        run docker compose logs -f ;;
  generate)
    run docker compose run --rm init-perms
    run docker compose run --rm --no-deps generate go run ./cmd/censurado/generate -out /site -page-size 6
    ;;
  build)       run docker compose build ;;
  init-perms)  run docker compose run --rm init-perms ;;
  config)      run docker compose config -q && echo OK ;;
  stack-clean) run docker compose down -v --remove-orphans ;;
  bootstrap)   run ./bootstrap.sh ;;
  serve)       run node automation/supervisor/serve.mjs ;;
  deploy)      run ./deploy/deploy-cdn.sh ;;
  help | -h | --help) usage ;;
  *)
    echo "run.sh: unknown command '$cmd' (try: ./run.sh help)" >&2
    exit 2
    ;;
esac
