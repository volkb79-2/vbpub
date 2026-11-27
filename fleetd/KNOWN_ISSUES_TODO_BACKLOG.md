# fleetd known issues and TODO backlog

## Summary

| ID | Item | Priority | State | Filed |
|---|---|---|---|---|
| FD-001 | Lite campaign dispatch, restart recovery, progress supervision, and candidate-result collection | P1 | DONE | 2026-11-13 |
| FD-002 | Standalone lane demand does not derive Assay mutation.jobs_max | P1 | OPEN | 2026-11-13 |
| FD-003 | Manual Run-Gate commands cannot pass fleetd's per-host RAM budget to Assay | P1 | OPEN (solved at host admission, FD-042) | 2026-11-13 |
| FD-004 | Host facts and planning do not account for the dev-gates slice memory ceiling | P1 | OPEN (solved at host admission, FD-042) | 2026-11-13 |
| FD-005 | Learn one bounded f_h per host from independent timing samples | P2 | PARKED 2026-11-24 (until the live campaign) | 2026-11-13 |
| FD-006 | Lift the one-slot-per-host and J worker cap in the campaign supervisor | P2 | PARKED 2026-11-24 (until the live campaign) | 2026-11-13 |
| FD-007 | Replace the RAM-proportional control-plane reserve with service MemoryMin facts or a fixed profile constant | P1 | DONE | 2026-11-13 |
| FD-007b | Calibrate the fixed profile reserve from measured p95 under load | P2 | OPEN | 2026-11-15 |
| FD-007c | Per-service reserve fallback with provenance for optional and missing units | P1 | DONE | 2026-11-15 |
| FD-008 | Strict whole-lane result merge from an explicit candidate-owner manifest | P1 | MOVED to assay B565 strict merge | 2026-11-13 |
| FD-009 | Restricted host enrollment for the existing hx transport | P1 | OPEN | 2026-11-13 |
| FD-010 | Consume Assay's exact-key baseline reuse in campaign execution | P2 | OPEN | 2026-11-13 |
| FD-011 | Optional persistent campaign scheduler after the one-shot loop is proven | P2 | PARKED 2026-11-24 (until the live campaign) | 2026-11-13 |
| FD-012 | Export campaign progress for cgprofile guard policy and evidence | P2 | CHANGED: thrashguard registration | 2026-11-13 |
| FD-013 | Serialize concurrent campaign state updates | P2 | DONE | 2026-11-14 |
| FD-014 | Probe command grows with the number of batches and hits the 128 KiB argument limit near 100 batches per host | P2 | DONE | 2026-11-14 |
| FD-015 | Cold-start stall threshold ignores the predicted candidate time, so slow lanes stall and re-queue without end | P1 | DONE | 2026-11-14 |
| FD-016 | A batch that exits without progress is re-queued on every exit, without limit | P1 | DONE | 2026-11-14 |
| FD-017 | One candidate that does not fit the re-queue target drops every other pending candidate of its batch | P2 | DONE | 2026-11-14 |
| FD-018 | Collect counts a verdict's never-started budget_exceeded candidates as timeouts and hides missing work | P1 | DONE | 2026-11-14 |
| FD-019 | Stale records in the append-only progress file count as finished for a later campaign on the same commit | P2 | DONE | 2026-11-14 |
| FD-020 | Host loss is measured from the last contact, and a slow probe counts as unreachable | P2 | DONE | 2026-11-14 |
| FD-021 | A transport or contract error on one host aborts the scheduling step for every host | P2 | DONE | 2026-11-14 |
| FD-022 | Dispatch does not re-check memory/IO PSI before starting the next batch | P2 | DONE | 2026-11-14 |
| FD-023 | The hx wrapper path is derived from the source checkout and fails from an installed wheel | P3 | DONE | 2026-11-14 |
| FD-024 | Deferred re-queue de-duplication is not pinned for three or more hosts | P3 | DONE | 2026-11-14 |
| FD-025 | The first stall window uses unscaled reference seconds when no comparable host speed reference exists | P3 | DONE | 2026-11-14 |
| FD-026 | A host that stays in ERROR has no loss timer | P3 | DONE | 2026-11-14 |
| FD-029 | Nothing wires `fleet scope`/`fleet sizing`/`fleet measurement` into a release or campaign workflow | P2 | OPEN | 2026-11-15 |
| FD-030 | The new per-worker demand keys are not accepted inside `run-gate.toml` | P2 | OPEN | 2026-11-15 |
| FD-031 | Sizing budgets come from medians and a fixed margin, not a measured tail | P2 | DONE | 2026-11-15 |
| FD-032 | `fleet scope` reads a checkout it never checks for uncommitted or untracked work | P3 | DONE | 2026-11-15 |
| FD-033 | The rolling cursor goes stale on every release that changes a target file | P2 | DONE | 2026-11-15 |
| FD-034 | No host-install script for fleetd; the docs prescribed a system-Python pip install that Debian 13 refuses | P2 | DONE | 2026-11-15 |
| FD-035 | `host-facts/1` validation does not bound a service `MemoryMin`, so an implausible fact is first refused at plan time, not at collect or validate time | P3 | DONE (by design) | 2026-11-15 |
| FD-037 | Rolling mode keys a candidate by its file's content, so a partial edit re-judges the whole file | P3 | PARKED 2026-11-24 (until the live campaign) | 2026-11-15 |
| FD-038 | Rolling mode's changed candidates share the `slice_size` budget, so heavy per-release churn can defer them and starve the never-judged remainder | P3 | DONE | 2026-11-15 |
| FD-039 | Host-side probe logic was a Python string and `FakeTransport` reimplemented it | P1 | PART 1 FIXED | 2026-11-24 |
| FD-040 | The real probe lost the cgprofile `memory_peak_bytes` that the fake returned | P1 | FIXED | 2026-11-24 |
| FD-041 | Delete `fleet merge-check`, `fleet-shard-identity/1` and `fleet guard validate`; assay owns `assay-merge-manifest/1` | P1 | FIXED IN THIS BRANCH (P-fd-02) | 2026-11-24 |
| FD-042 | Two schedulers book v1001: host-local weighted admission in the shared runner | P1 | OPEN | 2026-11-24 |
| FD-043 | Campaign unit = assay shard range per host | P2 | OPEN | 2026-11-24 |
| FD-044 | Rolling scope frozen; delete it after B561 carry-forward | P2 | OPEN | 2026-11-24 |
| FD-045 | Docs are a second, prose-pinned source of truth | P2 | OPEN | 2026-11-24 |
| FD-046 | Facts tables and one lane-cost store | P2 | OPEN | 2026-11-24 |
| FD-047 | FakeTransport rewrites progress mtimes after the shared probe | P3 | OPEN | 2026-11-24 |
| FD-048 | Malformed Run-Gate lane config can crash the host probe and fake start | P3 | OPEN | 2026-11-24 |
| FD-049 | host-facts/2: guard_present reshape together with FD-012 | P2 | DONE in P-fd-05 C1 | 2026-11-24 |
| FD-050 | Add admission queue and legacy charges to the host policy schema before deployment | P1 | FIXED IN THIS BRANCH (C3a) | 2026-11-26 |
| FD-051 | Retain and prune completed admission terminal records in host agent package C | P2 | OPEN | 2026-11-26 |
| FD-052 | An inspector ERROR at a fitting lease waiter blocks every later waiter until expiry | P2 | OPEN (known limit) | 2026-11-26 |
| FD-053 | Prevent overlapping authenticated group prefixes in package C policy | P2 | FIXED IN THIS BRANCH (C3a) | 2026-11-26 |
| FD-054 | Package C must bound every provider and inspector call made under the queue lock | P1 | FIXED IN THIS BRANCH (C3a) | 2026-11-26 |
| FD-055 | A stray record after an unconfirmed rollback can leave a charge the waiter never claims | P2 | OPEN | 2026-11-26 |
| FD-056 | The wait deadline is not rechecked after the queue lock is taken | P3 | OPEN | 2026-11-26 |
| FD-057 | Admission subprocess tests have no timeout, and the two-process test is weak | P3 | OPEN | 2026-11-26 |
| FD-058 | Aged-holder scan can skip queue-ordered merge-gate waiters | P1 | FIXED IN THIS BRANCH (C3a) | 2026-11-27 |
| FD-059 | Measure and design PSI-aware admission before enforcing a prediction | P1 | OPEN (design only) | 2026-11-27 |
| FD-060 | A granted lease client can stay connected forever without binding | P2 | FIXED IN THIS BRANCH (C3a) | 2026-11-27 |
| FD-061 | Persist executor result details for terminal status | P2 | FIXED IN THIS BRANCH (C3b-1) | 2026-11-27 |
| FD-062 | Retain and garbage-collect host upload objects | P2 | OPEN (C3b-2 follow-up) | 2026-11-27 |
| FD-063 | Incrementally count large Assay progress files | P2 | OPEN | 2026-11-27 |
| FD-064 | A legacy job directory without EXIT after host reboot remains charged | P2 | OPEN | 2026-11-27 |
| FD-065 | Legacy-job listing is point-in-time and completed directories consume the entry ceiling | P2 | OPEN | 2026-11-27 |
| FD-066 | Synchronous Docker process creation cannot be cancelled by the query deadline | P2 | OPEN | 2026-11-27 |
| FD-067 | Close the short-lane event-observation race before enabling the executor | P1 | OPEN; BLOCKER BEFORE ENABLEMENT | 2026-11-27 |
| FD-068 | Prove persistent exec-lane work stopped before cancellation releases its charge | P1 | OPEN; cancellation remains stopping | 2026-11-27 |
| FD-069 | Protect host-owned Run-Gate events from a same-uid host lane | P1 | OPEN; transport limit | 2026-11-27 |
| FD-070 | Verify exec checkout mount against a real persistent runner before enablement | P1 | OPEN; LIVE PROBE REQUIRED | 2026-11-27 |
| FD-071 | Preserve store collection failure detail in the host result | P2 | FIXED IN THIS BRANCH (C3b-1) | 2026-11-27 |
| FD-072 | Bind Run-Gate repo/project labels to the request | P2 | FIXED IN THIS BRANCH (C3b-1) | 2026-11-27 |

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

