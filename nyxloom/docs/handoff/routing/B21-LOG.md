# B21 — scope-amendment escalation (D-R16 §3) — implementation log

Worktree: `/workspaces/vbpub/.worktrees/nyxloom-B21-scope-amendment/nyxloom`
Branch: `feat/nyxloom-B21-scope-amendment` (based on main `7c7b6562`)

## Context read (in order, per handoff)

1. `docs/routing-model-redesign.md:374-379` (D-R16 decision 3) and
   `nyxloom-trove/4-backlog.md:146-155` (B21).
2. `src/nyxloom/adapters.py:683-719` (`classify_log_tail`), `:150-397`
   (`build_dispatch`, all three role branches + the argv_max guard +
   `prior_verdict` truncation logic it must coexist with).
3. `src/nyxloom/wrapper.py:1-70` (module docstring / INTERFACE CONTRACT),
   `:395-499` (the classification -> Receipt -> event-append sequence).
4. `src/nyxloom/daemon.py:1090-1140` (`_attempt_scan` receipt collection),
   `:3366-3600` (`EmitAttemptExit` executor: CARVER/FRONTIER_REVIEW/
   SELF_REVIEW early-return branches, then the DONE/BLOCKED/LIMIT/ERROR
   fan-out at the end -- confirmed this decision logic lives HERE, not in
   reconcile.py's pure planner), `:3132-3226` (`DispatchImplementer`
   executor -- where `build_dispatch` is called for a re-dispatch),
   `:3571-3900` (`LaunchReview` executor -- packet build + `build_dispatch`
   FRONTIER_REVIEW call).
5. `src/nyxloom/types.py:166-171` (`ReceiptResult`), `:188-231` (`EventType`),
   `:309-312` (`Scope` -- confirmed READ ONLY, never edited), `:401-410`
   (`Receipt`), `:477-` (`TaskStateFile` -- confirmed it has NO generic
   event-history field; custom event types are not auto-projected).
6. `src/nyxloom/storage.py:185-201` (`iter_events`), `:278-392`
   (`apply_event`'s projection switch -- confirmed SCOPE_AMENDMENT_* events
   get NO special-case projection, same shape as other pass-through event
   types e.g. `DECISION_OPENED`).
7. `src/nyxloom/reconcile.py:1-180` (module contract, items 1-11) and
   `:1648-1680` (`attempts_used`/`implementer_record_count`) -- confirmed
   the BLOCKED/LIMIT/ERROR receipt-routing decision is NOT in this module
   (it is 100% in daemon.py's `EmitAttemptExit`); reconcile.py is untouched
   by this package, a deliberate choice (see Decisions below).
8. `tests/test_behavioral.py:1-260` (fixtures, `FakeStep`/`FakeScript`,
   `_tick`/`_wait` driving helpers), `:400-463` (the two direct analogs:
   `test_fake_blocked_exit_sets_typed_contract_blocker` and
   `test_reject_loop_requeues_never_strands`).
9. `src/nyxloom/testing.py` (read-only, NOT in scope.touch) -- confirmed
   `FakeStep.extra_stdout` already exists as a fully generic "print this
   line" lever (no testing.py change needed for O1/O3).
10. `tests/test_adapters.py:1071-1145` (`classify_log_tail` cluster),
    `tests/test_reconcile.py` (confirmed this file is 100% `plan_project`
    unit tests, never touches `daemon.Daemon` -- so daemon-level wiring
    tests belong in test_behavioral.py, not here).
11. `tests/test_wrapper.py:314-410` (`TestBlocked`/`TestLimit` classes --
    the `mock_adapters` fixture + `fake_cli` script-generator pattern).
12. `tests/test_types.py` (full file, 113 lines) -- confirmed it is
    entirely the `RESERVED_ROLES`/Role-dispatch guard, not a serde
    round-trip suite; O7 tests are new additions modeled on its general
    "additive, non-hollow" test style, not literal copies of its content.

## Decisions made (resolving forks the recon didn't pin exactly)

- **Where the decision logic lives: daemon.py, not reconcile.py.** The
  handoff's D-B21-2 offered "reconcile.py (or daemon.py where the decision
  lives)" for `MAX_SCOPE_AMENDMENTS_PER_TASK`. Confirmed by reading
  `EmitAttemptExit`'s BLOCKED/LIMIT/ERROR branches (daemon.py:3574-3600):
  ALL of that receipt-routing decision already lives directly in
  daemon.py's executor, not reconcile.py's pure `plan_project`. The
  scope-amendment decision (count prior approvals, approve-and-requeue vs.
  fall-through-to-BLOCK) is the same *kind* of decision (receipt-driven,
  executed once per `EmitAttemptExit`), so it goes in the SAME place,
  beside the BLOCKED branch, per the handoff's own instruction. Put the
  constant there too. **reconcile.py is therefore untouched by this
  package** -- a deliberate choice, not an oversight, and does not
  contradict scope.touch (which listed it as an allowed-but-not-required
  file).
