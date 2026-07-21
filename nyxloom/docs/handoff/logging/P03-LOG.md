# P03 — reconcile trace (pure-core observability): LOG

Branch: `feat/logging-p03-reconcile-trace`
Worktree: `/workspaces/vbpub/.worktrees/logging-p03-reconcile-trace` (from
`main` @ `b624b16` — the branch point; the handoff prompt named `e664ad7`,
the logging-P02 merge commit, but local `main` had advanced past it with
unrelated wings-cgroups/cmru/ciu work by the time this worktree was created;
`e664ad7` is an ancestor of `b624b16`, so P01+P02's logging core and P02's
runtime control are both present).

## Context read (per the handoff's "Context to read first")

1. `docs/plan-logging.md` §4.3 (Reconcile trace design), §3 D-L5 (RESOLVED:
   ReconcileTrace return channel — a `PlanResult` list-subclass preferred
   over a bare tuple, explicitly to avoid churning ~50 call sites), §6 P03
   (the oracle list), §5 (DEBUG tier: "the reasoning: reconcile trace
   breadcrumbs (why dispatched / why skipped), guard evaluations...").
2. `src/nyxloom/reconcile.py` in full (1591 lines) — the module contract
   docstring (15 numbered items), every `Action` dataclass, `ReconcileInput`,
   and the whole body of `plan_project` plus its helpers
   (`dispatch_eligible`, `fresh_start_eligible`, `attempts_used`,
   `implementer_record_count`). Confirmed: **zero** existing `log`/`logging`
   import anywhere in this file (purity is currently intact — nothing to
   "preserve" so much as "must not introduce").
3. `src/nyxloom/daemon.py` around `run_pass` (the sole call site of
   `reconcile.plan_project`, line 729) — confirmed `log = get_logger("daemon")`
   already exists (P01) and `from .log import get_logger` is already
   imported; `bind` was NOT yet imported (P02 never used it) — added it here.
4. `tests/test_reconcile.py` (3833 lines, 130 `def test_`) — read the header
   helpers (`make_config`, `make_frontmatter`, `make_tsf`, `make_attempt`,
   `make_routes`, `_carve_base_kwargs`) and grepped every
   `plan_project(...)` call site (20 distinct call sites, ~130 tests
   collectively) to confirm HOW the return value is consumed: always via
   `isinstance(a, ...)` filtering, `len()`, or list-comprehension iteration
   over `actions` — never `type(actions) is list` or any check that would
   reject a `list` *subclass*. This is what made the `PlanResult(list)`
   subclass choice safe.
5. Grepped `tests/test_daemon.py` for every `monkeypatch.setattr(reconcile,
   "plan_project", ...)` call (15 sites) — ALL of them return a **bare
   `list`** (via `lambda inp: []`, a `_scripted` helper popping from a
   plain list, or a `boom` that raises). None construct a `PlanResult`.
   This is the load-bearing fact that shaped the daemon-side flush code:
   it MUST use `getattr(actions, "trace", None)` and treat a missing
   `.trace` as "nothing to flush," never assume every caller returns a
   `PlanResult`.
6. `src/nyxloom/log.py` in full — read-only (never edited, per scope).
   Confirmed `get_logger`, `bind` (contextmanager), `configure(level,
   log_dir, console)`, `DEBUG`/`INFO` constants are all already exported
   and usable from `daemon.py` without any change to `log.py` itself.
7. `_read_log_records` helper + the P01 tests around it in
   `tests/test_daemon.py` (`test_nonloopback_bind_prints_unauthenticated_notice`,
   `test_loopback_bind_prints_no_notice_THE_NEGATIVE`) — the established
   pattern for asserting against rendered JSONL records: `log.configure(level=...,
   log_dir=tmp_state / "logs", console=False)`, then `d.run_pass("demo")`
   directly (no HTTP thread needed for `run_pass`), then read back
   `logs/nyxloom.jsonl`.

## Design decision (D-L5): PlanResult shape

Chose `PlanResult(list)` — a `list` subclass, constructed as
`PlanResult(actions, trace=trace)` — over a `NamedTuple`/plain tuple
`(actions, trace)`. Verified concretely (not just per the handoff's steer)
that this is safe:
- `list.__eq__` compares by *content*, not exact type, so
  `PlanResult([x]) == [x]` is `True` — every existing `assert actions ==
  [...]`-style comparison (if any existed) would still pass. In practice
  the ~130 existing tests never compare the WHOLE return value to a literal
  list anyway — they always filter/index/len() it — but this was checked.
- `isinstance(actions, list)` is `True` for a `PlanResult` (subclass), so
  any hypothetical type-narrowing check downstream still holds.
- The daemon's own `for action in actions:` loop (a few lines below the
  `plan_project` call, at the top of `run_pass`) iterates it exactly like a
  plain list — zero change needed there.
- The 15 daemon-side monkeypatches returning a bare `list` never construct
  a `PlanResult` at all — proving the REAL shape in play across the test
  suite is "sometimes `PlanResult`, sometimes plain `list`," which is
  exactly why the daemon flush code reads `.trace` via `getattr(...,
  None)` rather than assuming it's always present.

## Where breadcrumbs were placed (module contract cross-reference)

All additions are `trace.note(kind, task_id, detail)` calls immediately
following an existing `action.append(...)` (or `continue`) — no new
conditionals were introduced except restructuring ONE guard (see below) to
attribute a skip reason. Each note site maps to a module-contract item:

- **item 1** (CARVED→QUEUED, lint_clean) → `state-transition`,
  `"CARVED->QUEUED"`.
- **item 2** (decision hold, both directions) → `state-transition`,
  `"QUEUED->NEEDS_DECISION"` / `"NEEDS_DECISION->QUEUED"`.
- **item 3** (dispatch loop) → `dispatch` (route id, on success) /
  `dispatch-skip` (the `dispatch_eligible` reason string, on failure) — the
  `else` branch of the existing `if eligible:` was previously empty
  (implicit skip); now it appends the breadcrumb.
- **item 9** (untargeted headroom-refill carve trigger) → restructured the
  single `if not inp.project_paused and not carve_in_flight and not
  carve_dispatch_planned:` guard into an `if/elif` chain
  (`project_paused` → `carve-skip`/`"paused"`; `carve_in_flight` →
  `carve-skip`/`"in-flight"`; else the original body, now ALSO noting
  `guard-exclude`/`"decision-held"` inside the ready-count loop's existing
  `continue` branch, and `carve`/`"headroom"` on a successful dispatch).
  Verified the truth table is unchanged: entering the ready-count
  computation still requires all three of `not paused`, `not in_flight`,
  `not already_planned` — exactly as the original single `and`-chain
  required — only the *attribution* of why it didn't enter changed.
- **item 12** (READY_TO_CARVE re-carve handler) → `carve`,
  `"ready-to-carve"`, on a successful dispatch (no skip breadcrumb added
  here — item 9 already demonstrates `carve-skip` for both reasons, and
  item 12's own guard shares the same `carve_in_flight`/`project_paused`
  variables, so adding a second skip site would only duplicate the same
  two reasons under a different task attribution with no new signal).
- **item 13** (guarded-automatic merge) → `merge`, `"auto-merge"`, on a
  successful `AutoMergeTask` — completes the `TRACE_KINDS` vocabulary
  (`dispatch`, `dispatch-skip`, `carve`, `carve-skip`, `merge`,
  `guard-exclude`, `state-transition` — every kind named in §4.3 now has at
  least one live producer).

Deliberately NOT instrumented (to keep the diff minimal and because the
oracle's "representative cases" list didn't require it): the fresh-start
DispatchImplementer path inside the poisoned-INTERRUPTED handling (item 4's
P34 sub-branch), the wave/self-review launch paths (item 5 / B5), and the
test-health carve trigger (item 15) — these are structurally identical to
already-instrumented sibling paths (fresh-start mirrors the ordinary
dispatch loop's route selection; test-health mirrors item 9's guard) and
adding trace notes there would be repetitive without adding a new
*representative* case the oracles ask for.

## Coverage strategy (100% diff-coverage, no hollow additions)

Before writing any NEW test, grepped `tests/test_reconcile.py` for existing
tests that already exercise each branch getting a new `trace.note(...)`
line, on the theory that a `trace.note()` call added immediately after an
already-executed `action.append(...)` is executed by the SAME existing test
— confirmed for every site:
- `test_create_carved_to_queued` → the `state-transition`/CARVED→QUEUED line.
- `test_decision_hold_never_dispatched` → the `state-transition`/
  QUEUED→NEEDS_DECISION line (task starts QUEUED with an open D-dep).
- `test_decision_hold_needs_decision_with_resolved` (line ~270) → the
  NEEDS_DECISION→QUEUED line.
- `test_dispatch_order_three_tasks_max_two` / `test_dispatch_first_route_unhealthy`
  → the `dispatch` line; `test_dispatch_no_healthy_route` /
  `test_decision_hold_never_dispatched` → the `dispatch-skip` line.
- `test_carve_trigger_none_when_project_paused` → the `carve-skip`/`"paused"`
  line; `test_carve_trigger_none_when_carver_already_inflight` → the
  `carve-skip`/`"in-flight"` line.
- `test_carve_trigger_decision_held_task_not_counted_ready` → the
  `guard-exclude` line.
- `test_carve_trigger_fires_below_target_no_carver_inflight` → the
  `carve`/`"headroom"` line.
- `test_ready_to_carve_dispatches_carve_and_plans_no_supersede_transition`
  → the `carve`/`"ready-to-carve"` line.
- `test_auto_merge_fires_when_guarded_automatic_and_merge_ready` → the
  `merge` line.

So every new line in `reconcile.py` was ALREADY covered by pre-existing
tests before a single new test was written for this package — the new
tests added below are purely for the P03 *oracles* (asserting the trace
CONTENT, not just re-proving coverage), and happen to re-exercise the same
lines (harmless redundancy, not a coverage crutch).

For `daemon.py`: the new `if trace is not None: with bind(...): for note
in ...: log.debug(...)` block needs BOTH branches covered (a `PlanResult`
with breadcrumbs, and a bare `list` with none) — the 15 pre-existing
`monkeypatch.setattr(reconcile, "plan_project", lambda inp: [])`-style
tests in `test_daemon.py` already exercise the `trace is None` (skip) path;
a new dedicated test exercises the `trace is not None` (flush) path with a
constructed `PlanResult`+`ReconcileTrace`, plus one dedicated test for the
skip path in case coverage.py's branch-coverage mode is enabled (belt and
braces — confirmed both pass locally before the container gate run).

## Local pre-check (NOT the ship signal — cockpit venv, see CLAUDE.md)

Ran `PYTHONPATH=src python3 -m pytest tests -q` in the devcontainer's own
venv (structlog 26.1.0 happened to already be installed there) purely as a
fast iteration signal before paying for the container gate:
- `tests/test_reconcile.py` alone: all pass (previously ~130 tests, now
  ~144 after the 14 new P03 tests).
- `tests/test_daemon.py -k reconcile_trace`: both new tests pass.
- Full `tests -q`: all green, one pre-existing `x` (xfail, unrelated to
  this package — same position noted in P01/P02's own reports).
This is explicitly NOT the gate — see REPORT.md for the real
`tester-unified` container run, the only ship signal per policy.

## Commits

1. `<to be filled after `git commit`>` — implementation + tests + this LOG.
2. A follow-up commit adds `P03-REPORT.md` once the real gate has been run
   against commit (1), since the report needs to quote that commit's own
   gate output and hash (chicken-and-egg with committing REPORT.md as part
   of the same commit it describes) — same two-commit shape P02's own LOG
   used for its "diff-coverage gap closure" follow-up, and P01's report
   references a "docs LOG.md" written iteratively.
