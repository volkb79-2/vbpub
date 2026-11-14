# fleetd known issues and TODO backlog

## Summary

| ID | Item | Priority | State | Filed |
|---|---|---|---|---|
| FD-001 | Lite campaign dispatch, restart recovery, progress supervision, and candidate-result collection | P1 | DONE | 2026-11-13 |
| FD-002 | Standalone lane demand does not derive Assay mutation.jobs_max | P1 | OPEN | 2026-11-13 |
| FD-003 | Manual Run-Gate commands cannot pass fleetd's per-host RAM budget to Assay | P1 | OPEN | 2026-11-13 |
| FD-004 | Host facts and planning do not account for the dev-gates slice memory ceiling | P1 | OPEN | 2026-11-13 |
| FD-005 | Learn one bounded f_h per host from independent timing samples | P2 | OPEN | 2026-11-13 |
| FD-006 | Lift the one-slot-per-host and J worker cap in the campaign supervisor | P2 | OPEN | 2026-11-13 |
| FD-007 | Replace the RAM-proportional control-plane reserve (operator-rejected) with the sum of reported MemoryMin or a fixed profile constant | P1 | OPEN | 2026-11-13 |
| FD-008 | Strict whole-lane result merge from an explicit candidate-owner manifest | P1 | OPEN | 2026-11-13 |
| FD-009 | Restricted host enrollment for the existing hx transport | P1 | OPEN | 2026-11-13 |
| FD-010 | Consume Assay's exact-key baseline reuse in campaign execution | P2 | OPEN | 2026-11-13 |
| FD-011 | Optional persistent campaign scheduler after the one-shot loop is proven | P2 | OPEN | 2026-11-13 |
| FD-012 | Export campaign progress for cgprofile guard policy and evidence | P2 | OPEN | 2026-11-13 |
| FD-013 | Serialize concurrent campaign state updates | P2 | DONE | 2026-11-14 |
| FD-014 | Probe command grows with the number of batches and hits the 128 KiB argument limit near 100 batches per host | P2 | OPEN | 2026-11-14 |
| FD-015 | Cold-start stall threshold ignores the predicted candidate time, so slow lanes stall and re-queue without end | P1 | DONE | 2026-11-14 |
| FD-016 | A batch that exits without progress is re-queued on every exit, without limit | P1 | DONE | 2026-11-14 |
| FD-017 | One candidate that does not fit the re-queue target drops every other pending candidate of its batch | P2 | OPEN | 2026-11-14 |
| FD-018 | Collect counts a verdict's never-started budget_exceeded candidates as timeouts and hides missing work | P1 | DONE | 2026-11-14 |
| FD-019 | Stale records in the append-only progress file count as finished for a later campaign on the same commit | P2 | DONE | 2026-11-14 |
| FD-020 | Host loss is measured from the last contact, and a slow probe counts as unreachable | P2 | OPEN | 2026-11-14 |
| FD-021 | A transport or contract error on one host aborts the scheduling step for every host | P2 | DONE | 2026-11-14 |
| FD-022 | Dispatch does not re-check memory/IO PSI before starting the next batch | P2 | OPEN | 2026-11-14 |
| FD-023 | The hx wrapper path is derived from the source checkout and fails from an installed wheel | P3 | OPEN | 2026-11-14 |
| FD-024 | Deferred re-queue de-duplication is not pinned for three or more hosts | P3 | DONE | 2026-11-14 |
| FD-025 | The first stall window uses unscaled reference seconds when no comparable host speed reference exists | P3 | OPEN | 2026-11-14 |
| FD-026 | A host that stays in ERROR has no loss timer | P3 | OPEN | 2026-11-14 |

## FD-001 Lite campaign lifecycle

**State:** DONE in MVP-B-lite. **Priority:** P1. **Filed:** 2026-11-13.

MVP-B-lite adds one-shot dispatch through the existing identity-checked hx
transport, deterministic named runner jobs, local restartable state, progress
and ETA reporting, stall/loss detection, planner-based re-queues, and a
one-owner combined candidate result. `fleet run` starts work and returns;
`status`, `tick`, and `collect` are explicit calls. The operator can repeat
`run` after restart to re-adopt jobs by name. There is no daemon, socket,
lease, or wall-clock campaign budget. Strict whole-lane Assay store merging
remains FD-008; this item does not claim it.

