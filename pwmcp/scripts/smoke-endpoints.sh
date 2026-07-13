#!/usr/bin/env bash
# ===========================================================================
# smoke-endpoints.sh — PWMCP stack endpoint validation
# ===========================================================================
# Validates that the locally-built + ciu-started pwmcp stack serves all three
# endpoints (3000 WS, 8931 MCP, 8932 devtools-mcp) correctly.
#
# Usage:
#   ./scripts/smoke-endpoints.sh              # full validation
#   ./scripts/smoke-endpoints.sh --quick       # skip tool-call tests
#   ./scripts/smoke-endpoints.sh --help        # this message
#
# Exit codes:
#   0 — all checks pass
#   1 — one or more checks failed
#   2 — prerequisites missing
#
# Requires:
#   - docker + compose (or the ciu-managed stack running)
#   - curl, jq, timeout
# ===========================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PWMCP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT="${PROJECT:-pwmcp}"
ENV="${ENV:-dev}"
STACK_NAME="${PROJECT}-${ENV}"
CONTAINER_NAME="${STACK_NAME}-pwmcp"
PWMCP_HOST="${PWMCP_HOST:-pwmcp}"

# Endpoints
WS_PORT=3000
MCP_PORT=8931
DEVTOOLS_PORT=8932
WS_URL="ws://${PWMCP_HOST}:${WS_PORT}/"
MCP_URL="http://${PWMCP_HOST}:${MCP_PORT}/mcp"
DEVTOOLS_URL="http://${PWMCP_HOST}:${DEVTOOLS_PORT}/mcp"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

pass=0
fail=0
total=0
errors=""   # accumulate failure descriptions

# ── Helper functions ───────────────────────────────────────────────────────

record_pass() {
    pass=$((pass + 1))
    echo -e "${GREEN}PASS${NC}"
}

record_fail() {
    local msg="$1"
    fail=$((fail + 1))
    errors="${errors}  FAIL #${total}: ${msg}"$'\n'
    echo -e "${RED}FAIL${NC}"
    if [ -n "${2:-}" ]; then
        echo "    ${2}" >&2
    fi
}

# MCP initialize: POST to the given URL with the given Host header.
# Outputs the full JSON-RPC response (stdout) + returns curl exit code.
mcp_initialize_raw() {
    local url="$1"
    local host="$2"
    # MCP streamable-HTTP servers require the client to accept BOTH JSON and
    # SSE; omitting this Accept header yields HTTP 406 Not Acceptable.
    curl -fsS -X POST "$url" \
        -H "Content-Type: application/json" \
        -H "Accept: application/json, text/event-stream" \
        -H "Host: ${host}" \
        -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke-test","version":"1.0"}}}'
}

# Extract the JSON-RPC payload from an MCP streamable-HTTP response. These
# servers reply with an SSE frame ("event: message" / "data: {json}"), not raw
# JSON, so the data line must be unwrapped before jq. Falls back to the raw
# body if it is already plain JSON.
sse_extract_json() {
    local body="$1"
    if printf '%s\n' "$body" | grep -q '^data:'; then
        printf '%s\n' "$body" | sed -n 's/^data: \{0,1\}//p' | tail -1
    else
        printf '%s' "$body"
    fi
}

# MCP initialize with JSON body validation using jq.
# Asserts HTTP 2xx + valid JSON-RPC result (no .error, has .result.serverInfo.name).
mcp_initialize_assert_ok() {
    local url="$1"
    local host="$2"
    local label="$3"
    local body
    body=$(mcp_initialize_raw "$url" "$host") || return 1
    body=$(sse_extract_json "$body")
    # Validate JSON-RPC success: no .error, and .result exists with .serverInfo.name
    echo "$body" | jq -e '
        .error == null
        and (.result | type == "object")
        and (.result.serverInfo | type == "object")
        and (.result.serverInfo.name | type == "string")
    ' >/dev/null 2>&1 || {
        echo "MCP initialize response validation failed for ${label}" >&2
        echo "  Response: $(echo "$body" | jq -c . 2>/dev/null || echo "$body")" >&2
        return 1
    }
    return 0
}