**State:** OPEN; solved at host admission (FD-042), 2026-11-24. **Priority:** P1. **Filed:** 2026-11-13.

`fleet plan` computes `B_h` and proves each planned wave against it, but the
current Run-Gate `--only-candidates` interface has no per-invocation
host-specific Assay memory-budget argument. The printed command supplies the
planned worker count only; it does not enforce `B_h`. Runtime admission remains
owned by the host's existing Run-Gate, Assay lane, and cgroup configuration.
The Run-Gate campaign client must carry the selected host budget into Assay's
memory admission (or establish an equivalent enforced bound) before treating
the plan's wave proof as an execution guarantee.

## FD-004 Host slice memory ceiling is not part of the planner facts

**State:** OPEN; solved at host admission (FD-042), 2026-11-24. **Priority:** P1. **Filed:** 2026-11-13.

WP1(a) reports physical lane memory and per-service control-plane `MemoryMin`,
while WP2 computes `B_h = P_h × overcommit − O_h` as specified in the compute-host
design. The facts do not include the host-owned `dev-gates.slice` `memory.max`
ceiling, so the planner cannot report when its estimate exceeds that execution
limit. The design lists slice `MemoryMax` as a downstream safety net; this MVP
keeps the specified physical-RAM budget and does not reinterpret the cgroup
ceiling as available RAM. A follow-up should collect the ceiling with a reason
when unavailable and derive an effective limit that also accounts for current
slice use and other running jobs before treating planned capacity as enforceable.

## FD-005 Learn the per-host correction factor

**State:** PARKED 2026-11-24 (until the live campaign). **Priority:** P2. **Filed:** 2026-11-13.

WP2 accepts one stored `f_h` per host, defaults it to 1.0, and validates it in
`[0.5, 2.0]`; this package deliberately does not learn it. WP5 should implement
the design's EWMA of actual/predicted time from finished candidates only when
their normalized reference came from another host or from the declared value.
Skip a sample normalized from that host's own prior measurement, reset on CPU
model change or re-enrollment, and never fit by lane or hardware flavor.

## FD-006 Lift the manual one-slot-per-host and J cap

**State:** PARKED 2026-11-24 (until the live campaign). **Priority:** P2. **Filed:** 2026-11-13.

The current manual planner uses one campaign slot per host because concurrent
same-lane selected runs share Run-Gate's in-flight identity and Assay store,
and it caps `W_h` at the lane's `efficient_upper_bound` J. On a four-core host
with oversubscribe 1.25 and J = 3, that leaves two of five CPU-derived worker
slots unused. This is an accepted safety deviation for the no-supervisor MVP.
WP4/WP5 should give selected runs distinct identities and schedule enough
independent instances to use the remaining safe capacity without sharing state.

## FD-007 Replace the rejected RAM-proportional control-plane reserve

**State:** DONE. **Priority:** P1. **Filed:** 2026-11-13. **Completed:**
fleetd-reserve wave, design rev 6.

The rejected `clamp(256 MiB + 6% × MemTotal, 768 MiB, 2 GiB)` rule and the
`max(reported ctl.slice/memory.min, default)` floor are replaced. Fleetd sums
the positive `MemoryMin` facts for its fixed control-plane service list only
when every listed service reports a positive value. If any service is absent,
zero, or `infinity`, it uses that host's fixed profile constant and reports the
missing service units. The compatibility profile constant is 768 MiB, the
former lower bound and above the measured 450–500 MB near-idle hot RSS on the
two 8 GiB hosts (2026-11-14). No reserve term depends on installed RAM.

Parent `ctl.slice/memory.min` plays no part in the reserve: the planner ignores
`control_plane.memory_min_bytes`. (`host-facts/2` collects that field again, for
reporting only.) Fleetd
emits `reserve_bytes`, `reserve_source`, and
`reserve_missing_services` in plan and campaign status output. Old host facts
without the new service values use their profile constant, allowing host
setup's per-service drop-ins to roll out without a flag day. This close covers
the replacement rule only; measured-p95 calibration is FD-007b.

## FD-007b Calibrate profile reserve from measured p95 under load

**State:** OPEN. **Priority:** P2. **Filed:** 2026-11-15.

The 768 MiB compatibility value is a safe starting profile constant, not a
calibrated target. The measured near-idle hot RSS on the two 8 GiB hosts was
about 450–500 MB; it does not establish the protected working set under
representative fleet load. Calibrate the fixed constant separately for each
host profile from measured p95 control-plane use under load. Do not scale the
reserve with `MemTotal`, pool samples across hardware flavors, or infer a
calibration from idle RSS.

**Close when.** The workload and measurement window are recorded, each profile
has a measured p95 and an explicit fixed reserve derived from it, and the
profile values are updated in host-owned facts configuration with tests
showing that planning uses the selected value. Keep the existing fallback
until that evidence is accepted.

**Per-service floors.** This entry covers the FIXED profile constant, which is
now used only when no per-service fact exists at all (see FD-007c). The
per-service fallback introduced by FD-007c already carries the installer's
measured floors, so a host with the documented drop-ins gets the measured table
rather than the constant. Calibration below is still open for the
`profile-constant` case, and for any host whose per-service table drifted
above twice the documented floors (the plan then refuses with FD-007b named in
the reason).

## FD-007c Per-service reserve fallback with provenance

**State:** DONE. **Priority:** P1. **Filed:** 2026-11-13 (as a reference),
completed 2026-11-15 in the `pi-fleetd-followups` package.

