# P03 — reconcile trace (pure-core observability): REPORT

Branch: `feat/logging-p03-reconcile-trace` · commit: `919524a11f2402aad9a13fed0ac85f3695a7c0bf`
Worktree: `/workspaces/vbpub/.worktrees/logging-p03-reconcile-trace` (from
local `main` @ `b624b16` at worktree-creation time; `main` had advanced to
`51b11166e8fc9417e5848eb0f342efcc0af28b84` by gate time via unrelated
operator commits — the gate command's `--base main` diffed against that
current `main`, and still found only this package's own 39 changed
executable lines, confirming none of that intervening `main` traffic
touched `reconcile.py`/`daemon.py`).
Image gated against: `tester-unified:local` (pre-existing image; already
carries `structlog` from P01's rebuild — confirmed present, no rebuild
needed for this package).

**Not merged. Not deployed. The daemon was never touched (per instructions
it stays stopped throughout).**

## Gate (real exit code, no masking pipe)

Run against the committed HEAD (`919524a`):

```
$ docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
    bash -lc 'cd /workspaces/vbpub/.worktrees/logging-p03-reconcile-trace/nyxloom && \
      PYTHONPATH=src /opt/tester-venv/bin/python -m coverage run --source=src/nyxloom -m pytest tests -q && \
      PYTHONPATH=src /opt/tester-venv/bin/python -m coverage json -o /tmp/nyxloom-cov.json && \
      PYTHONPATH=src /opt/tester-venv/bin/python -m nyxloom.coverage_gate --base main \
        --coverage-json /tmp/nyxloom-cov.json --source src/nyxloom' ; echo GATE_EXIT=$?

........................................................................ [  6%]
........................................................................ [ 13%]
........................................................................ [ 19%]
........................................................................ [ 26%]
........................................................................ [ 32%]
........................................................................ [ 39%]
........................................................................ [ 45%]
.........x.............................................................. [ 52%]
........................................................................ [ 58%]
........................................................................ [ 65%]
........................................................................ [ 71%]
........................................................................ [ 78%]
........................................................................ [ 84%]
........................................................................ [ 91%]
........................................................................ [ 98%]
......................                                                   [100%]
Wrote JSON report to /tmp/nyxloom-cov.json
diff-coverage OK: 39/39 changed executable lines covered (100.0% ≥ 100.0% floor)
GATE_EXIT=0
```