- **Counting mechanism: direct event-log scan, not a TaskStateFile field.**
  `storage.apply_event`'s projection switch (`storage.py:278-392`) does
  NOT special-case `SCOPE_AMENDMENT_APPROVED` (confirmed by reading it) --
  same shape as `DECISION_OPENED`/`NOTIFICATION_REQUESTED`/etc., which also
  get no projection. Two options existed: (a) add a new `TaskStateFile`
  field mirroring `progress_units` and teach `apply_event` to append to it,
  or (b) scan `storage.iter_events(project)` directly, mirroring
  `Daemon._ratchet_already_open`'s own identical pattern. Chose (b): it
  needed zero `TaskStateFile`/`apply_event` changes (a smaller, more
  additive diff) and the daemon already has this exact pattern to mirror.
  Unlike `_ratchet_already_open`'s 500-event recent window, my
  `_scope_amendments_approved` does NOT window -- the cap is a per-task
  LIFETIME bound (the whole point of D-B21-2), so an old approval scrolling
  out of a recent-N window must never silently re-open the cap.
- **Test-file mapping.** `tests/test_daemon.py` (the file with the
  `EmitAttemptExit`-per-`ReceiptResult` unit-test cluster, e.g. around its
  own lines 966-1152) is genuinely useful precedent but is **NOT in
  scope.touch**. Confirmed via the handoff's own oracle anchors that O1/O3
  are meant to be tested via the FULL daemon loop in `test_behavioral.py`
  (both cited anchors -- `test_fake_blocked_exit_sets_typed_contract_blocker`
  and `test_reject_loop_requeues_never_strands` -- live there, use
  `daemon.Daemon` + `fake_cli`, never `test_daemon.py`'s `_scripted()`
  synthetic-action shortcut). No forbidden-file trap triggered: the wide
  scope.touch worked as intended.
- **O2/O6 tested at the adapter unit level, not through the fake CLI's
  argv.** The "fake" CLI ignores its own prompt argv element entirely (it
  never captures/echoes it), so the ACTUAL text `build_dispatch` produced
  during a real daemon-driven re-dispatch isn't observable end-to-end
  without extra plumbing outside scope.touch (`testing.py`). Since the
  oracle text says "assert the injected prompt text", a direct
  `adapters.build_dispatch(..., approved_amendments=[...])` unit test in
  `tests/test_adapters.py` is the correct, non-hollow home for O2/O6 --
  it exercises the EXACT same code path `daemon.py`'s two call sites use,
  just without the subprocess indirection. The daemon-level WIRING (that
  `DispatchImplementer`/`LaunchReview` actually compute and pass
  `approved_amendments` from real events) is exercised for free by every
  existing + new `test_behavioral.py` test (the `[]`-default branch by
  every pre-existing test; the non-empty branch by the new O1/O3 tests,
  which both drive a real re-dispatch after an approval).
- **`fake_cli` fixture (test_wrapper.py) vs. raw JSON with embedded double
  quotes:** discovered empirically (see LOG entry below) that the fixture's
  naive `echo "{line}"` wrapping MANGLES a line containing its own `"`
  characters (shell word-concatenation silently drops the quote chars --
  verified with a standalone repro). Worked around by writing the
  wrapper-test scripts directly with a quoted heredoc (`cat <<'EOF' ...
  EOF`), which prints the marker line byte-for-byte with zero shell
  interpretation. `test_behavioral.py`'s `FakeStep(extra_stdout=...)` has
  NO such problem -- that value round-trips through JSON
  serialize/deserialize and a native Python `print()`, never through a
  shell command line.
