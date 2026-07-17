---
schema_version: 1
id: nyxloom-P33-robust-review-verdict
project: nyxloom
title: "Robust review verdict — derive from REVIEW.md, fail-safe to REJECTED"
tier: sonnet5-high
input_revision: "45b0546"
depends_on: []
session: fresh
source: {kind: backlog, ref: nyxloom-trove/4-backlog.md}
scope:
  touch:
    - "src/nyxloom/daemon.py"
    - "tests/test_daemon.py"
  forbid:
    - "src/nyxloom/reconcile.py"
    - "src/nyxloom/config.py"
    - "src/nyxloom/wrapper.py"
oracles:
  - id: O1
    observable: "A FRONTIER_REVIEW attempt that exits with receipt.result==DONE but whose committed `<task>-REVIEW.md` (on the task's feat/ branch) carries a REJECTED verdict causes the task to transition to REVIEW_REJECTED, NOT MERGE_READY. A test reproduces exactly the P26 case (clean process exit + a REVIEW.md whose verdict line says REJECTED) and asserts the resulting TASK_TRANSITIONED target is REVIEW_REJECTED and REVIEW_RECORDED payload result is 'rejected'."
    negative: "the current behavior — the FRONTIER_REVIEW branch (daemon.py ~1540-1555) maps receipt.result DONE -> MERGE_READY regardless of the report verdict, so a reviewer that reasons REJECT but forgets the `BLOCKED: rejected` final line rubber-stamps the task to MERGE_READY (live P26 incident 2026-07-16)."
    gate: tester-unified
  - id: O2
    observable: "A review whose `<task>-REVIEW.md` verdict is APPROVED (and receipt done) still transitions the task to MERGE_READY — the approval path is preserved. A test asserts MERGE_READY for an APPROVED report."
    negative: "the fix is over-strict and rejects genuinely-approved work, stalling the pipeline"
    gate: tester-unified
  - id: O3
    observable: "FAIL-SAFE: a review that exits done but whose `<task>-REVIEW.md` is MISSING, unreadable, or contains no unambiguous APPROVED verdict transitions the task to REVIEW_REJECTED (never MERGE_READY). A test asserts a missing/ambiguous report yields REVIEW_REJECTED, not MERGE_READY."
    negative: "ambiguity or a missing report defaults to MERGE_READY (unsafe — the exact rubber-stamp this package removes)"
    gate: tester-unified
  - id: O4
    observable: "The review packet (daemon.py ~1601-1632) instructs the reviewer to write a MACHINE-READABLE verdict line into `<task>-REVIEW.md` — exactly `VERDICT: APPROVED` or `VERDICT: REJECTED — <reason>` — in addition to the existing `BLOCKED: rejected` final-line signal (kept as defense-in-depth). A test asserts the assembled packet text contains the `VERDICT:` instruction."
    negative: "the reviewer is still only asked for a prose verdict, so the durable artifact has no reliably-parseable signal"
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "reading the reviewer's REVIEW.md verdict cannot be done from daemon.py without a wrapper.py or config.py change (then BLOCKED — those are forbidden)"
  - "a review wave covers multiple tasks and per-task verdict attribution needs a reconcile.py change (then file a D-decision; do not silently expand scope)"
---

# P33 — Robust review verdict (derive from REVIEW.md, fail-safe to REJECTED)

**Critical correctness fix.** The merge gate is only as trustworthy as its
verdict signal — and right now a reviewer that reasons REJECT but forgets one
mechanical output line silently rubber-stamps its task to MERGE_READY. That
happened live (P26, 2026-07-16): a correct REJECTED review report, clean process
exit -> `done` -> MERGE_READY -> nearly merged buggy daemon-core code. Make the
verdict robust: derive it from the durable REVIEW.md artifact and FAIL SAFE to
REJECTED on any ambiguity, so a missed magic-line can never approve-by-accident.

## Worktree / branch

The daemon runs this on a dedicated `implement` branch in its own git worktree
under `.worktrees/` (branch `feat/nyxloom-P33-robust-review-verdict` from
`main`); commit all work on that branch. Do not touch the main checkout.

## Context to read first (read ONLY these, in order)

- `src/nyxloom/daemon.py`:
  - **~1540-1555** — the `if attempt.role == Role.FRONTIER_REVIEW:` branch of
    EmitAttemptExit. TODAY: `REVIEW_RECORDED {result}` then
    `result is DONE -> MERGE_READY else -> REVIEW_REJECTED`. This is the bug:
    `receipt.result` reflects only PROCESS exit (wrapper.py infers done on a
    clean exit), never the review's actual verdict. CHANGE it to parse the
    reviewer's committed `<task>-REVIEW.md` verdict and fail-safe to reject.
  - **~1601-1632** — the review packet assembly (`## Your role: INDEPENDENT
    FRONTIER REVIEWER`). Item 7 already asks for a `BLOCKED: rejected —` final
    line; ADD a required machine-readable `VERDICT: APPROVED|REJECTED — <reason>`
    line written INTO `<task>-REVIEW.md` (step 6 already makes the reviewer
    write that file). Keep the `BLOCKED:` line as a second signal.
  - **~882** — note `REVIEW_RECORDED ... result == "rejected"` is already
    consumed for carve stats, but the receipt can never produce "rejected"
    today; your REVIEW_RECORDED must emit result 'rejected' on a rejected
    verdict so that consumer finally works.
- `src/nyxloom/wrapper.py` (READ only — forbidden to edit) — confirm how
  receipt.result is set (clean exit -> done; `BLOCKED:` line -> blocked). This
  is WHY process-exit is not a verdict.
- `tests/test_daemon.py` — mirror the existing FRONTIER_REVIEW-exit test to
  build the O1/O2/O3 fixtures (fake a REVIEW.md with each verdict + a receipt).

## Work

1. Add a verdict parser: read the task's committed `<task>-REVIEW.md` (from the
   feat/ branch, e.g. `git show feat/<task>:<reports_dir>/<task>-REVIEW.md`, or
   the review worktree) and extract `VERDICT: APPROVED|REJECTED`. No/ambiguous/
   unreadable verdict -> treat as REJECTED (fail-safe).
2. In the FRONTIER_REVIEW EmitAttemptExit branch: map the PARSED verdict (not
   receipt.result) — APPROVED -> MERGE_READY; REJECTED/ambiguous -> REVIEW_REJECTED.
   Emit REVIEW_RECORDED with result 'rejected' on reject. Keep the existing
   `result != DONE -> REVIEW_REJECTED` (BLOCKED:/nonzero) path as defense-in-depth.
3. Add the `VERDICT:` requirement to the review packet prompt (item 6/7).
4. Tests in `tests/test_daemon.py` proving O1 (done+REJECTED report -> REVIEW_REJECTED),
   O2 (APPROVED -> MERGE_READY), O3 (missing/ambiguous -> REVIEW_REJECTED),
   O4 (packet contains the VERDICT instruction).

## Scope / forbid

Touch ONLY `daemon.py` + `tests/test_daemon.py`. Do NOT edit `wrapper.py`
(receipt semantics stay), `reconcile.py`, or `config.py`.

## BLOCKED rule

If the verdict cannot be parsed from daemon.py without a forbidden-file change,
STOP — write `BLOCKED: <reason>` to the LOG, commit, and exit. Do NOT improvise.

## Gate

`tester-unified`:
```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd {worktree}/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```
