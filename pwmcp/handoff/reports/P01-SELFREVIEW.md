# P01 Self-Review Findings

Reviewed commit db3d026 against handoff `pwmcp/handoff/P01-chrome-devtools-mcp.md`.

## Findings

### 1. Traefik router rule — broken PathPrefix combination (must fix)

**File:** `pwmcp/ciu.compose.yml.j2` line 67
**Handoff ref:** "external mode gets its own Traefik router (path or port distinct from the existing -mcp router) — copy the existing router block's shape."

```
- traefik.http.routers.{{ pwmcp.unified.name }}-devtools.rule=Host(`{{ pwmcp.external.unified_host }}`) && PathPrefix(`/mcp`) && PathPrefix(`/devtools`)
```

Two `PathPrefix` matchers with different values means the path must start with BOTH `/mcp` AND `/devtools` — impossible (no request path can satisfy both). The rule would never match, making the devtools endpoint unavailable in external mode.

**Fix:** Use `PathPrefix(\`/devtools\`)` alone and add a StripPrefix middleware so `/devtools/mcp` reaches the backend as `/mcp`.

### 2. Smoke test: supervisord status check is hollow (must fix)

**File:** `pwmcp/scripts/smoke-endpoints.sh` line 153
**Handoff ref:** "Assert supervisord reports all three programs RUNNING after 30 s"

```
check_captured "All three programs RUNNING after 30s" "pass" \
    docker exec "${CONTAINER_NAME}" supervisorctl status
```

- Does not wait 30 seconds (no `sleep 30` before the check).
- Does not assert specific program states. `supervisorctl status` exits 0 even when programs are in FATAL/BACKOFF/STOPPED state. The test would pass with zero programs running.
- The title says "after 30s" but no delay is implemented.

### 3. Smoke test: MCP initialize does not parse JSON-RPC response (must fix)

**File:** `pwmcp/scripts/smoke-endpoints.sh` line 162
**Handoff ref:** "asserting a successful JSON-RPC result naming the server"

```
mcp_initialize() {
    curl -fsS -X POST ...
}
```

`curl -fsS` only checks HTTP 2xx status. A 200 response with `{"jsonrpc":"2.0","error":{"code":-32000}}` would pass (HTTP 200, error body). The test must use `jq` (already declared as a requirement) to verify the JSON body contains no `.error` field and has a `.result.serverInfo.name`.

### 4. Smoke test: devtools forged-Host assertion is wrong (must fix)

**File:** `pwmcp/scripts/smoke-endpoints.sh` line 180
**Handoff ref:** "the same POST with a forged Host: evil.example:8932 asserting rejection (non-2xx)"

```
check_captured "DevTools MCP with forged Host header" "fail" \
    mcp_initialize "${DEVTOOLS_URL}" "evil.example:${DEVTOOLS_PORT}"
```

The test expects failure (non-2xx), but mcp-proxy does not enforce Host header allowlisting — it will likely accept any Host. The handoff anticipates this gap: "If the chosen server/proxy has no host allowlist, document that gap in SECURITY.md explicitly." The test must expect success for devtools (since enforcement is absent) and the result must be documented as a gap, not hidden in a `#` comment.

For 8931 (@playwright/mcp) this test is correct because `--allowed-hosts` is enforced.

### 5. Smoke test: `check` function is dead code (fix)

**File:** `pwmcp/scripts/smoke-endpoints.sh` lines 49-75

The `check()` function is defined but never called. Only `check_captured()` is used. Remove dead code.

### 6. Chrome-major compatibility note missing (fix)

**File:** `pwmcp/README.md` line 134
**Handoff ref:** "note the Chrome-major ↔ chrome-devtools-mcp compatibility expectation"

The README notes Node version requirement but does not mention Chrome-major version compatibility expectations with chrome-devtools-mcp. Add a note alongside the pin table.

### 7. REPORT missing template rendering diff (document gap)

**Handoff ref:** "verify by rendering before/after and diffing; include the diff in the REPORT"

The ciu templates cannot be rendered in this environment (no ciu installation). This is an environment limitation, same as Docker. Document in REPORT and LOG that this verification is pending controller execution.

### 8. External-mode URL description in README is misleading (fix)

**File:** `pwmcp/README.md` line 104
**Handoff ref:** "external mode gets its own Traefik router"

The README says "In external mode the URL becomes `https://<unified_host>/mcp` with a `/devtools` path prefix." With the broken PathPrefix rule this is doubly wrong — but even after fixing the rule, the URL path is `/devtools` (or `/devtools/mcp`), not `/mcp`. Fix the description.

### 9. `jq` listed as requirement but never used (fix)

**File:** `pwmcp/scripts/smoke-endpoints.sh` line 20

```
#   - curl, jq, timeout
```

`jq` is listed as a prerequisite but no test in the script calls `jq`. Once finding #3 is fixed (JSON-RPC response parsing), `jq` will actually be used.

### 10. No 30-second delay before supervisord status check (fix)

**Handoff ref:** "also assert supervisord reports all three programs RUNNING after 30 s"

The check must wait 30 seconds after container start before asserting program states. Add `sleep 30` or a polling loop before the supervisord check.

---

## Summary

| # | Severity | Area | Status |
|---|----------|------|--------|
| 1 | **BUG** | ciu.compose.yml.j2 — Traefik router rule | To fix |
| 2 | **BUG** | smoke-endpoints.sh — supervisord check hollow | To fix |
| 3 | **BUG** | smoke-endpoints.sh — no JSON-RPC body assertion | To fix |
| 4 | **BUG** | smoke-endpoints.sh — wrong forged-Host expectation | To fix |
| 5 | MINOR | smoke-endpoints.sh — dead code | To fix |
| 6 | MINOR | README.md — missing Chrome compat note | To fix |
| 7 | INFO | REPORT — template diff not run (env limitation) | Document |
| 8 | MINOR | README.md — external URL description | To fix |
| 9 | MINOR | smoke-endpoints.sh — jq unused | To fix (by #3) |
| 10 | MINOR | smoke-endpoints.sh — no 30s wait | To fix |
