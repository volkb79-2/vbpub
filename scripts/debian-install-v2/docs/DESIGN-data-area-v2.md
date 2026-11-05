# Design: explicit disk plan (`partitioning`), partition numbers follow disk position, no hidden gap

Status: DESIGN ONLY, awaiting the controller's adversarial schema review. Nothing is implemented yet.
Operator directive 2026-11-05 (v1001 showed root 160G, swap vda4-7, data vda8 physically BEFORE the swaps, 166.8 GiB hole).
Operator update: NO backwards compatibility. The old keys are deleted, not migrated; one clean schema.

## What is deleted

* `disk_layout` (`full-root`/`dev-host`) and `layout_policy.recommended_root_gib`/`recommended_layout`/`min_root_gib`/
  `validate_root_size` (rule sizing: 5/16 root, 1/3 data, 20% root floor).
* `root_max_size_gb`, `data_area`, `data_partition`, `data_lvm` and every sub-key (`size_fraction`, `pv_size_*`, ...).
* The "capped root needed" prerequisite and the unallocated "reserve" between root and data.
* A config containing a deleted key is refused with: `<key> was removed; use partitioning (see docs/DESIGN-data-area-v2.md)`.
  (The existing unknown-key handling stays; this is only a more helpful message. No translation code.)

## Principles

1. The layout depends ONLY on the config: `partitioning` + the existing `swap_*` keys.
2. The plan must account for the WHOLE disk. Default reserve is 0. A plan that leaves space over is refused unless
   `reserve_gb` is given explicitly.
3. Partition numbers ascend with disk position: `1 efi, 2 boot, 3 root, 4.. data, then swaps`. Swap is last.
4. Swap is defined separately (`swap_policy`, `swap_disk_total_gb`, `swap_partition_count`) and is subtracted FIRST.

## Schema

```
"partitioning": {                       # optional; absent == {"root": "*"} (root takes everything not used by swap)
  "root":       "*" | 160 | "160" | "40%",      # default "*"
  "reserve_gb": 0,                              # default 0; explicit unallocated GiB, placed directly in front of swap
  "data": {                                     # optional; absent == no data area
    "kind":    "partitions" | "lvm",
    "vg":      "vg_data",                       # lvm only, required
    "size":    "*" | 200 | "60%",               # lvm only: size of the PV partition, default "*"
    "volumes": [ {...}, ... ]                   # partitions kind: one physical partition each; lvm kind: one LV each
  }
}
```

Volume object: `{"name": ..., "size": ..., "fs": ..., "mount": ..., "label": ...}`

| key | partitions | lvm |
| --- | --- | --- |
| `name` | not allowed | required (LV name) |
| `size` | required | required |
| `fs` | `btrfs`, `ext4`, `xfs` (required) | `btrfs`, `ext4`, `xfs`, or `none` (unformatted LV; default) |
| `mount` | required, absolute path of the filesystem (btrfs: of the root volume) | required unless fs `none` |
| `label` | required (it is also the GPT PARTLABEL used to recognise the partition on a rerun) | optional, filesystem label |

Sizes: integer GiB (`160` or `"160"`), percent string `"40%"` (integer 1-99), or `"*"` (the remainder).
`fs=zfs` and any other value are refused (no zfs install/pool path exists; btrfs is the proven COW filesystem).
The runner's cow detection looks for the label `MDT_COW`: use it for the cow area.

### Levels, percentages, `*`

* POOL level = root + every data partition (`kind=partitions`) or the single PV (`kind=lvm`, key `size`).
  Pool = (first swap start) - (root start) - `reserve_gb`. Percent is a share of the pool.
* VG level (`kind=lvm`) = the LVs. Percent is a share of the usable PV (PV minus 1 GiB LVM metadata, 4 MiB extents).
* At most one `*` per level. The `*` entry gets what is left, minimum 256 MiB (LV: 64 MiB).
* Pool level without `*`: the entries must add up to the whole pool. Rounding remainder (below 1 MiB per percent
  entry) goes to the last percent entry; anything more is refused. Fixed GiB sizes therefore need a `*` or a `reserve_gb`
  that makes them exact. VG level without `*`: free VG space is allowed (it is the LVM-level reserve).
