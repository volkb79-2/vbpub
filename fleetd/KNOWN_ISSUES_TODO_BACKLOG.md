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
| FD-007 | Replace the control-plane design default with configurable measured-p95 reserve | P2 | OPEN | 2026-11-13 |
| FD-008 | Strict whole-lane result merge from an explicit candidate-owner manifest | P1 | OPEN | 2026-11-13 |
| FD-009 | Restricted host enrollment for the existing hx transport | P1 | OPEN | 2026-11-13 |
| FD-010 | Consume Assay's exact-key baseline reuse in campaign execution | P2 | OPEN | 2026-11-13 |
| FD-011 | Optional persistent campaign scheduler after the one-shot loop is proven | P2 | OPEN | 2026-11-13 |
| FD-012 | Export campaign progress for cgprofile guard policy and evidence | P2 | OPEN | 2026-11-13 |
| FD-013 | Serialize concurrent campaign state updates | P2 | OPEN | 2026-11-14 |

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

**State:** OPEN. **Priority:** P2. **Filed:** 2026-11-13.

WP2 currently uses the design default
`clamp(256 MiB + 6% × MemTotal, 768 MiB, 2 GiB)` and takes the larger of that
default or the reported `ctl.slice/memory.min`. The design later replaces the
default with `1.25 × measured p95`, still clamped. A later host-policy change
should make that measured value an explicit, host-owned configuration source
after a reviewed measurement period; fleetd must not infer it from live host
usage in this MVP.

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

**State:** OPEN. **Priority:** P2. **Filed:** 2026-11-14.

Each `fleet run`, `status`, `tick`, and `collect` call currently reads and
atomically replaces the same campaign JSON without a per-campaign lock. Two
overlapping controller invocations can therefore both make valid transitions
from the same prior file and let the last rename discard the other's records.
Named runner starts remain idempotent, so this is a state-accounting race.
Add an exclusive lock at `<campaign>.lock` around each command's full
read/modify/write interval. Keep locks local to the state directory and do not
add leases, host-side locks, or a daemon.
