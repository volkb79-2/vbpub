# assay-P02 — changed lines and measurability — LOG

**Status:** DONE. Gate green.
**Branch:** `feat/assay-P02-changed-lines-measurability`
**Commit:** `128ae1b7`. **Base:** `main` at `faf502ed`.

## Gate

`tester-unified`, run in the FOREGROUND against HEAD with the container-side
path substituted for the host bind mount:

```
$ cgroup_parent="dev-background.slice"
$ docker run --rm --cgroup-parent="$cgroup_parent" \
    -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
    bash -c 'cd /workspaces/vbpub/.worktrees/assay-P02-changed-lines-measurability/assay \
             && export PYTHONPATH=src && /opt/tester-venv/bin/python -m pytest tests -q'
........................................................................ [  9%]
........................................................................ [ 18%]
........................................................................ [ 28%]
........................................................................ [ 37%]
........................................................................ [ 47%]
........................................................................ [ 56%]
........................................................................ [ 66%]
........................................................................ [ 75%]
........................................................................ [ 85%]
........................................................................ [ 94%]
..........................................                               [100%]
762 passed in 9.29s
GATE_EXIT=0
```

Baseline before this package: 733 passed. This package adds 29 tests.

Coverage, measured in the same image (not asserted by the gate, which
declares `asserts = ["tests-pass"]` only):

```
Name                         Stmts   Miss Branch BrPart  Cover   Missing
------------------------------------------------------------------------
src/assay/__init__.py           10      0      0      0   100%
src/assay/cli.py                40      0      4      0   100%
src/assay/config.py            291      0    144      0   100%
src/assay/diff.py               36      0     16      0   100%
src/assay/errors.py             56      0      4      0   100%
src/assay/git.py                28      0      8      0   100%
src/assay/measurability.py      23      0      4      0   100%
src/assay/verdict.py           293      0    146      0   100%
------------------------------------------------------------------------
TOTAL                          777      0    326      0   100%
762 passed in 9.35s
```

No image rebuild. Nothing but ignored caches (`.coverage`, `__pycache__`)
left in the worktree — verified with `git status --porcelain --ignored`.

## Delivered

