#!/usr/bin/env bash
# ─── tls-edge verify.sh — end-to-end sanity checks ──────────────────────────
# Runs a series of checks against a deployed tls-edge stack.
# Usage: scripts/verify.sh [--domain <domain>] [--no-canary]
#                          [--ops-check <network> <container> <port>]
#                          [--help]
set -euo pipefail

# ─── helpers ──────────────────────────────────────────────────────────────────
PASS=0; FAIL=0; SKIP=0; WARN=0

_bold()  { printf '\033[1m%s\033[0m' "$*"; }
_green() { printf '\033[0;32m%s\033[0m' "$*"; }
_red()   { printf '\033[0;31m%s\033[0m' "$*"; }
_yel()   { printf '\033[0;33m%s\033[0m' "$*"; }
_cyan()  { printf '\033[0;36m%s\033[0m' "$*"; }

pass() { PASS=$(( PASS + 1 )); echo "  $(_green "[PASS]") $*"; }
fail() { FAIL=$(( FAIL + 1 )); echo "  $(_red  "[FAIL]") $*"; }
skip() { SKIP=$(( SKIP + 1 )); echo "  $(_cyan "[SKIP]") $*"; }
warn() { WARN=$(( WARN + 1 )); echo "  $(_yel  "[WARN]") $*"; }

# ─── arg parsing ──────────────────────────────────────────────────────────────
DOMAIN=""
NO_CANARY=false
OPS_CHECK=false
OPS_NET="" OPS_CONTAINER="" OPS_PORT=""

usage() {
    cat <<'EOF'
Usage: scripts/verify.sh [OPTIONS]

Options:
  --domain <d>                         Domain for TLS/redirect/canary checks
  --no-canary                          Skip canary container test
  --ops-check <network> <container> <port>
                                       Test ops-network isolation
  --help                               Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --domain)    DOMAIN="$2"; shift 2 ;;
        --no-canary) NO_CANARY=true; shift ;;
        --ops-check) OPS_CHECK=true; OPS_NET="$2"; OPS_CONTAINER="$3"; OPS_PORT="$4"; shift 4 ;;
        --help)      usage; exit 0 ;;
        *) echo "Unknown flag: $1" >&2; usage >&2; exit 2 ;;
    esac
done

# ─── repo root + config ────────────────────────────────────────────────────────
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
ROOT="$(readlink -f "$SCRIPT_DIR/..")"
STACK_DIR="$ROOT/ciu-stack"
EDGE_PROXY="$ROOT/edge-proxy"
OVERRIDE_FILE="$STACK_DIR/ciu.toml.j2"
DEFAULTS_FILE="$STACK_DIR/ciu.defaults.toml.j2"

# Prefer the override if it exists, else fall back to defaults
if [[ -f "$OVERRIDE_FILE" ]]; then
    CFG_FILE="$OVERRIDE_FILE"
else
    CFG_FILE="$DEFAULTS_FILE"
fi

# Read mode (default: acme-tls)
TLS_MODE="$(grep -m1 '^mode\s*=' "$CFG_FILE" 2>/dev/null | sed 's/.*=\s*"\(.*\)".*/\1/' || true)"
TLS_MODE="${TLS_MODE:-acme-tls}"

# Read expose_http (default: false)
EXPOSE_HTTP="$(grep -m1 '^expose_http\s*=' "$CFG_FILE" 2>/dev/null | sed 's/.*=\s*\(.*\)/\1/' | tr -d ' \r' || true)"
EXPOSE_HTTP="${EXPOSE_HTTP:-false}"

