# Using fleetd

## Collect and validate host facts

The facts command is read-only. A fixture root gives the same parser and
collector behavior without reading the calling machine:

```sh
fleet facts collect --root ./host-root > host-facts.json
fleet facts validate host-facts.json
```

On a host, `fleet facts collect` requires `/etc/fleetd/facts.toml`. The file is
host-owned and must declare schema version 1, the control-plane slice, policy
limits, and source paths. `--root DIR` maps both this config path and the
config's absolute source paths beneath `DIR`; tests can collect facts without
reading the calling machine. The Geekbench JSON path is host-owned config;
fleetd reads the record and never invokes Geekbench. It exports only the
matching model/count score fields. A missing, stale, or invalid record leaves
`speed` as `null` with a reason. The JSON schema version is `host-facts/1`.

```toml
schema_version = 1

[paths]
geekbench_record = "/var/lib/mdt/host-facts/cpu-benchmark.json"
guard_config = "/etc/cgprofile/guard.toml"

[control_plane]
slice = "ctl.slice"

[policy]
overcommit = 1.5
oversubscribe_max = 1.25
psi_refuse_some_avg10 = 25
max_lane_containers = 8
```

`overcommit`, `oversubscribe_max`, and the PSI refusal threshold are explicit
host policy inputs, so an incomplete file fails with a field-specific error.
`host_role`, `max_lane_containers`, `guard_state`, and
`ksm_optin_shim` may be omitted; their facts stay `null` with a reason. The
collector reads guard config plus lexically ordered `.toml` drop-ins for its
digest and effective `enabled` value. It reports but does not apply guard
rules. It reads cgroup v2 controllers, the configured slice's `memory.min` and
`cpu.weight`, PSI avg10, swap counters, and KSM sysfs values. `memory.for_lanes`
is the raw `MemTotal` value; WP2 subtracts `control_plane.memory_min_bytes`
once when it calculates physical lane RAM. KSM swappiness-argument support is
unknown because the collector makes no write probe.

## Plan a candidate set

The collector permits omitted optional host_role and max_lane_containers facts
and records a reason. Planning requires both values, so collection can succeed
while the planner refuses an incomplete host configuration.

The candidate table identifies candidates and their source files. Each
candidate may carry its own measured RAM, elapsed seconds, measurement host,
and retry evidence. Measurements are optional; absent values fall through to
same-file and lane medians, then the lane demand declaration.

The input envelopes are closed and versioned. Candidate files use
candidate-table/1; host files use fleet-host-set/1. The host envelope
contains { "id", "facts", "other_running" } per host, with optional f_h.
facts is a validated host-facts/1 record from fleet facts collect.
other_running is always explicit (use [] when empty) and each row gives the
job id, expected memory charge, worker count, and container count. Every
running name present in facts.jobs must be represented. f_h defaults to 1.0
and must be in [0.5, 2.0]. The files
[candidate-table.valid.json](../tests/fixtures/candidate-table.valid.json)
and [fleet-host-set.valid.json](../tests/fixtures/fleet-host-set.valid.json)
show the exact input shapes.

The planner refuses a host whose role is shared-prod or unknown, as well as
hosts with unknown control-plane, memory, CPU, or PSI admission facts. The
collector can still report nullable optional facts with reasons; placement
fails closed when it needs one of them.

```sh
fleet plan r2 \
  --candidates candidates.json \
  --hosts hosts.json \
  --demand run-gate.toml
```

Normal output is stable JSON with host capacities, assigned candidates,
batches, and ETA. Add `--commands` to print shell-ready blocks. Each block
writes a batch's candidate IDs beneath `.assay/fleetd-plan/` and then invokes
the existing Run-Gate selector:

```sh
fleet plan r2 --candidates candidates.json --hosts hosts.json \
  --demand run-gate.toml --commands
```

The selected candidate files are consumed by Run-Gate as
`--only-candidates FILE`. Run each host's listed slots within the lane's
declared efficient job bound. An unplaceable candidate or a host refused by
the PSI admission threshold is reported with a reason, never silently dropped.
Commands are grouped by wave and slot. Slot commands in a wave may run
together; complete that wave before starting the next one. A retry command
includes --jobs 1. The command output contains no remote transport.
--batch-multiplier N changes the batch target from its default of
4 × worker_slots.

The host arithmetic uses P = memory.for_lanes_bytes -
control_plane.memory_min_bytes, O = other running charges + campaign_slots ×
instance_memory, and B = P × overcommit - O. Worker slots are the CPU-based
count after other workers, bounded by the host's configured container limit
and the lane's efficient upper bound. Shared placement also applies the
physical candidate cap: a candidate never exceeds P - O. Expected sums in
each wave, including an alone or retry wave, must fit B. Overcommit increases
only that sum budget; it never increases a candidate's physical cap.

RAM class is low when a candidate's expected charge is no greater than the
smallest host average budget b = B / W; all other candidates are high. The
planner places constrained and longer candidates first, honors each host's
mix limit where possible, and flags the assignment when no eligible host can
keep the mean within b. It builds batches around 4 × W, interleaving high and
low candidates. A candidate with memory-crash evidence is retried once on the
host with the largest physical alone cap, only if its charge also fits that
host's sum budget.

Elapsed measurements are normalized with the source host's single-core
score. Time is compared only when Geekbench major, exact version, and
baseline score agree. Host ETA applies baseline / single_core × f_h; multi-core
scores are not used. Missing or incomparable timing leaves placement intact
and reports ETA as unavailable with a reason.

The lane demand is a table in the existing Run-Gate TOML file:

```toml
schema_version = 1

[lanes.r2.demand]
unit = "candidate"
instance_memory = "250m"
unit_memory = "120m"
unit_memory_high = "600m"
unit_seconds_ref = 95
efficient_upper_bound = 3
```

The accepted units are `candidate`, `worker`, and `run`. Memory accepts
positive integer bytes with `B`, `k`, `m`, `g`, `kib`, `mib`, or `gib` suffixes.
See the [specification](SPEC.md) and [design choices](DESIGN-GUIDE.md).

## Validate guard rules

```sh
fleet guard validate /etc/cgprofile/guard.toml
```

This validates one rule file. It does not load drop-ins, signal cgprofiled,
freeze cgroups, or reclaim memory.
