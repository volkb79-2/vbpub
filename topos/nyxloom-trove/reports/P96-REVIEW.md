# P96-REVIEW — Independent adversarial review

**Reviewer:** Reasonix (adversarial, not the implementer)
**Branch:** feat/topos-P96-max-test-gate
**HEAD:** 9dcdba6e
**Verdict:** APPROVED

## Method

Read all required context (AGENTS.md, AUTHORING.md, STANDARD.md gate-contract +
validation sections, LESSONS.md PL2/PL3, gate-adoption-assessment.md, P96
handoff, REPORT, SELFREVIEW, LOG, COVERAGE-GAPS). Compared three-dot diff
against main. Independently verified every oracle: gate selection, shell exit
propagation, coverage evaluator correctness, source-prefix boundaries, JSON
validation, parity evidence, import identity, timing-test determinism, and
test hollowness. Ran focused tests (43/43 pass) plus the exact declared gate
in tester-unified (1758 passed, coverage_gate exit 0). Ran negative probes:
malformed JSON, missing coverage file, syntax error → all exit non-zero.

## Oracle verification

### O1 — topos[dev] declares pytest-cov + xdist; importable in tester-unified
**PASS.** `topos/pyproject.toml` has `pytest-cov>=5.0` and `pytest-xdist>=3.6`
in `[dev]`. Container test confirms both import: `docker run --rm
tester-unified:local ... python -c "import pytest_cov, xdist"` → OK. Test
`test_pytest_cov_and_xdist_are_importable` in `test_gate_environment.py`
passes in both cockpit and container.

### O2 — Project-owned, unit-tested changed-line evaluator
**PASS.** `topos/tools/coverage_gate.py` (299 lines) adapted from nyxloom's
reference with merge-base/first-parent semantics, fail-closed error handling,
and source-prefix boundary enforcement. 36 unit tests in
`topos/tests/test_coverage_gate.py` covering:

- Positive/negative coverage verdicts
- Malformed JSON records (missing key, non-list, non-int, non-object)
- Real git I/O (e2e test creates temp repo, runs real `git diff`)
- Git failure → `CoverageGateError` → exit 2
- Non-Python files ignored
- Non-executable lines (not in executed ∪ missing) ignored
- Unmeasured Python file → fail
- Empty diff → pass
- Changes outside source prefix ignored
- Source-prefix boundary: `topos/src/topos_evil/` is NOT matched
- Path normalization across spellings
- CLI wiring: pass (exit 0), fail (exit 1), error (exit 2)

All 36 pass in both cockpit and tester-unified. **Negative verified:**
uncovered changed line exits 1; missing/invalid coverage JSON exits 2; git
failure exits 2.

### O3 — Implementation gate runs via fail-closed shell composition
**PASS.** The `topos-suite` argv (nyxloom.toml:64) wraps in two nested bash
layers, both with `set -euo pipefail`. The inner pipeline is:

```
pytest ... -n auto --cov ... && coverage_gate.py ...
```

No pipes, loops, trailing `echo`, or `|| true` mask exits. Verified:

- Pytest failure → `&&` stops, bash exits with pytest's code (container
  smoke test: `-k NONEXISTENT` → exit 5)
- Coverage gate I/O error → exit 2 (container smoke test confirmed)
- Full gate run: 1758 passed, coverage_gate exit 0

The `py-compile` gate (nyxloom.toml:72) uses NUL-delimited `git diff -z |
xargs -0 -r python3 -m py_compile` under `set -o pipefail`. The `|| { rc=$?;
echo FAILED; exit $rc; }` pattern correctly propagates pipeline failures.
Tested: syntax error → xargs exits 123 → gate exits 123 (non-zero ✓). No
.py changes → xargs -r skips → echo "OK" → exit 0. Git failure (nonexistent
ref) → pipeline fails → exit non-zero.

**Minor note (INFO):** xargs translates py_compile's exit 1 to exit 123, so
the "FAILED (exit 123)" message is less diagnostic than "exit 1" would be.
The gate correctly treats any non-zero exit as failure. Diagnostic only.

### O4 — Parity: identical serial and parallel coverage
**PASS.** The LOG records 4 runs (2 serial, 2 `-n auto`) in one container,
all 1,272,814 byte identical JSON. Live gate run confirms the evaluator
produces consistent branch-aware coverage JSON. The self-review repaired the
timing test `test_default_recording_profile_is_linear_time` that flaked under
xdist contention — replaced `time.perf_counter()` with deterministic
operation counting via `monkeypatch`. The repaired test verifies O(N)
linearity via gauge-read counts (2880×20 and 5760×20), immune to CPU load.
All other tests are deterministic and independent of wall-clock timing.

No serial-covered/parallel-missed lines found. The handoff explicitly forbids
recapturing incidental child-process coverage — none was needed.

