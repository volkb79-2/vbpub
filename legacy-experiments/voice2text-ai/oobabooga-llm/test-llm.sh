#!/usr/bin/env bash
set -euo pipefail

YELLOW='\033[1;33m'; GREEN='\033[1;32m'; RED='\033[1;31m'; NC='\033[0m'

info() { echo -e "${YELLOW}[INFO]${NC} $*"; }
pass() { echo -e "${GREEN}[PASS]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; exit 1; }

SHIM_PORT=${SHIM_PORT:-8300}
SERVICE_PORT=${LLM_SERVICE_PORT:-8000}

info "1. Checking running containers" 
(docker compose ps || docker ps) >/dev/null 2>&1 || fail "Docker not accessible"

info "2. Waiting for shim health"
for i in {1..30}; do
  if curl -s http://localhost:${SHIM_PORT}/health | grep -qi 'ok'; then pass "Shim healthy"; break; fi
  sleep 2
  [[ $i -eq 30 ]] && fail "Shim health timeout"
done

info "3. Basic generation test"
REQ='{"model":"postproc-mini","messages":[{"role":"user","content":"Fix grammar: this are sample sentence"}]}'
RESP=$(curl -s -H 'Content-Type: application/json' -d "$REQ" http://localhost:${SHIM_PORT}/v1/chat/completions || true)
[[ -z "$RESP" ]] && fail "Empty response"

if echo "$RESP" | grep -q 'choices'; then
  pass "OpenAI-compatible response structure present"
else
  fail "Response missing 'choices'"
fi

info "4. Latency measurement"
START=$(date +%s)
curl -s -H 'Content-Type: application/json' -d "$REQ" http://localhost:${SHIM_PORT}/v1/chat/completions >/dev/null || fail "Second request failed"
END=$(date +%s)
ELAPSED=$((END-START))
pass "Second request latency: ${ELAPSED}s"

info "5. Memory usage (resident set)"
CID=$(docker ps --format '{{.ID}} {{.Names}}' | grep llm-webui | awk '{print $1}') || true
if [[ -n "$CID" ]]; then
  docker stats --no-stream $CID | awk 'NR==2 {print "Resident/Usage: "$3" / "$4}'
fi

info "All tests completed."