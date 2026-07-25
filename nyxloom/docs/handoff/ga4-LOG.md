# GA4 — carver periodic gate re-verify — Implementation log

**Branch:** feat/ga4-gate-verify-cadence
**Commit:** `56c1831c` (worktree `/workspaces/vbpub/.worktrees/ga4-gate-verify-cadence`)
**Status:** Complete. Gate GREEN (pytest 0, diff-coverage 100%).

## What / where

| File | Change |
|---|---|
| `src/nyxloom/reconcile.py` | Module-contract docstring item 16 (mirrors item 15's D-065 write-up). New `VerifyGate(Action)` dataclass (right after `CarveDispatch`) with a `project` field. New `ReconcileInput.days_since_gate_verify: float \| None = None` field (sibling of `days_since_test_health_carve`). New `"gate-verify"` `TRACE_KINDS` entry. New planner clause in `plan_project` (right before "=== Combine results ==="): fires `VerifyGate` when `policy.gate_verify_interval_days > 0` and (`days_since_gate_verify is None` or `>= interval`), gated ONLY on `project_paused` — deliberately outside the `carve_dispatch_planned` / `carve_in_flight` / `frontier_route_available` / `budget_allows` mutex item 9/12/15 share. Breadcrumbs `("gate-verify", None, "fire"/"paused")`. `gate_verify_actions` extended into the final `actions` list alongside `carver_actions`. |
| `src/nyxloom/daemon.py` | `import queue`; `gate_canary` added to the module import group. `Daemon.__init__`: `self._gate_verify_running: dict[str, threading.Thread] = {}`, `self._gate_verify_results: queue.Queue = queue.Queue()`. New `_days_since_gate_verify` (mirrors `_days_since_test_health_carve`, scans for `GATE_VERIFY_RECORDED` events by type rather than a `TASK_CREATED` payload marker, same 0.0-fail-safe direction). Wired into `_build_input`'s `ReconcileInput(...)` call. New `_execute_verify_gate` (idempotent thread-spawn), `_run_gate_verify_bg` (the actual probe — selects the gate via `gate_runner.select_verification_gate`, resolves HEAD, runs the good-HEAD gate via `gate_runner.run_gate_at_commit`, then `gate_canary.verify_gate_rejects_canary`; derives verdict NO_GATE / BROKEN / TRUSTWORTHY / LAUNDERS / INCONCLUSIVE exactly mirroring `cli.cmd_gate_verify`'s own derivation; wrapped end-to-end in try/except → INCONCLUSIVE on any failure; `.put()`s a result dict on the queue, touches nothing else), `_drain_gate_verify_results` (main-thread-only consumer, called once per pass from `run_pass`; drains the WHOLE shared queue, re-queues results for other projects, appends `GATE_VERIFY_RECORDED` + a debounced `NEEDS_OPERATOR{reason: gate-verify-<verdict>}` on LAUNDERS/BROKEN via the existing `_needs_operator_recently_emitted`). New `elif isinstance(action, reconcile.VerifyGate)` branch in `_execute`. New `appended.extend(self._drain_gate_verify_results(...))` call in `run_pass`, right after the per-action execution loop. |
| `src/nyxloom/config.py` | `Policy.gate_verify_interval_days: int = 0` (sibling of `test_health_interval_days`, same doc-comment convention). |
| `src/nyxloom/schemas/nyxloom-config.schema.json` | `"gate_verify_interval_days": {"type": "integer", "minimum": 0}` added to `policy.properties` (required by `test_every_policy_field_is_toml_settable_or_explicitly_infra_sourced` in `tests/test_test_health_carve.py`, which sweeps every `Policy` field generically). |
| `src/nyxloom/types.py` | `EventType.GATE_VERIFY_RECORDED = "GATE_VERIFY_RECORDED"` — audit-only, no projection. |
| `tests/test_invariants.py` | `EventType.GATE_VERIFY_RECORDED` added to `KNOWN_IGNORED_EVENT_TYPES` (the meta-invariant `test_every_event_type_handled_or_known_ignored` would otherwise fail on the full-suite run). |
| `tests/test_gate_verify_cadence.py` (new) | All oracles below. |

## Execution-model choice (as specified, no deviation)

A full verify runs the project's gate against several canary commits (real
subprocess gate runs — minutes), so it cannot run inline in a reconcile tick.
Implemented exactly as directed: **background thread + result queue**, event
appends confined to the main thread.

- `_execute_verify_gate` is the `VerifyGate` action handler: idempotent while
  `self._gate_verify_running[project]` is alive (no second thread starts) —
  this is *why* the planner may harmlessly replan the same `VerifyGate` every
  pass until the cadence resets.
- `_run_gate_verify_bg` is the thread body. It never touches the event log or
  daemon state — it only `.put()`s a small `{project, verdict, gate_id}` dict.
  Any exception anywhere in the probe degrades to `INCONCLUSIVE` rather than
  letting the thread die silently.
- `_drain_gate_verify_results` is the sole consumer, called once per pass from
  `run_pass` (main thread). The result queue is shared across every
  registered project (one `Daemon`, one `queue.Queue`), so the drain call
  processes results for `project` and **re-queues** any result belonging to a
  different project — safe because `run_pass` loops over every registered
  project each tick, so that project's own call reclaims its result.
