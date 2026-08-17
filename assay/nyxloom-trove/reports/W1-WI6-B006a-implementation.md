# W1-WI6 / sibling-wave item 5 — documentation and the three A-270 checks

Discharges `W1-CARVE-branch-coverage-and-whole-target.md` §7 item 5 (the
2026-08-17 WIDENED block) and `W1-CARVE-B006a-project-scope.md` §6 WI-6 in
one commit, since both name the same three user-facing documents and the same
three mechanical checks (A-270).

## Files touched

- `assay/README.md`
- `assay/docs/DESIGN-GUIDE.md`
- `assay/docs/CONSUMERS.md`
- `assay/nyxloom-trove/STATE.md`
- `assay/nyxloom-trove/4-backlog.md`
- `assay/nyxloom-trove/2-product-definition.md`
- `assay/CHANGES.md`
- `assay/tests/test_docs_examples_and_vocabulary.py` (new — the three checks)
- `assay/nyxloom-trove/reports/W1-WI6-B006a-implementation.md` (new, this file)

## Defects found in the shipped docs while doing this work — not omissions,
## examples that would not have parsed — fixed as part of this commit

1. `README.md`'s `assay.toml` example was missing four of the eight REQUIRED
   top-level lane fields (`env`, `env_passthrough`, `budget`,
   `allow_argv_append`). It would never have loaded.
2. `README.md`'s and `docs/CONSUMERS.md`'s `redirect_chain` examples both
   used a bare `argv[0]` (`"pytest"` / `"python"`) with no `PATH` in `env`
   or `env_passthrough` — refused at load, `BAD_LANE_CONFIG`.
3. `docs/DESIGN-GUIDE.md` §12's full lane example used SEMICOLON statement
   separators (`scope = "S1"; rigor = [...]; enforcement = "gate"`), which
   is not valid TOML syntax at all. This example could never have parsed,
   at any prior schema version.
4. The same example declared `rigor = ["R0","R1","R2"]` while also declaring
   `judge.canary`, which A-062 refuses as inert config without R3 declared.
5. The same example was `schema_version = 1` with R1/R2 declared and no
   `[isolation]` table — refused under the shipped v2 loader.
6. The same example's R1 declaration was missing `judge.base`, which is
   unconditionally required for `changed_lines`-mode R1 (this predates wave
   1; the prose claiming "all five" required judge fields was also wrong —
   `base` makes it six). Both are now corrected.
7. `docs/DESIGN-GUIDE.md`'s "Consumption without linking" section stated the
   artifact carries `schema_version: 5`; the shipped constant is 6.
8. `docs/DESIGN-GUIDE.md`'s illustrative NO_MEASUREMENT JSON snippet named
   the pre-rename field `changed_executable`; the shipped v6 field is
   `executable` (A-262).
