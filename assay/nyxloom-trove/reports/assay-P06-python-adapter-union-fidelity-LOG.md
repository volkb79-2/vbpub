# assay-P06 — Python adapter union fidelity — LOG

**Status:** DONE. Gate green.
**Branch:** `feat/assay-P06-python-adapter-union-fidelity`
**Base:** `main` at `05ab843e` ("rule(assay): P06 readiness findings -- A-098/A-099, land before dispatch").
**Commit:** `5a04508aad41c149fcd876a508d3a650b192c24e`

## Gate

`tester-unified`, run in the FOREGROUND against the working tree with the container-side path substituted for the host bind mount:

```
$ cgroup_parent="dev-background.slice"
$ docker run --rm --cgroup-parent="$cgroup_parent" \
    -w /workspaces/vbpub/.worktrees/assay-P06-python-adapter-union-fidelity/assay \
    -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
    bash -c 'export PYTHONPATH=src && /opt/tester-venv/bin/python -m pytest tests -q --cov=src/assay --cov-branch --cov-report=term-missing'
........................................................................ [  7%]
........................................................................ [ 14%]
........................................................................ [ 21%]
........................................................................ [ 29%]
........................................................................ [ 36%]
........................................................................ [ 43%]
........................................................................ [ 51%]
........................................................................ [ 58%]
........................................................................ [ 65%]
........................................................................ [ 73%]
........................................................................ [ 80%]
........................................................................ [ 87%]
........................................................................ [ 94%]
..................................................                       [100%]
================================ tests coverage ================================
_______________ coverage: platform linux, python 3.14.6-final-0 ________________

Name                                             Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------------------------------
src/assay/__init__.py                               10      0      0      0   100%
src/assay/adapters/__init__.py                       1      0      0      0   100%
src/assay/adapters/base.py                          12      0      0      0   100%
src/assay/adapters/python.py                        36      0      8      0   100%
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
TOTAL                                             1363      0    512      0   100%
986 passed in 11.08s
GATE_EXIT=0
```

Baseline before this package: 946 passed, 1327 stmts / 504 branches, 100%.
This package adds 40 tests, 36 statements, 8 branches (`adapters/python.py`
only) — still 100% statement and branch coverage. No other module's
statement/branch count changed (confirming O3: nothing in `evaluate.py`,
`adapters/base.py`, `registry.py`, `verdict.py`, or `schemas/` was touched).

## Delivered

| File | Notes |
|---|---|
| `src/assay/adapters/python.py` (new, 36 stmts / 8 branches) | `PythonAdapter` — a frozen `kw_only` dataclass implementing the five attributes + three methods A-097 fixes, plus one adapter-private constructor field (`coverage_key_prefix`) not part of the protocol's structural surface (the same pattern P05's own `FakeAdapter` already established with `key_prefix`/`test_marker`/`no_code_marker`). |
| `tests/test_adapters_python_test_path.py` (new, 11 tests) | `is_test_path`, dstdns's `_TEST_FILE_RE` verbatim (the sole holder — confirmed by reading topos/nyxloom in full, zero test-path concept in either). Each construct paired with a REAL sibling hazard (verified via a one-off Python check that the naive substring genuinely occurs in the sibling fixture before writing the assertion — `mytests/foo.py`, `mytest_foo.py`, `myconftest.py`). |
| `tests/test_adapters_python_has_executable_code.py` (new, 16 tests) | `has_executable_code`, the AST-based NoCode decision. One dedicated fixture per A-098 construct family (decorator, async def, if/for/with/try headers, class def, module docstring, multiline docstring, comments, pragma alone, pragma-with-code, combined non-code, empty file, unparseable source, embedded NUL byte). |
| `tests/test_adapters_python_normalize_coverage_key.py` (new, 5 tests) | `normalize_coverage_key`'s own prefix-strip boundary safety (A-099), as direct unit calls: identity default, positive strip, sibling-prefix hazard, bare-prefix edge, unrelated-prefix no-op. |
| `tests/test_adapters_python_union_fidelity.py` (new, 5 tests) | End-to-end: the real `PythonAdapter` plugged into P05's unmodified `evaluate_coverage`, for (a) a 24-line, hand-derived, every-construct-family fixture (O1) and (b) the genuinely-differing-spelling / sibling-prefix-hazard fixture driven through the real pipeline (O2), not just isolated unit calls. |
| `tests/test_adapters_python_registration.py` (new, 3 tests) | `new_registry(PythonAdapter())` / `get_adapter` wiring (O3), and a transparency assertion for the two union decisions the three reference gates gave no direct guidance on (`excluded_dir_names` empty, `requires_span_attribution` True). |

