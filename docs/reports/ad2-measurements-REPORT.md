<!-- origin: controller report, historical date stated in source -->
<!-- status: scrubbed historical copy -->
<!-- source: reports/ad2-measurements-REPORT.md -->

# AD2-1 live measurements (M-1, M-2, M-4, M-5) - report

Date: 2026-11-04 (21:46-21:48Z). Host: **r1002 only** (v1001 and <host-address> untouched). Kernel 7.2.11+deb13-amd64, docker 29.9.2,
zswap on, vm.swappiness 10, git 2.47.3. `r1002_api_check` passed before every write batch.
Guard: aborted/waited while CPU or memory PSI some avg10 >= 25 (one abort at CPU 25.7, rerun at 12.5; memory PSI 0.00
throughout; end values CPU 6-23). Sizes 16-32 MiB; host swap used rose 106 -> 132 MiB (M-4 only), no OOM risk.
Campaign containers (`run-gate-*`, `mdt-cockpit-runner`, `mdt-buildkitd`) not touched.

Scripts and raw outputs (scratchpad `SP` = `<temporary-path>`):
`SP/ad2-m45.sh` -> `ad2-m45-r1002.txt` (M-5 + M-4), `ad2-m4b.sh` -> `ad2-m4b-r1002.txt` (M-4 case 2 twice more),
`ad2-m1.sh` -> `ad2-m1-r1002.txt`, `ad2-m2.sh` -> `ad2-m2-r1002.txt`.

Cleanup verified: `/dev/shm/ad2-*` absent, `/sys/fs/cgroup/dev.slice/dev-ad2m.slice` absent, no `ad2-*` units, no `ad2-` mounts,
`docker volume ls --filter name=ad2-ovl` and `docker ps -a --filter name=^ad2-` empty. Throwaway cgroups were transient
scopes/services under `dev.slice/dev-ad2m.slice` (removed by systemd when stopped).

## M-5: whose `file_mapped` does a mapping count in? C7 CONFIRMED

A (scope, stays alive) writes 32 MiB; B (separate scope) mmaps it read-only and touches every page.

| | A (owner) | B (mapper) | parent slice |
|---|---|---|---|
| before B | shmem 32.00, file_mapped 0.09 | - | file_mapped 0.09 |
| B has it mapped | shmem 32.00, **file_mapped 32.09** | shmem 0, **file_mapped 0.00**, anon 3.2 | file_mapped 32.09 |
| B exited | file_mapped 0.09 | - | - |

The mapping is counted in the **owner's** `file_mapped`; the mapper shows nothing (its own anon is the interpreter).
Consequence: a lane that mmaps the shared template shows a flat `file_mapped` of 0 for it, and the cache slice carries the
number. Per-lane accounting of "how much of the tree did this lane touch" via memory.stat is impossible; only the owner's
`file_mapped` moves.

## M-4: swap-in owner rule C4 - CONFIRMED for a live owner, REFINED for a dead owner

Holder service writes 32 MiB into tmpfs; `memory.reclaim` 32M on the holder (swapped 24-26 MiB, zswapped 7.6-10 MiB; the rest
stayed resident); a reader scope `read()`s the whole file.

| Case | Holder before read | Reader after read | Holder / parent after |
|---|---|---|---|
| 1: holder alive | shmem 6.3, swap 25.7 | **shmem 0.00**, swap 0 | holder shmem back to **32.00**, swap 0 |
| 2: holder stopped before read (3 runs) | gone; parent shmem 8.5/5.8/6.6, parent **swap.current 23.5/26.2/25.4** | **shmem 16.2 / 14.6 / 17.1** (about 56-69 % of the swapped amount) | parent hierarchy total 32.0; remainder (about 7-12 MiB) charged to the parent, not the reader |

- Case 1 confirms C4: pages swapped out while the owner is online return to the **owner**; the reader pays nothing.
- Case 2 refines it. A stopped owner is **not** a clean trap for the whole amount. At removal the swap charge is
  reparented with the cgroup (parent `swap.current` jumped to the swapped amount), so part of the swap-in is charged to the
  parent (online heir). A **majority (14.6-17.1 MiB of 23.5-26.2) is charged to the reader**. The split is stable over three
  runs but not deterministic (likely fault path versus swap readahead). Do not rely on it either way.
- Net for the design: a persistent, never-stopped `dev-cache.slice` keeps every swap-in on the cache slice (case 1) and is
  **necessary**; without it a reader pays for a large share of what it faults back in.

## M-1: root host overlay semantics - ALL EXPECTATIONS CONFIRMED

Lower tmpfs (`uid=runner`, inode64) and a separate upper tmpfs, 1 000-file / 32 MiB git tree populated by the runner uid in
a scope, `mount -t overlay ... redirect_dir=on,xino=auto` as root.

- Mount: options show `redirect_dir=on,uuid=on,xino=auto`.
- `st_ino`: lower 171; merged file `9223372036854775979` = **bit 63 set, low 32 bits 171** (xino active as predicted).
  Directories likewise (`pkg03` low32 159); the merged root is a plain 3.