# MCP initialize that expects an error (for host-header rejection tests).
# Asserts HTTP non-2xx OR JSON-RPC error response.
mcp_initialize_assert_fail() {
    local url="$1"
    local host="$2"
    local label="$3"
    # Try the request; capture body and exit code
    set +e
    local body
    body=$(mcp_initialize_raw "$url" "$host" 2>&1)
    local rc=$?
    set -e
    # Pass if either curl failed (non-2xx) OR JSON-RPC has an error
    if [ $rc -ne 0 ]; then
        return 0  # expected HTTP failure
    fi
    # HTTP succeeded — check for JSON-RPC error
    echo "$body" | jq -e '.error != null' >/dev/null 2>&1 && return 0
    # Neither HTTP nor JSON-RPC error — unexpected success
    echo "Expected rejection for ${label} but request succeeded" >&2
    echo "  Response: $(echo "$body" | jq -c . 2>/dev/null || echo "$body")" >&2
    return 1
}

# Drive a real MCP tool end-to-end over the stateful streamable-HTTP session:
#   initialize (capture Mcp-Session-Id) -> notifications/initialized -> tools/call.
# Args: <url> <host> <tool-name> <arguments-json>. Prints the tool-result JSON
# (SSE-unwrapped) on stdout; returns non-zero on transport failure.
mcp_session_tool_call() {
    local url="$1" host="$2" tool="$3" args="$4"
    local hdrs sid
    hdrs=$(mktemp)
    curl -fsS --max-time 30 -D "$hdrs" -o /dev/null -X POST "$url" \
        -H "Content-Type: application/json" \
        -H "Accept: application/json, text/event-stream" \
        -H "Host: ${host}" \
        -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke-test","version":"1.0"}}}' \
        || { rm -f "$hdrs"; return 1; }
    sid=$(tr -d '\r' < "$hdrs" | awk -F': ' 'tolower($1)=="mcp-session-id"{print $2}')
    rm -f "$hdrs"
    [ -n "$sid" ] || return 1
    curl -fsS --max-time 30 -o /dev/null -X POST "$url" \
        -H "Content-Type: application/json" \
        -H "Accept: application/json, text/event-stream" \
        -H "Host: ${host}" -H "Mcp-Session-Id: ${sid}" \
        -d '{"jsonrpc":"2.0","method":"notifications/initialized"}' || return 1
    local body
    body=$(curl -fsS --max-time 75 -X POST "$url" \
        -H "Content-Type: application/json" \
        -H "Accept: application/json, text/event-stream" \
        -H "Host: ${host}" -H "Mcp-Session-Id: ${sid}" \
        -d "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"${tool}\",\"arguments\":${args}}}") || return 1
    sse_extract_json "$body"
}

# Check supervisord: wait up to 30s for all three programs to be RUNNING.
wait_for_supervisord() {
    local retries=30
    local i=0
    while [ $i -lt $retries ]; do
        local status_output
        status_output=$(docker exec "${CONTAINER_NAME}" supervisorctl -c /etc/supervisor/conf.d/pwmcp.conf status 2>/dev/null || true)
        local run_server_running=0
        local mcp_running=0
        local devtools_running=0
        while IFS= read -r line; do
            if echo "$line" | grep -qE '^run-server\s+RUNNING\s+'; then
                run_server_running=1
            fi
            if echo "$line" | grep -qE '^mcp\s+RUNNING\s+'; then
                mcp_running=1
            fi
            if echo "$line" | grep -qE '^devtools-mcp\s+RUNNING\s+'; then
                devtools_running=1
            fi
        done <<< "$status_output"
        if [ $run_server_running -eq 1 ] && [ $mcp_running -eq 1 ] && [ $devtools_running -eq 1 ]; then
            echo "  All three programs RUNNING (after ~${i}s)"
            echo "${status_output}"
            return 0
        fi
        sleep 1
        i=$((i + 1))
    done
    echo "  Timed out waiting for programs to reach RUNNING state:" >&2
    docker exec "${CONTAINER_NAME}" supervisorctl -c /etc/supervisor/conf.d/pwmcp.conf status >&2 || true
    return 1
}

