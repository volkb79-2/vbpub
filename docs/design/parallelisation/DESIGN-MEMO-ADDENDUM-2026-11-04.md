<!-- origin: 2026-11-04; status: design; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/PARALLELISATION-DESIGN-MEMO-ADDENDUM-2026-11-04.md -->
<!-- relation: parallelisation addendum -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# Parallelisation design memo: ADDENDUM for the operator directives of 2026-11-04

Status: DESIGN, no implementation. It amends `RP/reports/PARALLELISATION-DESIGN-MEMO-2026-11-05.md` (the "base memo").
Where the two disagree, this addendum wins. Inputs:
- the operator directives of 2026-11-04 (memory `feedback-tests-load-independent-host-owned-limits.md:11-17`; AGENTS.md
  `:209-225`);
- the measured tmpfs A/B (`RP/reports/assay-tmpfs-REPORT.md:30-66`) and the jobs audit;
- the mdt-cockpit host-setup code and docs, and the run-gate and assay code and docs;
- the **upstream Linux v7.2.11 sources**, fetched read-only from kernel.org stable into the session scratchpad. The hosts
  run `7.2.11+deb13-amd64`; the Debian patch set was not checked;
- read-only probes of r1002 and v1001 at 2026-11-04 20:49Z, after `r1002_api_check` and `v1001_api_check` (both OK).

Evidence tags: **[M]** measured, **[F]** read from code or docs, **[I]** inferred, **[U]** unknown. Kernel paths
(`mm/…`, `Documentation/…`) refer to tag v7.2.11. Repository paths are under `<checkout>`. `RP` =
`<controller-program-dir>`. T = MemTotal, S = SwapTotal (disk swap), R = host reserve.

---

## Executive summary

**1. The 4 GiB gates tier is a policy artefact, not a host limit. Fix it in mdt-cockpit; the kernel does not need
changing.**
- `mdt_capacity.py:308-332` sizes every tier as a fixed percentage of RAM: gates High/Max = 50 %/70 % of RAM, so
  4 GiB / 5.5 GiB on the 7.75 GiB hosts.
- `HOST_ROLE` today changes only the IO policy (`host-setup.env.example:430-450`).
- On both hosts dev.slice is **already** at the operator's budget: MemoryMax 7.5 G plus MemorySwapMax 15.5 G ≈ 23 G on
  r1002 [M]. What binds is the tier caps.
- The cost of those caps, measured since boot:

  | Host | `dev-gates` memory.high events | memory.max events | OOM kills | `workingset_refault_file` |
  |---|---:|---:|---:|---:|
  | v1001 | 210 332 | 155 768 | 0 | 8.78 M pages (about 33 GiB re-read from disk) |
  | r1002 | 15 075 | 258 482 | 1 | not quoted |

  So the tier caps turn RAM headroom we have into disk IO we cannot afford.
- **Decision for `HOST_ROLE=dedicated-dev`:**
  - dev.slice MemoryMax = T − R (R = 512 MiB), no dev.slice MemoryHigh, MemorySwapMax = S − 512 MiB;
  - **no** MemoryHigh, MemoryMax or MemorySwapMax on the tiers. Tiers are weighted by MemoryLow and CPUWeight
    instead;
  - runaway work is bounded by PSI: admission, plus systemd-oomd killing at pressure.
- `shared-prod` stays as it is.

**2. A literal `memory.max = 23 GB` would do nothing.**
- `memory.max` counts RAM only (cgroup-v2.rst:1376-1385). Swap is the separate `memory.swap.max` (:1846-1851).
- A 23 GB memory.max on a 7.75 GiB host is therefore the same as `max`.
- The faithful translation of "RAM+swap minus about 500 MB" is the pair above: T − R RAM plus S − 512 MiB swap, which
  is 22.75 GiB on r1002 and 14.75 GiB on v1001 until v1001's swap is restored.
- What bounds a 23 GB budget on 8 GB of RAM is **time under pressure**, not a byte limit. With ample swap the kernel
  reclaims before it kills (section 2.5). Hence PSI admission plus oomd.

**3. tmpfs charging: yes, it can be moved to another context. "Another user" does nothing; "another cgroup" does
everything.**
- **Who is charged.** Shmem pages are charged at allocation to the memcg of the faulting mm or of the writing task
  (`mm/shmem.c:1972`; `mm/memcontrol.c:1112-1145,5163-5172`). They stay charged there while other cgroups read them
  (cgroup-v2.rst:1963-1975).
- **When the populator's cgroup is removed.** v7.2 reparents its LRU folios to the parent
  (`mm/memcontrol.c:271-303,4318`).
- **After swap-out.** A page swapped out is charged back on swap-in to its recorded owner if that owner is online,
  otherwise to the faulting process (`mm/memcontrol.c:5221-5240`, `mm/swap_state.c:456`).
- **No mount option helps.** tmpfs has no `memcg=` option on 7.2 (`mm/shmem.c:4419-4474`); `noswap` exists but
  defeats zswap overcommit.
- **Therefore:**
  - a host tmpfs `/srv/tree-cache`, populated once per key by a run-gate helper container placed in a persistent
    `dev.slice/dev-cache.slice`;
  - bind-mounted **read-only** into lane containers;
  - this serves every worker, instance and resume without charging any lane.

**4. cow vs tmpfs: use one path, no decision logic.**
- **The tree.** A sealed template lives once in the cache tmpfs. Each worker gets a RAM-to-RAM copy into a
  container-scoped tmpfs, charged to the lane, compressible by zswap. After any anomaly the worker drops its copy and
  copies again.
- **Transient writes always go to container tmpfs:** `TMPDIR`, `HOME`, `XDG_CACHE_HOME`, `COVERAGE_FILE` and the
  pycache.
- **btrfs** is no longer required for parallel work.
- **Why this way:**
  - btrfs snapshots are new inodes, so every candidate's page cache starts cold
    (`materialize-experiment-REPORT.md:14`);
  - under a tight cap, clean cache becomes disk refaults, which is exactly the v1001 refault count above;
  - on a full hoster disk at about 6 MB/s, zswap (microseconds) beats a disk refault.
- **Overlay is not adopted now.** A per-worker overlay would save about 140 MB per worker, but it needs a
  dockerd-mounted host upper. Revisit only if N3 shows that per-worker copies dominate RAM.
- Gate: measurement N3 (section 8). The A/B has to be repeated under a 6 MB/s IO cap and under memory pressure,
  because the existing A/B (no wall gain) was taken with a fast, idle-ish disk.

**5. KSM cannot merge tmpfs or page cache.**
- KSM merges only private anonymous pages (`Documentation/admin-guide/mm/ksm.rst:25`; `mm/ksm.c:738-765`).
- It is the wrong tool for sharing the tree; one shared tmpfs already shares the pages.
- On 7.2 the `PR_SET_MEMORY_MERGE` opt-in **survives fork and execve** (`include/linux/mm_types.h:1974-1996`,
  `kernel/fork.c:1114-1119`, `mm/vma_exec.c:129`). This corrects `scripts/<private-host>-guide/MEASUREMENTS.md:243-244`.
  A one-line prctl in the lane's init is therefore enough; no LD_PRELOAD is needed.
- Forced KSM on lanes: measure it (N4) and keep it only if it pays.
- Forced KSM for **VM lanes** (QEMU guest RAM) is where it should pay most.

**6. VM boundary, revised.**
- Any test that needs a real Docker daemon, systemd, cgroup delegation or kernel-global state runs in a VM with its
  **own dockerd**, and the test container runs inside it. KVM is a schedule dependency, not a design input.
- DinD is not extended. The run-gate DinD environment (base WP10) is dropped; cmru's existing DinD stays until its VM
  replacement passes.
- vmctl gains `accel=auto`, KSM-mergeable guest RAM, free-page reporting, ready-base images with dockerd and the
  preloaded tester images, and per-run overlays on tmpfs.