- **Marker precedence in `classify_log_tail`:** placed the
  `SCOPE_AMENDMENT_REQUEST:` check immediately AFTER the `BLOCKED:` check
  and BEFORE the limit-phrase check, mirroring the existing "blocked wins"
  precedence note in the docstring. In practice an agent emits at most one
  marker per attempt, so this ordering is mostly a documentation/precedence
  concern (pinned by a NEGATIVE test either way).
- **Cap-exceeded blocker detail wording:** `"scope-amendment cap (N)
  reached; further request: <file> -- <reason>"[:200]`, truncated to 200
  chars like the existing `BLOCKED:`-path blocker (`(receipt.blocked_reason
  or "")[:200]`) -- kept the same defensive truncation convention.
- **`amendment_request.get("file") or "(unspecified)"`** in the daemon's
  `EmitAttemptExit` branch: a malformed/absent JSON payload (wrapper.py
  degrades to `amendment_request=None` on a JSON parse failure) must not
  crash the daemon or silently record an empty string as the "approved"
  file -- degrades to a labeled placeholder instead.

## Files touched

- `src/nyxloom/types.py` -- `ReceiptResult.SCOPE_AMENDMENT` (new member),
  `EventType.SCOPE_AMENDMENT_REQUESTED`/`SCOPE_AMENDMENT_APPROVED` (new
  members), `Receipt.amendment_request: dict | None = None` (new field).
  All additive; no existing member/field/table changed (D-B21-3
  verified by `test_receipt_result_pre_existing_members_unchanged`).
- `src/nyxloom/adapters.py` -- `classify_log_tail` recognizes
  `SCOPE_AMENDMENT_REQUEST:` (new `"scope_amendment"` classification, mirrors
  the `BLOCKED:` line-start match); `build_dispatch` gains
  `approved_amendments: list[str] | None = None`; IMPLEMENTER branch gets
  (a) a standing escape-hatch instruction on EVERY dispatch and (b) a
  conditional "Amendment-approved -- you MAY also edit: ..." block when
  `approved_amendments` is non-empty; FRONTIER_REVIEW branch gets a
  conditional "Scope-amendment note: ... legitimately in-scope" block.
- `src/nyxloom/wrapper.py` -- new `"scope_amendment"` classification branch:
  regex-extracts the JSON payload from the marker line (degrades to `None`
  on a parse failure, never crashes), sets
  `result=ReceiptResult.SCOPE_AMENDMENT`, carries `amendment_request` onto
  the written `Receipt`; folded into the existing WARNING-level logging
  bucket alongside LIMIT/BLOCKED (a soft, handled outcome, not an ERROR).
- `src/nyxloom/daemon.py` -- module constant
  `MAX_SCOPE_AMENDMENTS_PER_TASK = 2`; two new `Daemon` helper methods
  (`_scope_amendments_approved`, `_scope_amendment_files`); a new
  `elif result is ReceiptResult.SCOPE_AMENDMENT:` branch in
  `EmitAttemptExit`, beside the existing BLOCKED branch; both
  `build_dispatch` call sites (`DispatchImplementer`, the cold-dispatch leg
  of `LaunchReview`) now compute and pass `approved_amendments`.
- `reconcile.py` -- untouched (see Decisions above).
- Tests: `tests/test_types.py` (+4, O7), `tests/test_adapters.py` (+8:
  2 for `classify_log_tail` O4, 4 for `build_dispatch` O1/O2/O6, 2 more
  argv_max-bounding regression guards added after the live bug above),
  `tests/test_wrapper.py` (+2, O5, plus a `TestScopeAmendment` class doc
  note on the heredoc workaround), `tests/test_behavioral.py` (+4: O1, O3,
  the O6 daemon-level wiring test, plus a defensive-branch coverage test
  for the `try/except` in the new daemon helpers).

## Live bug found + fixed while writing the O6 behavioral test (important)