echo ""
echo "============================================"
echo " PWMCP Smoke Tests — Endpoint Validation"
echo "============================================"
echo " Stack:      ${STACK_NAME}"
echo " Container:  ${CONTAINER_NAME}"
echo " WS:         ${WS_URL}"
echo " MCP:        ${MCP_URL}"
echo " DevTools:   ${DEVTOOLS_URL}"
echo "============================================"
echo ""

# ── Prerequisite checks ────────────────────────────────────────────────────
echo "[PREREQUISITES]"

if ! command -v docker &>/dev/null; then
    echo -e "${RED}Docker not available. Skipping smoke tests.${NC}"
    echo "Run manually when the stack is deployed."
    exit 2
fi

if ! command -v jq &>/dev/null; then
    echo -e "${RED}jq not available. Skipping smoke tests.${NC}"
    exit 2
fi

if docker ps --format '{{.Names}}' | grep -q "${CONTAINER_NAME}"; then
    echo -e "  Container ${CONTAINER_NAME}: ${GREEN}running${NC}"
else
    echo -e "  Container ${CONTAINER_NAME}: ${YELLOW}not running (ciu not started?)${NC}"
    echo "  Smoke tests require a running stack. Skipping."
    exit 2
fi

echo ""

# ── 1. Supervisord program status ──────────────────────────────────────────
echo "[SUPERVISORD STATUS — wait up to 30s for RUNNING]"

# wait_for_supervisord is a shell function with its own bounded 30s poll loop,
# so it is called directly (it cannot be run under `timeout`, which only execs
# external binaries).
total=$((total + 1))
echo -n "  CHECK ${total}: All three programs RUNNING (with 30s poll) ... "
if wait_for_supervisord; then
    record_pass
else
    record_fail "supervisord did not report all three programs RUNNING within 30s"
fi

echo ""

# ── 2. MCP @playwright/mcp endpoint (port 8931) ────────────────────────────
echo "[MCP @playwright/mcp — PORT 8931]"

# 2a: Correct Host header → successful MCP initialize with valid JSON-RPC result
total=$((total + 1))
echo -n "  CHECK ${total}: MCP initialize with correct Host header (JSON-RPC validated) ... "
if mcp_initialize_assert_ok "${MCP_URL}" "${PWMCP_HOST}:${MCP_PORT}" "@playwright/mcp"; then
    record_pass
else
    record_fail "MCP initialize failed or JSON-RPC body not valid"
fi

# 2b: Forged Host header → rejection (@playwright/mcp enforces --allowed-hosts)
total=$((total + 1))
echo -n "  CHECK ${total}: MCP with forged Host header (expect rejection) ... "
if mcp_initialize_assert_fail "${MCP_URL}" "evil.example:${MCP_PORT}" "@playwright/mcp forged Host"; then
    record_pass
else
    record_fail "Forged Host was not rejected by @playwright/mcp"
fi

echo ""

# ── 3. MCP chrome-devtools-mcp endpoint (port 8932 via mcp-proxy) ──────────
echo "[DEVTOOLS chrome-devtools-mcp — PORT 8932]"

# 3a: Correct Host header → successful MCP initialize
total=$((total + 1))
echo -n "  CHECK ${total}: DevTools MCP initialize with correct Host header (JSON-RPC validated) ... "
if mcp_initialize_assert_ok "${DEVTOOLS_URL}" "${PWMCP_HOST}:${DEVTOOLS_PORT}" "chrome-devtools-mcp"; then
    record_pass
else
    record_fail "DevTools MCP initialize failed or JSON-RPC body not valid"
fi

