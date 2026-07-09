# P33 - Release Smoke Harness Work Log

Keep this file current while working. It is a resumability artifact, not a
place for private reasoning. Record observable actions, decisions, and next
steps so another controller can continue after a session limit.

## Context

- Branch: feat/groop-p33-release-smoke
- Worktree: .worktrees/-groop-p33-release-smoke
- Base commit: 885f1c6 (docs(groop): carve P32 P33 next slices)
- Package: P33 Release Smoke Harness
- Current objective: Implement rootless `python -m groop.acceptance smoke` for deterministic safe-path evidence

## Timeline

Append newest entries at the bottom.

```text
2026-07-22 UTC
- Action: Created branch and worktree from local main
- Commands: git worktree add -b feat/groop-p33-release-smoke .worktrees/-groop-p33-release-smoke main
- Files changed: N/A
- Result: Branch created at base commit 885f1c6
- Follow-up: Implement acceptance module

2026-07-22 UTC (continued)
- Action: Created groop/src/groop/acceptance.py smoke harness module
- Commands: write_file, py_compile, PYTHONPATH=groop/src python3 -m groop.acceptance smoke --cgroup-root ... --json
- Files changed: groop/src/groop/acceptance.py
- Result: Module compiles; smoke run on fixture: 8 entities, 572 metrics, all checks pass, exit 0
- Follow-up: Create tests

2026-07-22 UTC (continued)
- Action: Created groop/tests/test_acceptance.py with 13 tests
- Commands: installed pytest in .venv, ran PYTHONPATH=groop/src python3 -m pytest groop/tests/test_acceptance.py -v
- Files changed: groop/tests/test_acceptance.py
- Result: 13/13 passed, 1.80s
- Follow-up: Update documentation

2026-07-22 UTC (continued)
- Action: Updated MEASUREMENTS.md and OPERATIONS.md
- Commands: edit_file on both files
- Files changed: groop/MEASUREMENTS.md, groop/docs/OPERATIONS.md
- Result: MEASUREMENTS.md has P33 smoke section; OPERATIONS.md has release-smoke command example
- Follow-up: Run full test suite, finalize report

2026-07-22 UTC (continued)
- Action: Ran focused acceptance tests and py_compile
- Commands: python3 -m py_compile groop/src/groop/acceptance.py groop/tests/test_acceptance.py
- Files changed: N/A
- Result: Both files compile cleanly. 13/13 acceptance tests pass.
- Follow-up: Run full groop tests, write P33-REPORT.md, commit
```

## Decisions

- Decision: Use single-file module `groop/src/groop/acceptance.py` with `if __name__ == "__main__"` entry point
  Reason: Per handoff "Use a minimal if __name__ == '__main__' entry point for python -m"
  Impact: Simpler than a package; module is importable and runnable as `python -m groop.acceptance`
- Decision: Use `resource.getrusage()` for CPU/RSS measurements (stdlib only)
  Reason: Avoid subprocess execution requirement; stdlib cross-platform for Linux
  Impact: RSS from getrusage is child max RSS only, which matches P12 prior evidence pattern
- Decision: Host collector and network providers use defaults (real /proc reads) unless overridden
  Reason: The harness is for release smoke on a real host; tests with fixtures inject custom paths via subprocess PYTHONPATH
  Impact: Deterministic test runs use `--cgroup-root` pointing at fixture + host_collector substitution via test subprocess injection
- Decision: Test tests invoke `python -m groop.acceptance` as subprocess with PYTHONPATH
  Reason: Handoff explicitly says "Use PYTHONPATH=groop/src style invocations where a subprocess is the cleanest way"
  Impact: Tests verify the module entry point end-to-end

## Validation

```bash
# Acceptance tests (unit + subprocess)
PYTHONPATH=groop/src /home/vb/volkb79-2/vbpub/.venv/bin/python -m pytest groop/tests/test_acceptance.py -v
# 13 passed in 1.80s

# py_compile
PYTHONPATH=groop/src python3 -m py_compile groop/src/groop/acceptance.py groop/tests/test_acceptance.py
# exit=0

# Smoke run with fixture
PYTHONPATH=groop/src python3 -m groop.acceptance smoke --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl --json
# {"ok": true, ...} exit=0

# Non-existent replay path
PYTHONPATH=groop/src python3 -m groop.acceptance smoke --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch --replay /nonexistent/path.jsonl
# exit=1 (replay check fails as expected)
```

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [x] Feature branch committed.