### O5 — Honest statement and branch coverage baseline
**PASS.** `P96-COVERAGE-GAPS.md` records exact per-file totals: 11,764/13,830
statements (85.06%), 3,374/4,480 branches (75.31%) across 93 source files.
Every file's missing lines and branch pairs are enumerated. No rounding to
100%, no files excluded. Live gate run confirms identical totals in the
container-produced coverage JSON.

### O6 — topos-suite is the only implementation-phase gate
**PASS.** `nyxloom.toml`: `py-compile.phase = "review"`, `topos-suite.phase =
"implementation"`. nyxloom's `select_verification_gate` (gate_runner.py:30-46)
would now select `topos-suite`, resolving the pre-P96 LAUNDERS risk where
`py-compile` sorted first and a valid-syntax canary survived compilation.

## Findings

### F1 (OK) — `topos/tools/` lacks `__init__.py`
`topos/tools/` has no `__init__.py`. The test `from tools import
coverage_gate` works because `PYTHONPATH=topos` puts the project root on
sys.path, making `tools/` an implicit namespace package (Python 3.3+). This
is **not a blocker** — the import works correctly in both cockpit and
tester-unified. But implicit namespace packages are fragile: adding a
`topos/tools/__init__.py` would make the import explicit and independent of
the PYTHONPATH trick. **Recommended follow-up:** add `__init__.py` in the
next healing package.

### F2 (OK) — `PYTHONPATH=topos/src:topos` broadens sys.path
The `topos` directory (project root, not source) is on sys.path so `tools`
can be imported. The `topos/src` entry must sort first for the `topos`
package import to resolve correctly. This is correct as-is but worth
documenting: changing PYTHONPATH ordering would silently break imports.
**No action required.**

### F3 (OK) — py-compile `set -o pipefail` without `-e`
The py-compile gate uses `set -o pipefail` but NOT `set -e`. The `|| { ...;
exit $rc; }` pattern handles exit propagation explicitly, making `-e`
unnecessary. However, if the `||` block's `exit $rc` were accidentally
removed, the gate would silently pass. The test
`test_pycompile_syntax_error_exits_nonzero` guards against regressions.
**No action required.**

## Checks passed (no findings)

- **Gate selection** (O6): topos-suite is the only implementation gate.
  Pre-P96 LAUNDERS risk resolved.
- **Changed-line enforcement**: The evaluator correctly rejects uncovered
  executable lines (exit 1), errors on malformed data (exit 2), and passes
  clean diffs (exit 0). All branches covered by unit tests.
- **Source-prefix boundary**: `topos/src/topos_evil/` correctly rejected.
  Tests `test_rel_to_source_rejects_false_prefix_match`,
  `test_evaluate_ignores_changes_under_false_friend_prefix` pass.
- **Merge-base resolution**: Feature branch uses `git merge-base base HEAD`;
  merge commit uses first parent. Tests
  `test_resolve_base_merge_commit_uses_first_parent`,
  `test_resolve_base_linear_commit_uses_merge_base` pass.
- **Coverage JSON validation**: `_validate_cov_record` checks per-record
  types (valid, missing key, non-list, non-int, non-object). All negative
  tests pass.
- **Import identity**: `test_topos_imported_from_worktree` verifies topos
  resolves from the bound worktree, not image-baked `/src`. Verified live
  in tester-unified: `topos.__file__` →
  `/workspaces/vbpub/.worktrees/feat/topos-P96-max-test-gate/topos/src/topos/__init__.py`.
- **Timing-test determinism**: Wall-clock oracle replaced with operation
  counting. Verified 2880×20 and 5760×20 gauge reads match expectations.
  Deterministic under xdist.
- **No product source changes**: Diff confirms zero changes under
  `topos/src/**`.
- **No hollow tests**: Evaluator tests drive real behavior (real git,
  real subprocess, real coverage JSON). The e2e test creates a real git
  repo and verifies pass/fail/error exits. Negative tests prove the
  failure branches.
- **`# pragma: no cover`** escape hatch preserved (evaluator ignores lines
  excluded from coverage by coverage.py). No new pragmas in this package.

## Gate smoke test (live)

```
$ docker run --rm -v ... tester-unified:local bash -c '...'
=== 1758 passed in 60.94s ===
diff-coverage OK: 0/0 changed executable lines covered (100.0% ≥ 100.0% floor)
Evaluator exit: 0
Files in coverage: 93
Totals: 85.06% statements, 75.31% branches
```

## Summary

All 6 oracles independently verified. The gate selects `topos-suite` (not
`py-compile`), shell composition is fail-closed at every layer, the evaluator
rejects uncovered lines and malformed data, parity is confirmed, the baseline
is honest, and focused tests prove every negative branch. No blockers.

Three informational findings noted (F1–F3); none gate-blocking.
