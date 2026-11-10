<!-- Evidence scrub 2026-11-10: environment-specific host identities, host addresses, controller paths, SSH key material and fingerprints were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->
<!-- origin: 2026-11-04; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/PARALLELISATION-DESIGN-MEMO-ADDENDUM-2-2026-11-04.md -->
<!-- relation: parallelisation addendum 2 -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# Parallelisation design memo: ADDENDUM 2 (operator follow-ups of 2026-11-04)

Status: DESIGN, no implementation. It amends `RP/reports/PARALLELISATION-DESIGN-MEMO-ADDENDUM-2026-11-04.md`
("addendum 1"), which amends `RP/reports/PARALLELISATION-DESIGN-MEMO-2026-11-05.md` ("base memo"). Where they
disagree, this addendum wins.

Inputs:
- the two memos, `RP/reports/assay-tmpfs-REPORT.md`, `RP/reports/mdt-dedicated-mem-REPORT.md`, `RP/reports/AGENT-RULES.md`;
- `AGENTS.md:209-225` (limits are host-owned) and `:329-361` (host cgroup placement);
- code: `mdt-cockpit/host-setup/mdt_capacity.py`, `run-gate-project/run_gate.py`, `assay/src/assay/{isolation.py,_tmpfs_worker.py}`,
  `libraries/cowtree`, `scripts/<private-host>-guide` (soulmask_tmpfs), `shared-ramdisk-depot-manager` (srdm);
- upstream Linux **v7.2.11** sources (git.kernel.org stable, read-only, in the session scratchpad `ksrc/`), git v2.47.0
  `statinfo.c`/`Makefile`;
- read-only probes of <host> and <host> (2026-11-04 21:29Z) and one **non-root experiment on <host>** (21:34Z, CPU PSI
  some avg10 5.4, memory 0.0; transient user scopes and an unprivileged user+mount namespace; all state under one exact
  path in `/dev/shm`, removed and verified absent). `r1002_api_check` and `v1001_api_check` passed first. No root, no
  docker, no campaign touched, <protected-host> untouched.

Evidence tags: **[M]** measured, **[F]** read from code or docs, **[I]** inferred, **[U]** unknown. Kernel paths refer
to v7.2.11. Repository paths are under `<checkout>`. `RP` = `<controller-program-dir>`.
`SP` = the session scratchpad `<controller-scratch>/-workspaces-vbpub/bfe9ee76-aa16-4929-a108-36a00b55a0e5/scratchpad`.

---

## Executive summary

**1. tmpfs charging: the operator's reading is right for in-place overwrites, and wrong for the common case.**
- **The rule.** A tmpfs page is charged once, when it is *allocated*, to the writer's memcg. "Writer" means the faulting
  mm, or for `write(2)` the current task. A page that is already resident is never charged again.
- **In-place overwrite:** content written later by someone else into an existing page **stays charged to the first
  writer**.
- **The common case moves the charge.** Truncate-and-rewrite, unlink-and-create, append, writes into holes and
  `fallocate` all free pages or allocate new ones. The new pages are charged to **the new writer**. Git, assay's mutation
  write and most tools work this way.
- **Readers and mappers are never charged.** Their reads still show up in the owner's `file_mapped`.
- **Reclaim and swap** act only through the owner's cgroup, its ancestors, and global reclaim.
- **Measured on the Debian 7.2.11+deb13 kernel** (§1.2): A writes 32 MiB, and A is charged 32. B reads it: 0. B
  overwrites it in place: 0. B appends 8 MiB: +8. B rewrites it with `O_TRUNC`: net +24 (= +32 − its own 8). After A's
  scope was removed, A's 32 MiB showed up in the parent: reparenting [M].

**2. Provisioning boundary: the host owns *where and under which policy*, run-gate owns *when and placement*, assay
owns *what*, and cowtree owns *the generic publish/lease/GC mechanism*.**
- **mdt-cockpit host-setup** installs:
  - `dev-cache.slice`, a `/srv/tree-cache` tmpfs and a `/srv/tree-upper` tmpfs;
  - the env keys `CGROUP_PARENT_DEV_CACHE`, `MDT_TREE_CACHE_ROOT` and `MDT_TREE_UPPER_ROOT`, plus a `tree_cache`
    block in `host-capacity.json`;
  - the orphan-GC timer.

  This reuses the idioms of `MDT_COW_ROOT` and `CGROUP_PARENT_DEV_GATES`.
- **run-gate** places it:
  - it starts an exact-named populate helper with `--cgroup-parent $CGROUP_PARENT_DEV_CACHE`;
  - it binds the key read-only into lanes;
  - it creates and removes the per-worker overlay volumes.
- **assay** provides `assay template ensure` and consumes `ASSAY_TEMPLATE_DIR` and `ASSAY_WORKER_SLOTS`. It owns the
  key, the content, the verification and the worker reuse.
- **cowtree** gains a small `cache` primitive: atomic publish, flock lease, last-used, and holder-checked GC. That gives
  one implementation, shared by assay and the host timer.
- **srdm is not the provisioner**, but its measured idioms are reused (§2.3). cmru and ciu have no role.

**3. Per-worker writable layer with shared inodes and page cache: adopt overlayfs.**
- **The layout:**
  - a shared read-only lower in the cache tmpfs, owned by `dev-cache.slice`;
  - per worker, one overlay **mounted by dockerd as a `local` volume** when the container is created
    (`redirect_dir=on,xino=auto`);
  - the upper on `/srv/tree-upper`, charged to the lane that writes.
- **Privilege:** none new. The runner already holds the Docker socket.
- **Measured, 3 overlays over a 95 MiB, 3 000-file git tree [M]:**

  | | 3 overlays | 3 × `cp -a` copies |
  |---|---|---|
  | Setup time | 6 ms | 0.54 s |
  | Extra page cache after reading the whole tree through each | **+0 MiB** | **+283 MiB** |

- **`git status` through an overlay validates by `lstat` alone** [M]: 0.02 s and no index rewrite.
  - Why: the overlay passes ctime, mtime, size, uid and mode through, and the low 32 bits of `st_ino`.
  - Git does not compare `st_dev`.
  - So the template's B219 primed index is valid in every worker **without the per-worker re-prime** that copies need.
- **Per-worker cost:**
  - one mutate-and-restore cycle left 260 KB in the upper [M];
  - against 177 MiB and 1.0-2.9 s per worker for a copy (`assay-tmpfs-REPORT.md:53-54`).
  - At 5 instances × 3 workers that is about **2.6 GiB of RAM not spent** [I].
- **Hazards:**
  - a directory rename gives EXDEV without `redirect_dir` [M];
  - `O_RDWR` and same-value `chmod` copy the whole file up [M; srdm D-027].
