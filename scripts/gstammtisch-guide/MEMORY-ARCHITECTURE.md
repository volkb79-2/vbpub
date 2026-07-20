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

**zswap decompression is fast but not free — it causes stutter at scale:**

| Page location | Latency per page | 1 000 pages simultaneously | Player experience |
|---|---|---|---|
| Uncompressed RAM | ~50–100 ns | ~0.1 ms | Imperceptible |
| zswap (zstd) | ~2–5 µs | 2–5 ms | Brief stutter on rapid access burst |
| Swap disk (SSD) | ~100 µs–1 ms | 100 ms–1 s | Noticeable pause |
| Swap disk (HDD) | ~1–10 ms | 1–10 s | "Chest open" stall |

The swap-*in* fault is **synchronous**: the game thread blocks until the page is back. Disk-backed swap-in is the dominant cause of game stalls; zswap-backed swap-in is 100–2000× faster but still has a cost when many pages are needed at once (area loads, chest contents). `workingset_refault_anon` is the counter that measures exactly this cost.

**Observed refault rate thresholds (Soulmask, 3 players, 2026-06-27):**

| `refault/s` | State | Action |
|---|---|---|
| 0–30 | Excellent: essentially no zswap pressure | Floor may be lowereable |
| 30–100 | Good: natural steady-state background | No action needed |
| 100–500 (continuous) | Tight: hot tier partially in zswap | Raise `memory.min` |
| 500+ (continuous) | Bad: constant game-loop refaults, visible stutter | Raise `memory.min` immediately |
| 5k–40k for < 60s | Normal: area load event (player entering new zone) | Expected, unavoidable |

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
│     memory.zswap.writeback = 1       cold tail may drain to disk (2026-07-07; zswap LRU = coldest first)
│     (cpu.weight high via panel / default 100)
├── system.slice/                  ← apt, certbot, sshd, wings… default weights, short-lived
├── interactive.slice/             ← devcontainers + IDE: responsive, bounded, never OOM-killed
└── besteffort.slice/              ← test/build stacks: LOW priority, OOM-first, IO-capped
```

The two dev tiers are installed and maintained by the **mdt host-setup**
companion (`modern-debian-tools-python-debug/host-setup/`), not by this guide —
see [README §Scope](README.md#scope). Their knobs, and why some of them can only
be applied at runtime, are documented in that companion's `CGROUP-NOTES.md`.

**What each knob does under pressure**
- `memory.min` (**hard guarantee**) — the kernel will OOM-kill processes in *other* cgroups before reclaiming below this. This is your **"RAM never swapped" = the hot amount** lever (you can't truly pin a managed binary with `mlock`; `memory.min` is the practical equivalent). **Set it from measurement** (DAMON, §8) — too low and the game faults pages back under pressure → stutter; err high.
- `memory.low` (**best-effort**) — abundant memory → game keeps up to 12 G; under pressure the kernel reclaims unprotected cgroups first; the game is reclaimed *last*, never immune.
- `memory.high` (**throttle**) — dev slice over 8 G → allocations throttle + reclaim *within the slice*; slows the offender without killing.
- `memory.max` (**hard cap → OOM**) — dev slice hits 14 G → OOM kills the largest process *inside dev.slice*; the game is never considered.
- `memory.zswap.writeback` — `1` everywhere since 2026-07-07: zswap's LRU means only the coldest-of-cold pages drain to disk, freeing pool RAM. The game observably carries a ~4G genuinely-cold tail. Gate: if login latency regresses, set the game back to `0` (`SOULMASK_WRITEBACK=0`, see MEASUREMENTS.md M4). The pak slice goes further: `memory.zswap.max=0` (pak is zstd-incompressible, 1.006× — zswap would waste CPU+RAM on it; cold pak goes straight to disk).
- `cpu.weight` / `io.weight` — proportional under contention only; idle CPU/IO is always available to dev.

**Why swap-IN latency drives these choices:** swap-*out* is async/background; swap-*in* is a synchronous page fault that blocks the process. So protecting the game = keeping its *hot* pages where fault-in is fastest: uncompressed RAM via a correctly-sized (and chain-effective) `memory.min`, warm pages in zswap (µs faults), and only the genuinely-cold tail on disk. The monitor's `rf_d/s` column (disk refaults) is the alarm that cold-tail paging is touching pages that aren't actually cold.

### 5.0 cgroup v2 knob reference

**Memory knobs** — applied per-cgroup:

| Knob | Direction | Breach effect | Notes |
|---|---|---|---|
| `memory.min` | floor ↑ | Hard guarantee: kernel OOMs others before going below | Never violated even under OOM; set from measurement |
| `memory.low` | soft floor ↑ | Best-effort: cgroup reclaimed last, not immune | Exceeded under severe pressure; good for "prefer to keep" |
| `memory.high` | soft ceiling ↓ | Throttles allocs + aggressive reclaim; process survives | Useful for test pressure; never use on production game |
| `memory.max` | hard ceiling ↓ | OOM kill inside the cgroup | One step too far = crash; don't use for live calibration |
| `memory.zswap.writeback` | 0 / 1 | 0 = pages stay in zswap pool forever (never to disk) | 1 everywhere since 2026-07-07 (cold tail → disk); revert game to 0 if logins regress (MEASUREMENTS.md M4) |

**gstammtisch deployed values:**

| Cgroup | `memory.min` | `memory.low` | `memory.high` | `memory.zswap.writeback` |
|---|---|---|---|---|
| Soulmask game | 6G (3 players; 5G caused login failures) | 12G | 7G (1G login/area-load transient headroom; 8G if logins regress) | 1 (since 2026-07-07) |
| soulmask-paks.slice | 150M (calibrate via MEASUREMENTS.md M2) | — | — | zswap bypassed entirely (`memory.zswap.max=0` — pak incompressible) |
| interactive.slice | 0 | 2G | 5G | **0** — never page the IDE's cold tail to disk (mdt host-setup; systemd < 256 has no directive for this, so it is a raw write) |
| besteffort.slice | 0 | 0 | 8G | 1 (default) |
| system.slice (ancestor) | 7G (protection chain for the game floor) | — | — | — |
| soulmask.slice (ancestor) | 1G (protection chain for the pak floor) | — | — | — |

> **besteffort.slice, 2026-07-10** (`plan-stack-resource-tuning.md` D1/D2 — dstdns
> needs its full stack, 12–15 containers, running for meaningful development):
> `MemoryHigh=4G→8G`, `MemoryMax=6G→12G`, `MemorySwapMax=12G→24G` (columns above
> only cover min/low/high/writeback; max/swap.max live in the unit file). Disk
> IO caps are now **dynamic**: `setup-cgroups.sh` applies `BE_IO_CAP_PCT`
> (default 40%) of the measured `io-baseline.env` ceilings at runtime via
> `systemctl set-property --runtime besteffort.slice`; the unit's static
> `IO*Max` (31M/100/400) is only a boot-window fallback before that runs.
>
> ⚠ **Protection chain (found 2026-07-06):** `memory.min`/`memory.low` are *hierarchical* — a cgroup's effective protection is capped by every ancestor's value. The game scope lives under `system.slice` and the pak slice under `soulmask.slice`; without `MemoryMin` on those ancestors both floors are **ineffective** against global reclaim. Since 2026-07-07 `setup-cgroups.sh` asserts the ancestor floors (table above) via `systemctl set-property`. Do NOT set `system.slice` below the game floor — the parent value silently CAPS the child's effective protection.

**IO knobs** (BFQ scheduler required — without BFQ, `io.weight` / `io.bfq.weight` are no-ops):

| Knob | Range | Effect |
|---|---|---|
| `io.bfq.weight` | 1–1000 | BFQ proportional share (supersedes `io.weight` on BFQ) |
| `io.weight` | 1–10000 | Proportional share for other schedulers |
| `io.max` | `MAJ:MIN rbps=N wbps=N riops=N wiops=N` | Hard rate cap; `max` = unlimited |

### 5.0b Who owns which knob — slice units vs `set-property` vs raw writes

Slices and raw cgroup writes are not competing mechanisms: **systemd is the
single writer of the cgroup-v2 tree**, and any attribute it manages it will
re-write from its own records on every `systemctl daemon-reload` (which any
apt package that ships units triggers). Raw `echo`-writes into managed
attributes are therefore silently reverted — proven live 2026-07-07 when
`apt install systemd-oomd` wiped the game band an hour after the watcher had
applied and verified it (plan §1.5 Finding D). The ownership rule:

| Mechanism | Use for | Persistence |
|---|---|---|
| Slice/scope **unit file** (`[Slice]` properties) | Everything systemd has a property for, on *persistent* units: MemoryMin/Low/High/Max, MemoryZSwapMax (v253+), MemoryZSwapWriteback (v255+), CPUWeight, IOWeight, IO*Max, ManagedOOM* | survives reboot + reload |
| `systemctl set-property` (persistent) | Same properties on existing units without editing files (writes a drop-in under `/etc/systemd/system.control/`) — used for the ancestor floors (system.slice / soulmask.slice MemoryMin) | survives reboot + reload |
| `systemctl set-property --runtime` | Same properties on **transient docker scopes** (`docker-<id>.scope`) — the only reload-safe way to set knobs on a container systemd created for docker | survives reload; dies with the scope (fine — the watcher re-applies per container start) |
| Raw write to `/sys/fs/cgroup/...` | ONLY attributes systemd has no property for: `io.bfq.weight`, `memory.reclaim` (one-shot trigger). These are unmanaged, so reloads leave them alone | survives reload; dies with cgroup |

Why not raw-writes only ("one mechanism")? Nothing would survive a reboot or
even a package install, and you'd be fighting the tree's owner. Why not
slices only? systemd simply has no property for `io.bfq.weight`, and docker
scopes can't be given unit *files* (they're transient). The hybrid above is
the minimal consistent scheme; `setup-cgroups.sh` implements exactly this.

| Cgroup | `io.bfq.weight` | `io.max riops` | `io.max wiops` | `io.max rbps/wbps` |
|---|---|---|---|---|
| Soulmask | 1000 | max | max | max |
| bench containers | 1 | 100 | 400 | 30 MB/s |

**CPU knob:**

| Cgroup | `cpu.weight` | Effect |
|---|---|---|
| Soulmask | 800 | ~8× more CPU than a default-weight (100) process when contended |
| bench containers | 100 (default) | No advantage when contended |

**Cgroup counters to watch (from `memory.stat`):**

| Counter | What it measures |
|---|---|
| `workingset_refault_anon` | Pages evicted to zswap that were subsequently needed back ← primary pressure indicator |
| `pgmajfault` | Major page faults (disk reads or zswap decompresses) |
| `anon` | Anonymous pages in uncompressed RAM (game heap, stack) |
| `file` | File-backed pages in RAM (ordinary page cache) |
| `shmem` | Shared memory / tmpfs in RAM (pak ramdisk pages, in the correct cgroup) |
| `zswap` | Bytes in zswap pool (compressed) |
| `zswapped` | Cumulative bytes ever written to zswap |

### 5.1 Applying it (files)
- [`setup-cgroups.sh`](files/usr/local/sbin/setup-cgroups.sh) (run by [`gstammtisch-cgroups.service`](files/etc/systemd/system/gstammtisch-cgroups.service)) — applies the **game-side** knobs systemd can't: ancestor `MemoryMin` floors (protection chain), pak-slice zswap bypass, and locates the Soulmask container's scope to set `memory.min/low/high` + writeback. Re-runnable after the server restarts.
- `interactive.slice` / `besteffort.slice` and everything that sizes them (measured IO caps, the fio baseline, BFQ setup) — **not in this guide**: `modern-debian-tools-python-debug/host-setup/`, see [README §Scope](README.md#scope).

### 5.2 Pterodactyl / Wings integration (be realistic)
Wings creates the Soulmask container under Docker's default cgroup parent, and recreates it on updates — moving its cgroup is fragile. Use **defense in depth**:
1. **Pterodactyl panel** — set Soulmask's memory/CPU/IO limits there. Wings re-applies them on every (re)start. This is the *reliable* lever.
2. **dev containers** — launch into the dev tiers: `cgroup_parent: besteffort.slice` in compose (ciu governance injects it) for test/build stacks, `--cgroup-parent=interactive.slice` in devcontainer.json `runArgs` for the devcontainer itself. Placement is **create-time only** — a running container cannot be moved.
3. **`gstammtisch-cgroups.service`** re-asserts the raw Soulmask knobs; optionally trigger it from a Wings post-start hook or a `.timer` to survive container recreation.

### 5.3 systemd-oomd — the safety net
With this much oversubscription the in-kernel OOM killer is slow and may pick a poor victim. [`oomd.conf.d/gstammtisch.conf`](files/etc/systemd/oomd.conf.d/gstammtisch.conf) + `ManagedOOMMemoryPressure=kill` on `besteffort.slice` makes systemd-oomd kill the worst **best-effort** offender fast on PSI/swap pressure — never touching the protected game or the IDE tier. This is what makes `swappiness=100` + large swap safe.

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
5. Panel limits for Soulmask; mdt `host-setup/install.sh` for the dev tiers.
6. `swap-health watch` to confirm no red flags.

---

## 10. References
- Source guides: `GAMINGHOST-SWAP-1.md`, `GAMINGHOST-SWAP-2.md` (in the work folder).
- Kernel zswap: <https://docs.kernel.org/admin-guide/mm/zswap.html>
- Kernel cgroup v2: <https://docs.kernel.org/admin-guide/cgroup-v2.html>
- Chris Down, zswap vs zram (LRU inversion): referenced in SWAP-2.
- Linux 7.0 MM / swap table: <https://kernelnewbies.org/Linux_7.0>
- Debian 13 release: <https://www.debian.org/News/2025/20250809>
