# assay-P03 — coverage formats registry — LOG

**Status:** DONE. Gate green.
**Branch:** `feat/assay-P03-coverage-formats-registry`
**Commit:** `972e52c4`
**Base:** `main` at `e97d6e6f`.

## Gate

`tester-unified`, run in the FOREGROUND against HEAD with the container-side
path substituted for the host bind mount:

```
$ cgroup_parent="dev-background.slice"
$ docker run --rm --cgroup-parent="$cgroup_parent" \
    -w /workspaces/vbpub/.worktrees/assay-P03-coverage-formats-registry/assay \
    -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
    bash -c 'export PYTHONPATH=src && /opt/tester-venv/bin/python -m pytest tests -q --cov=src/assay --cov-branch --cov-report=term-missing'
........................................................................ [  8%]
........................................................................ [ 16%]
........................................................................ [ 25%]
........................................................................ [ 33%]
........................................................................ [ 42%]
........................................................................ [ 50%]
........................................................................ [ 59%]
........................................................................ [ 67%]
........................................................................ [ 76%]
........................................................................ [ 84%]
........................................................................ [ 93%]
........................................................                 [100%]
================================ tests coverage ================================
_______________ coverage: platform linux, python 3.14.6-final-0 ________________

Name                                             Stmts   Miss Branch BrPart  Cover   Missing
--------------------------------------------------------------------------------------------
src/assay/__init__.py                               10      0      0      0   100%
src/assay/cli.py                                    40      0      4      0   100%
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
src/assay/git.py                                    28      0      8      0   100%
src/assay/measurability.py                          23      0      4      0   100%
src/assay/verdict.py                               293      0    146      0   100%
--------------------------------------------------------------------------------------------
TOTAL                                             1047      0    426      0   100%
848 passed in 8.46s
GATE_EXIT=0
```

Baseline before this package: 762 passed, 777 stmts / 326 branches, 100%.
This package adds 86 tests, 270 statements, 100 branches — all still 100%
statement and branch coverage.

`git status --porcelain --ignored` after the run shows only intended changes
plus ignored caches (`.coverage`, `.pytest_cache`, `.hypothesis`,
`__pycache__`) — nothing else left in the worktree.

## Delivered

