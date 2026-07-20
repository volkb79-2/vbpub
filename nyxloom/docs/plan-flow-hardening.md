# Flow-system hardening — remaining-work plan

Status: **active** · last-verified 2026-07-20

Lossless handoff for the work after the 2026-07-20 Wave A live-bug session.
Authoritative spec: `docs/flow-system-review-and-redesign-CRITIQUE.md` (Fable-xhigh).
This doc records what shipped, what remains, and the sequenced plan.

## Merge discipline (repo is shared)
`main` of the vbpub repo is shared with the operator's own commits (git user =
`nyxloom-carver` / development@richtfest.tech, so ALL commits show that author).
Before every package merge: check if `main` moved past the branch-point; if it
did, build the merged tree with `git merge-tree --write-tree main <branch>` and
CAS-update via `git update-ref refs/heads/main <M> <old>` (never a plain
branch-tree merge — that reverts the operator's intervening commit). Materialize
only the package's own files into the live checkout.

## Wave A — LIVE-BUG hardening: COMPLETE + LIVE (2026-07-20)
Merged this session and running in the daemon (redeployed, PID healthy):

| Pkg | Critique | Defect |
|---|---|---|
| P63 | A11 | M13 auto-merge clobbered live tree; post-merge gate ran on wrong tree |
| P59b | A7 | M6/I8 verdict not attempt-bound (foreign APPROVED could rubber-stamp a merge) |
| P65 | A13 | M11 wave age-trigger read sorted-first not oldest (+ R3 counterfeit-input test) |
| P61 | A9 | M3 wave batching was a label — one frontier session per TASK, not per wave |
| P64 | A12 | M16/M17 dead-signal false alarms (ratchet progress_units, windowed blocked-count, gate blocker) |
| P62 | A10 | M10 planner self-contradiction + M12 no per-action isolation |

Earlier in the initiative: A4(P56) A5(P57) A6(P58) A8(P60). All CRITICALs +
every MEDIUM+ LIVE bug in the critique are fixed & live.

## Remaining Wave A — defense-in-depth (NO live bugs)
**Gap-closure package** (audited 2026-07-20 against current code):
- **A1 run_id**: primary M1 path already closed by P53's receipt rename-on-resume
  + its no-premature-exit test. DEFERRED belt+suspenders: `run_id` written by
  `wrapper.py` + scan matches receipt.run_id to the current run.
- **A2 CREATED-liveness**: CONFIRMED MISSING. A CREATED attempt whose wrapper
  never starts has no liveness timeout. Add one in `reconcile.py`. (Verify the
  carve-task-BLOCKED-escalate half; carve-slot-free already exists via P32/P50.)
- **A3 admission at the effect boundary (R5)**: CONFIRMED GAP. `launch_detached`
  takes no admission token; the 4 human-initiated modules (`intake_chat`,
  `onboarding_scan`, `decision_chat`, `onboarding_questionnaire`) call
  `build_dispatch`/`launch_detached` WITHOUT any paused/budget gate (M15/M18).
  Fix = `dispatch_admissible()` returns an opaque token; `launch_detached(spec,
  token)` refuses without it; thread the token through every launch site. This
  is a cross-cutting refactor — do it with fresh focus, not tail-of-session.
- **A12 part4**: emitter-or-reserved EventType guard (generalize P43 to event
  PRODUCERS). Needs AST-level emission analysis — a regex scan mis-classifies
  consumers (watchdog reads PROGRESS_RECORDED; notify reads WAVE_CLOSED) and
  helper emitters (`_emit_lifecycle`) as (non-)emitters. Remaining dead types
  (PROGRESS_RECORDED, WAVE_CLOSED) are harmless (no false alarms).
- **M20**: carve-exec failure should leave the task READY_TO_CARVE (carve-dispatch
  transactionality) — separate from A10's plan-level atomicity.

## Decisions locked
- **D-060 (APPROVED by operator)**: Wave B is **stages-as-data**, NOT a flow
  language. Compose a fixed set of stage KINDS (implement/frontier_review/carve/
  post_merge_gate/auto_merge/triage/self_review) via a `pipeline=` list in
  nyxloom.toml. Dynamism beyond stage composition (user-defined states/actions/
  conditionals) is explicitly REJECTED — it reopens the unchecked-invariant hole
  the frozen core exists to close.
- **D-061 (resolved autonomously 2026-07-20)**: FIX the progress ratchet (done in
  P64), do not retire it.

## Wave B — flow system (D-060) · 7 packages
See CRITIQUE §4 "Wave B" table (B1–B7) for the authoritative per-package spec:
- B1 `[D-060]` stage-architecture decision doc (mechanism/policy, stage schema,
  pipeline format) — a doc, needs operator sign-off on how much per-project
  divergence is allowed.
- B2 P70 stage registry + composed-pipeline validation (reconcile.py thins toward
  an engine; behaviour-parity suite for the default pipeline).
- B3 P71 per-stage concurrency (`concurrency` replaces lone `max_active_tasks`;
  review serial-1; gates async-with-timeout) — enables the serial/parallel
  scheduling knobs.
- B4 P72 triage stage (mechanical drift-guard tier + LLM tier; re-dispatch embeds
  the review verdict; route-escalation ladder).
- B5 P73 self-review stage (un-reserve Role.SELF_REVIEW; implement→self_review→
  frontier_review; P43 guard updated).
- B6 P74 reviewer session-reuse + spine digest (D-R10) — the reviewer-token
  optimization's second lever, unblocked now that A7 (verdict binding) + A9 (wave
  batching) are in.
- B7 P75 carver re-scope entry (triage architectural/stale → carve packet with
  verdict + drift report; SUPERSEDED only after the carve actually launched —
  uses A10's atomicity).

## Recommended next-phase protocol (Wave B)
Wave B is large enough to warrant the CLAUDE.md planning→implementation
transition: it is its own multi-phase feature. Suggested:
```
Worktree: git worktree add -b feat/flow-stages .worktrees/flow-stages main
Gate: ./scripts/testing-exec.sh "cd .worktrees/flow-stages && MOCK_MODE=true pytest -q"
```
Start with B1 (the decision doc + operator sign-off), then B2 as the foundation.
Consider dogfooding B4–B7 via the (now much-hardened) daemon once B2/B3 land.
