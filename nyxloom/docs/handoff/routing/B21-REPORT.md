# B21 — scope-amendment escalation (D-R16 §3) — implementation report

Worktree: `/workspaces/vbpub/.worktrees/nyxloom-B21-scope-amendment/nyxloom`
Branch: `feat/nyxloom-B21-scope-amendment` (based on main `7c7b6562`)

## What was built

The mid-flight scope-amendment escalation flow end-to-end: an IMPLEMENTER
that genuinely needs a file outside its `scope.touch` allowlist emits a
`SCOPE_AMENDMENT_REQUEST: {"file": ..., "reason": ...}` marker instead of
faking a workaround or hard-BLOCKing; the daemon recognizes it, approves it
(bounded, `MAX_SCOPE_AMENDMENTS_PER_TASK = 2` per task) and re-dispatches the
implementer with the widened allowlist named in its prompt; once the task
reaches review, the reviewer's prompt is told about the approved file(s) too,
so it does not reject the now-legitimate out-of-scope edit. At the cap, a
further request falls through to the existing hard-BLOCK path (typed CONTRACT
blocker) — the escalation is bounded, never an infinite loop.

## Per-seam summary

- **`src/nyxloom/types.py`** — additive only (D-B21-3): `ReceiptResult.
  SCOPE_AMENDMENT`, `EventType.SCOPE_AMENDMENT_REQUESTED` /
  `SCOPE_AMENDMENT_APPROVED`, `Receipt.amendment_request: dict | None = None`.
  No existing member/field/table changed — `TASK_TRANSITIONS`, `Scope`,
  `config.py`, `frontmatter.py`, `lint.py` untouched, exactly per the
  handoff's frozen-core boundary.
- **`src/nyxloom/adapters.py`** — `classify_log_tail` recognizes
  `SCOPE_AMENDMENT_REQUEST:` (line-start match, mirrors `BLOCKED:`, checked
  right after it so `BLOCKED:` keeps precedence — no regression). `build_
  dispatch` gains `approved_amendments: list[str] | None = None`: every
  IMPLEMENTER dispatch carries a standing escape-hatch instruction (measured
  safe for realistic long paths — a live long-path check landed at 872/1500
  chars); a re-dispatch with approved amendments embeds "Amendment-approved —
  you MAY also edit: ..."; a FRONTIER_REVIEW dispatch with approved
  amendments embeds "Scope-amendment note: ... legitimately in-scope". BOTH
  amendment notes are bounded to the remaining `argv_max` budget (skip
  entirely, never truncate, never raise, if insufficient room) — see the
  live-bug section below for why this bounding is load-bearing, not
  decorative.
- **`src/nyxloom/wrapper.py`** — a new `"scope_amendment"` classification
  branch: regex-extracts the JSON payload from the marker line (`json.loads`,
  degrading to `amendment_request=None` on a parse failure, never crashing
  the wrapper), sets `result=ReceiptResult.SCOPE_AMENDMENT`, carries
  `amendment_request` onto the written `Receipt`; logged at WARNING
  (soft/handled outcome, same bucket as LIMIT/BLOCKED, not an ERROR).