**7. Load independence.**
- Every flake is a defect. Build three things:
  - (a) a history-based flake inventory;
  - (b) a `flakehunt` lane that repeats the suite until it fails under five fixed stress profiles in cgroup-limited
    sibling containers;
  - (c) a shared pytest policy plugin. It refuses fixed sleeps, short timeouts, wall-clock assertions and literal
    `/tmp` paths at collection, and it fails a test that leaks threads, fds or processes. Precedent:
    `mdt-cockpit/scripts/test_runtime_lane.py:242-252`.
- Known open cases to root-cause first are listed in section 6.4.

**Build order (section 7):**
- AWP-1: host memory policy plus `host-capacity.json`.
- AWP-2: the cgpd capacity document and predicate.
- AWP-3: run-gate hints and live admission.
- AWP-4: assay back-pressure.
- N3, then AWP-5/6: tmpfs-only materialisation.
- AWP-7: lane-file sweep.
- AWP-10: the flake programme, in parallel from day one.
- AWP-8/9: VM harness and VM lanes.

---

## 0. What this addendum overturns in the base memo

| Base memo | New position | Why |
|---|---|---|
| §1 and Q2: gates `memory.high` 4 GiB is the binding fact; cmru has "a real memory limit" | The 4 GiB tier is a percentage default (`mdt_capacity.py:310-316`), wrong for `dedicated-dev`. cmru's real bound is host RAM+swap under PSI | Directive (2); AGENTS.md:219-225 |
| §2.2: "forcing IO through zswap is a cost"; clean page cache is free to drop | On these hosts dropping clean cache becomes disk refaults (v1001: about 33 GiB re-read [M]). A tree held once in a shared tmpfs and compressed into zswap is the better trade on 6 MB/s disks | Directive (4); probe |
| §2.2: never bind-mount host tmpfs | Bind a **dedicated** cache tmpfs, populated by a helper in `dev-cache.slice`, read-only | §1 kernel facts |
| §2.4: `materialize = "auto"` (cow / tmpfs / serial by capability) | One path: shared template plus per-worker tmpfs copy (after N3). No resolution logic | Directive (4) "no smart decision logic" |
| §3 and WP8: admission = Σ reservations ≤ tier `memory.high` | Admission = one live host predicate (MemAvailable/swap headroom, PSI, disk swap-in) owned by cgpd | Directive (5) |
| §5.2: no KVM, so VM lanes are R0 only, never R2, TCG | Design for KVM; TCG only as an interim footnote. R2 in a VM is allowed once KVM exists | Directive (3) |
| §5.4 and WP10: DinD sidecar is T1 for real Docker | A VM with its own dockerd is the boundary; no new DinD | Directive (3) |
| §5.3 and M3: mdt's 4.1 GiB peak "violates the tier" | Not a defect under the new policy. M3 only informs `jobs_max`; de-prioritised | Directive (2) |
| R-53 `ram_target` 75 % × lane memory / candidate_ram | Removed. Auto derives from live capacity | Directive (5) |

Unaffected and still valid: WP1 (fail-fast), WP2 (B219/B220/B222), WP3 (`parallel_capable`, now trivially true on the
tmpfs path), WP4b (per-worker scratch), WP5-WP7 (shards), WP9 (suite parts), WP12.

---

## 1. (a) Kernel facts, with evidence

### 1.1 Facts

Each row below is one fact. The first table states the fact and where it comes from; the second states what it means
for the design. The # numbers are shared between the two tables.

| # | Fact | Evidence |
|---|---|---|
| K1 | **Who is charged for a tmpfs page:** the memcg of the faulting mm (mmap fault) or, with no fault mm (`write(2)`, `copy_file_range`, `tar -x`), `active_memcg()`, then `current->mm`, then root. Charging happens when the page is first allocated: the first writer, not the mounter and not the uid | `mm/shmem.c:1972` (`mem_cgroup_charge(folio, fault_mm, gfp)`), `:2437` (`fault_mm = vma ? vma->vm_mm : NULL`); `mm/memcontrol.c:1105-1145` (`get_mem_cgroup_from_mm`), `:5163-5172` [F] |
| K2 | **Readers are not charged.** Reading or mapping an existing resident page from another cgroup does not move the charge | `Documentation/admin-guide/cgroup-v2.rst:1963-1975` ("charged to the cgroup which instantiated it and stays charged … until the area is released"; migration does not move it) [F] |
| K3 | **The owner cgroup is removed (v7.2):** offlining reparents the dying memcg's LRU folios and objcgs to the parent | `mm/memcontrol.c:271-303` (`memcg_reparent_objcgs` → `lru_reparent_memcg`), `:4300-4319` (`mem_cgroup_css_offline`) [F] |
| K4 | **Swap-out and swap-in:** swap-out records the owner's private id, walking up to an online ancestor (`mem_cgroup_private_id_get_online`). Swap-in charges that recorded memcg if it is still online, otherwise the faulting mm or the current task | `mm/memcontrol.c:3986-3999`, `:5674-5718`, `:5221-5240`; `mm/swap_state.c:456` [F] |
| K5 | **zswap and shmem:** shmem writeout goes `swap_writeout` → `zswap_store` like anonymous memory. The compressed bytes are force-charged to the owner's memcg memory counter, and also counted in `memory.zswap.current`. The swap slot counts in `memory.swap.current` | `mm/shmem.c:1601-1713`; `mm/page_io.c:251,285`; `mm/memcontrol.c:5963-5990` (`obj_cgroup_charge_zswap`: "PF_MEMALLOC context, charging must succeed") [F] |
| K6 | **zswap limits:** `memory.zswap.max` is checked on every ancestor before a store; 0 means bypass. `memory.zswap.writeback=0` on any ancestor pins the subtree in the pool | `mm/memcontrol.c:5929-5960`; cgroup-v2.rst:1885-1912 [F]; host-setup `CGROUP-NOTES.md:390-409` |
| K7 | **The global pool:** `max_pool_percent` of totalram, with acceptance hysteresis at `accept_threshold_percent`. Hosts: zstd, 25 %, 90 %, shrinker on | `mm/zswap.c:112-123,486-520` [F]; probe [M] |
| K8 | **memory.high vs memory.max:** high throttles and puts the cgroup under reclaim, never OOM-kills, and may be breached. max reclaims and then OOM-kills inside the cgroup. Overcommit on high is the documented strategy | cgroup-v2.rst:1350-1385, :1918-1939 ("Over-committing on high limit … is a viable strategy") [F] |
| K9 | **Protection:** memory.low/min apply relative to the reclaim target. With `memory_recursiveprot` (mounted on both hosts [M]) they spread through subtrees; an ancestor's protection caps a descendant's only for reclaim aimed above that ancestor | cgroup-v2.rst:210-216, :1941-1961 [F] |
| K10 | **tmpfs mount options on 7.2:** size, nr_blocks, nr_inodes, mode, uid, gid, huge, mpol, inode32/64, noswap, quota family, casefold, strict_encoding. **No memcg option.** `noswap` needs `CAP_SYS_ADMIN` in the init user namespace | `mm/shmem.c:4419-4474`, `:4616-4621`; `Documentation/filesystems/tmpfs.rst:107-121` [F] |
| K11 | **KSM:** merges only private anonymous pages, never shared, file-backed or tmpfs pages | `Documentation/admin-guide/mm/ksm.rst:25`; `mm/ksm.c:738-765` (`ksm_compatible` rejects shared/maybe-shared VMAs) [F] |
| K12 | **KSM opt-in inheritance (7.2):** `MMF_VM_MERGE_ANY` is in `MMF_INIT_LEGACY_MASK`. `mm_init` copies it from `current->mm` on fork and on the exec `mm_alloc`, then `ksm_execve` re-enters | `include/linux/mm_types.h:1974-1996`; `kernel/fork.c:1114-1119`; `include/linux/ksm.h:56-75`; `mm/vma_exec.c:129` [F] |
| K13 | **Swap IO is charged to the page owner's blkcg,** so dev.slice `io.max` throttles swap IO of dev pages | `mm/page_io.c:319,439,459` (`bio_associate_blkg_from_page`) [F]; `host-setup.env.example:179-186` |
| K14 | **btrfs snapshots and reflinks share extents, not page cache:** every clone starts with a cold cache | `materialize-experiment-REPORT.md:14` [M] |
| K15 | **systemd-oomd:** `ManagedOOMMemoryPressureLimit` is the fraction of a 10 s window in which **all** tasks were delayed (PSI full), held for `…DurationSec` (default 30 s); it kills the descendant with the most reclaim activity. `SwapUsedLimit` (default 90 %) acts when both the memory-used and swap-used fractions exceed it | systemd `man/oomd.conf.xml:164-202` [F] |

