# Design: explicit disk plan (`partitioning`), numbers follow disk position, whole disk accounted for

Status: IMPLEMENTED (revised after the adversarial review `div2-data-area-v2-DESIGN-REVIEW.md`, verdict REVISE).
Operator directive 2026-11-05 (v1001: root 160G, swap vda4-7, data vda8 physically BEFORE the swaps, 166.8 GiB hole)
and update: NO backwards compatibility, one clean schema, no shims.

## Removed (no migration)

`disk_layout` (`full-root`/`dev-host`), `root_max_size_gb`, `data_area`, `data_partition`, `data_lvm` and every sub-key, and
the rules `recommended_root_gib` (5/16 root), 1/3 data, `min_root_gib` (20% root floor). A config containing a removed key is
refused with ONE message listing all of them:
`configuration key(s) disk_layout, root_max_size_gb were removed; the disk layout is now the single partitioning object (see docs/DESIGN-data-area-v2.md): ...`.

## Principles

1. The layout depends ONLY on the config: `partitioning` plus the `swap_*` keys. Swap is sized first and is independent of
   the pool (never a share of it, so there is no circularity).
2. The plan accounts for the WHOLE disk. Default reserve 0. The only sanctioned hole is an explicit `reserve_gb`. Only the
   1 MiB head and 1 MiB end buffers remain (the swap end buffer is part of the swap policy).
3. Partition numbers AFTER ROOT ascend with disk position: data partitions (config order), then the swaps. Pre-root
   partitions keep the numbers they have (cloud images: root is 1 with 14/15 before it; ISO installs: 1 efi, 2 boot, 3 root).
   No global "1, 2, 3" claim is made; renumbering mounted pre-root partitions is out of scope.

## Schema

```
"partitioning": {                       # optional; absent == plain root growth (root_resize_policy / swap_*), no data area
  "root":       "*" | "keep" | 160 | "160" | "40%" | "33.33%",   # default "*"
  "reserve_gb": 0 | 25 | "*",                                   # default 0; unallocated GiB in front of swap; "*" = the remainder
  "data": {                             # optional; absent == no data area
    "kind":        "partitions" | "lvm",
    "vg":          "vg_data",           # lvm only, required
    "pv_size":     "*" | 200 | "60%",   # lvm only: the PV partition, default "*" (min 2 GiB)
    "vg_free_gib": 0,                   # lvm only: GiB kept free in the VG for lvextend, default 0
    "volumes":     [ {...} ]            # partitions: one GPT partition each; lvm: one LV each
  }
}
```

Volume `{name, size, fs, mount, label, type}`:

| key | partitions | lvm |
| --- | --- | --- |
| `name` | not allowed | required (LV name) |
| `size` | optional, default `"*"` | optional, default `"*"` |
| `fs` | `btrfs` `ext4` `xfs` `none` (raw partition) | same, default `none` |
| `mount` | required unless `none`; the filesystem top level | same |
| `label` | required, unique, 1-12 of `[A-Za-z0-9_.-]`; GPT PARTLABEL (rerun recognition) and fs label | optional fs label, unique |
| `type` | `linux` (default) or `zfs` (GPT type GUID, only with `fs: none`) | not allowed |

`fs: zfs` as a managed filesystem is DEFERRED, not rejected forever: the installer has no zfs package/pool path, so it is
refused with a clear message; `fs: none` (+ `type: zfs`) reserves a raw partition for a hand-made pool. `fs` is an enum, so adding
`zfs` later needs no restructuring. `subvol` (btrfs subvolume) is also deferred and refused. Labels are unique across the plan and
must not collide with `esp/efi/boot/root/swap/MDT_LVM_PV/vbpub-swap*`. `MDT_COW` is the label the runner's cow detection finds.

## Rules, in order of evaluation

1. **Sizes**: integer GiB, `"N%"` (0 < N <= 100, two decimals), or `"*"`. Omitted size == `"*"`.
2. **Pool** = (first swap start, or disk end minus 1 MiB when no swap is managed) - root start - fixed `reserve_gb`. **Percent is a
   share of the POOL** (with root `keep`: of the pool behind the kept root); the resolved plan is printed in GiB in every refusal and in
   `show_plan`.
3. **Pool entries** = root, then every data partition (or the single PV), in config order, plus `reserve_gb: "*"` if given.
   EXACTLY ONE entry may be `"*"` and it absorbs every rounding remainder. If none is `"*"`, all entries must be percentages adding up to
   100% (the rounding remainder, below 1 MiB per entry, goes to the last one). Fixed GiB cannot sum to the fractional pool, so a plan of
   fixed sizes needs a `"*"` or `reserve_gb: "*"`. Percent mixed with fixed sizes without a `"*"` is refused as ambiguous; percent with
   `reserve_gb: "*"` is refused (the pool would be unknown).