| Work item | File | Notes |
|---|---|---|
| 1 | `src/assay/git.py` | 28 executable statements. `run` (the thin subprocess boundary, raises `AssayError`/`GIT_FAILED`), `resolve_base` (merge-first-parent vs. `merge-base`), `head_rev`, `repo_top`, `dirty_paths` (every porcelain category, repo-top-relative, no scoping — scoping is `measurability.py`'s job) |
| 1 | `src/assay/diff.py` | 36 executable statements. `AddedLines` (frozen `kw_only` dataclass wrapping `Mapping[str, frozenset[int]]`), `parse_added_lines` — ported from the union of the three cited sibling gates |
| 2 | `src/assay/measurability.py` | 23 executable statements. `ResolvedBase` (frozen `kw_only`), `check_dirty_tree` (raises `NO_MEASUREMENT`/`DIRTY_TREE`, directory-boundary-correct source-root matching via `Path.is_relative_to`), `check_base_is_head` (raises `NO_MEASUREMENT`/`BASE_IS_HEAD`, else returns `ResolvedBase`) |
| 3 | `tests/conftest.py` | `GitRepo` dataclass + `git_repo` fixture — a real `git`-backed repository materialised under `tmp_path`, shared by all four new test modules |
| — | 4 test modules, 29 tests | one per oracle, per A-066 |

## Per-oracle evidence

Each oracle is followed by the mutation actually applied to prove its tests
bite (A-067). Every mutation below was run against the real gate image (and
independently against the local interpreter first) and reverted; the counts
are real, and every mutation was verified with `grep -c` before trusting the
count (P01b's own LOG records exactly this trap).

### O1 — `parse_added_lines` returns only new-side added line numbers

* `tests/test_diff_added_lines.py`, 12 tests: omitted hunk count (`@@ -10 +11
  @@`), explicit hunk count, `a/`/`b/`-prefixed paths, prefix-free paths
  (`--no-prefix`), a pure-deletion hunk, a deleted file (`+++ /dev/null`,
  including a contrived stray `+` line under it), multiple hunks in one file
  (line numbers must not leak across hunks), multiple files, a context line
  (the one branch `-U0` alone cannot reach), an empty diff, and a
  frozen/`kw_only`/immutable-mapping check on `AddedLines` itself.
* **Mutation** — changed `elif line.startswith("-"): continue` to also
  record and advance (`by_file.setdefault(current, set()).add(new_lineno);
  new_lineno += 1`). `grep -c` confirmed the mutation landed at exactly the
  intended site (both the `+` and mutated `-` branches now call the
  identical line, 2 occurrences). **Real result: 2 failed** —
  `test_pure_deletion_hunk_reports_no_changed_lines_for_that_file` and
  `test_context_line_advances_the_new_side_counter_without_recording_it`.
  The deleted-file tests did NOT fail, because `current is None` still
  guards that path independently — confirming the two guards are
  independently load-bearing, not one covering for the other.

### O2 — base resolution follows the shape of `HEAD`; a failing git command raises `GIT_FAILED`

* `tests/test_git_resolve_base.py`, 6 tests: a normal (non-merge) `HEAD`
  resolves to `merge-base(base, HEAD)`, verified against an INDEPENDENTLY
  computed `git merge-base` call (not routed through `assay.git` twice); a
  real two-parent merge commit resolves to its first parent, with an
  explicit assertion that the result differs from the fork point (proving
  the merge-base branch was NOT coincidentally taken); a forced failure
  (`rev-parse --verify` of a nonexistent ref) raises `AssayError`
  (`ERROR`/`GIT_FAILED`, exit code 2); the failure propagates through
  `resolve_base` itself; `head_rev`/`repo_top` are checked directly
  (`repo_top` from a nested subdirectory, since git repos are not flat).
* **Mutation 1** — `len(tokens) >= 3` → `len(tokens) >= 999999` (never takes
  the merge-commit branch). `grep -c "999999"` confirmed one occurrence.
  **Real result: 1 failed** —
  `test_merge_commit_head_resolves_to_its_first_parent_not_merge_base`.
* **Mutation 2** — `if proc.returncode != 0:` → `if proc.returncode != 0 and
  False:` (swallows every git failure). `grep -c` confirmed one occurrence.
  **Real result: 2 failed** — `test_a_failing_git_command_raises_git_failed`
  and `test_resolve_base_propagates_a_failing_git_command`. No other test
  failed, confirming every successful-path test genuinely never hits a
  failing git call.

### O3 — `DIRTY_TREE` catches staged/unstaged/untracked changes under a source root, never a sibling prefix

* `tests/test_measurability_dirty_tree.py`, 8 tests: a clean tree does not
  raise; a staged change, an unstaged change, and an untracked file under
  the root each raise with the affected repo-relative path named; a renamed
  file reports only the new path (`" -> "` never leaks into the message); a
  dirty sibling directory (`src/foo_evil` next to `src/foo`) does not raise;
  dirty content under a *second* declared root still raises; dirty content
  outside every declared root does not raise.
* **Mutation 1 (string-prefix matching)** — replaced
  `(top / rel).resolve().is_relative_to(root)` with
  `str((top / rel).resolve()).startswith(str(root))`. `grep -c` found 2
  occurrences of the substring, but one is the pre-existing docstring
  describing this exact anti-pattern — the traceback from the failing run
  confirmed the mutated comprehension was the one actually executing.
  **Real result: 1 failed** —
  `test_dirty_sibling_directory_outside_the_root_does_not_raise` — exactly
  the specimen the docstring names.
* **Mutation 2 (drop a porcelain category)** — added `if
  line.startswith("??"): continue` to `git.dirty_paths`, dropping untracked
  files. `grep -c` confirmed one occurrence. **Real result: 1 failed** —
  `test_untracked_file_under_the_root_raises_dirty_tree`.

### O4 — `BASE_IS_HEAD` fires before diff parsing; a clean docs-only commit passes both guards

* `tests/test_measurability_base_is_head.py`, 3 tests: `base` resolving to
  `HEAD` itself raises `NO_MEASUREMENT`/`BASE_IS_HEAD`; a real ancestor base
  clears the guard and returns a `ResolvedBase` whose `head_rev` matches an
  independently computed `HEAD`; a clean, committed, docs-only change
  (touching only a path outside the declared source root) clears BOTH
  `check_dirty_tree` and `check_base_is_head` in the same test.
* **Mutation 1** — deleted the equality guard (`if resolved == head:` → `if
  False:`). `grep -c` confirmed one occurrence. **Real result: 1 failed** —
  `test_base_resolving_to_head_raises_before_any_diff_is_parsed`.
* **Mutation 2** — made `check_dirty_tree` treat every call as dirty (`if
  dirty:` → `if True:`), simulating an over-eager guard that would reject a
  clean, uninteresting delta. `grep -c` confirmed one occurrence. **Real
  result: 4 failed** — the O4 docs-only fixture, plus all three O3
  "does-not-raise" tests (clean tree, sibling-outside, dirty-outside-every-
  root). This is the cross-check that O3's and O4's positive-path tests are
  the same property viewed from two oracles, not two independent claims.

## Self-review

### Would each oracle's test fail if the behaviour were removed?

Yes for all four, demonstrated by seven mutations (not estimated) rather than
asserted, with every mutation's presence verified by `grep -c` before the
count was trusted.

### What is MISSING from the diff the handoff asked for

Nothing in `## Work`. Both of the readiness-pass Work items (5 and 6) are
honoured as written: `AddedLines` and `ResolvedBase` are frozen `kw_only`
dataclasses, never a bare `dict[str, set[int]]`; both guard functions raise
`errors.AssayError` directly, no locally-defined exception type exists in
`git.py`/`measurability.py`.

### What I implemented that the handoff did not ask for

* **`git.repo_top`** — not named by any oracle, but required to correctly
  convert `git status --porcelain`'s always-repo-top-relative paths to
  absolute paths comparable against `source_root_paths` (which resolve
  against the *project* root, potentially a monorepo subdirectory — A-049).
  Without it, `check_dirty_tree` would silently assume `repo` is already the
  git top level, which is false whenever assay itself is invoked from a
  vbpub-style monorepo subdirectory — exactly assay's own situation.
  Verified empirically (see the LOG's investigation notes below) that `git
  status --porcelain` really does stay repo-top-relative regardless of the
  `-C` directory, before writing a single line of source.
* **`git.head_rev`** as its own function rather than inlined — used by both
  `check_base_is_head` and the O2 test suite; kept separate because
  `resolve_base` also needs `HEAD`'s parent list independently, so
  duplicating the `rev-parse HEAD` call inline in two call sites would be
  the thing worth avoiding, not the function boundary itself.
* **`ResolvedBase.head_rev`** — the handoff's example only says "a guard
  result"; carrying both revisions (not just `base_rev`) means a caller
  (P05) never has to call `git.head_rev` a second time to get the same
  answer this guard already computed.

### Decision ids I could not honour as written

None. A-025, A-035, A-049, A-073, A-090, A-091 all apply as cited; A-090 is
P05's obligation, not this package's — this LOG records only that the
guards' return shapes are typed and consumable, per A-090's own reasoning.

### Known-weak spots, stated plainly

* **`check_dirty_tree` and `check_base_is_head` are two separate calls, not
  one combined guard.** Oracle O4's own phrasing ("passes both
  measurability guards") confirmed this reading, but it means P05 must
  remember to call both, in the right place (before any diff parsing) — the
  type system does not enforce the ordering. This is flagged explicitly in
  `assay-P02-BRIEF.md` for whoever wires P05's O4.
* **No pathspec scoping is passed to `git status --porcelain` or `git
  diff`.** `git.dirty_paths` fetches the FULL repository status and
  `measurability.check_dirty_tree` filters client-side by resolved path.
  This is correct (verified: git's own pathspec matching is also
  directory-boundary-safe, so either approach would work) and keeps the
  boundary-matching logic in one Python location that A-067's mutation
  testing can directly target, but it does mean `git status` scans the
  whole working tree rather than only the declared source roots — a
  non-issue at assay's own scale, worth naming if a future consumer has an
  enormous monorepo and a narrow source root.
* **`AddedLines` and the guard functions are not yet wired to anything.**
  This package proves O1-O4 in isolation; A-090 explicitly assigns the
  end-to-end wiring (calling these guards ahead of the four-way coverage
  evaluation) to P05, not to this package.

## Investigation notes (not asked for, but load-bearing for the design)

Before writing `git.py`, I verified two DESIGN-GUIDE claims empirically
against a real git binary rather than trusting the cited implementations'
comments at face value (A-067's spirit applied to research, not just tests):

1. `git status --porcelain -- src/foo` does NOT match `src/foo_evil/b.txt` —
   git's own pathspec matching is already directory-boundary-safe. This
   confirmed a pathspec-based approach would also have worked, but I chose
   client-side `Path.is_relative_to` matching instead (see "known-weak
   spots" above) for testability.
2. `git status --porcelain` reports paths relative to the repository's TOP
   LEVEL even when invoked with `-C` pointing at a subdirectory — confirmed
   by creating a repo with a nested `proj/` directory and running `git -C
   proj status --porcelain`, which still reported `proj/src/a.txt`, not
   `src/a.txt`. This is exactly why `git.repo_top` exists.