9. `docs/DESIGN-GUIDE.md` §7's "a coverage tool" boundary row said assay
   "never... computes global coverage. Global/branch floors stay `coverage
   --fail-under`'s job" — directly contradicted by B005's whole-target mode,
   which this wave ships. Corrected to state the actual boundary: assay
   never discovers or globs files, only judges a lane's own declared
   `targets`, and an undeclared whole-project floor stays `coverage
   --fail-under`'s job.

None of these were caught before this item because no test had ever
extracted and parsed the documents' own TOML examples against the real
loader — exactly the "check that cannot fail" A-270 exists to close.

## A controller-found consumer trap, added to `docs/CONSUMERS.md`

Driving the shipped CLI against a real fixture repository, the controller
found that `coverage.py` writes its own `.coverage` data file into the
working directory even when `--cov-report` sends the rendered report
elsewhere. Inside a snapshot that file is untracked and unignored, so
assay's post-command dirt check correctly refuses with
`NO_MEASUREMENT`/`DIRTY_TREE` — a diagnosis that reads as "uncommitted work"
rather than "your coverage tool left a data file behind". This is not an
assay defect (the dirt check is behaving correctly); it is documented as a
setup step. Added directly inside the whole-target worked example (the first
R1-adoption point a reader hits), with a `.gitignore` block
(`.coverage`, `.coverage.*`, `.assay/`, `__pycache__/`, `.pytest_cache/`) and
the exact symptom named so a reader searching for `DIRTY_TREE` finds it.

## The three checks — `assay/tests/test_docs_examples_and_vocabulary.py`

Real command output, run from `assay/`:

```
$ PYTHONPATH=src python3 -m pytest tests/test_docs_examples_and_vocabulary.py -v
...
tests/test_docs_examples_and_vocabulary.py::test_every_live_toml_example_parses_with_the_shipped_loader[README.md:156] PASSED
tests/test_docs_examples_and_vocabulary.py::test_every_live_toml_example_parses_with_the_shipped_loader[CONSUMERS.md:95] PASSED
tests/test_docs_examples_and_vocabulary.py::test_every_live_toml_example_parses_with_the_shipped_loader[CONSUMERS.md:166] PASSED
tests/test_docs_examples_and_vocabulary.py::test_every_live_toml_example_parses_with_the_shipped_loader[DESIGN-GUIDE.md:214] PASSED
tests/test_docs_examples_and_vocabulary.py::test_every_live_toml_example_parses_with_the_shipped_loader[DESIGN-GUIDE.md:1109] PASSED
tests/test_docs_examples_and_vocabulary.py::test_at_least_one_live_toml_example_exists_in_each_of_the_three_documents PASSED
tests/test_docs_examples_and_vocabulary.py::test_a_stale_schema_version_is_refused_by_the_same_check_that_passes_the_real_examples PASSED
tests/test_docs_examples_and_vocabulary.py::test_a_malformed_lane_is_refused_by_the_loader_itself PASSED
tests/test_docs_examples_and_vocabulary.py::test_a_marked_fragment_is_excluded_from_the_live_set PASSED
tests/test_docs_examples_and_vocabulary.py::test_every_skip_marker_in_the_real_documents_carries_a_non_empty_reason PASSED
tests/test_docs_examples_and_vocabulary.py::test_materialize_lane_dependencies_ignores_a_non_dict_lanes_table PASSED
tests/test_docs_examples_and_vocabulary.py::test_materialize_lane_dependencies_ignores_a_non_dict_lane_entry PASSED
tests/test_docs_examples_and_vocabulary.py::test_materialize_lane_dependencies_ignores_a_lane_with_no_judge_table PASSED
tests/test_docs_examples_and_vocabulary.py::test_materialize_lane_dependencies_ignores_a_judge_with_no_source_roots_or_canary PASSED
tests/test_docs_examples_and_vocabulary.py::test_materialize_lane_dependencies_creates_declared_source_roots PASSED
tests/test_docs_examples_and_vocabulary.py::test_materialize_lane_dependencies_creates_a_declared_canary_target_only_if_missing PASSED
tests/test_docs_examples_and_vocabulary.py::test_every_snapshot_selection_value_is_documented PASSED
tests/test_docs_examples_and_vocabulary.py::test_every_judge_mode_value_is_documented PASSED
tests/test_docs_examples_and_vocabulary.py::test_every_rigor_level_is_documented PASSED
tests/test_docs_examples_and_vocabulary.py::test_every_coverage_format_is_documented PASSED
tests/test_docs_examples_and_vocabulary.py::test_a_fabricated_vocabulary_value_is_reported_missing_the_broken_control PASSED
tests/test_docs_examples_and_vocabulary.py::test_derived_vocabularies_are_not_accidentally_identical_placeholders PASSED
tests/test_docs_examples_and_vocabulary.py::test_slugify_reproduces_known_preexisting_anchors PASSED
tests/test_docs_examples_and_vocabulary.py::test_every_readme_design_guide_link_resolves PASSED
tests/test_docs_examples_and_vocabulary.py::test_a_dangling_anchor_is_detected_the_broken_control PASSED
tests/test_docs_examples_and_vocabulary.py::test_wave1_new_anchors_are_present_and_resolve PASSED
26 passed, 1 warning in 0.19s
```

Coverage on the new module itself (this project's own rule: new test code
counts):

```
$ PYTHONPATH=src python3 -m pytest tests/test_docs_examples_and_vocabulary.py \
    --cov=tests --cov-branch --cov-report=term-missing -q
