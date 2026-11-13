# fleetd design guide

## Why this package starts with facts and a planner

The compute-host design's first week is WP0, WP1(a), and WP2. That gives
operators useful per-host lists before a daemon exists. Fleetd owns no host
keys, transport, campaign execution, or verdicts. The next lifecycle package
must supply those through the accepted CIU, Run-Gate, and Assay contracts.

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
factor per lane or hardware flavor. ETA is informational and cannot reject or
time out work.

Time samples are comparable only when the hosts share a Geekbench major,
exact version, and baseline score. Refused hosts do not set the ETA reference,
so an ineligible machine cannot make ETA unavailable for eligible hosts. A
missing or mismatched benchmark among eligible hosts keeps RAM placement
available but makes ETA unavailable with a reason. Placement then balances
normalized reference work per worker as a deterministic tie-break. This is not
a speed comparison or ETA. A candidate time record from a host absent in the
current host set falls back to the next timing source and carries a reason.

The planner calculates P = memory.for_lanes_bytes -
control_plane.memory_min_bytes, O = other running expected charges +
campaign_slots × instance_memory, and B = P × overcommit - O. It derives
worker slots from CPU capacity after other running workers and bounds them by
`efficient_upper_bound`. This manual plan uses one campaign slot per host;
parallel campaign slots require the future supervisor because same-lane
selected Run-Gate calls share an in-flight record and Assay store. For a host
with W slots, b = B /
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
does not run the benchmark. A missing fact stays `null` with a reason, and a
planner that needs that fact refuses rather than inventing it.

Collection requires versioned `/etc/fleetd/facts.toml` policy and source-path
configuration. `HOST_ROLE` is read from the configured host-setup environment
file, not duplicated in fleetd policy; unknown or invalid roles remain null
with a reason and cannot receive work. The CPU lane count comes from the
online CPU list, and `memory.for_lanes_bytes` is raw `MemTotal`; the planner
subtracts the configured control-plane slice's `memory.min` once. The design text also describes
subtracting that reserve while constructing facts (§3.6); this package uses
the explicit §4.3 formula and keeps the raw counter in facts to avoid a double
subtraction.

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
The unimplemented Run-Gate client verb is tracked as
[RG-276](../../run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md#rg-276-the-run-gate-campaign-plan-client-is-not-implemented).

## Deliberate limits

The guard rules are parsed only; no process or cgroup is changed. There is no
daemon, socket, SSH, Docker, transport, remote collection, retry supervision,
or strict merge implementation. Guard configuration accepts the design's
`report` and `enforce` modes and the `least-progress-per-ram` victim policy, but
this package does not act on them. The current Run-Gate selected-candidate
interface also does not accept fleetd's host-specific RAM budget; the printed
commands use the planned worker count while runtime limits remain host and lane
policy. Track this integration under
[FD-003](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-003-fleet-ram-budget-is-not-sent-to-the-current-runner).
Released Run-Gate does not yet accept the
`demand` lane key, so the lane demand lives in a separate TOML input until
RG-275 lands; the missing client verb is tracked as RG-276. `efficient_upper_bound`
must match Assay's authoritative `judge.mutation.jobs_max`, tracked as
[FD-002](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-002-demand-upper-bound-is-not-joined-to-assay-inventory). See
[FD-001](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-001-fleet-level-campaign-lifecycle-is-not-implemented).
