# assay-P08 — Go adapter boundary proof — LOG

**Status:** DONE. Gate green.
**Branch:** `feat/assay-P08-go-adapter-boundary-proof`
**Worktree:** `/workspaces/vbpub/.worktrees/assay-P08-go-adapter-boundary-proof/assay`
**Base:** `main` at `c6bb7aa6` ("rule(assay): P08 readiness findings -- A-102/A-103/A-104, land before dispatch").
**Commit:** (recorded after `git commit`, see bottom of this file)

## Gate

`tester-unified`, run in the FOREGROUND against the working tree with the container-side path substituted for the host bind mount:

```
$ cgroup_parent="dev-background.slice"
$ docker run --rm --cgroup-parent="$cgroup_parent" \
    -w /workspaces/vbpub/.worktrees/assay-P08-go-adapter-boundary-proof/assay \
    -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
    bash -c 'export PYTHONPATH=src && /opt/tester-venv/bin/python -m pytest tests -q --cov=src/assay --cov-branch --cov-report=term-missing'
........................................................................ [  6%]
........................................................................ [ 12%]
........................................................................ [ 19%]
........................................................................ [ 25%]
........................................................................ [ 32%]
........................................................................ [ 38%]
........................................................................ [ 45%]
........................................................................ [ 51%]
........................................................................ [ 58%]
........................................................................ [ 64%]
........................................................................ [ 71%]
........................................................................ [ 77%]
........................................................................ [ 84%]
........................................................................ [ 90%]
........................................................................ [ 97%]
..............................                                           [100%]
================================ tests coverage ================================
_______________ coverage: platform linux, python 3.14.6-final-0 ________________

Name                                             Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------------------------------
src/assay/__init__.py                               10      0      0      0   100%
src/assay/adapters/__init__.py                       1      0      0      0   100%
src/assay/adapters/base.py                          27      0      8      0   100%
src/assay/adapters/go.py                           161      0     74      0   100%
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
TOTAL                                             1626      0    640      0   100%
1110 passed in 11.04s
GATE_EXIT=0
```

Baseline before this package: 1056 passed, 1465 stmts / 566 branches, 100%. This
package adds 54 tests, 161 statements, 74 branches — still 100% statement and
branch coverage. Every changed statement/branch count is confined to the new
`src/assay/adapters/go.py` (+161/+74). No other module's statement/branch count
moved. `git status --porcelain` shows only new, untracked paths — no existing
file was modified at all:

```
?? src/assay/adapters/go.py
?? tests/fixtures/go/
?? tests/test_adapters_go_has_executable_code.py
?? tests/test_adapters_go_normalize_coverage_key.py
?? tests/test_adapters_go_python_equivalence.py
?? tests/test_adapters_go_registration.py
?? tests/test_adapters_go_test_path.py
?? tests/test_adapters_go_union_fidelity.py
```

This is O3's own mechanical evidence: `adapters/base.py`, `evaluate.py`,
`registry.py`, `errors.py`, `verdict.py`, and `schemas/**` are all byte-identical
to `main` at `c6bb7aa6`. `registry.py` in particular needed literally zero
changes — confirmed by reading it in full before writing any code (it is a
pure, generic `name -> LanguageAdapter` lookup with no import of, or reference
to, any concrete adapter module).

## Delivered

