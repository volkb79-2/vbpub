# P11 — property tests + crash drills — REPORT

**Date:** 2026-07-15
**Result:** done
**Oracles:** 10 pass, 0 fail, 0 skipped

## Oracle compliance

| # | Oracle | Test(s) | Result |
|---|--------|---------|--------|
| 1 | Transition soundness (exhaustive) | `test_check_task_transition_exhaustive`, `test_check_attempt_transition_exhaustive`, `test_task_transition_graph_shape`, `test_attempt_transition_graph_shape` | pass |
| 2 | Serde round-trip fuzz + unknown-key rejection | `test_task_state_file_round_trip`, `test_task_state_file_unknown_key_rejected`, `test_event_round_trip`, `test_event_unknown_key_rejected`, `test_frontmatter_round_trip`, `test_frontmatter_unknown_key_rejected` | pass |
| 3 | Replay determinism | `test_replay_determinism` | pass |
| 4 | Sequence integrity under concurrency | `test_sequence_integrity_under_concurrency` | pass |
| 5 | apply_event tolerance | `test_apply_event_attempt_started_never_created_upserts`, `test_apply_event_unknown_task_is_noop`, `test_apply_event_task_transitioned_violating_graph_raises`, `test_apply_event_task_transition_enforces_graph` | pass |
| 6 | append-without-save heals | `test_append_without_save_heals` | pass |
| 7 | statefile atomicity | `test_statefile_atomicity_under_concurrent_saves` | pass |
| 8 | flock release on SIGKILL | `test_flock_release_on_sigkill` | pass |
| 9 | wrapper SIGKILL drill (skip-guarded) | `test_wrapper_sigkill_drill` | pass (not skipped — see note) |
| 10 | event-log fsync visibility | `test_event_log_fsync_visibility` | pass |

21 test functions total (some oracles are covered by more than one test function, per the
serde/transition breakdown above); all 21 pass, 0 fail, 0 skipped. Verified stable across 4
repeated full runs and 3 repeated targeted runs of the two signal/timing-sensitive drills
(oracles 8 and 9) with no flakes observed.

## Files touched

- `tests/test_properties.py` (new) — oracles 1-5
- `tests/test_crash.py` (new) — oracles 6-10

No other files modified; frozen core (`types.py`, `storage.py`, `leases.py`, `paths.py`) and
`wrapper.py` were read only.

## Gate command and output (verbatim)

```
cd /workspaces/vbpub/handoffctl2 && /workspaces/vbpub/.venv/bin/python -m pytest tests/test_properties.py tests/test_crash.py -q
```

```
============================= test session starts ==============================
platform linux -- Python 3.13.5, pytest-9.1.1, pluggy-1.6.0
rootdir: /workspaces/vbpub/handoffctl2
configfile: pyproject.toml
plugins: hypothesis-6.156.6, cov-7.1.0, anyio-4.14.2, asyncio-1.4.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collected 21 items

tests/test_properties.py ................                                [ 76%]
tests/test_crash.py .....                                                [100%]

============================== 21 passed in 5.19s ==============================
```

## Deviations / assumptions

- **Oracle 9 was not skipped.** At the time this handoff was picked up, `src/handoffctl/wrapper.py`
  was still the P04 stub (`launch_detached`/`wrapper_main` raising `NotImplementedError`), so
  `test_wrapper_sigkill_drill` was written skip-guarded as instructed
  (`except NotImplementedError: pytest.skip('P04 pending')`). Partway through this package, P04
  landed for real (visible on disk mid-session). The test's skip guard is retained (harmless,
  correct fallback if wrapper.py ever regresses to a stub), but it now exercises the **real**
  `launch_detached`/`wrapper_main` end-to-end: launches a detached `sleep 30` attempt, waits 1s for
  the wrapper to reach `RUNNING` (leases acquired, `ATTEMPT_STARTED` appended, child spawned), sends
  `SIGKILL` to the wrapper pid itself, then asserts (a) no `receipt.json` was written, (b) the
  spec's lease (`wrapper-drill-lease`) is free within 3s via `leases.holder_info`, and (c) the
  event log for that attempt ends at `ATTEMPT_STARTED` with no `ATTEMPT_EXITED`. All three held.
  Note: P04's own `tests/test_wrapper.py` explicitly **simplified** its oracles 9/10 (SIGTERM/
  SIGKILL) to avoid detached-process complexity (see `handoff/reports/P04-REPORT.md` "Deviations"
  section); this package's oracle 9 is therefore the first real end-to-end SIGKILL drill against
  the actual double-fork/flock/receipt-atomicity contract, and it passed clean.
- Oracle 4's process count/per-process event count (4 x 25) and oracle 8/9's 3s poll caps are
  taken as literal fixed values from the handoff, not hypothesis-fuzzed (the handoff only requires
  hypothesis for oracles 1-5's per-example fuzzing, and oracle 1 explicitly calls for exhaustive
  loops instead).
- Oracle 3 (replay determinism) intentionally avoids the `tmp_state` fixture per the handoff's own
  guidance (function-scoped fixture would be reused, not rebuilt, across hypothesis examples);
  it uses `tmp_path_factory.mktemp(...)` plus a local `os.environ` context manager instead, with a
  fresh project/state root per example.
- Oracle 3's attempt-state interleaving restricts itself to `AttemptState`/`EventType` pairs that
  have both a valid `ATTEMPT_TRANSITIONS` edge and a corresponding `EventType.ATTEMPT_*` member
  (`ATTEMPT_EVENT_FOR` mapping in the test file). `ABANDONED` has no dedicated event type in the
  frozen `EventType` enum, so the random walk never targets it — this is a property of the frozen
  schema, not a test weakening (`apply_event` itself does not validate attempt-state transitions at
  all, only task-state ones, per `storage.py`'s own docstring: "Tolerant on replay ... strict on
  semantics (task transitions are validated)").
- Hypothesis settings: `deadline=None` and `max_examples<=50` throughout, per the handoff cap
  (oracle 3 uses 20 to bound wall-clock cost of the per-example filesystem work; everything else
  uses 30-50).
- No genuine core defect was found; no BLOCKED report needed.

## Suggestions for the reviewer (not acted on)

- P04's own test suite (`tests/test_wrapper.py`) documents simplified oracles 9/10; now that a real
  SIGKILL drill exists and passes here, the reviewer may want to cross-reference that P04-REPORT.md
  deviation note against this package's evidence when assessing P04's completeness.
- The orphaned CLI child (`sleep 30`) spawned by the wrapper during the oracle-9 drill is
  best-effort SIGKILLed by the test via `attempt_dir/child.pid` for hygiene; if a future wrapper
  implementation changes where/whether `child.pid` is written, this cleanup step would silently
  no-op (harmless — the child would just run out its own sleep and exit).
