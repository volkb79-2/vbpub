#!/usr/bin/env bash
# ─── scripts/gen-guard-secret.sh — generate a basicAuth user:hash entry ───────
#
# Usage: gen-guard-secret.sh <username> <password>
#
# Outputs a single "user:hash" line suitable for use in a Traefik basicAuth
# middleware label.  Uses bcrypt (htpasswd -nbB) if htpasswd is available;
# falls back to APR1-MD5 (openssl passwd -apr1) otherwise.
#
# Bcrypt is strongly preferred.  APR1-MD5 is weak by modern standards but is
# universally available and still supported by Traefik.
#
# After running this script, copy the output into:
#   a) .env  →  GUARD_HASH=<output>            (single $; Compose handles escaping)
#   b) docker-compose.yml label value  →  escape every $ as $$  (see note below)
#
# Dollar-sign escaping note:
#   htpasswd and openssl emit hashes with literal $ characters, e.g.:
#     user:$2y$12$xyz...
#   In a docker-compose.yml label value, $ is a variable prefix that Compose
#   expands.  When pasting directly into a label, double every $:
#     user:$$2y$$12$$xyz...
#   When using a .env variable (GUARD_HASH), write single $ in the .env value
#   and reference it as ${GUARD_HASH} in the label — Compose handles the rest.

set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "Usage: $(basename "$0") <username> <password>" >&2
    exit 1
fi

USERNAME="$1"
PASSWORD="$2"

# ─── Generate hash ────────────────────────────────────────────────────────────
if command -v htpasswd &>/dev/null; then
    ENTRY="$(htpasswd -nbB "$USERNAME" "$PASSWORD")"
    METHOD="bcrypt (htpasswd)"
elif command -v openssl &>/dev/null; then
    HASH="$(openssl passwd -apr1 "$PASSWORD")"
    ENTRY="${USERNAME}:${HASH}"
    METHOD="APR1-MD5 (openssl) — consider installing apache2-utils for bcrypt"
else
    echo "error: neither htpasswd nor openssl is available" >&2
    echo "  Install htpasswd:  apt install apache2-utils  /  brew install httpd" >&2
    exit 1
fi

# ─── Output ───────────────────────────────────────────────────────────────────
echo "──────────────────────────────────────────────────────────────────────────────"
echo "  Method: $METHOD"
echo "──────────────────────────────────────────────────────────────────────────────"
echo
echo "  user:hash entry:"
echo "  $ENTRY"
echo
echo "  Usage:"
echo
echo "  A) Via .env (recommended — no manual \$-escaping needed):"
echo "       echo 'GUARD_HASH=$ENTRY' >> .env"
echo "     In docker-compose.yml label:"
echo "       - traefik.http.middlewares.<name>-guard.basicauth.users=\${GUARD_HASH}"
echo
# Show the $$-escaped version for direct-in-label use
ESCAPED="${ENTRY//\$/\$\$}"
echo "  B) Directly in docker-compose.yml label (every \$ must be written as \$\$):"
echo "       - traefik.http.middlewares.<name>-guard.basicauth.users=$ESCAPED"
echo
echo "──────────────────────────────────────────────────────────────────────────────"