## FD-002 Demand upper bound is not joined to Assay inventory

**State:** OPEN. **Priority:** P1. **Filed:** 2026-11-13.

WP2 accepts `[lanes.<lane>.demand].efficient_upper_bound` from a standalone
TOML file because Run-Gate 25.6.0 does not accept the `demand` key in
`run-gate.toml`. The same value must be no greater than the lane's authoritative
Assay `judge.mutation.jobs_max`; the current `fleet plan` CLI cannot derive that
inventory and therefore cannot reject a stale or oversized declaration. Until
the Run-Gate campaign client owns demand parsing, operators must keep these
values aligned. The follow-on client must derive the upper bound from Assay or
refuse a mismatch before rendering an executable command.

## FD-003 Fleet RAM budget is not sent to the current runner

**State:** OPEN. **Priority:** P1. **Filed:** 2026-11-13.

`fleet plan` computes `B_h` and proves each planned wave against it, but the
current Run-Gate `--only-candidates` interface has no per-invocation
host-specific Assay memory-budget argument. The printed command supplies the
planned worker count only; it does not enforce `B_h`. Runtime admission remains
owned by the host's existing Run-Gate, Assay lane, and cgroup configuration.
The Run-Gate campaign client must carry the selected host budget into Assay's
memory admission (or establish an equivalent enforced bound) before treating
the plan's wave proof as an execution guarantee.

## FD-004 Host slice memory ceiling is not part of the planner facts

**State:** OPEN. **Priority:** P1. **Filed:** 2026-11-13.

WP1(a) reports physical lane memory and control-plane `memory.min`, while WP2
computes `B_h = P_h × overcommit − O_h` as specified in the compute-host
design. The facts do not include the host-owned `dev-gates.slice` `memory.max`
ceiling, so the planner cannot report when its estimate exceeds that execution
limit. The design lists slice `MemoryMax` as a downstream safety net; this MVP
keeps the specified physical-RAM budget and does not reinterpret the cgroup
ceiling as available RAM. A follow-up should collect the ceiling with a reason
when unavailable and derive an effective limit that also accounts for current
slice use and other running jobs before treating planned capacity as enforceable.

## FD-005 Learn the per-host correction factor

**State:** OPEN. **Priority:** P2. **Filed:** 2026-11-13.

WP2 accepts one stored `f_h` per host, defaults it to 1.0, and validates it in
`[0.5, 2.0]`; this package deliberately does not learn it. WP5 should implement
the design's EWMA of actual/predicted time from finished candidates only when
their normalized reference came from another host or from the declared value.
Skip a sample normalized from that host's own prior measurement, reset on CPU
model change or re-enrollment, and never fit by lane or hardware flavor.

## FD-006 Lift the manual one-slot-per-host and J cap

**State:** OPEN. **Priority:** P2. **Filed:** 2026-11-13.

The current manual planner uses one campaign slot per host because concurrent
same-lane selected runs share Run-Gate's in-flight identity and Assay store,
and it caps `W_h` at the lane's `efficient_upper_bound` J. On a four-core host
with oversubscribe 1.25 and J = 3, that leaves two of five CPU-derived worker
slots unused. This is an accepted safety deviation for the no-supervisor MVP.
WP4/WP5 should give selected runs distinct identities and schedule enough
independent instances to use the remaining safe capacity without sharing state.

## FD-007 Calibrate the control-plane reserve from measured p95

**State:** OPEN. **Priority:** P1 (raised 2026-11-14). **Filed:** 2026-11-13.

WP2 currently uses the design default
`clamp(256 MiB + 6% × MemTotal, 768 MiB, 2 GiB)` and takes the larger of that
default or the reported `ctl.slice/memory.min`.

**Operator decision 2026-11-14: this rule is rejected.** The control plane does
not grow with installed RAM; it depends on the services that run before fleet
work starts. The replacement (design rev 6, not yet written): host install
(debian-install-v2 drop-ins) or a fleetd host profile sets `MemoryMin` per
control-plane service (sshd, dockerd, containerd, journald, cgprofiled,
psi-guard); fleetd's reserve becomes the sum of the reported `MemoryMin` facts,
else a fixed profile constant. There is no RAM-proportional term and no clamp
around one. Measured hot RSS on the two 8 GiB hosts (2026-11-14, read-only) is
about 450-500 MB. The earlier "1.25 × measured p95, still clamped" direction
is superseded.

