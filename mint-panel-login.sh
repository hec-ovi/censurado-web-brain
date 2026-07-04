#!/usr/bin/env bash
# Mint a fresh login for the operator panel served by publish (127.0.0.1:8082).
# Rotates PANEL_LOGIN_TOKEN + PANEL_LOGIN_TOKEN_HASH + PANEL_SESSION_KEY in .env.
# Local dev only. Pass a token as the first arg to set a specific one; without an
# arg, a fresh random token is minted.
set -euo pipefail
cd "$(dirname "$0")"

python3 - "$@" <<'PY'
import hashlib
import re
import secrets
import sys

# 32 random bytes, URL-safe: a 43-char login token unless explicitly provided.
token = sys.argv[1] if len(sys.argv) > 1 else secrets.token_urlsafe(32)
token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
session_key = secrets.token_hex(32)

p = ".env"
try:
    s = open(p).read()
except FileNotFoundError:
    s = ""


def setk(s, k, v):
    if re.search(rf'^{k}=.*$', s, flags=re.M):
        return re.sub(rf'^{k}=.*$', f'{k}={v}', s, flags=re.M)
    return s + (f'\n{k}={v}\n')


s = setk(s, "PANEL_LOGIN_TOKEN_HASH", token_hash)
s = setk(s, "PANEL_LOGIN_TOKEN", token)
s = setk(s, "PANEL_SESSION_KEY", session_key)
open(p, "w").write(s)

print("[.env] panel login token + hash + session key rotated")
print()
print("# Server env (already written to .env):")
print("PANEL_LOGIN_TOKEN=" + token)
print("PANEL_LOGIN_TOKEN_HASH=" + token_hash)
print("PANEL_SESSION_KEY=" + session_key)
print()
print("==================================================================")
print(" Panel login token (prefilled locally at the login page):")
print()
print("    " + token)
print()
print("==================================================================")
PY
