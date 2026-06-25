# gstammtisch — Memory & Swap Architecture

> Synthesis of the two source guides (`GAMINGHOST-SWAP-1.md`, `GAMINGHOST-SWAP-2.md`)
> and the design discussion, corrected against the **actual host** and current
> kernel facts (June 2026). Companion docs: [OBSERVATION.md](OBSERVATION.md)
> (diagnostics) and [SOULMASK.md](SOULMASK.md) (game-server specifics).

## 0. The host (measured, not assumed)

| | |
|---|---|
| **Host** | `gstammtisch`, hosted VM |
| **CPU / RAM** | 8 cores, **16 GB RAM** (the scarce resource) |
| **OS / kernel** | Debian 13 (trixie), kernel **7.0.10-1~bpo13+1** from trixie-backports, `PREEMPT_DYNAMIC` |
| **Disk** | 1 TiB `vda`, **MBR/dos** label, root LV on `vda5` (954.5 GiB), ~69 GiB free inside the extended partition |
| **Storage class** | **thin-provisioned** (TRIM works: `DISC-MAX=2G`); reports `rotational=1` (untrustworthy); scheduler `none` |
| **Game** | Pterodactyl Wings → Docker → Soulmask (`WSServer-Linux-Shipping`), ~13–14 GB RAM, 80 % of 1 core, much of it cold |
| **Dev** | VS Code over SSH, up to 20 containers (incl. SkyWalking), ~12 GB image builds; low steady load, heavy churn |

**Two corrections to the source guides' premises:**
1. Disk is **not** scarce — Docker lives on the 954 GiB root LV. The ~69 GiB free is *dedicated to swap*, so swap can be generous.
2. The host is **oversubscribed by design**: 14 GB game + 12 GB builds + 20 containers cannot all stay in 16 GB RAM. The architecture's job is not "make it all fit" — it's *keep Soulmask's hot set pinned while dev pressure spills through compressed RAM → disk in a controlled, low-priority way*. Dev builds **will** slow under pressure; that is acceptable per the requirements.

---

## 1. The two guides at a glance — decisions & verdicts

| Decision | SWAP-1 | SWAP-2 | **Final** | Why |
|---|---|---|---|---|
| Compression | zswap | zswap | **zswap** | LRU integration; cold pages auto-evict to disk (no zram LRU-inversion) |
| Compressor | zstd | zstd | **zstd** | RAM is the bottleneck, 7 idle cores; ~3:1 vs lz4's ~2:1 |
| Swap layout | 4 files "striped" | 1 partition | **2 labeled partitions** | striping is a *no-op* on one vda; 2 partitions chosen purely for per-device `iostat` visibility |
| Swap size | 16 GB | 20 GB | **~69 GiB (all free)** | disk isn't contended (Docker on root LV); generous overflow + systemd-oomd guards thrash |
| Pool `max_pool_percent` | 35 % | 25 % | **30 %** | ceiling, not reservation; shrinker prevents starvation; tune by observation |
| `accept_threshold_percent` | — | 80 % | **90 %** | SWAP-2's addition; reduces fill/evict thrash |
| Shrinker enable | cmdline ✓ | `vm.zswap_shrinker_enabled` sysctl ✗ | **module/post-boot** | that sysctl doesn't exist; it's `/sys/module/zswap/parameters/shrinker_enabled` |
| `vm.swappiness` | 30 ✗ | 80–100 ✓ | **100** | with zswap, anon reclaim is cheap — *want* it; protect game with cgroup, not swappiness |
| cgroup model | CPU/IO weights | `memory.low/min` + `zswap.writeback` + `io.max` | **SWAP-2's model** | precise, protection-first |
| Build I/O cap | BuildKit env | `io.max` 200 MB/s | **io.max / systemd IO\*Max** | robust, runtime-independent |
| Network | fq_codel + DSCP/tc | prio qdisc + filters | **fq_codel only** | don't build speculative QoS; revisit only if starvation observed |
| Kernel | "stay 6.12; 7.0 doesn't exist" ✗ | "7.0.10 available" ✓ | **on 7.0.10 already; plan toward 6.18 LTS** | see §8 |

---

## 2. zswap, not zram — and why it matters here

Both compress swapped pages in RAM. The difference is what happens when the pool fills:

- **zram** is a fixed-size compressed block device with **no automatic eviction**. Whatever swaps out *first* (cold init data) calcifies in fast RAM; pages swapped *later* (the game's active set during a build spike) spill to slow disk. That's **LRU inversion** — your fastest tier holds your coldest data. (Chris Down, Meta MM: *"do not run zram alongside disk swap wherever possible."*)
- **zswap** is integrated into the kernel reclaim path: it compresses into a RAM pool and, when pressure rises, the **shrinker** writes the *coldest* compressed pages to disk **while they stay compressed** (single compression — no decompress/recompress). Hot pages stay in the pool; cold pages drain.

For gstammtisch — one process holding lots of cold pages, sharing the box with bursty dev work — zswap's tiering is exactly right. Your intuition ("compression beats slow disk swap") holds *because zswap keeps the right pages compressed*; zram would give fast access to the wrong ones.

**Same-value / zero pages:** zswap special-cases "same-filled" pages (every word identical — the all-zero page being the common case from `calloc`/fresh container heaps). It stores just the fill value in the entry metadata — **no compression, ~no pool memory** — and reconstructs on fault-in. This is automatic and on by default (kernel 7.0 no longer exposes a `same_filled` knob; it's built in). It is *not* cross-page dedup; for that see **KSM** (§6).

---

## 3. The zstd boot-fallback gotcha (and the real fix)

You will see this at every boot:
```
[ 0.553294] zswap: compressor zstd not available, using default lzo
```
**Why:** zswap is built into the kernel and selects its compressor in an early initcall (~0.55 s) — *before* the initramfs loads any module. `zstd` is a module, so it isn't available yet → fallback to lzo. **Adding `zstd` to the initramfs does not fix this** (the built-in initcall still runs first), which is why that attempt failed.

**The fix — configure zswap post-boot, in order, via a oneshot:**
[`zswap-config.service`](files/etc/systemd/system/zswap-config.service) runs `Before=swap.target`:
`modprobe zstd` → set `compressor=zstd`, `max_pool_percent=30`, `accept_threshold_percent=90`, `shrinker_enabled=Y` → **then** `enabled=1`. Because the compressor is set before enabling, zswap's first and only pool is zstd. **No zswap.* tokens on the GRUB cmdline** — all five parameters are runtime-writable, and driving them post-boot is cleaner (the early lzo line becomes cosmetic; with `enabled` also deferred there's no lzo pool at all).

> `modprobe zstd` is required before `echo zstd > .../compressor` succeeds deterministically — the write *can* autoload it post-boot, but the service makes it explicit. `/etc/modules-load.d/zstd.conf` also force-loads it early.

Verify after boot: `cat /sys/module/zswap/parameters/compressor` → `zstd`. See [OBSERVATION.md §1](OBSERVATION.md).

---

## 4. sysctl tuning — with reasoning

File: [`99-gstammtisch-memory.conf`](files/etc/sysctl.d/99-gstammtisch-memory.conf).

| Knob | Value | Reasoning |
|---|---|---|
| `vm.swappiness` | **100** | With zswap, reclaiming anon pages is cheap (compressed RAM, not disk). High swappiness pushes cold anon into the pool and keeps file cache (Docker layers, build cache) resident. **SWAP-1's 30 was backwards** — it would hoard cold anon in uncompressed RAM and evict useful cache. *Do not use swappiness to protect the game* — that's `memory.min`'s job (cgroup v2 has no per-cgroup swappiness). |
| `vm.vfs_cache_pressure` | **50** | Keep dentry/inode caches; Docker does huge amounts of path lookup during create/teardown/builds. |
| `vm.watermark_scale_factor` | **50** | Wake kswapd earlier → background reclaim starts before pressure is acute → less blocking direct reclaim under build storms. |
| `vm.page-cluster` | **0** | No swap readahead. Wasteful on SSD/thin backing and on zswap fault-back (random access). |
| `vm.admin_reserve_kbytes` | **65536** | ~64 MB so root can always SSH in and act under OOM. |

---

## 5. cgroup v2 — priority & protection (the heart of it)

The goal: **Soulmask always preempts dev work and is the last thing reclaimed; dev work is throttled and dies first under pressure; standard services keep default priority.** Three slices:

```
/sys/fs/cgroup/
├── (Soulmask docker scope)        ← PROTECTED
│     memory.min  = <DAMON hot+warm>   hard floor; kernel OOMs others before dipping below
│     memory.low  = 12G                best-effort protection; last to be reclaimed
│     memory.zswap.writeback = 0       keep its pages in the FAST pool, never proactively to disk
│     (cpu.weight high via panel / default 100)
├── system.slice/                  ← apt, certbot, sshd, wings… default weights, short-lived
└── dev-workloads.slice/           ← LOW priority, OOM-first
      memory.high = 8G                 throttle reclaim inside the slice
      memory.max  = 14G                hard cap; OOM fires INSIDE this slice only
      memory.zswap.writeback = 1       dev pages may drain to disk (absorb the pressure)
      cpu.weight = 50 / io.weight = 50  Soulmask (100) gets 2:1 under contention
      IO{Read,Write}BandwidthMax=/dev/vda 200M   builds can't saturate the disk
      ManagedOOMMemoryPressure=kill    systemd-oomd kills worst dev offender first
```

**What each knob does under pressure**
- `memory.min` (**hard guarantee**) — the kernel will OOM-kill processes in *other* cgroups before reclaiming below this. This is your **"RAM never swapped" = the hot amount** lever (you can't truly pin a managed binary with `mlock`; `memory.min` is the practical equivalent). **Set it from measurement** (DAMON, §8) — too low and the game faults pages back under pressure → stutter; err high.
- `memory.low` (**best-effort**) — abundant memory → game keeps up to 12 G; under pressure the kernel reclaims unprotected cgroups first; the game is reclaimed *last*, never immune.
- `memory.high` (**throttle**) — dev slice over 8 G → allocations throttle + reclaim *within the slice*; slows the offender without killing.
- `memory.max` (**hard cap → OOM**) — dev slice hits 14 G → OOM kills the largest process *inside dev.slice*; the game is never considered.
- `memory.zswap.writeback` — `0` on Soulmask: its compressed pages stay in the pool (fault-back in µs, never a disk-ms stutter); `1` on dev: dev pages absorb the disk overflow. This asymmetry is the single most game-friendly setting here.
- `cpu.weight` / `io.weight` — proportional under contention only; idle CPU/IO is always available to dev.

**Why swap-IN latency drives these choices:** swap-*out* is async/background; swap-*in* is a synchronous page fault that blocks the process. So protecting the game = keeping its pages where fault-in is fastest (RAM pool, via `memory.zswap.writeback=0` + a correctly-sized `memory.min`).

### 5.1 Applying it (files)
- [`dev-workloads.slice`](files/etc/systemd/system/dev-workloads.slice) — standard knobs via systemd (memory, cpu/io weight, IO bandwidth cap, oomd).
- [`setup-cgroups.sh`](files/usr/local/sbin/setup-cgroups.sh) (run by [`gstammtisch-cgroups.service`](files/etc/systemd/system/gstammtisch-cgroups.service)) — applies the knobs systemd can't: `memory.zswap.writeback` on the dev slice, and locates the Soulmask container's scope to set `memory.min/low` + `memory.zswap.writeback=0`. Re-runnable after the server restarts.

### 5.2 Pterodactyl / Wings integration (be realistic)
Wings creates the Soulmask container under Docker's default cgroup parent, and recreates it on updates — moving its cgroup is fragile. Use **defense in depth**:
1. **Pterodactyl panel** — set Soulmask's memory/CPU/IO limits there. Wings re-applies them on every (re)start. This is the *reliable* lever.
2. **dev containers** — launch into the slice: `docker run --cgroup-parent=dev-workloads.slice --label workload=dev …` (or `cgroup_parent: dev-workloads.slice` in compose). The dev's own devcontainer can stay at default priority so it preempts the containers *it* starts.
3. **`gstammtisch-cgroups.service`** re-asserts the raw Soulmask knobs; optionally trigger it from a Wings post-start hook or a `.timer` to survive container recreation.

### 5.3 systemd-oomd — the safety net
With this much oversubscription the in-kernel OOM killer is slow and may pick a poor victim. [`oomd.conf.d/gstammtisch.conf`](files/etc/systemd/oomd.conf.d/gstammtisch.conf) + `ManagedOOMMemoryPressure=kill` on the dev slice makes systemd-oomd kill the worst **dev** offender fast on PSI/swap pressure — never touching the protected game. This is what makes `swappiness=100` + large swap safe.

---

## 6. KSM & THP

**KSM (Kernel Samepage Merging)** — optional dedup of identical **anonymous** pages across processes. Relevant to "20 containers from the same Python image", with one caveat: **read-only image content (interpreter, libs, .pyc) is already shared via the page cache** — KSM only adds dedup of identical *heap/anon* pages (zeroed arenas, interned objects, identical buffers). Savings are real but workload-dependent.
- **Dev opts containers in** (no app rewrite): call `prctl(PR_SET_MEMORY_MERGE, 1)` at process start — marks all current+future anon memory mergeable, inherited by children. In Python: `ctypes.CDLL(None).prctl(67, 1, 0, 0, 0)` at the top of the entrypoint.
- **Admin enables `ksmd`** ([`ksm.conf`](files/etc/tmpfiles.d/ksm.conf)): `run=1` + advisor `scan-time` (self-tunes scan CPU). Idles cheaply until something is marked. **Never mark the game** (no benefit; adds CoW-fault latency + side-channel surface).
- **Caveat:** KSM enables memory-dedup side channels — fine on a single-tenant host you control; don't extend to untrusted workloads.
- Verify: `/sys/kernel/mm/ksm/pages_sharing`, `general_profit` (if low/negative, not worth it).

**THP** — set `madvise`, not `always` ([`thp.conf`](files/etc/tmpfiles.d/thp.conf)). `always` makes the kernel compress/swap 2 MB units, wasting zswap pool space and hurting the ratio. Apps that benefit opt in with `madvise(MADV_HUGEPAGE)`.

---

## 7. Network

A single game server needs **low latency**, not throughput tuning. Set `net.core.default_qdisc = fq_codel` (fair-queues each flow, keeps bufferbloat low so latency-sensitive game UDP isn't drowned by bursty Docker pulls) and stop there. The DSCP/iptables/prio-qdisc machinery in both source guides is **speculative** — only build it if monitoring actually shows game traffic being starved. (Not enabled by default in this deliverable; add a one-line sysctl if you want it.)

---

## 8. Kernel — you're on 7.0.10; here's what that means

The source guides were wrong in opposite directions; the facts (verified June 2026):
- **Debian 13 "trixie"** released 2025-08-09, **default kernel 6.12 LTS**. SWAP-1's "not released" was stale.
- **Linux 7.0** is real (released 2026-04-12). SWAP-1's "doesn't exist" was wrong; SWAP-2 was right it's available. Your `7.0.10-1~bpo13+1` is it, from **trixie-backports**.

**What 7.0 actually buys this workload:**
- **Swap Table Phase II** — ~20 % better swap-*in* throughput when multiple processes share swapped-out memory (≈ a 20-container fleet). The blocking read-back path is faster here.
- **DAMON interval auto-tuning** — removes the fiddly manual `--sample-us`/`--aggr-us` tuning. **Use it** to measure Soulmask's hot/warm/cold split before setting `memory.min` (§5).

**The watch-item:** 7.0 is **non-LTS** (short support; backports will move you forward). Don't pin-and-forget. The stability landing spot is **6.18 LTS** once it's blessed and in trixie-backports — it carries Swap Table Phase I plus better mTHP allocation under container churn. No urgency; the config matters far more than the version.

**Latency lever:** your kernel is `PREEMPT_DYNAMIC` — if you ever chase game-tick stutter, boot with `preempt=full` (lower latency, small throughput cost) vs. the default `voluntary`. Don't change it preemptively.

---

## 9. Install order

See [README.md](README.md) for the full runbook. Summary:
1. `scripts/install.sh` (copies files, enables units, applies sysctl, brings up zswap+zstd).
2. `partition-editor.py … add-swap … --commit` — create the two swap partitions.
3. Clean `zswap.*` out of GRUB; optional `preempt=full`.
4. DAMON-measure Soulmask hot set → set `SOULMASK_MIN` → `systemctl restart gstammtisch-cgroups`.
5. Panel limits for Soulmask; `--cgroup-parent=dev-workloads.slice` for dev containers.
6. `swap-health watch` to confirm no red flags.

---

## 10. References
- Source guides: `GAMINGHOST-SWAP-1.md`, `GAMINGHOST-SWAP-2.md` (in the work folder).
- Kernel zswap: <https://docs.kernel.org/admin-guide/mm/zswap.html>
- Kernel cgroup v2: <https://docs.kernel.org/admin-guide/cgroup-v2.html>
- Chris Down, zswap vs zram (LRU inversion): referenced in SWAP-2.
- Linux 7.0 MM / swap table: <https://kernelnewbies.org/Linux_7.0>
- Debian 13 release: <https://www.debian.org/News/2025/20250809>
