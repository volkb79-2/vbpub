# P14 self-hosted conformance — LOG

**Status:** DONE
**Branch:** `feat/assay-P14-self-hosted-conformance`
**Base:** `56c821c2` (`rule(assay): P14 readiness findings -- A-128 through A-133, land before dispatch`)
**Commit:** `461ed28f`
**Worktree:** `/workspaces/vbpub/.worktrees/assay-P14-self-hosted-conformance/assay`

This is the FINAL package in the P00–P14 series. `CGROUP_PARENT_DEV_BACKGROUND`
confirmed `dev-background.slice` before any work started.

## What was built

- `src/assay/verify.py` (new): `verify_document`/`verify_text`/`cmd_verify`/
  `build_verify_parser`. Two stages: (1) reconstruct the `assay.verdict`
  dataclass graph field-by-field from the parsed JSON (schema-equivalent by
  construction — never a hand-rolled JSON-Schema evaluator, and never a
  runtime `jsonschema` dependency, which would violate A-005's zero-runtime-
  deps invariant since `jsonschema` is a *test*-only extra), catching
  `TypeError`/`ValueError`/`KeyError`/`AttributeError` as one failure entry;
  unknown keys at every level (top, claim, evidence, coverage, canary,
  mutation, mutant-outcome) are caught by diffing the raw dict's keys against
  the reconstructed object's own `to_dict()` keys, never by a blind
  `Verdict(**document)` unpack (which would wrongly reject the two legitimate-
  but-derived fields `exit_code`/`argv_modified`); (2) four explicit checks
  against the RAW document (outcome-agrees-with-rollup via
  `verdict.rollup()`, `argv_effective == argv_declared + argv_appended`,
  claims-cover-declared-rigor, evidence-covers-declared-evidence) — done on
  the raw dict rather than the reconstructed object so their own rejection
  branches are independently reachable by a test, not merely a code path
  reconstruction would also hit.
- `src/assay/cli.py`: `verify` wired in as a third subcommand; `main()`
  gained a `stdin` parameter (backward compatible, keyword-only).
- `tests/fixtures/verdicts/evidence_unreadable_artifact.json` (new): the one
  genuinely missing vocabulary pair per A-128 — evidence-level
  `ERROR`/`UNREADABLE_ARTIFACT`.