`registry.py` needed no edit — verified by reading it (P05's own generic
`new_registry(*adapters)`/`get_adapter(registry, language)` already accepts
any concrete `LanguageAdapter`, including `PythonAdapter`); confirmed
mechanically by the `git status`/`diff --stat` output above showing zero
changes under it.

## Per-oracle evidence

Every mutation was applied directly to `src/assay/adapters/python.py` with a
literal `# MUTATION: ...` marker, its presence confirmed with `grep -c`
before the run, the local interpreter used for iteration speed
(`PYTHONPATH=src python3 -m pytest tests -q`, verified identical pass count
to the container gate at baseline and at the final green run above — 986),
then reverted and re-verified via `grep -c "MUTATION" src/assay/adapters/python.py`
returning `0` and a full local rerun back at 986 passed (A-067).

### O1 — decorators, async/compound statements, docstrings, comments, pragma tokens, each resolved from a single reported line

* **Mutation 1 (`has_executable_code` — AST classification replaced with
  nonblank-line counting: `return any(line.strip() for line in
  text.splitlines())`).** `grep -c "MUTATION: nonblank line counting"` → 1.
  **Real result: 5 failed** —
  `test_a_module_docstring_alone_has_no_executable_code`,
  `test_a_multiline_docstring_alone_would_fool_a_nonblank_line_counter` (the
  fixture built specifically for this negative — a five-line docstring, zero
  real code),
  `test_comments_alone_have_no_executable_code`,
  `test_a_pragma_token_alone_has_no_executable_code`,
  `test_docstring_comment_and_pragma_together_still_have_no_code`. Exactly
  O1's own negative, verbatim.
* **Mutation 2 (`has_executable_code` — the docstring construct omitted:
  `for node in tree.body: return True` unconditionally, dropping the
  `_is_bare_string_statement` check).**
  `grep -c "MUTATION: docstring construct omitted"` → 1.
  **Real result: 3 failed** —
  `test_a_module_docstring_alone_has_no_executable_code`,
  `test_a_multiline_docstring_alone_would_fool_a_nonblank_line_counter`,
  `test_docstring_comment_and_pragma_together_still_have_no_code`. The
  decorator/async/compound-statement/comment/pragma-alone/empty/unparseable
  tests were unaffected — proving this mutation isolates exactly the one
  construct it removed, not a broader regression.
* **Mutation 3 (`is_test_path` — the `(^|/)` boundary anchor dropped:
  `_TEST_FILE_RE = re.compile(r"(tests/|test_|conftest\.py)")`).**
  `grep -c "MUTATION: boundary anchor dropped"` → 1.
  **Real result: 3 failed** —
  `test_a_sibling_directory_merely_ending_in_tests_is_not_mismatched`
  (`mytests/foo.py` — verified independently that the bare substring
  `"tests/"` genuinely occurs inside it before writing the assertion),
  `test_a_filename_merely_containing_the_test_prefix_mid_word_is_not_mismatched`
  (`pkg/mytest_foo.py`, same verification for `"test_"`),
  `test_a_filename_merely_containing_conftest_is_not_mismatched`
  (`pkg/myconftest.py`). Two other sibling-shaped fixtures in the same file
  (`tests_data/`, `testing_helpers.py`) were correctly UNAFFECTED by this
  specific mutation — verified by hand that neither contains the relevant
  bare substring either, so their own value is precision-of-match, not
  anchor-boundary detection; recorded in that module's own docstrings so the
  distinction is visible rather than an accidental gap.

### O2 — `normalize_coverage_key`'s own prefix-strip boundary safety (A-099)