Every place that applies the rejected rule (all must change together; at
`f931ed67b` and this review's head):

- `src/fleetd/plan.py` constants `_CONTROL_PLANE_MIN_*` and
  `_control_plane_reserve()`;
- `_refusal()` (reserve subtracted for the "no physical RAM" refusal) and
  `_capacity()` (`P = for_lanes - reserve`, hence `B`, `cap`, `cap_alone`,
  and the reported `reserve_bytes`/`reserve_source`);
- downstream through the plan: `cap_alone_bytes` drives the campaign's
  re-queue target (`campaign.py` `_largest_available_host`), and
  `cap_bytes`/`budget_bytes` drive `replan_pending()` placement;
- facts: `facts.py` reads `ctl.slice/memory.min` as one value; the sum of
  per-service `MemoryMin` needs a new fact;
- tests: `test_plan.py` reserve clamp cases, the seeded sweep's recomputed
  reserve, and the 768 MiB assertions on the golden 8 GiB hosts;
- docs: SPEC (facts collection and planning formulas), CONSUMERS (planning
  inputs), DESIGN-GUIDE (planning invariants), CHANGES; design doc §3.6 and
  §7.2 Q3.

On the two 8 GiB hosts the default is the 768 MiB floor (256 MiB + 6% of
8 GiB is about 747 MiB), above the measured 450-500 MB, so the current plans
are conservative, not unsafe.

## FD-008 Strict whole-lane result merge

**State:** OPEN. **Priority:** P1. **Filed:** 2026-11-13.

MVP-B-lite collects candidate verdict and progress files into one
`fleet-campaign-result/1`; it does not merge the same-judge Assay state stores
or produce a Run-Gate whole-lane verdict. The collector currently elects one
candidate owner by earliest finished record and counts later duplicates.

The follow-up must create an explicit `assay-merge-manifest/1` that binds each
planned candidate to exactly one owner store. Assay validates judge identity,
refuses missing or conflicting owner records, ignores and counts non-owner
duplicates, and merges only same-judge final records. Run-Gate forwards the
manifest without interpreting it. Dependencies: Assay B544 and Run-Gate
RG-277.

## FD-009 Restricted host enrollment

**State:** OPEN. **Priority:** P1. **Filed:** 2026-11-13.

MVP-B-lite uses the existing identity-checked hx wrapper and its local host
aliases. It does not create a fleet-specific restricted account or
forced-command entry. CIU should add an opt-in enrollment mode that installs a
forced command for the existing runner protocol, restricts the accepted
operations, and refuses general shell access. Fleetd must use that enrollment
contract without storing keys, addresses, or aliases in the repository.
Owner: CIU-218.

## FD-010 Exact-key baseline reuse

**State:** OPEN. **Priority:** P2. **Filed:** 2026-11-13.

Campaign start currently invokes the existing runner for each named batch and
does not reuse host-local baseline facts itself. Integrate Assay's exact-key
baseline cache when it is available, preserving the cheap environment smoke
and refusing reuse when judge identity, commit, image, resources, or collection
changes. Assay's existing B542 is the source contract; do not create a second
cache or weaken the baseline PASS requirement.

## FD-011 Optional persistent campaign scheduler

**State:** OPEN. **Priority:** P2. **Filed:** 2026-11-13.

The supported controller interface is deliberately one-shot: `run` starts
available jobs and returns, while callers invoke `status` and `tick` as needed.
Only consider a persistent scheduler after the named-job and restart-recovery
contracts have operational evidence. Preserve idempotent names, no leases,
progress-based stall detection, pause on unreachable hosts, and requeue after
30 minutes; do not introduce a campaign wall budget. Any daemon/socket process
model needs a separately reviewed lifecycle and local-state contract.

## FD-012 Campaign progress for guard policy

**State:** OPEN. **Priority:** P2. **Filed:** 2026-11-13.

MVP-B-lite reports progress but does not connect campaign ownership or
progress to cgprofile's report/enforce guard. Define a host-local, read-only
progress input that lets the guard rank useful lane work by progress per RAM,
then specify evidence and refusal behavior before enabling enforcement.
Fleetd owns stable campaign progress and ownership facts; cgprofile owns the
guard decision and any cgroup action. Track the cgprofile side in CP-64. The
lite controller itself must remain report-only with respect to guard rules.

## FD-013 Serialize concurrent campaign state updates

**State:** DONE (Opus series review, 2026-11-14): each command holds a
non-blocking `fcntl.flock` on `<campaign>.lock`; an overlapping command fails
with "busy" and changes nothing. **Priority:** P2. **Filed:** 2026-11-14.

Each `fleet run`, `status`, `tick`, and `collect` call currently reads and
atomically replaces the same campaign JSON without a per-campaign lock. Two
overlapping controller invocations can therefore both make valid transitions
from the same prior file and let the last rename discard the other's records.
Named runner starts remain idempotent, so this is a state-accounting race.
Add an exclusive lock at `<campaign>.lock` around each command's full
read/modify/write interval. Keep locks local to the state directory and do not
add leases, host-side locks, or a daemon.

## FD-014 Probe size is bounded by batches per host

**State:** OPEN. **Priority:** P2. **Filed:** 2026-11-14.

`probe_script` carries one JSON target per job (name, ID-file path, commit,
project, lane) twice: once as the base64 payload argument and once in the
leading metadata comment that only the fake transport reads. The whole script
is one remote shell argument, capped at 128 KiB (`MAX_ARG_STRLEN`), so a host
with more than about 100 batches in one campaign (all non-superseded jobs are
probed, finished ones included) fails every probe with `Argument list too long`.
The 230-candidate golden plan uses 18 batches on its larger host. Fix by
uploading the targets with `put` and probing only non-terminal jobs, or by
deriving paths on the host from the job name; drop the duplicate comment from
the real script. Until then keep campaigns under about 100 batches per host.

## FD-015 Cold-start stall threshold ignores the predicted candidate time

**State:** DONE (2026-11-14). **Priority:** P1. **Filed:** 2026-11-14 (Opus series review).

Design §3.9: `T_stall = max(15 min, 3 × p95 predicted candidate time on that
host)`. `campaign.py` `_stall_threshold()` uses only *observed* elapsed times
on the host. Until the first candidate on a host finishes, the threshold is
the 15-minute floor, measured from the job start, and that window also has to
cover the batch's Assay baseline (no baseline reuse yet, FD-010). A replacement
batch starts the baseline again. So a lane whose baseline plus first candidate
takes more than 15 minutes never records an observation and is stopped and
re-queued without end. Known per-candidate times: Assay self-qualification
about 505-560 s, pwmcp per-candidate cap 900 s.

Fix: compute the predicted host time of each candidate already in the plan
(`time_ref_seconds × baseline_score / host single_core × f_h`, or the
reference time when no Geekbench comparison exists) and use
`max(900, 3 × p95(predicted for the job's candidates), 3 × p95(observed on
that host))`. Add the declared or observed baseline time to the first window.
Only the host's own values are used.

**Fix/evidence:** Use each batch's candidate predictions and host-local
observations, adding the declared or same-host observed baseline in the first
window. `test_first_stall_window_uses_batch_prediction_and_declared_baseline`,
`test_first_stall_window_uses_baseline_observed_on_the_same_host`, and
`test_stall_threshold_never_pools_observations_across_hosts` (commit
`9197fbce0`).

## FD-016 Zero-progress exits are re-queued without limit

**State:** DONE (2026-11-14). **Priority:** P1. **Filed:** 2026-11-14 (Opus series review).

`_requeue_exited()` re-plans the unfinished candidates of every `EXIT` batch
on the same host, every time. A batch that fails before any candidate
finishes therefore gets a new batch number and a new runner job on each
`tick`, forever, for example after a lane configuration error, missing
tester image, or a candidate table built from another commit (Assay refuses
`--only-candidates` IDs absent from its plan). `run-r2-campaign.sh` also
ends with exit 4 after two attempts without progress, which takes the same
path. The design's "no max-attempt cap" applies to the relaunch loop while
candidates advance, not to batches that made no progress.

Fix: when an exited batch (`EXIT`, `missing_job`, or `STALLED`) added no
finished candidate since it started, mark it `FAILED`, do not re-queue it,
and surface its failure reason and exit code when available in `status` and
`collect`. The operator re-runs after fixing the cause. Re-queue stays
unlimited for batches that made progress.

**Fix/evidence:** Zero-progress exits, missing jobs, and stalls become visible
`FAILED` batches and are not re-queued; progress-making batches still re-plan
pending candidates. `test_failed_zero_progress_batch_is_not_requeued_across_ten_ticks`,
`test_stall_floor_and_unreachable_pause_are_progress_based`, and
`test_refresh_digest_mismatch_and_missing_running_jobs_are_detected` (commit
`9197fbce0`).

## FD-017 Re-plan of a lost batch is all-or-nothing

**State:** OPEN. **Priority:** P2. **Filed:** 2026-11-14 (Opus series review).

`_enqueue_replanned()` calls `replan_pending()` with the whole pending list of
one batch. If one candidate does not fit the target host (larger than its
`cap_alone` or sum budget), `replan_pending()` raises, the warning is
recorded, and every other pending candidate of that batch is neither
re-queued nor deferred. Reproduced: a 9 GiB candidate planned on a larger host,
that host lost, 7 placeable candidates of the same batch dropped. The two
current 8 GiB hosts are affected only when their other running charges
differ.

Fix: let `replan_pending()` return placed batches plus the unplaceable IDs;
re-queue the placed ones and keep the unplaceable ones deferred until a host
with enough `cap_alone` is online (or report them missing with the reason).

## FD-018 Verdict fallback hides never-started candidates

**State:** DONE (2026-11-14). **Priority:** P1. **Filed:** 2026-11-14 (Opus series review).

Assay lists every candidate of a selected run that did not start (unselected,
or the deadline remainder) under `budget_exceeded` with not-started evidence
(`assay/src/assay/mutation.py` `_append_unselected`, deadline mask). Progress
`candidate` events exist only for candidates that ran. `_collect_result()`
adds a verdict record for every batch candidate without a progress event on
that host, so a candidate that never ran anywhere is reported as `timeout`
instead of `missing`. Finished campaigns are unaffected (a real progress
record owns the candidate); an incomplete campaign reports `missing` too low,
and an operator can take it as complete.

Fix: skip verdict `budget_exceeded` entries whose process-tree evidence says
not started, or use the verdict only when the batch has no progress artifact
at all.

**Fix/evidence:** Verdict entries with `process_tree_evidence.leader_status`
`not-started` remain missing with a distinct reason and do not increase the
timeout total. `test_collect_reports_assay_not_started_candidates_as_missing`
(commit `9197fbce0`).

## FD-019 Stale progress records from an earlier campaign

**State:** DONE (2026-11-14). **Priority:** P2. **Filed:** 2026-11-14 (Opus series review).

Assay opens `progress-<lane>.jsonl` with `O_APPEND` and never truncates it.
The probe filters records by candidate ID only, so records left by an earlier
campaign on the same commit, project, and lane count as finished for a new
batch with the same IDs, and "earliest record wins" gives the old record
ownership. This matches Assay's own resume when the store reused those
results, but not when Assay re-judges them (judge, image, or resource
change).

Fix shape: a plain byte-offset or `emitted_at >= job start` filter is not
enough. Candidates that Assay resumes from its store emit no new event, so the
batch would look like it made no progress and loop (FD-016). Record the
progress file size at launch and count only newer events, and count resumed
candidates from the batch verdict's resumed set (or a per-candidate Assay
"resumed" event).

