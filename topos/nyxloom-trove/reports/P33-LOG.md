# P33 - Release Smoke Harness Work Log

Keep this file current while working. It is a resumability artifact, not a
place for private reasoning. Record observable actions, decisions, and next
steps so another controller can continue after a session limit.

## Context

- Branch: feat/topos-p33-release-smoke
- Worktree: .worktrees/-topos-p33-release-smoke
- Base commit: 885f1c6 (docs(topos): carve P32 P33 next slices)
- Package: P33 Release Smoke Harness
- Current objective: Implement rootless `python -m topos.acceptance smoke` for deterministic safe-path evidence

## Timeline

Append newest entries at the bottom.

```text
2026-07-10 UTC
- Action: Created branch and worktree from local main
- Commands: git worktree add -b feat/topos-p33-release-smoke .worktrees/-topos-p33-release-smoke main
- Files changed: N/A
- Result: Branch created at base commit 885f1c6
- Follow-up: Implement acceptance module

2026-07-10 UTC (continued)
- Action: Created topos/src/topos/acceptance.py smoke harness module
- Commands: write_file, py_compile, PYTHONPATH=topos/src python3 -m topos.acceptance smoke --cgroup-root ... --json
- Files changed: topos/src/topos/acceptance.py
- Result: Module compiles; smoke run on fixture: 8 entities, 572 metrics, all checks pass, exit 0
- Follow-up: Create tests

2026-07-10 UTC (continued)
- Action: Created topos/tests/test_acceptance.py with 13 tests
- Commands: installed pytest in .venv, ran PYTHONPATH=topos/src python3 -m pytest topos/tests/test_acceptance.py -v
- Files changed: topos/tests/test_acceptance.py
- Result: 13/13 passed, 1.80s
- Follow-up: Update documentation

2026-07-10 UTC (continued)
- Action: Updated MEASUREMENTS.md and OPERATIONS.md
- Commands: edit_file on both files
- Files changed: topos/MEASUREMENTS.md, topos/docs/OPERATIONS.md
- Result: MEASUREMENTS.md has P33 smoke section; OPERATIONS.md has release-smoke command example
- Follow-up: Run full test suite, finalize report

2026-07-10 UTC (continued)
- Action: Ran focused acceptance tests and py_compile
- Commands: python3 -m py_compile topos/src/topos/acceptance.py topos/tests/test_acceptance.py
- Files changed: N/A
- Result: Both files compile cleanly. 13/13 acceptance tests pass.
- Follow-up: Run full topos tests, write P33-REPORT.md, commit

2026-07-10 UTC (controller review)
- Action: Tightened acceptance output before merge
- Files changed: topos/src/topos/acceptance.py, topos/tests/test_acceptance.py, topos/MEASUREMENTS.md, topos/handoff/reports/P33-LOG.md, topos/handoff/reports/P33-REPORT.md
- Result: Replaced non-ASCII output markers with ASCII markers, made JSON output deterministic with sorted compact keys, removed stale date/environment claims, and added tests for sorted pretty JSON
- Validation: PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m pytest topos/tests/test_acceptance.py topos/tests/test_collector.py topos/tests/test_record.py -q -> 34 passed in 10.04s
- Validation: PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m py_compile topos/src/topos/acceptance.py topos/tests/test_acceptance.py -> clean
- Validation: PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m pytest topos/tests -q -> 292 passed in 33.03s
- Follow-up: Commit controller review patch and merge

2026-07-10 UTC (post-merge)
- Action: Merged P33 to main after P32 and ran final combined validation
- Validation: PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m pytest topos/tests -q -> 303 passed in 37.10s
- Validation: PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m py_compile topos/src/topos/daemon/status.py topos/src/topos/acceptance.py topos/src/topos/cli.py topos/tests/test_daemon_status.py topos/tests/test_acceptance.py -> clean
- Validation: PYTHONPATH=topos/src /tmp/p25-venv/bin/python -m topos.acceptance smoke --cgroup-root topos/tests/fixtures/cgroupfs/gstammtisch --replay topos/tests/fixtures/frames/gstammtisch-once.jsonl --json -> exit 0, ok true, 8 entities, 572 metric source labels, wall 0.1794s, RSS 89256 KB
```

## Decisions

- Decision: Use single-file module `topos/src/topos/acceptance.py` with `if __name__ == "__main__"` entry point
  Reason: Per handoff "Use a minimal if __name__ == '__main__' entry point for python -m"
  Impact: Simpler than a package; module is importable and runnable as `python -m topos.acceptance`
- Decision: Use `resource.getrusage()` for CPU/RSS measurements (stdlib only)
  Reason: Avoid subprocess execution requirement; stdlib cross-platform for Linux
  Impact: RSS from getrusage is child max RSS only, which matches P12 prior evidence pattern
- Decision: Host collector and network providers use defaults (real /proc reads) unless overridden
  Reason: The harness is for release smoke on a real host; tests with fixtures inject custom paths via subprocess PYTHONPATH
  Impact: Deterministic test runs use `--cgroup-root` pointing at fixture + host_collector substitution via test subprocess injection
- Decision: Test tests invoke `python -m topos.acceptance` as subprocess with PYTHONPATH
  Reason: Handoff explicitly says "Use PYTHONPATH=topos/src style invocations where a subprocess is the cleanest way"
  Impact: Tests verify the module entry point end-to-end

## Validation

```bash
# Acceptance tests (unit + subprocess)
PYTHONPATH=topos/src /home/vb/volkb79-2/vbpub/.venv/bin/python -m pytest topos/tests/test_acceptance.py -v
# 13 passed in 1.80s

# py_compile
PYTHONPATH=topos/src python3 -m py_compile topos/src/topos/acceptance.py topos/tests/test_acceptance.py
# exit=0

# Smoke run with fixture
PYTHONPATH=topos/src python3 -m topos.acceptance smoke --cgroup-root topos/tests/fixtures/cgroupfs/gstammtisch --replay topos/tests/fixtures/frames/gstammtisch-once.jsonl --json
# {"ok": true, ...} exit=0

# Non-existent replay path
PYTHONPATH=topos/src python3 -m topos.acceptance smoke --cgroup-root topos/tests/fixtures/cgroupfs/gstammtisch --replay /nonexistent/path.jsonl
# exit=1 (replay check fails as expected)
```

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [x] Feature branch committed.
