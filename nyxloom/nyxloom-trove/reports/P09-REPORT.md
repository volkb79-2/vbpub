# P09 — nyxloomd (resident reconciler + HTTP/SSE) — REPORT

**Result: done**

Date: 2026-07-15.

## Gate output (verbatim tail)

Command: `cd /workspaces/vbpub/nyxloom && /workspaces/vbpub/.venv/bin/python -m pytest tests/test_daemon.py -q`

```
..................                                                       [100%]
18 passed in 1.88s
```

Re-run 3x consecutively with identical output (no flakiness observed, including
the threaded HTTP/SSE tests).

## Per-oracle results

| # | Oracle | Test(s) | Result |
|---|--------|---------|--------|
| 1 | CreateTask/Transition | `test_create_task_and_transition` | pass |
| 2 | DispatchImplementer | `test_dispatch_implementer` | pass |
| 3 | EmitAttemptExit healing (done/blocked/limit/error) | `test_emit_attempt_exit_done`, `test_emit_attempt_exit_blocked`, `test_emit_attempt_exit_limit`, `test_emit_attempt_exit_error_retry`, `test_emit_attempt_exit_error_exhausted` | pass (5/5) |
| 4 | MarkInterrupted/ResumeAttempt/InterruptAttempt | `test_mark_interrupted_and_resume`, `test_interrupt_attempt_signals_pgid` | pass (2/2) |
| 5 | OpenWave/LaunchReview | `test_open_wave_and_launch_review` | pass |
| 6 | SpecAttention | `test_spec_attention` | pass |
| 7 | TICK_ERROR | `test_tick_error_recovers` | pass |
| 8 | Input building (recording plan_project) | `test_input_building` | pass |
| 9 | pidfile (alive blocks / dead allowed) | `test_pidfile_alive_blocks_start`, `test_pidfile_dead_pid_allowed` | pass (2/2) |
| 10 | HTTP surface | `test_http_endpoints` | pass |
| 11 | SSE | `test_sse_stream_and_stop` | pass |
| 12 | run_once | `test_run_once_no_pidfile_no_port` | pass |

**Totals: 18 tests, 18 pass, 0 fail.**

## Files touched (owned only)

- `src/nyxloom/daemon.py` — full implementation (was a raising stub).
- `tests/test_daemon.py` — new, 18 tests covering all 12 oracles.
- `handoff/reports/P09-REPORT.md` — this file.

No other file was read-for-effect beyond STANDING.md, my own handoff, and the
frozen/sibling module docstrings (types, storage, config, leases, paths,
reconcile, adapters, wrapper, render, notify, decisions, frontmatter, lint) —
all read-only.

## Cross-package coordination note

At read time, `adapters.py`, `wrapper.py`, `render.py`, `notify.py`,
`decisions.py`, `frontmatter.py`, and `reconcile.py` were already implemented
for real by their respective parallel packages (only `lint.py`'s
`lint_file`/`lint_project` still raised `NotImplementedError`). Per STANDING's
"monkeypatch where your handoff says so," I monkeypatched exactly the seams
P09-daemon.md names (`reconcile.plan_project`, `wrapper.launch_detached`,
`adapters.probe`/`build_dispatch`/`build_resume`, `render.render_after_event`,
`notify.notify_event`) in every test, plus `lint.lint_project` (named
explicitly for oracle 8, but needed in every test since `run_pass` calls it
unconditionally while building `ReconcileInput`, and it was still a stub).
This makes the suite's outcome independent of sibling packages' completion
state at gate time, as intended.

## Design decisions / deviations from the docstring (for reviewer judgment — not acted on further)

1. **`merged_branches` dual population.** The `run_pass` docstring describes
   `merged_branches` as `git branch --merged` output (branch names like
   `feat/<id>`), but `reconcile.py`'s **already-implemented**
   `dispatch_eligible` compares the bare `dep_id` (task id) directly against
   that set (`dep_id not in inp.merged_branches`). I populate the set with
   both the raw branch name and, for any `feat/<id>` branch, the bare `<id>`
   too (and likewise for tasks whose statefile is already MERGED+), so both
   readings are satisfied. Flagging the docstring/implementation mismatch for
   the reviewer rather than editing `reconcile.py` (not my file).
2. **`merge_history` / SPEC-health fields are best-effort.** None of the 12
   oracles exercise reconcile's PROGRESS RATCHET (§8) or SPEC HEALTH (§9)
   triggers, and `MERGE_RECORDED`'s payload contract per `storage.py` is only
   `{"merge_commit": str}` (no progress-unit count). I derive
   `merge_history` entries with a placeholder `0` units / `'review'` source
   from `MERGE_RECORDED` events, and `carve_outcomes` /
   `review_rejections_by_area` / `blocked_underspecified_count` /
   `ratchet_already_open` from a best-effort scan of recent events. These are
   real, non-crashing implementations but not rigorously specced or tested;
   a reviewer wiring the ratchet/spec-health features for real should
   revisit them (e.g. cross-reference `PROGRESS_RECORDED` events per task).
3. **`decisions.reconcile_decisions` wiring.** The design constraints require
   a "decision seen map" in daemon memory. I call
   `decisions.reconcile_decisions(cfg, states, seen)` each pass and append
   any `(DECISION_OPENED|DECISION_RESOLVED, decision_id)` events it returns,
   updating `seen` from a fresh `decisions.parse_inbox` read afterward (since
   `reconcile_decisions` itself does not mutate `seen`). Not directly
   exercised by the 12 oracles (oracle 8 only monkeypatches
   `decisions.open_ids`), but present for contract completeness.
4. **LaunchReview's synthetic attempt is attached to one task.** A frontier
   review attempt logically spans the whole wave, but `TaskStateFile.attempts`
   is per-task and `apply_event`'s `ATTEMPT_*` upsert requires a single
   `task_id`. I attach the review `Attempt` to the first task in
   `action.task_ids`; the packet dir (with one `<task_id>.diff` per task) is
   still assembled for the whole wave. Oracle 5 only requires the attempt to
   exist with the right route/role, which this satisfies.
5. **`_confirm_stall`'s tier-2 CPU check** reads `/proc/<pid>/stat` fields 14
   (utime) and 15 (stime) — Linux-only, matching this devcontainer/CI
   environment. Not exercised by any of the 12 oracles (no stall oracle is
   in the required list); implemented per the docstring but unverified by a
   dedicated test.
6. **HTTP port collision avoidance.** `Policy.http_port` defaults to 8942
   project-wide; since this devcontainer runs many parallel package builds
   concurrently, HTTP/SSE tests rewrite the test's own copy of
   `project.toml` to set `http_port = 0` before starting the daemon, so the
   OS assigns an ephemeral port — this is exactly the "support requesting
   port 0" design constraint, and `Daemon.http_port` always reflects the
   real bound port regardless of what the config requested.
7. **`notify.notify_event` is also called for `DAEMON_STARTED`/
   `DAEMON_STOPPED`** (emitted directly by `run()`, outside of `run_pass`),
   extending the "after every event append: notify.notify_event" rule beyond
   strictly the per-pass execution map. Judgment call; harmless since
   `notify_event` is idempotent/side-effect-bounded and neither event type is
   normally in `push_classes`.

## Suggestions for the reviewer (not acted on)

- Consider whether `merged_branches` should be normalized on the
  `reconcile.py` side instead (bare task ids only) now that both P02 and P09
  are implemented, to remove the dual-population workaround in item 1 above.
- If/when the progress ratchet and spec-health triggers become load-bearing,
  wire `merge_history`'s unit counts from `PROGRESS_RECORDED` events per task
  rather than the current placeholder.