**Fix/evidence:** Each runner job records start/end byte offsets; progress is
filtered by that range and the job's candidate IDs. Resumed results come from
that job's verdict. `test_prior_campaign_progress_does_not_count_for_a_new_batch_on_same_commit`
and `test_real_run_job_starts_non_root_project_and_recovers_digest_from_ids_file`
(commits `9197fbce0`, `c4b8a1e23`, `614a913c2`). The real runner resolves
the declared Assay artifact lane before recording the job's start offset.

## FD-020 Host loss timing and slow probes

**State:** OPEN. **Priority:** P2. **Filed:** 2026-11-14 (Opus series review).

The loss clock starts at the host's last successful contact, not at the first
failed probe. With manual or sparse `tick` calls (more than 30 minutes apart),
one transient failed probe marks the host `LOST` immediately, and all its
queued and running batches move to the other host. Reproduced with 31-minute
ticks. The transport timeout (30 s) also maps a slow probe (rc 124) to
"unreachable". The probe re-reads the shared progress file once per job, so a
loaded host with a large progress file can drift toward that limit.

Fix: start the loss clock at the first failed contact (or at
`max(last_contact, previous tick)`), and treat a timeout as "slow" separately
from ssh exit 255. Until then, call `tick` every few minutes (SPEC, README).