| Work item | File | Notes |
|---|---|---|
| 1 | `src/assay/coverage_parsers/model.py` | `FileCoverage` (frozen `kw_only`, `executed`/`missing`/`excluded: frozenset[int] \| None`, plus a derived `.executable` property) and `CoverageProfile` (frozen `kw_only`, `files: Mapping[str, FileCoverage]`) — the normalized model, DESIGN-GUIDE §11's exact shape. A leaf module with zero sibling imports, so both `coverage.py` and every parser module can import it with no cycle. |
| 1, 3, 4 | `src/assay/coverage.py` | `FormatSpec` (frozen `kw_only`, `parse`/`sniff` callables), `FORMAT_REGISTRY` (`Mapping[str, FormatSpec]`, four keys), `load_coverage_profile(text, *, declared_format)` (registry lookup → sniff cross-check → parse; `FORMAT_MISMATCH`/`BAD_LANE_CONFIG`), `read_coverage_artifact(path, *, declared_format)` (thin file-I/O boundary, `UNREADABLE_ARTIFACT` on OS/decode errors), `check_empty_coverage(profile) -> None` (A-093's named guard) |
| 2 | `src/assay/coverage_parsers/coverage_py_json.py` | strict `coverage.py` JSON parser; the only format where `excluded` is a real `frozenset` (possibly empty), never `None` |
| 2 | `src/assay/coverage_parsers/lcov.py` | strict lcov `.info` parser (`SF:`/`DA:`/`end_of_record`); multi-`DA:`-per-line summed; `excluded` always `None` |
| 2 | `src/assay/coverage_parsers/cobertura.py` | strict Cobertura XML parser (`ElementTree`); multi-`<class>`-per-file merged, executed wins; `excluded` always `None` |
| 2 | `src/assay/coverage_parsers/go_cover.py` | strict Go coverprofile parser; block-range expansion, executed-wins-on-overlap, last-colon Windows-drive-letter split; `excluded` always `None` |
| 3 | `src/assay/config.py` | `_load_coverage` now cross-checks `judge.coverage.format` against `coverage.FORMAT_REGISTRY`'s keys — `ERROR`/`BAD_LANE_CONFIG` on an unknown key (A-068) |
| — | 8 test modules, 86 tests | per-format (O1/O2/O3), registry-level (O2), excluded-semantics (O3), empty-guard (O4), config cross-check (O2) |

## Per-oracle evidence

Every mutation below was applied to the file, its presence confirmed with
`grep -c` before the test run, then reverted and re-verified clean
(`grep -c` again returning 0, `git status --porcelain` showing no diff in
that file) before moving to the next (A-067).

### O1 — independent fixtures parse all four formats into the same normalized model, including multiple blocks per line and Windows drive-letter paths

* 4 parser test modules (`test_coverage_parsers_{coverage_py_json,lcov,cobertura,go_cover}.py`)
  plus `test_coverage_registry.py`, all driven through the public entry point
  `load_coverage_profile` (not the parser modules' own `parse()` directly, for
  the positive fixtures) so a registry-level regression is caught, not only a
  per-format one. The Go fixture carries a genuine two-block overlap on one
  line (`foo.go` line 5: block 1 executed lines 3–5, block 2 never-taken lines
  5–7) and a Windows path (`C:\Users\dev\project\pkg\bar.go`) with the same
  overlap on line 12. Non-canonical extensions (`.rs` under `lcov` and
  `go-cover`, `.java`/`.ts` under `cobertura`/`coverage-py-json`) prove no
  format is bound to a language.
* **Mutation 1 (collapse "executed wins on overlap")** — in `go_cover.py`,
  changed `elif not already_executed:` to `elif True:`, so a later
  never-taken block always downgrades an already-executed line back to
  missing. `grep -c "elif True:" src/assay/coverage_parsers/go_cover.py` → 1.
  **Real result: 1 failed** —
  `test_windows_drive_letter_path_and_overlapping_blocks_normalize_correctly`.
  `test_executed_still_wins_when_the_never_taken_block_is_parsed_first` did
  NOT fail: that fixture's overlap is (missing-block, then executed-block),
  which the mutated `elif` branch never touches (it only breaks
  executed-then-missing), confirming the two tests exercise genuinely
  different orderings rather than one covering for the other.
* **Mutation 2 (remove a registered format)** — deleted the `"go-cover"` entry
  from `FORMAT_REGISTRY` in `coverage.py`.
  `grep -c '"go-cover": FormatSpec'` → 0 (confirmed removed).
  **Real result: 21 failed** — every `go-cover`-driven test across
  `test_coverage_parsers_go_cover.py` (20 tests) plus
  `test_coverage_registry.py::test_registry_has_exactly_the_four_formats_this_package_ships`
  (the literal-set assertion, not a `>= 4` check).

### O2 — malformed record / unknown format key / signature mismatch each raise a closed typed error at the right time; declaration selects, sniffing only cross-checks

* Per-format `test_malformed_*`/`test_malformed_block_*`/`test_malformed_tracefile_*`/
  `test_malformed_xml_*` parametrized tests (9 lcov cases, 6 cobertura cases,
  11 go-cover cases, 3 coverage-py-json cases plus 2 direct-`parse()` cases)
  cover `UNREADABLE_ARTIFACT`; one `declared_format_mismatch` test per format
  plus `test_coverage_registry.py` cover `FORMAT_MISMATCH`;
  `test_config_coverage_format.py` covers `BAD_LANE_CONFIG` at config-load
  time, distinct from both parse-time codes.
* **Mutation 1 (ignore malformed records)** — in `coverage_py_json.py`'s
  `_int_list`, changed `if not isinstance(item, int) or isinstance(item, bool):`
  to `if False and (...):`. `grep -c "if False and (not isinstance"` → 1.
  **Real result: 2 failed** —
  `test_malformed_record_raises_unreadable_artifact[missing_lines-contains-a-non-int]`
  and `test_bool_is_rejected_as_a_line_number_even_though_bool_is_an_int_subclass`
  (the `isinstance(True, int)` trap this check specifically guards).
* **Mutation 2 (remove the independent signature cross-check)** — in
  `coverage.py`'s `load_coverage_profile`, changed `if not spec.sniff(text):`
  to `if False and not spec.sniff(text):`. `grep -c "if False and not spec.sniff"` → 1.
  **Real result: 7 failed** — the four per-format
  `test_declared_format_mismatch_is_refused_before_any_record_is_read` tests,
  the two coverage-py-json-specific signature tests
  (`test_no_files_key_fails_the_signature_cross_check_before_parsing`,
  `test_not_valid_json_fails_the_signature_cross_check_before_parsing`), and
  `test_coverage_registry.py::test_read_coverage_artifact_still_cross_checks_the_signature`.
  Nothing else failed, confirming every positive-path test genuinely never
  depends on the cross-check firing.
* **Mutation 3 (select a parser from sniffed content instead of the
  declaration)** — in `coverage.py`'s `load_coverage_profile`, inserted a loop
  after the registry lookup that reassigns `spec` to the first
  `FORMAT_REGISTRY` entry whose `sniff(text)` is `True`, ignoring
  `declared_format`. `grep -c "_candidate_spec.sniff(text)"` → 1. **Real
  result: 5 failed** — the four `declared_format_mismatch` tests (each of
  which passes content that DOES sniff as a real, different, registered
  format) plus `test_read_coverage_artifact_still_cross_checks_the_signature`.
  The two coverage-py-json signature tests (`no-files-key`, `not-valid-json`)
  did **not** fail this time — their fixtures match NO registered format's
  sniffer, so the substitution loop finds nothing to swap in and the
  originally-declared spec is used regardless, correctly still raising
  `FORMAT_MISMATCH`. This is the precise, real distinction between "ignore
  the cross-check entirely" (7 failures) and "select from content instead of
  declaration" (5 failures) — a mutation whose failure count I would have
  gotten wrong by guessing rather than running it.
* **Mutation 4 (unknown format key accepted at config load)** — in
  `config.py`'s `_load_coverage`, changed `if fmt not in FORMAT_REGISTRY:` to
  `if False and fmt not in FORMAT_REGISTRY:`.
  `grep -c "if False and fmt not in FORMAT_REGISTRY"` → 1. **Real result: 1
  failed** — `test_unregistered_coverage_format_is_rejected_at_load_time`.

### O3 — `excluded` preserves `None` (cannot express exclusions) distinct from `frozenset()` (can, reports none)

* `test_coverage_excluded_semantics.py`'s paired Go/coverage.py fixture (same
  executed/missing sets, only `excluded` differs) plus a
  `test_formats_without_exclusion_support_always_report_none` parametrized
  check across `go-cover`/`lcov`/`cobertura`, plus each per-format module's
  own `test_excluded_is_always_none_for_this_format`.