| # | Consequence for the design |
|---|---|
| K1 | Populating from another cgroup moves the charge. Another user alone does nothing |
| K2 | One populated tree can serve N lanes for free |
| K3 | A short-lived populator container in `dev-cache.slice` leaves its pages owned by `dev-cache.slice`, which persists. No long-lived service is needed |
| K4 | Trap: a page swapped while its owner was alive and whose owner then died is recharged to the lane that faults it in. Mitigated by K3, because the slice is the online ancestor. Residual: pages swapped out during the few seconds of populating are negligible |
| K5 | zswap does not free a cgroup's budget, it shrinks pages by the compression ratio. memory.max (RAM) and swap.max are separate budgets |
| K6 | Keep writeback = 1 and zswap.max = max on every dev cgroup |
| K7 | One shared pool of about 2 GiB: the pool is a RAM cost too |
| K8 | Throttle with high only where an agent watches. Under overcommit, prefer PSI-driven admission and kills |
| K9 | Tier weighting = MemoryLow on the tiers works for reclaim triggered by dev.slice's own max, which is the case that matters when dev.slice max = T − R |
| K10 | Ownership must come from placement (K1/K3). Never use `noswap` for the cache |
| K11 | KSM does not dedupe the per-worker tree copies or the cache |
| K12 | One `prctl(PR_SET_MEMORY_MERGE, 1)` in a lane's PID 1 opts in the whole lane. The `MEASUREMENTS.md:243` claim ("does NOT survive execve") is wrong for 7.2 |
| K13 | A swapping lane is IO-throttled, not the host |
| K14 | Under a tight cap the snapshot path re-reads from disk, which is what the v1001 refault count shows |
| K15 | The default 75 %/30 s in host-setup (`host-setup.env.example:307,333`) is very late on a 6 MB/s disk |

### 1.2 Answer: can a cache slice serve lanes without charging them?

Yes, if:
1. **The populator runs in the cache slice** (K1). Run-gate starts an exact-named helper container (tester image,
   `--cgroup-parent dev-cache.slice`) that writes the sealed template into
   `/srv/tree-cache/<key>.tmp-<pid>` and renames it to `<key>`.
   - When the helper exits, its scope goes offline and its pages are reparented to `dev-cache.slice` (K3).
   - Later swap records name the online slice (K4).
2. **Lanes mount it read-only** (`--mount type=bind,src=/srv/tree-cache/<key>,dst=/assay-template,readonly`).
   - Without read-only, an in-place write would modify the shared page. The charge would not move (K2), but the tree
     would be corrupted.
   - Writes go to the per-worker copy or to scratch tmpfs, charged to the lane: correct attribution.
3. **The cache tmpfs is host-mounted by host-setup**: `size` = 25 % of T as a bound only, `huge=never` (the default),
   never `noswap`, mode 0750 for the runner group.
4. **Lifecycle.**
   - `key` = sha256(commit, snapshot selection, assay template format version).
   - Run-gate touches `<key>/.last-used` at each lane start.
   - A daily host timer removes keys unused for 24 h. That is the whole policy.
5. **Accounting.**
   - `dev-cache.slice` sits under dev.slice, so the cache counts in the dev budget once.
   - Its pages are zswappable like everything else. Source text compresses well, about 3x or better [I; N3 measures].

---

## 2. (b) Memory policy for `HOST_ROLE=dedicated-dev`

### 2.1 Formulas (T, S from `/proc/meminfo`; R = `MDT_HOST_RESERVE`, default 512 MiB; M = `MDT_HOST_SWAP_MARGIN`, existing, 512 MiB)

| Unit | MemoryMin | MemoryLow | MemoryHigh | MemoryMax | MemorySwapMax | ZSwap | CPU | OOM |
|---|---|---|---|---|---|---|---|---|
| dev.slice | `-` (or the guaranteed ceiling, unchanged) | `-` | `-` (max) | **T − R** | **S − M** | writeback yes, zswap.max unset | CPUQuota = CPU_MDT × 100 % | `ManagedOOMSwap=kill` (oomd SwapUsedLimit 90 %) |
| dev-interactive | `-` | **min(1 G, 15 % T)** | `-` | `-` | `-` (inherits) | yes | Weight 200 | none (IDE never oomd-killed, as today) |
| dev-gates | `-` | `-` | `-` | `-` | `-` | yes | Weight **100**, no quota | `ManagedOOMMemoryPressure=kill`, **50 % / 20 s** |
| dev-background | `-` | `-` | `-` | `-` | `-` | yes | Weight 20 | kill, 60 % / 30 s |
| dev-buildkitd | `-` | **256 M** | `-` | `-` | `-` | yes | Weight 50 | none |
| dev-cache (new) | `-` | `-` | `-` | `-` | `-` | yes | — | none (holds no processes after populate) |
| system.slice | **min(256 M, R/2)** | `-` | — | — | — | — | — | — |
| ssh.service, cgpd.service, mdt-psi-guard.service | — | — | — | — | — | — | — | **OOMScoreAdjust = −900** (today 0 [M]); docker −500 and containerd −999 already [M] |

Values for the hosts (T = 7.75 GiB on both):

| Host | dev MemoryMax | dev MemorySwapMax | RAM + swap budget |
|---|---|---|---|
| r1002 (S 16 GiB) | 7.25 G | 15.5 G | 22.75 GiB |
| v1001 (S 8 GiB) | 7.25 G | 7.5 G | 14.75 GiB until the re-install restores the swap |

Today both hosts run dev.slice High 7 G / Max 7.5 G, with tiers 3.5/4.5 (interactive), 4/5.5 (background, gates) and
2/2.5 (buildkitd) [M, `/etc/mdt/host-setup.env`]. The dev.slice values barely move; the tier caps disappear.

**Rationale for each choice:**
- **No dev.slice High.** A throttle needs a watching agent (K8). Our "agent" is admission plus oomd. A second throttle
  point only adds latency that tests must not depend on anyway.
- **Weights instead of caps.** cgroup v2 has no memory weight. Under shared reclaim, siblings lose memory in
  proportion to usage above their effective low (K9). So:
  - MemoryLow is the weight: protect the cockpit and the builder's hot set;
  - CPU and IO use real weights;
  - gates gets 100, up from today's 20 (`host-setup.env.example:330`): on a test host the gate tier *is* the
    workload; background soak stays at 20.
- **R covers what is not dev.** Measured non-dev anonymous memory is about 210-225 MB (system.slice anon 177-189 MB +
  user.slice anon 35 MB), plus SUnreclaim of 196-347 MB [M]. 512 MiB therefore covers it, while page cache of
  system.slice and user.slice competes under global reclaim. The installer warns when the p95 of (non-dev anon +
  SUnreclaim) over 1 h exceeds R. It does not refuse.

### 2.2 What changes in mdt-cockpit

- **`mdt_capacity.tier_memory_bytes`** (`:319-332`) becomes role-aware:
  - `dedicated-dev`: dev only, plus `-` for tier High/Max/SwapMax, plus the Low values above;
  - `shared-prod`: today's percentages, unchanged.
