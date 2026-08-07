# P03 — successor brief

You have `src/assay/coverage.py` and `src/assay/coverage_parsers/**`. Nothing
is re-exported from `src/assay/__init__.py` (out of P03's `scope.touch`) —
import directly: `from assay import coverage` or `from assay.coverage import
FileCoverage, CoverageProfile, FORMAT_REGISTRY, load_coverage_profile,
read_coverage_artifact, check_empty_coverage`.

## The shapes

```python
# assay/coverage_parsers/model.py, re-exported from assay/coverage.py
@dataclass(frozen=True, kw_only=True)
class FileCoverage:
    executed: frozenset[int]
    missing: frozenset[int]
    excluded: frozenset[int] | None   # None = format cannot express exclusions
    @property
    def executable(self) -> frozenset[int]: ...   # executed | missing

@dataclass(frozen=True, kw_only=True)
class CoverageProfile:
    files: Mapping[str, FileCoverage]   # keyed exactly as the artifact spells the path
```

```python
# assay/coverage.py
FORMAT_REGISTRY: Mapping[str, FormatSpec]   # keys: "coverage-py-json", "lcov", "cobertura", "go-cover"

def load_coverage_profile(text: str, *, declared_format: str) -> CoverageProfile:
    """Registry lookup -> sniff cross-check -> parse. Raises AssayError:
    BAD_LANE_CONFIG (unrecognised declared_format — shouldn't happen if you
    went through config.py, but guarded anyway), FORMAT_MISMATCH (content
    doesn't match declared_format's own signature), or whatever the matched
    parser raises (always UNREADABLE_ARTIFACT) on a malformed record."""

def read_coverage_artifact(path: Path, *, declared_format: str) -> CoverageProfile:
    """Thin file-I/O wrapper: reads path, then load_coverage_profile. OSError/
    UnicodeDecodeError -> UNREADABLE_ARTIFACT."""

def check_empty_coverage(profile: CoverageProfile) -> None:
    """Raises AssayError(NO_MEASUREMENT, EMPTY_COVERAGE) iff profile.files is
    empty. Returns None otherwise — including when files are present but
    every one reports an empty executed set (that is a LEGITIMATE 0%
    measurement and must reach evaluation)."""
```

## What P05 specifically needs to call

Your handoff's O4 (A-090/A-093) requires wiring P03's `EMPTY_COVERAGE` guard
ahead of the four-way coverage evaluation, the same way you wire P02's two
guards. The full sequence, in order:

```python
measurability.check_dirty_tree(repo, judge_config.source_root_paths)
resolved = measurability.check_base_is_head(repo, declared_base)
profile = coverage.read_coverage_artifact(
    artifact_path, declared_format=judge_config.coverage.format
)
coverage.check_empty_coverage(profile)
# only now is it safe to parse the diff and intersect it with `profile`.
```

`check_empty_coverage` takes the **already-parsed** `CoverageProfile`, not a
path or raw text — call `read_coverage_artifact` (or `load_coverage_profile`
if you already have the text) first, then hand the result to the guard.
Nothing enforces this ordering for you: calling `check_empty_coverage` before
parsing, or on the wrong profile, compiles fine and is simply wrong.

`judge_config.coverage.format` (the string from `assay.toml`) is already
guaranteed to be a `FORMAT_REGISTRY` key by config.py's own load-time
cross-check (A-068) — you do not need to re-validate it before calling
`read_coverage_artifact`.

## Traps

* **`check_empty_coverage` checks `profile.files`, never any file's
  `executed`/`missing` sets.** A profile with files present, each reporting
  an empty `executed` set, is a legitimate 0% and must NOT be treated as
  `EMPTY_COVERAGE` — conflating the two is the exact defect O4's own negative
  names, and I have a mutation in the LOG proving both directions of getting
  this wrong produce real, different failures.
* **No path reconciliation happens anywhere in this package.** Each parser
  returns file paths exactly as ITS format spells them: coverage.py JSON's
  own dict keys (typically relative to the coverage run's cwd), lcov's `SF:`
  paths (often absolute, as `geninfo` emits them), Cobertura's `filename`
  attribute (relative to the `<source>` element — which this parser does not
  even read), and Go's package-qualified import paths (e.g.
  `github.com/example/pkg/foo.go`, not a repo-relative filesystem path). None
  of these match `source_root_paths` or a git diff's paths without work YOU
  own — this package's own scope note forbids importing an adapter or
  inferring a language, so format-specific path normalization was
  deliberately left undone here.
* **`FileCoverage` fields are `frozenset[int]`, and `excluded` is `frozenset
  | None` — check `is None` before treating it as iterable.** A lane using
  `lcov`/`cobertura`/`go-cover` will always have `excluded is None` on every
  file; only `coverage-py-json` ever returns a real (possibly empty)
  frozenset there. If your evaluation logic does `if file_cov.excluded:`
  without an explicit `is not None` check first, `None` and `frozenset()`
  both look "falsy" and you will silently lose the None/empty distinction
  P03 exists to preserve.
* **A file present in the coverage artifact with BOTH empty `executed` and
  empty `missing`** (e.g. a Cobertura `<class>` with a `<lines/>` element
  present but no `<line>` children, or a `<class>` with no `<lines>` element
  at all) is a real, valid `FileCoverage` — not an error, not filtered out of
  `CoverageProfile.files`. `check_empty_coverage` only cares whether the
  FILE-level mapping is empty, not whether individual files measured
  anything.
* **`load_coverage_profile`'s sniff cross-check uses ONLY the declared
  format's own sniffer** — it never tries other formats and never guesses.
  If a lane's `judge.coverage.format` is right but the artifact path/content
  is stale or wrong, you get `FORMAT_MISMATCH`, not a coincidentally
  successful parse under a different format.

## Spec ambiguities I had to interpret

None beyond A-068/A-092/A-093, which the handoff had already ruled on before
I started. Two design choices not fully dictated by either, flagged in case
you need to revisit them:

1. **Where "malformed" ends and "format mismatch" begins**, for content that
   doesn't even sniff as its declared format (e.g. JSON with no `"files"`
   key at all, or a Go profile with no `mode:` header). I treated these as
   `FORMAT_MISMATCH` (caught by the sniff cross-check before `parse()` ever
   runs) rather than `UNREADABLE_ARTIFACT`, on the reasoning that content
   which doesn't even match the format's signature is a different kind of
   wrong than content that matches the signature but has a broken internal
   record. Each parser module's `parse()` function still independently
   guards these same cases (so a caller that bypasses the registry, as a
   test might, still gets a typed `UNREADABLE_ARTIFACT` rather than a bare
   Python exception) — but through `load_coverage_profile`, you will only
   ever see `FORMAT_MISMATCH` for these.
2. **Cobertura's `<class filename="...">` grouping merges multiple `<class>`
   elements sharing one file** (executed wins on conflict, same rule as
   lcov's repeated `DA:` records) — the DTD permits this but I found no real
   sample exercising it; my fixture is hand-constructed from the DTD's
   permitted shape, not observed in the wild. If a real-world Cobertura
   artifact behaves differently here, this parser's merge behavior — not the
   registry contract — is the place to revisit.