While writing `test_scope_amendment_files_reach_frontier_review_dispatch`
(driving a real task all the way to a review dispatch with an approved
amendment on record), the test HUNG for the full pytest timeout with the
reconcile pass repeating `actions=0 events=0` forever after a single
`review-launch` log line. Root-caused with targeted temporary debug prints
(added, verified, then fully removed -- confirmed via `git diff` showing
`daemon.py`'s `run_pass` byte-identical to pre-debug):

- **The bug:** my FIRST cut appended the "Scope-amendment note" to the
  FRONTIER_REVIEW prompt UNCONDITIONALLY (inside the `elif role is
  Role.FRONTIER_REVIEW:` prompt-construction, before `argv_max` was even
  computed) -- unlike `prior_verdict`'s embed, which is bounded/truncated to
  fit. For this test's real (if short) pytest tmp-dir packet paths, the base
  FRONTIER_REVIEW prompt was already ~959 chars; my unconditional +228-char
  note pushed it to 1187 -- still under the DEFAULT 1500 argv_max in THIS
  specific case, so unit tests with even-shorter fixture paths never caught
  it, but a **quick empirical check with the reviewer's OWN long-path
  regression fixture (`test_frontier_review_prompt_stays_under_argv_max_with_
  real_paths`'s `nyxloom-P74-...` path) showed the base reviewer prompt is
  already within ~200 chars of argv_max BY DESIGN** -- there was never
  enough headroom for an unconditional +228-char addition in the general
  case. The exact failure that hung my test: `AdapterError('rendered prompt
  exceeds argv_max (1558 > 1500)')`, raised inside `build_dispatch`, called
  from `LaunchReview`'s executor AFTER it had ALREADY appended the review's
  `ATTEMPT_CREATED` event (so the Attempt record exists, state=CREATED) but
  BEFORE `ATTEMPT_PREFLIGHTED`/`wrapper.launch_detached` ever ran. The
  daemon's `plan_project` then sees "this wave already has an attempt" and
  never re-plans a fresh `LaunchReview` -- the task is PERMANENTLY stranded
  at AWAITING_REVIEW with a dead CREATED-state attempt, no exception visibly
  surfaced anywhere in structlog output (the per-action `TICK_ERROR` IS
  written to the event log via `storage.append_and_apply`, just not `log.*`'d
  and -- a separate, PRE-EXISTING, out-of-scope gap noted below -- not
  counted into `run_pass`'s own `appended` list, so the pass-summary DEBUG
  line under-reports it as `events=0`).
- **The fix:** moved BOTH the FRONTIER_REVIEW "Scope-amendment note" and the
  IMPLEMENTER "Amendment-approved" note to AFTER `argv_max` is computed, each
  now gated by `if len(prompt) + len(note) <= argv_max: prompt += note` --
  skip entirely (never truncate mid-list, never raise) when insufficient
  room remains, mirroring `prior_verdict`'s established "degrade, never
  strand the dispatch" philosophy. Added two new regression-guard unit tests
  (`test_implementer_amendment_note_skipped_when_argv_max_too_tight`,
  `test_frontier_review_amendment_note_skipped_when_argv_max_too_tight`) that
  construct a route whose `argv_max` sits strictly between the base prompt's
  length and (base + note)'s length, asserting the note is skipped, the
  prompt is byte-identical to the no-amendments dispatch, and
  `build_dispatch` never raises. This is a REAL, above-the-bar correctness
  fix, not merely a test-writing workaround -- an unconditional version of
  either note would have been a live production risk on any project with
  moderately long worktree/handoff paths.
- **Separately observed, PRE-EXISTING, OUT OF SCOPE for this package** (not
  touched, `run_pass`/`_execute` reverted byte-identical to pre-debug):
  1. `daemon.py`'s `_attempt_scan` (module contract item 4) rescans EVERY
     historical EXITED IMPLEMENTER attempt on a task whenever the task is
     CURRENTLY ACTIVE, not just the latest -- a task with >= 2 sequential
     implementer attempts can get a STALE already-consumed attempt's receipt
     re-processed alongside the current one in the same pass (observed
     causing a `TransitionError` TICK_ERROR: `'task transition QUEUED ->
     AWAITING_REVIEW not allowed'`). This is why
     `test_scope_amendment_files_reach_frontier_review_dispatch` seeds the
     `SCOPE_AMENDMENT_APPROVED` event directly instead of scripting a real
     two-attempt scope-amendment cycle (see that test's own docstring). O1/O3
     above stay safe because their tick loops break as soon as the SECOND
     `ATTEMPT_CREATED` lands (same pass as the first attempt's healing), well
     before a LATER pass could re-scan the stale one.
  2. `run_pass`'s per-action exception handler (`daemon.py` ~line 775-789)
     writes a `TICK_ERROR` event via `storage.append_and_apply` on any
     executor exception, but never adds the resulting `Event` to its own
     `appended` list -- the event IS durably persisted (confirmed via
     `storage.iter_events`), but the `pass-summary` DEBUG log's `events=`
     count under-reports it, which is what made this bug harder to spot from
     log output alone (had to inspect the event log directly, not the
     console/log output, to find the `AdapterError`/`TICK_ERROR`).
  Neither of these is touched by this package's diff; both are flagged here
  for a future, separately-scoped fix.

## A second finding: a hollow negative test caught by a real coverage run

Ran `pytest --cov=nyxloom.adapters --cov=nyxloom.wrapper --cov=nyxloom.types
--cov=nyxloom.daemon --cov=nyxloom.reconcile --cov-report=term-missing` over
all 5 touched test files (a cockpit approximation of the gate's 100%
diff-coverage floor, not the gate itself) to self-check every line this
package adds. Two real gaps surfaced and were fixed:
- `daemon.py:3849-3853` (LaunchReview's amendment-aggregation loop body) was
  UNCOVERED until the O6 daemon-level wiring test was added (see the O6 row
  in REPORT.md's oracle table) -- every OTHER test's wave had an empty
  amendment list, so the loop body (`if f not in approved_amendments:
  append`) never actually ran.
- `wrapper.py:432-433` (the `except json.JSONDecodeError:` branch) was
  UNCOVERED even though `test_scope_amendment_malformed_json_degrades_to_
  none` existed and passed -- the test's ORIGINAL payload
  (`SCOPE_AMENDMENT_REQUEST: not valid json at all`) has no `{...}` braces
  at all, so it never matches the wrapper's own extraction regex
  (`^SCOPE_AMENDMENT_REQUEST:\s*(\{.*\})\s*$`) and `amendment_match` is
  `None` -- the test's assertion (`amendment_request is None`) still passed,
  but via the "no marker match" default, not via "matched, then json.loads
  failed." A HOLLOW negative, caught only by the coverage run, not by the
  test passing. Fixed by changing the payload to `{not: valid json}` (has
  braces, so the regex matches and `json.loads` is actually reached and
  actually raises on the unquoted key). Re-verified: `wrapper.py`'s coverage
  report no longer lists 432-433 as missing.

## Verification run in this cockpit (NOT the gate)

- `python3 -m py_compile` on all 5 touched `src/` modules and all 5
  touched test files -- clean.
- `PYTHONPATH=src python3 -m pytest` (this cockpit's own venv, pytest
  8.4.2 present) run per-file, final state (after both fixes above):
  - `tests/test_types.py` -- pass
  - `tests/test_adapters.py` -- pass (86 tests total, 8 new)
  - `tests/test_wrapper.py` -- pass (19 tests, was 17 pre-change)
  - `tests/test_reconcile.py` -- pass (untouched file, confirms no
    collateral breakage)
  - `tests/test_behavioral.py` -- pass, full suite, multiple times across
    the debugging process and once more clean at the end
  - `pytest --cov=nyxloom.adapters --cov=nyxloom.wrapper --cov=nyxloom.
    types --cov=nyxloom.daemon --cov=nyxloom.reconcile --cov-report=
    term-missing` over the 5 touched test files -- every line this package
    ADDS confirmed covered by cross-referencing this package's own new line
    numbers against the reported `Missing` ranges (not a repo-wide 100%
    claim -- `daemon.py` overall sits at ~33% because these 5 test files
    don't exercise the whole file, only the coverage-tool's own line-level
    `Missing` list was checked against this package's specific additions).
- This is a cockpit smoke check only -- see REPORT.md for the honest
  caveat about the authoritative `test-runner`/100%-diff-coverage gate,
  which I did not and must not run myself.
