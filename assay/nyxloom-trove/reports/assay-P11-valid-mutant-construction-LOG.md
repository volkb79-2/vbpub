# P11 — valid mutant construction — LOG

**Status:** DONE
**Branch:** `feat/assay-P11-valid-mutant-construction`
**Commit:** `102d8559ffa576404d32b0a5109d044facab0f0b`
**Base:** 1241 passed, 100% statement and branch coverage, 1917 stmts / 746 branches
**After:** 1301 passed, 100% statement and branch coverage, 2064 stmts / 796 branches
(+60 tests, +147 stmts, +50 branches)

## What was built

- `src/assay/mutation.py` (new) — the frozen `kw_only` `Mutant` dataclass
  (`lineno`, `operator`, `description`, `mutated_text`, plus a derived
  `identity` property mirroring `EvidenceDeclaration.identity`), the closed
  `MUTATION_OPERATORS` catalogue (`compare-swap`/`boolop-swap`/
  `bool-const-flip`/`falsy-swap`), and `byte_offset`/`line_for_offset` — the
  UTF-8-byte<->line arithmetic every splice depends on (verified empirically
  that `ast`'s own `col_offset`/`end_col_offset` are byte offsets, not
  character offsets).
- `src/assay/adapters/base.py` — `generate_mutants(text, lines) -> tuple[Mutant, ...] | Literal["UNSUPPORTED"]`
  added to `LanguageAdapter` as the 7th/final method (DESIGN-GUIDE §11's
  full flat surface, reached only now per A-084).
- `src/assay/adapters/python.py` — the real mutation engine: an `ast.walk`
  over `Compare`/`BoolOp`/`Constant`(bool)/`Return` nodes, each producing
  zero or more `_Site` candidates (a private `NamedTuple`: `lineno`,
  `operator`, `description`, byte `start`/`end`, `replacement` bytes),
  filtered to sites whose own line is in the caller's `lines` set, then
  spliced independently against the ORIGINAL bytes (`text_bytes[:start] +
  replacement + text_bytes[end:]`), never against a previously-mutated
  copy and never via `ast.unparse`. `generate_mutants` returns
  `"UNSUPPORTED"` only when `ast.parse(text)` itself fails.
- `src/assay/adapters/go.py` — `generate_mutants` returns `"UNSUPPORTED"`
  unconditionally, one line, never consulting `text`/`lines` at all.

## Key implementation decisions (interpretations beyond A-092/A-112/A-113/A-114/A-115)

1. **`col_offset` is a UTF-8 BYTE offset, not a character offset** — verified
   empirically (not assumed) with a non-ASCII fixture before writing any
   splice code: `ast.parse("x = 'héllo' + 1\n")` reports `col_offset=15` for
   the `1` literal, which only lines up against `line.encode("utf-8")`, not
   against `line` as a `str`. All offset arithmetic in `mutation.py`/
   `python.py` therefore operates on `bytes` throughout, never `str`
   indices, and the fixture `sample.py` deliberately carries a real
   non-ASCII comment (`café`) to prove this in a committed test, not just a
   scratch check.
2. **Per-site line filtering, not per-node.** For `compare-swap`/
   `boolop-swap`, eligibility (`site.lineno in lines`) is checked against the
   PHYSICAL LINE OF THE OPERATOR TOKEN ITSELF (computed via
   `line_for_offset` on the token's own resolved byte offset), not the
   enclosing `Compare`/`BoolOp` node's own `.lineno`. This matters for a
   chain or comparison that wraps across physical lines: a site is only
   emitted if ITS OWN token sits on a declared changed line, never merely
   because the expression started on one. This is a stricter reading of
   "changed-line experiment" than nyxloom's reference (which gates the whole
   node on one `lineno` check), and is not explicitly ruled in
   A-112/A-114/A-115 — flagged here as the one genuine interpretation this
   package made.
3. **`_find_token_span` has no defensive "not found" branch.** Per AUTHORING
   §3b.D (no genuinely-unreachable defensive code under the 100% branch
   gate), the operator-token search trusts the AST's own guarantee that the
   token lies inside the computed gap; a hypothetical mismatch surfaces as a
   real `AttributeError` crash (verified directly in self-review — see O2
   below) rather than a silently-skipped, untested `if match is None`
   branch.
4. **`Mutant.identity` folds in `mutated_text`.** `(lineno, operator,
   description)` alone is not unique for a 3+-operand boolean chain (two
   sites on the same line share all three), so `identity` is
   `(lineno, operator, description, mutated_text)` — still fully derived
   from the four stored fields, no fifth field added, and two genuinely
   different sites always produce different full texts since the splice
   landed at a different byte offset.

