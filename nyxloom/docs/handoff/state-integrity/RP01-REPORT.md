# RP01 — REPORT (`nyxloom resync` ground-truth re-baseline, probe + dry-run)

Branch: `feat/state-rp01-resync-dryrun`
Worktree: `/workspaces/vbpub/.worktrees/state-rp01-resync-dryrun/nyxloom`
Base: `main` @ `622d4cb`
Commits (this package):
- `b0fc77f` — feat: RP01 implementation (resync.py, cli.py verb, tests, LOG)
- `7ca224e` — fix: drop one dead defensive branch to close the diff-coverage gap

## Gate (tester-unified, the ONLY ship signal)

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -lc 'cd /workspaces/vbpub/.worktrees/state-rp01-resync-dryrun/nyxloom && \
    PYTHONPATH=src /opt/tester-venv/bin/python -m coverage run --source=src/nyxloom -m pytest tests -q && \
    PYTHONPATH=src /opt/tester-venv/bin/python -m coverage json -o /tmp/nyxloom-cov.json && \
    PYTHONPATH=src /opt/tester-venv/bin/python -m nyxloom.coverage_gate --base main \
      --coverage-json /tmp/nyxloom-cov.json --source src/nyxloom' ; echo GATE_EXIT=$?
```

Result (run against commit `7ca224e`):

```
Wrote JSON report to /tmp/nyxloom-cov.json
diff-coverage OK: 121/121 changed executable lines covered (100.0% ≥ 100.0% floor)
GATE_EXIT=0
```

**GATE_EXIT=0. diff-coverage OK: 121/121 (100.0%).**

Full-suite count (separately confirmed with a single `-q`, since the gate
command's own `-q` stacks with `pyproject.toml`'s `addopts = "-q"` into a
quieter mode that suppresses the one-line summary — this does not affect
the gate's verdict, only its human-readability):

```
1006 passed, 1 xfailed in 227.28s (0:03:47)
```

The one xfail is the pre-existing, unrelated
`tests/test_invariants.py::test_no_dead_end_draft` (confirmed by name via
grep, not just count — a documented, out-of-scope `TaskState.DRAFT`
tracking gap, `xfail(strict=True)`, untouched by this package).

### First gate attempt (informational — not the ship signal)

The first gate invocation, run against an UNCOMMITTED worktree, reported
`diff-coverage OK: 0/0 changed executable lines covered (100.0%)` — a
vacuously true pass. `coverage_gate` diffs against **committed** history
(`git diff <merge-base> HEAD`), so nothing had been staged/committed yet
and the diff was empty. Recognized before treating it as a real result;
committed the implementation (`b0fc77f`) and re-ran. That real run found
one genuinely uncovered line (`resync.py:148`, a defensive `if not name:
continue` guard for a blank `git branch --merged` line that real git
never emits — unreachable without a contrived mock). Removed it (folded
into an `if name:` wrapper; same behavior) rather than pragma-ing dead
code, committed (`7ca224e`), and the final run above is 100% green.

## What was built

- **`src/nyxloom/resync.py`** (new) — `docs/plan-state-integrity.md` Part
  B.4:
  - `ProposedTransition` (frozen dataclass): `task_id, believed_state,
    ground_truth, proposed_action, evidence`.
  - `GitFacts` (frozen dataclass): `merged_refs: frozenset[str]`,
    `content_merged: dict[task_id, evidence]`.
  - `gather_handoff_presence(root, states) -> dict[task_id, bool]` — I/O
    boundary #1 (filesystem + `frontmatter.parse_handoff`), mirrors
    `render.py`'s `_load_frontmatter` never-raise contract.
  - `gather_git_facts(repo_root, default_branch, states) -> GitFacts` — I/O
    boundary #2 (subprocess only): reproduces `daemon.py`'s
    `_merged_branches` `git branch --merged` half exactly (bare branch
    name + bare task-id for `feat/`-prefixed branches), THEN — only for
    tasks that check misses — runs the hardened content-check fallback:
    (a) a `git log --grep=<candidate> --fixed-strings` scan of the default
    branch's own history (catches a squash commit, whose message
    conventionally keeps the source branch/PR name, or a merge commit
    whose branch ref was since deleted); (b) a `git ls-tree -r --name-only`
    scan of the default branch's tree for any path containing both
    "archive" and the task_id (generalizes "the handoff's archived path
    under docs/archive" without assuming one fixed archive layout).
  - `resync_plan(states, frontmatters, git_facts) -> list[ProposedTransition]`
    — **PURE**: no filesystem, no subprocess, no clock. Implements B.2's
    decision table with this precedence: (1) already TERMINAL -> no-op;
    (2) merged (via either `git_facts` channel) -> `MERGED/COMPLETED`; (3)
    not merged + handoff present -> no-op ("open"); (4) not merged +
    handoff gone -> `NEEDS_OPERATOR` ("orphan"). Iterates `sorted(states)`
    for a stable, deterministic list order.
- **`src/nyxloom/cli.py`** — `cmd_resync(args)` (new) + `resync` subparser
  (`project` positional, no flags) + `main()` dispatch branch. Loads
  `ProjectConfig`, calls `storage.list_states` + both gatherers +
  `resync_plan`, prints the plan via the existing `_format_table` helper.
  **No `--apply` flag** — RP02, explicitly out of scope. Module docstring's
  interface-contract comment block updated with the `resync` entry.
- **`tests/test_resync.py`** (new, 20 tests).

## Deviation from the handoff prompt (documented, not a scope violation)

The handoff said to read "`reconcile.py`'s `_merged_branches`". That
method actually lives in **`daemon.py`** (`daemon.py:838-859`) —
`reconcile.py` is 100% pure (no subprocess; module docstring says so
explicitly) and only *consumes* a precomputed `merged_branches: set[str]`
field on `ReconcileInput`. `resync.py`'s merge-check helper is therefore a
**fresh, standalone reimplementation**, not an import of a bound
`Daemon` method — this matches the handoff's own scope constraint
("Do NOT edit reconcile.py/daemon.py") more literally than an import
would have (a bound method can't be imported and called without a live
`Daemon` instance anyway). No `reconcile.py`/`daemon.py`/`types.py` edits
were made; confirmed via `git show --stat` on both commits.

## Oracle-by-oracle evidence

1. **The dstdns case** — `tests/test_resync.py::
   test_resync_plan_merge_ready_merged_proposes_advance`: a task believed
   `MERGE_READY`, `git_facts.merged_refs` containing its branch, handoff
   marked absent (archived) -> `proposed_action == ACTION_ADVANCE`
   (`"MERGED/COMPLETED"`), `ground_truth == "merged"`. Reinforced by
   `test_resync_plan_non_terminal_belief_family_all_advance_when_merged`,
   which sweeps ALL of `CARVED, QUEUED, ACTIVE, AWAITING_REVIEW,
   MERGE_READY` through the same merged-git-facts input and asserts every
   one proposes the same advance (B.2 rows 1+2 collapse into one case, as
   documented in `resync_plan`'s own docstring).
2. **Genuinely open** — `test_resync_plan_genuinely_open_queued_no_action`:
   `QUEUED`, empty `GitFacts()`, handoff present -> the single row is
   `proposed_action == ACTION_NONE`, `ground_truth == "open"` (asserted via
   full dataclass equality against the expected `ProposedTransition`, not a
   loose substring check).
3. **Orphan** — `test_resync_plan_orphan_flagged_needs_operator_never_dropped`:
   a statefile with `handoff_path=None`, empty `GitFacts()` -> the row IS
   present in the returned list (never silently dropped) with
   `proposed_action == ACTION_NEEDS_OPERATOR`, `ground_truth == "orphan"`.
4. **Squash/CAS merge detection** — TWO separate tests prove the content
   check independently of `--merged`, against a REAL temporary git repo
   (the `sample_project` fixture's own initialized repo, extended per
   test):
   - `test_gather_git_facts_content_check_archived_path_catches_squash_merge`:
     no branch ref for the task exists at all (the deleted-branch case);
     the commit message deliberately does NOT name the task (so the
     commit-log channel finds nothing); an archived file is committed to
     `main` under `docs/archive/handoff/`. Asserts the task is NOT in
     `merged_refs` (proving `--merged` alone would have missed it) but IS
     in `content_merged`, with the matched path as evidence.
   - `test_gather_git_facts_content_check_commit_log_grep_catches_squash_reference`:
     the complementary content-check channel — an (empty, allow-empty)
     commit on `main` whose message names `feat/<task-id>` (a squash
     commit's conventional subject line) -> found via the commit-log grep,
     evidence string contains `"commit-log match"`.
   Both tests independently assert the row is absent from `merged_refs`
   first, so the "NOT in `git branch --merged`" half of the oracle is
   checked, not assumed.
5. **Purity** — `test_resync_plan_is_pure_and_deterministic`: builds
   plain in-memory `TaskStateFile`/`dict`/`GitFacts` objects (no fixture,
   no filesystem, no `tmp_state`), calls `resync_plan` twice with the
   IDENTICAL objects, asserts `plan1 == plan2` (full dataclass equality,
   not just length) AND that the task order is the stable sorted order
   `["a", "b", "c"]`. `resync_plan`'s only inputs are the three passed-in
   dicts/objects — no import of `subprocess`/`Path`/clock helpers inside
   the function body (verifiable by reading the function; it does not
   reference `_git`, `gather_git_facts`, or `gather_handoff_presence` at
   all).

## Additional test coverage (fact-gatherers, not oracle-required but load-bearing for the 100% gate)

- `test_gather_handoff_presence_present_missing_none_and_malformed` — all
  four presence outcomes in one table-driven test (present+parses,
  missing file, `handoff_path=None`, present-but-unparseable).
- `test_gather_git_facts_branch_merged_detected` — the plain `--merged`
  path (real `git merge --no-ff`), confirming BOTH the bare branch name
  and the `feat/`-stripped bare task-id land in `merged_refs`, and that a
  task already covered by `--merged` gets NO redundant `content_merged`
  entry (the "skip the content check when already resolved" cost
  optimization).
- `test_gather_git_facts_uses_attempt_branch_as_merge_candidate` — a task
  whose real branch name does NOT follow the `feat/<id>` convention (a
  recorded `Attempt.branch`) is still matched against `--merged`.
- `test_gather_git_facts_no_evidence_leaves_task_unmerged` — the negative
  case: genuinely no merge signal anywhere -> absent from both
  `merged_refs` and `content_merged` (so `resync_plan` correctly falls
  through to open/orphan, not a false "merged").
- `test_git_helper_returns_empty_on_nonzero_git_exit` — `gather_git_facts`
  against a directory that is not a git repo at all -> every git
  invocation fails, `_git` fails safe (empty string), `gather_git_facts`
  returns empty `GitFacts()` rather than raising.
- `test_git_helper_returns_empty_on_oserror` — a monkeypatched
  `subprocess.run` raising `OSError` (the "git executable missing"
  case) -> `_git` returns `""`, not an exception.
- `test_resync_plan_terminal_state_wins_precedence_over_merge_signal` —
  proves precedence: a `COMPLETED` task with a matching `merged_refs`
  entry still reports `ground_truth == "terminal"`, `ACTION_NONE` (not a
  redundant re-advance of an already-settled task).
- `test_resync_plan_content_merge_evidence_used_when_not_in_merged_refs` —
  the pure-planner-level counterpart of oracle 4: `GitFacts.content_merged`
  alone (no `merged_refs` entry) still drives the advance.
- `test_resync_plan_missing_frontmatter_entry_defaults_to_not_present` — a
  task_id absent from the `frontmatters` dict (not merely `False`) still
  fails safe to the orphan branch (`dict.get(..., False)`).
- `test_cli_resync_no_tasks_prints_message` / `test_cli_resync_prints_
  table_for_merged_task` — the CLI verb itself: an empty project prints
  `"no tasks"`; a merged `MERGE_READY` task prints a table row containing
  the task id, believed state, and proposed action, AND — the explicit
  dry-run assertion — `storage.load_state` afterward shows the statefile's
  `state` is STILL `MERGE_READY` (RP01 never writes).

## Deviations from scope.touch

None. Touched exactly: `src/nyxloom/resync.py` (new),
`src/nyxloom/cli.py` (module docstring + `cmd_resync` + subparser +
dispatch branch), `tests/test_resync.py` (new),
`docs/handoff/state-integrity/{RP01-LOG,RP01-REPORT}.md` (new, this
package's own handoff artifacts). Confirmed via
`git diff --stat main...HEAD` — no other file appears.
`reconcile.py`/`types.py`/`storage.py`/`daemon.py` were read but not
edited (grep-confirmed no diff hunks against any of them).

## Not built (explicitly out of scope per the handoff)

- `--apply` (RP02): no event is ever emitted, no statefile is ever
  written. `resync_plan` and `cmd_resync` are read/print-only.
- Wiring the pre-resume guard (RP03) or actually re-syncing the live
  dstdns/topos registered projects — this branch never touched the running
  daemon, the real registry, or any live project's state volume; only a
  temporary git-repo fixture and in-memory dataclasses were used in tests.

## Not merged

Per instructions, this branch was NOT merged to `main`. Ready for review
at `feat/state-rp01-resync-dryrun` (`7ca224e`).