- **The wizard** (`mdt-host-setup-wizard.py:458-479,2826-2833`) prints the role-specific proposal and asks
  `HOST_ROLE` before the memory questions. Today the role is asked only in the IO section (`:2023-2045`).
- **`host-setup.env.example`:**
  - §3.3's "Sizing is for a 16 GiB host" comment and the `800M/1.5G` example (`:319-328`) become role-specific;
  - `CGROUP-NOTES.md:880-889` ("sample gate-slice ceiling deliberately conservative") gets a `dedicated-dev`
    paragraph.
- **New units:** a `dev-cache.slice` unit and a `/srv/tree-cache` tmpfs mount unit (dedicated-dev only, opt-in on
  shared-prod).
- **New file `/etc/mdt/host-capacity.json`**, rendered at install: role, T, S, R, M, CPU_MDT, overcommit factors and
  PSI thresholds, and `lane_memory_max` = T − R (dedicated-dev) or the gates MemoryMax (shared-prod). This is the
  *host policy* that tools ask for (AGENTS.md:224).
- **Drop-ins:** OOMScoreAdjust for ssh, cgpd and mdt-psi-guard on both roles. system.slice MemoryMin on dedicated-dev.
- **mdt-psi-guard** stays in `report` on dedicated-dev. Its staged throttling protects production, which does not
  exist there. Its host signal (memory full ≥ 5 %, `host-setup.env.example:153-158`) becomes one input of the
  admission predicate. It does not drive actions.

### 2.3 PSI thresholds that matter after the change

| Signal | Threshold | Consumer | Source |
|---|---|---|---|
| CPU some avg10 (host) | ≥ 25 → stop admitting | admission | operator rule (AGENT-RULES.md:4) |
| memory some avg10 (host or dev.slice) | ≥ 25 → stop admitting | admission | operator rule |
| memory full avg10 (host) | ≥ 5 → stop admitting | admission | mdt-psi-guard host signal |
| disk swap-in (`pswpin` delta over 60 s, not `zswpin`) | ≥ 256 pages/s (1 MiB/s) → stop admitting | admission | operator "heavy swapping"; zswpin is cheap (CGROUP-NOTES.md:527-531) |
| memory full of dev-gates | 50 % for 20 s → oomd kills the leaf with the most reclaim | systemd-oomd | K15 |
| host swap use and memory use both > 90 % | oomd kills the dev descendant with the most swap | systemd-oomd | K15 |

### 2.4 shared-prod (the controller host, production game server)

Nothing changes except the OOMScoreAdjust drop-ins. Tier caps, MemoryMin floors, the PSI guard and IO caps stay as
they are. `host-capacity.json` carries `role = shared-prod`. Admission still uses the same predicate, but with the
gates tier's MemoryMax as the RAM budget.

### 2.5 Failure-mode analysis: a 22.75 GiB budget on 7.75 GiB of RAM

| Phase | Condition | What the kernel does | Cost | What bounds it |
|---|---|---|---|---|
| 0 | dev hot set + cache < T − R | nothing beyond normal cache reclaim | none | — |
| 1 | hot set fits, cold anon/shmem exceeds it | cold pages compressed into zswap (K5); memory.current keeps the compressed bytes | CPU µs per page in and out | pool size (K7) |
| 2 | pool at 25 % of T (about 1.9 GiB) | shrinker writes the coldest pool entries to disk swap; refaults of those come from disk | swap-out IO charged to the owner's blkcg (K13), throttled by dev io.max | dev io.max; the 6 MB/s hoster disk |
| 3 | **hot** set > T − R | continuous reclaim and disk refault = **thrash**. memory.max is not hit hard because swap absorbs, so no OOM | throughput collapse, host-wide memory PSI | admission stops first (memory some ≥ 25 or full ≥ 5); oomd kills the worst gates leaf after 20 s at full ≥ 50 % |
| 4 | dev swap.max or global swap exhausted | memcg OOM at dev.slice max (kills inside dev only); or oomd SwapUsedLimit | killed lane(s) | `memory.max` = T − R keeps system.slice out of memcg OOM; system.slice MemoryMin and OOMScoreAdjust cover global OOM |

**Thrash vs kill:**
- With S ≫ T the kernel always prefers thrash, so a byte limit never stops it.
- What stops it is time: oomd after 20 s of full pressure, and admission before that.
- With the old tier caps the same lanes were throttled at 4 GiB *before* any real shortage existed. That is the 210 k
  high events.
- The new policy lets work use RAM and zswap up to the point where PSI says it hurts.

**Risks and checks:**
- A lane that leaks memory can occupy all of dev's RAM for 20+ s before oomd fires. Interactive keeps its Low and the
  host keeps R.
- Validate oomd's kill and the system.slice protection in the mdt `vm-system` lane (AWP-1 gate) before applying on a
  live host.

### 2.6 Found while probing: host-side work escapes dev.slice

- user.slice peaked at 6.1 GiB (r1002) and 4.7 GiB (v1001) [M]. The runner's ssh sessions run the supervisors,
  run-gate itself and bare-host lanes.
- It is mostly page cache today, so it is reclaimable. But bare-host lanes (for example assay's
  `analysis-r2-pilot`) bypass the dev budget and admission entirely.
- AWP-12: `run-job` launches through a root-owned `systemd-run --slice=dev-gates.slice` wrapper. The command is fixed
  and the runner may only choose the job argv.

---

## 3. (c) Run-gate and assay: lane memory becomes a demand hint; admission and `jobs=auto` follow live capacity

### 3.1 What the repository files may say

| Key | Today | New meaning |
|---|---|---|
| `resources.memory` | the container's `--memory` (`run_gate.py:15794`) and the admission amount (`:4698-4762`) | **peak-RSS hint** for admission and auto only. History p95 replaces it once measured (the mechanism already exists for candidates, `:11081-11160`) |
| `resources.memory_swap` | `--memory-swap` (cmru `run-gate.toml:40` etc. say `16g`: a host fact in a repo file) | **deprecated**. Warn and ignore |
| `resources.cpus` | `--cpus` hard cap (`:15811`) and auto's `cpu_cap` | **CPU-demand hint** (cores the lane keeps busy) |
| `resources.candidate_ram` | floor for the per-candidate RSS estimate | unchanged: a hint |
| `ram_target` | 75 % of lane memory (R-53, SPEC.md:795-800) | **removed** |
| `jobs_max` (assay.toml) | ceiling | unchanged. It is a property of the *suite* (coordinator knee, `serial_only`), not of the host |

Container argv from **host policy** (`/etc/mdt/host-capacity.json`):
- `--memory <lane_memory_max>` (T − R; a runaway guard that keeps leaf `memory.events` attributable, R-29's
  observer requirement);
- `--memory-swap -1`;
- no `--cpus`;
- `--mount type=tmpfs` scratch, with size = `lane_memory_max` as a bound only;
- `--init` as today.

### 3.2 One admission predicate, owned by cgpd

cgpd already publishes gates-slice facts to run-gate (`run_gate.py:5896-5935`). It gains a host capacity document
(meminfo, dev.slice and tier current/stat, PSI for host and dev, 60 s `pswpin` rate, and `host-capacity.json`) and one
function, `admit(request)`. The library is shared by `cgpctl`, run-gate, assay and remote-gate.

```
H_ram  = min(MemAvailable - R, dev.max - dev.current + dev.file)     # reclaimable cache counts as free
H_swap = min(SwapFree - M,     dev.swap.max - dev.swap.current)
admit(peak P, hot fraction h, cpu c) :=
      h*P <= H_ram  and  (1-h)*P <= H_swap
  and Σ c(admitted) + c <= CPU_MDT * 1.25          # cores+1 rule on 4 vCPU = 5
  and stop signals of §2.3 all clear
h = 0.6 until cgpd has a measured hot fraction for the lane (DAMON hot set or anon after reclaim); then the measured p95
```

The second line (H_swap) is skipped when `S = 0`; a host without swap is RAM-only.

