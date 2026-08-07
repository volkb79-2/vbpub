# P02 — successor brief

You have `src/assay/diff.py`, `src/assay/git.py`, `src/assay/measurability.py`.
None are re-exported from `src/assay/__init__.py` (out of P02's `scope.touch`)
— import them directly: `from assay import diff, git, measurability` or
`from assay.measurability import check_dirty_tree, check_base_is_head,
ResolvedBase`.

## The shapes

```python
# assay/diff.py
@dataclass(frozen=True, kw_only=True)
class AddedLines:
    by_file: Mapping[str, frozenset[int]]   # new-side path -> added line numbers

def parse_added_lines(diff_text: str) -> AddedLines: ...
```

```python
# assay/measurability.py
@dataclass(frozen=True, kw_only=True)
class ResolvedBase:
    base_rev: str
    head_rev: str

def check_dirty_tree(repo: Path, source_roots: Sequence[Path]) -> None:
    """Raises AssayError(NO_MEASUREMENT, DIRTY_TREE); returns None if clean."""

def check_base_is_head(repo: Path, base: str) -> ResolvedBase:
    """Raises AssayError(NO_MEASUREMENT, BASE_IS_HEAD); else returns both revs."""
```

```python
# assay/git.py — the thin subprocess boundary, in case you need it directly
def run(repo: Path, *args: str) -> str: ...          # raises AssayError/GIT_FAILED
def resolve_base(repo: Path, base: str) -> str: ...   # first-parent or merge-base
def head_rev(repo: Path) -> str: ...
def repo_top(repo: Path) -> Path: ...                 # absolute repo top level
def dirty_paths(repo: Path) -> tuple[str, ...]: ...   # repo-top-relative, unscoped
```

## What P05 specifically needs to call

P05's own O4 requires wiring these guards ahead of the four-way coverage
evaluation, "short-circuiting evaluation on any of the three" (P02's two +
P03's `EMPTY_COVERAGE`). Call **both** of these, in this order, before
touching P03's coverage artifact or parsing any diff:

```python
measurability.check_dirty_tree(repo, judge_config.source_root_paths)
resolved = measurability.check_base_is_head(repo, declared_base)
# resolved.base_rev, resolved.head_rev are now safe to diff between.
```

They are **two separate calls, not one combined guard** — O4's own oracle
text ("passes both measurability guards", plural) settled this reading.
Nothing enforces the call order for you; get it wrong and a dirty tree could
reach `resolve_base` before being caught (still eventually correct, since
`check_base_is_head` doesn't care about tree state, but the error a caller
sees would be less specific if base also happens to equal HEAD on a dirty
tree — DIRTY_TREE should win, matching DESIGN-GUIDE's precedence intent).

`source_root_paths` comes straight from `config.JudgeConfig` (P00/P01) —
already resolved, absolute, existing directories. Pass that tuple directly;
`check_dirty_tree` does no filesystem validation of its own.

Getting the actual added-lines-by-file mapping: P02 does NOT provide a
one-call "diff scoped to source roots" function. Get the raw diff text via
`git.run(repo, "diff", "--unified=0", resolved.base_rev, resolved.head_rev)`
(no `--` pathspec — nothing in P02 scopes the diff to source roots, since no
oracle asked for it) and feed it to `diff.parse_added_lines`. Scoping to
source roots, if you need it, is your call to make — the coverage
intersection you already do against per-file coverage data may already do
this scoping implicitly, since coverage.py only reports measured files.

## Traps

* **`check_dirty_tree` scans the WHOLE repo, not just `source_roots`.** It
  filters client-side afterward via `Path.is_relative_to`. This is correct
  (verified against real git: pathspec-based scoping would also work, this
  is just the choice made) but means the underlying `git status` call has no
  `--` pathspec. Not a correctness issue at assay's scale.
* **`git status --porcelain` paths are ALWAYS repo-top-relative**, never
  relative to whatever directory you invoked `-C` with. If you ever call
  `git.dirty_paths` or `git.run(..., "status", ...)` directly instead of
  going through the guard, remember `git.repo_top(repo)` before doing any
  path-prefix comparison — verified empirically, not just trusted from the
  cited implementations' comments.
* **A file that changed by pure deletion is ABSENT from `AddedLines.by_file`**
  — not present with an empty `frozenset()`. Don't `.get(path, ...)` and
  assume a missing key means "file untouched"; it can also mean "file only
  had lines removed."
* **`resolve_base`'s merge-commit branch ignores its `base` argument
  entirely** — a HEAD with ≥2 parents always returns the first parent,
  regardless of what `base` names. This is intentional (ported from all
  three cited sibling gates) but easy to misread as a bug if you're
  expecting `base` to always matter.

## Spec ambiguities I had to interpret

None beyond A-090/A-091, which the handoff had already ruled on before I
started. The one design choice not fully dictated by either: whether
`check_dirty_tree`/`check_base_is_head` should be one combined function or
two. I read O4's "passes both measurability guards" (plural) as settling
this in favor of two — flag it if P05 finds a strong reason to want them
merged; nothing else depends on the split.
