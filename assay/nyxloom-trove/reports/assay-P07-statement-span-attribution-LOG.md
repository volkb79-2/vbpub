# assay-P07 — statement-span attribution — LOG

**Status:** DONE. Gate green.
**Branch:** `feat/assay-P07-statement-span-attribution`
**Base:** `main` at `90f9de44` ("rule(assay): P07 readiness findings -- A-100/A-101, land before dispatch").
**Commit:** `7aa474f7`

## Gate

`tester-unified`, run in the FOREGROUND against the working tree with the container-side path substituted for the host bind mount:

```
$ cgroup_parent="dev-background.slice"
$ docker run --rm --cgroup-parent="$cgroup_parent" \
    -w /workspaces/vbpub/.worktrees/assay-P07-statement-span-attribution/assay \
    -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
    bash -c 'export PYTHONPATH=src && /opt/tester-venv/bin/python -m pytest tests -q --cov=src/assay --cov-branch --cov-report=term-missing'
........................................................................ [  6%]
........................................................................ [ 13%]
........................................................................ [ 20%]
........................................................................ [ 27%]
........................................................................ [ 34%]
........................................................................ [ 40%]
........................................................................ [ 47%]
........................................................................ [ 54%]
........................................................................ [ 61%]
........................................................................ [ 68%]
........................................................................ [ 75%]
........................................................................ [ 81%]
........................................................................ [ 88%]
........................................................................ [ 95%]
................................................                         [100%]
================================ tests coverage ================================
_______________ coverage: platform linux, python 3.14.6-final-0 ________________

Name                                             Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------------------------------
src/assay/__init__.py                               10      0      0      0   100%
src/assay/adapters/__init__.py                       1      0      0      0   100%
src/assay/adapters/base.py                          27      0      8      0   100%
src/assay/adapters/python.py                        73      0     26      0   100%
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
src/assay/verdict.py                               323      0    168      0   100%
--------------------------------------------------------------------------------------------
TOTAL                                             1465      0    566      0   100%
1056 passed in 11.46s
GATE_EXIT=0
```

