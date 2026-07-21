# RP02-REPORT — `nyxloom resync --apply`

Package: RP02 (docs/plan-state-integrity.md Part B.4).
Branch: `feat/state-rp02-resync-apply`.
Worktree: `/workspaces/vbpub/.worktrees/state-rp02-resync-apply`.
Code commit: `7cadc4d46a169a02ac134aabef444f1c6efc3044`.

## Gate (the ONLY ship signal) — real run, tester-unified, from the worktree

Exact command from the handoff (`docker run ... tester-unified:local`, coverage
run over `tests`, `coverage json`, then `nyxloom.coverage_gate --base main`),
executed in the FOREGROUND, tail below:

```
........................................................................ [  6%]
........................................................................ [ 13%]
........................................................................ [ 20%]
........................................................................ [ 26%]
........................................................................ [ 33%]
........................................................................ [ 40%]
...............................................................x........ [ 47%]
........................................................................ [ 53%]
........................................................................ [ 60%]
........................................................................ [ 67%]
........................................................................ [ 73%]
........................................................................ [ 80%]
........................................................................ [ 87%]
........................................................................ [ 94%]
................................................................         [100%]
Wrote JSON report to /tmp/nyxloom-cov.json
diff-coverage OK: 62/62 changed executable lines covered (100.0% ≥ 100.0% floor)
GATE_EXIT=0
```

Full suite: all tests passed (one pre-existing `x` = xfail, unrelated to this
package -- present on `main` already). `GATE_EXIT=0`. `diff-coverage OK:
62/62 changed executable lines covered (100.0% ≥ 100.0% floor)` -- every
changed/added executable line in `src/nyxloom` (across `resync.py`, `cli.py`,
`types.py`) ran during the test run; no `# pragma: no cover` needed.

A local (non-Docker) pre-check with the identical `coverage_gate` invocation
was also run first, from `nyxloom/` (cwd matters -- `--source src/nyxloom` is
matched against `git diff --relative`'s cwd-relative pathspec, so `--repo`
must be the `nyxloom/` subdir, exactly as the handoff's `cd .../nyxloom &&`
does): identical `62/62 (100.0%)` result, confirming the Docker run wasn't
masking anything.

## Oracle-by-oracle evidence

All in `nyxloom/tests/test_resync_apply.py` (new file, 12 tests) unless noted.

1. **Applies a high-confidence advance** —
   `test_apply_merge_ready_high_confidence_advances_to_merged`: a
   `merged_refs`-backed `MERGE_READY` row, applied via `resync_apply`,
   advances `storage.load_state` to `TaskState.MERGED` AND writes exactly one
   `TASK_TRANSITIONED` event (verified via `storage.iter_events`) with
   `actor.kind is ActorKind.RESYNC` and the evidence text in
   `payload["reason"]` (plus folded into `payload["notes"]`, which also lands
   on the reloaded statefile's `.notes` per storage.py's own projection
   contract). Also covered end-to-end through the CLI in
   `test_cli_resync_apply_advances_and_prints_summary`.

2. **Idempotent** —
   `test_apply_is_idempotent_second_apply_emits_no_further_events`: applies
   once (1 event), re-plans against the SAME (now-mutated) `states` +
   unchanged `git_facts`, applies again -- the second `resync_apply` call
   returns `applied=False` for that task and `storage.iter_events` still has
   exactly 1 event (not 2). Also re-verified end-to-end via a second
   `cli.main(["resync", "demo", "--apply"])` call in
   `test_cli_resync_apply_advances_and_prints_summary`.

3. **Content-merge safety, BOTH sides** —
   `test_apply_content_merge_only_not_applied_without_opt_in`: a
   `content_merged`-only row is NOT applied by a bare `resync_apply` call
   (statefile untouched, zero events).
   `test_apply_content_merge_only_applied_with_explicit_opt_in`: the SAME row
   IS applied when `allow_content_merge=True`. End-to-end CLI coverage of the
   same split (`--apply` alone vs. `--apply --apply-content-merges`) in
   `test_cli_resync_apply_content_merges_flag_gates_the_squash_case`.

4. **NEEDS_OPERATOR / orphan never auto-applied** —
   `test_apply_needs_operator_row_never_auto_applied`: an orphan
   (`ACTION_NEEDS_OPERATOR`) row is reported as `applied=False` (never
   silently dropped -- it DOES appear in the `ApplyResult` list, per B.3),
   statefile untouched, zero events. Companion test
   `test_apply_action_none_rows_are_skipped_and_not_even_reported` confirms
   the OTHER never-applied bucket (`ACTION_NONE`, genuinely open) produces no
   `ApplyResult` entry at all (nothing actionable to flag).

