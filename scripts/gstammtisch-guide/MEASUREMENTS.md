# MEASUREMENTS — manual procedures for tuning the gstammtisch host

Step-by-step procedures behind the measurement plan in
`plan-host-resource-governance.md` §10. Each section states the question, the
exact commands, how to read the numbers, and which knob the result feeds.

Prereqs: `vmtouch`, `fio` (both via `install.sh`), damo toolkit at
`../damon-analysis/` (venv), the Python zswap monitor
(`soulmask-zswap-monitor.sh`), root.

---

## M0. Reading refaults correctly — zswap vs disk (why rflt/s == mflt/s)

Per-cgroup `memory.stat` counters:

| Counter | Counts |
|---|---|
| `workingset_refault_anon` | anon pages faulted back after reclaim — from zswap **and** disk combined |
| `pgmajfault` | ALL major faults — anon swap-ins (zswap+disk) **plus** file-backed reads |
| `zswpin` | pages decompressed **from zswap** only |
| `zswpout` | pages compressed into zswap |

So `rflt/s == mflt/s` is expected whenever the workload's major faults are all
anon swap-ins (a swap-in from zswap is still a major fault). The split you
actually care about:

```
zswap refaults/s  ≈ Δ zswpin                       (~3–5 µs each — harmless)
disk  refaults/s  ≈ Δ workingset_refault_anon − Δ zswpin   (≥ ms each — THIS causes lag)
file  majfaults/s ≈ Δ pgmajfault − Δ workingset_refault_anon  (rough; code/pak from disk)
```

The monitor's `rf_z/s` / `rf_d/s` columns compute this. Manual spot check:

```bash
CG=/sys/fs/cgroup/system.slice/docker-<gameid>.scope
grep -E '^(workingset_refault_anon|zswpin|pgmajfault) ' $CG/memory.stat; sleep 10
grep -E '^(workingset_refault_anon|zswpin|pgmajfault) ' $CG/memory.stat
# subtract, divide by 10
```

**SLO:** game `rf_d/s ≈ 0` during play; `rf_z/s` ≤ ~20/s sustained (area-load
spikes excepted).

---

## M2. Pak hot set — how big must the pak floor be?

Question: of the 1.67 G pak, how much is actually touched during play? Only
that needs the `MemoryMin` floor; the rest may live on disk
(`memory.zswap.max=0` on the pak slice sends cold pak straight to disk — pak
data is zstd-incompressible, 1.006×, so zswap would waste CPU+RAM on it).

`vmtouch -v` on a tmpfs file reports which pages are resident (swapped-out
tmpfs pages count as non-resident). Under memory pressure the resident set
converges on hot+warm:

```bash
# snapshot series across a play session (every 10 min):
while true; do
  date +%H:%M:%S
  vmtouch -v /mnt/soulmask-paks/WS-LinuxServer.pak | tail -4
  sleep 600
done | tee pak-residency.log
```

Interpretation:
- **Resident %** trending down over hours of pressure = the floor of the curve
  is hot+warm pak. (Observed 2026-07-07: 738M / 43.2% under moderate pressure.)
- The `[ooo  o ]` map shows *which* regions: dense head = index/frequently
  streamed assets; sparse tail = other-map / unused content. With two
  instances on different maps sharing one pak tmpfs, expect the union to be
  larger — measure again after instance 2 exists.
- Cross-check the *cost* of the current level: pak slice `rf_d/s` (monitor
  `p_rfd/s`) must stay ≈0 during play. If residency shrinks AND p_rfd/s rises,
  pressure has cut into warm pak → raise the floor.

Set the floor: `MemoryMin` in `soulmask-paks.slice` = observed stable
residency + ~20 % margin. Remember the chain: `soulmask.slice` MemoryMin
(setup-cgroups.sh `SOULMASK_SLICE_MIN`) must be ≥ the pak floor or it protects
nothing.

---

## M3. Game cold-tail size — what does writeback=1 buy?

```bash
CG=.../docker-<gameid>.scope
swap=$(cat $CG/memory.swap.current)
zeq=$(grep '^zswapped ' $CG/memory.stat | awk '{print $2}')
echo "on-disk cold tail: $(( (swap - zeq) / 1048576 ))M"
```

Track daily. The steady-state on-disk tail is RAM+zswap the host reclaimed for
free. If `rf_d/s` stays ≈0 while the tail grows, writeback=1 is pure win. If
`rf_d/s` recurs at fixed times (saves? logins?), those pages aren't really
cold — see M4.

---

## M4. Login-latency test — the GO/NO-GO gate for game writeback=1

Logins load the player's region: the one workload known to touch the cold
tail (the 5G/6G-band login failures). Test both tail states:

