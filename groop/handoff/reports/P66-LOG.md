# P66 Work Log

## Context

- Branch: feat/groop-p66-daemon-client-versioned-health
- Worktree: .worktrees/groop-p66-daemon-client-versioned-health
- Base commit: main (P63 merged; base commit bf74607 "docs(groop): 2026-07-15
  reconciliation")
- Package: P66 — Daemon Client Versioned Health Method
- Current objective: Add `request_health_versioned()` to `DaemonClient`,
  completing the versioned read surface with the one op P63 left out
  (`health`).

## Timeline

```text
2026-07-15 UTC
- Action: Read handoff (P66-daemon-client-versioned-health.md), README.md,
  client.py, api.py (_op_health + dispatch + envelope wrapper), and
  component_health.py per the bounded context list.
- Action: Read test_daemon_client_p63.py (harness shape) and
  test_daemon_component_health.py (existing legacy request_health coverage,
  which must stay unweakened).
- Decision: _op_health's result dict (build_health_response's
  type/schema_version/capability/components) is structurally identical to
  the legacy single-line health payload minus the envelope wrapper, so the
  existing DaemonClient._parse_health_payload can be reused verbatim to
  decode the versioned envelope's `result` dict into a HealthSnapshot --
  no second decode implementation, no api.py change needed. This resolves
  the package's own Escalate-if condition: the result shape IS expressible
  as a frozen typed result without touching api.py.
- Decision: "overall status" (named in the handoff's Required Deterministic
  Tests line) is not a field the P52 wire protocol emits anywhere -- grepped
  the whole daemon/cli/mcp tree for any existing "overall" health concept
  and found none tied to component health specifically. Found a close
  in-repo precedent instead: groop/src/groop/daemon/status.py's
  DaemonStatusReport.ok is a *computed* (not stored) frozen-dataclass
  property with a binary ok/DEGRADED reading. Followed that exemplar:
  DaemonVersionedHealthResult.overall_ok is a computed property, True iff no
  component reports DEGRADED or FAILED -- reusing
  ComponentHealthRegistry.set_state's own failed_attempt classification
  (`state in {DEGRADED, FAILED}`) rather than inventing a new severity
  ordering across all 7 ComponentState values. This does not touch
  component_health.py or change P47 semantics; it only reads the states P47
  already decodes.
- Action: Added `DaemonVersionedHealthResult` frozen dataclass
  (groop/src/groop/daemon/client.py) with `.snapshot: HealthSnapshot` and
  computed `.overall_ok` property. Named to not collide with the legacy
  `HealthSnapshot` type.
- Action: Added `request_health_versioned()` method on `DaemonClient`:
  calls `self._request_envelope("health")` (same transport as
  hello/current/history/entity, no second transport), then
  `self._parse_health_payload(result)` (same decode/validation as legacy
  `request_health`), wraps the result.
- Action: Did NOT touch `request_health`, `_read_health`,
  `_parse_health_payload`, or any other existing method signature/behavior.
- Action: Exported `DaemonVersionedHealthResult` through
  groop/src/groop/daemon/__init__.py (import + __all__).
- Action: Wrote groop/tests/test_daemon_client_p66.py -- 14 deterministic
  tests against the real DaemonApi envelope over a real AF_UNIX socket
  (same harness helpers as test_daemon_client_p63.py, no hand-mocked
  socket): all-healthy happy path, DEGRADED happy path (component + error
  payload + overall_ok False), FAILED happy path, ok:false ->
  DaemonResponseError with .code == "unavailable", malformed/oversized/
  non-object envelope -> DaemonProtocolError, id echo mismatch ->
  DaemonProtocolError, health-content-level malformed (invalid component
  state, incompatible schema_version) -> DaemonProtocolError, parity check
  between request_health_versioned() and legacy request_health() against
  the same registry, legacy-method-untouched check, result-type-collision
  check, connection failure -> DaemonConnectError.
- Action: Updated groop/docs/DAEMON.md "Typed Versioned Client (P63)"
  section (retitled P63/P66) with the new method row, import line, and a
  paragraph documenting request_health_versioned()'s decode reuse and
  overall_ok semantics.
- Action: Ran gates (see Validation below). All green.
- Result: Implementation complete; ready for commit.
```

## Decisions

- Decision: Reuse `DaemonClient._parse_health_payload` directly for the
  versioned envelope's `result` dict instead of writing a second parser.
  Reason: `_op_health()` returns `build_health_response(...)` verbatim as
  the dispatch result, which becomes the envelope's `result` field
  unmodified; its shape (`schema_version`/`capability`/`components`) is
  exactly what `_parse_health_payload` already validates. Writing a second
  parser would violate "no re-derive component-health semantics" and create
  a second place that could drift from the legacy validation.
  Impact: Every existing legacy-path edge case
  (`test_daemon_client_rejects_incompatible_health_payload`'s 6 mutations)
  is inherited for free by the versioned method with zero extra code.
- Decision: `overall_ok` is a computed `@property`, not a stored dataclass
  field.
  Reason: Matches the in-repo `DaemonStatusReport.ok` pattern; a stored
  field could desync from `.snapshot` if either were constructed
  independently (the dataclass is otherwise not validated on construction
  the way decode-time parsing is).
  Impact: `.overall_ok` is always consistent with `.snapshot`; no separate
  invariant to test for staleness.
- Decision: `overall_ok`'s "not ok" set is exactly `{DEGRADED, FAILED}`.
  Reason: This is `ComponentHealthRegistry.set_state`'s own existing
  `failed_attempt` classification, not an invented ranking. DISABLED (
  intentionally off) and STARTING/STOPPING/STOPPED (transitional, not
  failures) are treated as "not currently broken", matching how the
  registry itself never marks those as failed attempts.
  Impact: No new severity ordering invented across all 7 ComponentState
  values; the aggregate is directly traceable to existing P47 code.

## Blockers

None. The package's own Escalate-if condition (result shape not
expressible as a frozen typed result without touching api.py; or requiring
a change to any legacy method) did not fire -- see Decisions above.

## Validation

Environment: fresh venv built per the handoff's Environment/gates section
(`python3 -m venv .venv && .venv/bin/pip install -e './groop[dev]'`), Linux
x86_64, Python 3.14 (venv-resolved), from worktree root
`/workspaces/vbpub/.worktrees/groop-p66-daemon-client-versioned-health`.

```bash
PYTHONPATH=groop/src .venv/bin/python -m pytest groop/tests/test_daemon_client_p66.py -q -W error -p no:schemathesis
# 14 passed in 6.24s

PYTHONPATH=groop/src .venv/bin/python -m pytest groop/tests/test_daemon_client_p66.py groop/tests/test_daemon_client_p63.py groop/tests/test_daemon_component_health.py -q -W error -p no:schemathesis
# 83 passed in 18.34s

timeout 900 env PYTHONPATH=groop/src .venv/bin/python -m pytest groop/tests -q -W error -p no:schemathesis
# 1465 passed in 183.22s (0:03:03) -- zero skips, [dev] extra installed

.venv/bin/python -m py_compile groop/src/groop/daemon/client.py groop/src/groop/daemon/__init__.py groop/tests/test_daemon_client_p66.py
# clean

git diff --check
# clean

git status --short
#  M groop/docs/DAEMON.md
#  M groop/src/groop/daemon/__init__.py
#  M groop/src/groop/daemon/client.py
# ?? groop/tests/test_daemon_client_p66.py
```

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented (none).
- [x] Feature branch committed.