## Self-review (A-067: real, reverted mutations, `grep -c`-verified)

All mutations below were applied directly to the COMMITTED file at HEAD
(`102d8559`), verified present via `grep -c <marker>` returning `1`, run
against the full local suite, then reverted with `git checkout -- <file>`
and confirmed clean via `git status --short` / `git diff --stat` (both
empty) before moving to the next mutation. Final state after all four
mutations: clean tree, `1301 passed`.

### O1 — whole-file rewriting instead of splicing

Changed the final `mutated_text=` expression in
`_generate_python_mutants` from the byte splice to
`ast.unparse(ast.parse(<spliced text>))` — reprinting the WHOLE mutated
file through `ast.unparse`, nyxloom's own forbidden mechanism (A-112).

- Marker `SELF-REVIEW-MUTATION-O1`: `grep -c` = 1 (confirmed present).
- Result: **5 failed, 1296 passed** (`test_every_eligible_site_produces_the_hand_derived_exact_mutated_text`,
  `test_a_boolean_chains_untouched_operator_token_is_still_literally_present`,
  `test_the_non_ascii_comment_survives_every_mutation_byte_for_byte`,
  `test_only_sites_on_the_declared_lines_are_returned`,
  `test_o3_actual_identity_manifest_equals_the_independent_expected_manifest_exactly`).
  `ast.unparse` drops the file's own comments (including the `café`
  comment) and reformats whitespace, so every byte-exact assertion failed
  as expected; tests that only check the multiset of `(lineno, operator,
  description)` triples still passed, since `ast.unparse` still preserved
  the semantic change itself.
- Reverted: `git checkout -- src/assay/adapters/python.py`; `git diff
  --stat` empty; `1301 passed` confirmed afterward.

### O2 — Go's UNSUPPORTED replaced with empty-success

Changed `GoAdapter.generate_mutants`'s body from `return "UNSUPPORTED"` to
`return ()`.

- Marker `SELF-REVIEW-MUTATION-O2`: `grep -c` = 1.
- Result: **3 failed, 1298 passed** (all three tests in
  `test_adapters_go_generate_mutants.py`), each failing with
  `AssertionError: assert () == 'UNSUPPORTED'`.
- Reverted; `1301 passed` confirmed.

### O2b — text substitution creating invalid syntax (first attempt, informative failure)