1. **Cold-tail login:** server idle ≥2 h (tail settled to disk — verify M3
   value is stable). Player logs in. During login, watch the monitor at 2 s
   interval: record wall-clock from "connect" to in-game, peak `rf_d/s`,
   and whether the first attempt succeeds.
2. **Warm login (control):** same player logs out and immediately back in.

PASS: first-attempt success, login time within ~2× of warm case, `rf_d/s`
spike decays within ~30 s. FAIL → `SOULMASK_WRITEBACK=0` in setup-cgroups.sh
(env override, no code change) and re-run.

The 2026-07-07 00:39 observation (writeback=1, band 6G/8G): login caused a
~20 s refault burst of 124→964→684→227/s that included disk swap-ins
(disk_sw grew ~38M) — first attempt succeeded. That's a PASS baseline; repeat
whenever the band or writeback policy changes.

---

## M5. File-cache hotness — is 2–3 G of buff/cache justified? Is swappiness=100 right?

buff/cache decomposition first:

```bash
grep -E '^(Buffers|Cached|Shmem|SwapCached)' /proc/meminfo
# real file cache ≈ Cached − Shmem   (the pak tmpfs is inside Cached!)
```

Who's in the file cache — per-file residency of the usual suspects:

```bash
vmtouch /var/lib/pterodactyl/volumes/<id>/WS/Binaries/Linux/WSServer-Linux-Shipping
vmtouch -f /var/lib/docker/overlay2 2>/dev/null | tail -3   # docker layers (slow, run once)
vmtouch /usr/bin/dockerd /usr/bin/containerd
```

Is the cache *hot*? The kernel's own verdict is the refault counter — a page
cache that's too small shows **file refaults**:

```bash
# global:
grep -E 'workingset_refault_file|pgmajfault' /proc/vmstat; sleep 60
grep -E 'workingset_refault_file|pgmajfault' /proc/vmstat
# per cgroup: grep '^workingset_refault_file ' <cg>/memory.stat
```