- **`src/nyxloom/daemon.py`** — module constant `MAX_SCOPE_AMENDMENTS_PER_TASK
  = 2` (not `config.Policy` — frozen for this package, same reasoning as
  `HISTORY_REJECTION_WINDOW_SECONDS`). Two new `Daemon` helpers:
  `_scope_amendments_approved` (direct `storage.iter_events` scan, NOT
  windowed — the cap is a per-task LIFETIME bound) and `_scope_amendment_
  files` (dedup'd granted file list). A new `elif result is ReceiptResult.
  SCOPE_AMENDMENT:` branch in `EmitAttemptExit`, beside the existing BLOCKED
  branch: under the cap, records `SCOPE_AMENDMENT_REQUESTED` +
  `SCOPE_AMENDMENT_APPROVED` events and re-dispatches via the EXISTING legal
  `ACTIVE->QUEUED` edge (no new `TaskState`, no `TASK_TRANSITIONS` edit —
  D-B21-3); at the cap, falls through to the same typed-CONTRACT-blocker
  `TASK_BLOCKED` path the CONTRACT-blocked receipt uses. Both `build_
  dispatch` call sites (`DispatchImplementer`, `LaunchReview`'s cold-dispatch
  leg) now compute and pass `approved_amendments` from these same events.
  `reconcile.py` is **untouched** — the decision logic (counting, bounding,
  routing) lives 100% in `daemon.py`'s `EmitAttemptExit` executor, matching
  where the analogous BLOCKED/LIMIT/ERROR decisions already live (verified
  by reading that method before writing any code) — the handoff's D-B21-2
  explicitly offered `daemon.py` as the alternative location.

## D-B21-1/2/3 as implemented

- **D-B21-1 (overlay, not a handoff rewrite):** confirmed — the handoff `.md`
  file on disk is never touched. The widened allowlist reaches the
  implementer/reviewer ENTIRELY via events (`SCOPE_AMENDMENT_APPROVED`) read
  back and injected into the re-dispatch/review PROMPT TEXT. `tests/test_
  adapters.py::test_implementer_re_dispatch_widens_prompt_with_approved_
  amendments` and `::test_frontier_review_prompt_tells_reviewer_about_
  approved_amendments` assert this injection directly; `tests/test_
  behavioral.py::test_scope_amendment_files_reach_frontier_review_dispatch`
  proves the daemon-level wiring (not just the adapter call) computes and
  threads the same events through a REAL review dispatch.
- **D-B21-2 (module-constant cap, not `config.Policy`):**
  `MAX_SCOPE_AMENDMENTS_PER_TASK = 2` in `daemon.py`, enforced by counting
  prior `SCOPE_AMENDMENT_APPROVED` events (`Daemon._scope_amendments_
  approved`) inside the `EmitAttemptExit` executor's `elif result is
  ReceiptResult.SCOPE_AMENDMENT:` branch. `tests/test_behavioral.py::test_
  scope_amendment_bounded_falls_through_to_blocked_at_cap` (O3) proves the
  cap actually halts approval and produces a typed CONTRACT `TASK_BLOCKED`
  after exactly `MAX_SCOPE_AMENDMENTS_PER_TASK` approvals.
- **D-B21-3 (additive-only types, existing legal edge, no new role
  dispatch):** confirmed by `tests/test_types.py`'s new O7 cluster (pre-
  existing `ReceiptResult` members' `.value`s pinned unchanged; the new
  members/field round-trip through the EXISTING generic `_Serde`/`Event`
  serde with zero new converter code) and by re-using the ALREADY-legal
  `ACTIVE->QUEUED` edge (the same one the pre-existing LIMIT branch uses) —
  no `TASK_TRANSITIONS` edit, no new `Role`/`TaskState` member anywhere in
  the diff.

## Oracle -> test mapping

| Oracle | Test(s) |
|---|---|
| O1 (no dead-end, >=2 implementer attempts, never BLOCKED) | `tests/test_behavioral.py::test_scope_amendment_request_triggers_bounded_re_dispatch_not_blocked` |
| O2 (allowlist widens in the PROMPT) | `tests/test_adapters.py::test_implementer_re_dispatch_widens_prompt_with_approved_amendments` (+ NEGATIVE `test_approved_amendments_only_embedded_for_implementer`) |
| O3 (bounded, cap -> TASK_BLOCKED) | `tests/test_behavioral.py::test_scope_amendment_bounded_falls_through_to_blocked_at_cap` |
| O4 (marker recognition, no BLOCKED regression) | `tests/test_adapters.py::test_classify_log_tail_scope_amendment`, `::test_classify_log_tail_blocked_still_wins_no_regression` |
| O5 (wrapper -> receipt translation) | `tests/test_wrapper.py::TestScopeAmendment::test_scope_amendment_classification` (+ malformed-JSON negative `test_scope_amendment_malformed_json_degrades_to_none`) |
| O6 (reviewer sees the amendment) | `tests/test_adapters.py::test_frontier_review_prompt_tells_reviewer_about_approved_amendments` (unit) + `tests/test_behavioral.py::test_scope_amendment_files_reach_frontier_review_dispatch` (daemon-level wiring, real review dispatch) |
| O7 (types additive & serializing) | `tests/test_types.py::test_receipt_result_pre_existing_members_unchanged`, `::test_receipt_result_scope_amendment_member_added`, `::test_receipt_amendment_request_field_round_trips`, `::test_event_type_scope_amendment_members_added_and_round_trip` |

Plus: `tests/test_adapters.py::test_implementer_prompt_always_carries_scope_
amendment_escape_hatch` (O1's prompt-side precondition — the agent must be
TOLD the escape hatch exists), and two argv_max-bounding regression guards
(`test_implementer_amendment_note_skipped_when_argv_max_too_tight`, `test_
frontier_review_amendment_note_skipped_when_argv_max_too_tight`) added after
the live bug below; plus a defensive-branch test (`tests/test_behavioral.py
::test_scope_amendment_helpers_degrade_gracefully_on_storage_error`) for the
new `Daemon` helpers' `try/except` around `storage.iter_events`.

## A live bug found and fixed during implementation (read this)

While writing the O6 daemon-level behavioral test, an EARLIER, unconditional
version of the FRONTIER_REVIEW "Scope-amendment note" (appended inline,
before `argv_max` was computed, with no room check) caused `build_dispatch`
to raise `AdapterError('rendered prompt exceeds argv_max (1558 > 1500)')` for
the test's real (if short) packet paths — the base FRONTIER_REVIEW prompt is
already close to `argv_max` by design (confirmed against the reviewer's OWN
long-path regression fixture). Because `LaunchReview`'s executor had ALREADY
appended the review's `ATTEMPT_CREATED` event before the exception, and the
daemon never re-plans a `LaunchReview` for a wave that already has an
attempt record, the task was PERMANENTLY STRANDED at AWAITING_REVIEW with a
dead CREATED-state attempt — no exception visible in structlog output (only
in the raw event log, since a separate PRE-EXISTING gap in `run_pass`'s
per-action exception handler never counts the `TICK_ERROR` it writes into
its own `appended`/`events=` reporting).

**Fixed** by moving both the FRONTIER_REVIEW and IMPLEMENTER amendment notes
to after `argv_max` is computed and bounding each to `if len(prompt) +
len(note) <= argv_max: prompt += note` (skip, never truncate, never raise) —
the same "degrade, never strand" philosophy `prior_verdict`'s embed already
uses. Two new regression-guard unit tests pin this. This was a genuine,
above-the-bar correctness fix (not just a test-writing workaround): an
unconditional note would have been a live production risk on any project
with moderately long worktree/handoff paths, independent of this specific
test.

**Two separate, pre-existing, out-of-scope daemon behaviors were observed**
(NOT touched — `daemon.py`'s `run_pass`/`_attempt_scan` are byte-identical to
pre-debug, verified via `git diff`), documented in LOG.md for a future,
separately-scoped fix:
1. `_attempt_scan` rescans EVERY historical EXITED IMPLEMENTER attempt on a
   task whenever it is currently ACTIVE (not just the latest), which can
   re-process a stale already-consumed attempt's receipt alongside a fresh
   one in the same pass. Worked around in the O6 test by seeding the
   `SCOPE_AMENDMENT_APPROVED` event directly instead of scripting a full
   two-attempt request/approve cycle (see that test's docstring for the
   detailed reasoning); O1/O3 are unaffected (their tick loops stop before
   this could manifest).
2. `run_pass`'s per-action exception handler writes its `TICK_ERROR` event
   via `storage.append_and_apply` but never adds it to its own `appended`
   list, so the `pass-summary` DEBUG log under-reports `events=` on an
   executor exception (the event is still durably persisted; only the
   in-pass reporting undercounts it).

## Verification (cockpit only — NOT the gate)

- `python3 -m py_compile` on all 4 touched `src/` modules + all 4 touched
  test files: clean.
- `PYTHONPATH=src python3 -m pytest` in this cockpit's own venv (pytest
  8.4.2), run repeatedly through the debugging process and finally as a
  clean full pass, per file:
  - `tests/test_types.py` — pass (10 tests, 4 new O7)
  - `tests/test_adapters.py` — pass (86 tests, 8 new: O1/O2/O4/O6 + 2
    argv_max-bounding regression guards)
  - `tests/test_wrapper.py` — pass (19 tests, 2 new O5)
  - `tests/test_reconcile.py` — pass, UNCHANGED file (confirms zero
    collateral breakage from the daemon.py/adapters.py/types.py/wrapper.py
    edits)
  - `tests/test_behavioral.py` — pass, full suite (O1, O3, the O6
    daemon-wiring test, and the defensive-branch test, alongside every
    pre-existing behavioral test)
- A coverage run (`pytest --cov=nyxloom.adapters --cov=nyxloom.wrapper
  --cov=nyxloom.types --cov=nyxloom.daemon --cov=nyxloom.reconcile
  --cov-report=term-missing`) over the 5 touched test files was used to
  confirm every NEW line this package adds is exercised, including both
  branches of the daemon's cap check (approve-and-requeue vs.
  fall-through-to-BLOCK) and the LaunchReview aggregation loop's non-empty
  branch (initially missed — fixed by adding the O6 daemon-level test
  above, which is what surfaced the live argv_max bug in the first place).
  The SAME coverage run also caught a second, smaller defect: `tests/test_
  wrapper.py::test_scope_amendment_malformed_json_degrades_to_none`'s
  ORIGINAL payload (`SCOPE_AMENDMENT_REQUEST: not valid json at all`) has no
  `{...}` braces, so it never reaches the wrapper's `json.loads` call at all
  (it hits the "no marker match" default instead) -- a HOLLOW negative that
  passed for the wrong reason, only caught because `wrapper.py:432-433`
  (the `except json.JSONDecodeError:` line) showed up in the coverage
  report's `Missing` column despite the test passing. Fixed by changing the
  payload to `{not: valid json}` (braces present, so the regex matches and
  `json.loads` genuinely raises on the unquoted key) -- re-verified via a
  follow-up coverage run showing 432-433 no longer missing.
- This is a cockpit smoke check ONLY. I did NOT run the authoritative
  Docker `tester-unified` gate (100% diff-coverage floor) — per the
  handoff's gate-discipline instructions, I must not, and did not, run it
  myself. The controller must re-validate via `./scripts/testing-exec.sh`
  from `main` post-merge before this is trusted, per this project's
  "Cockpit vs. gating test runner" policy.

## Honesty notes

- I have NOT run the real gate. Everything above is cockpit-venv pytest
  output only.
- `reconcile.py` is listed in `scope.touch` but is genuinely UNTOUCHED —
  a deliberate choice (see LOG.md's "Decisions made" section), not an
  oversight or a missed requirement.
- The two pre-existing daemon behaviors documented above are NOT fixed by
  this package (out of scope) — flagged for a future package, not silently
  left for a reviewer to discover unaided.
- Commit hash: recorded after the commit below; see the git log for
  `feat/nyxloom-B21-scope-amendment`.
