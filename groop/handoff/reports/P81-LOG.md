# P81 Work Log

## Context

- Branch: `feat/groop-p81-redaction-single-enforcement`
- Worktree: `/workspaces/vbpub/.worktrees/groop-p81-redaction-single-enforcement`
- Base commit: `bf7460765ca4f65368f5c2304d898c4c98f242c5` (main)
- Package: P81 — Redaction: one enforcement point, no bypass
- Objective: One shared, fail-closed server-side redaction enforcement point;
  one typed marker dialect; `findings[]` covered; no P52 wire change.

## Timeline

```text
2026-07-15 UTC
- Action: Read the P81 handoff, README standing contracts, CONTRACTS §10, both
  frontends (http_gateway.py, mcp/server.py), model.py, daemon/api.py, and the
  existing gateway/MCP test harnesses and fixtures.
- Result: Confirmed the two defects are real. The gateway redacts only metric
  maps and ships findings + governance/network/damon/host_meta verbatim; MCP
  emits the "__redacted__" string dialect and falls back to the canonical
  classifier (not fail-closed) on absent metadata. The daemon fixture already
  carries a finding and per-entity governance/network dicts with value-bearing
  fields (governance.limits.*.recorded_value/live_value).

2026-07-15 UTC
- Action: Built the environment and captured a clean baseline.
- Commands: python3 -m venv .venv && .venv/bin/pip install -e './groop[dev]';
  timeout 900 env PYTHONPATH=groop/src .venv/bin/python -m pytest groop/tests
  -q -W error -p no:schemathesis.
- Result: 1451 passed, 0 skipped (P84 zero-skip gate green pre-change).

2026-07-15 UTC
- Action: Wrote groop/daemon/redaction.py — the single enforcement point:
  classify_metric (fail-closed to sensitive), one redaction_marker dialect, a
  PayloadShape registry of typed visitors (FRAME, ENTITY_FRAME, MCP_OVERVIEW,
  MCP_ENTITY, MCP_HISTORY), findings coverage, and fail-closed handling of
  unrecognized value-bearing fields and unregistered shapes.
- Action: Rewired both frontends to call redaction.redact_payload and deleted
  their local redaction walks (_redact_frame/_redact_metrics/_redaction_marker/
  _SENSITIVITY_RANK in the gateway; _redact/_REDACTED_MARKER/_sensitivity in
  MCP). MCP entity findings now carry source_metrics so the shared point can
  classify them.
- Commands: py_compile of changed files; focused gateway+MCP suites.
- Result: 65 passed after updating the MCP tests that encoded the retired
  dialect / non-fail-closed fallback and the oversized-finding fake (now carries
  source_metrics).

2026-07-15 UTC
- Action: Added tests/test_p81_redaction_enforcement.py — 10 adversarial oracles
  driving a real DaemonApi over a temp AF_UNIX socket through BOTH frontends,
  including the disarmed-walker oracle (monkeypatch redact_payload to identity
  and assert the shared leak check then fires).
- Commands: focused P81 gate (new file + gateway + MCP); py_compile;
  git diff --check.
- Result: 75 passed; compile clean; diff clean.

2026-07-15 UTC
- Action: Updated the README P81 status row to Done and wrote the report/log.
- Commands: full-suite P84 gate (see Validation).
```

## Decisions

- Decision: A `PayloadShape`-keyed registry of typed visitors behind a single
  public `redact_payload`, rather than one generic recursive walker.
  Reason: The handoff mandates "add typed visitors/registrations as those shapes
  land" and fail-closed on unknown shape. A registry makes registration the only
  way to widen the boundary and lets the disarm oracle no-op exactly one
  function.
  Impact: Both frontends import the module and call `redaction.redact_payload`;
  neither keeps a redaction walk. An unregistered shape raises `RedactionError`.

- Decision: Fail closed on unrecognized value-bearing entity/frame fields
  (`governance`, `network`, `damon`, `host_meta`) by replacing them with the
  sensitive marker.
  Reason: These carry metric values (e.g. `governance.limits.*.live_value`) and
  were shipped past the old redaction pass — the exact latent bypass the handoff
  describes for `findings`. The `Sensitivity` enum expresses this as the
  fail-closed default (unclassified ⇒ sensitive); no third dialect invented.
  Impact: A deliberate, contract-driven behavior change on the gateway routes.
  No test read these fields out of a frontend response (verified), and a future
  package can add a typed visitor to classify their internals.

- Decision: `classify_metric` fails closed to `sensitive` when metadata is
  absent/invalid, replacing MCP's canonical-classifier fallback.
  Reason: Oracle #4 requires an unclassified metric to be redacted below the
  `sensitive` ceiling in BOTH frontends; MCP's old fallback did not.
  Impact: Updated the one MCP test that asserted the old fallback semantics.

## Blockers

- Blocker: None. No P52 wire/envelope/error-code change was required; the
  `Sensitivity` enum expressed every classification (including the fail-closed
  default). Not BLOCKED.

## Validation

```bash
# Focused P81 gate (package venv, Python 3.14.6, Linux; groop[dev] installed)
PYTHONPATH=groop/src .venv/bin/python -m pytest \
  groop/tests/test_p81_redaction_enforcement.py \
  groop/tests/test_daemon_http_gateway.py \
  groop/tests/test_mcp_server.py -q -W error -p no:schemathesis
# 75 passed

# Required full gate (same venv; all extras present -> P84 zero-skip)
timeout 900 env PYTHONPATH=groop/src .venv/bin/python -m pytest \
  groop/tests -q -W error -p no:schemathesis
# 1461 passed in ~176s (0 skipped)

.venv/bin/python -m py_compile groop/src/groop/daemon/redaction.py \
  groop/src/groop/daemon/http_gateway.py groop/src/groop/mcp/server.py \
  groop/tests/test_p81_redaction_enforcement.py
# clean

git diff --check
# clean
```

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/diff recorded.
- [x] Known gaps documented.
- [ ] Feature branch committed (final step).