**GATE_EXIT=0. diff-coverage OK: 39/39 changed executable lines (100.0%).**
All dots (one pre-existing `x` = xfail, unrelated to this package, same
position P01/P02's own reports note); no `F`/`E` anywhere. This is the
FIRST and ONLY gate run for this package — no earlier failing run to
disclose (unlike P02's LOG, which narrates two fix-up iterations).

Full test count: pytest collected and ran the whole `tests/` suite
(the same 24-line-of-dots shape P01/P02 reported); 14 new tests added by
this package (12 in `test_reconcile.py`, 2 in `test_daemon.py`) are
included in that run, all passing.

## Oracle-by-oracle evidence (docs/plan-logging.md §6, P03)

1. **`plan_project` stays pure** (no clock, no I/O, no `log`/`nyxloom.log`
   import reachable from reconcile.py; deterministic given identical
   input).
   - `test_reconcile_module_never_imports_logger` — a source-level grep
     over `reconcile.py`'s own file contents for `from .log import`,
     `from nyxloom.log import`, `from . import log`, `from . import
     log_module`, `import nyxloom.log`; none present. Also asserts the
     module object itself has no `log`/`log_module` attribute bound.
   - `test_plan_project_is_deterministic_given_identical_input` — calls
     `plan_project(inp)` twice on the SAME `ReconcileInput`; asserts
     `list(r1) == list(r2)` AND `r1.trace == r2.trace` (dataclass equality
     recurses through `ReconcileTrace.breadcrumbs`, a list of `TraceNote`
     dataclasses) — proves adding the trace introduced no hidden
     nondeterminism (no clock read, no set-iteration-order leak, etc.).

2. **The trace records the decisive reason for representative cases.**
   - **dispatch (names route):** `test_trace_dispatch_names_the_route` —
     `TraceNote(kind="dispatch", task_id="P01", detail="route:route-1")`.
   - **dispatch-skip (paused / no-route / budget):**
     `test_trace_dispatch_skip_paused` (`detail == "paused"`),
     `test_trace_dispatch_skip_no_healthy_route` (`detail ==
     "no-healthy-route"`), `test_trace_dispatch_skip_budget_exhausted`
     (`detail == "budget-exhausted"`) — all three drawn directly from
     `dispatch_eligible`'s own existing reason vocabulary, so the
     breadcrumb detail IS that function's return value, not a
     reinterpretation.
   - **carve-skip (paused / in-flight):** `test_trace_carve_skip_paused`
     (`detail == "paused"`, `task_id is None` — a project-wide decision),
     `test_trace_carve_skip_in_flight` (`detail == "in-flight"`, with a
     CARVER attempt on a non-terminal task as the trigger).
   - **guard-exclude (decision-held):**
     `test_trace_guard_exclude_decision_held` — a QUEUED task with an open
     decision dep, excluded from the carve trigger's admissible-ready
     count; `TraceNote(kind="guard-exclude", task_id="Q1", detail=
     "decision-held")`.
   - **a state transition:** `test_trace_state_transition_carved_to_queued`
     — `TraceNote(kind="state-transition", task_id="P01", detail=
     "CARVED->QUEUED")`.
   - Two more kinds not explicitly required by the oracle list but
     completing `TRACE_KINDS` (§4.3's full vocabulary) are also exercised:
     `merge` (via the pre-existing
     `test_auto_merge_fires_when_guarded_automatic_and_merge_ready`, which
     now also produces a `TraceNote(kind="merge", detail="auto-merge")`
     without any new test needed) and `carve` (via
     `test_carve_trigger_fires_below_target_no_carver_inflight` /
     `test_ready_to_carve_dispatches_carve_and_plans_no_supersede_transition`,
     ditto).

3. **The daemon emits exactly one DEBUG record per breadcrumb, each with
   `project` bound.**
   - `test_reconcile_trace_flushed_as_one_debug_record_per_breadcrumb`
     (`tests/test_daemon.py`) — stubs `plan_project` to return a
     `PlanResult([], trace=<3 breadcrumbs>)`; configures logging to a
     `tmp_state / "logs"` dir at DEBUG with `console=False`; calls
     `d.run_pass("demo")` directly (no HTTP thread needed); reads back
     `logs/nyxloom.jsonl` via the existing `_read_log_records` helper
     (the same one P01's tests use); asserts exactly 3 records with
     `level == "debug"` and `msg == "reconcile-trace"`, every one carrying
     `project == "demo"`, and each record's `kind`/`task`/`detail` fields
     matching the 3 seeded breadcrumbs exactly.
   - `test_reconcile_trace_flush_skipped_when_plan_project_returns_bare_list`
     — the back-compat companion: stubs `plan_project` to return a bare
     `list` (no `.trace`); `run_pass` must NOT raise `AttributeError`, and
     must emit ZERO `"reconcile-trace"` records (nothing to flush). This
     doubles as coverage for the `if trace is not None:` guard's False
     branch, alongside the 15 pre-existing tests that already return bare
     lists.

4. **Breadcrumbs carry ids/enums only — no handoff prose.**
   - `test_trace_breadcrumbs_carry_ids_and_enums_never_prose` — sweeps 4
     representative scenarios (a clean dispatch, a paused dispatch-skip, a
     paused carve-skip, an in-flight carve-skip) and asserts every single
     breadcrumb's `detail` contains no space character and is under 80
     chars — a concrete proxy for "not free-form prose" (every fixed
     vocabulary string in this package — `"paused"`, `"in-flight"`,
     `"no-healthy-route"`, `"budget-exhausted"`, `"decision-held"`,
     `"CARVED->QUEUED"`, `"route:route-1"`, `"auto-merge"`, `"headroom"`,
     `"ready-to-carve"`, plus `dispatch_eligible`'s own
     `deps-unmerged:<id>` / `decision-hold:<D-id>` / `lease-unavailable:
     <name>` reasons — is a single unspaced token or `key:id` pair, never a
     sentence). Asserts `{"dispatch", "dispatch-skip", "carve-skip"} <=
     seen_kinds` so the sweep actually exercised more than one kind.

5. **Back-compat: the ~50 (actually 121) existing `test_reconcile.py`
   tests still pass unchanged.**
   - Zero lines in any PRE-EXISTING test were touched. The full gate run
     above (all dots, one unrelated pre-existing `x`) is the proof; the
     PlanResult-is-a-list property is additionally named directly by
     `test_plan_result_is_a_list_backcompat` (`isinstance(result, list)`,
     `isinstance(result, PlanResult)`, `isinstance(result.trace,
     ReconcileTrace)`, `len(result) == 1`, `result[0]` indexable, `result
     == [result[0]]` — list-subclass equality against a plain list
     literal).

## The PlanResult back-compat shape (D-L5)

Chose **`PlanResult(list)`** — `plan_project` returns
`PlanResult(actions, trace=trace)` — over a plain `(actions, trace)` tuple,
per the handoff's own steer, and verified (not merely assumed) it holds:

- Grepped every `plan_project(...)` call site across `test_reconcile.py`
  (20 distinct call sites, 121 tests collectively) and every
  `monkeypatch.setattr(reconcile, "plan_project", ...)` site in
  `test_daemon.py` (15 sites). ALL consume the return value via
  `isinstance(a, ...)` filtering, `len()`, or iteration — never a check
  that would distinguish a `list` from a `PlanResult` subclass, and never
  an equality check against the WHOLE return value as a tuple (which a
  `(actions, trace)` shape would have broken instantly on every single
  call site).
- `list.__eq__` compares by content, not exact type, so
  `PlanResult([x]) == [x]` — confirmed directly by
  `test_plan_result_is_a_list_backcompat`'s last assertion.
- `daemon.py`'s own `for action in actions:` loop (right below the
  `plan_project` call) needed zero changes.
- The 15 daemon-side tests that monkeypatch `plan_project` to return a bare
  `list` (not a `PlanResult`) proved this ISN'T a purely theoretical
  concern — the real shape flowing through the test suite today is
  "sometimes `PlanResult`, sometimes plain `list`" — which is exactly why
  the daemon's flush code reads `getattr(actions, "trace", None)` rather
  than assuming every caller constructs a `PlanResult`.

No alternative shape was found cleaner; the handoff's own preference
matched what the codebase's actual call-site shapes required.

## Deviations from the handoff

- **Worktree branch point:** handoff named `main @ e664ad7`; actual `main`
  HEAD at worktree-creation time was `b624b16` (several unrelated commits
  ahead — wings-cgroups, cmru, ciu work). `e664ad7` (the logging-P02 merge)
  is an ancestor of `b624b16`, so P01+P02's logging core and runtime
  control are both present and unaffected; this is noted as a deviation
  only because the exact hash differs from the prompt, not because
  anything was missing.
- **Two extra positive-kind breadcrumbs beyond the oracle's explicit list**
  (`carve` at both the item-9 headroom trigger and item-12 re-carve
  handler; `merge` at the guarded-automatic-merge site) — added because
  they complete `TRACE_KINDS`'s full vocabulary from §4.3 at near-zero
  marginal cost (each is a single `trace.note(...)` line immediately after
  an existing, already-tested `action.append(...)`), not because the
  oracle strictly required them. No new tests were needed for these — the
  pre-existing `test_auto_merge_fires_when_guarded_automatic_and_merge_ready`,
  `test_carve_trigger_fires_below_target_no_carver_inflight`, and
  `test_ready_to_carve_dispatches_carve_and_plans_no_supersede_transition`
  already execute these lines, which is how they reached 100%
  diff-coverage without dedicated assertions (the assertions ARE present,
  just folded into oracle-2's test list above via the pre-existing tests'
  continued passing).
- **One guard restructured** (item 9's untargeted carve trigger): the
  original single `if not inp.project_paused and not carve_in_flight and
  not carve_dispatch_planned:` became an `if/elif/elif` chain purely to
  attribute WHICH of the three conditions caused a skip. Verified the
  truth table producing `carve_actions` is byte-identical to before (same
  three pre-existing carve tests — `test_carve_trigger_fires_below_target_no_carver_inflight`,
  `test_carve_trigger_none_when_project_paused`,
  `test_carve_trigger_none_when_carver_already_inflight` — all pass
  unchanged, proving the action-producing behavior, not just the trace,
  is unaffected).
- Everything else matches the handoff as specified: `ReconcileTrace`/
  `TraceNote` are both frozen pure dataclasses with no clock/I/O;
  `plan_project`'s only new import is `Iterable` from `typing` (used
  solely for `PlanResult.__init__`'s type hint); `log.py` was never
  touched; the daemon flush is the ONLY place the trace ever reaches a
  logger, gated by `getattr(..., None)` for back-compat with bare-list
  stubs.

## For the controller (next steps, out of this package's scope)

- Merge is a controller action per the handoff's constraints (this session
  never merges and never touches the running daemon, which stays stopped
  throughout). Re-validate from `main` post-merge via the same gate
  command, per the review checklist's environment-specific-claims item.
- P04 (log-stream UI) depends on P01 + P02 (already merged) — this package
  (P03) is independent of P04's critical path but completes the
  `docs/plan-logging.md` §6 phase sequence's third of four foundational
  phases before the P05* instrumentation sweep can begin (P05a/P05b/P05c
  all depend on P01 + P03).
