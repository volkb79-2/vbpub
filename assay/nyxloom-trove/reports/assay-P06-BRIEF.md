# P06 — successor brief (for P07, statement-span attribution)

`src/assay/adapters/python.py` ships `PythonAdapter`: a frozen `kw_only`
dataclass implementing P05's frozen `LanguageAdapter` protocol
(`name="python"`, `source_globs=("*.py",)`, `excluded_dir_names=frozenset()`,
`requires_span_attribution=True`, `external_tools=()`), plus one
adapter-private field outside the protocol, `coverage_key_prefix`.

## What each method classifies today

* **`is_test_path`** — dstdns's `_TEST_FILE_RE` verbatim: `tests/` at any
  depth, a `test_*.py` filename, or an exact `conftest.py`. Does NOT
  recognise the `foo_test.py` suffix convention (no reference gate does).
* **`has_executable_code(rel_path, text)`** — `ast.parse`, fail-closed
  (`True`) on `SyntaxError`/`ValueError`; walks only `tree.body` (module
  top level) and returns `True` at the first statement that isn't a bare
  docstring `Expr`. Decorators, `async def`, and every compound-statement
  header trigger `True` since the header itself is a top-level `ast.stmt`.
  A WHOLE-FILE boolean — "does this file have ANY code", never "which
  lines".
* **`normalize_coverage_key(key)`** — boundary-safe strip of
  `coverage_key_prefix + "/"` off *key*'s front; identity when unset or the
  key doesn't start with it at that exact segment boundary.

None of these three methods, and nothing in `evaluate.py` (untouched by
P06), does any multi-line statement attribution. Line classification
comes entirely from the coverage artifact's own JSON; this adapter never
inspects which lines coverage.py reported, only whether a file has an
entry at all and whether its key spells the same file the diff does.

## Where the gap you're closing shows up — concrete example

```python
1  def build_config():
2      return {
3          "a": 1,
4          "b": 2,
5      }
```

Real `coverage.py` attributes the trace hit for this whole statement to
**line 2 only**. Lines 3–5 appear in NEITHER `executed_lines` NOR
`missing_lines` — simply absent. If a diff changes only line 4,
`evaluate_coverage` cannot know it belongs to the statement anchored at
line 2: line 4 is in none of `FileCoverage`'s three sets (already fully
populated by P03's parser — this is what the artifact itself omits, not
P06's classification), so it falls into `evaluate.py`'s rule 4
("non-executable, silently ignored") though it's real and changed. A
change to line 4 alone can sail through the gate with ZERO coverage
signal today. This is the gap dstdns's `statement_spans` mechanism
(`scripts/coverage_gate.py`, "B065/P80" comment block, read in full for
P06) closes: correlate line 4 back to its enclosing statement's own
tracked line so it inherits that line's real status.

Two shapes dstdns is careful about:

1. **Compound statements must not claim their entire body** — an
   `if`/`for`/`def`'s span stops at the first body statement's line, or
   nested statements' own tracked coverage gets swallowed.
2. **A bare docstring `Expr` is never a span anchor.**
   `PythonAdapter._is_bare_string_statement` (private, `python.py`) already
   implements this exclusion for a different question ("has code at all");
   dstdns's `statement_spans` has an equivalent, same name, for "which
   lines does this span cover". Decide whether P07 shares this helper or
   re-derives its own — P06 kept it private, nothing in scope needed a
   second caller.

## Out of scope for P07 (A-098 — do not re-litigate)

Single-line-reported classification (decorators, async/compound headers,
docstrings, comments, pragma tokens) is P06's own proven territory. P07
adds a NEW method to `adapters/base.py` (`statement_spans`, A-084/A-097) —
it does not change `is_test_path`/`has_executable_code`/`normalize_coverage_key`.
