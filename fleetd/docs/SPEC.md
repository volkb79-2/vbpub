# fleetd specification

## Scope

The first-week package contains four strict versioned contracts, a facts
collector with an injectable filesystem root, and a pure deterministic campaign
planner. It does not run campaigns or communicate with hosts.

## Contracts

- `host-facts/1` carries CPU, Geekbench, memory, PSI, cgroup v2, KSM, and
  control-plane facts. A missing nullable leaf must carry a non-empty sibling
  `<field>_reason`.
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

## Facts collection

`fleet facts collect [--root DIR]` reads `/proc` and `/sys` beneath the
selected root, reads the Geekbench record from the host-owned config, and emits
`host-facts/1` JSON. Missing source data is represented as `null` with a
reason. Collection is read-only. `fleet facts validate FILE` validates a
serialized record without contacting the host.

## Planning

`fleet plan` consumes a candidate table, host facts plus per-host correction
factors, and a Run-Gate lane demand table. RAM determines eligibility and
candidate routing. Expected charges are compared with the overcommitted sum
budget; individual candidates are always bounded by physical RAM after the
control-plane reserve. Lists prefer a low/high mix and batch around four times
the host worker capacity. Retry candidates with recorded memory evidence are
assigned alone to the host with the largest physical alone-capacity.

The planner uses Geekbench single-core scores for normalized time and ETA,
never multi-core scores. One bounded correction factor is read per host, with
the design default `1.0`; factors are not learned or pooled by lane in this
package. Identical inputs produce byte-identical JSON and stable command
ordering.
