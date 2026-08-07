# P15 — measurement input integrity — LOG

**Status:** DONE
**Branch:** `feat/assay-P15-measurement-input-integrity`
**Worktree:** `/workspaces/vbpub/.worktrees/assay-P15-measurement-input-integrity/assay`
**Base:** `75c845a2` (`docs(assay): STATE.md -- P15-P25 carved and landed, P26/P27 blocked on usage cap`)
**Handoff `input_revision`:** `9782ddf906bbaead3966934bb7ea143761064f77` (the review commit) — unchanged
between that commit and `75c845a2`; nothing in `src/assay/` moved in between.

Dispatched directly (no separate implementer/controller split for this
package): implemented, self-reviewed, and gate-verified in one session per
explicit instruction.

## What was built

- `src/assay/diff.py`: `parse_added_lines` rewritten from a line-by-line
  content-sniffing scanner (`line.startswith("+++ ")` checked unconditionally,
  including inside hunk bodies) to a real two-state machine (`_State.AWAITING`
  / `_State.IN_HUNK`) driven by each hunk's own declared `-old_count`/
  `+new_count`. A body line's leading marker byte decides everything while
  `IN_HUNK`; header-shaped text (`+++ `, `--- `, `@@ ... @@`) is only ever
  recognised while `AWAITING`, so a genuinely added line whose own content
  begins `++` (sol's finding 3, first reproduction) can never be misread as a
  file header. Git's `\ No newline at end of file` marker (any line starting
  with a literal backslash) is now explicit metadata that advances neither
  side (finding 3, second reproduction) rather than falling into the
  generic "context line" branch. New `_unquote_git_path` reverses git's
  C-style path quoting (named + `\NNN` octal escapes) for a `+++ ` target
  wrapped in double quotes, and strips the one-tab disambiguation marker git
  appends to an unquoted, space-containing path (verified empirically against
  a real git binary — see "Empirical findings beyond the handoff's literal
  text" below).
- `src/assay/git.py`: `run`/new `_run_bytes` both force `-c
  core.quotePath=false` on every invocation (disables non-ASCII octal
  escaping; git still always quotes control bytes/backslash/quote
  regardless). `dirty_paths` rewritten from `git status --porcelain` +
  line-splitting + a literal `" -> "` rename split, to `git status
  --porcelain=v1 -z` read as raw bytes: NUL-terminated records, a rename/copy
  status consuming the next NUL field (the old path) rather than parsing
  displayed arrow text. New `_decode_git_path` implements the decode-or-reject
  policy (`AssayError`/`GIT_FAILED` on non-UTF-8 bytes, never a silent
  replace or a bare `UnicodeDecodeError`). Simplified away one defensive
  branch (a guard for `-z` output whose last split field is non-empty) that
  real git output can never trigger — an untestable guard the module's own
  established precedent (the blank-porcelain-line comment this replaced)
  already argues against keeping.
- `src/assay/coverage_parsers/model.py`: `FileCoverage.__post_init__` now
  enforces, for every constructed instance regardless of which format parser
  built it: every line number in `executed`/`missing`/(known) `excluded` is
  positive, and the three buckets are pairwise disjoint (only checking
  `excluded` when not `None`). Raises a bare `ValueError` (matching
  `StatementSpan`'s own established construction-time-validation shape) —
  this module stays a leaf with zero new imports.
- `src/assay/coverage_parsers/coverage_py_json.py`: `_parse_record` wraps its
  `FileCoverage(...)` construction in `try`/`except ValueError`, converting a
  model-invariant violation into the same `UNREADABLE_ARTIFACT` shape every
  other malformed-record defect already produces. The other three parsers
  (lcov/cobertura/go-cover) are untouched — each already classifies a line
  from one summed hit count, so a line lands in exactly one bucket by
  construction and positivity is already validated before it reaches
  `FileCoverage`; wrapping their construction calls too would add
  provably-unreachable `except` branches.
- `src/assay/evaluate.py`: `evaluate_coverage`'s `cov_by_repo_path`
  construction changed from a bare dict comprehension (silent last-key-wins)
  to an explicit loop that raises `AssayError`/`UNREADABLE_ARTIFACT` the
  moment two distinct raw keys normalize to the same repository path, naming
  both raw keys and the shared path in the message. Module and function
  docstrings updated to document this as the one deliberate exception to
  "nothing here raises."
- `src/assay/config.py`: `_resolve_source_root` gains a containment check
  (`resolved.is_relative_to(project_root)`) AFTER the existing
  absolute/exists checks — `project_root` is already fully resolved by
  `load_lane_file`, so this catches both a `..`-escaping raw string and a
  symlink whose real target sits outside the project root, using the same
  `Path.resolve()` call the existing checks already perform (no new
  filesystem call). `_load_lane` gains an `env`/`env_passthrough` name-set
  intersection check, raised as `LaneConfigError` before the existing bare-
  executable/PATH check — this makes the collision `assay.runner.
  resolve_command_plan` would otherwise silently resolve (fixed env
  overwritten by ambient passthrough) structurally unreachable without
  touching `runner.py`, which is out of this package's `scope.touch`.
- Test files: `test_coverage_parsers_model.py` (new, 13 tests — direct
  `FileCoverage` invariant tests), `test_diff_real_git_fixtures.py` (new, 8
  tests — real `git diff` output through the actual `assay.git.run`
  boundary: `++`-content, no-newline marker, rename, space/tab/Unicode/
  embedded-newline paths, multi-hunk isolation), `test_diff_unquote_git_path.py`
  (new, 6 tests — white-box escape-grammar edge cases a real git binary
  cannot be made to emit: octal escape, truncated escape, unrecognised
  escape), `test_git_dirty_paths.py` (new, 8 tests — real-repo NUL-transport
  proof, a failing-invocation control, plus the decode-or-reject negative),
  `test_evaluate_coverage_key_collision.py`
  (new, 3 tests — collision in both insertion orders, plus a non-colliding
  control), `test_diff_added_lines.py` (6 existing fixtures corrected from
  hunk headers real git could never emit — an implied `old_count=1` with zero
  old-side body lines shown — to balanced, realistic ones; 1 new test for a
  branch the new state machine reaches differently than the old scanner
  did), `test_config_source_roots.py` (+3: `..`-escape, symlink-escape,
  symlink-within-bounds control), `test_config_reject.py` (+3: single
  collision, multiple collisions, disjoint-tables control),
  `test_coverage_parsers_coverage_py_json.py` (+4 malformed-record fixtures:
  both-executed-and-missing, both-executed-and-excluded, zero line, negative
  line), `test_canary_go_pipeline.py` (1 pre-existing test's own fixture
  repaired — see "Housekeeping" below).

## Gate output (real `tester-unified` Docker container)

```
$WT/assay/tools/cgroup-parent.sh  # -> dev-background.slice, verified configured
docker run --rm --cgroup-parent=dev-background.slice \
  -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c '<build+install assay-0.0.0 wheel into a scratch venv, then>
    assay run tester-unified --verdict-json $scratch/verdict.json &&
    PYTHONPATH=$venv_site ASSAY_SELF_HOSTING_VERDICT=$scratch/verdict.json \
      /opt/tester-venv/bin/python -m pytest tests/test_self_hosting.py -q \
      --override-ini=pythonpath='
```

```
Successfully built assay-0.0.0-py3-none-any.whl
Successfully installed assay-0.0.0
tester-unified: PASS (exit 0)
  commit: 75c845a2b80ba6e5c400f5fa7465517d79e6f46b
  argv: python -m pytest tests -q --ignore=tests/test_self_hosting.py --override-ini=pythonpath=
.......                                                                  [100%]
7 passed in 10.92s
EXIT_CODE=0
```

`assay run tester-unified` itself only asserts R0 (command exit code) —
assay's own `assay.toml` declares `rigor = ["R0"]` permanently (A-133); it
does not self-check coverage percentage. Coverage was independently verified
by running `pytest --cov=assay --cov-branch` directly inside the SAME
`tester-unified:local` container (not just the ambient devcontainer python),
against the same source tree, using the container's own
`/opt/tester-venv/bin/python`:

```
1576 passed, 1 skipped, 1 deselected in 62.97s
TOTAL   2529 stmts (0 miss)   986 branches (0 partial)   100%
```

(The 1 deselected test, `test_standalone.py::test_a_real_pass_matches_the_documented_r0_pass_shape`,
is the pre-existing, documented environment-specific case — passes in the
real gate image, fails only from an ambient interpreter with a working
`setuptools_scm`; unrelated to this package, unchanged by it.)

Baseline before this package (ambient devcontainer python, same source
tree at `75c845a2`, before any P15 edit): 1529 passed (per STATE.md), 2433
stmts / 944 branches, 100%. After: 1576 passed (+47), 2529 stmts (+96) / 986
branches (+42), still 100%.

## Per-oracle evidence

### O1 — real Git fixtures produce exact new-side line and dirty-path identities

`test_diff_real_git_fixtures.py` drives real `git diff --unified=0` output
(through `assay.git.run` itself, proving the `-c core.quotePath=false` +
`_unquote_git_path` composition, not just `parse_added_lines` in isolation)
for: a source line whose content begins `++`, git's real no-newline marker
(asserted present in the fixture's own diff text, twice, before trusting the
parser's answer), a rename with a content change, and paths containing a
space/tab/non-ASCII character/embedded newline. `test_git_dirty_paths.py`
proves the same path shapes plus a rename whose OLD path itself contains the
literal string `" -> "` (the exact string a display-format split gets
wrong) through `git.dirty_paths`, plus the decode-or-reject negative on a
real non-UTF-8-named file.

**Mutation (A-067-1, diff state machine):** reinstated the pre-P15
unconditional `if line.startswith("+++ "):` check ahead of the state-gated
one. Reran `test_diff_added_lines.py`/`test_diff_real_git_fixtures.py`/
`test_diff_unquote_git_path.py`: **1 test failed**
(`test_an_added_line_whose_own_content_begins_plusplus_is_not_mistaken_for_a_header`,
real-git fixture reporting `{}` instead of `{"a.py": frozenset({2})}`) —
exactly sol's finding-3 first reproduction. Reverted; `git diff --stat`
against the staged tree showed the file unchanged from committed content.

**Mutation (A-067-2, no-newline marker):** disabled the `line.startswith("\\")`
branch inside `IN_HUNK`. Reran the same three files: **1 test failed**
(`test_the_no_newline_marker_advances_neither_side`, reporting
`frozenset({2})` instead of `frozenset({1, 2})` — the marker line silently
fell through and mis-advanced the counter). Reverted.

**Mutation (A-067-3, NUL path transport):** replaced `dirty_paths`'s body
with the pre-P15 `git status --porcelain` (no `-z`) + line-splitting +
literal `" -> "` rename split. Reran `test_git_dirty_paths.py`/
`test_measurability_dirty_tree.py`: **5 tests failed** — the arrow-text
rename, the space/tab/embedded-newline path fixtures (each returning a
differently-mangled string), and the decode-or-reject negative (which now
raised a bare `UnicodeDecodeError` from inside `subprocess.run`'s own
newline translation instead of the typed `AssayError`, proving that
protection is real too). Reverted.

### O2 — overlapping buckets and normalized-key collisions rejected; order-independent

`test_coverage_parsers_model.py` proves `FileCoverage`'s own invariants
directly (13 tests: positive-line and pairwise-disjoint checks across all
three fields). `test_coverage_parsers_coverage_py_json.py`'s new fixtures
prove the same invariants reachable through a real malformed JSON artifact
(sol's exact false-`PASS`-100.0-still-reporting-missing reproduction).
`test_evaluate_coverage_key_collision.py` proves the normalized-key
collision is rejected in BOTH declaration orders (the same two raw keys,
reversed) — the literal property a last-key-wins dict comprehension
violates — plus a non-colliding control that still evaluates normally.

**Mutation (A-067-4, coverage model):** `FileCoverage.__post_init__` made an
early no-op `return`. Reran `test_coverage_parsers_model.py`/
`test_coverage_parsers_coverage_py_json.py`: **13 tests failed** (all 9
direct model tests plus 4 of the parser's own malformed-record fixtures —
the both-executed-and-missing/both-executed-and-excluded/zero/negative
cases, which construct successfully instead of raising). Reverted.

**Mutation (A-067-5, key collision):** `cov_by_repo_path` reverted to the
bare last-key-wins dict comprehension. Reran
`test_evaluate_coverage_key_collision.py`: **2 tests failed** — both
orderings of the same two colliding keys now silently succeed instead of
raising, confirming the order-dependence the fix removes was real in both
directions, not just one. Reverted.

### O3 — source-root escape and env/env_passthrough collision refused at load

`test_config_source_roots.py`'s new fixtures prove a `..`-escaping raw
string and a symlink whose real target sits outside the project root are
both rejected, using a layout (`test_the_layout_really_distinguishes_the_two_roots`,
pre-existing) that already guarantees the escape target is a REAL, existing
directory — so a check that only asked "does this exist" would have
accepted it. `test_config_reject.py`'s new fixtures prove a name in both
`env` and `env_passthrough` is refused, naming the offending variable(s).

**Mutation (A-067-6, root containment):** the `is_relative_to` check
short-circuited with `if False and ...`. Reran `test_config_source_roots.py`:
**2 tests failed** (the `..`-escape and the symlink-escape fixtures, both
now loading successfully instead of raising `LaneConfigError`). Reverted.

**Mutation (A-067-7, env collision):** the collision-raise short-circuited
the same way. Reran `test_config_reject.py`: **2 tests failed** (single-name
and multi-name collision fixtures, both loading successfully). Reverted.

After every mutation above, `git diff --stat` against the staged (committed)
tree showed no residual difference — each break was applied and reverted in
isolation, never combined with another.

## Empirical findings beyond the handoff's literal text

Two mechanisms were discovered empirically (real git binary, not inferred
from documentation) and were necessary for O1 to hold, though the handoff's
own work items do not name them individually:

1. **`core.quotePath` only disables non-ASCII octal-escaping — it does
   NOT disable quoting for control bytes, a backslash, or a double quote.**
   Confirmed by probing a real embedded-newline and a real tab-containing
   path with `-c core.quotePath=false` set: both were still wrapped in
   double quotes with `\n`/`\t` escapes. This is why `diff.py` still needs
   `_unquote_git_path`'s own C-style decoder even after `git.py` forces
   `core.quotePath=false` on every invocation — the two mechanisms are
   complementary, not redundant.
2. **git appends a literal trailing TAB to an UNQUOTED `---`/`+++` header
   line whenever the path contains a space** (verified: `+++ b/with
   space.py<TAB>`, no such tab for a space-free path) — the one
   disambiguation an otherwise-unquoted line can carry, since nothing else
   marks where the path ends before the line terminator. A trailing tab can
   never be part of the real path here (a real trailing tab is itself a
   control byte and would force full quoting instead), so stripping it
   unconditionally in the not-quoted branch is always correct.

Both are documented in `diff.py`'s own docstrings at the point they matter,
not only here.

## Housekeeping

- Five pre-existing fixtures in `test_diff_added_lines.py` used a hunk
  header with an implied `old_count=1` (`@@ -N +M @@`, no comma) while
  showing ZERO old-side body lines — a shape real git never produces (an
  implied count of 1 requires exactly one `-`/context line). Under the old
  content-scanning parser this was harmless by coincidence; under the new
  counts-driven state machine, one of these fixtures
  (`test_multiple_hunks_in_one_file_do_not_leak_line_numbers_across_hunks`)
  would have left its first hunk permanently open, silently swallowing the
  second hunk's own header as phantom body content — caught by running the
  suite, not by inspection. All five corrected to balanced, real-git-shaped
  headers (an explicit `,0` for a pure insertion, or an added `-old_line`
  for the one fixture specifically testing omitted-count defaulting on both
  sides); every corrected fixture's own expected result is unchanged.
- One pre-existing test, `test_canary_go_pipeline.py::test_a_transformed_run_that_fails_for_the_wrong_reason_survives`,
  constructed a `FileCoverage` with the SAME lines in both `executed` and
  `excluded` (`executed=frozenset({19}) | added, excluded=added`) — exactly
  the class of self-contradictory artifact this package's O2 now rejects.
  Its own docstring already stated the intent ("marks the appended lines
  EXCLUDED (not missing)"); the `| added` in `executed` was excess relative
  to that stated intent. Fixed to `executed=frozenset({19})` only —
  confirmed via `canary.py`'s own reason-code logic (which keys off
  `changed_lines & excluded`, not `executed`) that this does not change the
  test's asserted outcome.
- Removed one genuinely unreachable defensive branch in `git.py` while
  writing `dirty_paths`'s new body (a guard for `-z` output whose last
  split field is non-empty, which real `-z` output can never produce — see
  the module's own comment at the point it was removed) rather than adding
  a test that could never exercise it.

## What could not be honored as written

Nothing. Every named work item, oracle, and constraint was implementable as
specified; no `escalate_if` condition was triggered (no public schema
change was needed for lossless path transport; no legal coverage format was
found unable to express the disjointness the common model now requires).