tests/test_docs_examples_and_vocabulary.py    180    0    24    0   100%
26 passed, 1 warning in 3.13s
```

## Proof each check can go red — real command output, then restored

**Check 1 (TOML examples parse + declare LANE_SCHEMA_VERSION) — broke
`README.md`'s example to `schema_version = 1`:**

```
tests/test_docs_examples_and_vocabulary.py::test_every_live_toml_example_parses_with_the_shipped_loader[README.md:156] FAILED
E   AssertionError: README.md:156 does not declare the current LANE_SCHEMA_VERSION (2); got 1
```

Restored (`schema_version = 2`); re-ran green.

**Check 2 (closed vocabulary coverage) — redacted both occurrences of
`go-cover` in `docs/DESIGN-GUIDE.md`:**

```
tests/test_docs_examples_and_vocabulary.py::test_every_coverage_format_is_documented FAILED
E   AssertionError: undocumented coverage format(s): {'go-cover'}
```

Restored both occurrences; re-ran green.

**Check 3 (DESIGN-GUIDE anchors resolve) — renamed the "Two R1 modes, one
claim per lane (A-260)" heading `docs/DESIGN-GUIDE.md` links depend on:**

```
tests/test_docs_examples_and_vocabulary.py::test_every_readme_design_guide_link_resolves FAILED
E   AssertionError: dangling README -> DESIGN-GUIDE anchor(s): ['two-r1-modes-one-claim-per-lane-a-260']
tests/test_docs_examples_and_vocabulary.py::test_wave1_new_anchors_are_present_and_resolve FAILED
E   AssertionError: missing DESIGN-GUIDE anchor: two-r1-modes-one-claim-per-lane-a-260
```

Restored the heading; re-ran green (`grep` confirmed no leftover corruption
in any of the three documents before the real full-suite run below).

## Full suite — real foreground output, exit code captured before any pipe

```
$ PYTHONPATH=src python3 -m pytest -q > suite-run-final.txt 2>&1
$ echo "REAL_EXIT_CODE=$?"
REAL_EXIT_CODE=0
$ tail -8 suite-run-final.txt
...........................................                              [100%]
=============================== warnings summary ===============================
../../../../../home/vscode/.venv/lib/python3.14/site-packages/schemathesis/generation/coverage.py:305
  ...DeprecationWarning: jsonschema.exceptions.RefResolutionError is deprecated...
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
2840 passed, 11 skipped, 1 warning in 209.09s (0:03:29)
```

2840 = the 2814-passed baseline this item started from, plus the 26 new
tests in `test_docs_examples_and_vocabulary.py`; the 11 skips are the
pre-existing, unrelated `test_standalone.py` devcontainer skip plus the
existing skip set this branch already carried. No regression.

## Facts from the dispatch brief, verified against shipped code

All verified correct except where noted above (the README's stale "not
whole-project coverage" bullet and `docs/CONSUMERS.md`'s missing
`[isolation]` guidance, which this item exists to fix, and the additional
staleness items found and listed above that the brief did not name).
Specifically confirmed against `src/assay/config.py`, `src/assay/errors.py`,
`src/assay/coverage.py`, `src/assay/verdict.py`, and
`src/assay/schemas/verdict.schema.json`:

- `LANE_SCHEMA_VERSION = 2`, `VERDICT_SCHEMA_VERSION = 6`.
- `SNAPSHOT_SELECTIONS = {"repository", "repository-minus-unsafe-symlinks"}`,
  required on every R1/R2/R3 lane, refused on R0-only.
- `JUDGE_MODES = {"changed_lines", "whole_target"}`, absent means
  `changed_lines`.
- `ReasonCode` gained exactly `UNCOVERED_BRANCHES`, `BRANCH_UNAVAILABLE`,
  `TARGET_NOT_MEASURED` — confirmed against `errors.py`'s enum and its
  inline documentation comments.
- The coverage `pct` combined-percentage formula, the `executable` rename,
  and the `snapshot_policy` schema shape (including its exact verbatim
  schema `description`) were all confirmed by reading the shipped schema
  and model directly, not assumed from the carve prose.
- `cmru/assay.toml` is untouched (still schema v1, R0-only) — confirmed via
  `git log` and a direct read; this commit does not touch it, per the
  wave's own hard constraint and B006(a) WI-1's explicit exclusion.

## What could not be documented honestly

Nothing. Every fact required by the dispatch was either already true of the
shipped code, or (in the nine cases listed above) was corrected as part of
this same commit so the documents match what actually ships.
