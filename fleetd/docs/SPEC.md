# fleetd specification

## Scope

The first-week package contains four strict versioned contracts, a facts
collector with an injectable filesystem root, and a pure deterministic campaign
planner. It does not run campaigns or communicate with hosts.

## Contracts

- `host-facts/1` carries CPU, Geekbench, memory, PSI, cgroup v2, KSM, and
  kernel-interface, control-plane, guard, policy, job, and tool facts. When a
  job or tool inventory is not available, that object is `null` with a
  non-empty sibling `jobs_reason` or `tools_reason`. A missing nullable leaf
  must carry a non-empty sibling `<field>_reason`.
- `work-request/1` carries the lane, commit and image identity, floors, demand,
  planned and pending candidate IDs, and optional carry-over provenance.
- `assay-merge-manifest/1` maps each planned candidate ID to exactly one owner
  store. The owner is authoritative; copies in other stores are ignored.
- `guard.toml` accepts only the host-owned keys and closed vocabularies from
  the compute-host design. This package parses and validates the rules; it does
  not apply them.

Unknown fields are rejected at every object boundary. Validators identify the
field path and the missing, extra, or invalid value. See the [consumer guide](CONSUMERS.md)
for examples and the [design guide](DESIGN-GUIDE.md) for invariants.
Every candidate ID in candidate tables, work requests, and merge manifests is
the lowercase 64-character SHA-256 digest emitted by native Assay R2.

### Lane demand

A TOML file passed to `--demand` carries a `[lanes.<lane>.demand]` table (released
Run-Gate does not yet accept that key in its own file; see RG-276) with these
required keys: `unit`, `instance_memory`, `unit_memory`, `unit_seconds_ref`,
and `efficient_upper_bound`. `unit_memory_high` is optional. Units are the
closed set `candidate`, `worker`, and `run`; the CLI converts memory strings
using binary k/m/g suffixes. This R2 candidate planner requires
`unit = "candidate"` and refuses the other valid schema units because their
placement meaning is not defined yet. The high estimate cannot be lower than
the median estimate. Unknown keys are refused.

### Merge ownership

`assay-merge-manifest/1` has `planned_ids` and an `owners` object. The planned
ID list must be unique, and its set must exactly equal the owner keys. For each
ID, only the record in its named owner store is selected. A record with the
same ID in any other store is ignored. A missing record from the owner store
is an error.

## Facts collection

`fleet facts collect [--root DIR]` reads `/proc` and `/sys` beneath the
selected root, reads required versioned configuration at
`/etc/fleetd/facts.toml`, reads the Geekbench record from its configured
host-owned path, and emits `host-facts/1` JSON. The config declares
`overcommit`, `oversubscribe_max`, PSI refusal threshold, control-plane slice,
and host source paths; missing required policy fails. `HOST_ROLE` is read from
the configured host-setup environment file, an existing host-owned source, and
is not shadowed in fleetd policy. `host-facts/1` accepts `dedicated-dev` and
`shared-prod` as host roles; missing, duplicate, empty, or unknown values are
represented as `null` with a reason. Optional facts whose source is absent are
also represented as `null` with a reason. Collection is read-only: it never
invokes Geekbench or writes sysfs/cgroup files.

The collector exports only the benchmark's matching model and online-CPU
count. It accepts measured Geekbench 6/7 records with their matching reference
score, and never uses a table fallback. Guard config is validated after
lexically merging TOML drop-ins; its digest covers the main file and drop-ins.
The guard runtime state and KSM opt-in shim are nullable unless their paths are
configured. `memory.for_lanes_bytes` is raw `MemTotal`; the planner applies the
control-plane `memory.min` reserve once per §4.3. `fleet facts validate FILE`
validates a serialized record without contacting the host.

## Planning

`fleet plan` consumes a candidate table, host facts plus per-host correction
factors, and a Run-Gate lane demand table. RAM determines eligibility and
candidate routing. Expected charges are compared with the overcommitted sum
budget; individual candidates are always bounded by physical RAM after the
control-plane reserve. Lists prefer a low/high mix and batch around four times
the host worker capacity. Retry candidates with recorded memory evidence are
assigned alone to the host with the largest physical alone-capacity.

The planner uses eligible hosts' Geekbench single-core scores for normalized
time and ETA, never multi-core scores. A refused host does not change the ETA
reference. One bounded correction factor is read per host, with
the design default `1.0`; factors are not learned or pooled by lane in this
package. Identical inputs produce byte-identical JSON and stable command
ordering.
When no host is eligible, `time_reference.reason` says so separately from the
case where eligible hosts have no Geekbench records.

The planner's host-set input requires explicit other-running rows and rejects
hosts with unknown role, shared-prod role, PSI above its configured threshold,
or unknown capacity facts. Worker capacity is the CPU-derived worker count
after other work, bounded by the lane's efficient upper bound. This manual MVP
uses one campaign slot per host because concurrent selected runs would share a
Run-Gate lane instance and Assay store. It computes:

- P = memory.for_lanes_bytes - control_plane.memory_min_bytes;
- O = other running expected charges + campaign_slots × instance_memory;
- B = P × overcommit - O;
- b = B / W for W worker slots;
- cap = min(B - (W - 1) × ram_low, P - O - (W - 1) × ram_low);
- cap_alone = P - O.

Each wave's expected charge must be no greater than B. Each individual
candidate must fit cap_alone, including alone and retry batches. Retry goes
only to the host with the largest cap_alone and must also fit that host's B.
Overcommit applies to expected sums and never enlarges the physical cap.

Candidate and host inputs use candidate-table/1 and fleet-host-set/1. Candidate
measurements resolve by own id, same-file median, lane median, then declared
demand. Candidate timing follows the same precedence after normalization to
the common Geekbench major, exact version, and baseline. If timing is
incomparable, placement still uses RAM and the output includes an ETA reason.
When no comparable ETA exists, assignment balances normalized reference work
per worker as a placement tie-break; it does not claim that the hosts have
comparable speeds. An old measurement whose host is absent from the host set is
ignored for timing and recorded with a fallback reason.
The batch target defaults to four times W and is configurable with
--batch-multiplier. The command renderer emits one invocation per host batch,
with `--jobs` no greater than W, and runs batches serially within each copied
host section. Selected-only runs normally return Run-Gate exit 4 (`PARTIAL`);
the generated script accepts 0 or 4 and stops on other codes. `efficient_upper_bound`
must not exceed the lane's Assay `jobs_max`; the standalone planner cannot
derive that value until the Run-Gate campaign client is implemented.

This package deliberately omits the Run-Gate campaign-plan client command;
the integration contract is tracked under
[RG-276](../../run-gate-project/KNOWN_ISSUES_TODO_BACKLOG.md#rg-276-the-run-gate-campaign-plan-client-is-not-implemented).
