# P07 — successor brief (for P08, Go adapter)

`src/assay/adapters/base.py` ships the frozen `LanguageAdapter` protocol at
its FINAL shape — P07 was the one deliberate post-P05 extension the series
carves out (A-084/A-097), and no later adapter package may modify it again.
It is now: **five attributes, four methods.**

```python
name: str
source_globs: tuple[str, ...]
excluded_dir_names: frozenset[str]
requires_span_attribution: bool
external_tools: tuple[str, ...]

def is_test_path(self, rel_path: str) -> bool: ...
def has_executable_code(self, rel_path: str, text: str) -> bool: ...
def normalize_coverage_key(self, key: str) -> str: ...
def statement_spans(self, text: str) -> tuple[StatementSpan, ...] | None: ...
```

`StatementSpan` is a new frozen `kw_only` dataclass in `adapters/base.py`:
`start_line: int, end_line: int` (both 1-based inclusive, `end_line >=
start_line`), refusing malformed construction with `ValueError`.

## Go does NOT have Python's gap — confirmed, not assumed

I read `shared-ramdisk-depot-manager/tools/covergate/profile.go` in full.
`go test -coverprofile`'s own format is confirmed **block-based, not
line-based**: each profile line is `<path>:<startLine>.<startCol>,<endLine>.<endCol>
<numStmts> <count>`, and `ParseCoverProfile` expands it directly —
`for l := start; l <= end; l++ { ... }` — writing EVERY line in `[start,
end]` into `Executed` or `Missing` individually, with executed-wins on
overlap (`} else {` closing one block and opening another). There is no
"the trace hit lands on the block's first line only" behaviour anywhere in
this parser; every line in a Go coverage block gets its own real entry.
**Python's whole gap — a multi-line statement's interior lines missing from
BOTH `executed_lines` and `missing_lines` — is structurally impossible for
this format.** Nothing needs reconstructing after the fact.

## What this means for your adapter

Your Go `LanguageAdapter` should declare `requires_span_attribution =
False` and can safely implement `statement_spans` as:

```python
def statement_spans(self, text: str) -> tuple[StatementSpan, ...] | None:
    return None
```

`evaluate.py`'s rule 3b only calls `statement_spans` when
`requires_span_attribution` is `True` — with it `False`, this method is
never reached at all, so even the trivial `None`-returning body above costs
nothing at runtime; it exists only to satisfy the protocol's structural
shape. Do not attempt to port anything from `srdm/tools/covergate`'s own
block-expansion logic into `statement_spans` — that logic is COVERAGE
FORMAT PARSING (P03's `coverage_parsers/go_cover.py` already owns it, or
will), not statement-span attribution; conflating the two axes is exactly
what DESIGN-GUIDE §11 warns against ("format and language are not the same
axis").

## What's frozen vs. what's still yours

`adapters/base.py` is OFF LIMITS to you (A-097/A-101, no exceptions).
`evaluate.py`'s rule 3b (statement-span attribution) and the `Coverage`/
schema `unclassified_lines`/`files_with_unclassified_lines` pair are also
P07's — you consume them (by declaring `False`), you do not extend them.
Your own `scope.touch` should mirror P06's exactly, one file over:
`adapters/go.py` (new), `tests/**`, plus whatever `external_tools`
declaration `has_executable_code`'s own Go parsing needs (A-013/A-087 —
P08 ships no external Go prerequisite per A-087's own ruling; a narrow
lexer/parser proven from committed text, no toolchain in the devcontainer).