Changed `_COMPARE_TOKEN[ast.LtE]` from `"<="` to `"<>"` (invalid Python 3
syntax). This dict is used for BOTH locating an existing operator's token
in the source AND building a replacement, so this mutation broke the
SEARCH for a genuine `"<="` in the fixture too, producing an `AttributeError`
crash (`_find_token_span`'s trusted-match assumption, decision #3 above)
rather than an isolated invalid-syntax mutant — 12 failed, 1289 passed.
Reverted and replaced with a more surgical mutation (O2c) that corrupts
only the REPLACEMENT text, to demonstrate the specific "creates invalid
syntax" negative in isolation.

### O2c — text substitution creating invalid syntax (isolated)

Changed only the `replacement=` construction in `_compare_swap_sites` from
`_COMPARE_TOKEN[target_cls].encode("utf-8")` to
`(_COMPARE_TOKEN[target_cls] + " )#!@ garbage").encode("utf-8")`.

- Marker `SELF-REVIEW-MUTATION-O2c`: `grep -c` = 1.
- Result: **4 failed, 1297 passed**, critically including
  `test_every_generated_mutant_parses_with_ast_parse` (O2's own direct
  validity oracle) — `ast.parse` raised `SyntaxError` on the corrupted
  `compare-swap` mutants, exactly the negative O2 names.
- Reverted; `1301 passed` confirmed.

### O3 — removing an eligibility filter

Added `ast.In: "in"` / `ast.NotIn: "not in"` to `_COMPARE_TOKEN` and
`ast.In: ast.NotIn` / `ast.NotIn: ast.In` to `_COMPARE_SWAP` — i.e.
"un-excluding" `in`/`not in` from the catalogue, so the fixture's lines 40
(`if a in b:`) and 42 (`if a not in b:`) — deliberately absent from
`EXPECTED_MUTATIONS`/`EXPECTED_IDENTITIES` — now produce mutants.

- Marker `SELF-REVIEW-MUTATION-O3`: `grep -c` = 4 (two dict entries × two
  lines each).
- Result: **4 failed, 1297 passed**
  (`test_every_eligible_site_produces_the_hand_derived_exact_mutated_text`,
  `test_an_unsupported_construct_in_an_otherwise_parseable_file_contributes_zero_sites_not_an_abort`,
  `test_o3_actual_identity_manifest_equals_the_independent_expected_manifest_exactly`,
  `test_o3_manifest_multiset_counts_match_not_just_the_set_of_triples`).
  The manifest-multiset failure names the exact extra identities:
  `(40, 'compare-swap', 'In->NotIn')` and `(42, 'compare-swap', 'NotIn->In')`
  — an identity absent from the independent expected manifest, exactly O3's
  negative.
- Reverted; `1301 passed` confirmed, `git diff --stat` empty.

### Self-review checklist answers (from the handoff, step 6)

(a) **Byte-exact single-span diff, every other byte identical.**
`test_every_eligible_site_produces_the_hand_derived_exact_mutated_text`
proves this for all 27 eligible sites in `sample.py` at once: each
expected text is built by replacing exactly ONE physical line of the
ORIGINAL text (`_with_line_replaced`, computed independently of
`assay.mutation`'s own byte arithmetic) and compared for exact string
equality against the real `Mutant.mutated_text`.
`test_the_non_ascii_comment_survives_every_mutation_byte_for_byte`
additionally proves a specific non-ASCII line stays byte-identical across
every one of the 27 mutants.

(b) **Every generated Python mutant round-trips through `ast.parse`.**
`test_every_generated_mutant_parses_with_ast_parse` calls `ast.parse` on
all 27 real mutants from the full fixture; no exception is caught, so any
failure fails the test itself (proven to actually catch invalid syntax via
the O2c self-review mutation above).

(c) **`GoAdapter.generate_mutants` always returns `UNSUPPORTED`.**
`tests/test_adapters_go_generate_mutants.py` proves this against real,
literal, committed Go source containing an obvious-looking compare-swap
candidate (`if a < b {`), with varying `lines` arguments (empty, single
line, whole file) and varying/garbage/invalid text content — never a
text-guessed mutant.

(d) **A 3+-operand boolean chain produces one `Mutant` per operator token.**
The fixture's `bool_chain_and` (3 operands, line 48) produces exactly 2
`Mutant`s and `bool_chain_or` (4 operands, line 54) produces exactly 3 —
both proven in `test_every_eligible_site_produces_the_hand_derived_exact_mutated_text`
(exact text per site) and explicitly in
`test_a_boolean_chains_untouched_operator_token_is_still_literally_present`,
which additionally asserts each site's mutated line contains exactly one
`and` and one `or` token (never two `or`s, which reassigning the shared
`ast.BoolOp.op` field wholesale would produce).

## Real gate output (verbatim, foreground, `tester-unified:local`)

```
........................................................................ [  5%]
........................................................................ [ 11%]
........................................................................ [ 16%]
........................................................................ [ 22%]
........................................................................ [ 27%]
........................................................................ [ 33%]
........................................................................ [ 38%]
........................................................................ [ 44%]
........................................................................ [ 49%]
........................................................................ [ 55%]
........................................................................ [ 60%]
........................................................................ [ 66%]
........................................................................ [ 71%]
........................................................................ [ 77%]
........................................................................ [ 83%]
........................................................................ [ 88%]
........................................................................ [ 94%]
........................................................................ [ 99%]
.....                                                                    [100%]
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
src/assay/mutation.py                               34      0     12      0   100%
src/assay/registry.py                               22      0      4      0   100%
src/assay/runner.py                                118      0     18      0   100%
src/assay/verdict.py                               370      0    206      0   100%
--------------------------------------------------------------------------------------------
TOTAL                                             2064      0    796      0   100%
1301 passed in 35.16s
GATE_EXIT=0
```

## An implementer error made and corrected during this session

Mid-session, before committing, I ran `git checkout -- src/assay/adapters/python.py`
as part of an EARLIER self-review revert attempt. Because the file had never
been committed yet at that point, this reverted the ENTIRE file back to the
pre-P11 baseline (446 lines, no mutation engine at all) rather than undoing
only the just-applied self-review mutation — `git checkout --` restores from
the index/HEAD, not from "one edit ago". I reconstructed the full P11
implementation from the conversation's own edit history, re-verified it
against the local suite (100% coverage, 1301 passed, byte-for-byte identical
coverage numbers to the pre-incident run), committed immediately, and only
then continued the self-review using `git checkout --` against the now-safe
committed HEAD for each subsequent revert. No data was lost in the final
result, but this is worth naming plainly rather than omitting.

## Things not honored as written, and why

None. Every element of the pinned return contract (A-114), the boolop/
compare-swap byte-splice discipline (A-115), and the "ineligible reasons
live at the test layer" ruling was followed as specified. No forbidden file
(`errors.py`, `verdict.py`, `schemas/`) was touched or needed — confirmed by
`git diff --stat` against the base commit showing only `scope.touch`'s own
five paths.
