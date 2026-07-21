# RP01 — LOG (probe + dry-run `nyxloom resync`)

Branch: `feat/state-rp01-resync-dryrun`
Worktree: `/workspaces/vbpub/.worktrees/state-rp01-resync-dryrun/nyxloom`
Base: `main` @ `622d4cb` (which contains `9c22b51`, the commit that added
`docs/plan-state-integrity.md`).

## Context read (per the handoff)

- `docs/plan-state-integrity.md` Part B in full (B.0–B.4).
- `src/nyxloom/types.py`: confirmed real `TaskState` enum members — no
  `AWAITING_REVIEW`/`MERGE_READY`/`MERGED`/`COMPLETED`... wait, all of these
  DO exist: `DRAFT, NEEDS_DECISION, READY_TO_CARVE, CARVED, QUEUED, ACTIVE,
  SELF_REVIEWING, AWAITING_REVIEW, REVIEW_REJECTED, MERGE_READY, MERGED,
  VALIDATING, COMPLETED, BLOCKED, SUPERSEDED, CANCELLED`.
  `TERMINAL_TASK_STATES = {COMPLETED, SUPERSEDED, CANCELLED}`. Confirmed
  `TaskStateFile` fields: `handoff_path`, `state`, `project`, `attempts`
  (each `Attempt` has a `.branch` field), no separate "branch name" field on
  the statefile itself.
- `src/nyxloom/storage.py`: `list_states(project) -> dict[str, TaskStateFile]`
  reads only statefiles (no event replay) — exactly what RP01 needs
  (read-only ground-truth gathering, never touches `events.jsonl`).
- Frontmatter reader: `src/nyxloom/frontmatter.py` (`parse_handoff(path) ->
  (Frontmatter, body)`, raises `HandoffParseError` on anything wrong).
  `render.py`'s `_load_frontmatter` is the existing precedent for "never
  raise, presence = exists + parses" — mirrored in `resync.py`'s
  `gather_handoff_presence`. `handoff_path` on a statefile is relative to
  the project's repo `root` (config.ProjectConfig.root), confirmed via
  `paths.py` (state volume is separate from the git-tracked consumer repo)
  and `render.py` (`root / tsf.handoff_path`).
- **Correction vs. the handoff prompt**: `_merged_branches` actually lives
  in `daemon.py` (not `reconcile.py` — `reconcile.py` is 100% pure, no
  subprocess; it only *consumes* a precomputed `merged_branches: set[str]`
  field on `ReconcileInput`). Read `daemon.py:838-859` for the existing
  logic: `git branch --merged <default_branch>`, plus both the bare branch
  name and (for `feat/`-prefixed branches) the bare task-id token, plus
  every task already in a terminal-ish state added directly. `resync.py`'s
  merge-check helper is a **fresh, standalone implementation** (not an
  import from daemon.py, which is a bound method requiring a `Daemon`
  instance) that reproduces the `--merged` half and ADDS the two content
  checks the plan doc calls for: (a) a commit-log grep on the default
  branch for any commit message referencing the branch/task-id token
  (catches a squash commit, which keeps the original branch name in its
  subject line by convention, or a deleted-branch merge — the ref is gone
  but the merge/squash commit's message text survives); (b) an
  `archive`-directory content scan of the default branch's tree
  (`git ls-tree -r --name-only`) for any path containing both "archive"
  and the task_id (generalizes "the handoff's archived path under
  docs/archive" without assuming one fixed archive layout — see CLAUDE.md
  "Docs lifecycle on merge").
- `cli.py`: confirmed the "one project positional arg, thin handler,
  lazy-imports inside the function" pattern (mirrors `status`/`digest`).
  `resync` follows the same shape.

## Design

- `resync.py`:
  - `ProposedTransition` (frozen dataclass): `task_id, believed_state,
    ground_truth, proposed_action, evidence`.
  - `GitFacts` (frozen dataclass): `merged_refs: frozenset[str]`,
    `content_merged: dict[str, str]` (task_id -> evidence string, populated
    ONLY for tasks the `--merged` pass didn't already cover).
  - `gather_handoff_presence(root, states) -> dict[str, bool]` (I/O boundary
    #1: filesystem + frontmatter parse).
  - `gather_git_facts(repo_root, default_branch, states) -> GitFacts` (I/O
    boundary #2: subprocess git calls only).
  - `resync_plan(states, frontmatters, git_facts) -> list[ProposedTransition]`
    — PURE: no I/O, no clock, no git; walks B.2's decision table exactly.
    Precedence inside the planner: terminal state -> no-op first (already
    settled, regardless of any git signal); else merged (via either
    `git_facts` channel) -> propose `MERGED/COMPLETED`; else handoff present
    -> no-op ("open"); else -> `NEEDS_OPERATOR` (orphan / handoff gone).
- `cli.py`: `cmd_resync(args)` — loads project cfg, `storage.list_states`,
  calls both gatherers, calls `resync_plan`, prints a table via the
  existing `_format_table` helper. Registered as `resync <project>` in
  `main()`'s subparser wiring, dispatch branch added to the `if/elif` chain.
  No `--apply` flag (out of scope — RP02).

## Status

Implemented `src/nyxloom/resync.py` (new), `src/nyxloom/cli.py` (`resync`
verb registered), `tests/test_resync.py` (new, 20 tests: pure planner ×9,
git/handoff fact-gatherers ×9 against a real temp git repo (the
`sample_project` fixture) + 1 mocked-OSError edge case, CLI ×2).

First commit `b0fc77f`. First gate run: had to be re-run after realizing
nothing was staged/committed yet — `coverage_gate` diffs against committed
history, so an uncommitted worktree reads as a 0/0 empty diff (a clean
pass that proves nothing). After committing, the real gate run reported
`121/122` (99.2%) — `resync.py:148`, a defensive `if not name: continue`
guard in `_branch_merged_refs` for a blank `git branch --merged` line,
which real git output never produces (unreachable without a contrived
mock). Removed it (folded into an `if name:` wrapper, same behavior, one
fewer line) rather than pragma-ing it — it was genuinely dead code, not a
covered-but-untestable line. Second commit `7ca224e`. Re-ran the gate:
**GATE_EXIT=0**, `diff-coverage OK: 121/121 changed executable lines
covered (100.0% ≥ 100.0% floor)`. Full suite: `1006 passed, 1 xfailed`
(the pre-existing, unrelated `test_invariants.py::test_no_dead_end_draft`
xfail — confirmed by name, not just count, via a separate non-double-`-q`
run since the gate command's literal `-q` stacks with `pyproject.toml`'s
own `addopts = "-q"` into pytest's quieter `-qq`-equivalent mode, which
suppresses the one-line summary entirely; re-ran once more with a single
`-q` to recover the human-readable count for this LOG only — the gate's
own verdict does not depend on that summary line, only on its exit code).

See `RP01-REPORT.md` for the full oracle-by-oracle evidence.
