# RP02-LOG — `nyxloom resync --apply`

Package: RP02 (docs/plan-state-integrity.md Part B.4).
Branch: `feat/state-rp02-resync-apply`. Worktree:
`/workspaces/vbpub/.worktrees/state-rp02-resync-apply`.

## Actions (in order)

1. Created worktree from `main` (`9321dd6`).
2. Read context: `docs/plan-state-integrity.md` (Part B, B.2/B.3/B.4),
   `resync.py` (RP01, on main), `cli.py` (`cmd_resync`), `storage.py`
   (`append_and_apply`, `_validate_before_append`, `apply_event`),
   `types.py` (`ActorKind`, `TASK_TRANSITIONS`, `EventType`), and the
   existing `tests/test_resync.py` + `tests/conftest.py` fixtures.

2a. **Load-bearing design decision (transition-graph walk).** Read
    `types.TASK_TRANSITIONS` carefully: `MERGED` has exactly ONE incoming
    edge, from `MERGE_READY`. Every other non-terminal state
    (CARVED/QUEUED/ACTIVE/AWAITING_REVIEW/SELF_REVIEWING/REVIEW_REJECTED/
    BLOCKED/DRAFT/NEEDS_DECISION/READY_TO_CARVE) has NO direct edge into
    MERGED. B.2's decision table proposes the SAME `ACTION_ADVANCE` for
    all of these once a merge is confirmed, but the apply layer must NOT
    fabricate an illegal (or dishonestly multi-hop) transition. Resolution
    (`_legal_advance_transition` in resync.py):
      - believed == MERGE_READY -> TASK_TRANSITIONED to MERGED (the one
        real legal edge; matches Oracle 1's explicit MERGE_READY example).
      - believed == MERGED, or already TERMINAL -> None (nothing further;
        this is what makes a second `--apply` a true no-op with ZERO new
        events, not just an unchanged statefile — the idempotency oracle
        requires no event at all, and `TaskState` equality doesn't achieve
        that alone because `resync_plan` keeps re-proposing ACTION_ADVANCE
        for an already-MERGED task, so the guard lives in the apply layer,
        not in `resync_plan`, keeping RP01's decision table and its
        existing tests completely untouched).
      - any OTHER non-terminal, non-MERGED believed state -> TASK_SUPERSEDED
        (legal from every non-terminal state per the transition graph —
        verified by inspection of every row in TASK_TRANSITIONS). Semantics:
        the task's OWN nyxloom-tracked lifecycle is retired because the
        real work already landed through an untracked channel; TASK_SUPERSEDED
        does NOT imply "MERGED" happened via nyxloom's own review/gate flow.

3. `types.py`: added `ActorKind.RESYNC = "resync"` (one member, with a
   comment citing RP02).

4. `resync.py`:
   - Extended `ProposedTransition` with `merge_source: str | None = None`
     (default preserves every existing RP01 test's equality/construction).
   - Added `MERGE_SOURCE_REFS` / `MERGE_SOURCE_CONTENT` constants.
   - `_merge_evidence` now returns `(evidence, source)` (or None), tagging
     WHICH `GitFacts` channel fired. `merged_refs` checked first (matches
     `gather_git_facts`'s own gathering order: content-check only runs for
     tasks NOT already resolved by `--merged`).
   - `resync_plan`'s single call site updated to thread `merge_source`
     through; no other behavior change (RP01's own tests unmodified and
     still pass unchanged — verified below).
   - Added the apply layer: `ApplyResult` dataclass, `_legal_advance_transition`,
     `resync_apply(project, states, plan, *, allow_content_merge=False,
     actor_id="resync")`. Calls `storage.append_and_apply` for each legal
     ACTION_ADVANCE row (actor `Actor(ActorKind.RESYNC, actor_id)`, payload
     carries both `"reason"` (verbatim evidence, for the oracle) and
     `"notes"` (the storage.py-documented projection field). SAFETY gate:
     a row whose `merge_source == MERGE_SOURCE_CONTENT` is skipped
     (reported, not applied) unless `allow_content_merge=True`.
     `ACTION_NEEDS_OPERATOR` rows are always reported, never applied.
     `ACTION_NONE` rows are skipped (not even reported — nothing
     actionable).

5. `cli.py`:
   - `cmd_resync` extended: still prints the RP01 dry-run table first
     (unconditionally); if `--apply` was NOT passed, returns 0 exactly as
     before (byte-identical dry-run output/behavior). If `--apply` was
     passed, calls `resync_apply` on the SAME plan/states, then prints an
     "applied N/M; K skipped" summary plus one line per considered
     (non-ACTION_NONE) row.
   - Added `--apply` and `--apply-content-merges` argparse flags to the
     `resync` subparser.
   - Deliberately did NOT add any project-pause check to `cmd_resync` —
     none existed before this package either; resync is an operator verb,
     never routed through the daemon's dispatch loop, so a project-level
     pause flag file has zero effect on it (verified with a dedicated
     test: `test_apply_works_on_paused_project`).

6. Wrote `tests/test_resync_apply.py` (new file) covering all 6 oracles
   (see RP02-REPORT.md for the oracle-by-oracle mapping) plus the legal-
   transition-graph edge case (non-MERGE_READY believed state -> SUPERSEDED,
   not a fabricated MERGED).

7. Ran the full existing test suite + the new file locally in the
   worktree first (fast iteration), then the REAL gate in
   `tester-unified` per the handoff's exact command — see REPORT for the
   tail.

## Deviations from the handoff

None. Touched only `resync.py`, `cli.py`, `types.py` (one `ActorKind`
member), and the new `tests/test_resync_apply.py`, plus these two
handoff docs — matching `scope.touch` exactly. Did not edit
`storage.py`/`storage_sqlite.py`/`reconcile.py`/`daemon.py` (read-only,
used their public API as instructed).

## Status

Not blocked. Proceeding to gate + REPORT + commit.