- **Reset:** settle repairs from the read-only template, then degrades that worker to a copy.
- A privileged broker is deferred.
- Addendum 1 §4.2 "overlay not adopted now" is overturned, gated on M-2 and N3′ (§6).

**4. CPU weights: keep dev-gates at 20.**
- **Why the old proposal was wrong.** Addendum 1 §2.1 proposed raising it to 100 on a symmetry argument ("the gate tier
  *is* the workload"), with no measured starvation. `cpu.weight` is work-conserving: it only splits CPU among siblings
  that are runnable at the same time.
- **Siblings used almost no CPU** of dev.slice's CPU since boot [M]:
  - interactive + background + buildkitd = **0.36 %** (<host>);
  - **1.06 %** (<host>).
- **So the weight changed nothing.** The gates tier's high CPU pressure (some avg10 ≈ 38 on <host>) is contention among
  its own lanes.
- **Evidence that would justify a change:** §4.3.

**5. Memory ceilings: the operator's rule is already implemented and live.** It is commit `286002597` on main.
- **The live values:**
  - gates and background High = dev High − (interactive Low + buildkitd Low) = 6.75 − 0.75 = **6.0 GiB** on both hosts
    [M];
  - the operator's premise ("gates high/max = max") predates it.
- **Keep the High rule.** It works because dev.slice's own High throttles **every** descendant, the cockpit included
  (`mm/memcontrol.c:2757-2788`). Gates must therefore hit its own High first.
- **Two fixes, one extension:**
  - (a) **load-tier MemoryMax = dev MemoryHigh** (T − keep = 6.75 GiB). This is a reachable, RAM-based backstop that
    confines the final memcg OOM to gates. Today's derived 22 GiB / 14 GiB is unreachable, a no-op;
  - (b) **load-tier MemorySwapMax = dev swap.max − ΣLow**, which leaves swap for the cockpit's pages above its Low;
  - (c) count the new cache slice's Low in Σ.

**Order:**
1. AD2-0, the tier fix (S).
2. M-1, M-2, M-4, M-5: feasibility and kernel checks (S).
3. AD2-2, host plumbing.
4. AD2-3, cowtree cache.
5. AD2-4, assay template plus worker sources.
6. AD2-5, run-gate populate, bind and overlay slots.
7. N3′.

Section 7 has owners, sizes, gates and risks.

---

## 1. Q1: who pays for a tmpfs page

### 1.1 Kernel facts

| # | Fact | Evidence |
|---|---|---|
| C1 | A new shmem folio is charged at allocation: `mem_cgroup_charge(folio, fault_mm, gfp)`. `fault_mm` is the faulting VMA's mm (mmap fault), else NULL | `mm/shmem.c:1926-1972`, `:2437` [F] |
| C2 | **Charge target with NULL mm** (`write(2)`, `copy_file_range`, `splice`, `fallocate`): `active_memcg()` if a kernel path set one (loop device, some async writers), else `current->mm`, else root. Either way it is resolved through **`mm->owner`'s memcg**. The uid plays no part | `mm/memcontrol.c:1112-1145` [F] |
| C3 | **A resident folio is found and reused with no charge.** `shmem_get_folio_gfp` looks the index up in the page cache first and only allocates, and so charges, on a miss | `mm/shmem.c:2439-2470` [F] |
| C4 | A **swapped-out** page is charged on swap-in to the memcg recorded at swap-out if that memcg is still online, **else to the faulting task's mm** | `mm/memcontrol.c:5221-5240`; `mm/shmem.c:2447-2455` [F] |
| C5 | When the owner cgroup is removed, its folios are **reparented** to the parent | `mm/memcontrol.c:271-303` (`lru_reparent_memcg` :292), `:4301` [F]; [M] §1.2 |
| C6 | shmem folios are swap-backed, so they sit on the owner's **anon** LRU lists. Reclaim reaches them only through the owner's own or an ancestor's `memory.high`/`max`, or global reclaim, and the owner's `memory.low`/`min` protect them. **A reader's limit never reclaims them, and they do not count towards it** | `include/linux/mm_inline.h:28-31`; cgroup-v2.rst:1616-1619, 1963-1975 [F] |
| C7 | `NR_FILE_MAPPED` is accounted to the **folio's** memcg (the owner), whoever maps it | `mm/rmap.c:1334-1340` (`lruvec_stat_mod_folio(folio, …)`) [F] |
| C8 | zswap: the compressed copy of a swapped shmem page is charged to the owner (`memory.zswap.current`, and inside `memory.current`) | addendum 1 K5, K6 [F] |
| C9 | **Throttling at memory.high walks ancestors.** On every charge, the task is flagged if *any* ancestor is above its `memory.high`. It then reclaims from each over-high ancestor and sleeps by the worst overage, at most 2 s per return to user space | `mm/memcontrol.c:2757-2788`, `:2330-2350`, `:2434-2445`, `:2368` [F] |
| C10 | Overlay inode objects are `SLAB_ACCOUNT`: an overlay lookup charges kernel memory to the **looking-up** task, i.e. the lane | `fs/overlayfs/super.c:1593-1596` [F] |

### 1.2 The precise answer, measured on the hosts' kernel

| Operation by a second cgroup B on a page that A wrote first | Who is charged | Measured on <host> [M] (`SP/ad2-exp-<host>.txt:6-13`) |
|---|---|---|
| A writes a new 32 MiB file | A | A +32 MiB |
| A's scope is removed | A's parent (reparent, C5) | the parent's `shmem` went from 0 to 32.0 MiB |
| B reads it, or maps it read-only | nobody new (C3, C6) | B +0 |
| B overwrites in place (`conv=notrunc`) | **still A, or A's heir** (C3) | B +0 |
| B appends 8 MiB | B, for the new pages only (C1) | B +8 MiB |
| B rewrites with `O_TRUNC` | truncate frees everything, then B owns the new pages | B +24 MiB net (+32, − its own 8 freed) |
| B unlinks and recreates the file (git, assay `isolation.py:1343-1344`, editors) | B | not run; same path as `O_TRUNC` [I] |
| Pages swapped while A is alive, faulted in by B | A if online (C4); if A is gone, mostly **B** (56-69 %), the rest the parent | measured, `reports/ad2-measurements-REPORT.md` M-4: A alive, B +0 and A back to 32 MiB; A stopped, B +14.6-17.1 of 23.5-26.2 MiB |
| B maps and touches a file | `file_mapped` goes to the **owner** (C7) | measured, same report M-5: A +32.09 MiB, B 0 |

What `memory.stat` shows:
- **The owner:** `shmem` and `file` (file includes tmpfs, cgroup-v2.rst:1546-1548, 1576-1578); `active_anon`/`inactive_anon`
  (C6); `file_mapped` for every mapper (C7); `zswap`/`zswapped` and `memory.swap.current` once swapped.
- **Readers:** none of these. They pay only for their own kernel objects (C10 for overlay) and see the event counters
  (`pgfault`, `pgmajfault`) in their own cgroup.

**Gotcha** [I, verify in M-2]: content pushed into a container by `docker cp` is extracted by a dockerd child. It is
charged to `system.slice/docker.service`, not to the lane. Never populate through `docker cp`.

### 1.3 Debian kernel: what differs and what is still open

- **Confirmed on `7.2.11+deb13-amd64` [M]:** first-writer charge, no recharge on in-place overwrite, new-writer charge
  on append and truncate, and reparenting on scope removal (§1.2). These were the facts addendum 1 asked to confirm.
- **Debian config that matters here** (`/boot/config-7.2.11+deb13-amd64`, both hosts identical [M]):
  - `CONFIG_OVERLAY_FS=m`, with `REDIRECT_DIR`, `INDEX`, `XINO_AUTO` and `METACOPY` all **not set**;
  - module parameters `redirect_dir=N xino_auto=N metacopy=N index=N`.

  So overlay options must be passed explicitly.
- More Debian config:
  - `CONFIG_TMPFS_XATTR=y`, so trusted and user overlay xattrs work on tmpfs;
  - host tmpfs mounts are `inode64`;
  - `CONFIG_LRU_GEN_ENABLED=y`, so MGLRU is on;
  - `CONFIG_FUSE_PASSTHROUGH=y`;
  - `kernel.unprivileged_userns_clone=1`.
- **Still open, to measure live:** the swap-in owner rule C4 (M-4) and owner attribution of `file_mapped` C7 (M-5).

---

## 2. Q2: which tool provisions, and where the boundary is

### 2.1 The patterns already in the estate

| Pattern | What it does | What we reuse |
|---|---|---|
| **soulmask_tmpfs** (`scripts/<private-host>-guide/files/etc/systemd/system/soulmask_tmpfs.{slice,service}`, `…/sbin/soulmask_tmpfs-setup.sh`) | a oneshot service populates **one** shared tmpfs from the main instance. The script moves itself into the slice that carries the policy, so the pages are charged there (`setup.sh:141-158`). It then remounts read-only (`:697-701`) and bind-mounts into every instance (`:732`). Written paths get a **private** per-instance tmpfs (`soulmask_tmpfs-paths.conf`, `[per-instance-*]`) | populate inside the policy cgroup; publish read-only; bind into consumers; private writable space per consumer |
| **srdm** (`shared-ramdisk-depot-manager/nyxloom-trove/PLAN.md:59-72,186-197`) | immutable hashed releases held in tmpfs by per-class hold units in policy cgroups; read-only binds; holders read from `/proc/*/mountinfo`; overlay planned for `rw` (`PLAN.md:344-358`) | D-016: the slice exists before population and is stopped explicitly. D-019: private op root. D-020: two-step read-only bind. D-012/D-018: holders from mountinfo. D-027: copy-up costs (`decisions.md:987-1012`). The overlay holder is visible only as a `lowerdir=` **path** (`decisions.md:1071-1093`) |
| **CoW area** (`mdt-cockpit/host-setup/scripts/mdt-cow-init-user.sh`; `run_gate.py:982,1935-1946`) | the host creates `/srv/cow/<user>` and exports `MDT_COW_ROOT`. run-gate binds it at the identical path and forwards `COWTREE_MODE`. cowtree and assay consume it | the host exposes an area through one env key; run-gate validates it and binds it; libraries consume it |
| **Placement** (`AGENTS.md:337-361`) | `$CGROUP_PARENT_DEV_*` env keys; consumers verify `LoadState` because a mistyped slice fails open | `$CGROUP_PARENT_DEV_CACHE`, with the same check |

Why not srdm itself:
- srdm's unit is an **operator-promoted release**: per-file hashing, probes, classes, Wings assignments and a cohort
  update, run by an operator as root.
- Assay templates are created **by automation, per commit, many times a day**, and live for hours.
- The common core is small: atomic publish, lease, GC. It belongs in cowtree, which already does leases, owner manifests
  and GC for disposable trees.

### 2.2 Ownership table

| Concern | Owner | What it does | What it never does |
|---|---|---|---|
| Host area and policy | **mdt-cockpit host-setup** | `dev-cache.slice` unit (under dev.slice, started at install, MemoryLow from `DEV_CACHE_MEMORY_LOW`, default empty). Mount units `srv-tree\x2dcache.mount` (tmpfs, `size` = 25 % T, `nr_inodes` bound, `mode=2770`, gid = gate-runner group, `nosuid,nodev`, **not** `noexec`, `huge=never`, never `noswap`) and `srv-tree\x2dupper.mount` (tmpfs, 10 % T, same flags). Exports the env keys. Writes the `tree_cache` block of `host-capacity.json`. Installs `mdt-tree-cache-gc.timer`, which calls `cowtree cache gc`. Applies the tier rule of §5 | know keys, git or lanes; create per-lane state |
| Generic keyed-tree lifecycle | **cowtree** (`libraries/cowtree`, new `cache` module and CLI) | `ensure(root, key, build)`: an exclusive flock on `.locks/<key>`; build into `<key>.tmp-<id>`; write `COMPLETE` (manifest digest); `rename` → `<key>`. `lease(key)`: LOCK_SH on `<key>/.lease`, held for a lane's life. `touch_last_used`. `gc(root, idle)`: a key is removed only with LOCK_EX\|NB on the lease **and** no holder in `/proc/*/mountinfo` (bind root path, **or** an overlay `lowerdir=` option naming it, srdm `decisions.md:1071-1093`). Stale `.tmp-*` are removed when unlocked | know git or assay; mount anything |
| Template content and key | **assay** | `assay template ensure --lane L --root $MDT_TREE_CACHE_ROOT`. Key = sha256(template format version, judged commit, snapshot selection, `link_paths`, prefix). It builds with the existing `_build` + `_prime_index` (`isolation.py:860-899`). There is **no chmod seal**: the mount gives the read-only protection, and a `chmod` would force full-file copy-ups (D-027). It prints `key path`. Lane side: `ASSAY_TEMPLATE_DIR` (read-only), `ASSAY_WORKER_SLOTS` (overlay merged dirs), else copy; settle repairs from the template, then degrades | mount anything; choose placement |
| Placement and per-lane lifecycle | **run-gate** | Checks `CGROUP_PARENT_DEV_CACHE` is a loaded unit and `MDT_TREE_CACHE_ROOT` is a tmpfs mount point. Runs the helper `rg-tcache-<token>` (lane image, lane `--user`, `--init`, `--cgroup-parent $CGROUP_PARENT_DEV_CACHE`, checkout plus cache root bound). Binds `<root>/<key>/tree` **read-only** at `/assay-template`. Creates N exact-named overlay volumes (§3.3). Tears down: container rm → volume rm → upper removal (`rg-tupper-clean-<token>`). `--dry-run` prints all of it. If a host key is missing or a volume mount fails: one WARNING, and the lane uses the copy path | write tree content; set host limits |
| Consumers | **cmru `tester-gate`**, remote-gate | through run-gate only | own implementations |
| — | **ciu** | none | — |

### 2.3 Interfaces (what a host exposes; what run-gate asks; what assay does)

```
# host (mdt-cockpit): exported to the runner like CGROUP_PARENT_DEV_GATES today
CGROUP_PARENT_DEV_CACHE=dev-cache.slice
MDT_TREE_CACHE_ROOT=/srv/tree-cache          # tmpfs; 2770 root:<gate-runner-group>
MDT_TREE_UPPER_ROOT=/srv/tree-upper          # tmpfs; same mode; absent = no overlay slots
/etc/mdt/host-capacity.json  "tree_cache": {"root": …, "slice": …, "size_bytes": …,
                                            "upper_root": …, "upper_size_bytes": …,
                                            "overlay_opts": "redirect_dir=on,xino=auto"}

# run-gate per lane start
docker run --rm --init --name rg-tcache-<token> --user <lane uid:gid> \
  --cgroup-parent "$CGROUP_PARENT_DEV_CACHE" --mount type=bind,src=$MDT_TREE_CACHE_ROOT,dst=$MDT_TREE_CACHE_ROOT \
  <checkout binds> <lane image> assay template ensure --lane L --root "$MDT_TREE_CACHE_ROOT"   # -> "<key> <path>"
docker volume create --driver local --opt type=overlay --opt device=overlay \
  --opt o=lowerdir=<root>/<key>/tree,upperdir=$MDT_TREE_UPPER_ROOT/<token>/w<k>/u,workdir=$MDT_TREE_UPPER_ROOT/<token>/w<k>/w,redirect_dir=on,xino=auto \
  rg-tslot-<token>-w<k>                                       # k = 0..jobs-1
docker run … --mount type=bind,src=<root>/<key>/tree,dst=/assay-template,readonly \
  --mount type=volume,src=rg-tslot-<token>-w<k>,dst=/work/w<k> … \
  -e ASSAY_TEMPLATE_DIR=/assay-template -e ASSAY_WORKER_SLOTS=/work/w0:/work/w1:…
```

- Placement of the helper is **the** charging mechanism (C1, C5). After it exits, its pages belong to `dev-cache.slice`.
- The helper runs as the lane uid so that the index's `uid`/`gid` stat fields match in the lanes (§3.4).

---

## 3. Q3: shared lower, per-worker writable layer

### 3.1 Options evaluated

| # | Option | Shares inodes and page cache? | Who mounts | Verdict |
|---|---|---|---|---|
| O1 | **overlayfs, mounted by dockerd as a `local` volume** (lower = cache key, upper on a host tmpfs) | **yes**: reads and mmaps go to the lower inode (`fs/overlayfs/file.c:323-339,468-476`; `fs/backing-file.c:329-355`) [F]; [M] +0 MiB for 3 readers | dockerd, root, at container create. No new privilege: the runner already has the Docker socket | **ADOPT** (phase 2) |
| O2 | overlay mounted by a privileged host broker inside the container's mount ns (upper on the container's own tmpfs) | yes | a new root daemon: unix socket with SO_PEERCRED, the peer's cgroup must be a run-gate lane scope, fixed path grammar, `openat2(RESOLVE_NO_SYMLINKS)`, nsenter | **defer**. It adds in-container reset, dynamic slots and an upper freed with the container. Build it only if N3′ shows frequent degrades (§3.6) |
| O3 | systemd template units per slot (`mdt-tree-slot@.service`) through polkit | yes | systemd | a static slot cannot follow a per-commit lower without a privileged remount: it is O2 in another form. Rejected as primary |
| O4 | overlay in a user namespace inside the lane | yes | the lane | needs a custom seccomp (`unshare`/`mount`) and AppArmor unconfined for **every** lane. Under `userxattr`, `redirect_dir` is forced to `nofollow` (`fs/overlayfs/params.c:988-1010`), so a directory rename gives EXDEV [M]. `xino` falls back to off without init-ns `CAP_DAC_READ_SEARCH` (`fs/overlayfs/util.c:80-92`) [M: raw ino 382]. **Rejected** (security relaxation plus semantic drift) |
| O5 | fuse-overlayfs | **no**: the FUSE inode has its own page cache per mount; passthrough needs init-ns `CAP_SYS_ADMIN` | lane plus `/dev/fuse` | **Rejected** |
| O6 | hardlink farm (per-worker links to foreign-owned 0444 inodes) | yes | the helper | an in-place write by a test gives EACCES where a copy succeeds, so verdicts drift. link/unlink bumps the **shared** ctime, which goes stale in every worker's index. **Rejected** |
| O7 | reflink on tmpfs | — | — | tmpfs has no `remap_file_range` (0 occurrences in `mm/shmem.c`) [F]. **Not available** |
| O8 | btrfs on zram or a RAM loop device | **no**: a clone is a new inode with a cold cache (addendum 1 K14) | — | **Rejected** |
| O9 | per-worker copy into the container's tmpfs from `/assay-template` (addendum 1 AWP-6) | no: 177 MiB per worker, charged to the lane, zswappable | — | **kept** as the fallback and the degrade path |