**Swappiness validation recipe** (this is the empirical answer to "is 100
right for our workload"): watch both refault streams for a representative day —

| Observation | Meaning | Action |
|---|---|---|
| file refaults ≈ 0, anon rf_z modest, rf_d ≈ 0 | cache is big enough AND anon reclaim is absorbed by zswap | swappiness=100 is right; nothing to change |
| file refaults sustained ≫ 0 (esp. game/wings cgroups) | kernel is dropping needed file pages (code!) | swappiness even higher is impossible-ish (max 200) → protect cache by capping anon hogs instead |
| anon rf_d sustained ≫ 0 on the game | cold tail not really cold / floor too low | fix floors/writeback (M4), NOT swappiness |

The asymmetry to remember: with zswap, an anon refault costs µs; a file
refault always costs a disk read. swappiness=100 deliberately trades the
cheap direction. Lowering it to 5–10 would evict executable pages (the game
binary itself) to "protect" anon that zswap already handles — the wrong trade
on this host. There is no per-cgroup swappiness in cgroup v2.

DAMON option for deeper file-cache analysis: `damo` in **paddr** (physical)
mode samples ALL memory including page cache — use to build a hot/warm/cold
histogram of the whole host when deciding zswap `max_pool_percent` (target:
pool ≈ compressed size of the *warm* anon set; currently 40 %).

---

## M6. Disk IOPS ceiling + bench cap

The benchmark and the caps it feeds live in the mdt host-setup companion
(`modern-debian-tools-python-debug/host-setup/`), not in this guide — dev/test
IO governance is not a game-side concern. The procedure and the reference
numbers below still apply to this host.

```bash
mdt-io-baseline.py        # caches RIOPS_MAX, WIOPS_MAX, RBW_MAX_BPS, WBW_MAX_BPS
                          # sustained-v3: 4 fio passes × (10s ramp + 40s measure),
                          # 4G span, incompressible buffers — ~4 min saturation
systemctl start mdt-host-slices.service  # derives the caps: 60% of measured for
                                         # the besteffort tier, 80% per container
```

Reference (sustained-v3, 2026-07-08, game running): riops 90,173 / wiops 58,695 /
rbw 2,149 MB/s / wbw 516 MB/s; p99 4k-read 497µs, 4k-write 1,695µs, 128k-write
7.1ms. The earlier burst numbers (rbw 4.3 GB/s, wbw 1.48 GB/s) were
hypervisor-cache artifacts — ramp_time + 4G span + `buffer_compress_percentage=0`
killed them. Notable: game `io.pressure full avg300` stayed at 0.05 % through the
whole 4-minute saturation run — the BFQ/io.weight arbitration alone nearly fully
shielded the game even WITHOUT caps on the aggressor (the benchmark ran uncapped
in system.slice).

Validation that the cap protects the game: run a real build
(`release-bake.sh` / docker-repack) and watch the game's `io.pressure`
(`full avg10` < 2) and monitor `rf_d/s` ≈ 0 while `iostat -x 5` shows the
build pinned at the cap.

Why no system-wide IOPS cap: `io.max` cannot be set on the cgroup root, and
capping every slice would throttle the game's own DB backups too. The design
is: **cap the aggressors** (bench/buildkit/besteffort via io.max) + **BFQ
weight priority** for the game (io.bfq.weight 1000 vs 1). `io.cost.qos`
(latency-target QoS on the whole device) is the real "system-wide fairness"
mechanism if ever needed — complex to tune, revisit only if weights+caps
prove insufficient.

---

## M7. KSM across two Soulmask instances — setup + benefit measurement

**What KSM can and cannot merge** (this decides where the savings can come from):

| Page type | KSM mergeable? | Two-instance situation | If not KSM — what instead |
|---|---|---|---|
| Private anonymous (heap/malloc, stacks, anon mmap — the UE actor graph, decompressed asset structures, engine globals) | **YES** — for opted-in processes only | Biggest candidate: identical engine/static data both instances decompress from the same pak | — |
| File-backed page cache (game binary `.text`, `.so` libs, mmap'd files) | NO | ⚠ each Pterodactyl volume has its OWN copy of the install → **different inodes → the page cache holds duplicates too** | hardlink-dedupe the two installs (`jdupes -L volA volB`; re-run after each steam update), or a shared RO install (unverified — see SOULMASK.md §9) |
| shmem / tmpfs (the pak ramdisk) | NO | — | already solved better: ONE shared pak tmpfs bind-mounted into both instances = true sharing, zero scan cost |
| mlocked / hugetlbfs pages | NO | none here | — |

**Capability check — resolved 2026-07-07 on this host (kernel 7.0):**
`prctl(PR_SET_MEMORY_MERGE, 1)` succeeds **unprivileged inside a default
docker container** (tested: `docker run --rm debian:stable-slim perl -e
'…syscall(157,67,1,0,0,0)…'` → `r=0`, with and without `--cap-add
SYS_RESOURCE`). Older kernels required CAP_SYS_RESOURCE (not grantable under
wings); on this kernel no wings/egg capability change is needed.

**Concrete setup steps:**

1. Build the shim (source: `game_stuff/soulmask/ksm-optin.c` — an
   `__attribute__((constructor))` that calls `prctl(PR_SET_MEMORY_MERGE,1)`;
   a preload is required because the flag does NOT survive execve, so a
   wrapper-then-exec would lose it, while a preload runs inside the game
   process after exec):
   ```bash
   gcc -shared -fPIC -O2 -o ksm-optin.so game_stuff/soulmask/ksm-optin.c
   ```
2. Copy it into each instance volume and chown to the container user:
   ```bash
   install -o 988 -g 988 ksm-optin.so /var/lib/pterodactyl/volumes/<uuid>/ksm-optin.so
   ```
3. Switch the server(s) to the KSM egg variant
   `game_stuff/soulmask/egg-soulmask-rcon-ksm.json` (identical to the RCON
   egg, startup prefixed with `LD_PRELOAD=/home/container/ksm-optin.so`) —
   or just prefix the per-server Startup Command in the panel. Missing .so =
   harmless ld.so warning, server runs without KSM.
4. Confirm opt-in in the server console log: `[ksm-optin] KSM enabled…`.
5. ksmd is already enabled at boot (`files/etc/tmpfiles.d/ksm.conf`,
   `/sys/kernel/mm/ksm/run=1`). Scan-rate knobs if the default is too lazy:
   `pages_to_scan` (default 100/wake) and `sleep_millisecs` (default 20).

**Measure the benefit (run ≥1 h with both instances up and players seen on both):**
```bash
grep . /sys/kernel/mm/ksm/{pages_shared,pages_sharing,pages_unshared,pages_volatile,full_scans,stable_node_chains}
# RAM actually saved ≈ (pages_sharing − pages_shared) × 4K
```
**Measure the costs:** ksmd CPU (`pidstat -p $(pgrep ksmd) 10`) and CoW-unshare
stutter — a merged page the game WRITES triggers a copy fault in the game
thread. Watch game minor-fault rate and frame consistency (`ServerFPS`)
with KSM on vs off.

**Expectation:** modest. Worlds/actor graphs differ per map; the biggest
identical anon data is what both engines build from the same pak. Decision
rule: keep KSM if savings ≥ ~300 M with no ServerFPS/stutter regression;
otherwise revert (remove the LD_PRELOAD prefix; merged pages unshare on the
next write, or immediately via `echo 2 > /sys/kernel/mm/ksm/run`).
