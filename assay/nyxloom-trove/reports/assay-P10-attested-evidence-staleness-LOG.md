# assay-P10 — attested evidence staleness — LOG

**Status:** DONE. Gate green.
**Branch:** `feat/assay-P10-attested-evidence-staleness`
**Worktree:** `/workspaces/vbpub/.worktrees/assay-P10-attested-evidence-staleness/assay`
**Base:** `main` at `aa5b28c7` ("rule(assay): P10 readiness findings -- A-110/A-111, land before dispatch").
**Commits:**
- `2b13ecef` ("feat(assay): P10 -- attested evidence staleness, never verified")
- `911af565` ("test(assay): P10 -- pin A-110's remap independently of the outer catch")

## Gate

`tester-unified`, run in the FOREGROUND against the working tree with the container-side path substituted for the host bind mount:

```
$ cgroup_parent="dev-background.slice"
$ docker run --rm --cgroup-parent="$cgroup_parent" \
    -w /workspaces/vbpub/.worktrees/assay-P10-attested-evidence-staleness/assay \
    -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
    bash -c 'export PYTHONPATH=src && /opt/tester-venv/bin/python -m pytest tests -q --cov=src/assay --cov-branch --cov-report=term-missing; echo GATE_EXIT=$?'
........................................................................ [  5%]
........................................................................ [ 11%]
........................................................................ [ 17%]
........................................................................ [ 23%]
........................................................................ [ 29%]
........................................................................ [ 34%]
........................................................................ [ 40%]
........................................................................ [ 46%]
........................................................................ [ 52%]
........................................................................ [ 58%]
........................................................................ [ 63%]
........................................................................ [ 69%]
........................................................................ [ 75%]
........................................................................ [ 81%]
........................................................................ [ 87%]
........................................................................ [ 92%]
........................................................................ [ 98%]
.................                                                        [100%]
================================ tests coverage ================================
_______________ coverage: platform linux, python 3.14.6-final-0 ________________

Name                                             Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------------------------------
src/assay/__init__.py                               10      0      0      0   100%
src/assay/adapters/__init__.py                       1      0      0      0   100%
src/assay/adapters/base.py                          29      0      8      0   100%
src/assay/adapters/go.py                           177      0     76      0   100%
src/assay/adapters/python.py                       107      0     34      0   100%
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
src/assay/registry.py                               22      0      4      0   100%
src/assay/runner.py                                118      0     18      0   100%
src/assay/verdict.py                               370      0    206      0   100%
--------------------------------------------------------------------------------------------
TOTAL                                             1917      0    746      0   100%
1241 passed in 35.64s
GATE_EXIT=0
```

Baseline before this package: 1184 passed, 1810 stmts / 710 branches, 100%.
This package adds **57 tests, 107 statements, 36 branches** — still 100%
statement and branch coverage. Per-file delta: `attestation.py` (new)
+100/+34, `runner.py` +7/+2 (the `evidence`/`declared_evidence` parameters
and their coverage guard). No other module's statement/branch count moved
(`verdict.py`, `schemas/verdict.schema.json`, and `config.py`, all forbidden,
are untouched and unchanged — confirmed both by `git diff --stat` against
those paths coming back empty and by their identical stmt/branch counts
against the P09 baseline).

```
$ git diff --stat -- src/assay/config.py src/assay/verdict.py src/assay/schemas
(empty)
$ git status --porcelain -- .
M  assay/src/assay/runner.py
M  assay/tests/conftest.py
A  assay/src/assay/attestation.py
A  assay/tests/fixtures/attestations/empty_reviewed_paths.json
A  assay/tests/fixtures/attestations/missing_producer.json
A  assay/tests/fixtures/attestations/not_valid_json.json
A  assay/tests/fixtures/attestations/reviewed_paths_not_all_strings.json
A  assay/tests/fixtures/attestations/reviewed_paths_not_array.json
A  assay/tests/fixtures/attestations/top_level_not_an_object.json
A  assay/tests/fixtures/attestations/well_formed_example.json
A  assay/tests/fixtures/verdicts/evidence_current.json
A  assay/tests/fixtures/verdicts/evidence_declared_missing.json
A  assay/tests/fixtures/verdicts/evidence_never_declared.json
A  assay/tests/fixtures/verdicts/evidence_stale.json
A  assay/tests/test_attestation_evaluate.py
A  assay/tests/test_attestation_load_declared.py
A  assay/tests/test_attestation_pipeline_integration.py
A  assay/tests/test_attestation_record.py
A  assay/tests/test_runner_assemble_verdict_evidence.py
A  assay/tests/test_verdict_evidence_artifacts.py
```