- `st_dev` uniform for file/dir/root in the merged mount (138); differs from the lower's (136). ctime and uid identical to the lower.
- `git status` through the overlay: 0 dirty, **0.013 s, index not copied up** (the primed index is stat-valid despite the
  bit-63 inode change; git compares the low 32 bits).
- Directory rename of a lower directory: **`rename-ok`** (EXDEV is gone under root `redirect_dir=on`).
- Write 32 MiB in the merged dir as the writer scope: writer `shmem` **+32.0 MiB**, slab 1.2 MiB (upper charged to the writer).
- Populate scope's pages (31.6 MiB) appeared in the parent slice after its scope exited (reparent confirmed).
- The only upper content after status + rename: `/pkg01 /.git`.

## M-2: dockerd `local` overlay volume end to end - WORKS, no lane privileges

`docker volume create --driver local --opt type=overlay --opt device=overlay --opt o=lowerdir=...,upperdir=...,workdir=...,redirect_dir=on,xino=auto`
as the **runner** user (docker group), lower and upper on `/dev/shm` (same tmpfs, so samefs). Container:
`--cgroup-parent dev-gates.slice --memory 1g --user 1003:1003`, image `tester-unified:r25-e92d43b28b00`, default AppArmor and seccomp,
**CapEff = 0**.

- Create rc 0; the mount inside the container is `overlay ... rw,lowerdir=...,upperdir=...,workdir=...,redirect_dir=on,uuid=on`.
  (`xino=auto` is not echoed in mountinfo on a samefs mount; on non-samefs it is, see M-1.)
- `git status` in the container: 0 dirty, 0.014 s (index valid).
- Append to a lower file and write 16 MiB: **container-leaf `shmem` +16.02 MiB**, slab 1.46 MiB, file 16.45, `memory.peak` 22.3 MiB. The
  charge lands in the container leaf, not in the lower's owner and not in dockerd.
- Directory rename inside the container: rename-ok.
- Cleanup recipe verified in full: `docker volume rm ad2-ovl-1`; `docker run --rm ... -v $X:/x <image> rm -rf /x/U`
  (removed the root-owned work dir, rc 0); `rm -rf $X` (rc 0); nothing left (volumes, containers, mounts, paths).
- Observation: the upper dir is world-readable under `/dev/shm` for the runner (listed `big pkg00 pkg01 pkg01.r`). The production
  `<local-path>` must be mode 2770 root:gate-runner-group as designed, so lanes do not see other lanes' uppers.

## Not run

- **M-3** (1/3/6 overlays on the real 177 MiB tree vs copies): not trivially cheap (hundreds of MiB per arm); needs its own slot.
  The 3-overlay vs 3-copy result in the addendum (95 MiB, +0 vs +283 MiB) stands.
- **M-6/N3'** (needs AD2-5), **M-7** (24 h sampler): out of scope here.

## Decision rules and outcomes

| Rule (addendum 2) | Outcome |
|---|---|
| M-1 decides xino bit 63, uniform `st_dev`, `rename-ok`, stat-valid index, upper charged to the writer | all five confirmed on the host kernel as root |
| M-2 decides O1 works under docker 29.9 with default AppArmor/seccomp and no lane privileges, charge in the container leaf, cleanup recipe | confirmed; CapEff 0; recipe works and leaves nothing |
| M-4 decides C4 on Debian and whether the persistent `dev-cache.slice` mitigation is necessary | C4 confirmed (live owner); dead owner charges the reader for a large (56-69 %) share and the parent for the rest, so the persistent slice **is necessary** |
| M-5 decides `file_mapped` goes to the owner | confirmed (mapper 0, owner +32 MiB, parent +32 MiB) |

## Does it change the addendum's design?

No change to the design; it is confirmed, with three notes.

1. **Cache-slice helper**: the "populate inside `dev-cache.slice`, slice persists" mechanism is validated by M-1 (populate pages
   reparent to the slice) and strengthened by M-4 (a live owner absorbs all swap-in; a missing owner leaks the charge to
   readers). The slice must never be stopped while keys are live; `host-check` should WARN if `dev-cache.slice` is not
   active (the addendum already lists this failure mode; the M-4 split makes the reader-pays variant concrete).
2. **dockerd-mounted overlay volume (O1)**: works unprivileged from the runner with `redirect_dir=on`; rename works; upper charged
   to the container leaf. AD2-5's "docker run retries without slots on refusal" fallback was not exercised (no refusal occurred).
   M-2 is satisfied, so the AD2-5 dependency on M-2 is cleared. N3' (M-6) remains the adoption gate.
3. **Accounting caveat for N3'**: because mappings count in the owner's `file_mapped` (M-5), compare arms by lane `shmem`/`memory.peak`
   and `dev-cache.slice` stats, not by lane `file_mapped`. `§1.2` rows "not run: M-4" and "M-5" are now measured (above).
