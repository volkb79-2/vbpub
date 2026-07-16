# P46 Admin Action Execution Kernel — Correction Work Log

## Context

- Branch: `feat/topos-p46-admin-action-execution-kernel`
- Worktree: `.worktrees/-topos-p46-admin-action-execution-kernel`
- Scope: `topos/**` only
- Objective: correct the P46 controller security findings without merging

## Timeline

```text
2026-07-10
- Read P46 handoff, prior P46 commits, report, log, implementation, tests, and
  operational/status documentation.
- Confirmed prior report's explicit gaps: missing root gate, optional/arbitrary
  audit path, relative executables, unbounded subprocess capture, weak Docker
  names, and missing injected identity.
- Reworked catalog/preview/execute/CLI around one frozen ActionPlan contract.
- Added root gate, fixed production audit default, API-only fixture audit path,
  secure no-follow 0600 append/fsync audit, stable identity and clock seams,
  bounded selector pipe draining, absolute executables, finite timeout gate,
  typed runner/audit failures, and strict target validation.
- Removed execute CLI --audit-log; preview --audit-log remains preview-only.
- Added adversarial controller-review tests and updated old absolute-argv and
  mandatory-audit expectations.
- Focused suite after controller review: 129 passed in 0.45s.
- Full suite after controller review: 532 passed, 1 skipped in 48.77s.
- Full-source compile: 97 Python files compiled successfully.
- Next: review diff and commit.
```

## Decisions

- Production audit path is fixed at `/var/log/topos/actions.jsonl`; only the
  Python API accepts an injected absolute fixture path.
- Audit records omit confirmation text and use one stable identity captured for
  the pre/post pair.
- Child stdout/stderr are drained concurrently and excess bytes are discarded
  after a bounded prefix so a noisy child cannot deadlock the runner.
- Post-audit failure changes the typed top-level outcome to `audit_failure` but
  preserves `action_outcome`, return code, and bounded output.
- The allowlist remains exactly Docker/systemd start/stop/restart. No kill,
  update, set-property, CIU, TUI, or daemon mutation path was added.

## Validation commands

```bash
PYTHONPATH=topos/src /workspaces/vbpub/.venv/bin/python -m pytest topos/tests/test_actions.py -q
PYTHONPATH=topos/src /workspaces/vbpub/.venv/bin/python -m pytest topos/tests -q
mapfile -d '' pyfiles < <(find topos/src/topos topos/tests -name '*.py' -print0)
/workspaces/vbpub/.venv/bin/python -m py_compile "${pyfiles[@]}"
```

Final validation: focused 129 passed; full 532 passed, 1 skipped; full-source compile of
97 Python files passed.

Controller review additionally made the injected root check fail closed unless
it returns the boolean `True`, and guarantees that an unexpected pipe-drainer
failure kills/reaps the child before returning a typed runner failure.

Post-merge controller validation with P44 on main: combined focused P44/P46
regression `151 passed in 0.58s`; full suite `554 passed, 1 skipped in 48.30s`.
