# assay-P05 — language-free evaluation core — LOG

**Status:** DONE. Gate green.
**Branch:** `feat/assay-P05-language-free-evaluation-core`
**Base:** `main` at `0958efdf` ("rule(assay): P05 readiness findings -- A-096/A-097, land before dispatch").
**Commit:** filled in after this LOG is committed (see the commit that follows this one on the branch).

## Gate

`tester-unified`, run in the FOREGROUND against the working tree with the container-side path substituted for the host bind mount:

```
$ cgroup_parent="dev-background.slice"
$ docker run --rm --cgroup-parent="$cgroup_parent" \
    -w /workspaces/vbpub/.worktrees/assay-P05-language-free-evaluation-core/assay \
    -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
    bash -c 'export PYTHONPATH=src && /opt/tester-venv/bin/python -m pytest tests -q --cov=src/assay --cov-branch --cov-report=term-missing'
........................................................................ [  7%]
........................................................................ [ 15%]
........................................................................ [ 22%]
........................................................................ [ 30%]
........................................................................ [ 38%]
........................................................................ [ 45%]
........................................................................ [ 53%]
........................................................................ [ 60%]
........................................................................ [ 68%]
........................................................................ [ 76%]
........................................................................ [ 83%]
........................................................................ [ 91%]
........................................................................ [ 98%]
..........                                                               [100%]
================================ tests coverage ================================
_______________ coverage: platform linux, python 3.14.6-final-0 ________________

Name                                             Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------------------------------
src/assay/__init__.py                               10      0      0      0   100%
src/assay/adapters/__init__.py                       1      0      0      0   100%
src/assay/adapters/base.py                          12      0      0      0   100%
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
src/assay/evaluate.py                               73      0     24      0   100%
src/assay/git.py                                    28      0      8      0   100%
src/assay/measurability.py                          23      0      4      0   100%
src/assay/registry.py                               22      0      4      0   100%
src/assay/runner.py                                111      0     16      0   100%
src/assay/verdict.py                               318      0    168      0   100%
--------------------------------------------------------------------------------------------
TOTAL                                             1327      0    504      0   100%
946 passed in 10.65s
GATE_EXIT=0
```

Baseline before this package: 896 passed, 1169 stmts / 452 branches, 100%.
This package adds 50 tests, 158 statements (`adapters/__init__.py`, `adapters/base.py`,
`evaluate.py`, `registry.py` new; `runner.py` +55, `verdict.py` +49), 52 branches
— all still 100% statement and branch coverage.

`git status --porcelain --ignored` after the run (filtered of cache noise) shows
only intended changes: four new modules (`adapters/__init__.py`, `adapters/base.py`,
`evaluate.py`, `registry.py`), five modified source files (`runner.py`,
`schemas/verdict.schema.json`, `verdict.py`, and three test modules touched for
the A-096 field addition), six new R1 verdict fixtures, and five new test
modules plus `conftest.py`'s harness additions — nothing else left in the
worktree.

## Delivered

