<!-- origin: 2026-11-05; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/div2-data-area-v2-DESIGN-REVIEW.md -->
<!-- relation: adversarial design review -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# div2 data-area v2 design: adversarial review

Reviewed: `scripts/debian-install-v2/docs/DESIGN-data-area-v2.md` (2751743a9), against `config.py`, `layout_policy.py`, `installer.py` (`_plan_swap_partitions` 1911, `_data_area_layout` 2275, `_tail_swap_numbers` 2341, `_write_sfdisk_plan` 2650, root growth ~3940-4030, `_configure_data_area` ~3834, fstab `nofail` at 3830) and README "Data area".
Simulation: `<controller-scratch>/-workspaces-vbpub/bfe9ee76-aa16-4929-a108-36a00b55a0e5/scratchpad/sim.py` (literal implementation of the note's sizing/numbering; 512 GiB, 1 TiB, 128 GiB, 6 GiB; root start 1.25 GiB; swap 4 parts).

**VERDICT: REVISE.** The core idea (single ordered pool, swap subtracted first, ascending numbers == ascending starts, sequential layout) is sound and the arithmetic holds: in every accepted plan, sizes tile the disk exactly (0 inside gaps, only the 1 MiB end buffer remains), alignment is 1 MiB, and order is correct. The problems are in what can be expressed and in missing semantics.

## Simulation results (what the literal algorithm does)

* Accepted plans, all disks: no internal gaps, no misaligned entries, tail after last swap = 1 MiB (the end buffer; "NO space lost" is therefore only true modulo 1 MiB head + 1 MiB tail; state it).
* Pool is NEVER an integer GiB (disk - 1.25 GiB boot - 1 MiB buffer - swap), e.g. 494.749 GiB on 512. Consequence: **any plan made only of fixed GiB entries (root 100 + data 200, or root 300 + reserve 100) is refused on every disk**, because fixed integers cannot sum to a fractional pool and `reserve_gb` is also integer GiB. The note's own advice ("a `reserve_gb` that makes them exact") is unsatisfiable. Reproduced: 512G leaves 194.75 G, 1T 706.75 G, 128G 16.75 G, 6G 0.75 G.
* Three percentages 33/33/33: refused ("leaves 4.95 G"); 33/33/34 accepted. `"33.3%"`, `"99.9%"` rejected as bad size. Equal thirds need a `*`.
* `root 99%` + `data 1%` on a 6 GiB disk: data partition = 38 MiB (accepted; only `*` has the 256 MiB floor). btrfs/xfs mkfs would fail or yield junk. On 128 GiB: root 1% = 1.19 GiB, below any usable Debian root.
* `root 160 + data *` on 128 GiB: refused with a negative-star message (the text "star too small" is wrong when the remainder is negative; should be the over-allocation message).
* Unaligned root start (+37 sectors): root and data come out unaligned unless the implementation aligns root END and pushes data start up; the note says so in one clause but the literal algorithm as written ("boundaries laid out sequentially, each aligned") needs the root SIZE rounded, and then the rounding remainder must go to the `*`/last entry, not be lost. Not specified who absorbs it. Odd disk sector count: tail is 1.6 MiB (unaligned end is wasted, harmless).
* Zero size, two stars, >100%: correctly refused.

## Findings

### BLOCKER

**B1. Fixed + fixed is unexpressible; reserve cannot fix it (see simulation).** The operator's own example ("a size for one of them so the other takes the rest") works only because someone writes `"*"`. Fixed root + fixed data (the natural "160 + 200") is refused with advice that cannot succeed.
Change: (a) every size defaults to `"*"` when omitted; (b) `reserve_gb` accepts `"*"` (explicit "leave the rest unallocated on purpose", the only sanctioned hole); (c) the refusal message computes the fix: `root 100 + data 200 leave 194.75 GiB: set data size to "*" (becomes 294.75 GiB), or set reserve_gb to "*" to keep it free`. Exactly one `*`-holder in {root, data entries, reserve}.

**B2. Root "expand or not" is dropped.** Operator: "define how root is treated (expand or not)". The note forces `root_resize_policy=resize` and offers only `*`/N/%, so there is no "keep the existing root size, data takes the rest" (the cloud-image case: root 3 GiB stays, data `*`) and no way to say "do not grow root". Also `root_resize_policy=untouched/grow-only` stay in config.py as separate knobs, contradicting principle 1 ("depends ONLY on partitioning").
Change: `root` accepts `"keep"` (current size, never shrink/grow; pool = rest) in addition to `"*"`, N, `"N%"`. Drop the requirement for `root_resize_policy=resize`; derive the resize behaviour from `root` (keep => untouched; N/%/`*` => resize, shrinking allowed only if N is below current size and the existing safety checks pass). Delete `root_resize_policy` as a user key or make it a refused-if-contradicting echo. `*` for root still means "growpart to the pool boundary" exactly as today.

### HIGH

**H1. Shrink path is unspecified.** v1001-style hosts can have a root larger than the target (`root 160` on a 500 GiB root). Data extents then overlap the live root until the fs shrink; the note only describes "root growth after the table write". Existing `_plan_root_shrink` -> `_write_sfdisk_plan` order must be kept: fs shrink, then ONE table write containing data+swap. State: table write for data partitions is always after any shrink and before any grow; the planner refuses data extents that intersect the current root unless a shrink step is planned; add a test.

**H2. Percent base ambiguity / mixed fixed+percent.** "Percent is a share of the pool" where pool excludes boot area, swap and reserve; users read it as percent of the disk. Mixed `root 100 + data 50%` (no star) = 100 + 247 G on 494 G pool => leaves 147 G, refused, while "percent of what remains after fixed" is the other plausible reading.
Change: pin the definition in the key name and output: percent is of the POOL, and mixing percent with fixed without a `*` is a config error (`"root 100 GiB with data 50% is ambiguous; percentages are a share of the whole pool (494.75 GiB)..."`). Add the resolved plan table (GiB per entry, partition number, start) to `show_plan` and to every refusal message; wizard prints it before commit.

**H3. Data area forced to `swap_policy=on` and `swap_partition_count>=1`.** Needless coupling (the only reason is pool_end = first swap start). With swap off/untouched the pool ends at disk end minus 1 MiB (swap off) or the existing next-partition boundary. Operator said swap "is defined separately", i.e. not a prerequisite.
Change: `pool_end = first_swap_start if swap managed else disk_end - 1 MiB`; refuse only `swap_policy=untouched` with existing swap partitions located after the pool end.

**H4. Per-entry minimum sizes only for `*`.** Percent or fixed entries can be 38 MiB (6 GiB disk) or a 1.2 GiB root. Change: global floors checked for every entry: root >= max(preserve_root_size_gb, 4 GiB, current used + margin), partition fs btrfs/xfs >= 512 MiB (ext4 >= 64 MiB), PV >= 2 GiB, LV >= 64 MiB (already), message names the entry and the numbers.

**H5. LVM `*` LV vs "VG must keep free space".** The note's `*` LV means `lvcreate -l 100%FREE` => zero VG free space; the retained old code (`layout_policy.py` 412-417) and the message table row "LV over PV ... VG must keep free space" refuse exactly that. Internally inconsistent. Also: (i) creation order: a `*` LV listed first would eat the VG before the fixed LVs are created, so fixed LVs must be created first and `*` last regardless of config order; (ii) the "usable PV = PV - 1 GiB" constant wastes ~1 GiB and makes percent LVs inexact (real overhead is 1 MiB pe_start + extent rounding).
Change: LV sizes become `lvcreate -L <N>G` (fixed), `-l <p>%VG` (percent of the whole VG; LVM rounds down to extents, no constant), `-l 100%FREE` (`*`, created last). Add `vg_free` knob: `"vg_free_gib": 0` (default; operator says use the whole thing) removes the "must keep free space" refusal; if the user wants snapshots they set it (the `*` LV then uses `100%FREE` minus that via `-l <n>` extents computed after vgcreate via `vgs -o vg_free_count`). Since the cow filesystem is btrfs (snapshots in-fs), 0 is a reasonable default. Replace the old "LV over PV" message by "LV sizes (200 GiB) exceed the VG (168.0 GiB)".

**H6. "1 efi, 2 boot, 3 root" is not true on cloud images.** Existing code discovers `root_number` (Debian generic cloud: root=1, 14 bios, 15 efi), and pre-root partitions keep their numbers. The note's guarantee holds only for ISO-installed hosts (netcup). What does hold: numbers after root ascend with position. State exactly that, and test with root_number=1 + preserved 14/15 (tail numbers 2..): do not claim a global `1,2,3` invariant. Renumbering existing pre-root partitions is out of scope (root is mounted).

### MEDIUM

**M1. zfs / "btrfs/zfs/?" and raw partitions.** Operator listed zfs; hosts previously had a zpool. Refusing `fs=zfs` is defensible (no installer path), but the partition kind has no `fs: "none"`, so a user cannot even reserve a raw GPT partition for a later `zpool create` (the LVM kind has `none`). Change: allow `fs: "none"` for partitions (PARTLABEL only, type GUID "Solaris /usr & Apple ZFS" `6A898CC3-1DD2-11B2-99A6-080020736631` selectable via optional `type: "zfs"` raw-only), keep zfs-as-managed-fs refused with the existing message. Keep the schema open: `fs` is an enum, adding `zfs` later needs no restructuring. Record in the note that zfs is deferred, not rejected.

**M2. Only one filesystem per partition/LV; no subvolume/dataset list.** The operator's "list of cow things for each partition/LVM ... mount path of the root volume/dataset" implies per-fs children. Current schema: one fs, one mount = fs top level. Change (optional, low cost): volume gets optional `"subvol": "@cow"` (btrfs: mount `-o subvol=`; created by the installer). Defer further datasets; document that the mount is the fs top-level unless `subvol` is given.

**M3. Label semantics.** `label` serves as GPT PARTLABEL (36 UTF-16 chars) AND fs label (btrfs 255, ext4 16, xfs 12), capped at 12 to satisfy xfs. Uniqueness is not required by the note: two volumes with equal labels would break resume recognition (recognised by type GUID + PARTLABEL) and `LABEL=` fstab fallback. Change: labels must be unique across the plan and not collide with the swap/EFI/boot labels; for lvm kind, PV PARTLABEL is fixed `MDT_LVM_PV` (state it) and `label` is the fs label of the LV. fstab source should be UUID (as now), never LABEL when two disks carry `MDT_COW`.

**M4. Rerun/reinstall on a prior layout.** "A rerun that would move/delete a data partition holding a signature is refused" is right, but the plan is "pure function of (config, disk size, root start)": that only holds if no pre-existing data partitions change the answer. A reinstall with different `partitioning` on a disk that already has data partitions at old extents is refused (good), but the message should tell the operator how to proceed (`wipe` is not available), and the `*` entry's size on rerun must be recomputed identically even after root has been grown (root_start is stable, root SIZE is not an input: say so). Add a resume test: crash after table write, before root grow; and after root grow, before mkfs.

**M5. `reserve_gb` placement is useless for growth.** Reserve sits between the last data partition and swap, so root can never grow into it; the note's `/etc/cloud` root-cap guard text ("whenever a reserve exists ... something could be grown into later") is wrong for that placement. Reserve only enables later extra partitions. Change: guards installed only when `root` is not `*`/`keep`-with-resize; document reserve as "kept unallocated, you can add a partition there later".

**M6. Percent grain.** Integer 1-99 only; `100%` refused (even sole root), no decimals, equal thirds impossible without `*`. Change: accept `"N%"` with N in (0,100], up to 2 decimals; sum must be <= 100 and, without a `*`, exactly 100 within 1 MiB x entries; rounding remainder to the `*` entry if present, else to the last percent entry (as the note says).

**M7. Mount ordering with docker data-root.** `nofail` is already emitted (3830), good, but a missing `/srv/cow` mount with `nofail` lets docker start and write into the root fs. Change: when the volume mount equals or contains a configured docker `data-root`, emit `x-systemd.required-by=docker.service` (or `RequiresMountsFor` drop-in); fstab entries are written sorted by path so parent before child (nested already refused).

### LOW

**L1. Wrong refusal text** for negative star remainder ("would get only 0.10 GiB") - use the over-allocation message (sim shows -41 GiB).
**L2. Hand-writability.** Nested `partitioning.data.volumes[]` with two meaning-different `size` fields (PV `size` vs volume size) and a `kind` enum is learnable, but `data.size` for lvm is confusing: rename to `pv_size`. Provide defaults so the minimal forms are: `{"partitioning":{"root":160,"data":{"kind":"partitions","volumes":[{"fs":"btrfs","mount":"/srv/cow","label":"MDT_COW"}]}}}` (size omitted == `*`). Add a one-line shorthand? Not needed.
**L3. Refusal for removed keys** names only the first key; list all removed keys in one message.
**L4. GPT limits.** 128 total entries; also `swap_partition_count <= 64` + data count; message exists. No further issue.
**L5. Tiny disks.** 6 GiB examples pass the algorithm (root 1, data 2.749 G, 4x256 MiB swap). The VM-test example uses `root: 1` below the preserve floor; keep a documented test-only override or the H4 floor will break it (`preserve_root_size_gb` already exists as the knob).
**L6. Docs/code surface:** besides files listed in the note, `wizard.py`, `actions.py`, `docs/CONSUMERS.md`, `docs/DESIGN-GUIDE.md`, `TODO.md`, `testing/vm/run-vm-tests.sh`, `test_wizard`, `test_user_docs`, `test_install_plan`, `test_root_growth_crash`, `test_disk_policy`, `test_disk_layout` reference the removed keys and must be changed in the same commit. `cow-carve.sh`/runner detection by `MDT_COW` is unaffected.

## Swap circularity: none (checked)
`automatic_swap_size_gb` depends only on disk size and RAM (min(2xRAM, 20% of disk)), never on pool<home>, so "swap first, then pool" is acyclic. Keep it that way: never allow a swap size expressed as a share of the pool. Note the auto value can be small enough that 20% of 6 GiB = 1 GiB; fine.

## Proposed REVISED schema (adopt verbatim)

```
"partitioning": {                      # optional; absent == {"root": "*"}
  "root":       "*" | "keep" | 160 | "160" | "40%" | "33.33%",   # default "*"
  "reserve_gb": 0 | 25 | "*",          # default 0; unallocated GiB kept free in front of swap; "*" = the remainder
  "data": {                            # optional; absent == no data area
    "kind":    "partitions" | "lvm",
    "vg":      "vg_data",              # lvm only, required
    "pv_size": "*" | 200 | "60%",      # lvm only: the PV partition; default "*"
    "vg_free_gib": 0,                  # lvm only: GiB left free in the VG for lvextend; default 0
    "volumes": [ {...} ]               # partitions: one GPT partition each; lvm: one LV each
  }
}
```

Volume: `{"name", "size", "fs", "mount", "label", "subvol"}`.

| key | partitions | lvm |
| --- | --- | --- |
| `name` | not allowed | required, LV name |
| `size` | optional, default `"*"` | optional, default `"*"` |
| `fs` | `btrfs`, `ext4`, `xfs`, `none` (raw partition, no mount/label needed beyond PARTLABEL) | same set, default `none` |
| `mount` | required unless `none`; absolute path of the filesystem top level (or of `subvol` if given) | same |
| `label` | required, unique, 1-12 [A-Za-z0-9_.-], used as PARTLABEL and fs label | optional fs label, unique |
| `subvol` | optional, btrfs only (created and mounted with `subvol=`) | same |

Rules, in order of evaluation:
1. Sizes: integer GiB, `"N%"` (0 < N <= 100, <= 2 decimals), or `"*"`. Percent is a share of the POOL (partitions/PV level) or of the VG (LV level); the resolved plan is always printed in GiB.
2. POOL = (first swap start, or disk end minus 1 MiB when swap is not managed) - root start - fixed `reserve_gb`. Swap is sized first and is independent of the pool.
3. Pool entries = root, then each data partition (or the single PV) in config order, plus `reserve_gb: "*"` if given. Exactly one entry may be `*` (omitted size == `*`); if none is `*`, all entries must be percentages summing to 100 (rounding remainder, less than 1 MiB per entry, goes to the last entry). Mixing percent and fixed without a `*` is refused as ambiguous. The `*`-entry absorbs every rounding remainder; no sector is left over except the 1 MiB head and 1 MiB tail buffers.
4. `root: "keep"` = current root size (no grow/shrink); pool excludes it. `root` N or percent below the current size means shrink (existing safety checks); `*` or larger means grow to the boundary. No `root_resize_policy` key.
5. Minimums, checked for every entry: root >= max(preserve_root_size_gb, 4 GiB); btrfs/xfs partition 512 MiB, ext4 64 MiB, PV 2 GiB, LV 64 MiB. Message names entry, size and minimum.
6. LV level: sizes `-L N G`, `-l P%VG`, `*` = `-l 100%FREE` created LAST regardless of config order (after fixed and percent LVs); `vg_free_gib` is subtracted from the `*` LV (or from the unallocated VG when no `*`). Without `*`, remaining VG space stays free.
7. Numbering: tail numbers ascend after the root number, skipping preserved partitions; data partitions first, swaps last; physical order root, data, reserve, swaps. Invariant: ascending numbers after root == ascending start sectors (not a global 1,2,3 claim).
8. Write order: any root shrink, then ONE crash-safe table write (data + swaps), then root growth to the planned end; plan is a pure function of (config, disk size, root start).
9. `fs: zfs` refused (not yet supported; use `fs: "none"` for a raw partition). Labels unique across the plan; fstab by UUID with `nofail`; mounts equal/nested refused.
10. `/etc/cloud` root-cap guards installed when `root` is not `*`.
Refusal messages: keep the note's table; replace "star too small" for negative remainders by the over-allocation text; add "ambiguous mixed sizes", "root 100 + data 200 leave X GiB: set data size to \"*\" (=Y GiB) or reserve_gb to \"*\"", "label X used twice", "<entry> is N MiB, below the minimum M".

Examples (all valid): `{"partitioning":{"root":160,"data":{"kind":"partitions","volumes":[{"fs":"btrfs","mount":"/srv/cow","label":"MDT_COW"}]}}}`; `{"partitioning":{"root":100,"reserve_gb":"*","data":{"kind":"partitions","volumes":[{"size":200,"fs":"btrfs","mount":"/srv/cow","label":"MDT_COW"}]}}}`; `{"partitioning":{"root":"keep","data":{"kind":"lvm","vg":"vg_data","volumes":[{"name":"cow","size":"60%","fs":"btrfs","mount":"/srv/cow","label":"MDT_COW"},{"name":"scratch","fs":"xfs","mount":"/srv/scratch","label":"SCRATCH"}]}}}`.