The old rule summed per-service `MemoryMin` facts only when every listed
service reported a positive value, and otherwise used the host's fixed profile
constant. `mdt-psi-guard.service` is optional and absent on hosts that do not
run it, so every such host fell back to the constant forever and the measured
per-service table could never take effect there.

The planner now resolves one term per listed service and sums them. A reported
positive value, at or below that service's documented per-service ceiling, is
used as reported. A service with no positive value — or a value above its
ceiling — contributes that service's documented default from
`CONTROL_PLANE_SERVICE_MEMORY_MIN_DEFAULTS` (the measured table the Debian
installer writes: ssh 10, docker 210, containerd 92, journald 24, cgprofiled 4,
psi-guard 5 MiB). A service in `CONTROL_PLANE_SERVICE_OPTIONAL_UNITS`
(`cgprofiled.service`, `mdt-psi-guard.service` from the installer's own set) whose
systemd `LoadState` is `not-found` or `masked` contributes 0 — and only that
case may contribute 0. An unknown or uncollected load state is a missing fact,
never an absent unit. The total therefore never falls below the sum of what was
actually reported.

The collector queries each unit's `LoadState` alongside its `MemoryMin`, carries
both as `host-facts/1` facts (`control_plane.service_memory_min.<unit>`
gains nullable `load_state` + reason), and emits
`control_plane.service_memory_min_reason` instead of a table when a facts root
queries no service. Plans and campaign status expose `reserve_services`, one
`{unit, source, bytes}` row per service with the provenance value
(`reported`, `default`, `absent-optional`), beside `reserve_bytes`,
`reserve_source`, `reserve_missing_services`, and `reserve_reason`. The fixed
profile constant remains ONLY for the no-per-service-fact case, and
`reserve_source = "profile-constant"` says so.

Two derived sanity bounds refuse instead of summing: a reported value above
4 x its own documented default, and a total above twice the documented floors
(690 MiB). A refusal names the unit or the total and the FD-007b re-measurement,
and the host is not planned that wave; nothing is clamped. Tests parse
`scripts/debian-install-v2/debian_install_v2/templates.py` and
`scripts/debian-install-v2/docs/CONTROL-PLANE-MEMORY.md` and fail on drift
with fleetd's copy.

## FD-035 `host-facts/1` does not bound a service `MemoryMin`

**State:** DONE, closed as BY DESIGN (fleetd WP3, 2026-11-18; fleetd@e5e97ac78).
**Priority:** P3. **Filed:** 2026-11-15. The contract validates type only and
planning refuses implausible values; `docs/SPEC.md` says so in one sentence
("By design (FD-035)"). No code change, per the document-limits-instead-of-
machinery rule.

The contract validator checks the TYPE of `control_plane.service_memory_min.<unit>.memory_min_bytes` (a nullable
non-negative integer) but not its magnitude, because `host-facts/1` is a
host-report contract and the sanity bound lives in planning policy. Planning
refuses what the contract accepts: a value above 4 x that service's documented
default, or a total above twice the documented floors, refuses the host with
`reserve_reason`. So an implausible report degrades from "the plan refuses this
host and says why" instead of "`fleet facts collect` refuses to emit it", which
is the intended split, but it means a collector run on a misconfigured host
still produces a document a reader may take as valid.

Either direction is defensible; the open question is whether the bound belongs
in the contract (with a config or table source) or stays a planning refusal.
Recorded rather than silently left as an asymmetry, so a reviewer does not
have to rediscover it. No code change in this package on purpose.

## FD-034 Host-install script for fleetd

**State:** DONE. **Priority:** P2. **Filed:** 2026-11-15. **Completed:**
`pi-fleetd-followups`.

The docs described a host install but no script existed, and both the README
and the consumer guide told an operator to `pip install` the two wheels with
`python3 -m pip`, which Debian 13 refuses for the system Python (PEP 668).
`scripts/netcup/runner/install-fleetd.sh` verifies both wheel hashes, installs
offline into `/opt/fleetd/<version>/venv`, links `/usr/local/bin/fleet`, is
idempotent for the same hashes, and has a `--check` mode that writes nothing.
`fleetd/README.md`, `fleetd/docs/SPEC.md`, `fleetd/docs/CONSUMERS.md` and both
netcup guides point at it. The script was exercised only against temp prefixes
and fake wheels; no host was touched, so live host acceptance remains a
separate step for the host board.

## FD-008 Strict whole-lane result merge

**State:** MOVED to assay B565 strict merge (2026-11-24). **Priority:** P1. **Filed:** 2026-11-13.

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
Fleetd's own `assay-merge-manifest/1` validation and owner-record selection
were deleted in P-fd-02; this moved item remains assay-owned.

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

**State:** PARKED 2026-11-24 (until the live campaign). **Priority:** P2. **Filed:** 2026-11-13.

The supported controller interface is deliberately one-shot: `run` starts
available jobs and returns, while callers invoke `status` and `tick` as needed.
Only consider a persistent scheduler after the named-job and restart-recovery
contracts have operational evidence. Preserve idempotent names, no leases,
progress-based stall detection, pause on unreachable hosts, and requeue after
30 minutes; do not introduce a campaign wall budget. Any daemon/socket process
model needs a separately reviewed lifecycle and local-state contract.

## FD-012 Campaign progress for guard policy

**State:** CHANGED 2026-11-24: thrashguard registration (job registration on the thrashguard socket, owner TG-1). **Priority:** P2. **Filed:** 2026-11-13.

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

**State:** DONE (fleetd-wp1-20261118, 729b3671e). **Priority:** P2. **Filed:** 2026-11-14.

Residual (WP1b): the name table still grows by about 11 bytes per non-settled job, so one host's probe reaches the 128 KiB argument cap at about 11 000 non-settled jobs; `probe_settled` closes the stall-in-the-end-offset-gap race.

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

**State:** DONE (2026-11-18, 4157958d3). **Priority:** P2. **Filed:** 2026-11-14 (Opus series review).

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

**Resolved (2026-11-18, 4157958d3).** `replan_pending` returns `(batches, unplaceable)`; the placeable candidates are re-queued and the unplaceable ids stay in `deferred_requeues` with the planner's reason (and a state warning) until a larger host is online, and `collect` reports them missing with that reason.

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

**State:** DONE (2026-11-18, cb7a00b33). **Priority:** P2. **Filed:** 2026-11-14 (Opus series review).

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

**Resolved (2026-11-18, cb7a00b33).** The loss clock starts at the first failed contact the controller observed (`unreachable_since = now`, persisted, at every contact-failure site); a timeout (rc 124) raises `HostSlow` and is reported `SLOW`, feeding the same 1800 s clock; ssh 255 is unchanged.

**Follow-up (WP2b, 28c91bbda).** An `ERROR` answer now also ends an open unreachable episode (one shared helper with the good-probe path); before, a probe-level error left the old clock open and the next failed probe declared the host `LOST` at once and re-queued batches still running there. A host already `UNREACHABLE` when this code takes over a state keeps its old clock and may go `LOST` on the first new tick (documented in SPEC).

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

**State:** DONE (fleetd-wp1-20261118, 7411617f3). **Priority:** P2. **Filed:** 2026-11-14 (Opus series review).

Design §4.3 says fleetd starts nothing new on a host whose memory or IO PSI
`some avg10` exceeds 25. `fleet plan` applies that once, from the facts
snapshot; `_start_available()` starts the next batch without fresh PSI. Over a
multi-hour campaign, admission therefore rests on the plan-time value and the
host guard.

Fix: have the probe also return `/proc/pressure/{memory,io}` `some avg10`
and defer starts on that host above the plan's `psi_refuse_some_avg10`
(no stop, only no new start).

## FD-023 hx wrapper path only valid in a source checkout

**State:** DONE (fleetd-wp1-20261118, 3258259fa). **Priority:** P3. **Filed:** 2026-11-14 (Opus series review).

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

**State:** DONE (2026-11-18, 3fc04670b). **Priority:** P3. **Filed:** 2026-11-14 (fix4 review, for the Opus final review).

`CampaignManager._predicted_candidate_seconds` scales a candidate's
`time_ref_seconds` by the reference baseline, the host's single-core score and
`f_h` only when the host's Geekbench major, version and baseline score match the
plan's `time_reference`. Otherwise it uses `time_ref_seconds` unscaled (and
ignores `f_h`); the SPEC documents this fallback. The stall threshold is
`max(900 s, 3 x predicted p95, 3 x observed p95)` plus the declared (or
host-p95) `baseline_seconds` while the batch has finished no candidate. A host
that is slower than the reference by more than that margin, with no comparable
score, can therefore be stopped during its first candidate; the batch then has
no finished candidate and becomes `FAILED` (not re-queued, FD-016). The
host-observed p95 corrects the threshold only after a candidate has finished on
that host. The 900 s floor and the baseline term hide the gap for short
candidates but not for long ones.

