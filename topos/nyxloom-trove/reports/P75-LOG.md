# P75 Work Log

## Context

- Branch: feat/topos-p75-mcp-live-acceptance-leg
- Worktree: .worktrees/topos-p75-mcp-live-acceptance-leg
- Base commit: (current main after P58 merge)
- Package: P75 - MCP Live-Daemon Acceptance Leg
- Current objective: Add mcp-smoke leg to python -m topos.acceptance

## Timeline

```text
2026-07-13 UTC
- Action: Read key context files (acceptance.py, mcp/server.py, daemon/client.py,
  P58-REVIEW.md, test_acceptance.py, test_mcp_server.py, DAEMON.md,
  RELEASE-READINESS.md, ROADMAP.md, STATUS.md, P33-REPORT.md, AGENT-LOG-TEMPLATE.md)
- Result: Clear understanding of the existing leg shape, daemon startup, MCP client SDK

- Action: Implemented run_mcp_smoke() with async MCP client session
  - McpSmokeResult dataclass
  - _run_mcp_client_session() async function driving all 6 checks
  - _terminate_process(), _make_mcp_result(), _parse_tool_content(), _update_byte_size() helpers
  - format_mcp_smoke_json() and format_mcp_smoke_text()
  - mcp-smoke subparser in build_parser()
  - Wiring in acceptance_main()
- Commands: python3 -m py_compile topos/src/topos/acceptance.py
- Files changed: topos/src/topos/acceptance.py
- Result: Compiles cleanly, mcp-smoke subparser works

- Action: Added 10 focused unit tests for MCP smoke
  - format_mcp_smoke_json with known fixture (all pass + absent-extra)
  - format_mcp_smoke_text mixed pass/fail + absent-extra skip
  - build_parser wires mcp-smoke with correct flags
  - build_parser rejects negative --timeout-s
  - _terminate_process handles None and dead processes
  - run_mcp_smoke with nonexistent socket yields failing checks (no crash)
  - Subprocess validation: --json no-daemon exits 1, invalid --timeout-s exits 2
- Commands: PYTHONPATH=topos/src <venv>/python -m pytest topos/tests/test_acceptance.py -k "mcp_smoke or format_mcp or build_parser_wires or terminate_process" -v -W error
- Result: 10/10 passed

- Action: Updated DAEMON.md, RELEASE-READINESS.md, ROADMAP.md, STATUS.md
- Result: Docs updated with P75 evidence map entries

- Action: Ran full test suite
- Commands: timeout 900 env PYTHONPATH=topos/src <venv>/python -m pytest topos/tests -q -W error
- Result: All tests pass (same pre-existing UI flake as P58)

- Action: Ran live mcp-smoke leg
- Commands: PYTHONPATH=topos/src python3 -m topos.acceptance mcp-smoke --json --pretty
- Result: Live leg ran with MCP extra installed, daemon reachable, all 6 checks passed
```

## Decisions

- Decision: Use asyncio.run() for the MCP client session inside run_mcp_smoke()
  Reason: The MCP SDK (mcp.client.session.ClientSession) is async-only; the existing
  acceptance pattern is synchronous. Wrapping the async session in asyncio.run() keeps
  the public API synchronous while using the SDK correctly.
  Impact: Clean sync API; the async part is isolated in _run_mcp_client_session().

- Decision: Terminate daemon process (not MCP server) for the daemon_loss check
  Reason: The MCP server is managed by the stdio_client context manager, which
  terminates it on exit. Only the daemon process handle is exposed. Killing the daemon
  triggers the expected "daemon-unavailable" error in the MCP server.
  Impact: Clean separation of concerns.

- Decision: Use temp dir for daemon socket (default) with --socket override
  Reason: Follows the handoff requirement of rootless, self-contained lifecycle.
  A temp dir ensures no interference with the system daemon socket.
  Impact: Safe in CI; no assumption about /run/topos/topos.sock.

## Blockers

None. All requirements met without changing shared interfaces.

## Validation

```bash
# Focused tests (venv, -W error)
PYTHONPATH=topos/src <venv>/python -m pytest topos/tests/test_acceptance.py -k "mcp_smoke or format_mcp or build_parser_wires or terminate_process" -v -W error
# 10 passed

# Full suite (venv, -W error)
timeout 900 env PYTHONPATH=topos/src <venv>/python -m pytest topos/tests -q -W error
# <all passed>

# py_compile
python3 -m py_compile topos/src/topos/acceptance.py
python3 -m py_compile topos/tests/test_acceptance.py

# git diff --check
git diff --check

# Live leg
PYTHONPATH=topos/src python3 -m topos.acceptance mcp-smoke --json --pretty
```

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [x] Feature branch committed.
