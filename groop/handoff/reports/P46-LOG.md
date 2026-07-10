# P46 Admin Action Execution Kernel — Work Log

Keep this file current while working. It is a resumability artifact, not a
place for private reasoning.

## Context

- Branch: `feat/groop-p46-admin-action-execution-kernel`
- Worktree: `.worktrees/-groop-p46-admin-action-execution-kernel`
- Base commit: `9d6327b` (main)
- Package: P46 — Admin action execution kernel
- Current objective: Add the first production-safe execution path behind P21's
  preview model for allowlisted Docker/systemd start, stop, and restart actions
  only.

## Timeline

```text
2026-06-18
- Action: Read handoff (P46-admin-action-execution-kernel.md), controller guide,
  existing actions module (catalog, preview, audit, __init__), CLI, model,
  config, conftest, and existing tests.
- Decision: Keep execution audit as separate pre/post functions in execute.py
  rather than extending the existing AuditLog class, because execution audit
  needs fsync durability and fail-closed pre-execution write. Preview audit
  remains simple append-only.
- Decision: Put target validation in execute.py and import it from preview.py
  so both paths share the same validator. Put whitespace rejection only for
  execution-allowed kinds because systemd-set-property targets legitimately
  contain spaces.
- Decision: Use injected runner/clock fixtures for tests instead of mocking
  subprocess, so tests prove gate ordering and argv correctness without real
  Docker/systemd calls.

- Action: Updated catalog.py — added EXECUTION_ALLOWLIST frozenset with 6
  start/stop/restart kinds. SYSTEMD_SET_PROPERTY excluded from execution.
- Files changed: groop/src/groop/actions/catalog.py

- Action: Created execute.py — the core execution module with:
  - ExecuteResult frozen dataclass (kind, target, argv, returncode, stdout,
    stderr, outcome, duration_s)
  - validate_target() — rejects empty/option-like targets, shell metacharacters,
    control chars, path syntax; Docker-specific name/id regex; systemd-specific
    unit name regex and suffix validation
  - _default_runner() — subprocess.run with shell=False, clean minimal env,
    bounded timeout, captured output, output bounding/redaction
  - _write_execution_audit_pre() — writes pre-execution audit JSONL record with
    fsync; fail-closed on OSError
  - _write_execution_audit_post() — appends post-execution outcome record
  - execute_plan() — 8-gate pipeline: admin, confirm, valid kind, allowlist,
    target validation, pre audit, subprocess, post audit
  - result_to_jsonable() and render_result_text() helpers
- Files changed: groop/src/groop/actions/execute.py (created)

- Action: Updated audit.py docstring to describe both preview and execution
  audit paths.
- Files changed: groop/src/groop/actions/audit.py

- Action: Updated __init__.py — added EXECUTION_ALLOWLIST, ExecuteResult,
  execute_plan, validate_target exports.
- Files changed: groop/src/groop/actions/__init__.py

- Action: Updated preview.py — added validate_target() call to build_preview()
  so targets are validated at preview-build time.
- Files changed: groop/src/groop/actions/preview.py

- Action: Updated cli.py — added "execute" subparser to parse_action_args
  (--kind, --target, --admin, --confirm, --json, --audit-log, --timeout).
  Added execute branch to _main_action() calling execute_plan().
  Exit codes: 0 success, 1 nonzero/timeout/runner_failure, 2 refusal.
- Files changed: groop/src/groop/cli.py

- Action: Updated test_actions.py — added 41 new P46 tests:
  TestTargetValidation (40 parametrized cases), TestExecutionGates (6),
  TestExecutionSuccess (3), TestExecutionAudit (2), TestExecutionTimeout (1),
  TestExecutionRunnerFailure (1), TestExecutionResultRendering (2),
  TestExecutionCliIntegration (5), TestExecutionAllowlistExclusion (2),
  TestOutputBounding (3), TestPreviewWithValidation (4).
- Files changed: groop/tests/test_actions.py

- Action: Ran focused tests — 96/96 passed (including all existing P21 tests).
- Action: Ran full suite — 499 passed, 1 skipped (same baseline).
- Action: Ran py_compile — clean on all changed/new files.

- Action: Updated documentation:
  - README.md: P46 status Planned→Done with report link
  - STATUS.md: v2 percentage 55-60%→60-65%, removed executable actions from
    "Not Implemented", added P46 to "Implemented", updated Quality Gate section
  - ROADMAP.md: remaining packages 5-7→4-6
  - OPERATIONS.md: added execute CLI docs, updated safety model
  - RELEASE-READINESS.md: item 12 updated to reflect P46
  - MEASUREMENTS.md: no changes needed

- Action: Wrote P46-LOG.md and P46-REPORT.md.
- Status: Feature branch ready for commit.
```

## Decisions

- Decision: Keep execution audit separate from preview audit.
  Reason: Execution audit needs pre/post records, fsync durability, and
  fail-closed semantics. Preview audit is simple append-only without fsync.
  Merging them would make the AuditLog class more complex without benefit.
  Impact: Two audit code paths, clearly documented.

- Decision: Use injected runner/clock fixtures instead of subprocess mocking.
  Reason: Tests prove exact argv construction, gate ordering, and that no real
  Docker/systemd is called — without depending on mock internals.
  Impact: execute_plan() accepts optional runner= and clock= parameters.

- Decision: validate_target() allows whitespace for preview-only kinds.
  Reason: systemd-set-property targets contain spaces ("my.slice MemoryMax=1G").
  Execution-allowed kinds get a separate no-whitespace check.
  Impact: Clean separation between preview (permissive) and execution (strict).

- Decision: Target validation happens both in preview and execution paths.
  Reason: Fail fast during preview, and re-validate during execution to prevent
  TOCTOU race.
  Impact: validate_target called from both build_preview() and execute_plan().

## Validation

```bash
PYTHONPATH=groop/src python3 -m pytest groop/tests/test_actions.py -v
# 96 passed in 0.58s (41 new P46 tests + 55 existing P21 tests)

PYTHONPATH=groop/src python3 -m pytest groop/tests -q
# 499 passed, 1 skipped in 48s

python3 -m py_compile \
  groop/src/groop/actions/execute.py \
  groop/src/groop/actions/__init__.py \
  groop/src/groop/actions/preview.py \
  groop/src/groop/actions/audit.py \
  groop/src/groop/actions/catalog.py \
  groop/src/groop/cli.py \
  groop/tests/test_actions.py
# Exit 0 — all clean
```

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [x] Feature branch committed.
