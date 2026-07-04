#!/usr/bin/env bash
# Mint the one cross-service secret and the local operator panel login, then write them
# into .env so `docker compose up -d` works end to end. Safe to re-run: it
# always mints a fresh operator key and keeps/normalizes the local panel login.
set -euo pipefail
cd "$(dirname "$0")"

COMPOSE="docker compose"
ENV_FILE=".env"

# upsert KEY=VALUE in .env (python, so '/','+','=' in hashes/base64 are literal)
set_env() {
  python3 - "$ENV_FILE" "$1" "$2" <<'PY'
import sys
path, key, val = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    lines = open(path).read().splitlines()
except FileNotFoundError:
    lines = []
out, seen = [], False
for line in lines:
    if line.startswith(key + "="):
        out.append(f"{key}={val}"); seen = True
    else:
        out.append(line)
if not seen:
    out.append(f"{key}={val}")
open(path, "w").write("\n".join(out) + "\n")
PY
}

# 0. .env exists
if [ ! -f "$ENV_FILE" ]; then
  cp .env.example "$ENV_FILE"
  echo "[bootstrap] created .env from .env.example"
fi

# 1. seed the publish keys file as an empty JSON array (bind-mount needs a FILE)
if [ ! -f keys.json ]; then
  printf '[]\n' > keys.json
  echo "[bootstrap] seeded keys.json = []"
fi

# 1b. create the persistent data dirs (host bind mounts) so Docker does not create
# them root-owned. They hold the sqlite db and the /media images, and survive
# `docker compose down -v`. init-perms (step 5) chowns them to the nonroot writer uid
# (65532). Never deleted by a wipe; delete ./data by hand to truly start over.
mkdir -p data/db data/media
echo "[bootstrap] ensured persistent data dirs: data/{db,media}"

# 2. build the publish image we need for minting the operator key
echo "[bootstrap] building publish image..."
$COMPOSE build publish >/dev/null

# 3. mint the operator key (write + publish-any + admin:write) and register it in keys.json.
# admin:write lets the trusted operator key reach the edit lane (PUT /articles), which the
# topic cleanse uses to remap an existing article's tags; the append-only publish path
# never needs it but it is the same trusted key.
echo "[bootstrap] minting operator key..."
KEYOUT=$($COMPOSE run --rm -T --no-deps publish \
  -gen-key -author newsroom -scope articles:write -scope articles:publish-any -scope admin:write)
TOKEN=$(printf '%s\n' "$KEYOUT" | sed -n 's/^TOKEN=//p')
ENTRY=$(printf '%s\n' "$KEYOUT" | grep -E '^\{' | head -n1)
if [ -z "$TOKEN" ] || [ -z "$ENTRY" ]; then
  echo "[bootstrap] FAILED to parse gen-key output:" >&2
  printf '%s\n' "$KEYOUT" >&2
  exit 1
fi
python3 - keys.json "$ENTRY" <<'PY'
import sys, json
path, entry = sys.argv[1], sys.argv[2]
raw = open(path).read().strip()
arr = json.loads(raw) if raw else []
arr.append(json.loads(entry))
open(path, "w").write(json.dumps(arr, indent=2) + "\n")
PY
set_env NEWSROOM_OPERATOR_TOKEN "$TOKEN"
echo "[bootstrap] operator key registered in keys.json and written to .env"

# 4. configure the operator panel login (served by publish at 127.0.0.1:8082). The clear token is
# stored for localhost login prefill; PANEL_LOGIN_TOKEN_HASH gates access and
# PANEL_SESSION_KEY signs the session cookie.
echo "[bootstrap] configuring operator panel credentials..."
PANEL_CREDS=$(python3 - <<'PY'
import hashlib, secrets
token = ""
try:
    for line in open(".env").read().splitlines():
        if line.startswith("PANEL_LOGIN_TOKEN="):
            token = line.split("=", 1)[1]
            break
except FileNotFoundError:
    pass
token = token or "admin"
print("TOKEN=" + token)
print("HASH=" + hashlib.sha256(token.encode("utf-8")).hexdigest())
print("SKEY=" + secrets.token_hex(32))
PY
)
PTOK=$(printf '%s\n' "$PANEL_CREDS" | sed -n 's/^TOKEN=//p')
PHASH=$(printf '%s\n' "$PANEL_CREDS" | sed -n 's/^HASH=//p')
PSKEY=$(printf '%s\n' "$PANEL_CREDS" | sed -n 's/^SKEY=//p')
if [ -z "$PTOK" ] || [ -z "$PHASH" ] || [ -z "$PSKEY" ]; then
  echo "[bootstrap] FAILED to mint panel credentials:" >&2
  printf '%s\n' "$PANEL_CREDS" >&2
  exit 1
fi
set_env PANEL_LOGIN_TOKEN "$PTOK"
set_env PANEL_LOGIN_TOKEN_HASH "$PHASH"
set_env PANEL_SESSION_KEY "$PSKEY"
echo "[bootstrap] panel login token + hash + session key written to .env"

# 5. make the shared site volume AND the persistent ./data bind mounts writable by
# the nonroot writer uid (65532). They initialize root-owned, so the first
# `generate`/regenerate or media write would fail with "permission denied".
# Idempotent.
echo "[bootstrap] fixing site + data permissions..."
$COMPOSE run --rm -T init-perms >/dev/null

echo
echo "=================================================================="
echo " Bootstrap complete. Secrets are in .env; keys.json holds the hash."
echo
echo "  Operator panel login token (127.0.0.1:8082, prefilled locally):"
echo "    $PTOK"
echo
echo " Next:"
echo "   docker compose up -d                  # bring the stack up (the generate"
echo "                                         # watcher builds + refreshes the site)"
echo "   make generate                         # optional one-shot site rebuild"
echo "=================================================================="
