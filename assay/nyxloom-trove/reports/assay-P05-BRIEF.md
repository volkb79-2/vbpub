# P05 — successor brief

You have `src/assay/adapters/base.py`, `src/assay/evaluate.py`,
`src/assay/registry.py`, plus the R1 half of `src/assay/runner.py`. Nothing
is re-exported from `src/assay/__init__.py` — import directly:
`from assay.adapters.base import LanguageAdapter`, `from assay.registry
import Registry, get_adapter, new_registry`, `from assay.evaluate import
evaluate_coverage, CoverageEvaluation`.

## The `LanguageAdapter` protocol — EXACT shape, frozen (A-097)

```python
class LanguageAdapter(Protocol):
    name: str
    source_globs: tuple[str, ...]          # fnmatch patterns; "*" matches "/" too
    excluded_dir_names: frozenset[str]     # bare dir-name segments, e.g. {"vendor"}
    requires_span_attribution: bool        # unused by P05's own logic; for P07
    external_tools: tuple[str, ...]        # A-013; empty if no subprocess boundary

    def is_test_path(self, rel_path: str) -> bool: ...
    def has_executable_code(self, rel_path: str, text: str) -> bool: ...
    def normalize_coverage_key(self, key: str) -> str: ...
```

Do NOT add `statement_spans`, `inject_import_break`, `inject_uncovered_line`,
or `generate_mutants` here — P06/P08 have no scope to touch `adapters/base.py`
at all (verify: your handoff's `scope.touch` is `["src/assay/adapters/python.py",
"src/assay/registry.py", "tests/**"]` — no `base.py`, no `evaluate.py`). If you
find the protocol under- or over-built for real Python fidelity, that is a
BLOCKED signal, not permission to edit the base file yourself.

## The path contract — READ THIS BEFORE WRITING `is_test_path`/`has_executable_code`

Every path string the protocol's methods receive is spelled EXACTLY the way
`git diff` spells a new-side path: forward-slash, relative to the repository's
TOP LEVEL (not the project root, not cwd), no `./` prefix. `evaluate_coverage`
never hands an adapter an absolute path and never hands it a source-root-relative
path — always the raw diff path. Your `is_test_path`/`has_executable_code`
implementations should pattern-match against this spelling directly (e.g.
`"/tests/" in rel_path or rel_path.startswith("tests/")`, `rel_path.rsplit("/",
1)[-1].startswith("test_")`).

`normalize_coverage_key(key)`: *key* is whatever coverage.py's own JSON dict key
looks like for a file (typically relative to wherever `coverage run` had its
cwd — often the project root, sometimes with a different prefix in a monorepo).
Return the SAME spelling the diff uses. For plain Python with no import-path
games, this is very likely the identity function OR a simple prefix
reconciliation if coverage.py's cwd differs from the repo top. Test this
explicitly with a fixture where the two spellings actually differ — P05's own
`test_normalize_coverage_key_reconciles_a_language_specific_prefix` in
`tests/test_evaluate_language_free.py` shows the pattern with a fake prefix.

## The registry's exact registration API

```python
# assay/registry.py
@dataclass(frozen=True, kw_only=True)
class Registry:
    adapters: Mapping[str, LanguageAdapter]

def new_registry(*adapters: LanguageAdapter) -> Registry: ...  # ValueError on duplicate names
def get_adapter(registry: Registry, language: str) -> Registry: ...  # AssayError(ERROR, BAD_LANE_CONFIG) if unknown
```

No module-level default registry exists anywhere, and none should — every
caller (a future `cli.py` wiring, or your own tests) builds a `Registry`
explicitly via `new_registry(python_adapter_instance)`. There is nothing to
"register into" globally; each test should build its own fresh `Registry`.

## `evaluate_coverage`'s exact signature (what your adapter will be fed into)

```python
def evaluate_coverage(
    *, added: AddedLines, profile: CoverageProfile, adapter: LanguageAdapter,
    repo_top: Path, source_root_paths: Sequence[Path], fail_under: float,
    allow_excluded: bool, read_source_text: Callable[[str], str],
) -> CoverageEvaluation: ...
```

`has_executable_code` is consulted ONLY for a changed, considered (under a
source root, matches `source_globs`, not `is_test_path`, not under an
`excluded_dir_names` segment), non-test file that has **no entry at all** in
`profile.files` after `normalize_coverage_key` matching — never for a file
coverage.py DID measure. For real Python, decide this with `ast.parse` (catch
`SyntaxError` — treat as `True`, conservatively assume there IS code rather
than silently excusing an unparseable file, per srdm's own asymmetry lesson
DESIGN-GUIDE §11 states: "a wrong `false` causes a silent excuse").

## What P05 already proved, so you do not need to re-prove it

The four-way union itself (executable requires execution, excluded fails
unless `allow_excluded`, non-executable lines are silently ignored,
lines outside the diff never enter the computation) is `evaluate.py`'s own
tested contract — your job is ONLY to make Python's real classification
(decorators, multiline statements, docstrings, `# pragma: no cover`, async/
compound statements) map correctly onto `executed`/`missing`/`excluded`
before it ever reaches `evaluate_coverage`. That happens entirely inside
`coverage.py`'s own JSON output — your adapter does not touch executed/missing/
excluded sets at all; it only answers `is_test_path`/`has_executable_code`/
`normalize_coverage_key`, and declares `source_globs = ("*.py",)` (or similar).

## Traps

* **`source_globs` uses `fnmatch`, and `*` matches `/` too.** `"*.py"` matches
  `src/pkg/sub/mod.py` at any depth — you do NOT need `"**/*.py"`.
* **`considered` counts FILES, not lines**, and counts a file even if it turns
  out to contribute zero executable lines (a legitimate 0/0). Don't try to
  make your adapter influence this count beyond the four gating checks above.
* **A file WITH a coverage entry never calls `has_executable_code`** — proven
  by `test_has_executable_code_is_never_consulted_for_a_file_coverage_did_measure`.
  If your Python adapter's own tests show it being called for such a file,
  something upstream of your adapter is wrong, not your adapter.
