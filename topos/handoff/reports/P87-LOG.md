# P87 Work Log

Keep this file current while working. It is a resumability artifact, not a
place for private reasoning. Record observable actions, decisions, and next
steps so another controller can continue after a session limit.

## Context

- Branch: feat/topos-p87-docker-owner-safety
- Worktree: /workspaces/vbpub/.worktrees/topos-p87-docker-owner-safety
- Base commit: bf74607 (main)
- Package: P87 - close Docker action owner and protected-ID bypasses
- Current objective: single-inspect owner/protected-ID safety gate on the raw
  Docker mutation verbs (start/stop/restart/kill/update), fail-closed.

## Timeline

Append newest entries at the bottom.

```text
2026-07-15 (session start)
- Action: Read spec, README standing contracts, action kernel (execute.py,
  catalog.py, kill_ops.py, update_ops.py, preview.py, governance.py, audit.py),
  dockerjoin.py + CIU labels, model.py, config.py, LIFECYCLE-ADAPTERS.md,
  DECISIONS-INBOX D-016, and the P46/P72/P78 action tests.
- Result: Confirmed label names to reuse:
    Compose  -> com.docker.compose.project (com.docker.compose.service)
    CIU      -> ciu.managed="true", ciu.stack
    Wings    -> Service="Pterodactyl", ContainerType="server_process"
      (TUI-SPEC.md line 554: the labels wings sets are exactly these two).
  Confirmed the hard constraint: existing P46/P72 start/stop/restart/kill/update
  tests call the executors WITHOUT any owner-inspect seam and expect success
  with no Docker present. => the owner-safety inspect MUST be an opt-in
  Python-API seam (default None = legacy P46/P72 path, no owner layer). Once
  engaged, the gate is fail-closed (contract 7).
- Follow-up: implement owner_safety.py, wire it as a POST-audit gate on the
  three Docker executors (so refusals are audited as a pre/post pair per
  oracle 2), wire production CLI, write tests, run gates.
```

## Decisions

- Decision: owner-safety runs as a POST-audit gate (not pre-audit).
  Reason: oracle 2 requires the refusal to produce "one pre/post audit
  outcome"; a pre-audit gate writes zero audit records. Post-audit gates run
  before the runner and write the pre+post pair on refusal.
  Impact: owner/protected-ID refusals are durably audited; the existing kill
  pre-audit `protected_gate` (P72) is left untouched so its tests pass verbatim.

- Decision: the `owner_inspect` seam defaults to None (no owner layer).
  Reason: existing P46/P72 tests must pass UNMODIFIED and run without Docker.
  A fail-closed always-on inspect default would refuse those standalone runs.
  Impact: production enforcement requires wiring the seam; the CLI execute path
  is wired to a real resolver. Once the seam is engaged the gate is fail-closed.

## Blockers

- None.

## Validation

Environment: fresh venv in the worktree, `.venv/bin/pip install -e './topos[dev]'`
(exit 0). Commands run with `PYTHONPATH=topos/src` from the worktree root.

- Focused (P87 + the P46/P72/P78 action suites, to prove they pass unmodified):
  `pytest topos/tests/test_p87_owner_safety.py topos/tests/test_actions.py
  topos/tests/test_p72_kill_update.py topos/tests/test_p78_action_kernel.py
  -q -W error -p no:schemathesis` -> `388 passed in 1.83s`.
- Full zero-skip suite:
  `timeout 900 ... pytest topos/tests -q -W error -p no:schemathesis` ->
  `1516 passed in 193.67s (0:03:13)` (zero skips, zero failures).
- `py_compile` on owner_safety.py, execute.py, cli.py, test_p87_owner_safety.py: clean.
- `git diff --check`: clean.
- CLI smoke: `action execute --kind docker-restart ... ` refuses at the root
  gate (exit 2, no crash, new owner_safety import path exercised); `action
  preview` unaffected (exit 0).

## Timeline (continued)

```text
2026-07-15 (implementation)
- Action: Added topos/src/topos/actions/owner_safety.py (single-inspect resolver,
  owner detection, canonicalized protected check, typed refusals). Wired it as a
  post-audit gate into execute_plan/execute_kill/execute_update via a shared
  _make_owner_safety_gate() helper; added owner_inspect/owner_protected_services
  API-only seams. Wired the production CLI execute path to the real resolver.
  Added topos/tests/test_p87_owner_safety.py (all 5 oracles + single-inspect +
  systemd-unaffected + legacy no-op).
- Result: gates green as recorded above.
- Follow-up: none. P93 owns the full owner-chain protocol.
```