5. **Paused project is resyncable** —
   `test_apply_works_on_paused_project`: touches the project-level pause flag
   (`paths.pause_flag("demo")`) BEFORE calling
   `cli.main(["resync", "demo", "--apply"])`; the task still advances to
   `MERGED` and the pause flag file is left untouched (resync neither reads
   nor clears it -- confirmed `cmd_resync` never imports/calls anything
   pause-related).

6. **Legal transition (no `TransitionError`)** — every applied test above
   passes without raising, which already covers the two live edges
   (`MERGE_READY`→`MERGED`, `MERGE_READY`→`MERGED` again in idempotency).
   Two DEDICATED tests additionally exercise the harder case -- a
   HIGH-CONFIDENCE merge hit on a believed state that has NO direct edge into
   `MERGED`:
   `test_apply_non_merge_ready_believed_state_uses_superseded_not_merged`
   (`QUEUED` → `TASK_SUPERSEDED`, not a fabricated `MERGED`) and
   `test_apply_active_believed_state_with_merge_also_uses_superseded`
   (`ACTIVE` → `TASK_SUPERSEDED`), both asserting the resulting statefile
   state, the event type, and that no `TransitionError` was raised.

## Content-merge safety design (SAFETY section of the handoff)

`ProposedTransition` gained a `merge_source: str | None = None` field
(default preserves every pre-existing RP01 `ProposedTransition` construction
and equality check in `test_resync.py` unchanged). `_merge_evidence` now
returns `(evidence, source)` where `source` is one of two new constants:
`MERGE_SOURCE_REFS` (a real `git branch --merged` hit -- high confidence) or
`MERGE_SOURCE_CONTENT` (the commit-log-grep / archive-path-scan fallback --
lower confidence, can match an unrelated commit). `resync_plan`'s decision
table itself is otherwise UNCHANGED (zero behavior change to RP01, verified
by the pre-existing `test_resync.py` suite passing unmodified).

`resync_apply` gates purely on `merge_source`: a `MERGE_SOURCE_REFS` row
auto-applies under a bare `--apply`; a `MERGE_SOURCE_CONTENT` row is skipped
(reported, not applied) unless the caller passes `allow_content_merge=True`
(wired to the CLI's new `--apply-content-merges` flag). This is a single,
explicit `if` gate in `resync_apply` (not scattered logic), so the SAFETY
property is easy to audit: grep `MERGE_SOURCE_CONTENT` in `resync.py`.

## Legal-transition mapping (the transition-graph walk)

Read `types.TASK_TRANSITIONS` end-to-end before writing the apply layer:
`MERGED` has exactly ONE incoming edge, from `MERGE_READY`. B.2's decision
table proposes the identical `ACTION_ADVANCE` for EVERY non-terminal believed
state once a merge is confirmed (CARVED/QUEUED/ACTIVE/AWAITING_REVIEW/... all
collapse into the same row per RP01's own docstring), but the apply layer
must never fabricate an illegal or dishonest multi-hop edge just to reach a
nominal "MERGED" target. `_legal_advance_transition` implements the mapping:

- `believed == MERGE_READY` → `TASK_TRANSITIONED` to `MERGED` (the one real
  edge -- the dstdns-P30 / ui-P10 motivating case, and the ONLY case that
  literally reaches `MERGED`).
- `believed == MERGED` or already in `TERMINAL_TASK_STATES` → `None`
  (nothing further -- this is what makes idempotency zero-events-not-just-
  unchanged-state: the guard lives in the apply layer's own mapping, so
  `resync_apply` never even calls `storage.append_and_apply` for an
  already-settled task, rather than relying on `append_and_apply`'s built-in
  from==to no-op, which WOULD still write a redundant event to the log).
- any OTHER non-terminal, non-MERGED believed state → `TASK_SUPERSEDED`
  (legal from every non-terminal state in the graph -- verified by inspection
  of every row in `TASK_TRANSITIONS`): the task's own nyxloom-tracked
  lifecycle is retired because the real work already landed through a
  channel nyxloom wasn't tracking; TASK_SUPERSEDED does NOT claim the
  review/gate pipeline actually ran.

## Deviations from the handoff

None. `scope.touch` honored exactly: `resync.py`, `cli.py`, `types.py` (one
`ActorKind` member: `RESYNC`), `tests/test_resync_apply.py` (new), plus these
two handoff docs. Did not edit `storage.py` / `storage_sqlite.py` /
`reconcile.py` / `daemon.py` -- read-only, used their public API
(`storage.append_and_apply`, `storage.list_states`, `storage.load_state`,
`storage.iter_events`) exactly as instructed. Not BLOCKED.

## Not merged

Per the handoff's hard rule ("Do NOT merge"), this branch is left committed,
gated GREEN, and unmerged for the controller/reviewer to merge with
`--no-ff` after review.
