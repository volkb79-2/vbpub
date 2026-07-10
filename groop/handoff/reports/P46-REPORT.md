# P46 — Admin Action Execution Kernel — Report

## Summary

Added the first production-safe execution path behind P21's preview model for
allowlisted Docker/systemd start, stop, and restart actions only. The execution
kernel (`groop/src/groop/actions/execute.py`) implements an 8-gate pipeline
that refuses execution if any gate fails, preserving fail-closed safety for
every execution attempt.

## What Was Built

### 1. Execution allowlist (`catalog.py`)

- New `EXECUTION_ALLOWLIST` frozenset containing only the 6 start/stop/restart
  `ActionKind` members.
- `SYSTEMD_SET_PROPERTY` remains in the catalog for preview but is excluded from
  execution. Any future kind added to the catalog is also excluded unless
  separately opted into the allowlist.

### 2. Target validation (`execute.py` — `validate_target()`)

- Shared by both preview and execution paths.
- Basic safety: rejects empty targets, option-like targets (`--x`, `-x`),
  shell metacharacters (`;&|`$(){}[]<>\"'\\`), control characters, path
  syntax (`/`, `..`).
- Whitespace rejected only for execution-allowed kinds.
- Docker: accepts 64-char hex IDs or `[a-zA-Z0-9_.-]` names (max 128 chars).
- Systemd: accepts `[a-zA-Z0-9@._:-]+` with valid suffixes
  (`.service`, `.slice`, `.scope`, `.target`, `.socket`, `.mount`,
  `.timer`, `.path`) or bare unit names.

### 3. Execution module (`execute.py`)

- `ExecuteResult` frozen dataclass with typed `outcome` field:
  `"success"`, `"nonzero"`, `"timeout"`, `"refusal"`, `"runner_failure"`.
- `_default_runner()`: subprocess.run with `shell=False`, clean minimal
  environment (PATH, LANG), bounded 30s timeout, captured output.
- Output bounding/redaction: stdout/stderr capped at 4096 chars with
  ` ... (truncated)` suffix.
- `execute_plan()`: 8-gate pipeline:
  1. Admin mode enabled
  2. Typed `--confirm EXECUTE`
  3. Valid ActionKind
  4. Kind in EXECUTION_ALLOWLIST
  5. Target passes validate_target()
  6. Pre-execution audit write (fail closed if OSError)
  7. Subprocess execution via runner
  8. Post-execution audit outcome append
- Injected `runner=` and `clock=` parameters for zero-mutation test safety.
- `result_to_jsonable()` and `render_result_text()` helpers.

### 4. Execution audit (`execute.py` — `_write_execution_audit_pre/post`)

- Two-line JSONL records: pre-execution (before subprocess) and post-execution
  (outcome, returncode, duration).
- Pre-execution record written with `fsync()` for durability before the
  subprocess call. If the write fails, execution is refused.
- Records include: ts, user, kind, target, argv, mode="execute", admin, confirm,
  stage="pre"/"post", outcome, returncode, duration_s.

### 5. CLI path (`cli.py`)

- `groop action execute --kind KIND --target TARGET --admin --confirm EXECUTE`
  [--json] [--audit-log PATH] [--timeout SECONDS]
- Exit codes: 0 success, 1 nonzero/timeout/runner_failure, 2 refusal.
- `groop action preview` behavior is fully preserved.

### 6. Tests (`test_actions.py`)

41 new tests covering:
- 40 parametrized target validation cases (Docker names/ids, systemd units,
  shell metacharacters, option-like, empty, whitespace, control chars,
  invalid suffixes, path syntax)
- Gate ordering: each gate independently verified to produce refusal
- Successful execution: injected runner proves exact argv per kind
- Nonzero exit propagation
- Timeout outcome
- Runner failure outcome
- Audit pre/post record contents
- Audit skipped when no path configured
- JSON/text result rendering
- CLI arg parsing, exit codes
- Allowlist exclusion verification
- Output bounding edge cases
- Preview validation integration

## Deviations from Handoff

- **Root check not implemented in the kernel itself.** The handoff says "root in
  production". The execution module does not check UID because:
  1. The execution tests are all injected-runner based and cannot test real root.
  2. The existing pattern in the codebase (e.g., DAMON control) checks root
     at the CLI/subsystem boundary, not in the core module.
  3. The `_default_runner` will naturally fail with permission errors if run
     as non-root, producing a `runner_failure` outcome.
  A future package can add an explicit `--require-root` gate if desired.
- **`systemctl set-property` target validation:** The handoff says to reject
  "invalid systemd unit forms". `SYSTEMD_SET_PROPERTY` is a preview-only kind
  not in the execution allowlist, so its target format (with spaces for
  key=value pairs) is validated with basic safety checks only — execution will
  refuse it at the allowlist gate anyway.

## Out of Scope (preserved)

- Kill, update/pull/recreate, CIU/Compose orchestration, batch actions.
- `systemctl set-property` and memory governance.
- TUI bindings/modals and daemon mutation RPCs.
- Live destructive acceptance on this development host.

## Test Evidence

```bash
$ PYTHONPATH=groop/src python3 -m pytest groop/tests/test_actions.py -v
# 96 passed in 0.58s

$ PYTHONPATH=groop/src python3 -m pytest groop/tests -q
# 499 passed, 1 skipped in 48s
```

## py_compile Evidence

```bash
$ python3 -m py_compile \
    groop/src/groop/actions/execute.py \
    groop/src/groop/actions/__init__.py \
    groop/src/groop/actions/preview.py \
    groop/src/groop/actions/audit.py \
    groop/src/groop/actions/catalog.py \
    groop/src/groop/cli.py \
    groop/tests/test_actions.py
# Exit 0 — all clean
```

## Files Changed

| File | Change |
|---|---|
| `groop/src/groop/actions/catalog.py` | Added `EXECUTION_ALLOWLIST` frozenset, updated docstring |
| `groop/src/groop/actions/execute.py` | **New** — execution kernel module |
| `groop/src/groop/actions/audit.py` | Updated docstring to describe execution audit path |
| `groop/src/groop/actions/__init__.py` | Added new exports to API and `__all__` |
| `groop/src/groop/actions/preview.py` | Added `validate_target()` call in `build_preview()` |
| `groop/src/groop/cli.py` | Added `execute` subparser and `_main_action` branch |
| `groop/tests/test_actions.py` | Added 41 new P46 tests |
| `groop/README.md` | P46 status Planned→Done |
| `groop/docs/STATUS.md` | v2% update, Implemented/Not Implemented, Quality Gate |
| `groop/docs/ROADMAP.md` | Remaining package count 5-7→4-6 |
| `groop/docs/OPERATIONS.md` | Added execute CLI, safety model updates |
| `groop/docs/RELEASE-READINESS.md` | Item 12 updated for executable actions |
| `groop/handoff/reports/P46-LOG.md` | **New** — work log |
| `groop/handoff/reports/P46-REPORT.md` | **New** — this report |

## Known Gaps

1. No explicit `os.geteuid() == 0` check in the execution kernel. Production
   deployments relying on root enforcement should add a wrapper or CLI gate.
2. The handoff mentions "injected runners" for tests and "injected clocks" —
   both are implemented. "Injected identity" is not implemented (the audit
   user is resolved from environment variables). A future package could add
   an explicit identity injection fixture.
3. TUI hotkey (`k`) remains disabled as specified — out of scope.
4. Only 6 action kinds are in the execution allowlist. Adding new kinds
   requires explicit updates to `EXECUTION_ALLOWLIST` in `catalog.py`.

## Proposed Contract Changes

None. All changes are additive with no shared interface modifications.