- On daemon restart mid-verify the in-memory thread is simply lost; the
  cadence re-fires on the next pass (idempotent, no durable half-state) — as
  specified, no extra machinery added for this case.

## Oracles — all implemented in `tests/test_gate_verify_cadence.py`

- **Feature-off byte-identical (LOAD-BEARING).** Two tests:
  `test_feature_off_pure_planner_emits_no_verify_gate_LOAD_BEARING`
  (parametrized over never-verified / recent / wildly-overdue /
  paused — `interval=0` emits **no** `VerifyGate` in any case) and
  `test_feature_off_real_run_pass_starts_no_thread_and_appends_no_event_LOAD_BEARING`
  (a REAL, unstubbed `run_pass` against `sample_project`'s default
  `gate_verify_interval_days == 0` config: asserts `_gate_verify_running == {}`,
  the result queue stays empty, and no `GATE_VERIFY_RECORDED` /
  `NEEDS_OPERATOR{gate-verify-*}` event is ever appended). **Confirmed
  passing** (see gate output below; both green in the docker run).
- **Planner cadence:** never-verified fires; overdue fires; exactly-at-interval
  fires (boundary, `>=` not `>`); not-yet-due does not fire; paused project
  gets none.
- **Mutex independence (the actual design call item 16 makes):**
  `test_verify_gate_does_not_compete_with_the_single_carve_slot` fires BOTH a
  headroom `CarveDispatch` and a `VerifyGate` in the same pass; separate tests
  prove `VerifyGate` still fires with no healthy frontier route, exhausted
  budget, and a carver already in flight — none of item 9/12/15's guards
  apply.
- **Breadcrumb:** `"gate-verify"`/`"fire"` on fire, `"paused"` on the paused
  skip, absent entirely when the feature is off.
- **Cadence primitive (`_days_since_gate_verify`):** None when never verified;
  real age computed from event timestamp; latest-of-several selected; bad
  (naive) timestamp and unreadable log both fail-safe to `0.0`, never `None`.
- **Executor idempotence:** a live background thread blocks a second thread
  from starting (`test_execute_verify_gate_starts_no_second_thread_while_one_is_alive`,
  with a controlled-release fake thread body); positive control
  (`test_execute_verify_gate_starts_a_fresh_thread_once_the_prior_one_finished`)
  proves a genuinely-finished verify DOES get a fresh thread next time.
- **`_execute` isinstance wiring:** dedicated test confirms the `VerifyGate`
  branch actually calls `_execute_verify_gate`.
- **Verdict derivation (`_run_gate_verify_bg`):** NO_GATE (no declared gate);
  BROKEN (good HEAD fails — canary asserted to never run in this case);
  TRUSTWORTHY (canary killed); LAUNDERS (every canary survives); INCONCLUSIVE
  (canary probe inconclusive, unresolvable HEAD, and an unhandled exception —
  three separate tests).
- **Drain / escalation:** LAUNDERS and BROKEN both append
  `GATE_VERIFY_RECORDED` + a `NEEDS_OPERATOR{reason: gate-verify-<verdict>}`;
  TRUSTWORTHY appends only the record, no escalation (negative); a repeated
  LAUNDERS debounces the second escalation via the existing
  `_needs_operator_recently_emitted`; a result for a different project is
  re-queued, not consumed or lost.
- **End-to-end:** `test_end_to_end_dispatch_thread_then_drain_closes_the_cadence`
  dispatches a real `VerifyGate` through a real `run_pass`, joins the real
  background thread (only the slow gate/canary calls are faked, per the
  handoff's own instruction), then drives a second `run_pass` and asserts the
  `GATE_VERIFY_RECORDED` event, its verdict/gate_id, no spurious escalation,
  and that `_days_since_gate_verify` now returns a real (small) age.
- **Meta:** `EventType.GATE_VERIFY_RECORDED` registered in
  `KNOWN_IGNORED_EVENT_TYPES` (`tests/test_invariants.py`);
  `gate_verify_interval_days` present in the config schema with
  `test_gate_verify_interval_days_is_schema_valid_in_the_repos_own_config`.

## Gate verdict (verbatim, from the mandated docker command)

```
PYTEST_EXIT:0
diff-coverage OK: 91/91 changed executable lines covered (100.0% ≥ 100.0% floor)
GATE_EXIT:0
```

Full suite (all of `tests/`, `-n 4`, coverage-tracked) is green, and every one
of the 91 changed executable lines across `reconcile.py`/`daemon.py`/
`config.py`/`types.py`/the new test file is covered — no gaps.

A prior local (devcontainer, non-gating) full-suite run
(`PYTHONPATH=src python3 -m pytest tests -q`) was also green, confirming no
regression before spending the docker gate run.

## Not done / deferred

Nothing deferred — every item in the handoff's scope (reconcile.py action +
cadence, daemon.py background-thread execution model + drain, config.py +
schema knob, types.py event, the meta test-invariants registration, and the
full oracle set) is implemented and gate-verified. `nyxloom-trove/nyxloom.toml`
(the repo's own dogfood config) was deliberately left untouched — enabling the
cadence in production is a separate operational decision (test_health_interval_days
is currently held at 0 there too, mid convergence-freeze), not part of this
package's scope.
