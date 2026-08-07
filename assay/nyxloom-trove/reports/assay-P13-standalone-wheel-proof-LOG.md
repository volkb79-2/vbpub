# P13 — standalone wheel proof — LOG

**Status:** DONE
**Branch:** `feat/assay-P13-standalone-wheel-proof`
**Worktree:** `/workspaces/vbpub/.worktrees/assay-P13-standalone-wheel-proof/assay`
**Base:** `a42fe02a` (`rule(assay): P13 readiness findings -- A-123 through A-127, land before dispatch`)
**Files touched:** `tests/test_standalone.py` only (new file). `pyproject.toml`,
`README.md`, `tools/standalone-proof.sh` — all left untouched (see "What was
NOT built" below).

## What was built

One new test module, `tests/test_standalone.py` (7 tests), consuming
`conftest.py`'s already-existing, session-scoped `standalone` fixture
(A-123) exactly the way `test_dependency_purity.py`/
`test_verdict_schema_is_packaged.py` already do — no new build/install
mechanism, per the handoff's explicit instruction.

* **O1 — a real `assay run` through the installed console script:**
  - `test_a_real_pass_matches_the_documented_r0_pass_shape` — a lane
    matching `tests/fixtures/verdicts/r0_pass.json`'s own declared shape
    exactly (`argv=["/bin/sh","-c","exit 0"]`, `env={"MOCK_MODE":"true"}`,
    `env_passthrough=[]`) is written into a real, `tmp_path`-materialized
    git repo (the `git_repo` fixture), run via `standalone.run("assay",
    "run", "package", "--file", ..., "--verdict-json", "-")`, and the real
    stdout JSON is compared field-for-field against
    `runner_verdict_fixture("r0_pass")` with `{assay_version, commit,
    started, ended}` excluded (A-124/handoff item 10) — every other field
    matches exactly. Additionally asserts `assay_version == "0.0.0"`
    directly (positive confirmation of A-124/A-069, not just an exclusion)
    and `commit == git_repo.head()`.
  - `test_a_real_nonzero_exit_produces_a_genuine_fail_command_failed` — the
    same mechanism with `exit 3`; asserts real `FAIL`/`COMMAND_FAILED`,
    process exit code 1.
* **O3 — schema validation of a real artifact, a real Python fixture through
  the full pipeline, and Go adapter-level exposure:**
  - `test_a_real_python_fixture_passes_through_the_installed_wheel` —
    copies the already-committed `tests/fixtures/canary/python/{pkg,tests}`
    into a real git repo, lane argv is `[sys.executable, "-m", "pytest",
    "tests", "-q"]` (the SAME pattern `test_canary_python_pipeline.py`/
    `test_mutation_python_pipeline.py` already use — a real ambient
    interpreter with the `test` extra, never the scratch venv, which
    deliberately has nothing but assay itself), run through the installed
    console script. Real PASS, schema-validated.
  - `test_the_installed_wheel_ships_and_exposes_the_go_adapter` —
    adapter-level only (A-126): `standalone.run("python", "-c", ...)`
    imports `assay.adapters.go.GoAdapter` FROM INSIDE the scratch venv
    (proving the wheel ships the `adapters` subpackage, not only
    `cli`/`config`/`errors`/`verdict`) and calls
    `has_executable_code`/`normalize_coverage_key`/`is_test_path` against
    the real, committed `tests/fixtures/canary/go/greet/greet.go` text
    (embedded via `repr()`, never a second committed `.go` file). No Go
    toolchain anywhere in this devcontainer (A-042/A-087) — never a
    genuine `go test` run.
* **O1's negative, corrected per A-124** — package data and console entry
  point, each proven with a FRESH, independently-built mutated wheel (never
  the shared `standalone` fixture, which stays unmutated for every other
  consumer):
  - `test_removing_package_data_ships_a_wheel_whose_schema_cannot_load` —
    build+install both succeed; `assay.verdict.load_schema()` inside the
    mutant venv raises `FileNotFoundError`.
  - `test_removing_the_console_script_ships_a_wheel_with_no_invocable_binary`
    — build+install both succeed; `venv/bin/assay` does not exist;
    invoking it raises `FileNotFoundError` before a process even starts.
  - `test_declaring_a_runtime_dependency_breaks_the_offline_scratch_install`
    — Work item 4's other genuinely-testable break (O2/A-005), proven at the
    WHEEL level rather than only the AST level: `--no-deps` lets the BUILD
    succeed regardless of what's declared; `--no-index` against a clean venv
    then has nothing to satisfy a real `Requires-Dist: requests>=2` with, so
    the INSTALL fails.
  - A shared helper, `_build_and_install_mutant`, reuses `conftest`'s own
    `_build_backend_home`/`_clean_env` (imported, not reimplemented) for the
    two-environment recipe, and `_drop_toml_table` (a table-level analogue
    of `conftest.drop_key`) raises loudly if the named header is not
    present exactly once — never a silent no-op mutation.

## What was NOT built (and why)

* **`pyproject.toml`** — untouched. Confirmed per finding #3: the console
  entry point, package data, and `dependencies = []` are all already
  declared; nothing needed adding.
* **`README.md`, `tools/standalone-proof.sh`** — both left absent, per
  A-127: optional, named by no oracle, and inventing content to fill an
  unused scope slot would be scope creep in the wrong direction.
* **A new `tests/fixtures/standalone/**` directory** — not created at all.
  Every fixture used is either already committed and already
  `collect_ignore_glob`-excluded (`fixtures/canary/python/`,
  `fixtures/canary/go/greet/`) or materialized as a literal string/temp
  file at test time (A-125).

## Gate output (verbatim, real Docker run, foreground, final/authoritative)

```
cgroup_parent="$(/workspaces/vbpub/.worktrees/assay-P13-standalone-wheel-proof/assay/tools/cgroup-parent.sh)"
docker run --rm --cgroup-parent="$cgroup_parent" \
  -w /workspaces/vbpub/.worktrees/assay-P13-standalone-wheel-proof/assay \
  -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'export PYTHONPATH=src && /opt/tester-venv/bin/python -m pytest tests -q --cov=src/assay --cov-branch --cov-report=term-missing; echo GATE_EXIT=$?'
```

```
........................................................................ [  5%]
........................................................................ [ 10%]
........................................................................ [ 15%]
........................................................................ [ 20%]
........................................................................ [ 25%]
........................................................................ [ 30%]
........................................................................ [ 35%]
........................................................................ [ 40%]
........................................................................ [ 45%]
........................................................................ [ 50%]
........................................................................ [ 56%]
........................................................................ [ 61%]
........................................................................ [ 66%]
........................................................................ [ 71%]
........................................................................ [ 76%]
........................................................................ [ 81%]
........................................................................ [ 86%]
........................................................................ [ 91%]
........................................................................ [ 96%]
..............................................                           [100%]
================================ tests coverage ================================
_______________ coverage: platform linux, python 3.14.6-final-0 ________________

Name                                             Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------------------------------
src/assay/__init__.py                               10      0      0      0   100%
src/assay/adapters/__init__.py                       1      0      0      0   100%
src/assay/adapters/base.py                          31      0      8      0   100%
src/assay/adapters/go.py                           181      0     76      0   100%
src/assay/adapters/python.py                       214      0     72      0   100%
src/assay/attestation.py                           100      0     34      0   100%
src/assay/canary.py                                 85      0     22      0   100%
src/assay/cli.py                                    76      0     16      0   100%
src/assay/config.py                                294      0    146      0   100%
src/assay/coverage.py                               32      0      6      0   100%
src/assay/coverage_parsers/__init__.py               1      0      0      0   100%
src/assay/coverage_parsers/cobertura.py             44      0     16      0   100%
src/assay/coverage_parsers/coverage_py_json.py      44      0     18      0   100%
src/assay/coverage_parsers/go_cover.py              69      0     32      0   100%
src/assay/coverage_parsers/lcov.py                  61      0     26      0   100%
src/assay/coverage_parsers/model.py                 16      0      0      0   100%
src/assay/diff.py                                   36      0     16      0   100%
src/assay/errors.py                                 56      0      4      0   100%
src/assay/evaluate.py                              118      0     52      0   100%
src/assay/git.py                                    28      0      8      0   100%
src/assay/measurability.py                          23      0      4      0   100%
src/assay/mutation.py                              144      0     56      0   100%
src/assay/registry.py                               22      0      4      0   100%
src/assay/runner.py                                119      0     18      0   100%
src/assay/verdict.py                               429      0    240      0   100%
--------------------------------------------------------------------------------------------
TOTAL                                             2234      0    874      0   100%
1414 passed in 59.27s
GATE_EXIT=0
```

Baseline before this package: 1407 passed, 2234 stmts / 874 branches, 100%
coverage. This run: **1414 passed** (baseline + this package's 7 new tests,
exactly), **same 2234 stmts / 874 branches at 100%** (unchanged, as
predicted — this package touches no `src/assay` code), `GATE_EXIT=0`.

## Per-oracle section, with mutation evidence (A-067)

For each mutation below: the marker string was verified via `grep -c` to be
present before / absent after the edit; the full foreground gate (same
command as above, `tests -q`, no `--cov`) was re-run against the MUTATED
worktree; failure counts are the real, observed counts; the edit was then
reverted via `git checkout -- pyproject.toml` and confirmed clean via `git
diff --stat` (empty) before the next mutation. This exercised the ACTUAL
committed `pyproject.toml`, not only the isolated copies my own negative
tests build internally — so the counts below also show every PRE-EXISTING
test elsewhere in the suite that depends on the same property.

### O1 — package data (`[tool.setuptools.package-data]`)

`grep -c "package-data" pyproject.toml`: `1` → (removed 3 lines) → `0` →
(reverted) → `1`.

Real gate re-run against the mutated tree: **6 failed, 1408 passed** (of
1414). Failures:
- `tests/test_standalone.py::test_removing_package_data_ships_a_wheel_whose_schema_cannot_load`
  — fails for the CORRECT reason here: this test's own `_drop_toml_table`
  helper reads the ALREADY-mutated real `pyproject.toml` at import time, so
  it can no longer find the header to drop and raises loudly (`"pyproject.
  toml has no table header ... to drop"`) rather than silently doing
  nothing — the exact behavior A-067 wants from a mutation helper.
- `tests/test_verdict_schema_is_packaged.py::test_pyproject_declares_the_schema_as_package_data`
- `tests/test_verdict_schema_is_packaged.py::test_the_schema_is_inside_the_built_wheel`
- `tests/test_verdict_schema_is_packaged.py::test_the_installed_package_resolves_the_schema_from_inside_the_venv`
- `tests/test_verdict_schema_is_packaged.py::test_the_installed_schema_still_rejects_a_malformed_verdict`
- `tests/test_verdict_schema_is_packaged.py::test_load_schema_works_from_the_installed_package`

Five pre-existing tests (all in `test_verdict_schema_is_packaged.py`, whose
own `standalone` fixture instance is now built from the mutated real
`pyproject.toml`, per the shared session scope) fail alongside my own
negative.

### O1 — console entry point (`[project.scripts]`)

`grep -c "project.scripts" pyproject.toml`: `1` → (removed 3 lines) → `0` →
(reverted) → `1`.

Real gate re-run: **5 failed, 1409 passed** (of 1414). Failures:
- `tests/test_dependency_purity.py::test_the_installed_console_script_runs_against_a_fixture_project`
- `tests/test_standalone.py::test_a_real_pass_matches_the_documented_r0_pass_shape`
- `tests/test_standalone.py::test_a_real_nonzero_exit_produces_a_genuine_fail_command_failed`
- `tests/test_standalone.py::test_a_real_python_fixture_passes_through_the_installed_wheel`
- `tests/test_standalone.py::test_removing_the_console_script_ships_a_wheel_with_no_invocable_binary`
  (same loud-raise-instead-of-silent-noop shape as above — this test's own
  `_drop_toml_table` call raises `AssertionError` because the header is
  already gone from the ambient file).

### O1's negative, as originally worded (`fallback_version`) — NOT exercised

Per A-124, independently reconfirmed by this package: `setuptools_scm` is
absent from every interpreter in the real `tester-unified:local` image, so
this code path structurally never executes here and the mutation is
unfalsifiable in this gate image. Not attempted, per the handoff's explicit
correction. What IS asserted instead: `assay_version == "0.0.0"` on the
real installed wheel (see O1 PASS test above) — direct, positive evidence
of A-069/A-124's documented gap, not merely its absence from a comparison.

### O2 — runtime dependency (`dependencies = []` → `["requests>=2"]`)

Not one of P13's own oracles (already covered by `test_dependency_purity.py`
upstream), but Work item 4 explicitly names it as "genuinely testable in
this gate image," and it is proven both by a dedicated new test (above) and
by this manual full-suite mutation:

`grep -c 'dependencies = \["requests'` pyproject.toml: `0` → (mutated) → `1`
→ (reverted) → `0`.

Real gate re-run: **4 failed, 13 errors, 1397 passed** (of 1414 — the
widest-reaching mutation of the three, exactly as expected: the
session-scoped `standalone` fixture's OWN `--no-index` install now fails at
FIXTURE SETUP, which `pytest` reports as an ERROR — not a FAIL — for every
other test in the session that depends on it).

Failures:
- `tests/test_dependency_purity.py::test_pyproject_declares_zero_runtime_dependencies`
- `tests/test_standalone.py::test_removing_package_data_ships_a_wheel_whose_schema_cannot_load`
  (its own mutated build inherits the ambient `requests>=2` dependency too,
  so ITS install fails for an additional, correct reason)
- `tests/test_standalone.py::test_removing_the_console_script_ships_a_wheel_with_no_invocable_binary`
  (same)
- `tests/test_standalone.py::test_declaring_a_runtime_dependency_breaks_the_offline_scratch_install`
  (its own precondition check, `ORIGINAL_PYPROJECT.count("dependencies = []") == 1`,
  correctly fails loudly against the already-mutated ambient file)

Errors (fixture setup failure cascading to every dependent test):
- `tests/test_dependency_purity.py::test_the_built_wheel_declares_no_runtime_requirement`
- `tests/test_dependency_purity.py::test_assay_imports_in_a_venv_that_contains_only_itself`
- `tests/test_dependency_purity.py::test_the_venv_holds_no_third_party_distribution`
- `tests/test_dependency_purity.py::test_the_installed_console_script_runs_against_a_fixture_project`
- `tests/test_standalone.py::test_a_real_pass_matches_the_documented_r0_pass_shape`
- `tests/test_standalone.py::test_a_real_nonzero_exit_produces_a_genuine_fail_command_failed`
- `tests/test_standalone.py::test_a_real_python_fixture_passes_through_the_installed_wheel`
- `tests/test_standalone.py::test_the_installed_wheel_ships_and_exposes_the_go_adapter`
- `tests/test_verdict_schema_is_packaged.py::test_the_schema_is_inside_the_built_wheel`
- `tests/test_verdict_schema_is_packaged.py::test_the_installed_package_resolves_the_schema_from_inside_the_venv`
- `tests/test_verdict_schema_is_packaged.py::test_the_installed_schema_still_rejects_a_malformed_verdict`
- `tests/test_verdict_schema_is_packaged.py::test_the_installed_package_exposes_the_verdict_model`
- `tests/test_verdict_schema_is_packaged.py::test_load_schema_works_from_the_installed_package`

