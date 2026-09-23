# RG-55 P1 daemon adversarial review — round 1

REJECT

- Tip reviewed: `920231186fdfb81ea3d9e9adb2d0b57ed11ab6fd`
- Comparison: tip against `main`
- Reviewer mode: fresh blind-first review, Luna xhigh only
- Review scope: the exact handoff, then the blind source pass, then the full
  diff and the P1 LOG/REPORT/briefs; no product or gate-source edits

## Blockers

### B1 — DAMON can take ownership of, configure, and stop a foreign kdamond

Evidence: `scripts/cgroup-profiler/lib/damon.py:253-259` records the current
`nr_kdamonds` only as a baseline, but chooses an index from `_live` starting at
zero. With a pre-existing kdamond at index 0, `_live` is empty and
`create_kdamond(0)` is a no-op; `DamonSession.__enter__`
(`scripts/cgroup-profiler/lib/damon.py:381-411`) then configures and turns on
that pre-existing slot. Teardown unconditionally calls `kdamond_off` at
`scripts/cgroup-profiler/lib/damon.py:507-521`, and pool release can also
shrink the global counter at `scripts/cgroup-profiler/lib/damon.py:262-278`.
The existing green test at `scripts/cgroup-profiler/tests/test_damon.py:423-434`
even expects a pre-existing slot to end `off`, which asserts the unsafe result
instead of foreign-state preservation.

Prescription: allocate only indices created by this pool after its baseline,
track ownership separately from liveness, and make every configure/on/off,
release, and counter restore conditional on proven ownership. Preserve all
foreign slots and foreign growth. Replace the test's final `off` assertion
with `on` (and retain a write-call assertion) and add a failed-acquire case
that proves no placeholder index is stopped.

### B2 — `stop` points at `damon.jsonl` when no DAMON series exists

Evidence: the contract permits `series.damon` to be a path or `null`
(`run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md:138-146`). The
daemon only appends that file when a DAMON sample exists at
`scripts/cgroup-profiler/lib/serve.py:687-730`; an off or unavailable session
therefore has no DAMON series. Nevertheless, `_stop_response` always returns
`"damon": "damon.jsonl"` at `scripts/cgroup-profiler/lib/serve.py:928-943`.
The direct read-only probe of `_stop_response('s1', None, already_stopped=False)`
returned that path.

Prescription: derive the value from the session's persisted DAMON state and
whether at least one DAMON record was written; return `null` when the file is
absent, including stored/restarted sessions, and add stop tests for off,
unavailable, on-with-no-record, and on-with-record cases.

### B3 — no-token starts are incorrectly deduplicated

Evidence: `scripts/cgroup-profiler/lib/serve.py:452-476` indexes every start
by `(container_id, token)`, including `token is None`. A second no-token start
for the same container consequently returns `reused: true`, although the
contract limits idempotency to the same non-null token
(`run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md:91-98`) and the
no-token mode is the all-pids scope. This prevents independent concurrent
no-token sessions and makes the registry key encode a policy the contract did
not grant.

Prescription: perform the reuse lookup and registry insertion only for a
non-null token; each no-token start must receive its own session, subject to
`max_sessions`. Add a two-start/no-token and concurrent no-token test.

### B4 — the required start sample is not recorded as sample 0

Evidence: the contract requires sample 0 at `start`
(`run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md:328-332`).
`_create_session_locked` reads a baseline and host snapshot at
`scripts/cgroup-profiler/lib/serve.py:491-500`, but constructs an empty
accumulator at `scripts/cgroup-profiler/lib/serve.py:547-556` and starts the
sampler later. The first accumulator row is only made by
`_on_session_sample` at `scripts/cgroup-profiler/lib/serve.py:692-698`; the
stop fallback at `scripts/cgroup-profiler/lib/serve.py:900-917` is a late
sample, not the start sample. Thus shared deltas, percentile population,
sample count, and baseline-relative values depend on scheduler timing and do
not implement the specified s_0..s_n sequence.

Prescription: capture the already-read target, host, slice, and PID data as an
atomic accumulator/persisted sample 0 before starting the sampling thread,
then make the stop path preserve that invariant and test a start followed by
an immediate stop.

### B5 — `cores_max` does not use the required per-pair elapsed time

Evidence: the contract defines each rate as `(delta usage) / delta t`
(`run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md:335`). The
accumulator loops over pairs but divides every pair by the configured interval
at `scripts/cgroup-profiler/lib/summary.py:417-423`; no sample timestamp is
retained by `SummaryAccumulator.add_sample` (`scripts/cgroup-profiler/lib/summary.py:292-313`).
The sampler records actual monotonic times, but they are not passed into this
calculation. A read-only accumulator probe with a two-second configured
interval returned `cores_max=2.0` for successive 4,000,000 and 1,000,000 usec
increments, demonstrating the fixed-interval implementation rather than a
per-pair `delta t` calculation.

Prescription: retain/pass each sample's monotonic timestamp and calculate
each consecutive rate from its actual positive delta; return `null` for an
unreadable or non-positive time delta, and add a deliberately jittered-time
golden test.

### B6 — malformed interval input is reported as a daemon fault, not bad input

Evidence: `scripts/cgroup-profiler/lib/serve.py:449-450` calls `float()`
without translating `ValueError`/`TypeError` into `RequestError`. The socket
handler catches that as an unanticipated exception at
`scripts/cgroup-profiler/lib/serve.py:1112-1138`, logs it, and closes the
connection. The contract classifies malformed start arguments as exit-2
`bad-argument`, not exit-3 daemon fault (`RG-55-INTERFACE-CONTRACT.md:1.3`
and `:95-98`).

Prescription: validate interval type, finiteness, and range in the start
handler and return the normal one-line contract error for malformed input;
test non-numeric, NaN, infinity, and out-of-range requests through the real
socket path.

