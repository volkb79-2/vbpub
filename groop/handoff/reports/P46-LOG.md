# P46 Admin Action Execution Kernel — Correction Work Log

## Context

- Branch: `feat/groop-p46-admin-action-execution-kernel`
- Worktree: `.worktrees/-groop-p46-admin-action-execution-kernel`
- Scope: `groop/**` only
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
- Focused suite: 127 passed.
- Full suite: 531 passed in 43.88s.
- Full-source compile: 97 Python files compiled successfully.
- Next: review diff and commit.
```

## Decisions

- Production audit path is fixed at `/var/log/groop/actions.jsonl`; only the
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
PYTHONPATH=groop/src /workspaces/vbpub/.venv/bin/python -m pytest groop/tests/test_actions.py -q
PYTHONPATH=groop/src /workspaces/vbpub/.venv/bin/python -m pytest groop/tests -q
mapfile -d '' pyfiles < <(find groop/src/groop groop/tests -name '*.py' -print0)
/workspaces/vbpub/.venv/bin/python -m py_compile "${pyfiles[@]}"
```

Final validation: focused 127 passed; full 531 passed; full-source compile of
97 Python files passed.
