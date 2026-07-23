# B24 — transient-failure backoff-resume (D-R17) — implementation report

Worktree: `/workspaces/vbpub/.worktrees/nyxloom-B24-backoff-resume/nyxloom`
Branch: `feat/nyxloom-B24-backoff-resume` (based on main `1e13e8ac`)

## What was built

When an IMPLEMENTER attempt fails because the PROVIDER was throttled
(502/429/`ResourceExhausted`/idle-timeout/`Worker local total request
limit`), classified by output (not exit code, since free routes exit 0 even
on failure) as `"transient"`, it is now RESUMED with an exponential backoff
(60s / 300s / 900s) rather than restarted fresh or blocked. After
`MAX_TRANSIENT_RESUMES` (3) failed resumes of the SAME attempt, the daemon
gives up on it, pauses the throttled route, and requeues the task so the
EXISTING first-healthy-route selector picks the next route in the tier on
its own.

## Per-seam summary and how each D-B24 decision was implemented

- **`src/nyxloom/types.py`** — additive only: `ReceiptResult.TRANSIENT`.
  No existing member/table changed.
- **`src/nyxloom/adapters.py`** (D-B24-3's classification half) —
  `TRANSIENT_PHRASE` regex (502/`ResourceExhausted`/idle-timeout/worker
  request-limit/a distinct HTTP-429 shape), checked in `classify_log_tail`
  in the SAME terminal tail window as `limit_phrase` (`tail_lim`), and
  BEFORE it — so "Worker local total request limit reached" (which would
  ALSO match `limit_phrase`'s own bare "limit reached" alternative) is
  reclassified transient, not LIMIT (the ordering the handoff's O1
  specifically names). The docstring's phantom "error prefix /
  'error'/'exceeded'/HTTP 429" heuristic (no `ERROR_PREFIXES` constant ever
  existed) was corrected to describe the actual code, not implemented.
  **No regression**: the transient HTTP-429 signature was deliberately
  scoped narrower than "too many requests" wording so the pre-existing
  `"429 too many requests"` -> `"limit"` classification
  (`test_classify_log_tail_limit_variants` and a second inline case)
  remains unchanged and green, untouched.
- **`src/nyxloom/wrapper.py`** (D-B24-1, the critical seam) — a new
  `classification == "transient"` branch sets `result =
  ReceiptResult.TRANSIENT`, but — unlike every other non-DONE
  classification — `event_type = EventType.ATTEMPT_INTERRUPTED`,
  `attempt_state = AttemptState.INTERRUPTED`: the EXACT pair the
  pre-existing `if interrupted:` (real-signal) branch above it uses. Never
  `ATTEMPT_EXITED`/`EXITED`. Confirmed via `types.py`'s own tables that an
  EXITED attempt could never be revived (`TERMINAL_ATTEMPT_STATES`,
  `ATTEMPT_TRANSITIONS[EXITED] == frozenset()`), and that
  `storage._attempt_regression` explicitly whitelists `INTERRUPTED ->
  RUNNING` as not-a-regression — so this is what keeps the EXISTING
  `ResumeAttempt` path (unmodified) able to retry it.
- **`src/nyxloom/daemon.py`** — `TRANSIENT_BACKOFF_SCHEDULE = (60, 300,
  900)` and `MAX_TRANSIENT_RESUMES = 3` (D-B24-4, module constants beside
  `MAX_SCOPE_AMENDMENTS_PER_TASK`, never `config.Policy` — frozen for this
  package). Two new methods:
  - `_transient_backoff_ready(project, states)` (D-B24-2) — beside
    `_resume_failures`; per-attempt readiness derived from disk
    (`_next_resume_n(attempt_dir) - 1` for the prior-resume count, the
    newest `attempt.resume-N.log`'s mtime for the wait-start instant). No
    persisted field, no schema change. Once `prior_resumes >=
    MAX_TRANSIENT_RESUMES` it returns `False` FOREVER for that attempt_id.
  - `_transient_escalate(project, cfg, states)` (D-B24-5) — the at-cap
    escalation: transitions the task `ACTIVE -> QUEUED` (the SAME legal
    edge the LIMIT branch already uses) and calls `self._provider_pause(...,
    state="throttled")`. Run in `run_pass` BEFORE `_build_input`/
    `plan_project` (mirroring `_reconcile_decisions`'s exact early-mutation
    timing), so a fresh `DispatchImplementer` via the EXISTING queued-task
    dispatch loop (`reconcile.py:966-975`, confirmed to be the loop the
    handoff's "existing selector" citation actually refers to — NOT the
    separate `poisoned`/`fresh_start_eligible` loop further down) can fire
    in the very same pass the route gets paused.
  - `_provider_pause` gained an optional `state: str = "limited"` param
    (D-B24-3) — every pre-B24 call site is byte-identical (default
    unchanged); `_transient_escalate` passes `state="throttled"` so the
    escalation's `PROVIDER_STATE_CHANGED` payload reads distinctly from a
    genuine LIMIT pause, with no new `EventType`.
  - `_build_input` computes and passes `transient_backoff_ready` into
    `ReconcileInput`; `run_pass` calls `_transient_escalate` right after
    `_reconcile_decisions`.
- **`src/nyxloom/reconcile.py`** — `ReconcileInput.transient_backoff_ready:
  dict[str, bool] = field(default_factory=dict)` (D-B24-2, beside
  `resume_failures`, defaulted so no existing test needed updating). The
  handoff's instruction was "ONE extra conjunct... at reconcile.py:1130";
  I extended this to gate BOTH the `ResumeAttempt` line AND the
  `elif tsf.state == TaskState.ACTIVE:` dead-end-BLOCKED fallback
  immediately below it with the SAME `ready` value — necessary, not
  optional: gating only the resume line would let a merely-still-backing-
  off attempt (ready=False, handle present, budget available) fall
  straight into "no resume handle or attempts exhausted" BLOCKED on the
  very next pass, misreporting a transient wait as a genuine dead end
  (directly contradicting "the task never BLOCKED"). Documented inline and
  in the module's own numbered INTERFACE CONTRACT docstring (item 4). Also:
  `attempts_used` (D-B24-6) now excludes `ReceiptResult.TRANSIENT` from the
  budget exactly like `LIMIT` — added exactly as instructed; documented in
  its own docstring that under THIS implementation's steady-state behavior
  (a transient attempt stays non-terminal/INTERRUPTED, so `attempts_used`'s
  own `TERMINAL_ATTEMPT_STATES` guard already excludes it regardless of the
  receipt filter) this exclusion mostly guards a future/edge path rather
  than being independently reachable today — added anyway per the
  handoff's explicit instruction, cheap and correct, mirrors LIMIT exactly.

## Oracle -> test mapping

- **O1 (classification)**:
  `tests/test_adapters.py::test_classify_log_tail_transient_variants`
  (all 5 signatures -> `"transient"`),
  `::test_classify_log_tail_transient_ordering_beats_limit` (the
  "Worker local total request limit reached" ordering case),
  `::test_classify_log_tail_transient_no_regression` (BLOCKED/
  scope_amendment/genuine-limit/domain-prose all unchanged, INCLUDING the
  existing "429 too many requests" -> "limit" case, proving no regression
  from the narrower HTTP-429 scoping).
- **O2 (TRAP-1, load-bearing)**:
  `tests/test_wrapper.py::TestTransient::
  test_transient_classification_is_interrupted_not_exited` — asserts
  `receipt.result == "transient"`, the statefile attempt ends
  `AttemptState.INTERRUPTED` (explicitly asserted `is not EXITED`), exactly
  one `ATTEMPT_INTERRUPTED` event and ZERO `ATTEMPT_EXITED` events.
- **O3 (backoff gate)**:
  `tests/test_reconcile.py::test_transient_backoff_not_ready_parks_no_resume_no_blocked`
  (not-ready: neither ResumeAttempt nor BLOCKED),
  `::test_transient_backoff_ready_resumes` (ready: ResumeAttempt planned),
  `::test_transient_backoff_ready_defaults_true_for_non_transient`
  (no-regression: missing entry still resumes, byte-identical to pre-B24).
  Plus the daemon-level timing unit tests for the underlying helper:
  `tests/test_daemon.py::test_transient_backoff_ready_first_resume_always_ready`,
  `::test_transient_backoff_ready_waiting_vs_elapsed` (both directions),
  `::test_transient_backoff_ready_picks_newest_of_multiple_logs`,
  `::test_transient_backoff_ready_capped_forever_false`,
  `::test_transient_backoff_ready_missing_log_falls_back_to_ready`,
  `::test_transient_backoff_ready_non_interrupted_and_non_transient_excluded`.
- **O4 (end-to-end, modeled on test_behavioral.py:431-517)**:
  `tests/test_behavioral.py::test_transient_throttle_resumes_same_attempt_end_to_end`
  — real `Daemon.run_pass` loop, `fake_cli` with `FakeStep(extra_stdout=
  "Upstream idle timeout after 90s, connection reset")`, then a
  `_impl_commit_step()`; asserts exactly one `ATTEMPT_RESUMED` for the SAME
  `attempt_id`, `attempt.resume-1.log` exists, exactly ONE implementer
  `ATTEMPT_CREATED` (no fresh dispatch), and the task never `BLOCKED`. Note:
  the shared `fake-impl` test route has no `resume` template and the fake
  CLI has no real session-capture wiring (`capture_session` for a bare
  `cli="fake"` route with neither `session_capture` nor `session_discover`
  set always returns `None`) — the test adds a local `resume` template
  (literal `TASK_ID` baked in, since `build_resume`'s mapping only supports
  `{session}`/`{worktree}`/`{prompt}`) and seeds `session_handle` manually
  once the attempt is observed `INTERRUPTED`, mirroring
  `test_mark_interrupted_and_resume`'s (test_daemon.py) identical manual
  seeding — so it is the daemon's own gate under test, not the harness's
  session-capture plumbing.
- **O5 (bounded escalation, modeled on test_daemon.py:1024-1047)**:
  `tests/test_daemon.py::test_transient_escalate_below_cap_no_action`
  (under cap: no events),
  `::test_transient_escalate_at_cap_pauses_and_requeues` (at cap: task
  QUEUED, `PROVIDER_STATE_CHANGED{state: "throttled"}`, `NEEDS_OPERATOR`,
  and `_provider_ok(routes)["fake-cli"] is False` on the next snapshot),
  `::test_transient_escalate_excludes_non_matching_tasks` (every guard
  clause individually: non-ACTIVE task, no attempts, non-INTERRUPTED
  latest attempt, non-IMPLEMENTER role, non-TRANSIENT receipt — none
  escalate even when otherwise at cap),
  `::test_transient_escalate_full_pass_bounded_and_reroutes` (full
  `run_pass` end-to-end: task QUEUED + events on pass 1, `captured[1].
  provider_ok["fake-cli"] is False` on pass 2, mirroring
  `test_emit_attempt_exit_limit`'s exact two-pass shape).
- **O6 (budget exclusion)**:
  `tests/test_reconcile.py::test_attempts_used_excludes_transient` (mirrors
  the existing `test_attempts_used_counts_only_implementer_not_review_or_carve`'s
  LIMIT-exclusion shape exactly, for TRANSIENT).

## Verification

- `python -m py_compile` on all 5 changed source modules and all 5 changed
  test files: **clean**.
- This devcontainer happens to have the app's full dependency closure
  available (unlike the documented cockpit-vs-gating split, which assumes
  it normally does not) — used as a smoke check only, per the handoff's own
  allowance, NOT as the authoritative gate:
  - `pytest tests/test_adapters.py tests/test_wrapper.py
    tests/test_reconcile.py tests/test_daemon.py -q` — **all green**, run
    both in isolation (`-k transient`/`-k "Transient or Limit"` etc.) and as
    full-file runs, no regressions.
  - `pytest tests/test_behavioral.py -q` (full file, 15 tests) — **all
    green**, no cross-test interference from the new test's local
    `routes.toml` override.
  - `pytest tests/ -q` (the WHOLE suite, every test file) — **all green**,
    exit code 0 (one pre-existing `x` xfail, unrelated to this change). No
    regressions anywhere in the suite from this diff.
- **Never claim the real gate passed**: the authoritative gate is the
  Docker `tester-unified` run under a 100% diff-coverage floor
  (`./scripts/testing-exec.sh` equivalent for this repo), which this agent
  did not and cannot run. All of the above is local-devcontainer pytest
  only.

## Commit

See the branch's HEAD commit for the exact hash (recorded at commit time,
after this file was written — the LOG entry appended post-commit carries
the actual hash if this line was not updated first).