**Wanted.** Without a comparable score, widen the first window by a documented
factor, or require a first observed candidate time per host before the first
stall decision, or have the planner refuse to start a non-comparable host
without an explicit stall allowance. **Close when.** A test with a non-comparable
host whose first candidate takes more than `3 x time_ref_seconds + baseline`
(and more than 900 s) is not stalled in its first window, and the SPEC and
design guide state the new rule.

**Resolved (2026-11-18, 3fc04670b).** Ruling: while a host without a comparable Geekbench reference has observed no finished candidate, its predicted candidate time is `NONCOMPARABLE_FIRST_WINDOW_FACTOR` (3) x the unscaled reference, so the first window is `max(900 s, 9 x reference)` plus the baseline; documented in the SPEC and design guide.

## FD-026 Host in ERROR has no loss timer

**State:** DONE (2026-11-18, 18c8b30dc). **Priority:** P3. **Filed:** 2026-11-14 (fix4 review, for the Opus final review); code path traced and confirmed 2026-11-14.

FD-021 records a `ContractError` or non-`HostUnavailable` `TransportError` on one
host as `ERROR` and keeps supervising the others. The loss timer lives only in
the `HostUnavailable` branch of `CampaignManager._refresh`
(`unreachable_since_mono`, `LOST` after `LOST_AFTER_SECONDS`); `_record_host_error`
neither starts nor evaluates it. `_detect_stalls` skips any job whose host is not
`ONLINE`, and `_handle_lost_hosts` acts only on `LOST`, so the batches on a host
that keeps answering with an error are neither stopped, declared stalled nor
re-queued. Confirmed with the fake transport: a probe that raised a
`ContractError` on every tick for 5 hours of campaign time left the host
`ERROR`, `unreachable_since_mono` unset, `loss_requeued` false and its batches
`RUNNING`. The same applies to a host that was `UNREACHABLE` and then answers
with an error: its existing timer is not evaluated while the status is `ERROR`.
The campaign shows the error text but nothing ages it, so an unattended campaign
can wait on that host indefinitely.

**Wanted.** Decide the policy and document it: either age a persistent `ERROR`
(status reports the time in error and the campaign warns past a threshold), or
declare `LOST` after the same 1800 s as an unreachable host and re-queue its
unfinished candidates elsewhere (a returning host may duplicate work, as for any
lost host; contract faults mean its batches may in fact be alive). **Close when.**
Tests show a host in `ERROR` beyond the threshold is reported (and, if LOST is
chosen, has its unfinished candidates re-queued) and that a recovery before the
threshold keeps its batches; SPEC and design guide state the rule.

**Resolved (2026-11-18, 18c8b30dc).** Ruling: age and warn only. `status` host rows carry `error_since`, `error_seconds` and a `warning` from 1800 s; no automatic LOST and no re-queue (an ERROR host may have live batches), the operator decides.

**Follow-up (WP2b, 28c91bbda).** The error clock is decided once per command from the host's final status (after the start step for `run`/`tick`, at the end of the refresh for `status`/`collect`/`measurement`); an error raised after a good probe (digest mismatch, invalid record, refused stop or start) no longer restarts it, so the 1800 s warning fires exactly once. Known limit: a `status` between ticks ends a refused-start episode (it probes successfully and cannot start); the next refused start begins a new clock.

## FD-029 Nothing wires the default R2 commands into a workflow

**State:** OPEN. **Priority:** P2. **Filed:** 2026-11-15.