**This replaces two things:**
- **R-29's `memory.current + declared ≤ memory.max`** (`run_gate.py:4752-4762`). It counts reclaimable cache as used:
  v1001's gates tier holds 1.49 GB of file pages out of 2.69 GB current [M]. With max = 5.5 G, a lane declaring 4 G is
  refused although 1.5 GB is free cache.
- **R-45's `cur + request > 0.9 × max`** (`:5935`).

**Wait policy:**
- Admission waits and polls every 30 s, as in R-45.
- At the `admission_wait` bound it proceeds with one warning **unless** memory full ≥ 5 or disk swap-in is heavy. In
  that case it keeps waiting. R2 supervisors use an unbounded wait.
- A verdict never depends on admission (unchanged rule).

### 3.3 `jobs = "auto"`

```
N = clamp(1, jobs_max, min(free_cpu_slots, floor(H_ram / (h * candidate_P)), candidates_remaining))
```

- `free_cpu_slots` = CPU_MDT × 1.25 − Σ admitted CPU hints.
- No btrfs or `cheap_clone` term: the tmpfs path is always parallel-capable (section 4), so `run_gate.py:4639-4657`
  and assay's guards (`mutation.py:3639-3655`, `runner.py:6505-6515`) drop the btrfs condition.
- The INFO line prints every input.

### 3.4 Assay: back-pressure instead of a constant

Assay already has an admission budget per candidate (B207: `--memory-budget-bytes`, README.md:358-364,
CONSUMERS.md:4557-4566).

- Run-gate passes `ASSAY_R2_MEMORY_BUDGET_BYTES = H_ram` at start.
- **Before each dispatch** assay evaluates the stop signals of §2.3 (from `/proc/pressure/*` and `/proc/vmstat`, or
  through cgpd when visible). While any signal is set, it holds the next start: pause, never kill.
- It records `admission_wait_seconds` in progress.

Workers are therefore the *maximum* concurrency, and real concurrency drops under pressure without a controller.
Verdicts are unaffected: a pause only changes wall time.

### 3.5 What the operator's "4-5 cmru instances" needs

| Resource | Demand of 5 instances |
|---|---|
| RAM | 5 × (1.0-1.75 GiB) peak = 5-8.75 GiB, against H_ram ≈ 6.5 GiB idle plus 15.5 GiB swap on r1002 |
| CPU | 5 × 0.85 core = 4.25 cores on 4 vCPU = the cores+1 rule |

- With h ≈ 0.6 the hot set is about 3-5.25 GiB, which fits.
- With fail-fast and `--no-cov` (base WP1) RSS should drop further [I].
- Gate: measurement N1 (section 8). It runs 2, 3, 4 and 5 instances on r1002 after AWP-1 and records throughput
  (candidates per hour), PSI, `zswpin/pswpin`, and the oomd and memcg events. The knee decides the default CPU
  overcommit factor (1.25).

---

## 4. (d) cow vs tmpfs, revisited under the directives

### 4.1 What was measured, and why it is not the deciding case

- The A/B (`assay-tmpfs-REPORT.md:36-57`) found no wall gain on r1002 (0.06 vs 0.09-0.12 s materialise per
  candidate), 18-25 % fewer bytes written, and +280/+670 MB peak at jobs 1/3.
- It ran on a disk with headroom, without memory pressure, and with the per-worker template on btrfs.
- The directives target the opposite case:
  - full hoster disks at about 6 MB/s;
  - overcommitted RAM;
  - tight caps already causing refaults (v1001: 8.78 M file refaults in gates [M]).

### 4.2 Recommended architecture (one path)

```
host tmpfs /srv/tree-cache  (dev.slice/dev-cache.slice owns pages; populated once per key by a helper container)
        │  bind, read-only
        ▼
lane container (dev-gates, leaf memcg)
   /assay-template  (RO, shared by every worker / instance / resume / shard on this host)
   /work            (container tmpfs, charged to lane)
      w0/ w1/ w2/   per-worker copy: cp -a /assay-template → wN at worker start (RAM→RAM, ~1 s for 175 MB)
      scratch/wN/   TMPDIR HOME XDG_CACHE_HOME COVERAGE_FILE PYTHONPYCACHEPREFIX
   candidate: mutate in place → run → restore + stat-walk + primed-index status (B219/B222)
   ANY anomaly (crashed, hung, budget, signalled leader, limit event, dirty walk) → rm -rf wN; copy again
```

**Properties:**
- **Disk:** zero disk reads for the tree after populate; no tree writes to disk.
- **Memory pressure:** pages go to zswap, not to a disk refault (K5, K14).
- **RAM:** one tree per host plus N copies, which are mostly cold and compress.
- **No privilege**, no btrfs, no overlay, and **no decision logic**. The anomaly rule is "drop and recopy", trivially
  correct.
- `/dev/shm` is not used. A `--mount type=tmpfs` replaces the 64 MiB default (RG-163).

**Rejected alternatives:**

