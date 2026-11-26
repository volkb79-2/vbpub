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
COMPOSE_FILE="$STACK_DIR/ciu.compose.yml"

# Container names. Plain-compose deployments use edge-traefik / edge-dockerproxy;
# a ciu instance (get.py install) names them <project>-<instance-id>-<service>.
# Override with EDGE_* variables, or discover container names from compose labels.
_discover() {  # $1 = compose service name
    docker ps --filter "label=com.docker.compose.service=$1" \
        --filter "name=edge-$1" --format '{{.Names}}' 2>/dev/null | sed -n '1p'
    docker ps --filter "label=com.docker.compose.service=$1" \
        --filter "name=tls-edge-.*-$1" --format '{{.Names}}' 2>/dev/null | sed -n '1p'
}
EDGE_TRAEFIK="${EDGE_TRAEFIK:-$(_discover traefik | sed -n '1p')}"
EDGE_DOCKERPROXY="${EDGE_DOCKERPROXY:-$(_discover dockerproxy | sed -n '1p')}"
EDGE_TRAEFIK="${EDGE_TRAEFIK:-edge-traefik}"
EDGE_DOCKERPROXY="${EDGE_DOCKERPROXY:-edge-dockerproxy}"
_acme_storage_mount_at_var_lib() {
    docker inspect -f '{{range .Mounts}}{{if eq .Destination "/var/lib/traefik"}}{{if eq .Type "volume"}}{{.Name}}{{else if eq .Type "bind"}}{{.Source}}{{end}}{{end}}{{end}}' \
        "$EDGE_TRAEFIK" 2>/dev/null || true
}
_acme_storage_mount_at_etc() {
    # Accept the former target for an already-running instance during upgrade.
    docker inspect -f '{{range .Mounts}}{{if eq .Destination "/etc/traefik/acme"}}{{if eq .Type "volume"}}{{.Name}}{{else if eq .Type "bind"}}{{.Source}}{{end}}{{end}}{{end}}' \
        "$EDGE_TRAEFIK" 2>/dev/null || true
}
EDGE_ACME_VOLUME="${EDGE_ACME_VOLUME:-}"
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
    _STATIC_DOMAIN_LINE="$(grep -A5 '^\[.*static\]' "$CFG_FILE" 2>/dev/null \
        | grep -m1 '^domains\s*=' || true)"
    _DOMAIN="$(printf '%s\n' "$_STATIC_DOMAIN_LINE" \
        | sed -n 's/^[[:space:]]*domains[[:space:]]*=[[:space:]]*\["\([^"]*\)".*/\1/p')"
    # Fall back to acme.dns.wildcard_main only when it has a quoted value.
    if [[ -z "$_DOMAIN" ]]; then
        _DOMAIN="$(grep -m1 '^wildcard_main\s*=' "$CFG_FILE" 2>/dev/null \
            | sed -n 's/^[[:space:]]*wildcard_main[[:space:]]*=[[:space:]]*"\([^"]\{1,\}\)".*/\1/p' || true)"
    fi
    if [[ -n "$_DOMAIN" ]]; then
        DOMAIN="$_DOMAIN"
    fi
fi

ACME_MODES="acme-tls acme-http acme-dns"
is_acme_mode() { echo "$ACME_MODES" | grep -qw "$TLS_MODE"; }
if is_acme_mode && [[ -z "$EDGE_ACME_VOLUME" ]]; then
    EDGE_ACME_VOLUME="$(_acme_storage_mount_at_var_lib)"
    EDGE_ACME_VOLUME="${EDGE_ACME_VOLUME:-$(_acme_storage_mount_at_etc)}"
fi

echo
echo "$(_bold "── tls-edge verify")"
echo "   root       : $ROOT"
echo "   config     : $CFG_FILE"
echo "   tls.mode   : $TLS_MODE"
echo "   expose_http: $EXPOSE_HTTP"
echo "   domain     : ${DOMAIN:-(none — domain-dependent checks will SKIP)}"
echo

# ─── check 1: validated ciu configuration ────────────────────────────────────
echo "$(_bold "1. ciu configuration")"

if [[ -x "$ROOT/ciu" ]]; then
    if "$ROOT/ciu" check 2>&1 | sed 's/^/   /'; then
        pass "ciu check succeeded"
    else
        fail "ciu check failed"
    fi
else
    skip "bundled ciu launcher is missing; run this from the installed stack root"
fi

# ─── check 2: compose config validation ───────────────────────────────────────
echo
echo "$(_bold "2. docker compose config")"

COMPOSE_TO_CHECK="$COMPOSE_FILE"
COMPOSE_KIND="rendered CIU compose"
if [[ ! -f "$COMPOSE_TO_CHECK" ]]; then
    fail "$COMPOSE_KIND file is missing: $COMPOSE_TO_CHECK"
elif docker compose -f "$COMPOSE_TO_CHECK" config -q 2>&1 | sed 's/^/   /'; then
    pass "docker compose config -q succeeded"
else
    fail "docker compose config -q failed"
fi

# ─── check if containers are running ──────────────────────────────────────────
CONTAINERS_RUNNING=true
for _CTR in ${EDGE_TRAEFIK} ${EDGE_DOCKERPROXY}; do
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

for _CTR in ${EDGE_TRAEFIK} ${EDGE_DOCKERPROXY}; do
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

_PROXY_RESULT="$(docker run --rm --network traefik_internal \
    -e PROXY_HOST="$EDGE_DOCKERPROXY" alpine:3.20 sh -c '
    if wget -qO- --timeout=3 "http://${PROXY_HOST}:2375/version" >/dev/null 2>&1; then
        GET_OK=1
    else
        GET_OK=0
    fi

    POST_OUTPUT="$(wget -qO- --timeout=3 --post-data=x \
        "http://${PROXY_HOST}:2375/containers/create" 2>&1)"
    POST_STATUS=$?
    if [ "$POST_STATUS" -ne 0 ] \
        && printf "%s\n" "$POST_OUTPUT" \
            | grep -Eq "server returned error: HTTP/[0-9.]+ 403([[:space:]]|$)"; then
        POST_403=1
    else
        POST_403=0
    fi

    printf "GET=%s POST_403=%s PROBE=complete\n" "$GET_OK" "$POST_403"
' 2>/dev/null || true)"

_GET_OK="$(echo "$_PROXY_RESULT" | grep -o 'GET=[01]' | cut -d= -f2 || true)"
_POST_403="$(echo "$_PROXY_RESULT" | grep -o 'POST_403=[01]' | cut -d= -f2 || true)"
_PROBE_COMPLETE="$(echo "$_PROXY_RESULT" | grep -c 'PROBE=complete' || true)"

if [[ "$_PROBE_COMPLETE" != "1" ]]; then
    fail "socket proxy: probe did not report a result"
elif [[ "$_GET_OK" == "1" && "$_POST_403" == "1" ]]; then
    pass "socket proxy: GET /version allowed, POST /containers/create rejected"
elif [[ "$_GET_OK" == "1" && "$_POST_403" == "0" ]]; then
    fail "socket proxy: POST /containers/create was NOT rejected (POST: 0 not enforced)"
elif [[ "$_GET_OK" == "0" ]]; then
    fail "socket proxy: GET /version failed (proxy unreachable or broken)"
else
    fail "socket proxy: unexpected result — GET=$_GET_OK POST_403=$_POST_403"
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
        -s -o /dev/null -w '%{http_code}' -H "Host: $DOMAIN" http://${EDGE_TRAEFIK}:80/ 2>/dev/null || true)"
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
    # Checks: a cert is served AND it is not the Traefik default self-signed cert.
    # Does NOT perform full chain validation (that requires the CA bundle
    # and an internet-routable TLS handshake; use 'openssl verify' manually if needed).
    _TLS_OUT="$(docker run --rm --entrypoint sh --network ingress_public alpine/openssl -c \
        "openssl s_client -connect ${EDGE_TRAEFIK}:443 -servername $DOMAIN </dev/null 2>/dev/null \
         | openssl x509 -noout -subject -issuer" 2>/dev/null || true)"
    if echo "$_TLS_OUT" | grep -qi "TRAEFIK DEFAULT CERT"; then
        fail "TLS: Traefik is serving its self-signed default cert (real cert not loaded)"
        echo "$_TLS_OUT" | sed 's/^/     /'
    elif [[ -n "$_TLS_OUT" ]]; then
        pass "TLS cert loaded for $DOMAIN (not the Traefik default self-signed)"
        echo "$_TLS_OUT" | sed 's/^/     /'
    else
        fail "TLS: no certificate response from ${EDGE_TRAEFIK}:443 (servername $DOMAIN)"
    fi
fi

# ─── check 8: mode-specific cert verification ─────────────────────────────────
echo
echo "$(_bold "8. Mode-specific certificate check ($TLS_MODE)")"

if is_acme_mode; then
    _ACME_STORAGE_READY=true
    _ACME_MOUNT_ARGS=()
    if [[ -z "$EDGE_ACME_VOLUME" ]]; then
        fail "ACME storage mount not found at /var/lib/traefik or /etc/traefik/acme; set EDGE_ACME_VOLUME to an existing volume name or absolute bind path"
        _ACME_STORAGE_READY=false
    elif [[ "$EDGE_ACME_VOLUME" == /* ]]; then
        # --mount refuses a missing bind source; unlike -v it never creates it.
        _ACME_MOUNT_ARGS=(--mount "type=bind,source=$EDGE_ACME_VOLUME,target=/a,readonly")
    elif docker volume inspect "$EDGE_ACME_VOLUME" >/dev/null 2>&1; then
        _ACME_MOUNT_ARGS=(--mount "type=volume,source=$EDGE_ACME_VOLUME,target=/a,readonly")
    else
        fail "ACME storage volume '$EDGE_ACME_VOLUME' does not exist (it was not created)"
        _ACME_STORAGE_READY=false
    fi

    if [[ "$_ACME_STORAGE_READY" == true ]]; then
        if _ACME_MOUNT_PROBE="$(docker run --rm "${_ACME_MOUNT_ARGS[@]}" \
            alpine:3.20 sh -c 'test -d /a' 2>&1)"; then
            _ACME_CHECK="$(docker run --rm "${_ACME_MOUNT_ARGS[@]}" \
                alpine:3.20 sh -c 'test -s /a/acme.json && echo "ok" || echo "missing"' 2>/dev/null || true)"
            if [[ "$_ACME_CHECK" == "ok" ]]; then
                pass "acme.json exists and is non-empty"
            else
                fail "acme.json missing or empty in ACME storage (certificate not yet issued?)"
            fi

            _ACME_PERMS="$(docker run --rm "${_ACME_MOUNT_ARGS[@]}" \
                alpine:3.20 sh -c 'stat -c "%a" /a/acme.json 2>/dev/null || echo "?"' 2>/dev/null || true)"
            if [[ "$_ACME_PERMS" == "600" ]]; then
                pass "acme.json permissions are 0600"
            else
                fail "acme.json permissions: expected 0600, got ${_ACME_PERMS:-(unknown)}"
            fi

            if [[ -n "$DOMAIN" ]]; then
                _DOMAIN_RESULT="$(docker run --rm "${_ACME_MOUNT_ARGS[@]}" \
                    -e "VERIFY_DOMAIN=$DOMAIN" \
                    alpine:3.20 sh -c '
                        if DOMAIN_COUNT="$(grep -Fci -- "$VERIFY_DOMAIN" /a/acme.json 2>/dev/null)"; then
                            echo "COUNT=$DOMAIN_COUNT"
                        else
                            GREP_STATUS=$?
                            if [ "$GREP_STATUS" -eq 1 ]; then
                                echo "COUNT=0"
                            else
                                echo "READ_ERROR"
                            fi
                        fi
                    ' 2>/dev/null || true)"
                if [[ "$_DOMAIN_RESULT" =~ ^COUNT=([0-9]+)$ ]]; then
                    _DOMAIN_COUNT="${BASH_REMATCH[1]}"
                    if [[ "$_DOMAIN_COUNT" == "0" ]]; then
                        warn "acme.json does not mention $DOMAIN (issuance pending or domain mismatch)"
                    else
                        pass "acme.json references domain $DOMAIN ($_DOMAIN_COUNT occurrence(s))"
                    fi
                else
                    fail "acme.json domain probe could not read the file (got: ${_DOMAIN_RESULT:-(no response)})"
                fi
            fi
        else
            _ACME_STORAGE_READY=false
            fail "ACME storage at '$EDGE_ACME_VOLUME' could not be mounted by Docker: ${_ACME_MOUNT_PROBE:-(no Docker diagnostic)}"
        fi
    fi

    # Check traefik logs for ACME errors
    if _ACME_LOGS="$(docker logs "$EDGE_TRAEFIK" 2>&1)"; then
        if _ACME_ERRORS="$(printf '%s\n' "$_ACME_LOGS" \
            | grep -iE 'acme.*(error|unable|failed)')"; then
            fail "ACME errors found in traefik logs:"
            printf '%s\n' "$_ACME_ERRORS" | sed -n '1,5s/^/     /p'
        else
            _ACME_GREP_STATUS=$?
            if [[ "$_ACME_GREP_STATUS" -eq 1 ]]; then
                pass "no ACME errors in traefik logs"
            else
                fail "could not scan traefik logs for ACME errors"
            fi
        fi
    else
        fail "could not read traefik logs for ACME errors"
    fi

elif [[ "$TLS_MODE" == "static" || "$TLS_MODE" == "dev" ]]; then
    if [[ -z "$DOMAIN" ]]; then
        skip "no domain — skipping static/dev cert file check"
    else
        # Try docker exec first; fall back to --volumes-from for shell-less images
        _CERT_CMD="docker exec ${EDGE_TRAEFIK} cat /certs/live/$DOMAIN/fullchain.pem"
        if $_CERT_CMD >/dev/null 2>&1; then
            pass "static cert /certs/live/$DOMAIN/fullchain.pem accessible via exec"
        else
            # Fallback: shell-less variant — use alpine with volumes-from
            if docker run --rm --volumes-from ${EDGE_TRAEFIK} alpine:3.20 \
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
    trap '_canary_cleanup' EXIT

    if docker run -d --rm \
        --name "$_CANARY_NAME" \
        --network ingress_public \
        -l traefik.enable=true \
        -l "traefik.http.routers.verify-canary.rule=Host(\`tls-edge-verify-canary.invalid\`)" \
        -l "traefik.http.routers.verify-canary.entrypoints=websecure" \
        -l "traefik.http.routers.verify-canary.tls=true" \
        -l "traefik.http.services.verify-canary.loadbalancer.server.port=80" \
        traefik/whoami:v1.11 >/dev/null 2>&1; then
        _CANARY_RESP=""
        _CANARY_OK=false
        for ((_CANARY_ATTEMPT = 1; _CANARY_ATTEMPT <= 30; _CANARY_ATTEMPT++)); do
            # The URL host supplies TLS SNI; Host selects the deliberately
            # non-issuable router. --connect-to reaches the edge container.
            _CANARY_RESP="$(docker run --rm --network ingress_public curlimages/curl:8.10.1 \
                -ksS --max-time 5 \
                --connect-to "$DOMAIN:443:${EDGE_TRAEFIK}:443" \
                -H "Host: tls-edge-verify-canary.invalid" \
                "https://$DOMAIN/" 2>&1 || true)"
            if printf '%s\n' "$_CANARY_RESP" | grep -q "Hostname:"; then
                _CANARY_OK=true
                break
            fi
            if [[ "$_CANARY_ATTEMPT" -lt 30 ]]; then
                sleep 1 || true
            fi
        done

        if [[ "$_CANARY_OK" == true ]]; then
            pass "canary: Traefik routed to whoami via tls-edge-verify-canary.invalid (SNI $DOMAIN)"
        else
            fail "canary: response did not contain 'Hostname:' after 30 probes; --domain must name a host for which the edge already serves a certificate"
            if [[ -n "$_CANARY_RESP" ]]; then
                _CANARY_LAST_LINE="$(printf '%s\n' "$_CANARY_RESP" | sed -n '$p')"
                printf '     last curl error/output: %s\n' "$_CANARY_LAST_LINE"
            fi
        fi
    else
        fail "canary: container could not be started"
    fi

    _canary_cleanup
fi

# ─── check 10: access log JSON ────────────────────────────────────────────────
echo
echo "$(_bold "10. Access log JSON format")"

if _JSON_LOG="$(docker logs --tail 20 "$EDGE_TRAEFIK" 2>/dev/null)"; then
    if _HAS_JSON_ROUTERNAME="$(printf '%s\n' "$_JSON_LOG" | python3 -c '
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
' 2>/dev/null)"; then
        if [[ "$_HAS_JSON_ROUTERNAME" == "yes" ]]; then
            pass "access log: found JSON entries with RouterName key"
        else
            warn "access log: no JSON lines with RouterName found in last 20 log lines (no traffic yet, or format changed)"
        fi
    else
        warn "access log: could not inspect JSON entries in Traefik logs"
    fi
else
    warn "access log: could not read Traefik logs"
fi

# ─── check 11: ops-check ──────────────────────────────────────────────────────
echo
echo "$(_bold "11. Ops endpoint isolation")"

if [[ "$OPS_CHECK" == false ]]; then
    skip "no --ops-check specified"
else
    _OPS_RESULT="$(docker run --rm \
        --network "$OPS_NET" \
        -e "OPS_URL=http://$OPS_CONTAINER:$OPS_PORT/metrics" \
        alpine:3.20 sh -c '
            if ! command -v wget >/dev/null 2>&1; then
                echo "OPS=inconclusive"
                echo "OPS_ERROR=wget is unavailable"
                exit 0
            fi
            WGET_OUTPUT="$(wget -S -qO- -T 3 "$OPS_URL" 2>&1)"
            WGET_STATUS=$?
            if [ "$WGET_STATUS" -eq 0 ] \
                || printf "%s\n" "$WGET_OUTPUT" \
                    | grep -Eqi "HTTP/[0-9.]+[[:space:]]+[0-9][0-9][0-9]|server returned error: HTTP/"; then
                echo "OPS=reachable"
            elif printf "%s\n" "$WGET_OUTPUT" \
                | grep -Eqi "can.t connect|bad address|download timed out"; then
                echo "OPS=unreachable"
            else
                echo "OPS=inconclusive"
                if [ -n "$WGET_OUTPUT" ]; then
                    printf "%s\n" "$WGET_OUTPUT" | sed -n "\$p" | sed "s/^/OPS_ERROR=/"
                else
                    echo "OPS_ERROR=(no wget diagnostic)"
                fi
            fi
        ' 2>&1 || true)"
    _OPS_CLASS="$(printf '%s\n' "$_OPS_RESULT" | sed -n 's/^OPS=//p' | sed -n '1p')"
    _OPS_ERROR="$(printf '%s\n' "$_OPS_RESULT" | sed -n 's/^OPS_ERROR=//p' | sed -n '1p')"
    if [[ "$_OPS_CLASS" == "reachable" ]]; then
        fail "ops isolation: http://$OPS_CONTAINER:$OPS_PORT/metrics reachable from $OPS_NET (endpoint NOT isolated)"
    elif [[ "$_OPS_CLASS" == "unreachable" ]]; then
        pass "ops isolation: http://$OPS_CONTAINER:$OPS_PORT/metrics unreachable from $OPS_NET (isolation working)"
    else
        if [[ -z "$_OPS_CLASS" ]]; then
            _OPS_ERROR="$(printf '%s\n' "$_OPS_RESULT" | sed -n '$p')"
            [[ -n "$_OPS_ERROR" ]] || _OPS_ERROR="no probe result marker"
        fi
        fail "ops isolation: probe inconclusive${_OPS_ERROR:+: $_OPS_ERROR}"
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