- `tests/test_verdict_conformance.py` (new, 108 tests): O1's hand-transcribed
  vocabulary audit (independent of `assay.errors.REASON_CODES`) plus O2's
  full accept/reject suite for `assay verify`, every case cross-checked
  against the independent `jsonschema` validator or a fact computed by hand
  in the test (never `assay verify`'s own word alone).
- `tests/test_self_hosting.py` (new, 7 tests): O3/O4. Reads the REAL,
  self-hosted `assay run` artifact nyxloom.toml's own gate script just
  produced (skips gracefully, with a documented reason, when
  `ASSAY_SELF_HOSTING_VERDICT` is unset — a bare developer `pytest` run has
  no such artifact); the A-131 producer-mutation proof (self-contained,
  builds its own mutated wheel from a `shutil.copytree`'d copy); a control
  proving the identical scenario through the real, unmutated wheel; a
  deterministic regression test for a coverage gap this package's own
  restructuring would otherwise silently introduce (see O4 below); three
  small config guards (`asserts == ["tests-pass"]`, `handoff_globs`
  narrowing, cgroup wiring presence).
- `assay.toml`: lane argv gains
  `--ignore=tests/test_self_hosting.py --override-ini=pythonpath=`; `env`
  drops `PYTHONPATH`; stale "P11 upgrades this" comment corrected (A-133).
- `nyxloom-trove/nyxloom.toml`: gate script restructured per A-130 (see
  "Mechanism, and what it took to get there" below); `handoff_globs`
  narrowed to `assay-*.md`; stale "P11 upgrades this" comment corrected
  (A-133).
- `README.md`: deliberately left absent, following A-127's already-
  established precedent (present in `scope.touch` as permission, not
  obligation; named by no oracle) — the same reasoning P13 already applied.
  This LOG and the companion BRIEF serve the "future maintainer" role
  instead.

## Mechanism, and what it took to get there

A-130's illustrative recipe (a `--system-site-packages` scratch venv
prepended to `PATH`) needed real correction once tested against the actual
`tester-unified:local` image, not just reasoned about — all three
corrections below were found by running things for real, not by inspection:

1. **`pyproject.toml`'s own `pythonpath = ["src"]` ini option shadows a
   wheel install regardless of `PATH`/`PYTHONPATH`.** Confirmed empirically
   before writing any TOML: it inserts `"src"` at `sys.path[0]` unconditionally.
   `--override-ini=pythonpath=` (on both the self-hosted lane's own argv and
   the second gate step) is the fix.
2. **A venv created FROM a venv does not inherit the immediate parent's
   site-packages via `--system-site-packages`** — it resolves to the
   *original* venv's own `sys.base_prefix` (here `/usr/local`, not
   `/opt/tester-venv`), so `pytest`/`coverage`/`pytest-cov` (which live only
   in `/opt/tester-venv`'s own site-packages) stayed invisible to a nested
   scratch venv. Nor is `/opt/tester-venv` itself writable by the container's
   run-uid (`pip install` there raises `PermissionError`), so installing the
   wheel directly into it is not an option either. The fix: a `.pth` file
   written into the scratch venv's own site-packages, naming
   `/opt/tester-venv`'s site-packages directory directly — `site.py` appends
   any path found this way to `sys.path` at interpreter startup.
3. **`/opt/tester-venv/bin/python` cannot import `setuptools`, and its own
   `pip` cannot build the wheel** (`BackendUnavailable: Cannot import
   'setuptools.build_meta'`), even with `PYTHONPATH` pointed at a working
   `setuptools`. The wheel build must run via a FRESH, blank scratch venv's
   own `pip` — exactly what `conftest.py`'s already-proven `standalone`
   fixture already does and never deviates from. `setuptools` itself is
   found via the identical fallback `_build_backend_home()` already
   encodes (try the ambient interpreter, fall back to its
   `sys.base_prefix`'s own `python3`), replicated in bash since there is no
   Python process yet to call it from at that point in the script.

None of this required touching `runner.py`, `verdict.py`, `errors.py`, or
`schemas` — all forbidden, and all untouched (verified via `git diff --stat`
throughout, see O3 below).

## Real gate output (verbatim)

This is a two-step gate now (A-130); I could not copy a prior package's LOG
command verbatim, so I constructed the exact resolved argv from
`nyxloom-trove/nyxloom.toml` itself (via `tomllib`, substituting `{worktree}`)
and ran that resolved string, unmodified, in the foreground. Below is the
**exact declared gate**, run for real against `tester-unified:local`, with
`${PIPESTATUS[0]}` captured (never through a bare pipe, per DOCTRINE §1):

```
$ bash -c "$(python3 -c "import tomllib,json; d=tomllib.load(open('nyxloom-trove/nyxloom.toml','rb')); print(d['gates']['tester-unified']['argv'][2].replace('{worktree}','/workspaces/vbpub/.worktrees/assay-P14-self-hosted-conformance'))")"
```

```
Processing ./.
  Preparing metadata (pyproject.toml): started
  Preparing metadata (pyproject.toml): finished with status 'done'
Building wheels for collected packages: assay
  Building wheel for assay (pyproject.toml): started
  Building wheel for assay (pyproject.toml): finished with status 'done'
  Created wheel for assay: filename=assay-0.0.0-py3-none-any.whl size=127877 sha256=e277d90275d82ea6b57e6d5797696a165bfadc4cd03d7d2c8910c9860ca5903e
  Stored in directory: /tmp/pip-ephem-wheel-cache-s6wk1zp4/wheels/23/95/44/7a3569d684ad9c7fb3d70b35df1b5ee2db2144c56871db1e8c
Successfully built assay

[notice] A new release of pip is available: 26.1.2 -> 26.2.1
[notice] To update, run: /tmp/tmp.fbkqnKH6cS/venv/bin/python -m pip install --upgrade pip
Processing /tmp/tmp.fbkqnKH6cS/wheels/assay-0.0.0-py3-none-any.whl
Installing collected packages: assay
Successfully installed assay-0.0.0
tester-unified: PASS (exit 0)
  commit: 56c821c2ec7a90b7a68f926c92b4b44f8418da44
  argv: python -m pytest tests -q --ignore=tests/test_self_hosting.py --override-ini=pythonpath=
.......                                                                  [100%]
7 passed in 13.48s
```

`GATE_EXIT=0` (`${PIPESTATUS[0]}`, captured through `tee`, never a bare
pipe). `assay_version` reads `"0.0.0"` in this real image — `setuptools_scm`
is absent there, matching A-069/A-124/P13's own documented, now
quadruple-confirmed finding. `commit: 56c821c2...` matches the worktree's
real `HEAD` exactly (`git rev-parse HEAD` at gate time), which
`tests/test_self_hosting.py::test_the_self_hosted_lanes_own_verdict_is_independently_valid`
(part of the "7 passed" above) verifies independently, not by trusting the
line above.

### The same gate, instrumented for coverage (diagnostic only — never the declared `argv`)

Following P13's own precedent (append `--cov` flags to the SAME real
command; the gate's own pass/fail is unaffected by coverage flags), run as
three pytest invocations inside one `docker run` — step 1's own coverage
pass, the REAL `assay run` (unmodified, to get a genuine artifact), and
step 2's coverage-appended pass — then one combined `coverage report`:

```
=== STEP 1: self-hosted lane, WITH coverage instrumentation ===
........................................................................ [  4%]
[... 22 lines of dots ...]
..........                                                               [100%]
1522 passed in 61.60s (0:01:01)
STEP1_COV_PYTEST_EXIT=0
=== also produce the REAL verdict.json via assay run ===
tester-unified: PASS (exit 0)
  commit: 56c821c2ec7a90b7a68f926c92b4b44f8418da44
  argv: python -m pytest tests -q --ignore=tests/test_self_hosting.py --override-ini=pythonpath=
STEP1_ASSAY_RUN_EXIT=0
=== STEP 2: independent conformance check against the REAL artifact ===
.......                                                                  [100%]
7 passed in 11.45s
STEP2_PYTEST_EXIT=0
=== COMBINED COVERAGE REPORT ===
Name                                                                     Stmts   Miss Branch BrPart  Cover
------------------------------------------------------------------------------------------------------------
[... 26 files, all 100% ...]
------------------------------------------------------------------------------------------------------------
TOTAL                                                                     2433      0    944      0   100%
```

**Baseline before this package (P13's LOG): 1414 passed, 2234 stmts / 874
branches, 100% coverage.** This run: **1522 + 7 = 1529 passed** (baseline
1414 + this package's 108 new tests in `test_verdict_conformance.py` + 7 new
tests in `test_self_hosting.py`, exactly), **2433 stmts / 944 branches, 100%
coverage** (2234+199 stmts from `verify.py` [194] + `cli.py`'s growth [5];
874+70 branches from `verify.py` [68] + `cli.py`'s growth [2]), `GATE_EXIT=0`
on every step. Both this instrumented run and the exact-declared-argv run
above were executed against the real `tester-unified:local` image, not this
devcontainer's own Python.

One pre-existing, environment-specific test (`test_standalone.py::
test_a_real_pass_matches_the_documented_r0_pass_shape`) fails ONLY in this
devcontainer's own sandbox Python (where `setuptools_scm` resolves a real
version, unlike the real gate image) — confirmed pre-existing (reproduced
identically before touching any file) and irrelevant here since the real
gate image, used for every number above, does not exhibit it.

## Per-oracle section, with mutation evidence (A-067)

For each: the marker/property was verified via a real, observed count
before/after; edits were made via the `Edit` tool only (never `sed`/Python
`write_text`); every mutation was reverted and confirmed clean via `git
diff --stat` (or, for a never-committed new file, byte-identity against the
saved-correct version) before the next.

### O1 — the vocabulary-fixture matrix audit

Deleted `tests/fixtures/verdicts/evidence_unreadable_artifact.json` (the
one file this package adds), reran `tests/test_verdict_conformance.py`:

**2 failed, 104 passed** (down from 108 passed with the fixture present) —
`test_every_required_vocabulary_pair_has_a_covering_fixture` and
`test_error_unreadable_artifact_is_reachable_only_via_evidence_never_a_claim`
both correctly fail; `test_the_audits_own_negative_deleting_a_fixture_leaves_a_pair_uncovered`
(which explicitly simulates this exact deletion) continues to pass, as
expected. Restored the file; reran: 108 passed.

### O2 — `assay verify` accepts valid / rejects malformed-unknown-key-wrong-rollup

Mutated `verify_document`'s first line to `return []` unconditionally (the
exact "verifier that returns success unconditionally" O2's negative names),
via `Edit`, with a grep-able marker comment. Reran
`tests/test_verdict_conformance.py`:

**29 failed, 79 passed** — every REJECT test (unknown-key, wrong-rollup,
missing-field, bad-enum, mismatched-exit-code, etc.) correctly fails; every
ACCEPT test trivially still passes (an always-`[]` verifier "accepts"
anything). `grep -c A067-SELF-REVIEW-MUTATION-MARKER src/assay/verify.py`:
`1` → (reverted via `Edit`) → `0`. Reran: 108 passed;
`git diff --stat -- src/assay/verify.py` empty (untracked-file content
matches the committed version exactly).

### O3 — self-hosting is non-circular; the producer mutation is caught independently

This package's own producer-mutation demonstration (A-131) IS the
self-review evidence, per the handoff's own instruction. Concrete,
mechanical checks performed:

- `grep -c "outcome=Outcome.FAIL," src/assay/runner.py`: `1` **before**
  running `tests/test_self_hosting.py::test_a_universal_pass_producer_mutation_is_wrongly_accepted_by_verify_alone`
  and `test_the_same_scenario_through_the_real_unmutated_wheel_correctly_reports_fail`,
  and `1` **after** — the real, committed, forbidden `runner.py` is
  genuinely never touched; only a `shutil.copytree`'d disposable copy is
  (A-120's own precedent, one level over). `git diff --stat -- src/assay/runner.py`
  is empty both before and after.
- Both tests **pass for real** (2 passed, run in isolation via `-k
  "universal_pass or unmutated_wheel"`), meaning: the mutated wheel really
  does report `PASS` (exit 0) for a command that genuinely exited 7; `assay
  verify`, invoked as a real subprocess against that artifact, really does
  exit 0 (wrongly accepts it — schema-valid, internally self-consistent,
  the lie baked into the claim itself); the test's own independent fact
  (`/bin/sh -c "exit 7"`, run again outside assay entirely, really returns
  7) genuinely disagrees with what the artifact claims; and the SAME
  scenario through the real, unmutated, session-shared wheel
  (`conftest.standalone`) genuinely reports `FAIL`/`COMMAND_FAILED` instead
  — confirming the divergence is the mutation's doing, not the test
  fixture's.
- **A-131's own illustrative example does not work as literally stated —
  found by testing it, not by inspection.** Forcing ONLY
  `assemble_verdict`'s `outcome = rollup(statuses)` line to
  `Outcome.PASS` while leaving `claims` unchanged does not produce a lying
  artifact; it makes `Verdict.__post_init__`'s own (unmodified, unforbidden)
  `_check_outcome_agrees_with_rollup` raise `ValueError` at construction
  time, so the mutated build would crash rather than emit anything —
  verified directly: `Verdict(outcome=Outcome.PASS, claims=(Claim(status=
  Outcome.FAIL, reason_code=COMMAND_FAILED),), ...)` raises `"outcome PASS
  disagrees with the rollup of its claims and evidence (FAIL)"` immediately.
  The mutation actually implemented instead targets `execute_command`'s
  FAIL-branch `CommandResult` (`outcome=Outcome.FAIL, reason_code=
  ReasonCode.COMMAND_FAILED` → `outcome=Outcome.PASS, reason_code=None`) —
  this produces a genuinely self-consistent, schema-valid, `Verdict`-
  constructible lie (the claim itself lies, not just the top-level
  rollup), which is what actually demonstrates O3's negative ("assay verify
  as the only validator... lets the... variant pass"). This is the one
  place I deviated from the handoff's literal illustrative text; the
  deviation is load-bearing and documented here and in
  `tests/test_self_hosting.py`'s own module docstring and the mutation
  constant's own comment.

### O4 — the gate declares only demonstrated capabilities, retains the cgroup helper, 100% coverage

- **`asserts`**: `tests/test_self_hosting.py::test_the_gates_own_asserts_declare_only_tests_pass`
  asserts `asserts == ["tests-pass"]` exactly (A-133) — passes on the real
  file; no mutation performed here since A-133's own reasoning (not
  broadening `asserts`) is what's being protected, and there is nothing to
  toggle without inventing a capability assay does not have.
- **cgroup helper wiring**: removed `--cgroup-parent="$cgroup_parent"` from
  `nyxloom.toml`'s `argv` (leaving `tools/cgroup-parent.sh` itself and the
  rest of the `docker run` invocation untouched), via `Edit`. Reran
  `tests/test_cgroup_parent.py tests/test_self_hosting.py`: **2 failed, 9
  passed, 1 skipped** —
  `test_cgroup_parent.py::test_nyxloom_gate_uses_verified_value_without_a_literal_slice`
  (pre-existing) and this package's own
  `test_self_hosting.py::test_the_cgroup_helper_wiring_is_present_in_the_restructured_gate_script`
  both correctly fail. Reverted; confirmed byte-identical to the
  verified-correct version (`current == correct` via direct comparison);
  reran: 11 passed, 1 skipped.
- **Coverage regression this package's OWN restructuring would otherwise
  introduce, found and closed**: before A-130, `src/assay/__init__.py`'s
  `except PackageNotFoundError: __version__ = "0+unknown"` branch
  ("running from a source tree with no install") was covered only
  INCIDENTALLY — every gate session imported `assay` from a bare,
  never-installed source tree. A-130 makes the self-hosted lane always run
  through a genuinely installed wheel, so that incidental coverage
  disappears; confirmed by running the real gate's own coverage-
  instrumented step 1 BEFORE adding a fix and observing `80%` (missing
  lines 43-44) on `assay/__init__.py`. Closed deterministically with
  `tests/test_self_hosting.py::test_the_version_fallback_still_fires_when_assay_is_not_installed`
  (`monkeypatch`es `importlib.metadata.version` to raise, reloads the
  module, asserts the fallback, then reloads again to restore real state —
  restoration confirmed by the module returning a real version immediately
  after). Confirmed the combined coverage run above is 100% WITH this test
  present; this was found and fixed during this package's own development,
  not left for a future package to discover.
- **100% statement/branch coverage**: shown directly above, both in the
  exact-declared-argv run's own implicit evidence (all 1529 tests pass) and
  the coverage-instrumented diagnostic run (`TOTAL 2433 0 944 0 100%`).

## Anything not honored as written, and why

- **A-131's illustrative mutation example** (see O3 above) — the literal
  text (`assemble_verdict`'s `outcome = rollup(statuses)` line) does not
  produce the artifact O3 describes; verified empirically, and the
  equivalent-in-spirit, actually-working mutation
  (`execute_command`'s FAIL-branch `CommandResult`) was used instead. Same
  file (`runner.py`), same disposable-copy mechanism, same "force
  unconditional PASS" intent — a different exact line.
- **`README.md`** left absent, per A-127's already-established precedent
  (permission in `scope.touch`, not obligation; named by no oracle). This
  LOG and the companion BRIEF fill the "future maintainer" role the
  handoff itself assigns to the BRIEF.
- **The scratch-venv mechanism inside `nyxloom.toml`'s gate script** differs
  materially from A-130's own illustrative text (`--system-site-packages`
  alone) — all three corrections (the `pythonpath` ini override, the `.pth`-
  file site-injection instead of `--system-site-packages`, and building via
  a fresh blank venv rather than `/opt/tester-venv`'s own pip) were found by
  running the actual recipe against `tester-unified:local` and reading the
  real failures, not by re-deriving them from the handoff text alone. The
  underlying MECHANISM A-130 requires (build+install a real wheel first,
  PATH-prepend it, run `assay run` through it, never a source-tree shadow,
  a separate independent second step) is fully honored; only the specific
  shell recipe for achieving "make pytest/coverage/pytest-cov visible to
  the wheel-installed interpreter" changed from the illustrative example.

## Everything else

All four oracles (O1–O4) pass against the real gate. `src/assay/verdict.py`,
`errors.py`, `schemas/`, and `runner.py` were never edited (confirmed via
`git status`/`git diff --stat` throughout — only `verify.py` imports from
`verdict.py`, never edits it). No `# pragma: no cover` (or any spelling of
that token) appears on any line I authored. No test in either new module
depends on wall-clock timing, test order, or worker assignment — every
external state (`tmp_path`, fresh git repos, fresh venvs, `monkeypatch`) is
freshly built and torn down per test.
