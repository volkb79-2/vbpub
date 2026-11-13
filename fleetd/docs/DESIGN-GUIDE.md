# fleetd design guide

## Why MVP-B-lite uses one-shot control

MVP-A supplied facts, contracts, and a deterministic planner. MVP-B-lite adds
the smallest campaign loop on top of the existing transport and runner: a
`Transport` boundary, named remote jobs, local JSON state, one-step scheduling,
and combined reporting. The controller calls the identity-checked `hx.sh`
wrapper; the configured host aliases stay in local operator configuration.
Fleetd contains no SSH client, host address, key, provider token, daemon,
socket, or lease protocol.

This package deliberately keeps host placement in the planner. It starts at
most the planned number of campaign slots per host (currently one), then
returns. A controller can call `status` to inspect progress and ETA and `tick`
to schedule one next step. The persistent runner checkout and scripts must
already exist on each selected host.

## RAM is the placement constraint

Lane caps bound runaway containers; they are not expected worker charges.
Expected charges may sum against the configured 1.5 overcommit budget, but a
single candidate is compared to physical lane RAM after the control-plane
reserve. A candidate that cannot fit physically is unplaceable. Low and high
RAM work is mixed to keep the average expected charge near each host's budget.

## Geekbench is only a time estimate

The planner normalizes serial candidate time with the Geekbench single-core
score and the declared reference score. It multiplies by one stored factor per
host, defaulting to `1.0`. It never divides by multi-core score and never fits a
factor per lane or hardware flavor. Learning the host EWMA and skipping
self-predicted samples are tracked under
[FD-005](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-005-learn-the-per-host-correction-factor).
ETA is informational and cannot reject or time out work.

Time samples are comparable only when the hosts share a Geekbench major,
exact version, and baseline score. Refused hosts do not set the ETA reference,
so an ineligible machine cannot make ETA unavailable for eligible hosts. A
missing or mismatched benchmark among eligible hosts keeps RAM placement
available but makes ETA unavailable with a reason. Placement then balances
normalized reference work per worker as a deterministic tie-break. This is not
a speed comparison or ETA. A candidate time record from a host absent in the
current host set falls back to the next timing source and carries a reason.

The planner computes `reserve_h = max(reported ctl.slice memory.min, design
default)`, `P = memory.for_lanes_bytes - reserve_h`, O = other running expected
charges + campaign_slots × instance_memory, and B = P × overcommit - O. It
derives worker slots from CPU capacity after other running workers and bounds them by
`efficient_upper_bound`. This manual plan uses one campaign slot per host;
parallel campaign slots require the future supervisor because same-lane
selected Run-Gate calls share an in-flight record and Assay store; this
accepted capacity limit is tracked under
[FD-006](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-006-lift-the-manual-one-slot-per-host-and-j-cap).
The design default is `clamp(256 MiB + ceil(6% × MemTotal), 768 MiB, 2 GiB)`;
an absent slice or a report below that default uses `design-default`, while a
present report at or above it uses `reported`. Each host plan records
`reserve_bytes` and `reserve_source`; an unknown MemTotal still refuses the
host. The measured-p95 rule remains follow-up work (FD-007). For a host with W
slots, b = B /
W; a candidate's shared cap is the smaller of B - (W - 1) × ram_low and
P - O - (W - 1) × ram_low. A candidate that runs alone or as a retry must
also fit the host's sum budget B and physical alone cap P - O. Thus
overcommit never raises the physical cap, and every running wave's expected
charge stays within B.

Placement refuses a host with unknown role, explicit shared-prod role, PSI
above the configured admission threshold, or missing facts needed for a safe
RAM/CPU budget. A missing max_lane_containers is not guessed.

## Facts are read from their owner

The collector accepts a root directory so tests use synthetic `/proc` and
`/sys` trees. Production collection reads the host-owned Geekbench record; it
does not run the benchmark. Missing facts stay `null` with reasons. Planning
uses the design's control-plane reserve default when the slice or its
`memory.min` is not yet installed; other required capacity facts still fail
closed.

Collection requires versioned `/etc/fleetd/facts.toml` policy and source-path
configuration. `HOST_ROLE` is read from the configured host-setup environment
file, not duplicated in fleetd policy; unknown or invalid roles remain null
with a reason and cannot receive work. The CPU lane count comes from the
online CPU list, and `memory.for_lanes_bytes` is raw `MemTotal`. WP2 computes
`reserve_h = max(reported memory.min, clamp(256 MiB + ceil(6% × MemTotal),
768 MiB, 2 GiB))` and subtracts that reserve once from raw lane memory. For a
present slice, a report at or above the default is used; an absent slice,
missing value, or smaller report uses the clamped default. Host plans show
`reserve_bytes` and `reserve_source`; reported values above 2 GiB remain
honored because that cap is for the default. The design's later
`1.25 × measured p95` replacement is left for follow-up work (FD-007).

The demand schema validates `candidate`, `worker`, and `run` as its closed
vocabulary. This R2 candidate planner only accepts `unit = "candidate"`;
other units fail with a clear input error until their placement meaning is
specified.