`fleet scope`, `fleet sizing` and `fleet measurement` are committed, tested
producers, but nothing calls them yet: a release step does not regenerate the
candidate-id file and a campaign does not size its shards from the plan.
(`fleet merge-check` and the shard identity document were deleted by FD-041;
strict shard merging is assay's, B565 `strict-shard`.)
Today an operator runs each command by hand and passes the results on. The
intended callers are the Run-Gate campaign client (RG-276) and the Assay
supervisor (`--shard`, `--instances`), and neither has the wiring yet.

**Wanted.** Decide which owner calls each producer and at which step: the
changed-lines id file at the release step (cmru release), the sizing document
at campaign start, and the measurement recorder after collection. **Close
when.** One end-to-end invocation of a real campaign runs all three without an
operator retyping a path, and a test pins that a stale id file stops the flow
instead of being noticed later.

## FD-030 The per-worker demand keys are not accepted inside `run-gate.toml`

**State:** OPEN. **Priority:** P2. **Filed:** 2026-11-15.

`worker_memory`, `worker_cpu` and `instance_cpu` are fleetd demand keys. Run-Gate
25.6.0 refuses `demand` inside `run-gate.toml` altogether (RG-275/RG-276), so a
project keeps its demand in a separate TOML file and passes it to
`fleet plan --demand`. Until Run-Gate owns the table, the declaration that
sizing reads and the declaration the lane runs under are two files that can
drift, and `efficient_upper_bound` still cannot be joined to Assay's
`judge.mutation.jobs_max` (FD-002).

**Wanted.** When RG-276 lands, accept the three new keys in the same table the
lane declares, so one declaration feeds plan, sizing and Run-Gate. **Close
when.** A `run-gate.toml` declaring `demand` with the per-worker unit plans and
sizes the campaign with no separate TOML file, and Run-Gate refuses a
`worker_memory` above `unit_memory` the same way fleetd does.

## FD-031 Sizing budgets use medians and a fixed margin, not a measured tail

**State:** DONE (fleetd WP3, 2026-11-18; fleetd@63568d5d3, docs fleetd@e5e97ac78).
**Priority:** P2. **Filed:** 2026-11-15.

**Resolution.** `fleet-r2-measurement/1` keeps its median fields and gains
additive optional summary fields (`cost_candidates`, `cost_seconds_sum`,
`cost_seconds_mean`, `cost_seconds_stdev`, `cost_seconds_max`) over killed,
survived, `budget_exceeded` and `hung` candidates at their bounds. Sizing uses
the mean and derives each shard's margin as `max(1.10, 1 + 1.645 x cv /
sqrt(n))` (the one-sided 95th percentile of a sum of `n` candidates); `--margin`
is the explicit override; a document without the new fields sizes from its
median. The budget is documented as an estimate whose remainder continues in a
later instance. Tests: `tests/test_sizing.py` (a dispersed measurement yields a
budget at or above its measured tail, the override still works, older documents
load). Original finding below.

`fleet sizing` computes each shard's budget from `candidates x
per_candidate_seconds / workers + baseline`, multiplied by a fixed `--margin`
(1.10 by default), and the per-candidate seconds are the campaign MEDIAN over
killed and survived candidates. A heavy-tailed lane therefore gets a budget
derived from its typical candidate: if a shard meets several candidates far
above the median, its sum exceeds the estimate and the derived budget stops it
part way. The measurement document already records `elapsed_seconds_max`, so
the tail is observable; sizing does not use it.

Review addition: a shard's wall time is the SUM of its candidates, so the
estimator that matches the budget is the MEAN, not the median, and a
right-skewed distribution has mean above median. The median is also taken over
`killed` and `survived` only: candidates that ended `budget_exceeded` or `hung`
are excluded from it yet still occupy their workers for their full per-candidate
bound, so a lane with a few percent of those is under-budgeted by exactly that
share. The measurement document has no sum or mean field yet.

**Wanted.** Either derive the budget from a measured tail (for example the p95
of the per-candidate sum, or median plus a measured dispersion term) with the
margin as an explicit override, or document plainly that a heavy-tailed lane is
expected to hit its budget and be continued by a later instance. **Close
when.** A test with a dispersed measurement shows the derived budget covering
the measured tail, or the limitation is stated in the SPEC and design guide
with a reason and the backup path.

## FD-032 The scope producer does not check the checkout it reads

**State:** DONE (review, 2026-11-15): `fleet scope` now refuses a checkout whose
HEAD is not the named commit or whose tracked files have uncommitted changes,
with a test for each path; untracked files are ignored. **Priority:** P3.
**Filed:** 2026-11-15.

Original finding: `fleet scope` computes the diff between a release tag and the commit it is told
to judge, and reads the candidate list from an `assay plan --json` file. It
never checks that the working tree is clean, so a scope computed in a checkout
with uncommitted work is a valid-looking document about a tree that is not the
commit it names: the plan may list candidates from the dirty tree while the
diff describes the committed one. The result is internally inconsistent without
saying so. `assay plan` itself reads the working tree; nothing here decides
whether that is the intended behaviour.

**Wanted.** Decide whether the producer should refuse a dirty checkout (the
safe default, since the diff and the plan would otherwise describe two trees)
or record the dirty state in the document as a distinct fact that a consumer
must acknowledge. **Close when.** A scope generated from a dirty checkout is
either refused with a reason or carries a field that says so, with a test for
each path, and the SPEC states the rule.

## FD-033 The rolling cursor goes stale on every release that changes a target file

**State:** DONE (`pi-fd033-20261115`). **Priority:** P2. **Filed:** 2026-11-15
(review of the R2-default package). **Completed:** 2026-11-15.

A candidate id folds in the SHA-256 of its source file and of the mutated file
(`assay/src/assay/candidate_identity.py`), so the whole-target list digest the
`fleet-r2-scope-cursor/1` cursor was validated against changed whenever any target
file changed. `fleet scope` then refused with `stale-cursor`, and `--rebaseline`
restarted at offset 0 of the new, id-sorted list: the refusal was fail-closed,
but rolling mode was a sample of the first slice, not a sweep of the target.

**What changed.** The sweep state is now a `fleet-r2-scope-cursor/2` document
keyed by **file content** and by a location-stable candidate key, not by the
candidate-id list:

- per path, the cursor records the **git blob id** of the content its marks
  belong to, the planned candidate **count**, the **candidate keys** judged at
  that content, and the **pass** in which the path was last judged;
- a candidate key is `sha256(path, operator, ordinal)` where the ordinal counts
  within one `(path, operator)` group in line order. It is derived in fleetd
  from what `assay plan --json` already emits; `assay/src` is untouched and no
  assay change is required;
- a path whose current blob differs from its mark is **due again immediately**
  and judged whole (a changed site identity shifts when anything before it
  moves); a path whose blob still matches keeps its marks, so an edit to
  another file never invalidates the cursor;
- a mark that cannot be confirmed against the current plan (a different
  planned count for the path, or a judged key the plan no longer contains) makes
  that path due again and is reported as `changed`; a path with no planned
  candidate is `retired` with a reason. Nothing is dropped silently, and a
  candidate is never marked judged while its file changed since the mark;
- the slice is a **prefix** of the deterministic order (changed files first,
  least recently judged first, then never-/not-yet-judged candidates in
  path/line order), and `slice_size` is a budget that re-slices the same sweep;
- a **finished sweep** starts a recorded new pass (`new_pass: true`) instead of
  reporting an empty `nothing-to-judge`; in rolling mode that status now means
  only "the plan is empty";
- the document carries the accounting (`changed_files`, `changed_candidates`,
  `never_judged_candidates`, `pending_candidates`, `judged_candidates`,
  `retired`) and a whole-document `state_digest`, so a hand-edited cursor is
  refused rather than trusted;
- the cursor is resumed only for its own lane (`stale-cursor`, exit 3, otherwise).
  A `fleet-r2-scope-cursor/1` cursor is refused with its migration: `--rebaseline`
  is that migration (one new pass from the current content) and reports the
  schema it migrated from in `slice.legacy_cursor`.

Rolling therefore means: **the candidates of files changed since they were last
judged** (a changed file is judged whole) **then the next slice of never-/
least-recently judged candidates, all within one `slice_size` budget** (FD-038:
the changed candidates are not exempt from the budget). A mark counts only for
the pass it was written in, so every later pass sweeps the whole target again.
`--rebaseline` is now rare and explicit rather than a per-release ritual.

**Evidence.** `tests/test_scope.py` covers a synthetic target of 60 candidates
across five releases (full coverage in `ceil(N / slice_size)` releases, then a
recorded new pass; every later pass sweeps the whole target again, the review
round's regression), an edit between releases (edited file judged whole, others
resume, no candidate skipped, no re-judge of untouched files), file adds/
deletes/plan-shape changes (`changed`/`retired` accounting), determinism
(identical input sequences produce identical slices), corrupt/stale/hand-edited/
other-lane/legacy cursors refused, and the CLI round trip. Documentation: `docs/
R2-DEFAULT.md` ("Scope: what an R2 campaign judges"), `docs/DESIGN-GUIDE.md`
("Why the sweep is keyed by file content"), `docs/SPEC.md`, `docs/CONSUMERS.md`
and `CHANGES.md`.

**Not covered, by decision.** A release that edits part of a file re-judges that
whole file (FD-037); the key assumes `assay plan` reports a stable `operator`
for a site; cross-worktree/other-origin id collisions are assay's problem, not
the cursor's, because the cursor keys on content rather than on ids.

## FD-038 Rolling mode's changed candidates share the `slice_size` budget

**State:** DONE (fleetd WP3, 2026-11-18; fleetd@a6beb0b5a, docs fleetd@e5e97ac78).
**Priority:** P3. **Filed:** 2026-11-15 (review of FD-033).

**Resolution.** A separate cap for the sweep share: `[r2] reserve_percent`
(default 25, 0 to 100, rounded up, capped at `slice_size - 1`) of every slice is
reserved for the non-changed remainder, and the scope document's `slice` reports
`reserve_percent`, `reserved_candidates`, `deferred_changed_candidates` and
`deferred_changed_files` (every deferred changed file). `0` restores the
changed-first slice. `fleet-r2-scope-cursor/2` is unchanged. Tests:
`tests/test_scope.py` (a target edited every release still judges the reserved
share and no deferred file is absent from the accounting). Original finding
below.

The rolling slice is a prefix of one order: files whose content changed since
their mark first, then never-/not-yet-judged candidates. A release that changes
more candidates than `slice_size` judges only a prefix of them (the rest of a
half-judged changed file continues in path order, not first), and a target whose
files are re-edited every release by more than `slice_size` candidates never
reaches the never-judged remainder. `changed-lines` has no such budget.

**Wanted.** A decision: either the changed candidates are exempt from
`slice_size` (rolling = changed-lines + a budgeted slice, at the cost of an
unbounded release), or a separate cap for the sweep share (a minimum number of
never-judged candidates per release), with the accounting naming what was
deferred. **Close when.** A synthetic target edited every release shows the
chosen rule and a test pins that no file is deferred without being reported.

## FD-037 Rolling keys a candidate by its file's content, so a partial edit re-judges the whole file

**State:** PARKED 2026-11-24 (until the live campaign). **Priority:** P3. **Filed:** 2026-11-15 (with FD-033).

Rolling whole-target mode (FD-033) keys a candidate to its file's **content**
(the git blob id) rather than to a line span, because Assay's candidate id folds in the
source file's SHA-256 and a line number shifts whenever anything above a site
moves. The consequence, deliberately accepted for FD-033: a release that edits
one line of a 2000-line target file marks every candidate of that file due
again, so the release re-judges the whole file even though the changed-lines
scope would have judged only the sites on the added lines. That is a superset
of the changed-lines scope — coverage is never lost — but it is extra
mutation work on big files.

**Wanted.** A refinement that keeps the fail-closed property without the
whole-file cost, for example keeping per-site marks with a content digest of
the site's own neighbourhood (the enclosing block/scope text, or the file span
between two unchanged sites) so a local edit re-judges the local sites and an
insertion invalidates only the marks below it. Any such key must still make a
site due again whenever its content changed and must never re-point a mark at
another site.

**Close when.** A synthetic target with a partial edit shows the release judging
only the edited region's candidates while every unedited region keeps its
marks, and the accounting names the sites that were re-judged and the sites that
were kept.

## FD-039 Host probe/start are string programs re-implemented by FakeTransport

**State:** PART 1 FIXED (2026-11-24, `6226a08a1`); installed `fleet host ...`
verbs remain P-fd-05. **Priority:** P1. **Filed:** 2026-11-24.

fleetd: host-side logic as a real module (hostside.py) shared by the shipped probe and FakeTransport; then installed `fleet host …` verbs with a versioned `fleet-host-agent/1` answer.

Source: .controller-state/design-review-2026-11-24/fleetd.md H1

**Evidence.** `fleetd.hostside` is stdlib-only source shipped through
`importlib.resources`; the probe, Assay-lane resolution, progress filtering and
verdict reading each have one implementation. `FakeTransport` materializes a
temporary host home and calls the same functions:
`test_the_shipped_probe_equals_the_direct_hostside_answer` and
`test_fake_start_and_probe_resolve_the_declared_assay_lane`.

## FD-040 `memory_peak_bytes` is always null on real hosts

**State:** FIXED (2026-11-24, `6226a08a1`). **Priority:** P1. **Filed:** 2026-11-24.

Collect's de-duplication drops the progress records that carry the peak, so `memory_peak_bytes` is null for every candidate collected from a real host. Fix with the hostside module (FD-039): real-format progress files collect a non-null peak.

Source: .controller-state/design-review-2026-11-24/fleetd.md H1

**Evidence.** The shared probe reads `profile.metrics.memory_peak_bytes` from
Assay `resource-profile/1` progress events, and collection merges a measured
duplicate into the first-owned record instead of dropping it:
`test_measurement_writes_per_candidate_evidence_from_owner_records`,
`test_collect_keeps_a_measured_peak_when_the_probe_record_lacks_it`, and
`test_progress_parser_reads_only_a_positive_profile_memory_peak`.

## FD-041 Delete `fleet merge-check`, `fleet-shard-identity/1` and `fleet guard validate`

**State:** FIXED IN THIS BRANCH (P-fd-02). **Priority:** P1. **Filed:** 2026-11-24.

fleetd: delete `fleet merge-check` now (assay strict merge owns it); assay owns `assay-merge-manifest/1`. Drop guard.toml validation (owner: thrashguard/TG-1); FD-012 becomes job registration on the thrashguard socket. Also delete `fleet-shard-identity/1` (fd-H5 Direction).

Source: .controller-state/design-review-2026-11-24/fleetd.md H5, M1

## FD-042 Two schedulers book v1001: host-local weighted admission in the shared runner

**State:** OPEN — P-fd-05 package C2 delivers the admission library only; it is not wired to a host agent or any caller. **Priority:** P1. **Filed:** 2026-11-24.

`fleetd.admission` now supplies the stdlib host book: capacity checks, durable charges, a priority queue, aged reservations, and lease accounting. The remaining FD-042 work is the host agent and socket, wiring remote-gate, fleetd, run-gate and ciu callers to that one book, and exposing the held charges in the host probe. FD-003/FD-004 are not closed until that integration ships.

Source: .controller-state/design-review-2026-11-24/fleetd.md H3

## FD-043 Campaign unit = assay shard range per host

**State:** OPEN. **Priority:** P2. **Filed:** 2026-11-24.

fleetd: campaign unit = assay shard range per host (sizing → --shard-offset/--shard-total); retire batch candidate lists except the memory-crash retry; collect = store copy + strict --merge-shards. Operator decision 2026-11-24 (option C): shard ranges per host now; RAM-ordered lists later only with measured per-candidate RAM.

Source: .controller-state/design-review-2026-11-24/fleetd.md H2; .controller-state/PROGRAM-2026-11-24/PROGRAM.md 2026-11-24 05:23Z (operator: option C)

## FD-044 Rolling scope frozen; delete it after B561 carry-forward

**State:** OPEN. **Priority:** P2. **Filed:** 2026-11-24.

fleetd: freeze rolling scope; delete `fleet scope` and the rolling cursor after assay B561 carry-forward exists.

Source: .controller-state/design-review-2026-11-24/fleetd.md H5

## FD-045 Docs are a second, prose-pinned source of truth

**State:** OPEN. **Priority:** P2. **Filed:** 2026-11-24.

fleetd docs: design moves to fleetd/docs/DESIGN.md, rules stated once in SPEC, closed FD items one line, drop sentence-pinning doc tests.

Source: .controller-state/design-review-2026-11-24/fleetd.md M5

## FD-046 Facts tables and one lane-cost store

**State:** OPEN. **Priority:** P2. **Filed:** 2026-11-24.

fleetd facts: per-service floors from host facts.toml (installer-written), drop the copied table + drift test; speed resolution same as cgprofile machine/1. One lane-cost store (run-gate footprint/history); remote-gate weight and fleet sizing read it; fleet measurement retired.

Source: .controller-state/design-review-2026-11-24/fleetd.md M3, M4

## FD-047 FakeTransport rewrites progress mtimes after the shared probe

**State:** OPEN. **Priority:** P3. **Filed:** 2026-11-24.

`FakeTransport._probe` replaces each shared-probe `progress_mtime` with the
per-job `FakeJob.progress_mtime`. The real probe returns the progress file's
`stat` mtime, so jobs sharing a file have one mtime and a file with content gets
that mtime even when the fake job field is null. Remove the override, or document
and justify the deliberate divergence in FD-039. Close when the fake row is the
direct `hostside.probe_document` value and no test depends on fake-only timing.

Source: fix-round review finding 4; `fleetd/src/fleetd/transport.py:427-429`.

## FD-048 Malformed Run-Gate lane config can crash the host probe and fake start

**State:** OPEN. **Priority:** P3. **Filed:** 2026-11-24.

A non-table `lanes` value or lane declaration raises `AttributeError` in
`resolve_assay_lane`; the fake start catches only `ValueError`. One unsafe lane
declaration also fails the whole host probe, and the shipped entrypoint prints a
traceback instead of one concise error line. Validate the table shape and define
the per-job versus whole-probe error boundary for fake and shipped execution.
Close with malformed-config tests through both paths.

Also: a garbled artifact can still crash the whole host probe. `math.isfinite(float(x))`
on a huge JSON integer raises `OverflowError`, which `probe_document` (it catches
`OSError` and `ValueError`) does not catch. Sites: `hostside.progress_records` (strict),
`verdict_reading` (baseline `wall_s`) and `campaign.py:686`. The old runner had it too.
Close with a huge-integer artifact test through both paths.

Source: fix-round review finding 5; `fleetd/src/fleetd/hostside.py:33-43,245`;
r2 review finding B.

Nit (r2 finding C): `returned_after_loss` is set on every refresh while pending and
nothing reads it except tests. Document it as a status marker or drop it.

## FD-049 host-facts/2: guard_present reshape together with FD-012

**State:** DONE in P-fd-05 C1. **Priority:** P2. **Filed:** 2026-11-24.

`host-facts/2` now reports `guard_present`, `guard_state`, `config_digest`, and
`frozen_units` with explicit wrapped values and null reasons. Freeze events
remain null until their source is available. This closes the guard-fact reshape
only. FD-012 remains the separate follow-up to register each started campaign
job on thrashguard's registrant socket when present; fleetd does not validate
or enforce guard rules.

Source: .controller-state/design-review-2026-11-24/fleetd.md M1; FD-012

## FD-050 Add host admission policy keys before deployment writes them

**State:** FIXED IN THIS BRANCH (C3a). **Priority:** P1. **Filed:** 2026-11-26.

`facts.py` and the `/2` validator accept `policy.aging_s`,
`policy.legacy_charge_bytes`, and `policy.legacy_planned_cores`; the facts
projection carries their units, source and unknown reasons. `aging_s` is
required for every queue decision; the two legacy charge values are required
when a listed job lacks an `EXIT` marker. Host configuration must still wait
until a release containing this schema is installed. Unknown
`policy.legacy_planned_cores` returns 75 with that key in the refusal reason.

Source: P-fd-05 C2 controller ruling M4 and the package C host-facts/2 policy
row.

## FD-051 Retain completed admission terminal records

**State:** OPEN for host agent package C. **Priority:** P2. **Filed:** 2026-11-26.

`fleetd.admission` writes terminal group/digest state before removing the active
charge-bearing record and does not prune terminal records. The host agent
package C owns the retention policy and safe pruning boundary.

Source: P-fd-05 C2 controller ruling M2.

## FD-052 Lease inspector ERROR can block later waiters

**State:** OPEN (known limit). **Priority:** P2. **Filed:** 2026-11-26.

A fitting lease waiter whose inspector keeps answering `ERROR` holds no charge
but can block every waiter that sorts after it, in lower priority classes too,
until its admission wait expires. The book fails closed; package C may add an
explicit recovery or retry policy.

Source: P-fd-05 review r1 Low 2 and controller ruling.

## FD-053 Prevent overlapping authenticated group prefixes

**State:** FIXED IN THIS BRANCH (C3a). **Priority:** P2. **Filed:** 2026-11-26.

The current id check is textual: group `ops` can claim `ops-ci-run-1`, which
also has a valid-looking prefix for group `ops-ci`. `agent_policy._identities`
rejects a policy with overlapping groups; the `/1` id grammar remains
unchanged.

Source: P-fd-05 C2 controller ruling for FU12 group-prefix ambiguity.

## FD-054 Bound every call made under the admission queue lock

**State:** FIXED IN THIS BRANCH (C3a). **Priority:** P1. **Filed:** 2026-11-26.

`capacity_provider`, `legacy_jobs_provider` and the Docker `inspector` run while
`queue.lock` is held, and that flock blocks without a timeout. One hung call
stops every submit, completion, status, cancel and wait expiry, so a merge gate
waits forever and a finished job keeps its charge. C3a wraps each call in a
single-outstanding daemon worker with the positive `agent.toml.query_timeout_s`:
capacity and legacy-list timeout to 75, inspector timeout to `ERROR`. A timed
out worker never owns `queue.lock`; later calls cannot create an unbounded
backlog behind it.

Source: P-fd-05 C2 review r2 M-2.

## FD-055 Stray charged record after an unconfirmed rollback

**State:** OPEN. **Priority:** P2. **Filed:** 2026-11-26.

`_atomic_new_json` unlinks the path even when the write failed before the rename,
and the wait loop does not look for an existing record before admitting. If a
rollback cannot be confirmed, the waiter counts the stray record against itself
and expires with "never started" while the record stays charged. Unlink only
after a successful rename, and check for the record inside the loop.

Source: P-fd-05 C2 review r2 L-5.

## FD-056 Wait deadline is not rechecked under the lock

**State:** OPEN. **Priority:** P3. **Filed:** 2026-11-26.

The wait loop checks its deadline before it takes `queue.lock`, so a decision
delayed by lock contention can admit after the deadline. Harmless for an
in-process caller; the socket agent must handle a late grant.

Source: P-fd-05 C2 review r2 L-6.

## FD-057 Admission subprocess tests: no timeout, weak two-process case

**State:** OPEN. **Priority:** P3. **Filed:** 2026-11-26.

A stalled child hangs the test run instead of failing it, and the two-process
admission test detects a missing lock only when the interleaving is unlucky. It
cannot fail spuriously.

Source: P-fd-05 C2 review r2 L-8.

## FD-058 Preserve queue order when merge gates pass an aged reservation

**State:** FIXED IN THIS BRANCH (C3a). **Priority:** P1. **Filed:** 2026-11-27.

`AdmissionBook._queue_allows` stops its scan as soon as it finds the aged
reservation holder. It could therefore miss a fitting merge-gate waiter that
must precede the candidate being polled, allowing admission order to depend on
which waiter reacquires `queue.lock` first. `_queue_allows` now continues
collecting queue-order blockers after identifying the reservation holder. The
holder remains the first aged, statically eligible waiter; admitted merge gates
may pass it only in the already-defined queue order. A regression test proves
that the second fitting merge gate cannot pass the first while the aged holder
is non-fitting.

Source: P-fd-05 C3a controller ruling for FD-058.

## FD-059 PSI-aware admission prediction

**State:** OPEN; design only, not implemented. **Priority:** P1. **Filed:** 2026-11-27.

The proposed sustained refusal uses `psi.memory.full_avg60_pct >= 10`; the 10%
threshold is operator-specified and has no supporting measurement yet. A missing
or unreadable full-avg60 value must refuse rather than become zero. This is
separate from the configured `policy.psi_refuse_some_avg10` check against
memory `some avg10`, and from the existing full-`avg10` admission check.

The predictive rule has no model or reclaimable-memory input today. It needs
`memory.available_bytes` (MemAvailable from `/proc/meminfo`, bytes), a separately
defined non-overlapping reclaimable-memory input (source unresolved, bytes),
`resources.declared_peak_bytes` (submitter declaration, bytes), or measured job
warm use (`memory.current - memory.stat[inactive_file]`, cgroup v2, bytes), and
`psi.memory.full_avg60_pct` (`/proc/pressure/memory`, percent). C3a must not
invent a prediction or treat missing facts as zero. The design requires an
observation-only calibration against real PSI and warm-use outcomes before any
predictive refusal is enabled; no coefficients, sample counts, or error bounds
have measurement support yet.

The pressure response is also design only. Estimate an actual RAM shortfall
from `psi.memory.{some,full}_avg{10,60}_pct` (`/proc/pressure/memory`, percent),
`memory.available_bytes` (`/proc/meminfo:MemAvailable`, bytes), refault evidence,
and admitted-job working sets (cgroup v2 `memory.current -
memory.stat[inactive_file]`, bytes). `host-facts/2` does not collect refault
counters or per-job working sets; the refault source and fact shape remain
unresolved. Select one freeze candidate whose freeze can cover the measured
shortfall, preferring the most recently admitted job when it covers it. Never
freeze all dev work or all jobs considered causal; `wings` and the game server
are never candidates. No formula, threshold or measurement-free constant is
chosen.

Thaw one job at a time only after the cause changes: a job finishes or freed
memory covers the frozen job's working set. Do not thaw because a pressure
number crosses a threshold. A freeze preserves the job and its charge; it does
not automatically stop, tear down, reschedule or move it. Moving depends on
progress, whether partial results resume, and another host being idle; fleetd
decides. If other hosts are busy, retain a `frozen-queued` state on the current
host.

This refines DESIGN §3.3's `memory full avg10 >= 3` admission-only sentence and
§3.5's frozen-time stall sentence. It clarifies that §4.3's cancel/TERM thaw and
stop sequence requires explicit cancellation or TERM, not a pressure freeze;
§3.4's `frozen_units > 0` check remains an admission refusal only.

Source: operator ruling for P-fd-05 C3a subtask 6 (2026-11-27).

## FD-060 Granted lease never binds while its connection stays alive

**State:** FIXED IN THIS BRANCH (C3a). **Priority:** P2. **Filed:** 2026-11-27.

The required positive `agent.toml.lease_bind_timeout_s` bounds the complete
bind frame from grant on the monotonic clock; trickled bytes do not extend the
deadline. Expiry tombstones before inspecting the exact name, releases the
charge only on confirmed absence, and keeps it on inspector error or presence.
The client receives an error and the connection ends. Late create attempts are
refused by the tombstone. The deadline ends when the bind frame completes.

Source: P-fd-05 C3a fix round 2 controller ruling (2026-11-27).

## FD-061 Persist executor result details for terminal status

**State:** FIXED IN THIS BRANCH (C3b-1). **Priority:** P2. **Filed:** 2026-11-27.

C3b-1 writes `verdict`, `exit_code`, `lane`, `api`, `collected_at`,
`result_sha256` and `result_size` into the durable terminal record before
removing the admission charge. `status` returns those details after restart.
Old C2 records remain readable; absent executor fields remain unknown. Cancel
of a terminal record remains a refusal. Completion-write failure leaves the
charge in place.

## FD-062 Retain and garbage-collect host upload objects

**State:** OPEN; C3b-2 follow-up. **Priority:** P2. **Filed:** 2026-11-27.

C3a bounds aggregate stored and in-flight upload bytes with
`policy.max_upload_store_bytes`, but it does not delete committed objects.
C3b must define retention and garbage collection for content-addressed upload
objects. Collection must preserve objects referenced by admitted or terminal
job records and coordinate with active uploads; no expiry or quota default is
defined here.

Source: P-fd-05 C3a round 2 controller ruling (2026-11-27).

## FD-063 Incrementally count large Assay progress files

**State:** OPEN. **Priority:** P2. **Filed:** 2026-11-27.

C3a bounds the Assay candidate-count scan to the opened file's 8 MiB snapshot.
Larger progress files report `done: null` with a reason rather than making the
host rescan an unbounded JSONL history on every progress request. A future
incremental design must preserve duplicate candidate de-duplication and the
no-follow descriptor safety of the current reader. No checkpoint format or
retention policy is defined here.

Source: P-fd-05 C3a fix round 2 controller ruling (2026-11-27).

## FD-064 Legacy job directories without EXIT remain charged after host reboot

**State:** OPEN. **Priority:** P2. **Filed:** 2026-11-27.

The admission provider charges every entry in `legacy_jobs_dir` without a
regular `EXIT` marker. If a host reboots while a legacy `run-job` command is
running, the command may never write that marker, so the directory continues
to consume its legacy RAM and planned-core charge after reboot. This package
does not infer completion or remove the directory; someone must remove the
stale directory for its charge to clear. Restart recovery and cleanup are
separate work.

Source: P-fd-05 C3b-2a controller ruling (2026-11-27).

## FD-065 Legacy-job scan observation and directory retention

**State:** OPEN. **Priority:** P2. **Filed:** 2026-11-27.

The admission scan lists the pinned `legacy_jobs_dir` twice and checks that the
configured path still names that directory. It does not coordinate with
`scripts/netcup/runner/run-job`, which does not take `queue.lock`: a job created
after the final listing is not seen until the next admission observation. The
jobs-directory owner is trusted to manage its own `EXIT` markers. Completed
job directories remain in the namespace and count toward
`MAX_LEGACY_JOB_ENTRIES` (65,536); an operator must prune them before the
ceiling or admission refuses with 75. No cleanup or writer protocol is part of
this package.

Source: P-fd-05 C3b-2a controller ruling F2/F5 (2026-11-27).

## FD-066 Docker process creation is synchronous and uncancellable

**State:** OPEN. **Priority:** P2. **Filed:** 2026-11-27.

`subprocess.Popen` creation is synchronous. While it is blocked, the caller
cannot cancel it, and the admission thread may remain occupied after its
`query_timeout_s` outer wait has refused. If creation later returns, the
provider immediately signals the new process group and makes a bounded reap
attempt. The deadline still bounds output collection, reaping and the answer
from the outer admission call; it cannot bound process creation itself.

Source: P-fd-05 C3b-2a fix round 2 controller ruling R2-2 (2026-11-27).

## FD-067 Close the short-lane event-observation race before enabling the executor

**State:** OPEN; BLOCKER BEFORE ENABLEMENT. **Priority:** P1. **Filed:** 2026-11-27.

The worker follows the host-owned event file while Run-Gate is running and
inspects and durably records each `lane-started` observation. If Run-Gate removes
a very short-lived container before the worker can inspect it, the observation
is an ERROR and the job cannot PASS. Close this race in Run-Gate or establish an
equivalent host-observable handoff before enabling executor submissions. Do not
relax the identity or checkout-mount checks.

Source: P-fd-05 C3b-1 fix round 1 controller ruling for H3 (2026-11-27).

## FD-068 Persistent exec cancellation cannot prove the in-runner process stopped

**State:** OPEN; cancellation remains `stopping`. **Priority:** P1. **Filed:** 2026-11-27.

The executor cannot prove that terminating the local `docker exec` client stopped
the process inside a persistent runner. Cancellation of `mode = "exec"` and an
end without the Run-Gate verdict proof therefore retain the checkout, containers
and admission charge. Define and test a host-observable stop protocol before
allowing either path to finalize.

Source: P-fd-05 C3b-1 fix round 1 controller ruling for H4 (2026-11-27).

## FD-069 Same-uid host lanes can modify the host-owned Run-Gate event file

**State:** OPEN; transport limit. **Priority:** P1. **Filed:** 2026-11-27.

The event file is outside the checkout and inaccessible to the lane container,
but a host-mode lane runs as the configured runner uid that owns the file. It can
rewrite that file and influence event binding. Before host-mode submissions are
enabled, move event ownership behind a host-only writer or provide an equivalent
mechanism that preserves the Run-Gate event contract.

Source: P-fd-05 C3b-1 fix round 1 controller ruling for M6 (2026-11-27).

## FD-070 Verify exec checkout mount against a persistent runner

**State:** OPEN; live probe required. **Priority:** P1. **Filed:** 2026-11-27.

The executor keeps the strict requirement that the inspected persistent runner
has a bind mount whose source is this job's checkout. No real persistent runner
has been probed to establish that the configured runner can satisfy that
per-job source. Keep exec-mode enablement behind a live acceptance probe; do not
weaken the mount check.

Source: P-fd-05 C3b-1 fix round 1 controller ruling for M7 (2026-11-27).

## FD-071 Preserve store collection failure detail in the host result

**State:** FIXED IN THIS BRANCH (C3b-1). **Priority:** P2. **Filed:** 2026-11-27.

Store transport/collection failures are included in the result's `collection`
detail while leaving the Run-Gate verdict unchanged. Retryable infrastructure
failures keep the job and charge in `stopping`; a semantic store failure is
recorded alongside the final lane verdict.

Source: P-fd-05 C3b-1 fix round 1 controller ruling for L5 (2026-11-27).

## FD-072 Bind Run-Gate repository and project labels to the request

**State:** FIXED IN THIS BRANCH (C3b-1). **Priority:** P2. **Filed:** 2026-11-27.

Container binding requires the `run-gate.repo` and `run-gate.project` label
values to equal the authenticated request, in addition to requiring the full
Run-Gate label set. This prevents a correctly named job/lane/commit container
from another repository or project from satisfying the binding.

Source: P-fd-05 C3b-1 fix round 1 controller ruling for L2 (2026-11-27).
