# P08 — successor brief (for P09 canary, P11 mutation)

`src/assay/adapters/go.py` ships `GoAdapter`, a frozen `kw_only` dataclass
implementing the frozen `LanguageAdapter` protocol (A-097/A-101, unmodified —
this package touched no line of `adapters/base.py`). You both add methods to
this SAME class; the protocol itself stays frozen (A-084).

## Current shape

```python
@dataclass(frozen=True, kw_only=True)
class GoAdapter:
    name: str = "go"
    source_globs: tuple[str, ...] = ("*.go",)
    excluded_dir_names: frozenset[str] = frozenset()
    requires_span_attribution: bool = False
    external_tools: tuple[str, ...] = ()
    module_path: str = ""

    def is_test_path(self, rel_path: str) -> bool: ...        # "_test.go" suffix
    def has_executable_code(self, rel_path: str, text: str) -> bool: ...
    def normalize_coverage_key(self, key: str) -> str: ...    # module_path strip
    def statement_spans(self, text: str) -> None: ...         # always None
```

`has_executable_code` is a private, narrow, deterministic LEXER — not a real
Go parser: `_strip_comments_and_literals` masks every comment and
string/rune/raw-string literal to same-length whitespace (real newlines kept),
then `_scan_for_top_level_func_body` finds a top-level `func` and
`_scan_signature_for_body` walks its signature (one combined `(`/`[` nesting
counter) for the body-opening `{`. Pure function of `text` alone — no
filesystem, subprocess, or Go toolchain. `external_tools = ()` (A-087); keep
it that way unless your need genuinely cannot be met from committed text.

## What this means for you

**P09 (`inject_import_break`/`inject_uncovered_line`)**: signature `(text) ->
(text, description)`, PURE (A-010) — do not write the file yourselves.
`_strip_comments_and_literals`'s masked output is reusable: same-length text
where every non-code byte range is blanked, so an offset into the mask is a
valid offset into the real text — useful for a safe insertion point that
avoids landing inside a string/comment. For `inject_uncovered_line`
specifically, the point where `_scan_signature_for_body` returns
`(index_past_the_open_brace, True)` is a proven-correct "real top-level
function body start" anchor (never a struct/interface body, never a nested
closure) — reuse it rather than re-deriving.

**P11 (`generate_mutants`)**: signature `(text, lines) -> mutants |
UNSUPPORTED` (A-011). Same masking pass is your likely first move, same
reason. The existing scanner does NOT walk function BODIES — it short-
circuits `True` the instant it finds the opening `{` and never looks inside.
Walking a body's own statements is genuinely new logic.

## Known limitations you are inheriting (LOG's "known-weak spots")

* No dedicated fixtures for method receivers, multi-value return parens, or
  variadic parameters — believed correctly handled by the existing
  nesting-counter (same path as the tested generics case) but unproven by
  name. Add one if your work touches signature parsing.
* `var Handler = func() { ... }` (a top-level closure) IS recognised as "has
  code" here — MORE permissive than `covergate`'s own reference
  (`*ast.FuncDecl`-only). Matters only if you assume parity with it.
* Bracket-depth tracking in the signature scanner has no fixture that
  isolates it as load-bearing (every realistic construct tried still
  resolves correctly without it) — do not assume an existing fixture would
  catch a bracket-handling regression in code you add nearby.