## Determinism and manual command delivery

Candidate, host, batch, and command ordering use explicit stable keys. JSON
contains no generated timestamp. `--commands` prints self-contained sections;
each writes one batch ID file in the checkout and invokes Run-Gate once with
`--only-candidates` and the planned `--jobs` value. Batches run serially in
the section. Candidate selection normally returns exit 4 because other campaign
candidates were not selected (the claim is `PARTIAL`; the code is Run-Gate's
`BUDGET_EXCEEDED`, so a genuinely exhausted batch budget looks the same).
Generated scripts accept 0 or 4 and stop on any other result; the whole-lane
closeout finishes any selected candidate that did not complete. Run only the
matching host section from that host's lane checkout. Copy each host's whole
Assay store (the path each run prints as `run-gate: state directory:`)
beneath the final checkout's ignored `.run-gate/collected/` directory, then run the lane without
`--only-candidates`, using repeatable `--merge-state` paths, to write the
whole-lane verdict. The planner does not copy stores or provide transport.
Every host must use the same lane commit and tester image; `--merge-state`
ignores records from a different judge identity, so compare the closeout's
candidate counts with the planned list before trusting its ETA. See the
[consumer guide](CONSUMERS.md) for the exact store-path rules.
The unimplemented Run-Gate plan client is tracked as
[RG-276](../../run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md#rg-276-the-run-gate-campaign-plan-client-is-not-implemented).

## Named jobs and restart recovery

Each planned batch name is
`<campaign>.<host>.s<slot>.b<n>.<list8>`. `list8` is the first eight hex
characters of SHA-256 over the sorted candidate IDs, each followed by a
newline. Before starting, fleetd probes the runner's job table and compares
the full list digest. An existing matching name is adopted; the same name with
a different or unverifiable list refuses. A changed candidate list therefore
has a new name, and the old job is stopped before replacement work starts.

Fleetd delegates start and stop to the runner's `run-job` and
`run-r2-campaign.sh` commands. The real transport shells out to the local
identity-checked `scripts/controller/hx.sh` wrapper. Tests use an in-memory
fake with a controllable clock, job table, progress files, and unreachable or
slow hosts; tests do not start a host, SSH client, or Docker container.

The controller keeps `fleet-campaign-state/1` JSON outside Git and updates it
with a temporary file plus rename. A restarted controller probes by job name
and re-adopts existing work. There are no leases. On a failed probe, stall
time pauses for that host. More than 30 minutes without contact marks the host
lost; `tick` re-plans only unfinished candidate IDs on the reachable planned
host with the largest `cap_alone_bytes`. If the original host later returns,
the duplicate run may finish too; collection chooses one owner per candidate.

The fleetd stall threshold is `max(900 seconds, 3 × p95)` from that host's
observed per-candidate elapsed times. It measures time since that batch last
finished a candidate and never imposes a campaign wall budget. The existing
runner also has its own no-progress attempt stop; an exited job is handled by
the next `tick` as an unfinished batch. The runner policy is not changed here.

## Collection and ownership

`collect` fetches each known progress stream and batch verdict. Per candidate,
the earliest valid finished event owns the result; later events are ignored
and counted. If timestamps tie or are absent, stable host and job ordering
keeps the report deterministic. The collector counts `killed`, `survived`,
`timeout` (`budget_exceeded` and `hung`), `crashed`, `equivalent`, and
`missing`, and reports artifact gaps. Per-host wall time and observed candidate
rate accompany the one-owner table.

The lightweight collector does not merge Assay state stores. Assay's strict
`--merge-manifest` and Run-Gate forwarding are tracked under Assay B544 and
Run-Gate RG-277; the exact-key Assay baseline cache is already tracked as
[B542](../../assay/nyxloom-trove/4-backlog.md#b542-r2-restarts-repeat-a-passing-single-core-baseline-and-leave-a-serial-retry-tail).

## Deliberate limits

The guard rules are parsed only; no process or cgroup is changed. There is no
daemon, socket, Docker, retry service, or strict Assay store merge. Guard configuration accepts the design's
`report` and `enforce` modes and the `least-progress-per-ram` victim policy, but
this package does not act on them. The future daemon is fleetd FD-011; the
campaign-aware cgprofile guard is CP-64 in cgprofile's backlog. The current Run-Gate selected-candidate
interface also does not accept fleetd's host-specific RAM budget; the printed
commands use the planned worker count while runtime limits remain host and lane
policy. Track this integration under
[FD-003](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-003-fleet-ram-budget-is-not-sent-to-the-current-runner).
The restricted forced-command entry for campaign hosts remains CIU-218. The
strict result merge remains fleetd FD-008, Assay B544, and Run-Gate RG-277.
Released Run-Gate does not yet accept the
`demand` lane key, so the lane demand lives in a separate TOML input until
RG-275 lands; the missing client verb is tracked as RG-276. `efficient_upper_bound`
must match Assay's authoritative `judge.mutation.jobs_max`, tracked as
[FD-002](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-002-demand-upper-bound-is-not-joined-to-assay-inventory). See
[FD-001](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-001-lite-campaign-lifecycle).