* **Mutation** — changed `excluded=None` to `excluded=frozenset()` in all
  three of `lcov.py`, `cobertura.py`, `go_cover.py` (one call site each).
  `grep -c "excluded=frozenset()"` across the three files → 1 each (3 total,
  confirmed each landed at its own single call site, not zero and not
  duplicated). **Real result: 16 failed** — every exact-`FileCoverage`-equality
  assertion across the three formats' own test modules (basic fixture,
  multi-record/multi-class/overlap fixtures, the "unrecognised record types
  ignored" fixture — 12 tests), each format's own `excluded_is_always_none`
  test (3 tests), the cross-format paired-fixture test, and the parametrized
  `test_formats_without_exclusion_support_always_report_none` (its 3 cases
  reported as one failing parametrization block per format). This is the
  broadest-impact mutation of the four oracles, which is expected: `excluded`
  is a field of the dataclass every positive fixture asserts equality
  against, so any test asserting an exact `FileCoverage` for one of the three
  `None`-only formats is automatically an O3 witness, not only the tests
  explicitly named for O3.

### O4 — `check_empty_coverage` distinguishes zero-files from zero-executed-lines

* `test_coverage_empty_guard.py`: a zero-file `CoverageProfile` raises; a
  non-empty profile whose one file reports an empty `executed` set does not
  raise; both re-verified through a REAL parsed `coverage-py-json` artifact
  (`{"files": {}}` vs. a file with `"executed_lines": []`), not only
  hand-built `CoverageProfile` objects. A signature-introspection test
  (`inspect.signature`) asserts the guard's parameter list is exactly
  `["profile"]` and its return annotation is `None` — A-093's literal
  interface requirement, since a wrongly-shaped guard would satisfy every
  behavioral test here while still failing P05's ability to call it
  independently.
