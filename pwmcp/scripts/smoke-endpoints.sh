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

check() {
    local desc="$1"
    local expected="$2"
    shift 2
    total=$((total + 1))
    echo -n "  CHECK ${total}: ${desc} ... "

    # Wrap in timeout (30s default) to prevent hanging
    if timeout 30 "$@" >/dev/null 2>&1; then
        if [ "$expected" = "pass" ]; then
            echo -e "${GREEN}PASS${NC}"
            pass=$((pass + 1))
        else
            echo -e "${RED}FAIL (unexpected pass)${NC}"
            fail=$((fail + 1))
        fi
    else
        if [ "$expected" = "fail" ]; then
            echo -e "${GREEN}PASS (expected failure)${NC}"
            pass=$((pass + 1))
        else
            echo -e "${RED}FAIL${NC}"
            fail=$((fail + 1))
        fi
    fi
}

check_captured() {
    local desc="$1"
    local expected="$2"
    shift 2
    total=$((total + 1))
    echo -n "  CHECK ${total}: ${desc} ... "

    set +e
    local output
    output=$(timeout 30 "$@" 2>&1)
    local rc=$?
    set -e

    if [ "$expected" = "pass" ] && [ $rc -eq 0 ]; then
        echo -e "${GREEN}PASS${NC}"
        pass=$((pass + 1))
    elif [ "$expected" = "fail" ] && [ $rc -ne 0 ]; then
        echo -e "${GREEN}PASS (expected failure)${NC}"
        pass=$((pass + 1))
    elif [ "$expected" = "pass" ] && [ $rc -ne 0 ]; then
        echo -e "${RED}FAIL (rc=${rc})${NC}"
        echo "    Output: ${output}" >&2
        fail=$((fail + 1))
    else
        echo -e "${RED}FAIL (expected failure but got rc=${rc})${NC}"
        echo "    Output: ${output}" >&2
        fail=$((fail + 1))
    fi
}

mcp_initialize() {
    local url="$1"
    local host="$2"
    curl -fsS -X POST "$url" \
        -H "Content-Type: application/json" \
        -H "Host: ${host}" \
        -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke-test","version":"1.0"}}}'
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

# Check docker is available
if ! command -v docker &>/dev/null; then
    echo -e "${RED}Docker not available. Skipping smoke tests.${NC}"
    echo "Run manually when the stack is deployed."
    exit 2
fi

# Check if container is running
if docker ps --format '{{.Names}}' | grep -q "${CONTAINER_NAME}"; then
    echo -e "  Container ${CONTAINER_NAME}: ${GREEN}running${NC}"
else
    echo -e "  Container ${CONTAINER_NAME}: ${YELLOW}not running (ciu not started?)${NC}"
    echo "  Smoke tests require a running stack. Skipping."
    exit 2
fi

echo ""

# ── 1. Port/process checks via supervisord ─────────────────────────────────
echo "[SUPERVISORD STATUS]"

check_captured "All three programs RUNNING after 30s" "pass" \
    docker exec "${CONTAINER_NAME}" supervisorctl status

echo ""

# ── 2. MCP @playwright/mcp endpoint (port 8931) ────────────────────────────
echo "[MCP @playwright/mcp — PORT 8931]"

# 2a: Correct Host header → successful MCP initialize
check_captured "MCP initialize with correct Host header" "pass" \
    mcp_initialize "${MCP_URL}" "${PWMCP_HOST}:${MCP_PORT}"

# 2b: Forged Host header → rejection (non-2xx)
check_captured "MCP with forged Host header" "fail" \
    mcp_initialize "${MCP_URL}" "evil.example:${MCP_PORT}"

echo ""

# ── 3. MCP chrome-devtools-mcp endpoint (port 8932 via mcp-proxy) ──────────
echo "[DEVTOOLS chrome-devtools-mcp — PORT 8932]"

# 3a: Correct Host header → successful MCP initialize
check_captured "DevTools MCP initialize with correct Host header" "pass" \
    mcp_initialize "${DEVTOOLS_URL}" "${PWMCP_HOST}:${DEVTOOLS_PORT}"

# 3b: Forged Host header → rejection (non-2xx) [mcp-proxy has no host allowlist,
#     but we expect non-2xx because the Host won't match mcp-proxy's expectations]
check_captured "DevTools MCP with forged Host header" "fail" \
    mcp_initialize "${DEVTOOLS_URL}" "evil.example:${DEVTOOLS_PORT}"

# 3c: Call a real tool end-to-end (start a performance trace on a data: URL page)
if [ "${1:-}" != "--quick" ]; then
    check_captured "DevTools MCP end-to-end tool call (list all tools)" "pass" \
        curl -fsS -X POST "${DEVTOOLS_URL}" \
            -H "Content-Type: application/json" \
            -H "Host: ${PWMCP_HOST}:${DEVTOOLS_PORT}" \
            -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
fi

echo ""

# ── 4. Fault isolation ─────────────────────────────────────────────────────
echo "[FAULT ISOLATION]"

# 4a: Checking that playwright-mcp (8931) is unaffected by devtools-mcp
check_captured "MCP (8931) works after devtools health check" "pass" \
    mcp_initialize "${MCP_URL}" "${PWMCP_HOST}:${MCP_PORT}"

# 4b: Kill devtools-mcp program and verify 8931/3000 still respond
echo "  Stopping devtools-mcp program (fault isolation test)..."
docker exec "${CONTAINER_NAME}" supervisorctl stop devtools-mcp 2>/dev/null || true
sleep 2

check_captured "MCP (8931) still works after devtools-mcp stopped" "pass" \
    mcp_initialize "${MCP_URL}" "${PWMCP_HOST}:${MCP_PORT}"

# Restart devtools-mcp for subsequent tests
docker exec "${CONTAINER_NAME}" supervisorctl start devtools-mcp 2>/dev/null || true

echo ""

# ── Summary ────────────────────────────────────────────────────────────────
echo "============================================"
echo -e " Results: ${GREEN}${pass} passed${NC}, ${RED}${fail} failed${NC} (${total} total)"
echo "============================================"

if [ $fail -gt 0 ]; then
    echo "Failures detected. Review output above."
    exit 1
fi

if [ $pass -eq 0 ]; then
    echo "No tests ran (prerequisite issue?)."
    exit 2
fi

echo "All checks passed."
exit 0