### O3's negative ("omitting schema/fixtures ... or importing a host-only
module fails in the clean venv")

Not exercised as a FOURTH separate real-repo mutation. It is the same
underlying claim as O1's package-data break (schema/data missing at
runtime — already exercised above, with real evidence) and the same
underlying claim as O2's dependency break for "a host-only module" (nothing
outside `assay` itself is ever importable in the scratch venv — already
exercised above). Treating it as a distinct fourth mutation would have
re-tested the identical mechanism a third/fourth time rather than adding
new evidence.

### Clean revert, confirmed each time

After each of the three real mutations above: `git checkout -- pyproject.
toml` then `git diff --stat` — empty every time. Final `git status --short`
before commit shows only the new, untracked `tests/test_standalone.py`.

## Self-review answers

1. **Does removing `fallback_version` produce the byte-identical `0.0.0`
   wheel?** Not re-derived from scratch (A-124 already established this
   with two real wheel builds); this package's own tests reflect the
   CORRECTED claim by asserting `assay_version == "0.0.0"` directly against
   the real, unmutated, correctly-configured installed wheel — never
   attempting the unfalsifiable fallback-version removal.
2. **Local sanity note (not authoritative):** this devcontainer's host
   Python (outside `tester-unified`) DOES have `setuptools_scm` importable,
   so a local (non-gate) run of the PASS-shape test produces
   `assay_version == "0.1.0"` (the real fallback correctly firing) rather
   than `"0.0.0"` — expected and consistent with A-124 (the gap is
   specific to the `tester-unified:local` image, which lacks
   `setuptools_scm` in every interpreter), and exactly why A-040 makes the
   real Docker gate the only ship signal. All numbers in this LOG are from
   the real gate.