* **Mutation 1 (disable the guard)** — changed `if not profile.files:` to
  `if False and not profile.files:`. `grep -c "if False and not profile.files"` → 1.
  **Real result: 2 failed** — `test_zero_files_profile_raises_no_measurement_empty_coverage`
  and `test_real_zero_files_artifact_parsed_end_to_end_raises`.
* **Mutation 2 (conflate zero-files with zero-executed-lines — the exact
  defect O4's negative names)** — changed the guard condition to
  `if not any(fc.executed for fc in profile.files.values()):`.
  `grep -c "if not any(fc.executed for fc in profile.files.values())"` → 1.
  **Real result: 2 failed** — `test_nonzero_files_with_empty_executed_sets_does_not_raise`
  and `test_real_nonzero_files_zero_executed_artifact_parsed_end_to_end_reaches_evaluation`
  (both false positives: a real, non-vacuous 0% measurement now wrongly
  raises `EMPTY_COVERAGE`). The two true-empty-artifact tests did **not**
  fail — `any(... for fc in {}.values())` is `False` regardless, so the
  conflated check still happens to raise correctly for a truly empty
  `files` mapping. This is the real, useful distinction: the "zero files"
  and "zero executed lines" cases are not symmetric under this particular
  wrong implementation, and only running it (rather than assuming both
  guard tests would fail) surfaced that.

## Self-review

### Would each oracle's test fail if the behaviour were removed?

Yes for all four, demonstrated by 9 mutations (not estimated), with every
mutation's presence and absence confirmed by `grep -c` before and after,
matching P02's own discipline.

### What is MISSING from the diff the handoff asked for

Nothing in `## Work`. Items 1, 3, 4's rulings (A-092, A-068, A-093) are
honoured as written:

* every typed domain result (`FileCoverage`, `CoverageProfile`, `FormatSpec`)
  is a frozen `kw_only` dataclass — never a bare `dict`/map shape, despite
  every cited sibling implementation (`dstdns`'s `dict[str, dict]`, `topos`'s
  plain-`dict` validation, `srdm`'s `FileCoverage{Executed map[int]bool}`)
  using exactly that shape;
* every reject path raises `errors.AssayError` directly (via a `_malformed`
  factory-function pattern per parser module — a function that *returns* an
  `AssayError` for the caller to `raise`, not a new exception *type*, so
  A-092's "no locally-defined exception type" holds literally: `grep -rn
  "^class.*Error" src/assay/coverage.py src/assay/coverage_parsers/` finds
  nothing);
* `check_empty_coverage(profile) -> None` is named, independently callable,
  and reachable without a full parse — verified both behaviorally and by
  signature introspection (see O4 above);
* `judge.coverage.format` is cross-checked against `FORMAT_REGISTRY`'s keys
  at config-load time, with `UNREADABLE_ARTIFACT` and `FORMAT_MISMATCH`
  staying strictly parse-time and never collapsed into `BAD_LANE_CONFIG` or
  into each other (this is what O2's four-mutation set above actually
  proves, not just documents).

### What I implemented that the handoff did not ask for

* **`FileCoverage.executable` property** (`executed | missing`) — not part of
  DESIGN-GUIDE §11's literal three-field shape, added because every cited
  sibling implementation (dstdns, topos, srdm) computes this exact union at
  its own call site (`missing | executed`, `Executable(line)`), and P05 will
  need it again to intersect changed lines against "does this format know
  about this line at all". A derived `@property`, not a fourth stored field,
  so it can never disagree with `executed`/`missing`.
* **`read_coverage_artifact(path, *, declared_format)`** — a thin file-I/O
  wrapper around the pure `load_coverage_profile(text, *, declared_format)`,
  mirroring `git.py`'s split from `diff.py` in P02. Not named by any oracle,
  but P05 will need to actually read `judge.coverage.artifact` from disk, and
  without this it would have to hand-roll the same
  OSError/UnicodeDecodeError-to-`UNREADABLE_ARTIFACT` mapping `git.py`
  already established as house style for I/O boundaries.
* **`coverage_parsers/model.py`** as a dedicated leaf module for
  `FileCoverage`/`CoverageProfile`, re-exported from `coverage.py`. Not
  dictated by the handoff (which only names the two dataclasses, not a file
  layout), chosen to break what would otherwise be a real import cycle:
  `coverage.py` needs to import every parser module to build the registry,
  and every parser module needs the two normalized types.
* **Defensive unknown-format-key handling inside `load_coverage_profile`
  itself** (raising the same `LaneConfigError` config.py would, rather than a
  bare `KeyError`) — belt-and-suspenders for a caller that reaches this
  function without going through `config.py`'s own cross-check (a test, or a
  future direct caller). Verified by `test_coverage_registry.py::
  test_unrecognised_declared_format_raises_lane_config_error`.

### Known-weak spots, stated plainly

* **lcov and Cobertura's "no exclusion concept" reasoning is my own inference
  from the public formats, not a cited estate precedent** — the handoff
  states this explicitly has no prior art anywhere. I verified it against
  the geninfo spec (lcov: exclusion markers are resolved by the `lcov`/
  `geninfo` COMMAND before the `.info` file is written, leaving no per-line
  trace) and against the real Cobertura DTD plus the actual netcup sample
  (`<line>` has no exclusion attribute, and none appears in the real file
  either). This is the one place a domain expert could correct me if either
  format turns out to carry a convention I didn't find.
* **Cobertura's `<class filename="...">` grouping assumes the DTD's
  permissive "many `<class>` per file" case might occur**, and merges with
  "executed wins" the same way lcov's repeated `DA:` records do. This is
  untested against any REAL multi-class-per-file Cobertura output (the
  netcup sample never does this — one `<class>` per Python module) — my
  fixture for it is hand-constructed to exercise the DTD's permitted shape,
  not observed in the wild.
* **`go_cover.py`'s `numStmts` field is parsed and validated but never used**
  — kept only so a corrupted `numStmts` fails loudly (`isdigit()` check)
  rather than being silently accepted; the normalized model has no field for
  it, matching DESIGN-GUIDE §11's `FileCoverage` shape exactly.
* **No pathspec/format-specific path normalization happens anywhere in this
  package** — each parser returns file paths exactly as its artifact spells
  them (coverage.py JSON's own keys, lcov's `SF:` paths, Cobertura's
  `filename` attributes relative to `<source>`, Go's package-qualified
  paths). Reconciling these against `source_root_paths` is explicitly P05's
  job (this package's "Scope / forbid" note: "must not import an adapter or
  infer a language"), but it means P05 will need format-aware path
  reconciliation it does not yet have a guard for — flagged in the BRIEF.

### Decision ids I could not honour as written

None. A-013 (adapters may shell out, declare `external_tools`) does not apply
here — this package parses artifacts, not language source, and never shells
out to anything (no `go`, no `lcov`, no `coverage` binary). A-035 and A-042
apply as cited (the `EMPTY_COVERAGE` guard, and fixtures-not-toolchain);
A-068/A-092/A-093 are discharged as detailed above.