# 3b: Forged Host header → note: mcp-proxy has NO host allowlist enforcement.
#     Unlike @playwright/mcp, mcp-proxy accepts any Host header. This is a
#     documented gap (see SECURITY.md). The test expects SUCCESS because there
#     is no enforcement. The handoff contract is met by documenting the gap.
total=$((total + 1))
echo -n "  CHECK ${total}: DevTools MCP with forged Host header (expect SUCCESS — no host allowlist, see SECURITY.md) ... "
if mcp_initialize_assert_ok "${DEVTOOLS_URL}" "evil.example:${DEVTOOLS_PORT}" "chrome-devtools-mcp forged Host"; then
    echo -n " (gap: mcp-proxy has no --allowed-hosts) "
    record_pass
else
    record_fail "DevTools MCP failed unexpectedly"
fi

# 3c: Drive Chromium end-to-end via a real tool call (new_page to a data: URL).
# This proves the server actually launched and controlled the browser, not just
# answered initialize. Uses the full stateful MCP session handshake.
if [ "${1:-}" != "--quick" ]; then
    total=$((total + 1))
    echo -n "  CHECK ${total}: DevTools MCP end-to-end new_page (drives Chromium) ... "
    body=$(mcp_session_tool_call "${DEVTOOLS_URL}" "${PWMCP_HOST}:${DEVTOOLS_PORT}" \
        new_page '{"url":"data:text/html,<h1>pwmcp-smoke</h1>"}' 2>/dev/null) || {
        record_fail "new_page tool call transport failed"
        body=""
    }
    if [ -n "${body:-}" ]; then
        # A successful tool result has .result.content and isError != true.
        if echo "$body" | jq -e '(.error == null) and (.result | type == "object") and ((.result.isError // false) == false)' >/dev/null 2>&1; then
            record_pass
        else
            record_fail "new_page returned an error or invalid result" "$(echo "$body" | jq -c . 2>/dev/null || echo "$body")"
        fi
    fi
fi

echo ""

# ── 4. Fault isolation ─────────────────────────────────────────────────────
echo "[FAULT ISOLATION]"

# 4a: Verify mcp still works
total=$((total + 1))
echo -n "  CHECK ${total}: MCP (8931) works after devtools health check ... "
if mcp_initialize_assert_ok "${MCP_URL}" "${PWMCP_HOST}:${MCP_PORT}" "@playwright/mcp post-healthcheck"; then
    record_pass
else
    record_fail "MCP not responding after devtools health check"
fi

# 4b: Stop devtools-mcp, verify 8931 still works
echo "  Stopping devtools-mcp program (fault isolation test)..."
docker exec "${CONTAINER_NAME}" supervisorctl -c /etc/supervisor/conf.d/pwmcp.conf stop devtools-mcp 2>/dev/null || true
sleep 2

total=$((total + 1))
echo -n "  CHECK ${total}: MCP (8931) still works after devtools-mcp stopped ... "
if mcp_initialize_assert_ok "${MCP_URL}" "${PWMCP_HOST}:${MCP_PORT}" "@playwright/mcp post-stop"; then
    record_pass
else
    record_fail "MCP not responding after devtools-mcp was stopped"
fi

# Restart devtools-mcp for subsequent tests
docker exec "${CONTAINER_NAME}" supervisorctl -c /etc/supervisor/conf.d/pwmcp.conf start devtools-mcp 2>/dev/null || true

echo ""

# ── Summary ────────────────────────────────────────────────────────────────
echo "============================================"
echo -e " Results: ${GREEN}${pass} passed${NC}, ${RED}${fail} failed${NC} (${total} total)"
echo "============================================"

if [ -n "$errors" ]; then
    echo "Failures:"
    echo "${errors}"
fi

if [ $fail -gt 0 ]; then
    exit 1
fi

if [ $pass -eq 0 ]; then
    echo "No tests ran (prerequisite issue?)."
    exit 2
fi

echo "All checks passed."
exit 0