## FD-021 One host's error aborts the scheduling step

**State:** DONE (2026-11-14). **Priority:** P2. **Filed:** 2026-11-14 (Opus series review).

`_refresh()` catches only `HostUnavailable`. A `TransportError` (hx exit 97/98,
a failing probe, a stop refused during the start race) or a `ContractError`
(digest mismatch) on any one host ends the whole `tick`, so no host is
refreshed, re-queued, or started. Failing closed for identity and guard
refusals is intended (it must not become a loss signal), but the other hosts
should still be supervised.

Fix: record the error per host (status `ERROR` with the message), skip that
host for the step, and continue with the others; still never treat 97/98 as
loss.

**Fix/evidence:** Catch transport and contract errors at host scope, persist
the message as host `ERROR`, and continue refreshing the other hosts.
`test_one_host_probe_error_is_reported_without_stopping_other_hosts` (commit
`9197fbce0`).

## FD-022 Dispatch does not re-check PSI

**State:** OPEN. **Priority:** P2. **Filed:** 2026-11-14 (Opus series review).

Design §4.3 says fleetd starts nothing new on a host whose memory or IO PSI
`some avg10` exceeds 25. `fleet plan` applies that once, from the facts
snapshot; `_start_available()` starts the next batch without fresh PSI. Over a
multi-hour campaign, admission therefore rests on the plan-time value and the
host guard.

