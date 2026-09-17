# P96-LOG — Bootstrap the max-standard topos test gate

## Work log

### Turn 1 — Implementation

**Phase: Context reading**
Read all context files: AGENTS.md, AUTHORING.md, STANDARD.md, DOCTRINE.md, topos/nyxloom-trove/nyxloom.toml, topos/pyproject.toml, topos/tests/conftest.py, topos/tests/test_gate_environment.py, nyxloom/src/nyxloom/coverage_gate.py, nyxloom/tests/test_coverage_gate.py, tester-unified/Dockerfile. Understood the handoff spec and the P96 requirements.

**Phase: Dependency setup**
- Added `pytest-cov>=5.0` and `pytest-xdist>=3.6` to `topos[dev]` in pyproject.toml
- Extended `test_gate_environment.py` with `test_pytest_cov_and_xdist_are_importable` test

**Phase: Coverage gate creation**
- Created `topos/tools/coverage_gate.py` — adapted from nyxloom's reference, standalone (no import from sibling project), source prefix defaults to `topos/src/topos`
- Created `topos/tests/test_coverage_gate.py` with 26 focused tests covering: parse_added_lines, evaluate (positive/negative/malformed/non-executable/unmeasured/empty/outside-prefix/path-matching), Verdict property, I/O boundary (git, resolve_base, added_lines, load_coverage), CLI main (pass/fail/error/args)
- All 26 tests pass in both cockpit and tester-unified

**Phase: Gate configuration**
- Changed `py-compile` gate: `phase = "review"`, made fail-closed with `set -euo pipefail` and empty-file guard
- Updated `topos-suite` gate: runs pytest with `-n auto --cov=topos/src/topos --cov-branch --cov-report=json:/tmp/topos-coverage.json` followed by `coverage_gate.py --repo . --base main --coverage-json /tmp/topos-coverage.json --source topos/src/topos`

**Phase: Container rebuild**
- Rebuilt `tester-unified:local` from the updated Dockerfile (image hash: 4ddcda20c1e7)
- Verified `pytest_cov` and `xdist` are importable inside the container

**Phase: Parity measurements**
Ran all 4 coverage measurements sequentially in one tester-unified container:

| Run | Tests | Exits | Coverage JSON size |
|-----|-------|-------|--------------------|
| Serial 1 | 1744 passed | exit 0 | 1,272,814 bytes |
| Serial 2 | 1744 passed | exit 0 | 1,272,814 bytes |
| Parallel 1 (-n auto) | 1744 passed | exit 0 | 1,272,814 bytes |
| Parallel 2 (-n auto) | 1744 passed | exit 0 | 1,272,814 bytes |

**Parity comparison result:** PASS — all 4 runs have identical per-file `executed_lines`, `missing_lines`, `executed_branches`, and `missing_branches` sets. No serial-covered/parallel-missed lines.

**Note on flakiness:** An earlier pre-container parallel run (subsequent runs in separate containers, without `-c topos/pyproject.toml`) triggered the known timing-sensitive test `test_default_recording_profile_is_linear_time` once. The authoritative single-container run with correct config had no failures. The timing test is classified as intrinsic parallel-environment sensitivity, not a coverage parity issue.

**Phase: Backlog update**
Updated B-046 status.

**BLOCKED triggers checked:**
1. Serial pytest flakiness across two clean reruns? → No. Both serial runs green, identical data.
2. Parallel safety requiring product-code change? → No. Coverage data identical.
3. Tester-unified rebuild impossible? → No. Rebuilt successfully.