### B7 — the CLI accepts an incompatible or malformed successful response

Evidence: `scripts/cgroup-profiler/cgprofile.py:864-878` prints any decoded
JSON object and returns success solely from `resp.get("ok")`. It does not
require a mapping, `contract == 1`, or the verb-specific response shape. A
daemon returning `{"ok": true, "contract": 999}` would therefore produce
exit 0 instead of the contract-mismatch daemon-fault result required by the
ctl contract.

Prescription: validate the response is an object with the expected major
contract before printing/accepting it; classify malformed or incompatible
responses as daemon faults without a traceback, and test wrong-major, list,
and missing-`ok` replies over a fake Unix socket plus one live acceptance
probe.

### B8 — the shipped user-facing capability has no required consumer guide

Evidence: this package adds the daemon/`ctl` capability and public metadata
and stack configuration, but `scripts/cgroup-profiler/docs/CONSUMERS.md` is
absent. The estate contract makes README, DESIGN-GUIDE, and CONSUMERS.md a
single completion obligation for user-facing capabilities, including
pasteable examples, current schema declarations, vocabulary coverage, and
resolving anchors. The handoff's C9 list does not discharge that higher-level
requirement.

Prescription: add `scripts/cgroup-profiler/docs/CONSUMERS.md` with pasteable
current-schema daemon and run-gate adoption examples, then add/verify the
README link and cross-document/config-example checks before accepting P1.

## Non-blocking findings (S-items)

### S1 — meta schema is only type-checked

`scripts/cgroup-profiler/lib/serve.py:428-448` checks that `meta` is a dict
but does not validate the contract's `lane`, `project`, `worktree`, `commit`,
`run_gate_revision`, `kind`, and `expected` shapes from
`RG-55-INTERFACE-CONTRACT.md:81`. Unknown keys should remain accepted, but
known-key type/vocabulary validation and tests are still needed.

### S2 — mixed task-file support loses descendants

`scripts/cgroup-profiler/lib/subtree.py:200-217` selects the task fast path
if any owner supports it, then `_walk_via_task` treats an unsupported or
vanished owner as having no children at `:219-231`. Fall back per owner, or
use the ppid map for unsupported owners, with a mixed-owner fixture.

### S3 — DAMON is permanently unavailable when a requested token has no PID at start

`scripts/cgroup-profiler/lib/serve.py:522-545` records
`unavailable:no pids to monitor yet` and never retries DAMON acquisition when
that token later appears. The start itself correctly remains non-failing, but
the intended later-monitoring behavior and status/report semantics need an
explicit controller decision and test.

### S4 — stale DAMON targets and PID reuse are not proven safe

`scripts/cgroup-profiler/lib/damon.py:453-485` refuses to shrink target slots.
After a process exits, an old numeric PID slot remains; the code has no
`/proc` start-time identity check. Add a PID-reuse/reparent-to-PID1 fixture and
document the chosen safety behavior before treating subtree churn as covered.

### S5 — deferred runtime artifacts remain unverified for this review

The P1 records explicitly defer real event emission, DAMON report-series
integration, and effective manifest limits (CP-5/CP-6/CP-7). They may be
valid scope decisions, but the controller should keep the deferred behavior
visible and not describe those artifacts as complete P1 output.

## Claims not independently verified

- The supplied P1 report claims final r0/r1 and live acceptance results from
  earlier commits/images; this review did not reproduce them at tip because
  the live safety gate was red. The current `cgprofile:local` image was stale
  and carried an older revision label (`241122b65543b4a59966f72245ba798ecbd029d6`).
- The handoff names controller rulings RW-47 and RW-48, but the supplied
  controller log contains RW-1 through RW-23 only. Their authority/application
  cannot be independently verified from the required record.
- `.assay/verdict-r2.json` records `assay_version: "0+unknown"` and no judge
  provenance. It does record 208 candidates, 203 killed, five survivors, and
  `FAIL/MUTANTS_SURVIVED` for commit `53abbf2`; the five dispositions therefore
  remain human claims rather than an independently version-proven assay
  result under the handoff's judge-version requirement.

## Tests and live evidence

- Passed, serial low priority: `nice -n 19 ionice -c 3 python3 -m pytest -q
  scripts/cgroup-profiler/tests/test_summary.py
  scripts/cgroup-profiler/tests/test_subtree.py
  scripts/cgroup-profiler/tests/test_damon.py` — **120 passed**.
- Passed, serial low priority: `nice -n 19 ionice -c 3 python3 -m pytest -q
  scripts/cgroup-profiler/tests/test_serve.py` — **94 passed, 1 skipped**.
- Blocked at collection: the combined serve/cgprofile focused run could not
  import `numpy` from `scripts/cgroup-profiler/tests/test_cgprofile.py`
  (`ModuleNotFoundError: No module named 'numpy'`). This is an environment
  finding, not a pass claim.
- Live probes were deliberately not started. At the final safety check,
  `/proc/pressure/memory` reported `some.avg10=54.60` and `full.avg10=37.72`,
  CPU reported `some.avg10=22.98`, and three `tester-unified:local` containers
  were already running. This exceeds the handoff's PSI <=5 condition and
  violates its <=2 gate-container condition. Starting/building the daemon
  would have added unsafe host load. No `ciu up` was run, so no `ciu down` was
  required by this review.
- The worktree was clean at review start and remained product-source clean;
  the only permitted write from this review is this round record.

## Blind-first attack surface covered

The blind pass covered DAMON ownership and teardown, contract/error paths,
sample timing and arithmetic/null behavior, subtree churn/PID identity,
restart/retention/report persistence, image/ciu/cmru safety, and the P1 test
and documentation obligations. The full P1 records and diff were consulted
only after that pass.