| Alternative | Why rejected |
|---|---|
| Per-worker overlay on the shared lower | Upper on host tmpfs, mounted by dockerd through `--mount type=volume,volume-driver=local,volume-opt=type=overlay`; this is a valid unprivileged-for-the-lane option. It saves about 140 MB per worker (the measured upper is about 37 MB per live candidate, `materialize-experiment-REPORT.md:44`). Cost: host-side upper directories and cleanup, and no in-container reset (an anomaly forces a container restart). Not now; reopen if N3 shows copies above 25 % of a lane's RSS |
| btrfs snapshot per candidate (today) | Kept running until N3 passes. Then retired as the assay materialisation default. `/srv/cow` stays for cowtree's other users |
| Per-worker copy from a btrfs template (today's tmpfs-worker) | Superseded: the template moves to the cache tmpfs |

### 4.3 What must be measured to confirm (N3, N4; section 8)

1. **Wall and IO under a slow disk.**
   - Arms: cow vs cache-tmpfs, jobs 1 and 3. Lane: `libraries/worktree` (as in the A/B) plus one long lane (pwmcp,
     20 candidates).
   - Each arm twice: unthrottled, and with the lane container at `--device-write-bps <dev>:6mb
     --device-read-iops <dev>:300`. Buffered writeback is blkcg-attributed on cgroup v2, so this throttles the
     container's real writes.
   - Metrics: total wall, `phase_seconds`, container and `dev-cache.slice` `io.stat`.
2. **Behaviour under memory pressure.**
   - Same arms, with an exact-named sibling `stress-ng --vm 1 --vm-bytes <T−R−2G> --vm-keep` in dev-gates so that
     dev.slice reclaims.
   - Metrics: `workingset_refault_file` (cow) vs `zswpin` (tmpfs), wall, PSI.
3. **zswap ratio for tree pages:** `memory.stat` `zswapped / zswap` in `dev-cache.slice` after the pressure run.
4. **Faults:** per-candidate `ru_majflt` (the assay progress observer already samples `memory.stat`; add `pgmajfault`).
5. **KSM (N4):** the lane's PID 1 does `prctl(PR_SET_MEMORY_MERGE, 1)` (K12). Compare 3 workers with and without it.
   - Metrics: `/proc/<pid>/ksm_stat` (`ksm_merging_pages`, `ksm_process_profit`), global `general_profit`, and ksmd
     CPU from the `/proc/<ksmd>/stat` delta.
   - The hosts currently merge about nothing: `pages_sharing` 31/47, `general_profit` 236/293 kB, while the advisor
     keeps `pages_to_scan` at 30000 [M].
   - Expect a low yield on CPython heaps (pointers, refcount churn) [I].

**Decision rules:**

| Question | Adopt or keep when |
|---|---|
| Single tmpfs path | unthrottled wall within +5 % of cow, **and** throttled wall at least 15 % better **or** disk write bytes at least 30 % lower, **and** no oomd or memcg kill in the pressure arm |
| Forced KSM on lanes | `general_profit` ≥ 10 % of the lanes' anon RSS **and** ksmd ≤ 5 % of host CPU. Otherwise lanes stay opted out; VM lanes stay opted in (section 5) |

---

## 5. (e) The VM vs container boundary, revised per directive (3)

### 5.1 The rule

- **Containers** (tester-unified, host daemon, gates slice) run *userland* tests against fakes. The host daemon is
  never the subject of a test.
- **VM lanes** run every test that needs one of the following:
  - a real Docker daemon (image permissions, `--user`/`--read-only` semantics, networks, compose, cgroup-parent
    placement, governance);
  - systemd as PID 1, cgroup delegation, sysctl or slices;
  - kernel-global state: loop devices, swap, mounts, reboot.

  The guest runs its **own dockerd**, and the test container runs inside the guest. This replaces the privileged-DinD
  T1 tier. DinD needs `--privileged` on the host kernel, which is the hazard class of the loop-swap incident (memory
  `cgroupns-host-loop-device-host-hang-incident.md:11-26`).
- **KVM** is a schedule dependency. Lanes are built now. Under TCG they run correctly but slowly; they become fast
  when `/dev/kvm` appears, with no design change.

### 5.2 Cost model

| Item | KVM [I unless stated] | TCG today [M] (footnote) |
|---|---|---|
| Boot to SSH, prepared ready-base without cloud-init | 3-10 s | about 66 s (`testing/vm/DESIGN.md:407-420`) |
| CPU | 0.9-1.0× native | 1/21-1/26 of native (Geekbench, `USAGE-TESTING.md:490-534`) |
| Memory per VM | guest working set + QEMU about 150 MB; dockerd and containerd in the guest about 100-150 MB. Free-page reporting returns freed guest RAM; **KSM merges identical kernel and base pages across concurrent VMs** (QEMU guest RAM is private anonymous memory, K11) | runner peak 3.09 GB for a 4 GiB guest |
| Image availability | ready-base qcow2 per (OS, dockerd, tester image digest set) with images pre-loaded: 0 s per run | same |
| Disk | per-run qcow2 overlay on tmpfs (RAM-first, charged to the lane, dropped at destroy) | same |
| Assay level | R0/R1, and **R2 allowed** (CPU parity) | R0 smoke only |

### 5.3 Which lanes move

| Lane or tests | Move | Stays in a container |
|---|---|---|
| ciu | real-network, compose, governance-placement and enroll (users, sshd, docker) tests → `ciu-vm` lane (guest dockerd). Today's cmru tester-gate DinD `[lanes.enroll]` is retired when `ciu-vm` passes | all fake-based tests (T0), parallel after WP4b |
| mdt-cockpit | the existing `vm-system` (systemd, slices, IO governance) gains the runtime-image tests that start containers (`scripts/test_runtime_lane.py`) | the `host_setup` wizard logic (assay-full). The 4.1 GiB peak is fine under the new policy |
| modern-debian-tools-python-debug | none: retire its R2 lane (diverged duplicate, base memo §5.3) | — |
| <private-host>-guide | `tests/test_soulmask_monitor_docker.py` → VM lane; a skip counts as a failure (today it skips silently, `run-gate.toml:5-7`) | the rest, `jobs_max = 3` after M2/M4 |
| fake-Docker tests (dstdns uid 65534 smoke, `dstdns-p4-REVIEW.md:9-13`) | paired contract cases fake ⇄ guest dockerd in a dstdns VM lane | the fakes |
| run-gate | its real-container launch tests (argv: `--memory`, mounts, `--init`, names) and the doctor/mountinfo R0 baseline that fails on the hosts → `run-gate-vm` | everything else. The fake-daemon flakes are test defects (section 6), not VM questions |
| debian-install-v2 | already VM (`os_integration`) | — |

### 5.4 What vmctl and its harness need

`scripts/debian-install-v2/testing/vm/vmctl` hard-codes `-accel tcg,thread=multi` (`:312-320`). It needs:
1. **`--accel auto`:** `kvm` and `-cpu host` when `/dev/kvm` is readable and writable, else `tcg`. The accel is
   recorded in evidence.
2. **Memory:** `-object memory-backend-ram,id=m0,size=…,merge=on` (KSM) and
   `-device virtio-balloon,free-page-reporting=on`. Guest size comes from the lane hint.
3. **`prepare-ready-base --docker-images <digest…>`:** Debian plus Docker engine plus `docker load` of the digests,
   then flatten. It is immutable and keyed by the digest set, and reuses the existing `prepare-base` / `snapshot`
   flow (`:139-194,417-431`).
4. **`--run-dir <tmpfs path>`:** the per-run overlay goes on the lane's tmpfs.
5. **Checkout transfer:** a tar over ssh into the guest (no virtiofsd, no root). Results come back through
   `vmctl copy`.
6. **The run-gate `mode = "vm"` contract** (base WP11): `prepare/start/wait/exec/collect/destroy`; destroy on every
   exit path; budget owned by run-gate.
7. **Placement:** QEMU runs inside the project's unprivileged runner container in dev-gates, with `--device /dev/kvm`
   only when present, `--init` and an exact name. Networking is user-mode only, no TAP. Admission uses the guest size
   as the hint.

---

## 6. (f) Tests independent of host load

### 6.1 Principle (AGENTS.md:209-217)

A result that differs under load, by host or "sometimes" is a defect: a race, a fixed sleep or timeout, SIGPIPE under
`pipefail`, shared state, ordering, an unbounded wait, or leaked threads, fds or processes. Reruns are not a
resolution.

### 6.2 Finding flakes systematically

**(a) Inventory, passive and free: `run-gate flakes [LANE] [--json]`.**
- From run-gate history and lane logs (junit when present), group by (lane, commit, image digest) and list test ids
  whose outcome differs inside a group.
- Assay R0 baselines and rejudges add data points: the same tree and image with a different outcome is a flake by
  definition.
- Output is a ledger: test id, failure signatures, hosts, PSI at start.

**(b) A `flakehunt` lane (run-gate lane kind or tester-base template; a hunt, not a gate).**
- Repeat a selection (whole suite, or ledger ids) until it fails or N = 20, under fixed **profiles**. Each profile is
  a cgroup recipe, not a hope about load:

| Profile | Mechanism (exact names, dev-gates, `--init`, removed by name) | Exposes |
|---|---|---|
| `slow-cpu` | lane container `--cpus 0.25` (cpu.max): everything is 4-16x slower, deterministically | fixed sleeps, short timeouts, wall-clock asserts |
| `cpu-contended` | sibling `stress-ng --cpu 4` at weight 1000 vs lane weight 10 | scheduling-order races, thread start/stop races |
| `slow-disk` | lane `--device-write-bps <dev>:6mb --device-read-iops <dev>:300` | fsync and IO waits with timeouts; models the full hoster disk |
| `mem-pressure` | lane leaf `--memory` = observed peak × 0.8 with swap allowed, plus a sibling `stress-ng --vm` | refault-induced stalls, timeouts around subprocess start |
| `order-shared` | `-p randomly` with recorded seeds, plus `-n 4` xdist, plus each failing test alone | shared `HOME`, `/tmp` and global state; order dependence |

- Output: per test id × profile, pass/fail counts with the first-failure trace.
- **Any** failure is a backlog item with a root-cause class. A fix is proven by 20 green repeats under the same
  profile.
- Hard rules: never `--privileged`; never `--pid/--net/--cgroupns=host`; stressors only as exact-named containers in
  dev-gates; no host-global state.

**(c) Enforcement in every gate: a shared pytest plugin `estate-test-policy`.**
- It lives in tester-base and is loaded by tester-unified lanes.
- **Collection lint** (AST). It fails collection unless the test carries
  `@pytest.mark.hardware_property(reason=…, backlog="XX-NN")` (the AGENTS.md:215-217 exception):
  - `time.sleep(<const>)` followed by an assertion in the same test;
  - literal timeouts below 120 s in `subprocess.*`, `wait`, `join`, `queue.get`, `socket.settimeout` and `Event.wait`.
    Generalises `mdt-cockpit/scripts/test_runtime_lane.py:242-252`;
  - assertions comparing `time.monotonic()/time.time()` deltas;
  - literal `/tmp/` paths;
  - reads of `/proc/pressure`, `os.cpu_count()` or `/proc/meminfo` inside assertions.
- **Runtime checks** (autouse fixture): fail a test that leaves new threads, child processes or fds open after
  teardown. Report all of them, not just the first.
- **Injected clocks** are the remedy pattern: `profile_admission`'s injected `clock/sleeper`
  (`run_gate.py:5896-5905`).
- **Shell gate scripts:** `shellcheck` plus a rule that `set -o pipefail` lines contain no `| head` or `| grep -q` on
  a producer that keeps writing.
- **Release rule:** one `flakehunt` pass (`slow-cpu` × 3, `slow-disk` × 1) is part of every release gate (R0+R1+R3
  plus review). Any failure blocks.

### 6.3 Root-cause classes and fixes

| Class | Fix |
|---|---|
| Sleep as synchronisation | event, condition or poll with a failsafe of at least 120 s |
| Short timeout | failsafe floor, or an injected clock |
| Partial reads on stream sockets | length or newline framing; read to the delimiter |
| Non-atomic fixture files read by another thread | write to a temp file, then `os.replace` |
| Daemon threads outliving teardown | join with a failsafe; the leak check |
| Shared `HOME`, `/tmp` or checkout | per-test `tmp_path`; per-worker env (base WP4b) |
| Ordering | explicit fixtures; no module state |
| Host facts in assertions | inject them |

### 6.4 Known open cases (root-cause first; each becomes a ledger seed)

| Case | Evidence | First hypothesis [I] |
|---|---|---|
| assay `test_self_qualification_container.py::test_outer_runner_launches_bounded_cgroup_visible_container_and_reads_job_status[b110-screen…]` fails on v1001, passes on r1002 at the same sha: "missing required environment fact ASSAY_B105_GATE_EXPECTED_TREE", log `worktree-untracked-by-assays-own-query` | `RP/reports/rigor-merge-prep-REPORT.md:26-30`. The brief reports three distinct v1001 failure modes; only this one is written down, so the other two must be recovered from the v1001 lane logs | shared checkout state: an untracked artefact from a concurrent process in the same checkout (v1001 runs campaigns from shared checkouts) |
| run-gate RG-160: `test_reattach_adopts_recorded_session_stop_called_with_it`, "ctl stop unparsable response" from the in-test fake daemon (runs 2 and 3 failed, run 4 passed) | `RP/reports/fix-r2-lanes-REPORT.md:38-40` | the fake daemon reads its per-verb plan files at request time (`tests/test_run_gate.py:305-325`). A plan file rewritten non-atomically while the handler thread reads it yields a truncated body. Confirm with `slow-cpu`; fix with `os.replace` |
| run-gate `TestStall*` environment-sensitive outside the gate | `RP/reports/rg-154-REPORT.md:16`, `fix-r2-lanes-REPORT.md:47` | timing thresholds |
| nyxloom NL-12 timing flake (`test_behavioral.py:932`); `cfg_daemon` thread outlives teardown | `RP/reports/nyx-3-REPORT.md:61`; `BUGFIX-PROGRAM-2026-11-04.md:304` | leaked thread plus a timing assert |
| ciu CIU-101 flaky warning test; `TestPermissionRemovalRetry` xdist-order flake | `BUGFIX-PROGRAM-2026-11-04.md:280`; `ciu-4-REPORT.md:28` | order or shared state |
| rigor chain "1 s timing test" | `RP/reports/rigor-chain-REVIEW.md:85` | wall-clock assertion |
| cgroup-profiler CP-53 accept-loop (fixed) | `cgp2-pkg3-REPORT.md:7` | template for the fix pattern |

---

## 7. Work packages (ordered)

| # | Package | Owner | Size | Depends | Gate / acceptance | Risk |
|---|---|---|---|---|---|---|
| AWP-0 | **N0 baseline:** 1 h of 1 s sampling of dev and tier memory, events, PSI, `pswpin/zswpin` and refaults on both hosts (= base M6) | controller (read-only) | S | — | data file plus summary | none |
| AWP-1 | **dedicated-dev memory policy** (section 2.1-2.2): role-aware `tier_memory_bytes`, wizard order, env example, units (`dev-cache.slice`, `/srv/tree-cache` mount), system.slice MemoryMin, OOMScoreAdjust drop-ins, oomd limits, `/etc/mdt/host-capacity.json` | mdt-cockpit | M | — | render tests for T=7.75 G / S ∈ {0, 8, 16} G × both roles; `vm-system` lane proves (i) an oomd kill of a gates leaf at full pressure, (ii) ssh, cgpd and dockerd survive a dev memcg OOM; live apply on r1002 between campaigns, then N0 again; then v1001 | a leaking lane can fill dev RAM for 20 s or more before oomd. Keep interactive Low and R; verify oomd is active (it is [M]) |
| AWP-2 | **cgpd host capacity document + `admit()` predicate** (section 3.2), `cgpctl capacity --json` | cgroup-profiler | S-M | AWP-1 (reads `host-capacity.json`; falls back to role `shared-prod` semantics when absent, with a WARNING) | unit tests on fixture cgroupfs/meminfo/PSI; live read on both hosts | predicate drift between consumers: one library, a version floor in run-gate |
| AWP-3 | **run-gate hints, not caps** (section 3.1-3.3): container argv from host policy, R-29 and R-45 replaced by `admit()`, auto from live capacity, `memory_swap` deprecated, `ram_target` removed, `ASSAY_R2_MEMORY_BUDGET_BYTES` passed, WARNING on any clamp; SPEC R-29/R-45/R-53 rewritten | run-gate | M | AWP-2; base WP3 | run-gate R0/R1; a live two-lane contention test on r1002 (second lane waits while memory full ≥ 5, admits after); `--dry-run` prints every predicate input | old pinned pyz consumers keep the old semantics: document it, and floor dstdns on the new run-gate |
| AWP-4 | **assay back-pressure** (section 3.4) | assay | S | — | assay gate; a pilot under a `stress-ng` sibling shows pauses in progress, identical buckets | wall-time variance: never in a verdict |
| AWP-5 | **template cache populate** (section 1.2): helper container in `dev-cache.slice`, atomic key directory, `.last-used`, read-only bind, cleanup timer (the timer in mdt-cockpit) | run-gate (+ mdt-cockpit timer) | S-M | AWP-1, N3 | test: lane memcg `memory.stat shmem` does not grow with the template; `dev-cache.slice` does; a second lane reuses the key | stale keys fill the tmpfs: 25 % size bound plus 24 h expiry |
| AWP-6 | **assay tmpfs-only materialisation** (section 4.2): template from `/assay-template`, per-worker copy on `/work`, drop-and-recopy after any anomaly, per-worker scratch env (base WP4b), btrfs guards removed | assay | M | AWP-5, N3 | B223 test list, plus: an anomaly forces a recopy; jobs 3 on a host without `/srv/cow`; buckets identical to cow on a 56-candidate pilot | the in-place residue blind spot (`materialize-experiment-REPORT.md` §4 tests (b), (f), (g)) |
| AWP-7 | **lane-file sweep:** remove host facts (`memory_swap`, caps meant as host limits); keep hints and `jobs_max` | project owners (cmru, mdt-cockpit, pwmcp, ciu, cgroup-profiler, dstdns) | S each | AWP-3 | each lane's `--dry-run` shows hint, host cap and selected N | campaign identity: do it between campaigns |
| AWP-8 | **vmctl for KVM/TCG** (section 5.4) plus the run-gate `mode = "vm"` contract (base WP11) | debian-install-v2 + run-gate | M | AWP-3 | existing `r1-vm-real-commit` and `vm-system` pass unchanged under TCG; a ready-base with a preloaded tester image runs `docker run --user 65534` inside the guest | TCG budgets until KVM exists |
| AWP-9 | **VM lanes** (section 5.3): <private-host> docker test, ciu-vm, run-gate-vm, dstdns contract smoke; mdt runtime tests into `vm-system`; retire the python-debug R2 lane | project owners | M each | AWP-8 | each lane: skip = fail; a planted defect (the uid-65534 smoke) is caught | TCG time until KVM; schedule R0 only until then |
| AWP-10 | **load-independence programme** (section 6): (a) `run-gate flakes`; (b) the `flakehunt` lane and profiles; (c) the `estate-test-policy` plugin; (d) fix the section 6.4 cases | run-gate (a, b), tester-base (c), project owners (d) | M (a-c), S each (d) | none: start now | (a) reproduces the known cases from history; (b) RG-160 reproduces under `slow-cpu` before its fix and survives 20 repeats after; (c) the plugin runs on every tester-unified lane with an allowlist only through the marker plus backlog id | plugin false positives: marker with reason, reviewed |
| AWP-11 | **measurements N1-N4** (section 8) | controller / measuring agents | S-M | AWP-1 (N1), AWP-5 (N3) | reports with decisions recorded | host load: run with the predicate, record PSI |
| AWP-12 | **runner jobs into dev.slice** (section 2.6): a root-owned fixed `systemd-run --slice=dev-gates.slice` wrapper for `run-job` | mdt-cockpit / scripts/netcup runner | S | AWP-1 | `run-job` processes appear under dev-gates; user.slice anon stays small | wrapper must not take arbitrary unit options |

**Order:**
1. AWP-0 and AWP-10 (start now, independent).
2. AWP-1.
3. N1.
4. AWP-2.
5. AWP-3 and AWP-4.
6. N3.
7. AWP-5 and AWP-6.
8. AWP-7.
9. AWP-12.
10. AWP-8.
11. AWP-9.

Base memo WP1, WP2 and WP5-WP7 proceed in parallel. Base WP8 is replaced by AWP-2/3, WP10 is dropped, and WP4 is
replaced by AWP-6.

---

## 8. Measurements

| # | What | How | Decides |
|---|---|---|---|
| N0 | Baseline (AWP-0) | 1 s sampler for 1 h: `memory.{current,events,stat,pressure}` of dev and tiers; `/proc/{meminfo,vmstat,pressure/*}` | before/after evidence for AWP-1 |
| N1 | cmru instance knee | after AWP-1 on r1002: 2, 3, 4 and 5 concurrent cmru R2 instances (shards of one plan, `--jobs 1` each), 1 h each step. Record candidates/h, CPU and memory PSI, `zswpin`/`pswpin`, zswap ratio, oomd and memcg events, anon and zswapped per leaf | the CPU overcommit factor; default h; whether 4-5 instances is the right target |
| N2 | Hot fraction h per lane | cgpd DAMON hot set (existing `--damon`) or `memory.reclaim` probing on a lane leaf during a run | replaces h = 0.6 |
| N3 | cow vs cache-tmpfs under a slow disk and under pressure | section 4.3 items 1-4 | AWP-5/6 go or no-go |
| N4 | Forced KSM (lanes, and 3 concurrent identical VMs once AWP-8 exists) | section 4.3 item 5 | KSM policy per lane class |
| N5 | KVM VM cost (when KVM exists) | boot-to-SSH, dockerd-ready, per-test overhead vs container, RSS per VM with and without KSM | VM lane admission hints; R2-in-VM scheduling |

---

## 9. Risks and open points

- **Overcommit moves the failure from "throttled early" to "thrash, then kill".** Correctness depends on oomd being
  active (it is [M]) and on admission reading PSI. Neither changes a verdict, but a killed lane wastes its run. The
  20 s at 50 % full threshold is a starting value: tune it from N0/N1.
- **v1001 has 8 GiB of swap** after the ZFS evaluation took two partitions, so its budget is 14.75 GiB until the
  planned re-install with swap at the end of the disk (memory `feedback-tests-load-independent-host-owned-limits.md:17`).
- **Debian kernel patches** were not compared with upstream v7.2.11. K3 (LRU reparenting) is the fact most worth
  confirming. Live check: after a populate helper exits, `dev-cache.slice` `memory.stat shmem` must hold the template
  and the helper scope must be gone.
- **The `MEASUREMENTS.md:243` execve claim** (<private-host>) conflicts with K12. Correct the doc after N4 confirms on the
  host.
- **[U]** zswap ratio for tree and heap pages; KSM yield; cmru hot fraction; KVM availability date and form (another
  provider or plan).

## 10. Sources

**Kernel** (upstream stable v7.2.11, fetched read-only from git.kernel.org): `mm/shmem.c`, `mm/memcontrol.c`,
`mm/swap_state.c`, `mm/page_io.c`, `mm/zswap.c`, `mm/ksm.c`, `mm/vma_exec.c`, `kernel/fork.c`,
`include/linux/{ksm.h,mm_types.h}`, `Documentation/admin-guide/cgroup-v2.rst`,
`Documentation/admin-guide/mm/{ksm,zswap}.rst`, `Documentation/filesystems/tmpfs.rst`; systemd `man/oomd.conf.xml`
(main).

**Repository:**
- `AGENTS.md:209-225,329-361`.
- `mdt-cockpit/host-setup/{mdt_capacity.py:212-251,308-332, mdt-host-setup-wizard.py:458-479,2023-2045,2735-2833,
  host-setup.env.example:40-450, CGROUP-NOTES.md:390-560,775-890, units/dev.slice.in:22-68,
  units/dev-gates.slice.in:24-80}`.
- `mdt-cockpit/scripts/test_runtime_lane.py:242-252`.
- `run-gate-project/{run_gate.py:4600-4680,4698-4762,5896-5935,15790-15812, SPEC.md:785-800,965-1010,2535-2545,
  2772-2802}`.
- `run-gate-project/tests/test_run_gate.py:295-330`.
- `assay/{README.md:348-364, docs/CONSUMERS.md:4555-4575}`.
- `scripts/debian-install-v2/{debian_install_v2/templates.py:148-180, testing/vm/vmctl:1-60,139-194,275-320,417-431}`.
- `scripts/<private-host>-guide/MEASUREMENTS.md:221-300`.
- `cmru/run-gate.toml:39-40,74`; `mdt-cockpit/run-gate.toml:30-31,74-75`.

**Reports (RP/reports):** `PARALLELISATION-DESIGN-MEMO-2026-11-05.md`, `assay-tmpfs-REPORT.md`,
`assay-jobs-audit-REPORT.md`, `materialize-experiment-REPORT.md`, `AGENT-RULES.md`, `rigor-merge-prep-REPORT.md`,
`fix-r2-lanes-REPORT.md`, `rg-154-REPORT.md`, `nyx-3-REPORT.md`, `ciu-4-REPORT.md`, `rigor-chain-REVIEW.md`,
`cgp2-pkg3-REPORT.md`, `BUGFIX-PROGRAM-2026-11-04.md`.

**Memory:** `host-shared-with-production-load-rule.md`, `soulmask-memory-pressure-findings.md`,
`cgroupns-host-loop-device-host-hang-incident.md`, `feedback-tests-load-independent-host-owned-limits.md`.

**Host probes** (read-only, as `runner`, 2026-11-04 20:49Z; guards and API checks passed): meminfo, swaps, zswap and
KSM parameters, vm sysctls, cgroup2 mount options, PSI, `memory.*` of dev.slice, its tiers, system.slice, user.slice,
docker, containerd and ssh; unit OOM properties; `/etc/mdt/host-setup.env` memory keys; psi-guard events; df. Raw
output: `<controller-scratch>/-workspaces-vbpub/bfe9ee76-aa16-4929-a108-36a00b55a0e5/scratchpad/probe-{r1002,v1001}.txt`.
