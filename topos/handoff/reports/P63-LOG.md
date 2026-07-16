# P63 Work Log

## Context

- Branch: feat/topos-p63-daemon-client-versioned-read
- Worktree: .worktrees/topos-p63-daemon-client-versioned-read
- Base commit: main (P52 merged)
- Package: P63 — Daemon Client Versioned Read Methods
- Current objective: Implement typed versioned-envelope client methods on DaemonClient

## Timeline

```text
2026-07-13 UTC
- Action: Read handoff and context files (client.py, api.py, CONTRACTS.md, test_daemon_client.py, test_daemon_p52.py, model.py, DAEMON.md)
- Action: Added DaemonResponseError.code attribute for typed ErrorCode recovery
- Action: Added frozen result dataclasses (DaemonCurrentResult, DaemonHistoryResult, DaemonEntityResult, DaemonHello)
- Action: Added _request_envelope private helper with id echo assertion, max response bytes, envelope decode
- Action: Added typed methods (request_hello, request_current, request_history, request_entity)
- Action: Wired new exports through daemon/__init__.py
- Action: Wrote 20 deterministic tests in test_daemon_client_p63.py
  - Happy paths for all 4 methods against real DaemonApi envelope
  - id echo mismatch -> DaemonProtocolError
  - All 5 error codes (not_found, invalid_type, out_of_range, bad_request, unavailable) recoverable via .code
  - History cursor form and time-window form
  - Client fast-fail on cursor+window both set
  - Gap/oldest/latest/next_cursor semantics match stream_batch
  - Malformed/oversized/empty/non-object/non-JSON -> DaemonProtocolError
  - Connection failure -> DaemonConnectError
  - Import isolation preserved
- Action: Updated DAEMON.md with Typed Versioned Client (P63) section
- Action: Ran gates — all 20 P63 tests pass, full suite 1023/2, py_compile clean, git diff --check clean
- Action: Wrote P63-LOG.md and P63-REPORT.md
- Result: Implementation complete; ready for commit
```

## Decisions

- Decision: Added .code attribute to DaemonResponseError rather than creating subclasses
  Reason: Simpler to branch on err.code than isinstance checks; matches the caller pattern P58 needs
  Impact: Code change is minimal; callers write `except DaemonResponseError as e: if e.code == "not_found":`
- Decision: Client fast-fails on cursor+window both set with ValueError before sending
  Reason: Handoff specified either approach; failing fast avoids pointless round-trip
  Impact: Tested explicitly
- Decision: Used _serve_echo_lines fixture for tests with hardcoded response templates
  Reason: Tests that send simulated error responses need the server to echo the client's generated id

## Validation

```bash
PYTHONPATH=topos/src python3 -m pytest topos/tests/test_daemon_client_p63.py -q -W error -p no:schemathesis
# 20 passed
timeout 900 env PYTHONPATH=topos/src python3 -m pytest topos/tests -q -W error -p no:schemathesis
# 1023 passed, 2 skipped
python3 -m py_compile topos/src/topos/daemon/client.py
python3 -m py_compile topos/src/topos/daemon/__init__.py
python3 -m py_compile topos/tests/test_daemon_client_p63.py
git diff --check
```