Every touched/added path is inside `scope.touch`. `src/assay/verdict.py`,
`src/assay/schemas`, and `src/assay/config.py` were never opened for writing.

## What was built

* `src/assay/attestation.py` (new) — three layers, deliberately separate:
  * `AttestationRecord` (frozen `kw_only`, A-092) — the three fields an
    attestation claims: `producer`, `attested_commit`, `reviewed_paths`.
  * `parse_attestation`/`load_attestation_file` — pure format loading, no
    git involved. Malformed JSON, a non-object top level, a missing
    required key, a `reviewed_paths` that is not an array of strings, or a
    value `AttestationRecord` itself refuses all raise
    `ERROR`/`UNREADABLE_ARTIFACT`. Proven against **static, committed**
    fixtures (`tests/fixtures/attestations/*.json`) because this layer's
    defects are git-independent.
  * `evaluate_attestation` — the git-dependent core (A-110). Two private
    helpers, `_check_ancestor_or_equal` and `_check_reviewed_paths_exist`,
    each wrap every `git.run` failure (`merge-base`/`cat-file`) and remap it
    to `ERROR`/`UNREADABLE_ARTIFACT` **at the check itself** — proven
    directly, calling these two private functions with no `evaluate_
    attestation` involved (`test_attestation_evaluate.py`'s
    "itself remaps" tests). `evaluate_attestation`'s own outer `try`/`except
    AssayError` is what turns any such exception into an `Evidence` VALUE
    rather than letting it escape as a raised exception — the two layers do
    different jobs (fix the reason code; turn the exception into a value),
    proven independently by mutating each one separately (see O3 below). A
    descendant attested commit reaches the identical `UNREADABLE_ARTIFACT`
    via ordinary comparison (`merge_base != attested_commit`), no exception
    involved. Once ancestry and path-existence are both proven,
    `git diff --name-only attested_commit head` names the changed paths; any
    declared reviewed path in that set renders `NO_MEASUREMENT`/
    `STALE_ATTESTATION` with the full payload preserved; none renders
    `PASS`. `record=None` renders `NO_MEASUREMENT`/`MISSING_ATTESTATION`
    with no payload.
  * `load_attested_evidence` (A-111) — the orchestration entry point:
    accepts `declared: Sequence[EvidenceDeclaration]` as a DIRECT parameter
    (never from `assay.toml`/`config.py`, both untouched), rejects a
    duplicate `(source, key)` identity with `ERROR`/`BAD_LANE_CONFIG` before
    reading anything, rejects a non-`"attested"` source the same way (A-085
    — no adjudicated loader exists), and resolves each declared key against
    `attestations_dir/<key>.json`. One broken attestation file (missing,
    malformed, or a bad attested commit) renders `ERROR`/`UNREADABLE_ARTIFACT`
    for THAT key alone; the rest of the declared list is unaffected.
* `src/assay/runner.py` — `assemble_verdict` gains `evidence: tuple[Evidence,
  ...] = ()` and `declared_evidence: tuple[EvidenceDeclaration, ...] = ()`,
  both defaulting to empty tuples so every existing caller (through P09) is
  byte-for-byte unaffected (proven: `tests/test_runner_verdict_fixtures.py`
  is untouched and still green). Evidence statuses fold into the same
  `rollup` claims already use. A new guard — the identical shape as the
  existing rigor-coverage guard — refuses `ERROR`/`BAD_LANE_CONFIG` BEFORE
  constructing a `Verdict` if `evidence` and `declared_evidence` do not
  cover each other exactly, rather than reaching `Verdict`'s own bare
  `ValueError`.
* Fixtures:
  * `tests/fixtures/attestations/*.json` — six static malformed-shape files
    plus one well-formed example, for format-loading tests independent of
    git state.
  * `tests/fixtures/verdicts/evidence_{never_declared,declared_missing,
    current,stale}.json` — four hand-written full verdict artifacts (O4).

## Per-oracle sections

### O1 — HEAD and ancestor attestations both produce current evidence, `verified_by_assay=false`, fields preserved

**Positive proof.** `test_attestation_evaluate.py`:
`test_an_attestation_naming_head_itself_is_current`,
`test_an_ancestor_attestation_with_unchanged_reviewed_paths_is_current`.

**Mutation evidence (A-067).**

| Mutation | What it does | Failing tests |
|---|---|---|
| Exact-HEAD-only (`if attested_commit != head:` replacing the merge-base comparison) — O1's own negative | rejects the legitimate ancestor fixture | **6** |
| `verified_by_assay=True` on the PASS return path — O1's own negative | marks a loaded review "verified" | **8** (caught immediately by `Evidence.__post_init__`'s own pre-existing guard, unmodified by this package) |

Verified via `grep -c` on the `MUTATION-O1a`/`MUTATION-O1b` markers before
each run (both `1`), and `git diff --stat` came back empty after each
`git checkout -- src/assay/attestation.py` revert.

### O2 — a changed reviewed path stales; a change outside stays current; a missing attestation is `MISSING_ATTESTATION`

**Positive proof.** `test_a_changed_reviewed_path_renders_stale`,
`test_a_change_outside_every_reviewed_path_remains_current`,
`test_a_missing_attestation_renders_no_measurement_missing_attestation`.

**Mutation evidence.**

| Mutation | What it does | Failing tests |
|---|---|---|
| `if record.attested_commit != head:` replacing the path-membership check — "commit inequality alone stales" | stales the outside-path fixture | **4** |
| `if False:` replacing the path-membership check — "ignoring path changes" | passes the changed-path fixture | **2** |

Same `grep -c`/revert-and-diff discipline as O1.

### O3 — descendant/unrelated/malformed attested commit and a missing reviewed path are `ERROR`/`UNREADABLE_ARTIFACT`; duplicate declarations are `ERROR`/`BAD_LANE_CONFIG`; no attestation path creates a `Claim`

**Positive proof**, one committed test per named case (`test_attestation_evaluate.py`):
`test_a_descendant_attested_commit_renders_unreadable_artifact`,
`test_an_unrelated_attested_commit_renders_unreadable_artifact_not_git_failed`
(a real orphan branch inside the same `tmp_path` repo — verified empirically
that `merge-base` on it exits 1),
`test_a_malformed_attested_commit_ref_renders_unreadable_artifact_not_git_failed`
(verified empirically that `merge-base` on a garbage ref exits 128),
`test_a_reviewed_path_missing_at_the_attested_commit_renders_unreadable_artifact`,
`test_one_missing_reviewed_path_among_several_still_renders_unreadable_artifact`.
`test_attestation_load_declared.py`:
`test_a_duplicate_declared_identity_is_rejected_before_any_attestation_is_read`.
Claim-source closure: `test_claim_source_is_closed_to_computed_only`,
`test_attestation_module_never_imports_claim`.

**Mutation evidence.**

| Mutation | What it does | Failing tests |
|---|---|---|
| `pass` replacing `_check_no_duplicate_declarations(declared)` — "duplicate collapse" | duplicate identities pass through | **1** |
| Outer `try`/`except AssayError` in `evaluate_attestation` removed entirely — "letting GIT_FAILED propagate uncaught" | descendant/unrelated/malformed/missing-path all raise instead of returning a value | **7** |
| (additional, beyond the handoff's three named negatives) inner `try`/`except` in `_check_ancestor_or_equal` removed, OUTER catch left intact | proves the two layers are independently meaningful: the direct-call tests (`test_check_ancestor_or_equal_itself_remaps_*`) still catch a raw `GIT_FAILED` reaching THAT function's own boundary even though the final `Evidence` the full pipeline produces would still look correct | **2** (both are the dedicated direct-call tests; every other test, including the full-pipeline ones, still passes — the outer catch backstops the observable outcome) |

Verified via `grep -c` on each `MUTATION-O3-*` marker (each `1`) before
running, and `git diff --stat` clean after every revert.

### O4 — hand-written full artifacts distinguish never-declared, declared-but-missing, current, and stale evidence, validated against schema v2

**Positive proof.** `test_verdict_evidence_artifacts.py`: four fixture-match
tests (one per state), plus `test_no_two_of_the_four_fixtures_are_equal`,
`test_missing_and_stale_carry_different_reason_codes_despite_both_being_no_measurement`,
`test_never_declared_and_declared_missing_are_distinguishable` — these three
assert directly on the four **hand-written fixture files**, independent of
any production code, which is O4's own primary defense (the fixtures
themselves must not collapse, regardless of what the producer does).

**Mutation evidence** (a corroborating check against the live producer, since
`evaluate_attestation` is a real second producer of the "stale" shape, not
only the schema/model): collapsing the STALE branch's return to drop its
payload and use `MISSING_ATTESTATION` instead of `STALE_ATTESTATION` — O4's
own negative ("stale with missing") — failed **2** tests
(`test_a_changed_reviewed_path_renders_stale`,
`test_a_stale_attestation_flows_through_to_an_overall_no_measurement_verdict`).
The four `test_verdict_evidence_artifacts.py` fixture-match tests
themselves are unaffected by this mutation (expected — they construct
`Evidence` directly, never through `evaluate_attestation`; their own
defense is the fixture-equality assertions above, not producer mutation).

Verified via `grep -c` on `MUTATION-O4` (`1`) and a clean revert diff.

### `assemble_verdict`'s own new guard (beyond the four named oracles, but load-bearing)

**Mutation evidence.** `if False:` replacing the
`missing_evidence or surplus_evidence` guard in `runner.assemble_verdict`
failed **2** tests
(`test_missing_declared_evidence_is_refused_...`,
`test_surplus_evidence_is_refused_...`) — both now hit `Verdict`'s own bare
`ValueError` instead of the intended `AssayError`/`BAD_LANE_CONFIG`,
demonstrating exactly why the guard exists (the same reasoning the
pre-existing rigor-coverage guard already established). `git diff --stat`
clean after reverting `src/assay/runner.py`.

## Self-review

**Would each oracle's test fail if the behaviour were removed?** Yes for
all four, plus the runner guard — every named negative (and one I added,
the inner/outer remap split) was applied as a REAL code mutation, not
merely asserted in prose; the suite was rerun with the mutation actually
landed (verified by `grep -c` on a marker string before running); a
non-zero failing count was recorded; and the mutation was reverted with
`git checkout --`, confirmed clean via `git diff --stat`/`git status
--porcelain`, each time.

**What's missing vs. the handoff?** Nothing I can identify against
O1-O4, A-092/A-110/A-111 as written. Work items 1-5 are all covered:
(1) the attestation format loads into `Evidence`; (2) equal-or-ancestor
then path-scoped comparison, A-110's corrected trap honored exactly
(unrelated/malformed/descendant all render the SAME `UNREADABLE_ARTIFACT`,
proven together in one test file); (3) the declared list is a direct
parameter, `config.py`/`assay.toml` untouched, duplicates validated within
the supplied list itself; (4) four full independent artifacts for
undeclared/missing/current/stale; (5) ancestry (all three named failure
shapes plus the missing-reviewed-path case), path scoping, non-laundering,
and exact evidence identity are all broken and their failure counts
recorded.

**What did I add beyond it, with justification?**
* A fourth structural check inside `evaluate_attestation`/
  `_check_reviewed_paths_exist`: a declared reviewed path that never
  existed at the attested commit renders `ERROR`/`UNREADABLE_ARTIFACT`.
  O3's own text names this explicitly ("a missing reviewed path"), but the
  handoff's Work item 2 prose only elaborates the three ancestry shapes —
  this is the fourth case O3 names but Work item 2 doesn't spell out
  mechanically, implemented via `git cat-file -e <commit>:<path>`, the same
  catch-and-remap discipline as the ancestry check.
* Rejecting a non-`"attested"` declared source inside
  `load_attested_evidence` with `ERROR`/`BAD_LANE_CONFIG` (A-085's own
  "adjudicated evidence remains reserved with no registry" made into an
  active refusal here rather than silent mishandling) — untested territory
  the handoff doesn't mention, since nothing else in this build ever
  declares `source="adjudicated"`, but a caller COULD pass one by mistake,
  and failing loudly beats silently producing nothing for that identity
  (which would then trip the OTHER new guard, in `runner.assemble_verdict`,
  with a less specific message).
* `runner.assemble_verdict`'s evidence-coverage guard (missing/surplus
  identities refused before construction) — not named by any of O1-O4, but
  the exact same reasoning the PRE-EXISTING rigor-coverage guard already
  established for `claims`/`declared_rigor` (its own docstring's own
  argument: refuse before reaching `Verdict`'s bare, uncatchable
  `ValueError`). Proven with its own mutation (2 failures) above.
* Direct, `evaluate_attestation`-bypassing tests of
  `_check_ancestor_or_equal`/`_check_reviewed_paths_exist` (private
  functions, imported directly — precedent: `test_evaluate_attribute_line.py`
  already imports `assay.evaluate._attribute_line`,
  `test_verdict_timestamp_agreement.py` already imports
  `assay.verdict._TIMESTAMP_RE`). Added specifically because the FIRST
  version of `evaluate_attestation`'s outer catch was broad
  (`except AssayError:`, no reason-code inspection), which meant a mutation
  removing ONLY the inner remap would have been invisible to any test that
  only reads the final `Evidence` value — these two tests close that gap
  and are what the "additional" mutation row in O3's table above actually
  exercises.
* Six static malformed-attestation-file fixtures under
  `tests/fixtures/attestations/` (missing key, wrong type twice, empty
  array, invalid JSON, non-object top level) plus one well-formed example —
  the handoff names the directory in `scope.touch` but doesn't enumerate
  what belongs there; these are the natural "format loading" fixtures that
  are git-independent, contrasted with the git-dependent fixtures (which
  are all materialised at test time in `tmp_path`, per house style).

**Known-weak spots, stated plainly:**
1. `_check_reviewed_paths_exist` only checks existence AT THE ATTESTED
   COMMIT, never at `head`. A reviewed path that existed at the attested
   commit, was deleted by `head`, is correctly caught (the deletion shows
   up in `git diff --name-only` as a changed path, so it renders `STALE`,
   not `UNREADABLE_ARTIFACT`) — this is intentional and correct — but there
   is no dedicated test naming "deletion specifically, as opposed to a
   content change" as the mechanism; both are exercised identically since
   `git diff --name-only` does not distinguish them and the code doesn't
   need to.
2. `load_attested_evidence`'s file convention (`<key>.json` in a directory)
   is this package's own invention — A-111 explicitly defers "a real
   `assay.toml` declaration mechanism" to later, but does not name a file
   convention for attestation CONTENT either. The `<key>.json` shape is
   documented in the function's own docstring but is not itself validated
   against any external spec; a future real integration may choose a
   different convention entirely, and this package's contract does not
   promise otherwise.
3. `producer` is validated only as a non-empty string — no format
   constraint (email vs. model-id vs. free text) is enforced, matching
   DESIGN-GUIDE §3's "records its producer (model id or human identity)",
   which itself does not specify a format.
4. The two-layer remap (inner functions fix the reason code; the outer
   catch in `evaluate_attestation` turns the exception into a value) means
   the SPECIFIC wording of A-110 ("the ancestry check itself... is caught
   and remapped") is honored literally by the inner functions, and the
   OBSERVABLE guarantee ("never left to propagate") is delivered by the
   outer catch — removing either one alone is independently provable (see
   O3's mutation table), but a reader expecting a SINGLE catch site to do
   both jobs at once will find two instead. This is documented in the
   module's own docstrings and in this LOG, not hidden.

## Nothing was BLOCKED

Every named oracle, and every A-092/A-110/A-111 ruling, was implementable
within `scope.touch` as given. `verdict.py`, `src/assay/schemas`, and
`config.py` were never opened for writing — confirmed by empty `git diff
--stat` against all three at both the initial commit and the final gate
run.
