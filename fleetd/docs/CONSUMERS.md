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
host_setup_env = "/etc/mdt/host-setup.env"
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
The host role is read from `host_setup_env`, which points to the existing
host-owned `HOST_ROLE` setting. `max_lane_containers`, `guard_state`, and
`ksm_optin_shim` may be omitted; their facts stay `null` with a reason. The
collector reads guard config plus lexically ordered `.toml` drop-ins for its
digest and effective `enabled` value. It reports but does not apply guard
rules. It reads cgroup v2 controllers, the configured slice's `memory.min` and
`cpu.weight`, PSI avg10, swap counters, and KSM sysfs values. `memory.for_lanes`
is the raw `MemTotal` value; WP2 subtracts `control_plane.memory_min_bytes`
once when it calculates physical lane RAM. KSM swappiness-argument support is
unknown because the collector makes no write probe.

## Plan a candidate set

The collector records a reason when the host role source or
`max_lane_containers` is unavailable. Planning requires both values, so
collection can succeed while the planner refuses an incomplete host
configuration. Unknown role strings are also rejected; only `dedicated-dev`
and `shared-prod` are accepted.

The candidate table identifies candidates and their source files. Candidate
IDs must be the lowercase 64-character SHA-256 digests emitted by native
Assay R2. Each candidate may carry its own measured RAM, elapsed seconds,
measurement host, and retry evidence. Measurements are optional; absent
values fall through to same-file and lane medians, then the lane demand
declaration.

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
  --demand lane-demand.toml
```

Normal output is stable JSON with host capacities, assigned candidates,
batches, and ETA. Add `--commands` to print self-contained host sections. Each
section writes one batch's candidate IDs beneath `.assay/fleetd-plan/` and
then invokes the existing Run-Gate selector:

```sh
fleet plan r2 --candidates candidates.json --hosts hosts.json \
  --demand lane-demand.toml --commands
```

The selected candidate files are consumed by Run-Gate as
`--only-candidates FILE`. A section has one command per batch, with
`--jobs` no greater than the planned worker slots. Batches run serially because
selected-only invocations in one checkout share the lane's in-flight record
and Assay state store. A retry command uses `--jobs 1`. Run only the matching
`# host <id>` section from that host's lane checkout; the output has no
transport and running every section in one shell sends them all to that one
machine. `fleet plan` itself writes no files; the copied section creates the
candidate files beneath `.assay/fleetd-plan/`.

Run-Gate marks an incomplete `--only-candidates` selection `PARTIAL` and exits
4. The generated script accepts exit 0 or 4 so the next batch runs, and stops
on any other result. An unplaceable candidate or a host refused by the PSI
admission threshold is reported in the plan with a reason, never silently
dropped. Each batch keeps the design's wave accounting in JSON, while one
selected-only invocation pays the baseline once for that batch.

The planned `--jobs` value is bounded by the lane's efficient upper bound.
Run-Gate can refuse `--jobs > 1` when the lane does not use a supported cheap
clone workspace. Fleetd's host-specific `B_h` is not passed through the
current selected-candidate interface; the command does not enforce that
per-host budget. Runtime admission is still provided by the host and lane's
existing Run-Gate, Assay, and cgroup limits (FD-003). The future Run-Gate
campaign client must pass the host budget into Assay admission or establish an
equivalent enforced bound before treating the planner's wave proof as an
execution guarantee.

After all host sections finish, copy each host's whole same-judge Assay state
store beneath the final checkout's ignored `.run-gate/` directory. Then run a
whole-lane closeout without `--only-candidates`; Run-Gate merges the copied
records and executes any remaining candidates before writing the complete
verdict:

```sh
run-gate r2 \
  --merge-state .run-gate/collected/host-small \
  --merge-state .run-gate/collected/host-large
```

Replace the example host names with the hosts in the plan. The source paths
must be directories beneath the checkout or judged worktree. `--batch-multiplier N`
changes the batch target from its default of 4 × worker slots.

The host arithmetic uses P = memory.for_lanes_bytes -
control_plane.memory_min_bytes, O = other running charges + one campaign
instance's `instance_memory`, and B = P × overcommit - O. Worker slots are the
CPU-based count after other workers, bounded by the lane's
`efficient_upper_bound`. The manual planner reserves one campaign slot per
host; parallel slots need the future supervisor. Shared placement also applies the
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

The lane demand is a `[lanes.<lane>.demand]` table. Released Run-Gate (25.6.0)
refuses `demand` as an unknown lane key, so do not add it to a project's
`run-gate.toml` yet; keep it in a separate TOML file that you pass to
`--demand`. Run-Gate will accept the table in its own file when RG-275 lands
(see [RG-276](../../run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md#rg-276-the-run-gate-campaign-plan-client-is-not-implemented)).
The example below is such a standalone file:

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

Keep `efficient_upper_bound` at or below the native R2 lane's
`judge.mutation.jobs_max`. The standalone planner validates and uses its demand
file but cannot derive that authoritative lane value yet; the follow-on Run-Gate
client will derive or refuse a mismatch (see [FD-002](../KNOWN_ISSUES_TODO_BACKLOG.md#fd-002-demand-upper-bound-is-not-joined-to-assay-inventory)).

The schema accepts `candidate`, `worker`, and `run`, but this R2 candidate
planner currently requires `unit = "candidate"`; it refuses the other valid
schema units until their placement meaning is defined. Memory accepts positive
integer bytes with `B`, `k`, `m`, `g`, `kib`, `mib`, or `gib` suffixes.
See the [specification](SPEC.md) and [design choices](DESIGN-GUIDE.md).

## Validate guard rules

```sh
fleet guard validate /etc/cgprofile/guard.toml
```

This validates one rule file. It does not load drop-ins, signal cgprofiled,
freeze cgroups, or reclaim memory.