* **Mutation (naive `removeprefix`, no boundary check: `return
  key.removeprefix(self.coverage_key_prefix) if self.coverage_key_prefix
  else key`).** `grep -c "MUTATION: naive removeprefix"` → 1.
  **Real result: 5 failed** —
  `test_a_configured_prefix_is_stripped_at_the_path_segment_boundary` (a
  SECOND-ORDER catch: `"myapp".removeprefix("myapp")` leaves a stray
  leading `"/"` — `"myapp/src/foo.py"` → `"/src/foo.py"`, not
  `"src/foo.py"` — so even the intended-to-work POSITIVE case broke, not
  only the hazard case),
  `test_a_sibling_prefixed_key_is_left_unchanged_not_mis_stripped` (the
  fixture built specifically for A-099's own negative — `"myapp_legacy/..."`
  mangled into `"_legacy/..."`),
  `test_a_key_equal_to_the_bare_prefix_with_no_trailing_segment_is_unchanged`,
  `test_normalize_coverage_key_reconciles_the_real_pipeline_end_to_end`
  (the O2 end-to-end fixture, through the real unmodified `evaluate_coverage`),
  `test_a_sibling_prefixed_files_own_coverage_is_not_stolen_or_lost` (the
  end-to-end sibling-hazard fixture — failed with the exact predicted
  mechanism: `read_source_text` was reached for `myapp_legacy/pkg/bar.py`
  because its mis-normalized key no longer matched any `profile.files`
  entry, tripping the test's own "every fixture file has a coverage entry"
  guard rather than a plain assertion mismatch — a second-order confirmation
  of the predicted fallthrough, the same shape P05's own LOG records for an
  analogous mutation).

### O3 — no forbidden-path diff; the P05 fake-adapter suite remains green

Not mutation-tested (there is no logic inside `python.py` whose removal
demonstrates "the core stayed untouched" — this is a structural property of
the diff, not a runtime behavior). Verified instead by:

* `git status --porcelain --ignored` (run from the worktree root, since
  `assay/` is a monorepo subdirectory and git's own toplevel sits one level
  above it) after the final commit shows exactly six new files, all inside
  `assay/src/assay/adapters/python.py` and `assay/tests/`: nothing under
  `evaluate.py`, `adapters/base.py`, `registry.py`, `verdict.py`, or
  `schemas/` appears in the diff at all.
* The coverage table above shows every OTHER module's statement/branch count
  unchanged from P05's own baseline (`evaluate.py` still 73/24,
  `adapters/base.py` still 12/0, `registry.py` still 22/4) — a second,
  independent confirmation that this package's tests genuinely exercise
  those modules' EXISTING code paths rather than new ones.
* All of P05's own fake-adapter tests (`test_evaluate_language_free.py`,
  `test_evaluate_four_way_union.py`, `test_registry.py`) are present,
  unmodified, and passing in the 986-passed total above.

## Self-review

### Would each oracle's test fail if the behaviour were removed?