* Requires `swap_policy=on` and `root_resize_policy=resize`; with a data area `swap_partition_count >= 1`.

## Numbering and ordering algorithm

1. `tail numbers` = ascending numbers after the root number, skipping numbers held by preserved pre-root partitions.
2. The first `n_data` tail numbers go to the data partitions in config order (`kind=lvm`: n_data = 1), the next
   `n_swap` to the swaps. More than 128 partitions in total is refused.
3. Physical order: root, data (config order, contiguous), reserve, swaps (contiguous, ending at disk end minus a
   1 MiB buffer). Therefore ascending numbers == ascending start sectors.
4. Sizes: `pool_end` = first swap start (aligned to 1 MiB); `total = pool_end - root_start - reserve`.
   Fixed entries take their GiB, percent entries `floor(total*p/100)` aligned down to 1 MiB, `*` the rest. Boundaries
   are laid out sequentially, each aligned to 1 MiB (an unaligned root start aligns the root END), the last entry ends
   exactly at `pool_end - reserve`. Result: `root + data + reserve + swap + preserved == usable disk`.

## Examples

512 GiB host (v1001/r1002 dev-host use), swap 4 x 4 GiB (`"swap_disk_total_gb": 16, "swap_partition_count": 4`):

```json
// root fixed 160, data takes the rest, btrfs cow  -> vda3 160G, vda4 ~335G MDT_COW, vda5-8 swap
{"partitioning": {"root": 160, "data": {"kind": "partitions", "volumes": [
  {"size": "*", "fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW"}]}}}

// root takes the rest, data fixed 200
{"partitioning": {"root": "*", "data": {"kind": "partitions", "volumes": [
  {"size": 200, "fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW"}]}}}

// percent split of the pool (must add up to 100)
{"partitioning": {"root": "40%", "data": {"kind": "partitions", "volumes": [
  {"size": "60%", "fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW"}]}}}

// two physical data partitions
{"partitioning": {"root": 100, "data": {"kind": "partitions", "volumes": [
  {"size": 200, "fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW"},
  {"size": "*", "fs": "xfs", "mount": "/srv/scratch", "label": "SCRATCH"}]}}}

// data as LVM: one PV partition (*), VG vg_data, two LVs (60% of usable PV, rest)
{"partitioning": {"root": 100, "data": {"kind": "lvm", "vg": "vg_data", "size": "*", "volumes": [
  {"name": "cow", "size": "60%", "fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW"},
  {"name": "scratch", "size": "*", "fs": "xfs", "mount": "/srv/scratch", "label": "SCRATCH"}]}}}

// no data area (explicit reserve of 100 GiB kept free in front of swap)
{"partitioning": {"root": "*"}}
{"partitioning": {"root": 300, "reserve_gb": 100}}      // 512 - 1.25 boot - 16 swap - 300 - 100 = ~94.7 left -> REFUSED (see messages)
```

Tiny test disk (6 GiB sparse image, root start after a 1.25 GiB boot area, swap 1 GiB in 4 x 256 MiB; used by the VM test):

```json
{"swap_disk_total_gb": 1, "swap_partition_count": 4,
 "partitioning": {"root": 1, "data": {"kind": "partitions", "volumes": [
   {"size": "*", "fs": "btrfs", "mount": "/srv/div2-vm-cow", "label": "MDT_COW"}]}}}
{"swap_disk_total_gb": 1, "swap_partition_count": 4,
 "partitioning": {"root": 1, "data": {"kind": "lvm", "vg": "vgdiv2vm", "size": "*", "volumes": [
   {"name": "cow", "size": 1, "fs": "btrfs", "mount": "/srv/div2-vm-cow", "label": "MDT_COW"},
   {"name": "rest", "size": "*", "fs": "xfs", "mount": "/srv/div2-vm-rest", "label": "REST"}]}}}
```

## Exact refusal messages (config load and plan time; nothing is written on refusal)