| File | Notes |
|---|---|
| `src/assay/adapters/go.py` (new) | `GoAdapter`, a frozen `kw_only` dataclass implementing the frozen `LanguageAdapter` protocol unchanged (A-097/A-101). `name="go"`, `source_globs=("*.go",)`, `excluded_dir_names=frozenset()` (the genuine union of `covergate`'s own — the one Go reference this package has — behaviour, confirmed by grep: it excludes no directory by bare name), `requires_span_attribution=False` (A-102), `external_tools=()` (A-087). `is_test_path` is `rel_path.endswith("_test.go")`, adopted verbatim from `covergate/main.go`'s own `isGoTestFile`. `normalize_coverage_key` mirrors `PythonAdapter.coverage_key_prefix`'s own boundary-safe prefix strip, adopted from `covergate/main.go`'s own `stripModulePrefix`. `statement_spans` returns `None` unconditionally. `has_executable_code` delegates to a private, narrow, deterministic lexer (below). |
| `src/assay/adapters/go.py`'s lexer (private module functions) | `_strip_comments_and_literals` masks every `//` line comment, `/* */` block comment, `"..."` interpreted string (with escapes, no bare newline), `` `...` `` raw string (spans lines, no escapes), and `'...'` rune literal to same-length whitespace (real newlines preserved), returning `None` on any unterminated one. `_scan_for_top_level_func_body` tracks brace depth to find TOP-LEVEL `func` words only (never descending into a nested body — matches `PythonAdapter`'s own `tree.body`-only convention). `_scan_signature_for_body` tracks one combined `(`/`)`/`[`/`]` nesting counter through a signature (receiver, name, generics, params, results) to find the body-opening `{`; an unmatched closer or an unclosed signature at EOF is treated as structurally unknown (`True`, fail-closed). `_top_level_keyword_at` is the shared word-boundary matcher (`func`/`type`/`var`/`const`/`package`/`import`) both scans use. |
| `tests/fixtures/go/hello/hello.go` (new) | A committed hello-world Go package: `Greet` (fully covered by the paired profile) and `Farewell` (not). Its own header comment documents the exact `go test -coverprofile` regeneration steps for a host that has Go. |
| `tests/fixtures/go/hello/doc.go` (new) | Comment-only — the literal shape of srdm's own historical incident (`covergate/evaluate.go`'s own comment, quoted in the handoff). Absent from `hello.out` entirely. |
| `tests/fixtures/go/hello/hello.out` (new) | A hand-written, `go test -coverprofile`-shaped profile: `Greet`'s body (lines 29-30) executed, `Farewell`'s body (lines 36-37) missing. No Go toolchain was invoked to produce it (A-042/A-087) — it is literal committed text, cross-checked by hand against `hello.go`'s own line numbers. |
| `tests/test_adapters_go_has_executable_code.py` (new, 30 tests) | O1/O4. Four fixtures ported LITERALLY from `covergate/hascode_test.go` (comment-only, declarations-only, function-body-present, bodyless/assembly). The rest are A-104's own hazard list, each with a dedicated, independently-reasoned fixture: comments/strings/raw-strings/runes containing a `func` word and/or braces (including escaped quotes inside both string and rune literals, and unterminated variants of every literal kind reaching EOF both via a bare newline and via true end-of-text), a real generics function (`profile_test.go:137`'s own `keysOf[T any](...)` shape, with a body), word-boundary hazards (`myfunction`, `funcName`), a struct field of function type (proving the top-level-only walk), an embedded NUL byte, two distinct "unmatched closer" shapes (a stray `}` and a stray `)`), an incomplete signature at EOF (`covergate/hascode_test.go`'s own `broken.go` literal), and scanning-continues-correctly proofs (a bodyless declaration followed by a real one, and two bodyless declarations in a row). |
| `tests/test_adapters_go_test_path.py` (new, 7 tests) | `is_test_path`'s `_test.go` suffix rule, including the boundary hazard a suffix rule DOES have (`"contest.go"` ends in `"test.go"` but not `"_test.go"`) and the reciprocal proof that Python's `test_*.py` convention is deliberately NOT recognised for Go. |
| `tests/test_adapters_go_normalize_coverage_key.py` (new, 5 tests) | The Go analogue of `test_adapters_python_normalize_coverage_key.py`, proving the module-path strip only fires at an exact path-segment boundary (`"srdm"` vs `"srdm_legacy"`). |
| `tests/test_adapters_go_registration.py` (new, 5 tests) | O3. Registers through unmodified `registry.py`, declares the expected protocol surface, `statement_spans` returns `None` unconditionally, a registry-obtained adapter evaluates identically to a direct one, and — the concrete "additive" proof — Go and Python adapters coexist in ONE registry, each independently addressable. |
| `tests/test_adapters_go_union_fidelity.py` (new, 4 tests) | O1 end-to-end. Reads the committed `hello.go`/`doc.go`/`hello.out` fixtures from disk and drives the real, unmodified `evaluate_coverage`, asserting the exact numeric mapping (and a second variant with a hand-written all-covered profile, proving the FAIL above was really about the real gap). Plus an `is_test_path` end-to-end skip proof and a `normalize_coverage_key` end-to-end proof (a genuinely differing key spelling). |
| `tests/test_adapters_go_python_equivalence.py` (new, 3 tests) | O2. The same `evaluate_coverage` run once with `PythonAdapter` and once with `GoAdapter` on a genuinely equivalent two-branch construct returns identical `changed_executable`/`covered`/`pct`/`outcome`/`reason_code`; Go's own untracked lines (bare closing braces, absent from any coverprofile block) fall through rule 4 exactly as Python's do, never reaching `unclassified_lines` (which `requires_span_attribution=False` makes structurally unreachable for Go); and a changed, coverage-absent Go file with lexically malformed text renders `FAIL`/`UNCOVERED_LINES` via the fail-closed `has_executable_code`, never `UNCLASSIFIED_LINES`. |

## Per-oracle evidence (A-067: real mutations, not estimates)

Every mutation below was applied directly to the source with a literal
`# MUTATION: ...` marker, its presence confirmed with `grep -c` before the run
(local interpreter, `PYTHONPATH=src python3 -m pytest tests -q`, verified
identical to the container gate at baseline — 1110 passed, 100%/100% — and
after every revert), then reverted and re-verified via `grep -c "MUTATION"`
returning `0` (grep exit 1) and a full rerun back to 1110 passed, 100%/100%.

### O1 — committed Go source plus pre-generated coverprofiles produce exact mappings; narrow deterministic lexer, fail-closed on malformed input

**Mutation**: `_strip_comments_and_literals` short-circuited to `return text`
unchanged (comment/string/rune masking disabled entirely — the "regex-only
func matching misclassifies comments, strings... in the paired fixtures"
negative, reproduced literally by removing the one thing that prevents it).
`grep -c "MUTATION: O1 masking disabled"` → 1.
**Real result: 7 failed** — every fixture whose whole point is a `func` word
or brace hidden inside a comment/string/raw-string/rune literal now
misclassifies: `test_a_line_comment_containing_func_and_braces_is_not_misclassified`,
`test_a_block_comment_containing_func_and_braces_is_not_misclassified`,
`test_a_block_comment_spanning_multiple_lines_is_stripped_correctly`,
`test_a_string_literal_containing_func_and_braces_is_not_misclassified`,
`test_an_unterminated_string_literal_that_runs_to_end_of_file_is_malformed`,
`test_a_raw_string_literal_spanning_multiple_lines_is_not_misclassified`,
`test_a_rune_literal_containing_a_brace_character_does_not_corrupt_brace_depth`
(the sharpest one: an unmasked `'{'` rune corrupts brace-depth tracking enough
that a REAL subsequent function body is never even reached). Reverted;
`grep -c "MUTATION"` → 0; rerun → 1110 passed, 100%/100%.

### O2 — the same evaluator returns equivalent results for Python and Go; Go's `requires_span_attribution=False` is load-bearing, not decorative

**Mutation**: `GoAdapter.requires_span_attribution` default flipped `False` →
`True` (the negative's own second half: "declaring `requires_span_attribution
=True` without a genuine, non-synthetic ambiguity source"). `grep -c
"MUTATION: O2"` → 1.
**Real result: 5 failed** — `test_python_and_go_return_equivalent_results_for_a_genuinely_equivalent_construct`
and `test_go_declares_no_span_attribution_the_python_adapter_genuinely_needs`
(both in the O2 module), `test_the_go_adapter_declares_the_expected_protocol_surface`
(registration), and both hello-world fixture tests in union-fidelity — every
one of them now sees Go's own untracked lines routed into `statement_spans`
(which still, correctly, returns `None` unconditionally), turning every
untracked line into `UNCLASSIFIED_LINES` and flipping outcomes from
`FAIL/UNCOVERED_LINES` (or `PASS`) to `FAIL/UNCLASSIFIED_LINES`. Reverted;
`grep -c "MUTATION"` → 0; rerun → 1110 passed, 100%/100%.

### O3 — no forbidden-path diff; weakening Python behaviour fails the retained suite

Two-part evidence, matching the oracle's own two-part text.

**Part 1 (structural)**: `git status --porcelain` inside the worktree, both
mid-implementation and at the end, shows only `??` (untracked, new) paths —
`src/assay/adapters/go.py`, `tests/fixtures/go/`, and six new
`tests/test_adapters_go_*.py` files. `adapters/base.py`, `evaluate.py`,
`registry.py`, `errors.py`, `verdict.py`, and `schemas/**` never appear as
modified, because they never were.

**Part 2 (the retained suite is a real net, not vacuously green)**: mutated
`src/assay/adapters/python.py`'s `is_test_path` (a file this package's own
`scope.touch` does NOT include, and which was reverted immediately after) to
`return False` unconditionally. `grep -c "MUTATION: O3"` → 1.
**Real result: 4 failed**, all in the PRE-EXISTING (P06's own, unmodified by
this package) `tests/test_adapters_python_test_path.py`:
`test_a_top_level_tests_directory_file_is_a_test_path`,
`test_a_nested_tests_directory_file_is_a_test_path`,
`test_a_test_prefixed_filename_is_a_test_path`,
`test_a_conftest_file_is_a_test_path`. Reverted; `grep -c "MUTATION"` → 0;
`git diff --stat -- src/assay/adapters/python.py` → empty (byte-identical to
`main`); rerun → 1110 passed, 100%/100%.

### O4 — a real function body is `True`; declarations/comments-only and bodyless are `False`; malformed/unknown is `True`

Two mutations, opposite directions of the same asymmetry (DESIGN-GUIDE §11 /
A-087): a wrong `True` is a false failure, a wrong `False` is a silent
excuse.

**Mutation 4a** (the "returning true for every file recreates srdm's 94-line
doc.go false failure" negative, reproduced literally): `has_executable_code`
short-circuited to `return True` unconditionally. `grep -c "MUTATION: O4a"` →
1. **Real result: 15 failed** — every comment-only/declarations-only/bodyless
fixture in `test_adapters_go_has_executable_code.py` (14 of them), plus
`test_the_hello_world_fixture_produces_the_exact_expected_mapping`, whose
`changed_executable` jumped from 4 to 12 as `doc.go` (comment-only, 8 lines,
absent from `hello.out`) stopped being excluded via the NoCode path and was
instead counted in full — the exact shape of the historical incident this
oracle names. Reverted; `grep -c "MUTATION"` → 0; rerun → 1110 passed,
100%/100%.

**Mutation 4b** (the "returning false on parser uncertainty silently excuses
changed code" negative): both fail-closed guard points in
`_has_top_level_func_body` (the embedded-NUL check and the
masked-text-is-`None` check) flipped `True` → `False`. `grep -c "MUTATION:
O4b"` → 2. **Real result: 7 failed** — every unterminated-literal/comment
fixture (block comment, both string-unterminated variants, raw string, rune,
NUL byte), plus `test_an_unknown_go_region_absent_from_coverage_renders_uncovered_lines`
in the O2 module, which flipped from `FAIL/UNCOVERED_LINES` to a bare `PASS`
— proving the silent-excuse direction is reachable and visible at the real
`evaluate_coverage` outcome level, not only as an isolated unit assertion.
Reverted; `grep -c "MUTATION"` → 0; rerun → 1110 passed, 100%/100%.

## Self-review

### Would each oracle's test fail if the behaviour were removed?

Yes for all four — demonstrated above with real mutations (not estimated),
each confirmed present via `grep -c` before the run and absent (`grep -c
"MUTATION"` → 0, exit 1) after revert, with a full rerun back to 1110 passed,
100%/100% after every single revert, including the one mutation (O3) applied
to a file outside this package's own `scope.touch`.

### What is MISSING from the diff the handoff asked for

Nothing in `## Work`. Items 1-5 are honoured as written:

1. **Only the Go adapter and registration added.** `requires_span_attribution
   = False` (A-102); `statement_spans` returns `None` unconditionally. No
   ambient tool invoked anywhere — `external_tools = ()`.
2. **The lexer skips every construct A-104 names** (line comments, block
   comments, interpreted strings with escapes, raw strings, rune literals)
   before searching for a word-boundary `func` token, and correctly handles
   a generics type-parameter bracket via the combined `(`/`[` nesting
   counter. Walks TOP-LEVEL declarations only (brace-depth-gated), matching
   `PythonAdapter.has_executable_code`'s own `tree.body`-only convention.
3. **Hello-world source and a pre-generated coverprofile committed**, plus
   regeneration documentation (in `hello.go`'s own header comment). No Go
   invoked in `tester-unified`.
4. **The existing evaluator and verdict producer exercised unchanged** —
   `evaluate_coverage` is imported and called directly, never modified;
   confirmed by the empty `git diff --stat` against every core file.
5. **Block mapping, strict parser fallback, ambiguity refusal, and
   cross-language equivalence all broken and measured** (A-067) — see the
   four oracle sections above.

### What I implemented that the handoff did not ask for, with justification

* **Two distinct O4 mutations (4a/4b) instead of one.** The oracle's own
  negative names TWO opposite failure directions ("returning false on
  uncertainty" vs "returning true for every file") — testing only one would
  leave the other direction's fail-closed guards unproven by mutation,
  reproducing exactly the kind of asymmetry-blind gap DESIGN-GUIDE §11
  itself warns about (srdm's own "a wrong `true` is a false failure, a wrong
  `false` is a silent excuse").
* **`tests/fixtures/go/hello/doc.go` as a SECOND fixture file alongside
  `hello.go`**, not asked for explicitly by name but required to make
  Work item 3's own "pre-generated coverage text" fixture actually exercise
  the NoCode path the handoff's own §3 citation (`evaluate.go`'s doc.go
  incident) is about — a `hello.go`-only fixture would never demonstrate
  that a file can be legitimately ABSENT from a coverprofile.
* **`test_adapters_go_python_equivalence.py` as its own file**, rather than
  folding O2 into `test_adapters_go_union_fidelity.py`. Kept separate
  because it is the one test module that imports and constructs BOTH
  `PythonAdapter` and `GoAdapter` side by side — a different shape of test
  from every other module here, which only ever touches Go.
* **The struct-field-of-function-type fixture**
  (`test_a_struct_field_of_function_type_is_not_mistaken_for_a_top_level_func`)
  was not named by A-104's own hazard list, but is the sharpest test of the
  "top-level declarations only" instruction available: without brace-depth
  gating, this fixture provably flips to `True` (traced by hand before
  writing the test; the field's own `func() int` type has a genuine
  unmatched `}` — the struct's own closing brace — before any body-opening
  `{`, tripping the same stray-closer fail-closed path O4 exercises for a
  different reason).

### Known-weak spots, stated plainly

Sol's own review flagged this exact package as the project's lowest-confidence
area — extra honesty follows.

* **The fixture corpus is still thin relative to real Go's own grammar.**
  Method receivers (`func (r *T) Name(...)`), multiple-return-value
  signatures wrapped in parens (`func f() (int, error) { ... }`), and
  variadic parameters (`func f(xs ...int) { ... }`) are all handled by the
  SAME generic nesting-counter logic that the generics test already
  exercises (no receiver- or variadic-specific code path exists to be
  under-tested), but none has its OWN dedicated fixture proving that by
  direct observation rather than by code-reading. I traced several by hand
  during design (documented in this package's own design notes, not
  committed) and am confident in the mechanism, but "confident by tracing"
  is weaker than "proven by a committed, independently-reasoned fixture",
  and a reviewer should not read the 30-test file as exhaustive of Go's
  grammar — only of A-104's own named hazard list plus `hascode_test.go`'s
  own four cases.
* **Generics-bracket tracking has no fixture that isolates it as the SOLE
  cause of a correct-vs-incorrect answer.** I attempted, at design time, to
  construct a realistic Go fixture where specifically NOT tracking `[`/`]`
  (while still tracking `(`/`)`) would flip the boolean answer, and could
  not find one: Go's own grammar means a stray `{` inside an untracked
  bracket span (the one realistic case, an inline `interface{ ~int }`
  generics constraint) always happens to still resolve to the CORRECT
  answer even without bracket-tracking, only earlier than a fully correct
  scan would find it. I verified this by tracing the `keysOf[T any]`
  fixture by hand assuming bracket-tracking were absent, and it still
  returns the correct `True`. The combined counter is still the objectively
  more correct implementation (A-104 names it explicitly) and IS exercised
  by the generics fixture, but that fixture's PASS does not, by itself,
  distinguish "bracket-tracking is load-bearing" from "bracket-tracking is
  harmless but unnecessary here" — a real mutation removing it would not
  currently flip any test in this suite. This is recorded rather than
  papered over with a contrived fixture that would not actually represent
  real Go source.
* **`normalize_coverage_key`'s module-path strip has no end-to-end fixture
  routed through the COMMITTED hello-world profile** — the boundary-safety
  tests (`test_adapters_go_normalize_coverage_key.py`) and the end-to-end
  proof (`test_adapters_go_union_fidelity.py`'s own
  `test_normalize_coverage_key_reconciles_the_real_pipeline_end_to_end`) both
  use hand-built, in-test profile text rather than `hello.out`, because
  `hello.out`'s own keys were deliberately written to match the diff paths
  exactly (keeping that fixture's own O1 focus uncluttered by a second,
  unrelated concern). The two concerns are proven independently and both
  genuinely exercise the real adapter method; they are simply never proven
  TOGETHER in one fixture.
* **Nested closures and anonymous function literals assigned to a top-level
  `var`** (`var Handler = func() { ... }`) are, by this module's own design,
  found and correctly classified as `True` if brace-depth is 0 where the
  `func` word appears (which it is, for a top-level `var`) — this is
  actually MORE permissive than `covergate`'s own `HasExecutableCode`, which
  only walks `f.Decls` for a literal `*ast.FuncDecl` and would NOT recognise
  this shape at all. I did not add a fixture proving this specific
  divergence from the cited reference implementation; it is a real,
  reasoned design choice (documented in `go.py`'s own module docstring) but
  is untested by name.

## Things I could not honor exactly as written

None outright refused. No `escalate_if` condition was tripped: the protocol
was sufficient to represent everything this package needed (confirmed by
never touching `adapters/base.py` or `evaluate.py`), and every fixture named
by the handoff (`hascode_test.go`'s four cases, `profile_test.go:137`'s
generics case, `evaluate.go`'s doc.go incident) was reachable and committed
as literal text with no Go toolchain invoked anywhere in this package's
tests. `src/assay/errors.py`, `src/assay/verdict.py`, and
`src/assay/schemas/**` were never needed.