Yes for all three, demonstrated above by 4 real mutations (not estimated),
each with its presence confirmed via `grep -c` before the run and its
absence confirmed via `grep -c "MUTATION"` returning `0` after revert, with
a full local rerun back at the pre-mutation pass count (986) after every
single revert. Every mutation produced at least one real failure; two (O1
mutation 1, O2's mutation) produced failures spanning multiple independent
test modules, including two second-order detections (a `KeyError`-style
guard trip in the O2 union-fidelity test, and the positive/matching case
itself breaking alongside the intended hazard case in O2's unit test).

### What is MISSING from the diff the handoff asked for

Nothing in `## Work`. Items 1–4 are honoured as written:

1. **Python adapter, registered explicitly** — `PythonAdapter`, plus
   `test_adapters_python_registration.py` proving `new_registry(PythonAdapter())`
   / `get_adapter` wiring, not merely construction.
2. **Union of reference behaviors, A-098's resolved scope** — `is_test_path`
   adopts dstdns's rule verbatim (the sole holder); `has_executable_code`
   covers every construct family A-098 names, each resolved from its own
   single line; genuine multi-line statement interior-line attribution is
   NOT attempted anywhere in this diff (there is no method in the frozen
   protocol to attempt it with).
3. **Committed literal snippets, independent expected sets** — every
   `has_executable_code`/`is_test_path` expectation in this diff was reasoned
   by hand against known `ast`/`coverage.py` semantics (and, for the
   `is_test_path` sibling fixtures, independently spot-checked with a
   one-off interpreter session BEFORE writing the assertion, to confirm each
   fixture actually exercises the substring hazard it claims to) — never
   generated by calling `PythonAdapter` itself and trusting the output.
4. **Each classification family broken, `normalize_coverage_key`'s mutation
   living inside `python.py`** — four mutations above, all inside
   `adapters/python.py`, none inside `evaluate.py` (which this package
   cannot touch).

### What I implemented that the handoff did not ask for, with justification

* **`PythonAdapter.coverage_key_prefix` as a constructor field.** The
  protocol's `normalize_coverage_key(self, key: str) -> str` takes no
  argument besides `key` — there is nowhere for a real prefix to come from
  except adapter-instance state the concrete class supplies itself, the
  same shape `FakeAdapter.key_prefix` already established in P05's own test
  harness. Without this field `normalize_coverage_key` could only ever be
  the identity function, which would make A-099's own oracle unsatisfiable
  as literally specified ("construct a fixture where the two spellings
  genuinely differ") — a real adapter needs SOME way to be told what to
  strip, exactly as Go's own module path is a fact from `go.mod`, not
  derived from the coverage key string alone.
* **`requires_span_attribution = True`.** P05's own protocol comment says
  P05's evaluation never reads this flag — it exists purely for P07 to
  discover which adapters to extend. `FakeAdapter` (a synthetic language
  with no real multi-line-statement gap) correctly declares `False`; real
  Python DOES have the gap A-098's own scope note describes at length, so
  declaring `True` here is this adapter's own honest signal, not an
  unrequested feature — the alternative (leaving it `False`) would be a
  quiet misstatement of fact P07 would have to discover the hard way.
* **`excluded_dir_names = frozenset()`.** Read literally, "take the union of
  reference behaviors" could be misread as an instruction to invent a
  plausible Python-idiomatic set (`__pycache__`, `.venv`, ...). Grepping all
  three reference gates in full for any directory-name exclusion mechanism
  returned zero hits in every one of them — the genuine union of three empty
  sets is the empty set, and DESIGN-GUIDE §5's own defaults doctrine ("never
  invent" a fact no source supplies) reads directly against inventing one.
  Documented at length in `python.py`'s own module docstring and asserted
  directly in `test_adapters_python_registration.py` so this is a visible,
  deliberate choice rather than an accidental omission a future reader has
  to rediscover by grepping the same three files again.
* **`test_a_fully_covered_variant_of_the_same_fixture_passes`** in the
  union-fidelity test module — not a named oracle requirement, added because
  a single always-FAIL fixture cannot by itself prove the FAIL was really
  about the specific gap it claims (the same "hollow test" risk AUTHORING.md
  §3b.C names); this test reuses the identical 24-line construct-family
  fixture with only the one real gap closed, and asserts PASS.

### Known-weak spots, stated plainly

* **The suffix-style test convention (`foo_test.py`) is deliberately not
  recognised** — `test_a_suffix_style_test_filename_is_deliberately_not_recognised`
  asserts this as a positive fact rather than merely omitting a test for it.
  None of the three reference gates recognises it, so per the "union of
  references" charter this package does not invent it; a project using that
  convention would see its test files' own changed lines wrongly counted
  toward the coverage floor. This is a real, named scope boundary, not an
  oversight — flagged here for whoever eventually revisits the union.
* **`has_executable_code` only inspects `tree.body` (module top level), not
  a full recursive `ast.walk`.** Reasoned in `python.py`'s own docstring to
  be equivalent for this method's actual question ("does this module have
  ANY real code"), since nesting requires a top-level statement to contain
  it and any such statement already trips the method to `True`. This is
  correct for the boolean this method answers, but it is worth stating
  plainly that this method was never asked to (and does not) enumerate
  WHICH lines have code — only P07's future `statement_spans` does that.
* **The O1 union-fidelity fixture's executed/missing/excluded sets are
  hand-reasoned against known `coverage.py` semantics, not generated by
  actually running `coverage.py` on the fixture.** This was a deliberate
  reading of the handoff's own instruction ("independently (by-hand)
  computed expected line sets, not sets generated by running your own
  adapter") — real `coverage.py` execution was available (it is a declared
  test dependency) but was judged out of scope for a unit-level oracle and a
  needless slowdown/fragility risk (coupling this package's tests to a
  third-party tool's own version-specific tracing behavior, which is
  already P03's parser's own tested concern, not this adapter's). Flagged
  as an interpretation, not a certainty, since the handoff text does not
  explicitly forbid running real `coverage.py` — only forbids trusting
  THIS adapter's own output as the source of expected values.

## Things I could not honor exactly as written

None. Every named oracle (O1, O2, O3), every Work item, and every lettered
test constraint (A–E) was followed as specified; no `escalate_if` condition
was tripped (the protocol was sufficient as frozen, and the three reference
gates did not disagree in any way decisions.md left unresolved).
