# Design: one explicit whole-disk partition plan

Status: implemented. This is the rationale and sizing contract for the public
`partitioning` config described in the [README](../README.md#disk-layout).
Adoption examples are in [CONSUMERS.md](CONSUMERS.md#disk-plan-and-offline-preview).

## Contract

The installer derives one ordered GPT plan from the config, disk size, root
start sector, and swap policy. It does not infer a layout from an older schema.
Removed keys (`root_resize_policy`, `disk_layout`, `root_max_size_gb`,
`data_area`, `data_partition`, and `data_lvm`) are refused. There are no old
layout or benchmark paths.

```json
{
  "partitioning": {
    "root": "*",
    "reserve_gb": 0,
    "iobench_raw_gb": 8,
    "data": {
      "kind": "partitions",
      "volumes": [
        {"size": "*", "fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW"}
      ]
    }
  },
  "swap_policy": "on",
  "swap_disk_total_gb": 16,
  "swap_partition_count": 4
}
```

`partitioning` is optional. Its defaults are `root: "*"`, `reserve_gb: 0`,
and `iobench_raw_gb: 8`; no data volume is created unless `data` is supplied.
The raw benchmark partition can be set to another integer size from 2 through
10240 GiB, or explicitly disabled with `false`. Its reserved GPT PARTLABEL is
`iobench-raw`. When enabled, it is always present in the plan, whether or not
`run_io_benchmark` is enabled.

## Pool boundaries and ordering

The first planned swap start is the exclusive end of the root/data pool when
tail swap exists. With `swap_policy: "off"`, or when retained pre-root swap
already satisfies the target and no tail swap is needed, the exclusive pool
end is the aligned last usable GPT sector. That calculation reserves GPT's
backup header and entry array. Both the swap planner and data planner call the
same boundary function.

The physical order after root is data partitions (in config order), then the
intentional unallocated reserve, then `iobench-raw`, then end-of-disk swap.
Numbers increase in that same physical order; preserved partitions before
root keep their current numbers. No partition extends past the shared pool
boundary or the last usable GPT sector. Alignment is 1 MiB; percentage
rounding remainder is absorbed by the final percentage entry. The sole `*`
absorbs the remaining allocation after fixed entries and reserve.

The raw partition is a planned Linux partition with PARTLABEL `iobench-raw`.
It is never formatted, mounted, or activated as swap. When the optional io.cost
benchmark is enabled, stage two verifies its label and geometry, confirms it
is neither mounted nor active swap, wipes signatures with `wipefs -a`, and
runs `iobench` directly on that partition. It remains reserved and raw after
the benchmark. There is no temporary-tail or second benchmark allocation
path. The configured partition size is large enough for the benchmark tool's
minimum-device policy; no scratch filesystem or deletion table write is
needed.

## Size rules and static validation

Pool sizes are integer GiB, percentages from 0.01% to 100.00% (up to two
decimal places), or `"*"`; omitted data size means `"*"`. Root also accepts
`"keep"`, meaning preserve its current size exactly. The only supported swap
policies are `on` and `off`, so the plan always knows the pool boundary.

At config load and `build-customscript`, the schema rejects impossible shapes:

- fixed GiB sizes need exactly one growable `"*"` or `reserve_gb: "*"`;
- percentage sizes cannot be mixed with fixed root/data sizes and must total
  exactly 100% unless a single `"*"` absorbs the remainder; a numeric fixed
  `reserve_gb` is allowed and is removed before the percentage pool is
  calculated;
- percentages cannot be combined with `reserve_gb: "*"` because that reserve
  would make the percentage pool unknown;
- duplicate stars, invalid labels, unsafe mounts, and out-of-range values are
  refused before a bundle is built.

Disk fit, minimum sizes, and GPT numbering are checked when the disk facts are
available. Use `debian-install-v2.py plan --config FILE --disk-size SIZE`
for an offline resolved GiB table. Automatic swap sizing also requires
`--ram-gib`; a `root: "keep"` plan requires `--root-current-size-gib`. Online
`plan` output and install refusals include the resolved table whenever the
disk facts and plan can be resolved. If a plan cannot fit, the refusal shows
the usable capacity and requested raw/swap sizes.

The offline resolver assumes root is partition 1 at sector 2048 by default
and omits preserved pre-root partition geometry. Override the root start with
`--root-start-sector` and root number with `--root-number` to match the target.
For the saved netcup layout use `--root-start-sector 2500608 --root-number 3`.
Live planning reads the actual partition geometry and preserves eligible
pre-root partitions.

## Root safety and table writes

`root: "keep"` uses the live root size; a numeric or percentage root is the
planned target; `"*"` fills the available root allocation. A larger target is
applied after the new free-space/swap boundary exists, with `sfdisk -N` for
only the root partition followed by `partx -u` and exact geometry checks.
This avoids rewriting an enlarged mounted-root entry as part of the full
partition table. A smaller target uses the existing offline ext4 shrink hook,
which verifies current usage and filesystem minimum plus the safety margin.

Stage two writes one crash-safe table containing data/raw/swap partitions,
then runs the install benchmark against the final raw partition. It applies
the io.cost model from that benchmark result before growing root, formatting
or mounting configured data volumes, and activating swap. The `sfdisk -N`
root-growth step is documented in [CHANGES.md](../CHANGES.md).

## Mount policy

Data mounts are validated during config load. The exact paths `/`, `/var`,
`/var/lib`, `/srv`, and `/home` are refused. A mount equal to, inside, or
above a protected OS-state tree is also refused: `/usr`, `/etc`, `/boot`,
`/root`, `/tmp`, `/proc`, `/sys`, `/dev`, `/run`, `/bin`, `/sbin`, `/lib`,
`/var/lib/dpkg`, `/var/lib/apt`, `/var/cache/apt`, `/var/log`,
`/srv/tree-cache`, and `/srv/tree-upper`. Refusals name the requested mount
and the protected path it would hide. Data children such as
`/var/lib/docker`, `/var/lib/containerd`, `/srv/cow`, `/srv/data`, and
`/home/runner` are allowed when they do not cover one of those protected
trees. The installer does not pre-populate protected system filesystems.

Mounts must be absolute, cannot collide with each other, and are written to
fstab by UUID. A mount equal to or containing `/var/lib/docker` gets a systemd
dependency so Docker waits for it.

## Partition recognition and limits

Configured data partitions are recognized only by both Linux partition type
and exact PARTLABEL. A matching filesystem label alone is never enough. The
`iobench-raw` partition is also checked by PARTLABEL, type, start, and size
before it can be wiped. Config load refuses a statically oversized partition
count; the offline and live planners also check the actual root number and
refuse any table that would exceed GPT's 128-entry limit before a table write.

The runner locates the planned CoW partition using PARTLABEL `MDT_COW`, so its
partition number may change when the plan changes. It never creates or carves
a partition itself.
