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

The candidate table identifies candidates and their source files. Each
candidate may carry its own measured RAM, elapsed seconds, measurement host,
and retry evidence. Measurements are optional; absent values fall through to
same-file and lane medians, then the lane demand declaration.

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
