# assay-P09 — cause-sensitive canary — LOG

**Status:** DONE. Gate green.
**Branch:** `feat/assay-P09-cause-sensitive-canary`
**Worktree:** `/workspaces/vbpub/.worktrees/assay-P09-cause-sensitive-canary/assay`
**Base:** `main` at `23122f9b` ("rule(assay): P09 readiness findings -- A-105..A-109, land before dispatch").
**Commit:** `08048d56` ("feat(assay): P09 -- cause-sensitive canary proves the gate rejects for cause")

## Gate

`tester-unified`, run in the FOREGROUND against the working tree with the container-side path substituted for the host bind mount:

```
$ cgroup_parent="dev-background.slice"
$ docker run --rm --cgroup-parent="$cgroup_parent" \
    -w /workspaces/vbpub/.worktrees/assay-P09-cause-sensitive-canary/assay \
    -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
    bash -c 'export PYTHONPATH=src && /opt/tester-venv/bin/python -m pytest tests -q --cov=src/assay --cov-branch --cov-report=term-missing; echo GATE_EXIT=$?'
........................................................................ [  6%]
........................................................................ [ 12%]
........................................................................ [ 18%]
........................................................................ [ 24%]
........................................................................ [ 30%]
........................................................................ [ 36%]
........................................................................ [ 42%]
........................................................................ [ 48%]
........................................................................ [ 54%]
........................................................................ [ 60%]
........................................................................ [ 66%]
........................................................................ [ 72%]
........................................................................ [ 79%]
........................................................................ [ 85%]
........................................................................ [ 91%]
........................................................................ [ 97%]
................................                                         [100%]
================================ tests coverage ================================
_______________ coverage: platform linux, python 3.14.6-final-0 ________________

Name                                             Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------------------------------
src/assay/__init__.py                               10      0      0      0   100%
src/assay/adapters/__init__.py                       1      0      0      0   100%
src/assay/adapters/base.py                          29      0      8      0   100%
src/assay/adapters/go.py                           177      0     76      0   100%
src/assay/adapters/python.py                       107      0     34      0   100%
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
src/assay/registry.py                               22      0      4      0   100%
src/assay/runner.py                                111      0     16      0   100%
src/assay/verdict.py                               370      0    206      0   100%
--------------------------------------------------------------------------------------------
TOTAL                                             1810      0    710      0   100%
1184 passed in 34.43s
GATE_EXIT=0
```

Baseline before this package: 1110 passed, 1626 stmts / 640 branches, 100%.
This package adds **74 tests, 184 statements, 70 branches** — still 100%
statement and branch coverage. Per-file delta: `adapters/base.py` +2/+0,
`adapters/go.py` +16/+2, `adapters/python.py` +34/+8, `canary.py` (new)
+85/+22, `verdict.py` +47/+38. No other module's statement/branch count
moved (`config.py` and `errors.py`, both forbidden, are untouched and
unchanged — confirmed both by `git status --porcelain` below and by their
identical stmt/branch counts against the P08 baseline).

```
$ git status --porcelain -- .
M  assay/src/assay/adapters/base.py
M  assay/src/assay/adapters/go.py
M  assay/src/assay/adapters/python.py
A  assay/src/assay/canary.py
M  assay/src/assay/schemas/verdict.schema.json
M  assay/src/assay/verdict.py
M  assay/tests/conftest.py
A  assay/tests/fixtures/canary/go/greet/greet.go
A  assay/tests/fixtures/canary/go/greet/greet_control.out
A  assay/tests/fixtures/canary/go/greet/greet_transformed.out
A  assay/tests/fixtures/canary/python/pkg/__init__.py
A  assay/tests/fixtures/canary/python/pkg/greet.py
A  assay/tests/fixtures/canary/python/tests/test_greet.py
A  assay/tests/fixtures/verdicts/r3_*.json (4 files)
A  assay/tests/test_adapters_go_canary_injection.py
A  assay/tests/test_adapters_python_canary_injection.py
A  assay/tests/test_canary_go_pipeline.py
A  assay/tests/test_canary_python_pipeline.py
A  assay/tests/test_canary_result.py
A  assay/tests/test_verdict_canary_artifacts.py
```

Every touched/added path is inside `scope.touch`. `src/assay/errors.py` and
`src/assay/config.py` were never opened for writing.

