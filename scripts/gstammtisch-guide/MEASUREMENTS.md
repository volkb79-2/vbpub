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
5. ksmd is already enabled at boot (`files/etc/tmpfiles.d/ksm.conf`:
   `run=1`, `advisor_mode=scan-time` self-tuning, `use_zero_pages=1` since
   2026-07-21 — all-zero anon pages map straight to the kernel's shared
   zero-page instead of the general merge path, freeing that RAM without
   waiting on zswap). ksmd is a **single kernel thread** — confirmed via
   `ps -eLo pid,tid,comm | grep ksmd` (pid == tid, no siblings) — so ~100%
   of *one* core is the hard ceiling, not configurable higher regardless of
   tuning.

   The self-tuning `scan-time` advisor is conservative by default (observed
   ~13.5K pages/sec, ksmd <4% of one core idle). To accelerate scanning
   temporarily for a measurement session:
   ```bash
   echo none      > /sys/kernel/mm/ksm/advisor_mode      # take manual control
   echo 4000      > /sys/kernel/mm/ksm/pages_to_scan       # up from the ~270-300 default
   echo 0         > /sys/kernel/mm/ksm/sleep_millisecs     # 20 -> 0, continuous scanning
   # ksmd climbs to ~99% of one core. Revert when done:
   echo scan-time > /sys/kernel/mm/ksm/advisor_mode
   echo 300       > /sys/kernel/mm/ksm/pages_to_scan
   echo 20        > /sys/kernel/mm/ksm/sleep_millisecs
   ```
   `advisor_max_cpu` / `advisor_target_scan_time` only take effect while
   `advisor_mode=scan-time` — writes to them are silently accepted but inert
   while in manual (`none`) mode. A reboot alone reverts any manual override
   back to `ksm.conf`'s defaults (nothing above persists on its own).

**Measure the benefit — prefer per-process** (kernel 7.0 exposes
`/proc/<pid>/ksm_stat`), since it's scoped to exactly the processes you care
about and immune to unrelated KSM activity elsewhere on the host (the global
counters below are host-wide, not per-server):
```bash
for c in <container-id-1> <container-id-2>; do
  echo "--- container $c ---"
  docker exec "$c" sh -c '
    for p in /proc/[0-9]*; do
      if grep -q WSServer "$p/cmdline" 2>/dev/null; then
        echo "pid $(basename "$p"):"
        cat "$p/ksm_stat" 2>/dev/null | sed "s/^/    /"
      fi
    done'
done
```
Key fields: `ksm_merging_pages` (this process's own pages currently
deduplicated — ×4K for MB saved); `ksm_process_profit` (net benefit
estimate in bytes, nets rmap bookkeeping overhead against savings — starts
negative early on, turns positive as ksmd works through the candidate
backlog, don't judge off an early reading); `ksm_rmap_items` (current
candidate-pool size, **not** a shrinking backlog — tracks the process's
mergeable memory footprint and fluctuates with normal alloc/free, doesn't
count down to zero). A PID showing `ksm_merge_any: no` / all-zero fields is
a forked child that didn't inherit the preload's opt-in — expected, only
the main process and some children pick it up.

Cross-check with the global counters, but don't rely on them alone if
anything else on the host might also be KSM-opted-in:
```bash
grep . /sys/kernel/mm/ksm/{pages_shared,pages_sharing,pages_unshared,pages_volatile,full_scans,stable_node_chains}
# RAM actually saved ≈ (pages_sharing − pages_shared) × 4K — host-wide, not per-process
```

**Measure the costs:** ksmd CPU (`pidstat -p $(pgrep ksmd) 10`, or the
instantaneous `/proc/<pid>/stat` utime/stime delta method if `pidstat` isn't
installed — `ps %CPU` is a lifetime average and won't reflect a recent
tuning change) and CoW-unshare stutter — a merged page the game WRITES
triggers a copy fault in the game thread. Watch game minor-fault rate and
frame consistency (`ServerFPS`) with KSM on vs off.

**Expectation:** modest. Worlds/actor graphs differ per map; the biggest
identical anon data is what both engines build from the same pak. Decision
rule: keep KSM if savings ≥ ~300 M with no ServerFPS/stutter regression;
otherwise revert (remove the LD_PRELOAD prefix; merged pages unshare on the
next write, or immediately via `echo 2 > /sys/kernel/mm/ksm/run`).

**Session log — 2026-07-21, full data series and what it taught us.** All
readings are the main `WSServer` process's `/proc/<pid>/ksm_stat` on
`soulmask2`/`b87c0a5b` and `soulmask2b`/`6c418fe7`. Sequence, not clock
time (some minutes apart each):