4. **Root**: `"keep"` = current size, never grown or shrunk (resize behaviour "untouched"); a size, percent or `"*"` = "resize": larger
   than the current root grows (`growpart` or `sfdisk -N` to the planned end), smaller shrinks through the existing offline path with its
   usage / filesystem-minimum safety checks. The behaviour is DERIVED from `root`; `root_resize_policy` cannot be combined with a plan
   (it stays the knob of the plain, plan-less path). Root must be at least `preserve_root_size_gb` (that key already is the test/VM knob).
5. **Minimums** (every entry, message names entry, size and minimum): btrfs/xfs partition 512 MiB, ext4 and raw 64 MiB, PV 2 GiB, LV 64 MiB.
6. **LV level**: `N` GiB -> `lvcreate -L NG`; `"P%"` -> `-l <extents>` = floor(P% of the VG's extents, read from `vgs -o vg_extent_count`);
   `"*"` -> `-l 100%FREE`, created LAST whatever the config order (after fixed and percent LVs); with `vg_free_gib` the `*` LV gets
   `-l <free extents - kept>`. Without a `*` LV the rest of the VG stays free. At most one `*` LV.
7. **Numbering**: tail numbers = ascending numbers after the root number, skipping numbers held by preserved pre-root partitions;
   data partitions take the first, swaps the following. > 128 partitions is refused. Invariant: ascending numbers after root ==
   ascending start sectors.
8. **Swap**: `swap_policy` `on` (swap at the end) or `off` (pool ends at the disk end minus 1 MiB). `untouched` is refused (the pool end
   would be unknown). With retained pre-root swap that meets the target there is no tail swap and the pool ends at the disk end.
9. **Write order (shrink path)**: any root filesystem shrink happens first; then ONE crash-safe table write containing data + swaps (backup,
   checksum, readback, rollback); then root growth to the planned end. The plan is a pure function of (config, disk size, root START);
   the current root SIZE is never an input, so a resume after the table write (root still small) or after growth recomputes identical
   extents. The table write is refused (extents would overlap the live root) until the shrink happened.
10. **Boot guards** (`/etc/cloud` growpart off, `/etc/growroot-disabled`) are installed when `root` is not `"*"`.
11. **fstab / mounts**: UUID source, `nofail`, btrfs `noatime,user_subvol_rm_allowed`; entries sorted by path (parents first; equal/nested
    mounts are refused); a mount equal to or containing `/var/lib/docker` also gets `x-systemd.required-by=docker.service` (nofail alone lets
    docker fill the root fs when the volume is missing).

## Examples

512 GiB host (v1001/r1002 dev-host use), swap 4 x 4 GiB (`"swap_disk_total_gb": 16, "swap_partition_count": 4`):

```json
{"partitioning": {"root": 160, "data": {"kind": "partitions", "volumes": [
  {"fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW"}]}}}
```
-> root 160 GiB (vda3), `MDT_COW` btrfs ~334.7 GiB (vda4, starts at the root end), swaps vda5-8 at the end.

```json
{"partitioning": {"root": "*", "data": {"kind": "partitions", "volumes": [
  {"size": 200, "fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW"}]}}}
{"partitioning": {"root": "40%", "data": {"kind": "partitions", "volumes": [
  {"size": "60%", "fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW"}]}}}
{"partitioning": {"root": 100, "reserve_gb": "*", "data": {"kind": "partitions", "volumes": [
  {"size": 200, "fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW"}]}}}
{"partitioning": {"root": 100, "data": {"kind": "lvm", "vg": "vg_data", "volumes": [
  {"name": "cow", "size": "60%", "fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW"},
  {"name": "scratch", "fs": "xfs", "mount": "/srv/scratch", "label": "SCRATCH"}]}}}
{"partitioning": {"root": "keep", "data": {"kind": "partitions", "volumes": [
  {"fs": "btrfs", "mount": "/srv/cow", "label": "MDT_COW"}]}}}
{"partitioning": {"root": "*"}}
```

Tiny test disk (6 GiB sparse image, 1 GiB swap in 4 x 256 MiB, used by the VM lane):

```json
{"swap_disk_total_gb": 1, "swap_partition_count": 4, "preserve_root_size_gb": 1,
 "partitioning": {"root": 1, "data": {"kind": "partitions", "volumes": [
   {"fs": "btrfs", "mount": "/srv/div2-vm-cow", "label": "MDT_COW"}]}}}
{"swap_disk_total_gb": 1, "swap_partition_count": 4, "preserve_root_size_gb": 1,
 "partitioning": {"root": 1, "data": {"kind": "lvm", "vg": "vgdiv2vm", "volumes": [
   {"name": "cow", "size": 1, "fs": "btrfs", "mount": "/srv/div2-vm-cow", "label": "MDT_COW"},
   {"name": "rest", "fs": "xfs", "mount": "/srv/div2-vm-rest", "label": "REST"}]}}}
```

## Exact refusal messages (config load or plan time; nothing is written)

| case | message (abridged) |
| --- | --- |
| removed key | `configuration key(s) disk_layout, root_max_size_gb were removed; the disk layout is now the single partitioning object (...)` |
| unknown key | `partitioning has unknown key(s): x` / `partitioning.data.volumes[i] has unknown key(s): x` |
| bad size | `partitioning.root: size must be an integer GiB value, a percent like "40%" (0-100, 2 decimals) or "*" or "keep"` |
| two stars | `exactly one "*" is allowed per plan, but root and volume[0] are all "*" (an omitted size is "*"): give all but one an explicit size` |
| percent > 100 | `the percentages add up to more than 100% (root 50%, volume[0] 60%)` |
| zfs | `partitioning.data.volumes[0]: fs zfs is not supported yet (the installer has no zfs package/pool path; btrfs is the supported COW filesystem). Use fs "none" for an unformatted volume, one of btrfs, ext4, xfs, none` |
| subvol | `...: subvol is not supported yet (the mount is the filesystem top level)` |
| label | `label MDT_COW is used twice (labels must be unique across the plan)`; `... label 'boot' collides with a system partition/swap label`; `a data partition needs a label (...)` |
| mount | `... mount must be an absolute path without a trailing slash`; `... mount '/boot/x' collides with the system tree (...)`; `mount point collision between /a and /a/b (...)` |
| prereq | `partitioning needs swap_policy=on or off: with swap_policy=untouched the installer cannot know where the pool ends`; `root_resize_policy cannot be combined with partitioning: ...` |
| fixed + fixed | `root 100 GiB, volume[0] (MDT_COW) 200 GiB leave 194.75 GiB of the disk unallocated: set volume[0] (MDT_COW) to "*" (it becomes 394.75 GiB), or set reserve_gb to "*" to keep it free on purpose` |
| ambiguous | `mixing percent and fixed sizes without a "*" is ambiguous (root 100 GiB, volume[0] 50%); percentages are a share of the whole pool (494.75 GiB): give one entry "*" or use percentages only` |
| percent sum | `without a "*" the percentages must add up to 100% (root 40%, volume[0] 50% = 90%)` |
| over-allocation (also negative `*` remainder) | `the plan over-allocates the pool by 105.25 GiB: root 600 GiB, volume[0] * do not fit into 494.75 GiB (swap and reserve already taken)` |
| minimum | `volume[0] (MDT_COW) is 30 MiB, below the minimum of 512 MiB` / `root is 5120 MiB, below the minimum of 10240 MiB` / `PV is ..., below the minimum of 2048 MiB` |
| LV over VG | `LV sizes (200.00 GiB) exceed the VG (168.00 GiB)` |
| `*` LV tiny | `the "*" LV would get 8 MiB, below the minimum of 64 MiB: the other LVs take the whole VG` |
| GPT limit | `cannot allocate N partitions after root: GPT partition-number limit reached` |
| data in the way | `refusing to delete or resize data-area partition N: it holds a btrfs signature ...` (rerun that would move a populated data partition) |

## Edge cases

* Preserved partitions: pre-root partitions are kept verbatim and reserve their numbers; a non-swap, non-data partition after root is
  refused; data partitions are recognised by (type GUID, PARTLABEL).
* Alignment 1 MiB everywhere; an unaligned root start aligns the root END (the `*`/last entry absorbs the difference).
* A rerun with a different plan on a disk whose data partition already holds a signature is refused (no wipe override; `wipefs -a` by hand
  on an empty host). A changed swap count never renumbers data (swaps follow data).
* Allowlist: `lvcreate -l` added; every installer argv is checked through `HostActions._validate` in the tests (argv_guard, dry-run and
  validating fakes, AST scan).

## Deviations from the review text (recorded)

* Root floor = `preserve_root_size_gb` (default 10), not a hard 4 GiB constant: the existing knob doubles as the test/VM override.
* `root_resize_policy` is not deleted: it stays the knob of the plan-less path (and `swap_policy` off/untouched, grow-only); with a plan it is
  refused. `subvol` is deferred. `vg_free_gib` and `pv_size` are as proposed.