## What was built

* `src/assay/canary.py` (new) — the orchestration:
  * `run_python_canary` — the FULL real R0+R1 pipeline (a genuine
    `execute_command` `pytest` subprocess run, then, only if it passed,
    `evaluate_r1`), run twice against a real, disposable git repo built with
    plain `git.run` (already-proven, unmodified `assay.git` boundary): once
    at the caller's already-committed control state, once at a NEW commit
    (`target_path`'s text replaced by the mechanism's own transform, built
    ON TOP of the control) so the transformed run's own R1 diff isolates
    exactly the injected change.
  * `run_go_canary` — R1-only, a pure function over two already-parsed
    `CoverageProfile` values (a committed control coverprofile, and a
    committed coverprofile with the transform's own appended lines marked
    missing), through the real `evaluate_coverage` with the real
    `GoAdapter`. No process, no git repo, no filesystem.
  * `judge_canary` — A-109's mapping, and `build_canary_claim` — the R3
    `Claim` wiring (mirrors `runner.build_r0_claim`).
* `src/assay/adapters/base.py` — the protocol's second deliberate extension
  (after P07's `statement_spans`): `inject_import_break`/
  `inject_uncovered_line`, pure `(text) -> (text, description)`.
* `src/assay/adapters/python.py` — the two mechanisms ported from
  `nyxloom/src/nyxloom/gate_canary.py` (mechanism only, never the
  `path.write_text` call): insert-after-docstring/`__future__` for
  import-break, append-a-never-called-function for uncovered-line.
* `src/assay/adapters/go.py` — the same two mechanisms, BOTH implemented as
  pure trailing appends: Go has no executable top-level statement (unlike
  Python's module body), so import-break is modelled as an appended
  `func init() { panic(...) }` — Go's own analogue of "a side effect that
  fires merely by the package loading".
* `src/assay/verdict.py` — `CanaryResult` (mechanism, description, control
  outcome, transformed outcome, expected/observed reason code — the exact
  construction-time-validation discipline `Coverage` already established)
  and `Claim.canary`, gated to `rigor == "R3"` the same way `Claim.coverage`
  is gated to `rigor == "R1"`.
* `src/assay/schemas/verdict.schema.json` — a `canary` `$def` (mirroring
  `coverage`'s own shape and additive-branch pattern) plus two new `allOf`
  branches on `claim`: `canary` legal only on R3, and never alongside
  `NO_MEASUREMENT`.
* Fixtures: a real, minimal Python pytest project
  (`tests/fixtures/canary/python/pkg/greet.py` + `tests/test_greet.py`) and
  a Go coverprofile pair (`tests/fixtures/canary/go/greet/greet.go` +
  `greet_control.out` + `greet_transformed.out`). The Go TRANSFORMED source
  text is never committed as a second `.go` file — it is computed at test
  time by literally calling the real `GoAdapter().inject_uncovered_line`
  against the committed control text, so the fixture cannot silently drift
  from the adapter it is meant to test; the fixture's independence lives in
  the hand-authored `greet_transformed.out` coverprofile instead, whose line
  numbers were derived by running the real transform once during authoring
  and reading the result back (documented in `test_canary_go_pipeline.py`'s
  own `test_the_appended_function_is_the_real_adapter_transform_...` test,
  which ties the fixture to the live adapter permanently).

## Per-oracle sections

### O1 — the real pipeline rejects the bad half for the expected reason

**Positive proof.** `tests/test_canary_python_pipeline.py` runs a real
`pytest` subprocess twice per mechanism (control, then transformed) against
a real git repo under `tmp_path`:
* import-break: control PASS, transformed R0 FAIL/`COMMAND_FAILED` —
  verified empirically (see module docstring) that `pytest --cov` writes NO
  `cov.json` on a collection error, confirming R1 must not even be attempted
  there.
* uncovered-line: control PASS, transformed R0 PASS but R1
  FAIL/`UNCOVERED_LINES`.

`tests/test_canary_go_pipeline.py` proves the identical shape for Go, R1
only, via the two committed coverprofiles.

**Mutation evidence (A-067).** Three mutations applied to
`assay.canary.judge_canary`, full suite rerun, reverted, `git diff` checked
clean after each:

| Mutation | What it does | Failing tests |
|---|---|---|
| Universal-PASS evaluator (`judge_canary` always returns `(PASS, None)`) | the bad half unconditionally "passes" | **22** |
| Skip the control-validity check (`if result.control_outcome is not PASS: ...` removed) | a broken baseline is never caught | **9** |
| Skip the cause comparison (`if result.observed_reason_code != result.expected_reason_code: ...` disabled) | any non-PASS failure "counts", regardless of cause | **4** |

Verified each mutation actually landed via `grep -c` on the marker string
before running, and `diff canary.py <saved original>` after reverting —
all three came back byte-identical to the pre-mutation file.

### O2 — the canary result attaches as `Claim.canary` (R3), independently

**Positive proof.** `tests/test_verdict_canary_artifacts.py` builds all four
A-109 terminal shapes directly from `Claim`/`CanaryResult`/`Verdict` (never
through `assay.canary`'s own orchestration) and compares the serialised
JSON, by ordinary equality, against four independently hand-written fixture
files (`tests/fixtures/verdicts/r3_*.json`), then validates each against the
shipped schema via the real `jsonschema` validator (never a hand-rolled
checker).

**Mutation evidence.** Removed the schema's own `allOf` branch requiring
`observed_reason_code` whenever `transformed_outcome` is present and
non-`PASS` (replaced its `if` with an unreachable `const`, confirmed via
`grep`, structurally re-validated the schema itself still parses). Rerun:
**1** test failed
(`test_recording_only_rejected_true_would_differ_from_the_expected_artifact`)
— the one test that specifically targets this schema rule. The model-level
twin of this rule (`CanaryResult.__post_init__`'s own
"`observed_reason_code` is required when `transformed_outcome` is
non-`PASS`" check, tested independently in `test_canary_result.py`, never
touches the schema file) was unaffected, confirming the two layers are
genuinely independent enforcement, not one masquerading as two. Reverted;
`diff` against the saved original schema came back clean.

### O3 — malformed/no-op is INCONCLUSIVE; unexpected-pass/wrong-reason is FAIL/SURVIVED

**Positive proof**, one committed test per named case:
* malformed (unrecognised mechanism name) — Python:
  `test_an_unrecognised_mechanism_is_inconclusive_after_a_real_control_run`;
  Go: `test_an_unrecognised_mechanism_is_inconclusive`.
* no-op (a fake adapter's `inject_uncovered_line` returns the SAME text
  unchanged — never naturally true of the real adapter, which always
  appends something) — Python: `test_a_transform_that_produces_no_change_is_inconclusive`;
  Go: same name.
* unexpected pass (an R0-only lane never reaches R1, so the uncovered-line
  transform sails through) —
  `test_an_r0_only_lane_never_catches_the_uncovered_line_transform_and_survives`
  (real pipeline) plus `test_an_unexpectedly_passing_bad_case_survives`
  (pure `judge_canary`).
* wrong reason (a deliberately mislabeled adapter's `inject_import_break`
  actually performs the uncovered-line transform, so R1 catches it for
  `UNCOVERED_LINES` while the mechanism claims `COMMAND_FAILED`) —
  `test_a_mechanism_that_fails_for_the_wrong_reason_survives` (real Python
  pipeline), `test_a_transformed_run_that_fails_for_the_wrong_reason_survives`
  (Go, via a synthetic profile), plus `test_a_failure_for_the_wrong_cause_survives`
  (pure `judge_canary`).

**Mutation evidence.** Collapsed the malformed/no-op branch
(`if result.transformed_outcome is None: return INCONCLUSIVE/...`) into
`return PASS, None` — "treating transform failure or no-op as a successful
rejection", O3's own negative, verbatim. Rerun: **6** failing tests (three
Go, two Python real-pipeline, one pure `judge_canary`). Reverted; `diff`
clean.

## Self-review

**Would each oracle's test fail if the behaviour were removed?** Yes for
all three — see the mutation tables above; every named negative was applied
as a real code mutation (not merely asserted in prose), the suite was
rerun, a non-zero failing count was recorded, and the mutation was reverted
and diff-verified clean.

**What's missing vs. the handoff?** Nothing I can identify against O1-O3,
A-105-A-109 as written. The handoff's Work item 4 also names "control
validity" and "cause matching" as things to break — both covered above
(control-validity mutation: 9 failures; cause-matching/wrong-reason
mutation: 4 failures).

**What did I add beyond it, with justification?**
* `_run_pipeline`, a private helper shared by BOTH the control and
  transformed halves of `run_python_canary` — not named in the handoff, but
  the natural way to guarantee "both halves are judged by the identical
  code path" rather than by two independently-written call sites that could
  silently drift.
* The R0-only-lane "unexpected pass" scenario is a REAL, meaningful
  demonstration (not merely a synthetic trigger) that R1 specifically is
  what catches the uncovered-line canary — a lane that runs tests but never
  checks coverage sails right past it.
* The "wrong reason" scenarios use a deliberately mislabeled/misbehaving
  adapter subclass to reach an otherwise hard-to-construct real-pipeline
  branch — the same technique P05's own `_UnparsableSpanAdapter` already
  established as house style for forcing an edge case through the real
  pipeline rather than only unit-testing it in isolation.
* `_appended_line_range`, needed only because Go's canary has no git diff
  to consult; documented as valid ONLY for a pure-append transform (both of
  `GoAdapter`'s own mechanisms qualify; Python's `inject_import_break`,
  which INSERTS, does not — Python's own canary uses a real git diff
  instead and never calls this helper).
* An explicit "malformed transform" test case using an unrecognised
  mechanism NAME (`"not-a-real-mechanism"`) as the concrete manifestation of
  O3's "malformed transform" — the handoff does not define what "malformed"
  means concretely; since both real injector functions are pure and total
  (never raise), an unresolvable mechanism NAME is the only naturally-
  occurring "malformed" case available, and it is what `_apply_mechanism`
  returning `None` represents.

**Known-weak spots, stated plainly:**
1. `judge_canary` collapses TWO structurally different causes — "the
   control itself was broken" and "the transform was malformed/no-op" —
   onto the SAME `INCONCLUSIVE`/`CANARY_INCONCLUSIVE` pair, because only one
   INCONCLUSIVE reason code exists in the closed vocabulary. A consumer that
   wants to distinguish them must read `CanaryResult.control_outcome`
   vs. `transformed_outcome` directly; the outer `Claim.status`/
   `reason_code` alone cannot.
2. Go's `_GO_R1_MECHANISMS` restricting import-break to "not provable" rests
   on an empirically-VERIFIED Python fact (a real `pytest --cov` run against
   an import-broken package writes no `cov.json`, confirmed locally in this
   devcontainer) generalised BY ANALOGY to Go (`go test -coverprofile`
   against a panicking `init()`) — the Go side of that claim is reasoned,
   not independently confirmed, because no Go toolchain exists anywhere in
   this devcontainer to confirm it against (A-042/A-087, the same
   constraint P08 already documented).
3. The "broken control" test scenarios (both languages) use a LOCALLY
   constructed broken fixture (a failing assertion, or a coverprofile
   reporting the control's own line as missing), never the COMMITTED
   canary fixture itself — the committed fixture is deliberately always
   well-formed, so the broken-baseline path is only exercised through
   synthetic, in-test-only variants.
4. `run_python_canary`'s two `git.run(repo, "commit", ...)` calls rely on
   the target repo already having a configured `user.email`/`user.name`
   (true of every test here, via the established `git_repo` fixture) — a
   caller pointing this at a repo with no configured identity would see
   `git.run` raise `AssayError`/`ERROR`/`GIT_FAILED`, a reasonable and
   already-typed failure mode, but there is no dedicated test naming it.
5. `PYTHONDONTWRITEBYTECODE=1` is REQUIRED in any Python canary lane's
   declared `env` whose source root sits inside the tree `pytest` runs
   against — without it, `.pyc` caches under the source root untrackedly
   dirty the tree between the control commit and the R1 diff, tripping
   `DIRTY_TREE` (discovered empirically while authoring the pipeline
   tests). This is documented in the test module's own comments but is not
   enforced or asserted anywhere in `canary.py` itself — a future caller
   wiring this into a real project's lane needs to know it.

## Nothing was BLOCKED

Every named oracle, and every A-105-A-109 ruling, was implementable within
`scope.touch` as given. `errors.py` and `config.py` were never needed.