3. Nothing named in the handoff was found to be unhonorable; no `BLOCKED`
   was needed. `pyproject.toml`/`README.md`/`tools/standalone-proof.sh`
   were all confirmed unnecessary to touch, per findings #2/#3 and A-127,
   and left untouched.

## Decisions this implementer had to interpret

* **O2's own mutation (Work item 4) was added as a genuine, committed test**
  (`test_declaring_a_runtime_dependency_breaks_the_offline_scratch_install`),
  not only as a manual self-review exercise, since it is cheap, directly
  named as "genuinely testable" by Work item 4, and strengthens A-005's
  proof at the wheel level (previously only proven at the AST level by
  `test_dependency_purity.py`'s tainted-copy check, and via `Requires-Dist`
  absence on the real, correctly-configured wheel — never via an actual
  broken-wheel install attempt before now).
* **O3's schema-validation sub-claim** ("independently validate its
  packaged schema v2") is satisfied by validating the REAL R0 artifacts
  this package's own O1 tests emit against the schema (via `conftest`'s
  shared `validator` fixture), rather than adding a duplicate of
  `test_verdict_schema_is_packaged.py`'s own dedicated schema-loading
  tests — the handoff's own framing ("filling in what these two files do
  NOT yet cover") supports this as non-duplicative.
* **Commit shape**: this package's commit follows the established
  two-commit convention seen on P09–P12 (`feat(assay): P13 -- ...` for the
  implementation, `docs(assay): P13 LOG + successor BRIEF for P14` for the
  reports) rather than one combined commit, since the handoff itself does
  not specify commit granularity and prior packages in this series are
  consistent on this point.