### 3.2 Facts that decide it [M unless tagged] (`SP/ad2-exp-<host>.txt:14-44`; userns overlays, 95 MiB / 3 000-file git tree)

| Question | Answer |
|---|---|
| Does reading one lower file through N overlays share one page cache? | **Yes.** Reading the whole tree through 3 overlays added +0 MiB `shmem`; 3 copies added +283 MiB. Mechanism: the overlay file's `read_iter` and `mmap` are redirected to the real lower file [F] |
| Mount cost | 3 mounts in 6 ms (copies: 0.54 s for 3 × 95 MiB on a quiet host) |
| `st_dev` | differs: the overlay's own dev, or the per-layer pseudo dev with `xino=off`/non-samefs (`fs/overlayfs/inode.c:95-152`). Git ignores it: `USE_STDEV` is off by default (git `statinfo.c:89-95`, `Makefile:204`) [F]. Under `xino=on/auto`, `st_dev` is uniform across files and directories (overlayfs.rst:57-77) [F] |
| `st_ino` of unmodified lower files | the lower's inode number (382 in all three mounts). With xino active, the fsid lands in bit 63: tmpfs file handles are not `ino32`, so 2 high bits are used (`super.c:1150-1159`, `shmem.c:4383`). **The low 32 bits are unchanged**, and git compares `(unsigned int) st_ino` (`statinfo.c:85`). After a copy-up the origin's ino is kept (`inode.c:186-221`) [F] |
| ctime, mtime, size, uid, gid, mode | the real lower stat (`ovl_getattr` → `vfs_getattr` on the real path, `inode.c:178-181`) [F]; [M] ctime identical |
| B219 primed index through an overlay | **valid**: `git status` gave 0 entries, 0.02 s, and the index was **not** rewritten in any of the 3 mounts (xino auto, xino off, samefs). A copy re-hashes: status 0.21 s, `update-index --refresh` 0.20 s for 95 MiB |
| What breaks the stat match | (i) an entry that was copied up (its upper ctime and mtime; one-file re-hash, cheap); (ii) a populator uid ≠ lane uid (OWNER_CHANGED, so the whole tree is re-hashed); (iii) template mtimes ≥ index mtime (racy; assay pins mtimes, `isolation.py:887-889`); (iv) a git built with `USE_STDEV` (not Debian's 2.47.3 [M]) |
| Mutation cycle (mutate → status M → unlink + write + chmod + utime restore) | status clean afterwards; upper holds the file plus `.git/index` (git status rewrote the index after the inode change): **260 KB** |
| Directory rename | **EXDEV** without `redirect_dir` (`userxattr` forces `nofollow`) [M]. Root-mounted overlays accept `redirect_dir=on` (overlayfs.rst:614-621) [F] |
| Same-value `chmod`, open `O_RDWR` without writing | **full data copy-up** [M; srdm D-027] |
| Upper charge | to the writer (C1): copy-up runs in the writing task with the mounter's creds, and creds do not affect memcg. Lookups charge `ovl_inode` slab to the lane (C10) |

### 3.3 Design (phase 2; phase 1 is addendum 1 AWP-5/6 with the template served from the cache)

```
/srv/tree-cache (host tmpfs, pages owned by dev.slice/dev-cache.slice)
   <key>/tree  ← populated once by rg-tcache-<token> (lane uid, primed index, COMPLETE, renamed into place)
   <key>/.lease  <key>/.last-used  <key>/COMPLETE
/srv/tree-upper (host tmpfs; pages charged to the writing lane, reparented to dev-gates.slice after it exits)
   <token>/w<k>/{u,w}
lane container (dev-gates leaf)
   /assay-template      RO bind of <key>/tree   (repair source, copy fallback, lease holder)
   /work/w<k>           overlay volume: lower <key>/tree, upper <token>/w<k>/u, redirect_dir=on, xino=auto
   /scratch/w<k>        container tmpfs: TMPDIR HOME XDG_CACHE_HOME COVERAGE_FILE PYTHONPYCACHEPREFIX
```

**Candidate cycle** (assay, unchanged except where marked):
1. Write the mutated file. Assay does unlink + write (`isolation.py:1343-1346`), so the overlay records a whiteout plus
   a new upper file; the parent directory is copied up as metadata only.
2. Run the tests.
3. `settle` (`_tmpfs_worker.py:182-235`) restores the file, the index and HEAD, and removes added entries.
4. **New:** an entry whose signature changed is first repaired from `/assay-template/<path>` (bytes, mode, mtime);
   then the tree is re-scanned.
5. Only an unrepairable state degrades the slot. Unrepairable means: a directory-level change, the upper tmpfs above
   80 % (`statvfs`), or an overlay error. The slot then degrades to a private copy in `/scratch` (O9) for the rest of
   the run.
6. **New:** no per-worker `_prime_index` on an overlay slot (`isolation.py:1019-1021`). Git falls back to hashing
   if a stat mismatches: slower, never wrong.

**Assay guards.** `_acquire_worker`'s `cheap_clone` guard (`isolation.py:961-967`) and the "jobs > 1 needs
cheap_clone" refusal (`assay-tmpfs-REPORT.md:57`) accept either worker source.

### 3.4 Costs: overlay slot vs per-worker copy (vbpub template: 177 MiB, 7 796 tracked files, 1.0 MB index)

| Item | Per-worker copy (O9) | Overlay slot (O1) |
|---|---|---|
| RAM per worker, charged to the lane | 177 MiB (`assay-tmpfs-REPORT.md:53`) | upper: about 1 MB for the index plus a few KB of mutated files [M 260 KB at 95 MiB]; `ovl_inode` and dentry slab for looked-up paths, about 1 KB × 7.8 k ≈ 8 MB worst case [I; M-3] |
| Worker start | 1.0 s (j1), 2.6-2.9 s (j3, concurrent) (`:54`) | volume mounted by dockerd at create (ms [M] for the mount itself); no copy |
| Per-worker index prime | `update-index --refresh` over 177 MiB ≈ 0.4 s [I: 0.20 s / 95 MiB measured] | none [M] |
| Page cache across workers, instances, resumes and shards | one copy each | **one per host per key** |
| Anomaly | rm plus recopy (177 MiB, about 1 s) | repair from lower (KB); degrade to a copy only when unrepairable |
| 5 cmru-scale instances × 3 workers | ≈ 2.6 GiB lane RAM (33 % of T) | ≈ 15-150 MB [I] |

### 3.5 Who may mount: the boundary

- **dockerd is the mounter (O1).** Run-gate already holds the Docker socket, so this adds no privilege. It is not a
  security boundary but a **blast-radius** one:
  - the only mounts are `type=overlay` volumes;
  - lowers are under `MDT_TREE_CACHE_ROOT`, uppers under `MDT_TREE_UPPER_ROOT/<token>`;
  - names are exact. run-gate validates the paths as it validates `MDT_COW_ROOT` (`run_gate.py:1923-1932`).
- **The lane never sees the upper's host path.** That keeps the rule "no changes to the underlying fs while mounted"
  (overlayfs.rst:651-664) true by construction. The lower is immutable per key.
- **`redirect_dir=on`** sets `trusted.*` xattrs that only root can forge. The lane cannot write them.

### 3.6 Failure modes

| Failure | Effect | Handling |
|---|---|---|
| The populate helper crashes | `<key>.tmp-*` without COMPLETE | lanes accept only COMPLETE keys; ensure retries; GC removes unlocked tmp dirs |
| Two lanes populate the same key | — | the per-key exclusive flock in `ensure`; the second waits, then reuses |
| The cache tmpfs is full | ensure gets ENOSPC | ensure first evicts idle, unleased keys (LRU); otherwise WARNING plus the in-container template build (today's path) |
| GC versus a live lane | — | LOCK_SH lease plus a mountinfo holder check (bind root path and overlay `lowerdir=`). A wrongly deleted key surfaces through B222 verification as an infra error, never as a verdict |
| The upper tmpfs is full | ENOSPC in the lane's writes | the 80 % check in settle degrades the slot to a copy; the tmpfs size is ≫ the expected upper |
| dockerd refuses the overlay volume (options, kernel) | container create fails | run-gate retries without slots, with a WARNING (copy path) |
| The lane is killed | the volumes unmount with the container; uppers stay, reparented to dev-gates.slice | run-gate teardown, then the GC timer for orphans |
| Cache pages swapped | lanes fault from zswap (µs) or disk | charged back to the online `dev-cache.slice` (C4); verify with M-4 |
| `dev-cache.slice` stopped | pages reparent to dev.slice (C5) | still inside the dev budget; host-check warns |
| Host reboot | the cache is gone | repopulated on demand |
| Overlay semantics differ from a plain fs | possible verdict drift (EXDEV, `st_dev`, mmap-after-copy-up, overlayfs.rst:593-609) | `redirect_dir=on`, `xino=auto`; a qualification check that buckets are identical across copy and overlay (N3′); a semantic canary in the run-gate VM lane |

### 3.7 Measurement plan (concrete; <host> only, CPU and memory PSI some avg10 < 25, exact paths, everything removed)

Each step starts with the guard below. Each step except M-7 removes everything it created, by exact path or name, and
confirms nothing is left: `test ! -e <path>` and `docker ps -a --filter name=^ad2-`.

```bash
source RP/dstdns-r2/env.sh && r1002_api_check   # then in each ssh: $R1002_GUARD; abort unless PSI some avg10 < 25
```

- **M-1, root semantics of a host overlay (root, about 5 min).**
  ```bash
  X=/dev/shm/ad2-root-20261105; test ! -e $X && mkdir -m 755 $X $X/L $X/U $X/M1
  ru=$(id -u runner); mount -t tmpfs -o size=512m,mode=755,uid=$ru ad2-lower $X/L; mount -t tmpfs -o size=128m,mode=755,uid=$ru ad2-upper $X/U
  systemd-run --scope -q --unit=ad2-pop -- runuser -u runner -- git clone -q --no-hardlinks <home>/checkouts/<vbpub sha> $X/L/tree
  runuser -u runner -- git -C $X/L/tree update-index --refresh; runuser -u runner -- mkdir -p $X/U/1/u $X/U/1/w   # merged root takes the upper's owner
  mount -t overlay ad2-ov1 -o lowerdir=$X/L/tree,upperdir=$X/U/1/u,workdir=$X/U/1/w,redirect_dir=on,xino=auto $X/M1
  stat -c '%n %d %i %Z' $X/L/tree/README.md $X/M1/README.md $X/M1 $X/M1/assay   # expect ino|2^63; same st_dev file/dir
  systemd-run --scope -q --unit=ad2-w -- runuser -u runner -- sh -c "git -C $X/M1 status --porcelain | wc -l; \
    mv $X/M1/assay $X/M1/assay.r && echo rename-ok; dd if=/dev/zero of=$X/M1/big bs=1M count=32 status=none; \
    grep -E '^(shmem|slab) ' /sys/fs/cgroup\$(sed 's/^0:://' /proc/self/cgroup)/memory.stat"
  umount $X/M1 $X/U $X/L; rm -rf $X
  ```
  Decides that `xino` is active (bit 63), `st_dev` is uniform, `rename-ok`, the index is stat-valid, and the upper is
  charged to the writer scope.
- **M-2, dockerd overlay volume end to end (runner, docker; lower and upper on `/dev/shm`, so samefs).**
  - Lower `/dev/shm/ad2-vol-20261105/L/tree` = a clone of a checkout.
  - `docker volume create … ad2-ovl-1` exactly as in §2.3.
  - `docker run --rm --init --name ad2-ovl-c1 --cgroup-parent dev-gates.slice --memory 1g --user $(id -u):$(id -g)
    --mount type=volume,src=ad2-ovl-1,dst=/w <tester-unified digest> sh -c 'git -C /w status --porcelain | wc -l;
    echo x >> /w/README.md; cat /sys/fs/cgroup/memory.stat | grep -E "^(shmem|slab) "'` (with
    `DOCKER_CONFIG=$HOME/.docker-nocreds`).
  - Clean up: `docker volume rm ad2-ovl-1`, then `docker run --rm --name ad2-ovl-clean --cgroup-parent dev-gates.slice
    -v /dev/shm/ad2-vol-20261105:/x <image> rm -rf /x/U` (the overlay work dir is root-owned, mode 000), then `rm -rf
    /dev/shm/ad2-vol-20261105`.

  Decides that O1 works under docker 29.9 with the default AppArmor and seccomp profiles and no lane privileges; that
  the charge lands in the container leaf; and the cleanup recipe.
- **M-3, N readers at real scale (runner, userns, like `SP/ad2-exp.sh` part B).**
  - The vbpub clone (177 MiB) with 1, 3 and 6 overlays against 1, 3 and 6 copies.
  - Measure the `shmem` and `slab` deltas **including the first lookups**, plus the wall time of mount or copy, of
    `git status` and of a full read.

  Decides the slab cost per mount and the §3.4 numbers.
- **M-4, swap-in owner rule C4 (root, about 2 min).**
  1. A holder service `systemd-run --unit=ad2-hold -p MemoryAccounting=yes runuser -u runner -- sh -c 'dd …32M into
     /dev/shm/ad2-sw-20261105/f; exec sleep 600'`.
  2. `echo 32M > /sys/fs/cgroup/system.slice/ad2-hold.service/memory.reclaim`; expect `memory.swap.current`/`zswapped`
     up and `shmem` down.
  3. A reader scope runs `cat f`; expect reader shmem +0 and holder shmem back.
  4. Repeat with `systemctl stop ad2-hold` between step 2 and the read; expect the **reader** to be charged (the trap).

  Remove `f` and the dir. Decides C4 on Debian, and whether the persistent `dev-cache.slice` mitigation is necessary.
- **M-5, `file_mapped` owner (runner, user scopes).**
  - Writer scope A creates 32 MiB. Mapper scope B mmaps and touches it.
  - Expect B `file_mapped` +0, and A's parent `file_mapped` +32 MiB.
- **M-6 = N3′, the decision run for phase 2** (AD2-6, after AD2-5).
  - The assay-tmpfs harness (worktree lane, `--shard 1/3`, the same 28 candidates) at jobs 3, with three arms: btrfs
    cow, cache plus copy (O9), cache plus overlay slots (O1).
  - Then 5 concurrent instances × 3 workers on the overlay arm.
  - Record: the lane `memory.peak` and `memory.stat` (`shmem`, `slab`); `dev-cache.slice` `memory.stat`; worker-start
    seconds; `phase_seconds`; `io.stat`; repairs and degrades per run; the bucket identity.
  - Adopt O1 as the default when the buckets are identical across all arms, the lane peak is at least 150 MiB per
    worker below O9, and degrades are ≤ 1 per 50 candidates. Otherwise keep O9 and reopen O2.
- **M-7, CPU-weight evidence sampler** (§4.3; read-only, 24 h, both hosts).

---

## 4. Q4: CPU weights

### 4.1 What the earlier memo proposed, and why

- Addendum 1 §2.1 set `dev-gates` CPUWeight 20 → 100 with the rationale "on a test host the gate tier *is* the
  workload".
- It was a symmetry argument made while removing the memory caps, so that gates would not lose to buildkitd (50) or
  rank with background soak (20). No starvation had been measured.
- It also blurred one fact: `cpu.weight` reserves nothing. It is work-conserving and only divides CPU among siblings
  that are **runnable at the same time** at the same level. The operator's statement is correct.

### 4.2 Live facts [M] (`SP/probe2-{<host>,<host>}.txt`)

| | dev.slice | interactive | gates | background | buildkitd |
|---|---|---|---|---|---|
| cpu.weight | 100 | 200 | 20 | 20 | 50 |
| cpu.max | 4 CPU | 4 CPU | 4 CPU | 4 CPU | 4 CPU |
| usage since boot, <host> (s) | 211 092 | 724 | 210 339 | 0.9 | 27.8 |
| usage since boot, <host> (s) | 102 364 | 521 | 101 275 | 509 | 58.5 |
| cpu.pressure some avg10, <host> | 37.9 | 0.01 | 37.9 | 0.00 | 0.00 |

- Siblings consumed 0.36 % (<host>) and 1.06 % (<host>) of dev.slice's CPU. The weights have had **no measurable
  effect**.
- The gates tier's pressure comes from its own lanes: 4 lane containers on 4 vCPUs. A weight between tiers cannot
  change that; admission (addendum 1 §3.2) can.

### 4.3 Recommendation, and the evidence that would change it

- **Keep gates at 20 (and background at 20).** Interactive (200) wins whenever it wants the CPU, which is the
  operator's intent.
- The only plausible starvation case is a long image build (buildkitd 50) during a campaign. Even then gates gets 2/7 of
  the contended CPU and all of the idle CPU.
- **Evidence to act on (M-7):**
  - sample every 60 s the `usage_usec` delta of each dev tier and the `cpu.pressure` "some" delta of dev-gates;
  - flag a window when gates' some ≥ 25 % **and** one sibling used ≥ 1 core;
  - if flagged windows exceed 10 % of campaign time over a week, lower buildkitd to 20. Do not raise gates above
    interactive.
- Separate, already filed: user.slice runs bare-host lanes at dev.slice's level (addendum 1 §2.6, AWP-12). That is a
  real fairness gap that weights inside dev.slice cannot fix.

---

## 5. Q5: dev-gates below dev.slice

### 5.1 State: already implemented and live

- **Commit `286002597`** (on main): "dedicated-dev load tiers (gates, background) MemoryHigh/Max below dev.slice by
  the protected tiers' MemoryLow".
- **Code:** `mdt_capacity.py:386-500`, with `_PROTECTED_LOW_KEYS` = interactive and buildkitd.
- **Live on both hosts [M]:**

  | Host | dev High | dev Max | dev swap.max | gates and background High | gates and background Max | Σ protected Low |
  |---|---|---|---|---|---|---|
  | <host> | 6.75 GiB | 22.75 GiB | 15.5 GiB | **6.0 GiB** | 22.0 GiB | 768 MiB |
  | <host> | 6.75 GiB | 14.75 GiB | 7.5 GiB | **6.0 GiB** | 14.0 GiB | 768 MiB |

  Interactive Low is 512 MiB and buildkitd Low 256 MiB; dev.slice Low = 768 MiB (their sum).
- The premise "gates high/max = max" describes the state between `de9507821` and `286002597`.

### 5.2 Why the High rule is the right one

- **dev.slice's own `memory.high` throttles every task charging anywhere below it**, the cockpit included (C9). The
  cockpit's MemoryLow protects its pages from *reclaim*, not its allocations from the *throttle*.
- So the protection that matters is to keep dev.slice below its own High. With gates ≤ 6.0, interactive ≤ its Low and
  buildkitd ≤ its Low, dev stays at 6.75 or below.
- Gates therefore hits **its own** throttle first, and only gates pays.

### 5.3 Recommendations

| Knob | Today | Recommended rule | Value on both hosts | Why |
|---|---|---|---|---|
| load-tier MemoryHigh | dev High − ΣLow | **keep** | 6.0 GiB | §5.2 |
| load-tier MemoryMax | dev Max − ΣLow (RAM+swap figure) | **dev MemoryHigh** (T − keep) | 6.75 GiB | `memory.max` counts RAM (and zswap) only, so 22 / 14 GiB can never be reached: a no-op. A RAM value is a reachable leak backstop. With swap available it only forces synchronous reclaim; it OOM-kills **inside gates** only once gates can no longer swap. High keeps acting below it |
| load-tier MemorySwapMax | = dev swap.max | **dev swap.max − ΣLow** | <host> 14.75 GiB, <host> 6.75 GiB | if gates takes all of dev's swap, dev-level reclaim cannot move the cockpit's above-Low pages out and only throttles. The same subtraction keeps that room, and makes a leak end in a memcg OOM inside gates instead of waiting for oomd |
| Σ protected Low | interactive + buildkitd | **+ `DEV_CACHE_MEMORY_LOW`** when `dev-cache.slice` exists (AD2-2) | unchanged while that is empty | the cache is a dev sibling too; protecting it must not push dev past its High |
| background | same derivation | keep | — | gates + background together can still exceed. Background is idle (5-23 MiB [M]) and admission counts dev.slice as a whole (addendum 1 §3.2). A shared `dev-load` parent would rename `dev-gates.slice` everywhere: not worth it now |

- **Keep:** dev.slice values, oomd settings, and zswap writeback everywhere.
- **Gate:** the mdt `vm-system` lane (AD2-0). A leaking gates leaf must end in an `oom_kill` in gates' `memory.events`
  once its swap is spent, while an interactive scope's memory PSI stays below 5 % and its allocations are not
  throttled. Never provoke this on a live host.

---

## 6. What this addendum overturns

| Earlier | Now | Why |
|---|---|---|
| Addendum 1 §4.2: per-worker overlay "not now" | adopted as phase 2 (O1, dockerd volumes), gated by M-2 and N3′ | §3.2 [M]: shared page cache, stat-valid index, 260 KB upper; operator directive (3) |
| Addendum 1 §1.2 and AWP-5: "run-gate helper" with unclear ownership | the split of §2.2, with cowtree as the shared publish/lease/GC mechanism | operator question (2) |
| Addendum 1 §2.1: gates CPUWeight 100 | **20** | §4 |
| Addendum 1 §2.1: tiers carry no High/Max/SwapMax | High = dev High − ΣLow (live); Max = dev High; SwapMax = dev swap − ΣLow | §5; operator directive (5) |
| B223 per-worker `_prime_index` after the copy | not on overlay slots; copies keep it | §3.2 [M] |

Addendum 1 AWP-1 to AWP-4 and AWP-7 to AWP-12 are unaffected. AWP-5/6 are refined by AD2-2 to AD2-5.

---

## 7. Work packages (ordered)

| # | Package | Owner | Size | Depends | Gate / acceptance | Risk |
|---|---|---|---|---|---|---|
| AD2-0 | load-tier MemoryMax = dev High; MemorySwapMax = dev swap − ΣLow; Σ includes `DEV_CACHE_MEMORY_LOW`; host-check and README | mdt-cockpit | S | — | host-setup tests: renders for T 7.75 G × S ∈ {0, 8, 16} G; `vm-system` leak test (§5.3). Live apply on <host>, then <host>, between campaigns | a lane needing more than 6.75 GiB resident, with no swap left, is now OOM-killed in gates instead of thrashing. Intended; document it |
| AD2-1 | measurements M-1, M-2, M-4, M-5 | controller | S | — | report with each expectation confirmed or refuted | root on <host>: PSI guard, exact paths |
| AD2-2 | host plumbing: `dev-cache.slice`; tree-cache and tree-upper tmpfs mount units; env keys; `host-capacity.json` `tree_cache`; `mdt-tree-cache-gc.timer` (`cowtree cache gc`, cowtree wheel in `/opt/mdt`) | mdt-cockpit | M | AD2-0, AD2-3 (for the timer) | `vm-system`: the units start; the slice persists and holds a helper's `shmem` after the helper exits; GC skips a leased key and removes an idle one | mount unit naming (`\x2d`); size bounds; ordering before lane starts |
| AD2-3 | `cowtree cache`: ensure, lease, last-used, gc (mountinfo plus overlay `lowerdir=`), JSON survey | cowtree | S-M | — | cowtree R0/R1; tests on fixture mountinfo and concurrent ensure | GC false negative = disk-free lie; GC false positive = blocked by the lease |
| AD2-4 | assay: `template ensure`; key; no chmod seal; `ASSAY_TEMPLATE_DIR` / `ASSAY_WORKER_SLOTS`; repair-from-template in settle; no prime on slots; jobs > 1 without `cheap_clone` | assay | M | AD2-3 | assay gate (100 % line and branch); B223 tests plus repair, degrade and no-prime tests; buckets identical cow / copy / overlay on a 56-candidate pilot | residue blind spots unchanged from today's settle |
| AD2-5 | run-gate: helper populate in `$CGROUP_PARENT_DEV_CACHE` (LoadState check); RO bind; overlay volumes and uppers; teardown and cleanup helper; WARNING fallback; `--dry-run`; SPEC | run-gate | M | AD2-2, AD2-4, M-2 | run-gate R0/R1; live on <host>: two lanes on one key, so `dev-cache.slice` shmem = 1 tree, lane shmem ≈ uppers, zero leftover volumes or uppers | Docker local-volume overlay quirks (M-2 de-risks) |
| AD2-6 | N3′ (M-6) and the decision | controller | S-M | AD2-5 | decision rule of M-6 recorded | host load: run under admission |
| AD2-7 | CPU-weight sampler M-7 (cgpd or controller script) | controller / cgroup-profiler | S | — | 24 h data and the §4.3 verdict | none |

**Order:**
1. AD2-0, AD2-1 and AD2-7 in parallel.
2. AD2-3.
3. AD2-2.
4. AD2-4.
5. AD2-5.
6. AD2-6.
7. O2 (broker) only if AD2-6 fails on degrades.

## 8. Live measurements (short list)

1. **M-1**: root host overlay semantics: xino bit 63, uniform `st_dev`, `redirect_dir` rename, stat-valid index,
   upper charge to the writer.
2. **M-2**: dockerd `local` overlay volume end to end, plus the cleanup recipe.
3. **M-3**: 1/3/6 overlays against copies on the real 177 MiB tree: shmem, slab, wall times.
4. **M-4**: swap-in owner rule and the dead-owner trap (`memory.reclaim` on a holder).
5. **M-5**: `file_mapped` attributed to the owner, not the mapper.
6. **M-6 (N3′)**: cow vs copy vs overlay at jobs 3, then 5 instances × 3 workers.
7. **M-7**: 24 h CPU-weight evidence sampler.
8. **AD2-0 gate**: `vm-system` leak test (VM only).

## 9. Sources

- **Kernel v7.2.11 (`SP/ksrc/`):**
  - `mm/shmem.c` :1926-1972, :2415-2470, :4383;
  - `mm/memcontrol.c` :271-303, :1112-1145, :2330-2350, :2368, :2434-2445, :2757-2788, :4301, :5163, :5221-5240;
  - `mm/rmap.c:1334-1340`; `include/linux/mm_inline.h:28-31`;
  - `fs/overlayfs/{inode.c:95-275, super.c:430-450,1137-1165,1593-1596, util.c:80-92, params.c:26,988-1010,
    file.c:323-339,468-476}`; `fs/backing-file.c:329-355`;
  - `Documentation/filesystems/overlayfs.rst:57-77,593-621,651-664`;
  - `Documentation/admin-guide/cgroup-v2.rst:1546-1590,1616-1619,1963-1975`.
- **git v2.47.0:** `statinfo.c:69-95`, `Makefile:204`.
- **Repository:**
  - `AGENTS.md:209-225,329-361`;
  - `mdt-cockpit/host-setup/{mdt_capacity.py:340-500, scripts/mdt-cow-init-user.sh}`;
  - `run-gate-project/run_gate.py:982,1905-1960`;
  - `assay/src/assay/isolation.py:860-1111,1338-1352`; `assay/src/assay/_tmpfs_worker.py`;
  - `libraries/cowtree/README.md`;
  - `scripts/<private-host>-guide/files/{etc/systemd/system/soulmask_tmpfs.slice, etc/systemd/system/soulmask_tmpfs.service,
    etc/<private-host>/soulmask_tmpfs-paths.conf, usr/local/sbin/soulmask_tmpfs-setup.sh}`;
  - `shared-ramdisk-depot-manager/nyxloom-trove/{PLAN.md:59-197,344-358, decisions.md:987-1093}`.
- **Reports (RP/reports):** `PARALLELISATION-DESIGN-MEMO-2026-11-05.md`, `PARALLELISATION-DESIGN-MEMO-ADDENDUM-2026-11-04.md`,
  `assay-tmpfs-REPORT.md`, `mdt-dedicated-mem-REPORT.md`, `AGENT-RULES.md`.
- **Probes and experiment (SP):**
  - `probe2.sh` with output `probe2-<host>.txt` and `probe2-<host>.txt`: kernel config, overlay module parameters, tier
    weights and limits, the env;
  - `ad2-exp.sh` with output `ad2-exp-<host>.txt`.
  - Commit for the tier rule: `286002597`.