Fix: have the probe also return `/proc/pressure/{memory,io}` `some avg10`
and defer starts on that host above the plan's `psi_refuse_some_avg10`
(no stop, only no new start).

## FD-023 hx wrapper path only valid in a source checkout

**State:** OPEN. **Priority:** P3. **Filed:** 2026-11-14 (Opus series review).

`HxTransport()` defaults to `Path(__file__).resolve().parents[3] /
"scripts/controller/hx.sh"`, which exists only when fleetd runs from the
repository (`PYTHONPATH=fleetd/src`). From an installed wheel the path is
wrong, every probe raises "could not launch hx transport", and the command
fails closed. Add a `--hx` option or a `FLEETD_HX` setting before fleetd is
installed as a wheel.

## FD-024 Deferred re-queue de-duplication with three or more hosts

**State:** DONE (2026-11-14). **Priority:** P3. **Filed:** 2026-11-14 (Opus series review).

The in-flight filter in `_retry_deferred()` excludes the source host by design
(its work is the work being moved). With three or more hosts it also prevents
re-queuing candidates already in flight on a third host. The existing code
implemented the filter, but the suite did not pin this cross-host case.

**Fix/evidence:** Add `test_deferred_retry_skips_work_already_running_on_a_third_host`
to assert a deferred candidate already running on a third host is not enqueued
again. Removing the in-flight filter is killed by that test (commit
`bee29cf0c`).

## FD-025 First stall window with a non-comparable speed reference

**State:** OPEN. **Priority:** P3. **Filed:** 2026-11-14 (fix4 review, for the Opus final review).

When no comparable host speed reference (Geekbench) exists, the FD-015 predicted
per-candidate time uses the plan's reference seconds unscaled. A host much slower
than the reference then gets a lower first-window stall threshold than it needs,
and a healthy first batch can be stopped before its first candidate finishes.
The observed per-host p95 corrects this only after the first candidate.

**Wanted.** Without a comparable reference, widen the first window by a documented
factor, or record the first observed candidate time per host before the first stall
decision. Test: a host measured 3x slower than the reference is not stalled in its
first window.

## FD-026 Host in ERROR has no loss timer

**State:** OPEN. **Priority:** P3. **Filed:** 2026-11-14 (fix4 review, for the Opus final review).

FD-021 records a transport or contract error on one host as ERROR and keeps
supervising the others. The fix4 review found that a host that stays in ERROR has
no loss timer, so it is not declared LOST and its running batches are not re-queued
elsewhere. (The exact code path is not yet traced; the first step of the fix is to
confirm it.)

**Wanted.** Start a loss timer when a host enters ERROR and declare it LOST after
the same threshold as an unreachable host; test that a host in ERROR beyond the
threshold has its batches re-queued and a recovery before it keeps them.