| # | Event | `soulmask2` rmap / merging (MB) / profit (MB) | `soulmask2b` rmap / merging (MB) / profit (MB) |
|---|---|---|---|
| 1 | Both freshly opted into KSM, same map (`DLC_Level01_Main`), default scan rate | 936927 / 35.8 / **-21.4** | 666856 / 39.1 / **-1.6** |
| 2 | Same map, still default scan rate, later | 1010305 / 110.0 / +48.3 | 385439 / 70.8 / +47.3 |
| 3 | Boost applied (`pages_to_scan=4000`, `sleep_millisecs` 20→10, ~69.6% CPU) | 909568 / 152.1 / +96.6 | 171382 / 96.8 / +86.3 |
| 4 | `sleep_millisecs`→5 (~75.8% CPU), →2 (~84.2%), →0 (~99.1%), `use_zero_pages` on | 953058→909568 range, converging | — |
| 5 | "Stabilized" reading (declared prematurely, see below) | 807991 / 169.2 / +120.1 | 294748 / 121.2 / +103.5 |
| 6 | `soulmask2b` switched to `Level01_Main` (base map), fresh container/PID; `soulmask2` untouched | 139115 / 87.9 / **+79.6** (dropped, not restarted) | 1595974 / **1319.6** / **+1287.0** (fresh boot, huge) |
| 7 | `soulmask2` restarted (fresh container/PID); `soulmask2b` untouched | 1337018 / **963.9** / **+928.9** (fresh boot, also huge) | 120090 / **169.7** / **+193.1** (collapsed, not restarted) |
| 8 | Later, neither restarted | 1074346 / 757.0 / +739.0 (still declining) | 146335 / 168.4 / +190.4 (flat) |
| 9 | Later still, neither restarted | 348513 / 470.6 / +500.1 (still declining) | 137274 / 167.3 / +190.1 (flat, this is the floor) |

**What this actually shows, in order of discovery:**

1. **Rows 1→2**: `ksm_process_profit` starts negative and turns positive as
   `ksmd` works through the initial rmap-bookkeeping backlog — don't judge
   off an early reading, this is expected, not a sign KSM isn't working.
2. **Rows 6→7, the important one**: restarting ONE instance visibly
   disturbs the OTHER's numbers even though it was never touched. When
   `soulmask2b` restarted (row 6), `soulmask2` — untouched — dropped
   169→87.9 MB. When `soulmask2` then restarted (row 7), `soulmask2b` —
   untouched — collapsed 1319.6→169.7 MB. **These two processes' KSM state
   is not independent.** Best explanation (not fully certain): `ksmd` is a
   single kernel thread with limited scan throughput even boosted; a fresh
   ~1M-item backlog from a newly-restarted process competes for that same
   attention, and previously-tracked-but-not-yet-*stable* candidates on the
   untouched side can drop out of tracking while the scan is dominated by
   the new arrival. **Practical implication: don't restart either instance
   mid-measurement-window** — it invalidates both readings, not just the
   restarted one.
3. **Rows 6-7's huge initial numbers (up to ~1.3 GB) are themselves
   inflated**, not a true steady state — a freshly-booted, mostly-empty
   world is disproportionately full of identical freshly-initialized/
   template memory (default actor structs, empty inventory slots, zeroed
   buffers) that hasn't yet been touched by real, divergent simulation.
   `use_zero_pages` (row 4) compounds this further by giving an instant,
   free win on any of that content that's literally all-zero.
4. **Rows 7→8→9: the decline is the real signal, and it's expected.**
   A live, continuously-simulating game server *writes* to its heap
   constantly — actor movement, procedural dungeon generation (seen in the
   boot log: "Create Dungeon Successed"), physics, inventory state — and
   ANY write to a KSM-merged page triggers copy-on-write, permanently
   breaking that specific merge. So the true trajectory isn't "ramp up to a
   plateau," it's **peak shortly after boot, then erode as gameplay
   diverges each server's actual content**, settling toward whatever
   subset is genuinely static and never written (this was the user's own
   hypothesis, confirmed by rows 8-9). `soulmask2b` (row 9, untouched since
   row 6) reached a flat floor (~167-169 MB, ~190 MB profit) faster because
   it had more elapsed post-restart time to diverge; `soulmask2` (freshly
   restarted at row 7) was still declining at row 9 and hadn't reached its
   own floor yet.

**Revised decision procedure, superseding the original "run ≥1h and read
once" framing above:** take repeated readings spaced several minutes apart
and wait for `ksm_merging_pages`/`ksm_process_profit` to visibly flatten
(consecutive reads within a few percent of each other) before applying the
≥300 MB keep/revert rule — an early or post-restart reading will
overstate real, sustained savings. Do not restart either instance while
waiting, for the reason in point 2. Real player activity (not just idle
world-ticking) during the wait is still preferable per the original
guidance, since it exercises the divergence this section describes rather
than waiting on it passively.

**Where this leaves the actual decision:** `soulmask2b`'s floor (~167 MB
merging, ~190 MB profit) is the more trustworthy number so far, since it's
the only one that's visibly flattened. That alone is already close to the
~300 MB keep/revert threshold when added to `soulmask2`'s own
(still-declining, so currently overstated) number — worth re-reading both
once `soulmask2` flattens too, rather than deciding now.

CPU-boost knobs used throughout: `advisor_mode=none`, `pages_to_scan=4000`,
`sleep_millisecs=0` (~99% of one core, confirmed single-threaded — see step
5 above for the full procedure and revert command), plus
`use_zero_pages=1` (now persisted in `files/etc/tmpfiles.d/ksm.conf`, so it
survives reboots; the scan-rate override does not and reverts to
`advisor_mode=scan-time` defaults on the next reboot).