| case | message |
| --- | --- |
| removed key | `<key> was removed; use partitioning (see docs/DESIGN-data-area-v2.md)` |
| unknown key | `partitioning has unknown key(s): a, b` / `partitioning.data has unknown key(s): ...` / `partitioning.data.volumes[i] has unknown key(s): ...` |
| bad size | `partitioning.root: size must be an integer GiB value, a percent like "40%" (1-99) or "*"` |
| two stars | `partitioning (root and data partitions): at most one "*" is allowed per level` |
| percent > 100 | `partitioning (root and data partitions): the percentages add up to more than 100%` |
| zfs | `partitioning.data.volumes[i]: zfs is not supported by this installer (no zfs package/pool path); btrfs is the supported COW filesystem. Use one of btrfs, ext4, xfs` |
| other fs | `partitioning.data.volumes[i]: fs must be one of btrfs, ext4, xfs` (LV: `... , none`) |
| mount | `... mount must be an absolute path without a trailing slash`; `... mount '/boot/x' collides with the system tree (root, /boot, /etc, /usr, ...)`; `mount point collision between /a and /a/b (equal or nested mounts are refused)` |
| label | `... label must be 1-12 characters of [A-Za-z0-9_.-]`; partition without label: `a data partition needs a label (...)` |
| reserve | `partitioning.reserve_gb must be an integer GiB value from 0 to 10240` |
| prereq | `partitioning needs swap_policy=on: ...`; `partitioning needs root_resize_policy=resize (...)` |
| over-allocation | `the plan over-allocates the pool by 12.00 GiB: root 160 GiB, volume 400 GiB do not fit into 500.50 GiB (disk 512.00 GiB, swap and reserve already taken)` |
| space left over | `the plan leaves 94.75 GiB of the disk unallocated: give one entry "*" (the remainder), make the percentages add up to 100%, or set partitioning.reserve_gb to keep space free on purpose` |
| star too small | `the "*" entry (root) would get only 0.10 GiB (needs at least 0.25 GiB): the other sizes (...) leave too little` |
| LV over PV | `LV sizes (200 GiB) exceed the physical volume (168.00 GiB less 1 GiB LVM metadata); the VG must keep free space` |
| too many partitions | `cannot allocate N partitions after root: GPT partition-number limit reached` |

## Edge cases

* Preserved existing partitions: pre-root partitions are kept verbatim and reserve their numbers; a non-swap,
  non-data partition after root is refused. Data partitions are recognised by (type GUID, PARTLABEL).
* Alignment 1 MiB everywhere; tiny disks: fixed sizes that do not fit, a `*` below 256 MiB or percentages above 100
  are plan-time errors with the sizes in the message.
* Crash safety / resume (kept): data + swaps are written in ONE crash-safe sfdisk transaction (backup, checksum,
  readback, rollback). The plan is a pure function of (config, disk size, root start), so a resume recomputes it.
  Root growth runs after the table write (`sfdisk -N` / growpart to the exact planned end; data starts exactly there,
  so no hole opens). A rerun that would move/delete a data partition holding a signature is refused.
* `/etc/cloud` root-cap boot guards (growpart off, `/etc/growroot-disabled`) are installed whenever root is not `*`
  (or a reserve exists), i.e. whenever something could be grown into later.
* Allowlist: LV creation adds `lvcreate -l` (`100%FREE` for the `*` LV); all argv are checked through the shared
  `HostActions._validate` guard in the tests.

## Implementation plan (after review)

`layout_policy.py` (schema, pure `plan_pool`/`plan_lvs`, numbering), `config.py` (`partitioning` field, removed-key refusal,
delete old fields), `installer.py` (`_tail_swap_numbers` skips the data count, `_data_area_layout` returns all data
extents, `_write_sfdisk_plan` writes them before swaps, `_configure_data_area` loops volumes, root-cap guards),
tests (unit + planner properties: sizes sum to the usable disk, ascending numbers == ascending starts, alignment),
the existing VM test extended with `*` data partition and LVM+LV (lane `r1-vm-real-commit`), CHANGES.md, README.