| Work item | File | Notes |
|---|---|---|
| 1 | `src/assay/adapters/__init__.py`, `src/assay/adapters/base.py` (new) | `LanguageAdapter` protocol — EXACTLY the five attributes + three methods A-097 names (`name`, `source_globs`, `excluded_dir_names`, `requires_span_attribution`, `external_tools`, `is_test_path`, `has_executable_code`, `normalize_coverage_key`). No `statement_spans`/`inject_*`/`generate_mutants`. |
| 2 | `src/assay/evaluate.py` (new) | `evaluate_coverage` — the pure four-way union (executed/missing/excluded/non-executable), `missing_lines`/`files_missing_coverage` per A-096, `has_executable_code`-gated NoCode handling for files absent from the coverage artifact, `normalize_coverage_key`-mediated matching. |
| 1, 2 | `src/assay/registry.py` (new) | `Registry` (frozen), `new_registry(*adapters)`, `get_adapter(registry, language)` — explicit, no default, no process-global state; unknown language raises `ERROR`/`BAD_LANE_CONFIG`. |
| 3 | `src/assay/runner.py` | `evaluate_r1` — P02's two measurability guards, then P03's `EMPTY_COVERAGE` guard, short-circuiting into a `NO_MEASUREMENT` `Claim` (never raising, mirroring `execute_command`'s contract); otherwise the four-way union and an R1 `Claim`. Genuinely structural `AssayError`s (`FORMAT_MISMATCH`, `UNREADABLE_ARTIFACT`, `GIT_FAILED`) propagate uncaught. |
| A-096 | `src/assay/verdict.py` | `Coverage` gains `missing_lines: Mapping[str, frozenset[int]]` and `files_missing_coverage: tuple[str, ...]`, both required (no default), fully validated in `__post_init__` (mapping shape, non-empty keys/sets, positive line numbers, sorted+unique paths), serialised in `to_dict()`. |
| A-096 | `src/assay/schemas/verdict.schema.json` | `$defs/coverage` gains `missing_lines`/`files_missing_coverage` as required properties, closed (`additionalProperties: false`). |
| 5 | `tests/fixtures/verdicts/pass.json`, `fail.json` | Updated with the two new fields; call sites in `test_verdict_serialises.py`, `test_verdict_schema_rejects.py`, `test_verdict_claims.py` updated (`grep -c "Coverage("` confirmed every site). |
| 4 | `tests/test_evaluate_four_way_union.py` (new, 7 tests) | O1 — each union member isolated with a fixture engineered so dropping that member's effect flips the result, plus one combined fixture. |
| 4 | `tests/test_evaluate_language_free.py` (new, 11 tests) | O2 half 1 — non-`.py` extension/synthetic syntax, source-root/excluded-dir/test-path/glob filtering, `normalize_coverage_key`, `has_executable_code`'s NoCode distinction. |
| 4 | `tests/test_registry.py` (new, 6 tests) | O2 half 2 — explicit registration, unknown-language refusal, no default fallback, fresh-instance isolation. |
| O4/O3 | `tests/test_runner_evaluate_r1.py` (new, 10 tests) | Real `git_repo`-backed scenarios: R1 PASS, FAIL/UNCOVERED_LINES, FAIL/EXCLUDED_LINES, and the three NO_MEASUREMENT branches, each compared against an independent hand-written fixture and validated against the packaged schema; plus the constant-PASS vacuity guard, the never-raises contract, a propagating structural error, and the real-filesystem `has_executable_code` read path. |
| — | `tests/test_verdict_coverage_missing_locations.py` (new, 16 tests) | `Coverage`'s new-field validation branches. |
| — | `tests/conftest.py` | `make_lane` gains an optional `judge` param; `make_r1_judge`, `FakeAdapter`, `write_coverage_json`, `R1_VERDICT_FIXTURES`/`r1_verdict_fixture` added. |

## The R1 seam — exact shape

```python
# assay/runner.py
def evaluate_r1(
    lane: Lane, *, repo: Path, project_root: Path, base: str,
    adapter: LanguageAdapter,
) -> Claim: ...
```

Called between `build_r0_claim` and `assemble_verdict`, exactly as A-090/A-094
prescribed — no restructuring of either.

## Per-oracle evidence

Every mutation was applied directly to the file, its presence confirmed with
`grep -c` before the run, the local interpreter used for iteration speed
(`PYTHONPATH=src python3 -m pytest tests -q`, verified identical pass count to
the container gate at baseline — 946 — and at the final green run above), then
reverted and re-verified byte-identical via `diff` against a saved copy (A-067).

### O1 — the four-way union: executable requires execution; excluded fails; non-executable passes; outside-diff doesn't matter

* **Mutation 1 (member 1 dropped — `total_covered += len(changed_exec)` instead
  of `len(changed_exec & file_cov.executed)`, i.e. treat every changed
  executable line as covered regardless of whether it actually executed).**
  `grep -c "MUTATION: member1 dropped"` → 1.
  **Real result: 5 failed** — `test_a_changed_missing_line_fails_the_union`,
  `test_all_four_members_combine_in_one_evaluation`,
  `test_a_non_python_extension_and_synthetic_syntax_reaches_the_same_result`,
  `test_r1_fail_uncovered_lines_matches_the_hand_written_fixture`,
  `test_a_hard_coded_constant_pass_would_fail_the_fail_fixture_comparison`
  (the last failed on a `KeyError` reaching for a `reason_code` that no longer
  existed because the mutation had already turned the real result into a PASS
  — a second-order confirmation, not a clean assertion failure, still a real
  detection, the same shape P04's LOG records for an analogous mutation).
* **Mutation 2 (member 2 dropped — the excluded-and-disallowed branch made a
  no-op).** `grep -c "MUTATION: member2 dropped"` → 1.
  **Real result: 3 failed** —
  `test_a_changed_excluded_line_fails_even_at_100_percent`,
  `test_all_four_members_combine_in_one_evaluation`,
  `test_r1_fail_excluded_lines_matches_the_hand_written_fixture`.
* **Mutation 3 (member 3 dropped — `changed_exec = lines` instead of
  `lines & executable`, i.e. count every changed line toward the denominator
  including non-code lines).** `grep -c "MUTATION: member3 dropped"` → 1.
  **Real result: 5 failed** —
  `test_a_changed_excluded_line_fails_even_at_100_percent`,
  `test_allow_excluded_opts_a_lane_back_into_passing`,
  `test_a_changed_line_outside_executable_and_excluded_never_fails_it` (the
  test built specifically for this member),
  `test_all_four_members_combine_in_one_evaluation`,
  `test_r1_fail_excluded_lines_matches_the_hand_written_fixture`.
* **Mutation 4 (member 4 dropped — `changed_missing = file_cov.missing`
  instead of `changed_exec & file_cov.missing`, i.e. use the file's WHOLE
  missing set instead of intersecting with the diff).**
  `grep -c "MUTATION: member4 dropped"` → 1.
  **Real result: 2 failed** —
  `test_a_pre_existing_uncovered_line_outside_the_diff_is_invisible` (the
  test built specifically for this member),
  `test_all_four_members_combine_in_one_evaluation`.

### O2 — the same result for a non-Python language; an unknown language is refused, never defaulted

* **Mutation 1 (a hardcoded `.py` filter — `if not path.endswith(".py"):
  return False` in `_is_considered`, replacing the `source_globs` check).**
  `grep -c "MUTATION: hardcoded .py filter"` → 1.
  **Real result: 16 failed** — every test in `test_evaluate_four_way_union.py`
  that reaches a `.zzz` file, every language-free test in
  `test_evaluate_language_free.py`, and five of `test_runner_evaluate_r1.py`'s
  git-backed scenarios — the entire synthetic-language fixture set disappeared,
  exactly O2's negative.
* **Mutation 2 (registry default fallback — `get_adapter` returns
  `next(iter(registry.adapters.values()))` on a `KeyError` instead of
  raising, when the registry is non-empty).**
  `grep -c "MUTATION: default fallback"` → 1.
  **Real result: 2 failed** —
  `test_an_unregistered_language_is_refused_not_defaulted`,
  `test_two_fresh_registries_do_not_share_state` (the second failed because
  the mutation made an unrelated adapter silently answer for a name it was
  never registered under — the exact "unknown language silently selects
  [something]" hazard, generalised beyond "Python" specifically since no real
  adapter exists yet for this package to special-case).

### O3 — runner integration emits a schema-valid R1 claim with exact totals, missing locations, and reason; independent fixtures for PASS/FAIL

* **Mutation (constant PASS — `evaluate_r1`'s final `Claim` hardcodes
  `status=Outcome.PASS, reason_code=None` regardless of the real
  `CoverageEvaluation`).** `grep -c "MUTATION: constant PASS"` → 1.
  **Real result: 4 failed** —
  `test_r1_fail_uncovered_lines_matches_the_hand_written_fixture`,
  `test_r1_fail_excluded_lines_matches_the_hand_written_fixture`,
  `test_a_hard_coded_constant_pass_would_fail_the_fail_fixture_comparison`,
  `test_evaluate_r1_reads_real_file_text_for_a_file_missing_from_coverage`.
  Notably `Verdict.__post_init__`'s own rollup-agreement check does NOT catch
  this one (unlike some of P04's mutations): `assemble_verdict` derives
  `outcome` from the (already-corrupted) claims, so the verdict is internally
  consistent but wrong — proving the INDEPENDENT fixture comparison is doing
  real work here, not merely duplicating the model's own self-check.

### O4 — the three guards short-circuit before evaluation; the coverage block is omitted, not zeroed

* **Mutation 1 (`DIRTY_TREE` guard call replaced with `pass`).**
  `grep -c "MUTATION: DIRTY_TREE guard disabled"` → 1.
  **Real result: 1 failed** —
  `test_r1_no_measurement_dirty_tree_matches_the_hand_written_fixture`, and
  the failure is exactly the negative's own words: with the guard gone, the
  (uncommitted, invisible-to-`git diff`) change was never seen, so
  `evaluate_r1` proceeded to a real evaluation and produced a fully-populated
  PASS claim (`covered=4, considered=1`, `exit_code=0`) instead of
  `NO_MEASUREMENT`/`DIRTY_TREE` with the coverage block absent — a different
  reason_code AND a present-but-wrong coverage block where the fixture has
  none, not merely a wrong boolean.
* **Mutation 2 (`EMPTY_COVERAGE` guard call replaced with `pass`).**
  `grep -c "MUTATION: EMPTY_COVERAGE guard disabled"` → 1.
  **Real result: 2 failed** —
  `test_r1_no_measurement_empty_coverage_matches_the_hand_written_fixture`,
  `test_evaluate_r1_never_raises_for_any_of_the_three_no_measurement_causes`.
  With the guard gone, a genuinely empty (`{"files": {}}`) coverage artifact
  reached the four-way union directly and produced `FAIL`/`UNCOVERED_LINES`
  (every changed line in `pkg/mod.zzz` treated as missing-from-coverage, since
  no file entries existed at all) instead of `NO_MEASUREMENT`/`EMPTY_COVERAGE`
  — a concrete instance of the exact ambiguity DESIGN-GUIDE §6 names ("0/0
  reads identically whether nothing was measured or everything passed").
  `BASE_IS_HEAD`'s own guard is the same three-line shape as `DIRTY_TREE`'s
  (a single `measurability.check_base_is_head` call inside the same `try`),
  not separately mutated here — the mechanism proven by mutations 1 and 2
  (the `try`/`except AssayError` catch-and-return-Claim pattern) is identical
  for all three guards, and the O4 fixture/test pair for
  `BASE_IS_HEAD` (`test_r1_no_measurement_base_is_head_matches_the_hand_written_fixture`)
  independently proves that specific branch is reachable and correctly
  shaped, which is what a fourth mutation would have re-confirmed.

## Self-review

### Would each oracle's test fail if the behaviour were removed?

Yes for all four, demonstrated above by 9 mutations (not estimated), each
with its presence confirmed before the run and its absence confirmed after
revert via byte-identical `diff`. Every mutation produced at least one real
failure; several (O1 mutations 1 and 3, O2 mutation 1, O4 mutation 2)
produced failures spanning multiple independent test modules.

### What is MISSING from the diff the handoff asked for

Nothing in `## Work`. Items 1–5 are honoured as written:

1. **Adapter protocol** — exactly the five attributes and three methods
   A-097 names, verified by re-reading A-097's own list against
   `adapters/base.py` line by line before writing a single test.
2. **Pure four-way evaluation** — `evaluate.py`'s `evaluate_coverage`,
   producing `missing_lines`/`files_missing_coverage` in A-096's exact shape.
3. **Runner integration** — `evaluate_r1`, calling P02's two guards then
   P03's guard, short-circuiting on any of the three (O4, A-090).
4. **Fake adapter, independent fixtures** — `FakeAdapter` in `conftest.py`;
   six new hand-written full-verdict JSON fixtures under
   `tests/fixtures/verdicts/`, three of them the NO_MEASUREMENT branches.
5. **Mutation evidence** — the nine mutations above, all real, all recorded
   with actual (not estimated) failure counts.

### What I implemented that the handoff did not ask for, with justification

* **`has_executable_code`'s NoCode consumption is scoped to exactly one
  case**: a considered, adapter-recognised, non-test file with NO entry at
  all in the coverage artifact. It is never consulted for a file the
  coverage artifact DID measure. This is a real design decision the handoff
  named the method for but did not itself pin down the call site of — I
  inferred it from DESIGN-GUIDE §2/§11's srdm NoCode discussion (a file with
  zero instrumentable functions should not be flagged `files_missing_coverage`)
  and from A-096's own wording ("contributing executable lines" — implying
  something must decide whether a missing file WOULD have contributed any).
  Flagging this as the one non-obvious interpretation call in this package:
  a different implementer could plausibly have consulted `has_executable_code`
  for every considered file (not only coverage-absent ones), which would
  change nothing observable when the file DOES have a coverage entry (its
  `executed`/`missing` sets already answer the question) but would add an
  unnecessary filesystem read on the hot path. I chose not to, and
  `test_has_executable_code_is_never_consulted_for_a_file_coverage_did_measure`
  pins this down explicitly.
* **`normalize_coverage_key` is applied symmetrically as a general path
  reconciliation hook**, not literally only to "coverage artifact keys" —
  the core builds `cov_by_repo_path` by normalizing every `profile.files`
  key through the adapter, then matches against the diff's OWN path
  spelling (already `git diff`-relative) directly, never normalizing the
  diff side. This is the one place A-097's own naming (`normalize_coverage_key`)
  and DESIGN-GUIDE §11's "prefix-boundary reconciliation... is universal and
  lives in the core; the language-specific prefix strip... is an adapter
  hook" needed a concrete call-site decision I had to make: I read it as
  "the coverage side is stripped to match the diff side", not "both sides
  meet in some third normalized space." Documented in `adapters/base.py`'s
  own docstring (the "path contract" section) so P06/P08 do not have to
  re-derive it.
* **`evaluate_r1` resolves the coverage artifact path against
  `project_root`, a parameter distinct from `repo`** (the git repository
  root). Neither A-090 nor A-094 named this split explicitly, but P04's own
  `execute_command` always runs with `cwd=project_root`, and `source_roots`
  resolve against the project root (A-049) — a monorepo where `assay.toml`
  sits below the git top level needs both roots available, so I added the
  parameter rather than assuming they coincide. All of this package's own
  tests happen to use the same directory for both (the simplest case), so
  this split is exercised structurally (the parameter exists and is threaded
  through) but not by a test where the two directories actually differ —
  see "known-weak spots" below.

### Known-weak spots, stated plainly

* **`project_root != repo` is untested.** Every git-backed test in
  `test_runner_evaluate_r1.py` passes the same path for both. The `_resolve_artifact_path`
  helper and its use are simple enough (`project_root / artifact` when
  relative) that I judge the risk low, but a future package adopting a real
  monorepo layout should add a test where `assay.toml`'s project root is a
  subdirectory of the git repository before relying on this.
* **`excluded_dir_names` and `source_globs` interact with `Path(path).parts[:-1]`
  in a way that is only tested for a SINGLE excluded segment
  (`"vendor"`) one level deep.** A file two levels under an excluded
  directory, or a source root itself named for an excluded segment, is not
  separately tested. The implementation (`any(part in adapter.excluded_dir_names
  for part in Path(path).parts[:-1])`) is a straightforward membership test
  over every path segment, so I judge this low-risk, but it is untested at
  more than one depth.
* **The four-way union's priority when a line legitimately belongs to
  neither `executed`/`missing`/`excluded` in a REAL adapter's output (as
  opposed to a coverage artifact's own guarantee) is not separately
  defended** — this package trusts `FileCoverage`'s own disjointness
  invariant (documented in `coverage_parsers/model.py`, enforced by every
  P03 parser) rather than re-validating it. If a future parser ever violated
  that invariant (a line appearing in two of the three sets), this package's
  behaviour would be whichever set's Python `set` union/intersection
  happens to resolve first — not specified, not tested, and not this
  package's contract to police (P03 owns `FileCoverage`'s construction).
* **`evaluate_r1`'s docstring states the `lane.judge`-is-fully-resolved
  precondition in prose only**, with no defensive check in code (an `assert`
  was written, then deliberately removed — it would have been dead code
  under the gate's 100%-branch-coverage requirement, since `config.py`'s
  loader already guarantees this for any lane that declares R1 rigor, and
  P05 has no scope to add a test that manufactures a config-loader-bypassing
  broken `Lane`+`JudgeConfig` combination without also duplicating P00/P01's
  own coverage of that guarantee). A caller violating the precondition gets
  an `AttributeError`, not a typed `AssayError` — the same "trust the
  contract, do no defensive re-validation" choice `measurability.py` and
  `coverage.py` already make explicitly in their own docstrings.

### Decision ids I could not honour as written

None. A-090 (P02's two guards + P03's guard wired ahead of the four-way
union, short-circuiting on any of the three) is discharged by `evaluate_r1`'s
`try`/`except AssayError` structure. A-092 (frozen `kw_only` dataclasses,
`errors.AssayError` raised directly, no locally-defined exception type) is
honoured throughout `adapters/base.py`, `evaluate.py`, `registry.py`, and the
`verdict.py` additions — `grep -n "^class.*Error"` across all four finds
nothing; every rejection in `registry.py` raises `AssayError` directly, and
every rejection in `evaluate.py` and `verdict.py`'s `Coverage.__post_init__`
is a bare `ValueError` (matching the existing `Coverage`/`Claim`/`Verdict`
construction-time-rejection house style already established by P01, not a
new pattern). A-096 (the two additive `Coverage` fields, always present,
never conditionally omitted) is discharged exactly as specified, including
updating `pass.json`/`fail.json` and every `Coverage(` call site named in the
handoff. A-097 (the exact eight-member adapter surface) is discharged and
cross-checked line-by-line against the decision text before any test was
written.