# Read domain if not given on CLI
if [[ -z "$DOMAIN" ]]; then
    # Try static.domains first entry
    _DOMAIN="$(grep -A5 '^\[.*static\]' "$CFG_FILE" 2>/dev/null \
        | grep -m1 '^domains\s*=' \
        | sed 's/.*\["\([^"]*\)".*/\1/' || true)"
    # Fall back to acme.dns.wildcard_main
    if [[ -z "$_DOMAIN" || "$_DOMAIN" == "[]" ]]; then
        _DOMAIN="$(grep -m1 '^wildcard_main\s*=' "$CFG_FILE" 2>/dev/null \
            | sed 's/.*=\s*"\(.*\)".*/\1/' || true)"
    fi
    if [[ -n "$_DOMAIN" && "$_DOMAIN" != "" ]]; then
        DOMAIN="$_DOMAIN"
    fi
fi

ACME_MODES="acme-tls acme-http acme-dns"
is_acme_mode() { echo "$ACME_MODES" | grep -qw "$TLS_MODE"; }

echo
echo "$(_bold "── tls-edge verify")"
echo "   root       : $ROOT"
echo "   config     : $CFG_FILE"
echo "   tls.mode   : $TLS_MODE"
echo "   expose_http: $EXPOSE_HTTP"
echo "   domain     : ${DOMAIN:-(none — domain-dependent checks will SKIP)}"
echo

# ─── check 1: render pipeline + drift ─────────────────────────────────────────
echo "$(_bold "1. Render pipeline + drift")"

if "$SCRIPT_DIR/render.sh" --check 2>&1 | sed 's/^/   /'; then
    pass "render.sh --check succeeded"
else
    fail "render.sh --check failed"
fi

TMPDIR_RENDER="$(mktemp -d)"
cleanup_tmp() { rm -rf "$TMPDIR_RENDER"; }
trap cleanup_tmp EXIT

DRIFT_HINT=""
DRIFT_RC=0
if [[ -f "$OVERRIDE_FILE" ]]; then
    # Local override exists — render with it (no --defaults-only) and diff
    python3 "$SCRIPT_DIR/render_standalone.py" --out "$TMPDIR_RENDER" || DRIFT_RC=$?
    DRIFT_HINT="scripts/render.sh"
else
    # No override — compare committed defaults
    python3 "$SCRIPT_DIR/render_standalone.py" --out "$TMPDIR_RENDER" --defaults-only || DRIFT_RC=$?
    DRIFT_HINT="scripts/update-rendered.sh"
fi
if [[ $DRIFT_RC -ne 0 ]]; then
    fail "drift check: renderer exited $DRIFT_RC"
fi

for RFILE in docker-compose.yml traefik.yml conf.d/certs.yml conf.d/options.yml conf.d/middlewares.yml; do
    RENDERED="$TMPDIR_RENDER/$RFILE"
    COMMITTED="$EDGE_PROXY/$RFILE"
    if [[ ! -f "$RENDERED" ]]; then
        [[ $DRIFT_RC -eq 0 ]] && fail "drift check: rendered $RFILE not produced"
        continue
    fi
    if [[ ! -f "$COMMITTED" ]]; then
        fail "drift check: $RFILE missing from edge-proxy/ (run $DRIFT_HINT)"
        continue
    fi
    if diff -I '^# rendered:' -q "$RENDERED" "$COMMITTED" >/dev/null 2>&1; then
        pass "no drift: $RFILE"
    else
        fail "drift detected in $RFILE — run $DRIFT_HINT"
        diff -I '^# rendered:' "$RENDERED" "$COMMITTED" 2>/dev/null | head -20 | sed 's/^/     /' || true
    fi
done

# ─── check 2: compose config validation ───────────────────────────────────────
echo
echo "$(_bold "2. docker compose config")"

_ENV_CREATED=false
if [[ ! -f "$EDGE_PROXY/.env" ]] && grep -q 'env_file' "$EDGE_PROXY/docker-compose.yml" 2>/dev/null; then
    touch "$EDGE_PROXY/.env"
    _ENV_CREATED=true
fi

if docker compose -f "$EDGE_PROXY/docker-compose.yml" config -q 2>&1 | sed 's/^/   /'; then
    pass "docker compose config -q succeeded"
else
    fail "docker compose config -q failed"
fi

if [[ "$_ENV_CREATED" == true ]]; then
    rm -f "$EDGE_PROXY/.env"
fi

# ─── check if containers are running ──────────────────────────────────────────
CONTAINERS_RUNNING=true
for _CTR in edge-traefik edge-dockerproxy; do
    _STATE="$(docker inspect -f '{{.State.Status}}' "$_CTR" 2>/dev/null || true)"
    if [[ -z "$_STATE" || "$_STATE" != "running" ]]; then
        CONTAINERS_RUNNING=false
        break
    fi
done

if [[ "$CONTAINERS_RUNNING" == false ]]; then
    echo
    echo "$(_bold "3–11. Container checks")"
    skip "containers not running — skipping checks 3–11 (hint: docker compose up -d)"
    echo
    # Jump to summary
else

# ─── check 3: container health ────────────────────────────────────────────────
echo
echo "$(_bold "3. Container health")"

for _CTR in edge-traefik edge-dockerproxy; do
    _HEALTH="$(docker inspect -f '{{.State.Health.Status}}' "$_CTR" 2>/dev/null || true)"
    if [[ "$_HEALTH" == "healthy" ]]; then
        pass "$_CTR is healthy"
    else
        fail "$_CTR health status: ${_HEALTH:-(unknown)}"
    fi
done

# ─── check 4: networks ────────────────────────────────────────────────────────
echo
echo "$(_bold "4. Networks")"

if docker network inspect ingress_public >/dev/null 2>&1; then
    pass "network ingress_public exists"
else
    fail "network ingress_public does not exist"
fi

_INTERNAL_FLAG="$(docker network inspect -f '{{.Internal}}' traefik_internal 2>/dev/null || true)"
if [[ "$_INTERNAL_FLAG" == "true" ]]; then
    pass "network traefik_internal exists and is internal"
else
    fail "network traefik_internal missing or not internal (got: ${_INTERNAL_FLAG:-(absent)})"
fi

# ─── check 5: socket-proxy policy ─────────────────────────────────────────────
echo
echo "$(_bold "5. Socket-proxy policy")"

_PROXY_RESULT="$(docker run --rm --network traefik_internal alpine:3.20 sh -c '
    if wget -qO- --timeout=3 http://edge-dockerproxy:2375/version >/dev/null 2>&1; then
        GET_OK=1
    else
        GET_OK=0
    fi
    if wget -qO- --timeout=3 --post-data=x http://edge-dockerproxy:2375/containers/create >/dev/null 2>&1; then
        POST_OK=1
    else
        POST_OK=0
    fi
    echo "GET=$GET_OK POST=$POST_OK"
' 2>/dev/null || true)"

_GET_OK="$(echo "$_PROXY_RESULT" | grep -o 'GET=[01]' | cut -d= -f2 || true)"
_POST_OK="$(echo "$_PROXY_RESULT" | grep -o 'POST=[01]' | cut -d= -f2 || true)"

if [[ "$_GET_OK" == "1" && "$_POST_OK" == "0" ]]; then
    pass "socket proxy: GET /version allowed, POST /containers/create rejected"
elif [[ "$_GET_OK" == "1" && "$_POST_OK" == "1" ]]; then
    fail "socket proxy: POST /containers/create was NOT rejected (POST: 0 not enforced)"
elif [[ "$_GET_OK" == "0" ]]; then
    fail "socket proxy: GET /version failed (proxy unreachable or broken)"
else
    fail "socket proxy: unexpected result — GET=$_GET_OK POST=$_POST_OK"
fi

# ─── check 6: HTTP redirect (only when expose_http=true) ──────────────────────
echo
echo "$(_bold "6. HTTP→HTTPS redirect")"

if [[ "$EXPOSE_HTTP" != "true" ]]; then
    skip "expose_http=false — no redirect entrypoint (skipping)"
elif [[ -z "$DOMAIN" ]]; then
    skip "no domain configured — skipping redirect check"
else
    # Probe from a one-shot container on ingress_public against the edge's
    # CONTAINER port — works on a real host and under DooD, and is independent
    # of the published host port mapping.
    _HTTP_CODE="$(docker run --rm --network ingress_public curlimages/curl:8.10.1 \
        -s -o /dev/null -w '%{http_code}' -H "Host: $DOMAIN" http://edge-traefik:80/ 2>/dev/null || true)"
    if [[ "$_HTTP_CODE" == "301" || "$_HTTP_CODE" == "308" ]]; then
        pass "HTTP redirect: got ${_HTTP_CODE} for Host: $DOMAIN"
    else
        fail "HTTP redirect: expected 301 or 308, got ${_HTTP_CODE:-(no response)}"
    fi
fi

# ─── check 7: TLS certificate ─────────────────────────────────────────────────
echo
echo "$(_bold "7. TLS certificate")"

if [[ -z "$DOMAIN" ]]; then
    skip "no domain — skipping TLS check"
else
    # One-shot openssl container on ingress_public → edge container port 443.
    _TLS_OUT="$(docker run --rm --entrypoint sh --network ingress_public alpine/openssl -c \
        "openssl s_client -connect edge-traefik:443 -servername $DOMAIN </dev/null 2>/dev/null \
         | openssl x509 -noout -subject -issuer" 2>/dev/null || true)"
    if echo "$_TLS_OUT" | grep -qi "TRAEFIK DEFAULT CERT"; then
        fail "TLS: Traefik is serving its self-signed default cert (real cert not loaded)"
        echo "$_TLS_OUT" | sed 's/^/     /'
    elif [[ -n "$_TLS_OUT" ]]; then
        pass "TLS cert loaded for $DOMAIN"
        echo "$_TLS_OUT" | sed 's/^/     /'
    else
        fail "TLS: no certificate response from edge-traefik:443 (servername $DOMAIN)"
    fi
fi

# ─── check 8: mode-specific cert verification ─────────────────────────────────
echo
echo "$(_bold "8. Mode-specific certificate check ($TLS_MODE)")"

if is_acme_mode; then
    # acme-data volume must have a non-empty acme.json
    _ACME_CHECK="$(docker run --rm \
        -v edge-proxy_acme-data:/a:ro \
        alpine:3.20 sh -c 'test -s /a/acme.json && echo "ok" || echo "missing"' 2>/dev/null || true)"
    if [[ "$_ACME_CHECK" == "ok" ]]; then
        pass "acme.json exists and is non-empty"
    else
        fail "acme.json missing or empty in acme-data volume (certificate not yet issued?)"
    fi

    if [[ -n "$DOMAIN" ]]; then
        _DOMAIN_COUNT="$(docker run --rm \
            -v edge-proxy_acme-data:/a:ro \
            alpine:3.20 sh -c "grep -ci '$DOMAIN' /a/acme.json 2>/dev/null || echo 0" 2>/dev/null || echo 0)"
        if [[ "${_DOMAIN_COUNT:-0}" -eq 0 ]]; then
            warn "acme.json does not mention $DOMAIN (issuance pending or domain mismatch)"
        else
            pass "acme.json references domain $DOMAIN ($_DOMAIN_COUNT occurrence(s))"
        fi
    fi

    # Check traefik logs for ACME errors
    _ACME_ERRORS="$(docker logs edge-traefik 2>&1 \
        | grep -iE 'acme.*(error|unable|failed)' || true)"
    if [[ -n "$_ACME_ERRORS" ]]; then
        fail "ACME errors found in traefik logs:"
        echo "$_ACME_ERRORS" | head -5 | sed 's/^/     /'
    else
        pass "no ACME errors in traefik logs"
    fi

elif [[ "$TLS_MODE" == "static" || "$TLS_MODE" == "dev" ]]; then
    if [[ -z "$DOMAIN" ]]; then
        skip "no domain — skipping static/dev cert file check"
    else
        # Try docker exec first; fall back to --volumes-from for shell-less images
        _CERT_CMD="docker exec edge-traefik cat /certs/live/$DOMAIN/fullchain.pem"
        if $_CERT_CMD >/dev/null 2>&1; then
            pass "static cert /certs/live/$DOMAIN/fullchain.pem accessible via exec"
        else
            # Fallback: shell-less variant — use alpine with volumes-from
            if docker run --rm --volumes-from edge-traefik alpine:3.20 \
                cat "/certs/live/$DOMAIN/fullchain.pem" >/dev/null 2>&1; then
                pass "static cert /certs/live/$DOMAIN/fullchain.pem accessible (via volumes-from fallback)"
            else
                fail "static cert /certs/live/$DOMAIN/fullchain.pem not readable (symlink-mount regression?)"
            fi
        fi
    fi
else
    skip "unknown mode $TLS_MODE — skipping mode-specific check"
fi

# ─── check 9: canary container ────────────────────────────────────────────────
echo
echo "$(_bold "9. Canary container")"

_CANARY_NAME="tlsedge-verify-canary"
_canary_cleanup() {
    docker rm -f "$_CANARY_NAME" >/dev/null 2>&1 || true
}

if [[ "$NO_CANARY" == true ]]; then
    skip "canary skipped (--no-canary)"
elif [[ -z "$DOMAIN" ]]; then
    skip "no domain — skipping canary"
else
    trap '_canary_cleanup; cleanup_tmp' EXIT

    docker run -d --rm \
        --name "$_CANARY_NAME" \
        --network ingress_public \
        -l traefik.enable=true \
        -l "traefik.http.routers.verify-canary.rule=Host(\`verify.$DOMAIN\`)" \
        -l "traefik.http.routers.verify-canary.entrypoints=websecure" \
        -l "traefik.http.services.verify-canary.loadbalancer.server.port=80" \
        traefik/whoami:v1.11 >/dev/null 2>&1

    sleep 5

    # --connect-to routes the canary hostname to the edge container directly
    # (works on host and under DooD, independent of published ports).
    _CANARY_RESP="$(docker run --rm --network ingress_public curlimages/curl:8.10.1 \
        -ksS --connect-to "verify.$DOMAIN:443:edge-traefik:443" \
        "https://verify.$DOMAIN/" 2>/dev/null || true)"

    _canary_cleanup

    if echo "$_CANARY_RESP" | grep -q "Hostname:"; then
        pass "canary: Traefik routed to whoami via verify.$DOMAIN"
    else
        fail "canary: response did not contain 'Hostname:' (routing broken?)"
        echo "$_CANARY_RESP" | head -5 | sed 's/^/     /'
    fi
fi

# ─── check 10: access log JSON ────────────────────────────────────────────────
echo
echo "$(_bold "10. Access log JSON format")"

_JSON_LOG="$(docker logs --tail 20 edge-traefik 2>/dev/null || true)"
_HAS_JSON_ROUTERNAME="$(echo "$_JSON_LOG" | python3 -c '
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        obj = json.loads(line)
        if "RouterName" in obj:
            print("yes")
            sys.exit(0)
    except Exception:
        pass
print("no")
' 2>/dev/null || true)"

if [[ "$_HAS_JSON_ROUTERNAME" == "yes" ]]; then
    pass "access log: found JSON entries with RouterName key"
else
    warn "access log: no JSON lines with RouterName found in last 20 log lines (no traffic yet, or format changed)"
fi

# ─── check 11: ops-check ──────────────────────────────────────────────────────
echo
echo "$(_bold "11. Ops endpoint isolation")"

if [[ "$OPS_CHECK" == false ]]; then
    skip "no --ops-check specified"
else
    _OPS_RESULT="$(docker run --rm \
        --network "$OPS_NET" \
        alpine:3.20 \
        wget -qO- -T 3 "http://$OPS_CONTAINER:$OPS_PORT/metrics" 2>/dev/null || true)"
    if [[ -n "$_OPS_RESULT" ]]; then
        fail "ops isolation: http://$OPS_CONTAINER:$OPS_PORT/metrics reachable from $OPS_NET (endpoint NOT isolated)"
    else
        pass "ops isolation: http://$OPS_CONTAINER:$OPS_PORT/metrics unreachable from $OPS_NET (isolation working)"
    fi
fi

fi  # end CONTAINERS_RUNNING block

# ─── summary ──────────────────────────────────────────────────────────────────
echo
echo "─────────────────────────────────────────────────────────────────────────────"
printf "  Results: %s  %s  %s  %s\n" \
    "$(_green "PASS:$PASS")" "$(_red "FAIL:$FAIL")" \
    "$(_cyan "SKIP:$SKIP")" "$(_yel "WARN:$WARN")"
echo "─────────────────────────────────────────────────────────────────────────────"
echo

if [[ "$FAIL" -gt 0 ]]; then
    exit 1
fi