Baseline before this package: 986 passed, 1363 stmts / 512 branches, 100%.
This package adds 70 tests, 102 statements, 54 branches — still 100% statement
and branch coverage. Every changed statement/branch count is confined to
`adapters/base.py` (+15/+8), `adapters/python.py` (+37/+18 net, after also
*removing* two now-provably-dead defensive branches — see "What I added
beyond the handoff, with justification"), `evaluate.py` (+45/+28) and
`verdict.py` (small net growth from two new `Coverage` fields plus a
refactor that DELETED four near-duplicate validation methods in favour of
two shared helpers). No other module's statement/branch count moved,
confirming nothing outside `scope.touch` was touched in a way that changed
behaviour — `runner.py`, `cli.py`, `config.py`, `registry.py` are all
unchanged in the diff.

## Delivered

| File | Notes |
|---|---|
| `src/assay/adapters/base.py` | Adds `StatementSpan` (frozen `kw_only`, construction-time `ValueError` on non-integer/non-positive/backwards ranges) and `LanguageAdapter.statement_spans` (A-101's exact signature). Updated docstrings to state this is the one deliberate post-P05 protocol extension, frozen again after this package. |
| `src/assay/adapters/python.py` | `PythonAdapter.statement_spans`, backed by a module-level `_statement_spans` ported from dstdns's `statement_spans` (compound-statement header trimming via `_COMPOUND_STATEMENT_TYPES`/`_first_nested_line`, decorator spans, match-case pattern spans). Shares P06's `_is_bare_string_statement` rather than re-deriving it. `TryStar`/`Match` are direct tuple members (not `getattr(ast, name, None)`-guarded like dstdns) because `pyproject.toml` requires Python ≥3.11, where both are unconditionally present — the dynamic guard would be a branch this project's own 100%-branch gate could never legitimately exercise either arm of. |
| `src/assay/evaluate.py` | New `_Attribution` enum (`NOT_CODE`, `AMBIGUOUS`) and pure `_attribute_line(line, spans)` — smallest properly-nesting span wins; a set of containing spans that does not form a strict containment chain is `AMBIGUOUS`, never resolved by sort order. `evaluate_coverage` gains rule 3b between the existing rules 2/3 and rule 4: an unattributed line is offered to `adapter.statement_spans` when `adapter.requires_span_attribution`; resolved lines join the numerator/denominator exactly as rules 1/2 do; `NOT_CODE`/anchor-is-itself-untracked fall to rule 4 unchanged; `AMBIGUOUS`/unparseable-file/anchor-status-unknown become `unclassified_lines`, which outranks `EXCLUDED_LINES` and `UNCOVERED_LINES` in the outcome/reason_code decision (mirrors dstdns's own `Verdict.passed` precedence). `CoverageEvaluation` gains `unclassified_lines`/`files_with_unclassified_lines`. |
| `src/assay/verdict.py` | `Coverage` gains the additive third pair `unclassified_lines`/`files_with_unclassified_lines` (A-096's discipline), both defaulting to empty. Refactored `_check_missing_lines`/`_check_files_missing_coverage` into two shared module-level functions (`_check_line_location_mapping`, `_check_file_tuple`) reused by both the old and new pairs — verified byte-identical error messages (see "Things I could not honor exactly as written" — none; see refactor note below) so no existing test needed a matching-string update. |
| `src/assay/schemas/verdict.schema.json` | `$defs/coverage` gains `unclassified_lines`/`files_with_unclassified_lines`, both in `required` (additive, `additionalProperties: false` already present). |
| `tests/test_adapters_base_statement_span.py` (new, 6 tests) | `StatementSpan` construction-time rejection: non-integer, non-positive, backwards ranges; frozen; the untouched/single-line forms build. |
| `tests/test_adapters_python_statement_spans.py` (new, 13 tests) | `PythonAdapter.statement_spans` direct unit tests: multi-line dict literal (the exact B065/P80 worked example), compound-header trimming (multi-line and one-line-body), multi-line decorator, multi-line match-case pattern, nested statements, bare module/class docstrings excluded, unparseable source / embedded NUL → `None`, empty file → `()`, exact return shape. |
| `tests/test_evaluate_attribute_line.py` (new, 11 tests) | `_attribute_line` pure-function unit tests with hand-built spans, per the handoff's own instruction: not-code, nested resolution (both orderings), duplicate-identical spans, non-nesting overlap, equal-size overlap, an unrelated non-containing span never causing a false ambiguity. |
| `tests/test_evaluate_span_attribution.py` (new, 12 tests) | O1/O2 through the real `evaluate_coverage`: executed/unexecuted interior-line attribution (numeric proof, not just outcome), the "statement start named" case, interior-line-only diff, non-code-between-statements, the genuinely-unattributable real-Python case (excluded anchor), unparseable file, and — via a new synthetic `SpanAdapter` (local to this file) — overlap/malformed/`None`-spans/no-attribution-declared, including the sharpened overlap fixture that would flip to a real PASS under naive "pick the first/smallest" resolution. |
| `tests/test_verdict_coverage_unclassified_locations.py` (new, 19 tests) | `Coverage`'s own construction-time validation of the new pair, mirroring `test_verdict_coverage_missing_locations.py`'s discipline exactly; proves the shared validation helpers hold the new fields to the same standard. |
| `tests/test_verdict_span_attribution_artifacts.py` (new, 6 tests) + 3 new fixture JSON files | O3: full, schema-valid, independently hand-written artifacts for attributed PASS, attributed FAIL/UNCOVERED_LINES, and FAIL/UNCLASSIFIED_LINES, assembled via `evaluate_coverage` + `Claim`/`Coverage`/`Verdict` directly (not through `runner.py` — see "known gap" below) plus two vacuity guards (omitted locations / rolled-up-as-PASS both differ from the real artifact). |
| `tests/conftest.py` | Adds `SPAN_VERDICT_FIXTURES`/`span_verdict_fixture` alongside the existing `R1_VERDICT_FIXTURES` pattern. |
| `tests/fixtures/verdicts/{pass,fail,r1_pass,r1_fail_uncovered_lines,r1_fail_excluded_lines}.json` | Updated to add the two new (empty) keys — required by construction-time-consistency once `Coverage.to_dict()` always emits them. |
| `tests/test_adapters_python_union_fidelity.py` | P06's own 24-line construct-family fixture deliberately changes every physical line, including docstrings/comments/blanks that are now genuinely "unattributed" on `PythonAdapter` (`requires_span_attribution=True`) — P07 adds a NEW `read_source_text` call site that P06's fixture had never anticipated (its old helper unconditionally raised). Updated to serve the real `SOURCE` for `pkg/mod.py`, added `assert result.unclassified_lines == {}` to two of its three tests as an explicit non-regression proof. |
| `tests/test_verdict_claims.py`, `tests/test_verdict_schema_rejects.py` | Two raw coverage-shaped dict literals used in schema-validation assertions extended with the two new required keys. |

## Per-oracle evidence (A-067: real mutations, not estimates)

Every mutation below was applied directly to the source with a literal
`# MUTATION: ...` marker, its presence confirmed with `grep -c` before the
run (local interpreter, `PYTHONPATH=src python3 -m pytest tests -q`,
verified identical to the container gate at baseline — 1056 — and after
every revert), then reverted and re-verified via `grep -c "MUTATION"`
returning `0` and a full rerun back at 1056 passed.

### O1 — executed continuation is attributed and passes provably; unexecuted fails with the statement start named

**Mutation** (`evaluate.py` — rule 3b's own trigger condition short-circuited: `if False and unattributed and adapter.requires_span_attribution:`). `grep -c "MUTATION: O1 attribution disabled"` → 1.
**Real result: 13 failed** — 9 of `test_evaluate_span_attribution.py`'s 12 tests (every one whose subject is a line span attribution was actually supposed to resolve), plus all 3 of `test_verdict_span_attribution_artifacts.py`'s independent-artifact comparisons and 1 of its vacuity guards. The 3 that correctly stayed green:
  `test_a_changed_line_that_is_itself_an_untracked_statement_start_is_non_code`
  (its own line was never going to be attributed to anything OTHER than
  itself either way — the anchor-is-itself-untracked branch is
  independent of rule 3b's outer trigger),
  `test_a_disjoint_span_that_does_not_contain_the_line_is_simply_non_code`
  and `test_no_declared_span_attribution_leaves_the_line_silently_non_code`
  (both already expect the line to end up untouched/non-code, which is
  also rule 3b DISABLED's own default behaviour — coincidentally
  indistinguishable for these two fixtures specifically, by design: they
  exist to prove the harness itself doesn't always fail closed, not to
  catch this particular mutation). Reverted; `grep -c "MUTATION"` → 0;
  rerun → 1056 passed.

### O2 — overlapping/malformed/genuinely-unattributable spans render FAIL/UNCLASSIFIED_LINES; no ambiguity becomes PASS

Two mutations, because the first one's own weak fixture design was itself caught and fixed during this pass (recorded honestly rather than silently redone):

* **Mutation attempt 1** (`_attribute_line`'s ambiguity guard disabled: `if False and not (...)`) against my FIRST-draft integration fixture (both `file_cov.executed`/`missing` empty). `grep -c` → 1. **Real result: only 2 failed** (`test_evaluate_attribute_line.py`'s own two direct unit tests). The two integration-level "overlap" tests in `test_evaluate_span_attribution.py` stayed GREEN — because with an empty `executed`/`missing`, the naive fallback anchor (whichever span sorts first) *also* has unknown status, so it lands in the same `else: unclassified` branch by a different route, masking whether the ambiguity guard specifically fired. **This is exactly the kind of vacuous oracle AUTHORING.md §3b.C warns about**, caught here by actually mutating rather than trusting the fixture's intent.
* **Fix applied**: rebuilt the fixture so the first-listed overlapping span's own start line (3) is genuinely `executed`. A naive "pick the first/smallest, no ambiguity check" resolution would then inherit COVERED status and the fixture would flip all the way to a real PASS — the sharpest, most literal form of the negative ("turns at least one ambiguity fixture green").
* **Mutation attempt 2**, same code change, against the FIXED fixture. `grep -c "MUTATION: O2 ambiguity guard disabled"` → 1. **Real result: 3 failed** — the same 2 direct unit tests, PLUS `test_overlapping_non_nesting_spans_render_unclassified_never_pass`, which now genuinely catches it. Independently confirmed with a standalone interpreter probe (pasted in this LOG's authoring session, not kept as a test): under the mutation, `evaluate_coverage` returns `outcome=PASS, covered=1, changed_executable=1, unclassified={}` for the exact ambiguous-line fixture — the negative, reproduced literally. Reverted; `grep -c "MUTATION"` → 0; rerun → 1056 passed.

### O3 — runner output for attributed PASS / attributed FAIL-UNCOVERED_LINES / FAIL-UNCLASSIFIED_LINES matches independently written schema-valid artifacts

**Mutation** (`Coverage.to_dict()` — the two new keys' emission deleted). `grep -c "MUTATION: O3 unclassified locations omitted"` → 1.
**Real result: 13 failed**, spanning FOUR test modules: `test_verdict_span_attribution_artifacts.py` (all 3 independent-artifact comparisons), `test_verdict_coverage_unclassified_locations.py` (3 of its own tests that assert the keys are present), `test_verdict_serialises.py` (4 of P05's own pre-existing PASS/FAIL fixture-comparison and schema-validation tests — because those fixtures now also carry the two keys, so their own `to_dict()`/schema-validate round trip broke too), and `test_runner_evaluate_r1.py` (3 of P05's own runner-integration fixture comparisons, for the identical reason). This is a materially STRONGER result than a scoped mutation would have shown: it demonstrates the additive-field discipline is load-bearing for every EXISTING producer, not only P07's own new one. Reverted; `grep -c "MUTATION"` → 0; rerun → 1056 passed, 100%/100%.

## Self-review

### Would each oracle's test fail if the behaviour were removed?

Yes for all three — demonstrated above with real mutations (not estimated),
each confirmed present via `grep -c` before the run and absent via
`grep -c "MUTATION"` returning `0` after revert, with a full rerun back to
1056 passed after every single revert.

### What is MISSING from the diff the handoff asked for

Nothing in `## Work`. Items 1-4 are honoured as written:

1. **`statement_spans` added to the protocol and the Python adapter**,
   exact A-101 shape (`tuple[StatementSpan, ...] | None`, new frozen
   `kw_only` `StatementSpan`), called by `evaluate.py` only when
   `requires_span_attribution` is `True`.
2. **Attribution kept pure; explicit unclassified locations, never a
   guess.** Overlapping/malformed/genuinely-unattributable all render
   `FAIL`/`UNCLASSIFIED_LINES` (A-100), never a new pairing.
3. **R1 payload and schema extended additively**, matching A-096's
   pattern exactly; independent expected artifacts for every new terminal
   path (attributed PASS, attributed FAIL/UNCOVERED_LINES,
   FAIL/UNCLASSIFIED_LINES).
4. **Attribution, overlap refusal, and rollup all broken and measured**
   (A-067) — see the per-oracle section above; three real mutations, one
   of which (O2) caught and fixed a genuinely weak fixture mid-pass rather
   than silently keeping it.

### What I implemented that the handoff did not ask for, with justification

* **Deleted two dead defensive branches** dstdns's own code carries
  (`getattr(ast, "TryStar"/"Match", None)` version-guard; `if pattern is
  not None` on a `match_case`'s own required `pattern` field) — replaced
  with direct references / unconditional access. `pyproject.toml` requires
  Python ≥3.11, where both AST node types are unconditionally present, and
  `ast.match_case.pattern` is a required grammar field that can never be
  `None` from `ast.parse`. Kept as dstdns's own defensive style, these
  branches could never have their "false" arm exercised by ANY legitimate
  test on this project's own fixed toolchain — AUTHORING.md §3b.D's own
  rule ("if a line is genuinely unreachable, restructure so it does not
  exist") applies as directly to a dead BRANCH as to a dead line. Left in,
  they would have forced either a coverage-floor miss or an artificial test
  that could only fake the branch (e.g. monkeypatching `ast.Match` away),
  which would test nothing about real Python semantics.
* **Refactored `Coverage`'s two existing validation methods into shared
  helpers** (`_check_line_location_mapping`, `_check_file_tuple`) reused by
  both the old pair and the new one, rather than hand-copying two more
  near-duplicate methods. Verified byte-identical error message text for
  every existing case (so no existing test needed updating) before trusting
  the refactor; the alternative — four independently-maintained methods
  with the same five rules — is exactly the drift risk A-096's own "exact
  same discipline" phrasing warns against.
* **A synthetic `SpanAdapter`** (local to `test_evaluate_span_attribution.py`,
  not added to the shared `conftest.py`) with an injectable `statement_spans`
  field, needed because `conftest.FakeAdapter` predates P07 and has no such
  method; kept local rather than added to the shared fixture module since
  only this package's own oracles need it.
* **`test_a_disjoint_span_that_does_not_contain_the_line_is_simply_non_code`
  and `test_no_declared_span_attribution_leaves_the_line_silently_non_code`**
  — not named oracles, added as the same "prove the FAIL cases are really
  about the defect, not an artefact of the harness" discipline P06's own LOG
  names for its own "fully covered variant" test.

### Known-weak spots, stated plainly

* **`assay.runner.evaluate_r1`'s own `Coverage(...)` construction call is
  NOT wired to pass through `unclassified_lines`/`files_with_unclassified_lines`.**
  `runner.py` is not in this package's `scope.touch` (only `adapters/base.py`,
  `adapters/python.py`, `evaluate.py`, `verdict.py`, `schemas/**`, `tests/**`
  are), so it was not touched, per the handoff's own explicit instruction to
  implement only within scope. This is a REAL, VERIFIED gap: a genuine run
  through `runner.evaluate_r1` today, using a real span-attribution adapter
  against a file with a real unclassified line, would build a `Claim` whose
  `status`/`reason_code` ARE still correct (`FAIL`/`UNCLASSIFIED_LINES` —
  these flow through `CoverageEvaluation.outcome`/`.reason_code`, already
  wired, unaffected by this gap), but whose nested
  `coverage.unclassified_lines`/`coverage.files_with_unclassified_lines`
  would silently default to empty — an inconsistent artifact (a claim
  naming a cause its own evidence bundle doesn't show). Verified directly by
  `test_verdict_span_attribution_artifacts.py::test_runner_py_does_not_pass_the_new_fields_through_a_documented_gap`,
  which inspects `inspect.getsource(runner.evaluate_r1)` and asserts the
  string `"unclassified_lines"` does not appear in it — a test that will
  itself start failing (loudly, on purpose) the moment a later package
  fixes this, flagging that this LOG entry and that test should be removed
  together. **The fix, when a later package's `scope.touch` includes
  `runner.py`, is one line**: add
  `unclassified_lines=result.unclassified_lines,
  files_with_unclassified_lines=result.files_with_unclassified_lines,` to
  the existing `Coverage(...)` call inside `evaluate_r1`, mirroring exactly
  how `missing_lines`/`files_missing_coverage` are already passed.
* **The `_attribute_line` pure function and `_Attribution` enum are tested
  by direct import of a private module member** (`from assay.evaluate import
  _Attribution, _attribute_line`), a deliberate deviation from this
  project's general preference (visible in P05/P06's own LOGs) for testing
  only through a module's public surface. Justified directly by the
  handoff's own text ("Attribution is a pure function; test it directly
  with hand-built spans... per the handoff's own guidance on O2's
  'overlapping' case"), and it is also exercised end-to-end through
  `evaluate_coverage` in `test_evaluate_span_attribution.py`, so the public
  surface is not left unproven.
* **`StatementSpan`'s "malformed" construction-time rejection is a
  DELIBERATE reading, not the only possible one.** O2's own text bundles
  "overlapping ..., malformed, or genuinely unattributable spans" as three
  items rendering `FAIL`/`UNCLASSIFIED_LINES` at RUNTIME. I read "malformed"
  as the individually-invalid-range case (refused at `StatementSpan`
  construction, matching A-092's Coverage/Claim precedent literally:
  "most of your rejections will likely be plain ValueErrors at construction
  time") and "overlapping" as the runtime-observable ambiguity case — since
  a `StatementSpan` that validates its own numbers at construction can
  never reach `evaluate_coverage`'s runtime logic in a malformed state at
  all, so a literal "malformed spans render FAIL/UNCLASSIFIED_LINES at
  runtime" reading is not simultaneously satisfiable with A-092's
  construction-time-rejection house style. A-101's own text supports this:
  it says explicitly "only 'overlap' needed this clarification," implying
  "malformed" was already self-evident and needed no separate runtime
  mechanism. Recorded here as an interpretation, flagged in case a reviewer
  reads it differently.

## Things I could not honor exactly as written

None outright refused. The one place I diverged from a literal reading is
the "malformed spans... render FAIL/UNCLASSIFIED_LINES" phrase discussed
above under known-weak-spots — resolved by choosing the reading consistent
with A-092's house style, documented rather than silently picked. No
`escalate_if` condition was tripped (the protocol was sufficient to
represent every reference-implementation behaviour I found in
`dstdns/scripts/coverage_gate.py`), and `src/assay/config.py` was never
needed.
