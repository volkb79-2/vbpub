# Plan — Host Resource Governance for `gstammtisch`

Status: PLAN ONLY (nothing applied). Authored 2026-07-01 against live host state.
Scope: tiered cgroup-v2 resource governance (CPU / memory / IO) for a mixed-use
16 GB host running one live game server (PROD), one interactive devcontainer, and
~18 best-effort test containers. Builds on the already-staged config under
`gstammtisch-guide/files/` — this doc records what is *actually applied*, what is
staged-but-inert, and the concrete deltas to close the gap.

Companion docs (read for the deep mechanism reasoning, do not duplicate here):
`MEMORY-ARCHITECTURE.md` (zswap/sysctl/cgroup reasoning), `CGROUP-MONITORING.md`
(every metric explained on live data), `SOULMASK.md` (game specifics + pak §2c).

---

## 0. TL;DR

The host is **memory-oversubscribed**, not mis-tuned. Total anon demand (~40 GB) on
16 GB RAM → ~53 GB in swap, MemAvailable ~1.6–1.9 GB, load avg ~74 on 8 cores. The
one PROD workload (Soulmask game, container `b87c0a5b…`) is **already protected**
(memory.min=7 G, cpu.weight=800, io.weight=4950, zswap.writeback=0 — applied post-RCON
by `soulmask-cgroup-watcher`). **The INTERACTIVE and BEST-EFFORT tiers have NO limits
at all** — every dstdns container, the devcontainer, the buildkit builder and the
leaking authentik-worker run with `memory.max=max`, so a single leak swap-poisons the
whole host. Fixing that (hard `memory.max` per best-effort container + a bounded
`besteffort.slice`) is the immediate safety win. The pak ramdisk exists but is
under-protected (charged outside the game cgroup, `MemoryMin=150M` ≪ 1.7 GB pak,
writeback=yes) so the pak can still be swapped to disk.

---

## 1. Verified current state (live, 2026-07-01)

### 1.1 Host
| Fact | Value | Source |
|---|---|---|
| RAM | 16375256 kB (~15.6 GiB) | `/proc/meminfo` MemTotal |
| MemAvailable / MemFree | ~1.9 GB / 263 MB | `/proc/meminfo` |
| Swap total / used | 69 GB (2× vda6+vda7 @ prio 10) / **~53 GB used** | `/proc/swaps` (SwapFree 15 GB) |
| Load avg (8 cores) | **74 / 72 / 69** | `/proc/loadavg` |
| zswap | enabled, `zstd`, max_pool_percent=**50**, shrinker=Y, accept_threshold=90 | `/sys/module/zswap/parameters/*` |
| **vm.swappiness** | **5** (live) — **not persisted** (nothing in `/etc/sysctl.d`) | `/proc/sys/vm/swappiness` |
| cgroup | v2 unified, systemd driver, `cgroupns=private` | `docker info` |
| IO scheduler on vda | **`[bfq]`** active (good — required for io.weight), rotational=1 (thin virtio) | `/sys/block/vda/queue/scheduler` |
| Docker daemon.json | **absent** | `/etc/docker/daemon.json` |

Note the contradiction: the staged `99-gstammtisch-memory.conf` sets `swappiness=100`
(deliberate: zswap makes anon reclaim cheap, protect the game with `memory.min` not
swappiness) but the live value is **5** and is not persisted anywhere. Raising
swappiness alone will NOT fix the thrash — the fix is capping oversubscription. Persist
the intended value once the caps below are in place. **Do NOT raise zswap
`max_pool_percent`** — a bigger pool steals RAM from the working set and worsens refault.

### 1.2 Per-container (verified via `docker inspect` + `docker exec … cat /sys/fs/cgroup/*`)
| Container | Tier | Docker limits | Live cgroup knobs | RAM / swap |
|---|---|---|---|---|
| `b87c0a5b…` = **Soulmask** (`WSServer-Linux-Shipping`, "DCHIVE Stammtisch DLC") | PROD | none (`Mem=0`, panel `SERVER_MEMORY=0`), BlkioWeight=500 | **min=7000M, low=12G, high=8000M, max=max, cpu.weight=800, io.weight=4950, zswap.writeback=0** (applied by watcher) | 3.0 G / 9.8 G |
| `dstdns-devcontainer-vb` | INTERACTIVE | **none** | **min=0, high=max, max=max, cpu.weight=100, io.max=∅, zswap.writeback=1** | 1.7 G / 3.0 G |
| `dstdns-98535c-authentik-worker` | BEST-EFFORT (leak) | **none** | **min=0, max=max** (nothing stops the leak) | 7.4 G / ~19 G |
| `dstdns-98535c-postgres` and ~17 other dstdns-* | BEST-EFFORT | **none** | **min=0, high=max, max=max, cpu.weight=100, io.max=∅** | <1 G each |
| `buildx_buildkit_keen_mestorf0` (docker builds) | BEST-EFFORT | **none** | **no limits, not targeted by setup-cgroups.sh** | — |

**Every non-prod cgroup has `CgroupParent=""` and `Memory=0`.** The staged
`dev-workloads.slice` / `besteffort` design is **inert**: no container is launched with
`--cgroup-parent`, so the slice files exist but govern nothing.

### 1.3 What IS applied vs staged-but-inert
| Item | State |
|---|---|
| zswap zstd + params | ✅ applied (matches `zswap-config.service` intent) |
| BFQ scheduler on vda | ✅ applied |
| Soulmask protection knobs (min/low/high/cpu/io/zswap.writeback) | ✅ applied by `soulmask-cgroup-watcher` after RCON |
| Pak ramdisk (3 G tmpfs over Paks dir) | ✅ mounted — but under-protected (see §3) |
| `dev-workloads.slice` (MemoryHigh 8G / Max 14G / oomd) | ❌ inert (no `--cgroup-parent` user) |
| Best-effort caps on dstdns-* | ❌ none exist |
| Bench io.max caps (devcontainer/test-runner) | ⚠️ only (re)applied when Soulmask restarts → **currently gone** on the devcontainer (`io.max=∅`); **buildkit never targeted** |
| `vm.swappiness=100` sysctl | ❌ not applied (live=5) |
| systemd-oomd per-slice kill | ❌ inert (slices unused) |

### 1.4 Soulmask decomposition (why prod is actually fine)
`memory.current=3.0 G` = 600 M hot anon + ~2.4 G of the game's **own** zswap pool;
`zswapped=9.6 G` uncompressed → `zswap=2.4 G` compressed = **3.8× compression**.
`shmem=4 KB` in the game cgroup ⇒ **the 1.7 GB pak tmpfs is NOT charged here** (cp ran
as root), so the game's `memory.min=7G` does not protect the pak. Game PSI:
`memory.pressure full avg10≈3.1`, `io.pressure full avg10≈3.7` — moderate, not
catastrophic. Prod is protected; the pak and the unbounded best-effort tier are the
gaps.

---

## 1.5 State update (2026-07-06) — live recalibration + new findings

Baseline re-verified on a quiet host (only Soulmask + wings + devcontainer + idle
buildkit running; no test stack). Supersedes parts of §1.

**Changes since 2026-07-01:**
| Item | Was (plan §1) | Now |
|---|---|---|
| Wings | — | **v1.13.1**; its new `machine_id` feature required adding `- "/run/wings:/run/wings"` to `ptero-wings/docker-compose.yml` (start failed with "bind source path does not exist" until added) |
| Soulmask band | min=7G, high=8000M | **min=6G, low=12G, high=8G** (live). 5G/6G band caused **player-login failures** (login loads the player region → transient demand above the 6G ceiling → throttle + refault storm; retries eventually warmed the pages). Repo `setup-cgroups.sh` + `soulmask-startup-cgroup.sh` updated to 6G/8G |
| `vm.swappiness` | 5 (unpersisted) | **100** live — matches `99-gstammtisch-memory.conf` (plan §1.1 row is stale) |
| zswap `max_pool_percent` | 50 | **30** live; shrinker=Y; debugfs: stored 6.7G uncompressed in 3.35G pool (≈2.0× system-wide), written_back_pages=626 |
| Monitor | — | repo `soulmask-zswap-monitor.sh` deployed to host (identical) |
| Snapshot | load 74, swap 53G | swap 11.3G used / 69G; MemFree 3.0G; buff/cache 3.1G (of which ~0.9G is resident pak shmem); game RAM 6.4G, z_pool 1.67G, z_eq 5.2G (**3.1×**), rflt 0–2/s |

**⚠ Finding A — memory.min protection chain is broken (affects §1.3 "✅ applied").**
cgroup-v2 `memory.min`/`low` are hierarchical: effective protection is capped by
*every ancestor's* value. Verified live: `system.slice/memory.min=0` (game scope's
parent) and `soulmask.slice/memory.min=0` (pak slice's parent). Therefore the game's
`memory.min=6G` and the pak's `MemoryMin=150M` protect **nothing against global
reclaim** — the observed stability comes from `memory.high` demand-shaping, not the
floor. (`memory_recursiveprot` is mounted but only propagates a *parent's* protection
down; it cannot fix children under zero-min parents.) Fix candidates:
- `systemctl set-property system.slice MemoryMin=7G` — makes the game floor
  effective; side effect: also shields sshd/dockerd/wings (arguably good — helps the
  can't-SSH-in-under-pressure problem). Weakens tiering only until non-prod
  containers move out of `system.slice` into their own slices (§3).
- `systemctl set-property soulmask.slice MemoryMin=<pak floor>` — required for any
  pak-slice floor to be effective.
- Long-term: place the game under `soulmask.slice` (needs Wings cgroup-parent
  support — under research) so one slice owns the whole prod budget.

**Finding B — the pak is zswap-incompressible; §4's recommendation reverses.**
Live: pak slice `zswapped=850M` vs `memory.zswap.current=844M` → **1.006×**. Pak
data is already compressed; zstd gains nothing. Squeezing pak pages into zswap burns
CPU and frees no RAM (1.79G pak currently costs 941M resident + 844M pool ≈ 1.79G).
The game's anon compresses 3.1× — zswap is the right tier for the game and the
**wrong tier for the pak**. Consequence: set `memory.zswap.max=0` on
`soulmask-paks.slice` (cold pak bypasses zswap, goes straight to disk swap) and size
`MemoryMin` to the *measured hot pak set* — not the §4 "pin all 2G" approach.

**Finding C — ~3.9G of game swap sits on real disk today despite writeback=0.**
Game `memory.swap.current=9.1G` vs `zswapped=5.2G`; the ~3.9G difference matches
system disk-swap. These pages were swapped before the watcher applied `writeback=0`
(post-RCON) — and with rflt 0–2/s at idle they are genuinely cold and cost nothing.
Evidence in favor of allowing cold→disk for the game too (§9), but note the login
failures show *logins touch part of the cold tail* — any writeback change must be
gated on a login-latency test.

**Prerequisite gap:** `systemd-oomd` is **not installed** on the host (Debian trixie
ships it as a separate package). All `ManagedOOM*=` lines are silent no-ops until
`apt install systemd-oomd`. (Installed 2026-07-07 — which promptly exposed Finding D.)

**⚠ Finding D (2026-07-07) — `systemctl daemon-reload` silently WIPES raw-written
scope knobs.** Docker containers are transient systemd scopes; on every
daemon-reload (triggered by ANY apt package that ships units) systemd re-applies
its recorded properties to each scope's cgroup — resetting attributes we wrote
directly (`echo … > memory.min` etc.) back to docker defaults. Proven timeline:
watcher applied+verified the band on the game scope at 23:16:28; `apt install
systemd-oomd` ran a daemon-reload at ~00:12; at 01:00 the scope was back to
min=0/low=0/high=max/writeback=1/cpu.weight=100. This also retroactively explains
§1.3's "devcontainer io.max currently gone" mystery. **Fix (implemented in
setup-cgroups.sh):** apply knobs via `systemctl set-property --runtime
docker-<id>.scope MemoryMin= MemoryLow= MemoryHigh= MemoryZSwapWriteback=
CPUWeight= IOWeight= IO*Max=` — systemd then owns the values and a reload
RE-applies them. Only `io.bfq.weight` keeps a raw write (no systemd property —
and systemd doesn't manage that attribute, so it survives reloads). Slice-unit
values (paks MemoryZSwapMax=0 etc.) were never affected: persistent units
re-apply their own files.

---

## 2. Root cause & policy

**Root cause:** memory oversubscription. Sum of anon working sets (authentik-worker
leak ~19 G swap + game ~10 G + game-container `b87c0a5b` ~9 G swap + devcontainer ~3 G)
far exceeds 16 GB RAM, so the kernel thrashes swap and evicts file cache (including the
pak). This is a *capacity/limit* problem, solved by **hard caps + protection floors per
tier**, not by swap/zswap tuning.

**Intended priority policy (3 tiers):**
1. **PROD (Soulmask)** — hard guarantees on CPU, memory, IO latency. Owns a protected
   `memory.min` floor and the resident pak. Highest cpu.weight + io.weight.
2. **INTERACTIVE (devcontainer + AI agents)** — assured responsiveness below prod;
   generous but bounded memory; medium cpu/io weight; **no harsh io cap** (it is also
   the IDE).
3. **BEST-EFFORT (all dstdns-* test stack, builds)** — soak idle capacity, yield
   instantly. **Hard `memory.max` so a leak can never poison the host.** Low cpu/io
   weight, `io.max` on the noisy ones (builds, test-runner), OOM-first.

---

## 3. Deliverable 1 — Tiered cgroup-v2 control design

### 3.1 Control matrix (target values)
| Control | PROD `soulmask.slice` | INTERACTIVE `interactive.slice` | BEST-EFFORT `besteffort.slice` |
|---|---|---|---|
| `cpu.weight` (`CPUWeight`) | **800** | **200** | **20** (proportional; 7 cores idle so this only bites under contention) |
| `cpu.max` | `max` (no hard cap — proportional only) | `max` | optional soft: leave `max`, rely on weight |
| `memory.min` (`MemoryMin`) | **5 G** game floor (+ pak, see §4) | 0 (responsiveness via cpu/io weight, not a floor) | **0** |
| `memory.low` (`MemoryLow`) | 12 G (best-effort soft protection) | 2 G | 0 |
| `memory.high` (`MemoryHigh`) | 6 G (production band ceiling) | **5 G** (soft throttle → spill to zswap) | **4 G** (whole tier) |
| `memory.max` (`MemoryMax`) | `max` (min+high band already governs it) | **7 G** (don't OOM the IDE; headroom above high) | **6 G** (whole tier — hard host-safety cap) |
| `memory.swap.max` | keep `max`; `zswap.writeback=0` (never to disk) | `max` | `max` (spill freely) |
| `memory.zswap.writeback` | **0** (game never spills to disk swap) | 0 | **1** (cold best-effort → disk, frees zswap pool) |
| `io.weight` (BFQ) | **4950** (`io.bfq.weight` 1000) | 100 (default) | **10** (`io.bfq.weight` 1) |
| `io.max` (hard IOPS/bps) | none | none | **builders/test-runner: `rbps=31M wbps=31M riops=100 wiops=400`** |
| systemd-oomd | off | off | **`ManagedOOMMemoryPressure=kill` @60% / `ManagedOOMSwap=kill`** |

Rationale for weights vs caps: with ~7 idle cores, CPU is proportional (`cpu.weight`),
not hard-capped — soulmask simply wins any contention 4:1 over interactive and 40:1
over best-effort. Memory is the scarce resource, so it gets both a **floor** (min, prod
only) and **hard ceilings** (max, every non-prod tier). IO latency for prod is
protected by BFQ weight *plus* hard `io.max` on the best-effort noisemakers.

### 3.2 Where to set each — three placement mechanisms

The host has three launch paths; each needs its own wiring. **Prefer declarative
placement (compose `cgroup_parent` / devcontainer runArgs / Wings panel) over the
post-hoc watcher** — declarative survives restarts; the watcher only re-fires on
Soulmask restart (which is why the devcontainer's io.max is currently empty).

**(a) BEST-EFFORT — docker-compose stack (dstdns-*).** Add to a compose override
(`docker-compose.governance.yml`) applied to the whole stack:
```yaml
# docker-compose.governance.yml — merged with `docker compose -f base -f governance up`
services:
  # apply to EVERY dstdns service; example for three representative ones:
  postgres:      { cgroup_parent: besteffort.slice, mem_limit: 2g,   mem_reservation: 512m }
  authentik-worker: { cgroup_parent: besteffort.slice, mem_limit: 1500m, mem_reservation: 256m }  # leak containment
  skywalking-oap: { cgroup_parent: besteffort.slice, mem_limit: 2g,   mem_reservation: 512m }
  # default for the rest:
  # <svc>: { cgroup_parent: besteffort.slice, mem_limit: 1g, mem_reservation: 256m }
```
`mem_limit` → container `memory.max` (leak containment per service); the
`besteffort.slice` (below) adds the tier-wide `MemoryMax=6G` host-safety cap and OOM
policy. `cgroup_parent` places every dstdns container under the slice so the slice caps
and cpu/io weights actually apply.

**(b) INTERACTIVE — devcontainer.** Add to `.devcontainer/devcontainer.json`:
```json
"runArgs": ["--cgroup-parent=interactive.slice",
            "--memory=7g", "--memory-reservation=2g", "--cpu-shares=200"]
```
(`--cpu-shares=200` → `cpu.weight` ≈200 under systemd driver; the slice sets the rest.)
Do **not** set a low `memory.max` on the devcontainer — it is also VSCode; capping too
low kills the IDE. `memory.high=5G` (soft, on the slice) throttles to zswap instead.

**(c) PROD — Pterodactyl/Wings.** Wings creates the game container with `CgroupParent=""`
(default) and cannot express `memory.min` (a *protection floor*, not a limit — the panel
only sets `memory.max`). Therefore **keep the RCON-gated `soulmask-cgroup-watcher`** for
the protection knobs (`memory.min/low/high`, `zswap.writeback=0`, `io/cpu.weight`) — it
is the only mechanism that can set a floor, and it already works. Two hardening deltas:
  - Set a **panel memory limit** (e.g. SERVER_MEMORY≈8000) as a backstop `memory.max`, so
    even if the watcher hasn't fired yet the game can't consume unbounded RAM.
  - Make the watcher also **write the pak-slice protection** (§4) so pak protection is
    reapplied on every Soulmask restart, not just at boot.

### 3.3 The three slice units (systemd `/etc/systemd/system/*.slice`)

`besteffort.slice` (**new** — the missing tier; supersedes/renames the inert
`dev-workloads.slice` role):
```ini
[Unit]
Description=Best-effort test stack — bounded, OOM-first, yields to prod+interactive
Before=slices.target
[Slice]
MemoryHigh=4G
MemoryMax=6G                 # hard host-safety cap: a leak here can never poison the host
CPUWeight=20
IOWeight=10
# hard IO cap for the whole tier's disk noise (device from `findmnt -no MAJ:MIN --target /var/lib/docker`)
IOReadBandwidthMax=/dev/vda 31M
IOWriteBandwidthMax=/dev/vda 31M
IOReadIOPSMax=/dev/vda 100
IOWriteIOPSMax=/dev/vda 400
ManagedOOMMemoryPressure=kill
ManagedOOMMemoryPressureLimit=60%
ManagedOOMSwap=kill
# memory.zswap.writeback=1 is the default (cold best-effort → disk, frees the pool).
```
`interactive.slice` (**new**):
```ini
[Unit]
Description=Interactive devcontainer + AI agents — responsive, below prod
[Slice]
MemoryHigh=5G
MemoryMax=7G
MemoryLow=2G
CPUWeight=200
IOWeight=100
# memory.zswap.writeback=0 applied by setup-cgroups.sh (systemd has no property for it)
```
`soulmask.slice` (**already conceptually in use** via the watcher; formalize as the
game's parent + keep `soulmask-paks.slice` nested — see §4):
```ini
[Slice]
MemoryMin=5G
MemoryLow=12G
MemoryHigh=6G
CPUWeight=800
IOWeight=4950
# zswap.writeback=0 + io.bfq.weight=1000 applied by setup-cgroups.sh
```
> Reality check the monitor assumes `PAK_CG=/sys/fs/cgroup/soulmask.slice/soulmask-paks.slice`,
> but Wings launches the game with `CgroupParent=""`. Either (i) configure Wings to launch
> under `soulmask.slice` if your Wings build supports a cgroup-parent override, or (ii)
> keep the watcher writing knobs to the *actual* Wings-created cgroup (current, working)
> and treat `soulmask.slice` as the label for the design, updating the monitor's `PAK_CG`
> discovery to resolve the pak slice dynamically. Verify the live pak charge location
> before trusting the paks-slice `MemoryMin` (see §4 verification).

---

## 4. Deliverable 2 — Pak-cache protection

**Verified current mechanism:** a **3 GB tmpfs** is mounted at
`/home/container/WS/Content/Paks` (host: `/var/lib/pterodactyl/volumes/b87c0a5b…/WS/Content/Paks`)
holding the **1.79 GB** `WS-LinuxServer.pak` (uses 1.7 G / 56%). So the "ramdisk"
approach the user tried **is live**. The rationale (staged docs) is sound: a clean
file-cached pak is *silently dropped* under pressure → 10 ms disk re-read → game stutter;
as tmpfs shmem it must instead go through **zswap** (~3 µs decompress) and cannot be
silently freed.

**But it is under-protected — two defects:**
1. The pak tmpfs is charged **outside the game cgroup** (game `shmem=4 KB`; cp ran as
   root). The game's `memory.min=7G` therefore does **not** cover the pak.
2. The staged `soulmask-paks.slice` sets `MemoryMin=150M` (≪ 1.7 GB pak) and
   `MemoryZSwapWriteback=yes`. Under the host's extreme pressure the pak's cold shmem can
   be pushed **to disk swap** — a *random-access* fault-in, arguably **worse** than a
   clean sequential file re-read the ramdisk was meant to prevent.

**Recommendation (robust, keeps the already-wired tmpfs): fix the pak slice to actually
pin the pak.** In `soulmask-paks.slice`:
```ini
[Slice]
MemoryMin=2G                 # ≥ pak size (1.79G) + headroom → shmem never reclaimed, stays uncompressed in RAM
MemoryZSwapWriteback=no      # pak may never be written to disk swap
```
and ensure the pak tmpfs is **charged to this slice** — the pak service must run
`Slice=soulmask-paks.slice` (it declares this) *and the `cp` that populates the tmpfs must
run inside that slice* (verify: the pages must land in the slice, not root). With
`MemoryMin=2G` the pak shmem is exempt from reclaim entirely: **guaranteed resident,
uncompressed, unevictable.**

**Tradeoff (state it explicitly for the operator):**
- **tmpfs + `MemoryMin=2G` (recommended):** guaranteed-resident, identical container
  paths (already wired), survives Wings restarts if the service is boot-ordered
  `Before=docker.service`. **Cost: 2 GB of RAM permanently spent** — acceptable because
  PROD gets hard guarantees, and 2 GB protected + 5 GB game floor = 7 GB of a 16 GB host
  still leaves ~9 GB for zswap pool + interactive + best-effort.
- **`vmtouch -dl <pak>` (alternative):** lock the *real* on-disk pak into page cache via
  `mlock`. Cheaper conceptually (no tmpfs copy, no bind-mount), truly unevictable while
  locked. **Cost:** still 1.7 GB RAM pinned; needs `vmtouch` installed + `memlock` rlimit
  raised for the locking process; must re-run after a pak update; and it locks *page
  cache*, so it competes differently under `MemoryMax`. Prefer only if you want to drop
  the tmpfs/bind-mount complexity.
- **Do NOT** rely on the game's `memory.min` to cover the pak — the pak is not charged to
  the game cgroup (verified).

**Verification before trusting either:** confirm where the pak is charged —
`for c in $(cat /sys/fs/cgroup/soulmask.slice/soulmask-paks.slice/memory.current); do …`
i.e. check `soulmask-paks.slice/memory.current ≈ 1.8 G` (pak is in the slice) vs the root
cgroup holding it. If it's in root, the slice `MemoryMin` protects nothing — re-run the
pak setup so the copy is charged to the slice.

---

## 5. Deliverable 3 — Per-container memory-limit table

Principle: **every container gets a hard `memory.max`** (container-level, via compose
`mem_limit` / devcontainer `--memory` / Wings panel) so no single leak can poison the
host; the **tier slice `MemoryMax`** is the aggregate backstop. Sum of *protected floors*
(`memory.min`) stays well under RAM; ceilings intentionally oversubscribe (cold pages go
to zswap/swap).

| Container | Tier | `memory.high` | `memory.max` (hard) | `memory.min` |
|---|---|---|---|---|
| Soulmask `b87c0a5b…` | PROD | 6 G | 8 G (panel backstop) | **5 G** (watcher) |
| pak tmpfs (`soulmask-paks.slice`) | PROD | — | — | **2 G** |
| `dstdns-devcontainer-vb` | INTERACTIVE | 5 G (slice) | 7 G | 0 |
| `authentik-worker` | BEST-EFFORT | — | **1.5 G** (leak containment; assumes worker-recycle fix) | 0 |
| `authentik-server` | BEST-EFFORT | — | 1 G | 0 |
| `postgres` (timescale) | BEST-EFFORT | — | 2 G | 0 |
| `skywalking-oap` | BEST-EFFORT | — | 2 G | 0 |
| `skywalking-banyandb` / `-ui` | BEST-EFFORT | — | 1 G / 512 M | 0 |
| `otel-collector-node` / `-aggregator` | BEST-EFFORT | — | 512 M each | 0 |
| `test-runner` | BEST-EFFORT | — | 4 G (+ io.max) | 0 |
| `buildx_buildkit_*` (builds) | BEST-EFFORT | — | 4 G (+ io.max) | 0 |
| controller / webapp-* / vault / consul / redis / minio / adminer / pgadmin / docker-stats-exporter | BEST-EFFORT | — | 512 M–1 G each | 0 |
| **`besteffort.slice` aggregate** | — | 4 G | **6 G (host-safety cap)** | 0 |

Budget sanity: protected = 5 G game floor + 2 G pak = **7 G < 16 G RAM** → ~9 GB left for
the zswap pool + interactive working set + best-effort. Best-effort is *deliberately*
squeezed (lives mostly in zswap/swap) and yields to prod+interactive on demand.

---

## 6. Deliverable 4 — Unified observability tool

**Recommendation: (a) a lightweight custom Python TUI** (`gov-top`) reading
`/sys/fs/cgroup` per container on an interval, computing rates + compression ratio.
Rationale: the host is RAM-starved — adding cadvisor+Prometheus+node_exporter+Grafana
(option b) would cost 0.5–1 GB+ of the very RAM we are trying to protect, and the
existing SkyWalking OAP (option c) is itself a ~2 GB best-effort hog we intend to *cap*,
not lean on. A ~15 MB Python process reading sysfs is the right footprint. It also
subsumes the two existing scripts (`swap-health.sh` system-wide, `soulmask-zswap-monitor.sh`
game+pak) into one per-container at-a-glance view — the user's explicit pain (htop/vmstat/sar
each show only a slice).

**All required primitives verified present per-cgroup** (read inside each container's
`/sys/fs/cgroup`, or on the host under each container's scope):

| Column | Meaning | cgroup file(s) |
|---|---|---|
| `RAM` | physical RAM incl. own zswap pool | `memory.current` |
| `anon` / `file` / `shmem` | working-set breakdown | `memory.stat` → `anon`,`file`,`shmem` |
| `swap` | uncompressed bytes swapped (zswap+disk) | `memory.swap.current` |
| `Δswap/s` | **swap rate-of-change** (leak detector) | delta of `memory.swap.current` per interval |
| `z_pool` | compressed bytes in zswap | `memory.zswap.current` |
| `z_eq` | uncompressed equiv in zswap | `memory.stat` → `zswapped` |
| **`ratio`** | **per-service compression ratio** = `z_eq / z_pool` | `memory.stat` `zswapped`/`zswap` |
| `zin/s` `zout/s` | zswap fault-in / evict rate | delta `memory.stat` `zswpin`/`zswpout` (**confirmed per-cgroup**) |
| `rflt/s` | anon refault rate (PRIMARY pressure signal) | delta `memory.stat` `workingset_refault_anon` |
| `mflt/s` | major faults/s (disk reads; spike = pak evicted) | delta `memory.stat` `pgmajfault` |
| `io r/w` | disk IO rate | delta `io.stat` `rbytes`/`wbytes`/`rios`/`wios` |
| `head%` | **headroom within limit** | `memory.current / memory.max` (and `/high`) |
| `PSI` | mem/io/cpu stall | `memory.pressure` `io.pressure` `cpu.pressure` (`full avg10`) |
| `tier` | soulmask / interactive / besteffort | cgroup path / docker label |

Compression-ratio caveat (from `CGROUP-MONITORING.md` §3): use
`zswapped / zswap` — **not** `memory.swap.current / memory.zswap.current` (the latter is
inflated by swapcached, gives a meaningless number).

**Sketch** (`gstammtisch-guide/scripts/gov-top.py`, no deps beyond stdlib):
- enumerate containers via `docker ps -q` → resolve each `/proc/<pid>/cgroup` (`0::`) to
  its `/sys/fs/cgroup/<scope>` (same resolution `setup-cgroups.sh` already uses);
- sample every N s (default 5), keep prior sample to compute all `/s` deltas + `Δswap/s`;
- one row per container sorted by tier then swap; color: PSI `full avg10` >5 red, refault
  >500/s red; a header line with host `/proc/meminfo` + `/proc/swaps` + zswap debugfs
  ratio (reuse `swap-health.sh` logic);
- `--once` for a snapshot, watch mode default; `--json` for scripting/alerts.

Optional low-cost bridge: the already-running `docker-stats-exporter` (Prometheus, on
`:9558`) can export these same cgroup fields if a lightweight Grafana is *ever* wanted —
but keep the TUI as the primary at-a-glance tool given the RAM budget.

---

## 7. Deliverable 5 — Rollout order, risk, validation

Apply in this order — **safety first, throttles last** (throttles carry over-restriction
risk and need a build to validate):

1. **[SAFETY — apply first, low risk] Best-effort `memory.max` caps.** Add
   `mem_limit` per dstdns service (§5) + create `besteffort.slice` with `MemoryMax=6G` +
   `cgroup_parent` on the compose stack. This alone prevents a future leak (or the current
   authentik-worker) from swap-poisoning the host. Risk: a container OOM-killed if its
   cap is too low → start generous, tighten later. **Do the authentik-worker
   `mem_limit=1.5G` even before its worker-recycle fix lands** — it bounds the leak now.
2. **[SAFETY] Fix the pak slice** (§4): `soulmask-paks.slice` `MemoryMin=2G` +
   `MemoryZSwapWriteback=no`, and verify the pak is charged to the slice. Low risk (only
   raises protection). Wire it into the watcher so it reapplies on Soulmask restart.
3. **[MEDIUM] `interactive.slice` for the devcontainer** (§3.2b): `MemoryHigh=5G`,
   `MemoryMax=7G`, `CPUWeight=200`. Risk: too-low `memory.max` kills the IDE → 7 G is
   deliberately generous.
4. **[MEDIUM] Persist `vm.swappiness=100`** via `99-gstammtisch-memory.conf` **only after**
   caps 1–3 are in place (with caps, high swappiness safely pushes cold anon to zswap
   instead of thrashing). Re-measure refault after.
5. **[HIGHER RISK — test in a window] `io.max` hard caps on best-effort builders.** Apply
   the `besteffort.slice` `IO*Max` (§3.3) and extend `setup-cgroups.sh` to also target
   **`buildx_buildkit_*`** (currently untargeted — the real pak-evicting IO source) and to
   reapply independent of Soulmask restart. Risk: over-throttling makes builds crawl →
   start at 31 MB/s / 100r+400w IOPS, watch a real build, relax if builds stall
   unacceptably while pak `mflt/s` stays ~0.
6. **[CLEANUP] cpu.weight tiers** (800/200/20) — proportional, essentially free with 7
   idle cores; apply last as fine-tuning.

**Validation (run `gov-top.py` throughout):**
- After step 1: re-measure per-container `memory.swap.current` — authentik-worker capped;
  total swap-in-use trending **down**; `MemAvailable` **up**.
- After step 2: `soulmask-paks.slice/memory.current ≈ 1.8 G`, `memory.swap.current=0` for
  the pak slice; game `mflt/s` ~0 **during a docker build** (the acceptance test — build
  IO must no longer evict the pak).
- After step 5: run a full `docker build` / test-runner suite and watch Soulmask
  `io.pressure full avg10` and `mflt/s` stay low while the build's `io.stat` rate sits at
  the cap. Confirm builds still complete in acceptable time.
- Ongoing: `rflt/s` on Soulmask should sit 0–100/s steady (per `soulmask-zswap-monitor.sh`
  calibration); sustained >500/s ⇒ raise `memory.min`.

**Rollback:** each mechanism is independent and reversible — remove a `mem_limit` /
`echo max > .../memory.max`, `systemctl revert`/disable a slice, `sysctl -w
vm.swappiness=5`, `echo "" > .../io.max`. Nothing here is destructive; no data migration.

---

## 8. Appendix — concrete deltas to the already-staged config
| File (under `gstammtisch-guide/files/`) | Change |
|---|---|
| `etc/systemd/system/besteffort.slice` | **create** (§3.3) — the missing tier (rename/replace `dev-workloads.slice`'s role) |
| `etc/systemd/system/interactive.slice` | **create** (§3.3) |
| `etc/systemd/system/soulmask-paks.slice` | `MemoryMin=150M → 2G`; `MemoryZSwapWriteback=yes → no` (§4) |
| `usr/local/sbin/setup-cgroups.sh` | add `buildx_buildkit_*` to bench targets; apply pak-slice knobs; make bench caps reapply independent of Soulmask restart. **DONE 2026-07-07**: also generalized from single-instance ("first WSServer container wins") to N-instance — iterates every running instance, `system.slice MemoryMin` now dynamic (sum of applied instance floors + 1G) — see SOULMASK.md §9b. |
| `usr/local/sbin/soulmask-instance-lib.sh` | **DONE 2026-07-07 — create.** Shared N-instance helpers (discovery, per-instance config load, RCON port/pass, `-c` selection) sourced by setup-cgroups.sh, soulmask-cgroup-watcher.sh, soulmask-shutdown.sh, soulmask-pak-ramdisk-setup.sh, soulmask-mempress.sh, soulmask-startup-cgroup.sh. |
| `etc/gstammtisch/instance-defaults.env` + `instances.d/<uuid>.env` | **DONE 2026-07-07 — create.** Per-instance SOULMASK_MIN/LOW/HIGH/WRITEBACK, ROLE, PAK_RAMDISK — see SOULMASK.md §9b. |
| `usr/local/sbin/soulmask-cgroup-watcher.sh` | **DONE 2026-07-07**: single-container "wait once" → N-instance reconcile loop (per-cid readiness state in `/run/soulmask-cgroup-watcher/`). |
| `usr/local/sbin/soulmask-shutdown.sh` | **DONE 2026-07-07**: stops every running instance, client/standalone before main (§9 cluster rule); `soulmask-graceful-stop.service` `TimeoutStopSec` 210s→600s accordingly. |
| `usr/local/sbin/soulmask-pak-ramdisk-setup.sh` (+ new `-teardown.sh`) | **DONE 2026-07-07**: one shared tmpfs bind-mounted into every `PAK_RAMDISK=1` instance (was: one volume only); target discovery now reads `instances.d/*.env`, not the old `SOULMASK_PAK_DIR`/state-file/filesystem-scan chain. |
| `.devcontainer/devcontainer.json` (dstdns repo) | add `runArgs` cgroup-parent + memory (§3.2b) |
| dstdns `docker-compose.governance.yml` | **create** — `cgroup_parent: besteffort.slice` + `mem_limit` per service (§3.2a, §5) |
| `etc/sysctl.d/99-gstammtisch-memory.conf` | already sets `swappiness=100`; **apply it** (currently unapplied; live=5) after caps land |
| `scripts/gov-top.py` | **create** — unified per-container TUI (§6) |
| Pterodactyl panel | set Soulmask memory backstop (SERVER_MEMORY≈8000) (§3.2c) |

---

## 9. Open decisions (2026-07-06 discussion) — RAM-constrained, 2nd instance incoming

> **DECIDED 2026-07-07** (operator review): #1 ✔ writeback=1 for the game
> (M4 login test is the revert gate; setup-cgroups.sh default now 1). #2 ✔ pak
> floor from measurement + `memory.zswap.max=0` (see MEASUREMENTS.md M2). #3 ✔
> swappiness stays 100 (validation recipe: MEASUREMENTS.md M5). #4 ✔ keep
> memory.high, tightened to **7G** (not 8G — still 1G transient headroom, resumes
> proactive cold-tail compression; back to 8G if logins regress). #5 ✔. #6 ✔
> daemon.json in repo + install.sh. #7 ✔ BUILDX_BUILDER (documented in
> modern-debian-tools) + buildx_buildkit_* added to setup-cgroups bench targets.
> #8 ✔ template+docs updated. #9 ✔ operator set max_pool_percent=40 (zswap-config
> updated); tests next. #10 ✔ systemd-oomd installed. #11 → ciu governance
> implementation in flight (overlay injection + derived riops). #12 = docs-only
> (see amended row). #14 → wings CgroupParent feasibility study in flight
> (`wings-cgroup-parent-proposal.md`); interim ancestor floors live in
> setup-cgroups.sh — NOTE: `system.slice` MemoryMin must be ≥ game floor + host
> daemons (7G = 6G + 1G); a smaller value (e.g. 5G) silently CAPS the game's
> effective floor at that value — the login-failure regime again.
> Readiness signal switched from RCON to the game-log `[SERVER_LIST] registe
> server ... succeed.` line (RCON responsiveness is not a health signal).

A second Soulmask instance (base map, clustered for character transfer — see
`SOULMASK.md` §9) adds a second ~6 G hot set to a 16 G host. That forces a
leaner posture than the plan above. Positions below are recommendations, not
yet applied.

| # | Question | Position |
|---|---|---|
| 1 | `writeback=1` for *all* cgroups incl. game? | **Lean yes for the game, gated on a login-latency test.** Evidence: ~3.9 G of game swap already sits on disk (Finding C) at rflt 0–2/s. But logins touch the cold tail (login failures = the 5G/6G lesson), so flip `memory.zswap.writeback=1` for the game only after measuring login p95 with warm vs cold tail (§10 M4). zswap's LRU means only coldest-of-cold reaches disk. Devcontainer/besteffort: writeback=1 unconditionally. |
| 2 | Pak floor: protect only the hot part (~150 M?) | **Yes — and bypass zswap entirely for pak** (`memory.zswap.max=0` on the pak slice) since pak compresses 1.006× (Finding B): zswap for pak wastes CPU + RAM. Cold pak → disk directly. Measure hot pak with `vmtouch -v` under pressure (§10 M2), then set `MemoryMin` = measured hot + margin. Supersedes §4's "pin 2G". With the 2nd instance sharing one pak tmpfs, this floor is paid once. |
| 3 | Lower `swappiness` to 5–10 to shrink buff/cache? | **No — keep 100.** Misreading: of the 3.1 G buff/cache, ~0.9 G is resident pak shmem (tmpfs counts here) and the rest is mostly executable text (game/wings/docker binaries) + Docker layer cache. Low swappiness makes the kernel drop *file* pages (code!) instead of swapping anon → major-fault storms from disk. With zswap, anon reclaim is the cheap direction; swappiness=100 is the design, not an accident (MEMORY-ARCHITECTURE §4). Per-cgroup swappiness does not exist in cgroup v2. |
| 4 | Drop `memory.high` for prod entirely? | **Keep as pressure valve at 8 G** (well above ~6 G steady). Removing it entirely is defensible once besteffort caps exist, but high is what pre-compresses the cold tail during calm periods; without it the squeeze happens reactively under global pressure. Revisit after instance 2: two instances may need high to arbitrate between them. NEVER set high near steady RSS again (login throttling). |
| 5 | `memory.max` only as leak insurance | Agreed — generous values, per container. **Addition: the real leak-stopper is `memory.swap.max`** — with 69 G swap, a leaking container (authentik-worker: 19 G swap) poisons swap/zswap long before any sane memory.max fires. Set `memory.swap.max` (e.g. 8–12 G) on besteffort.slice so leaks OOM inside their tier early. |
| 6 | `daemon.json` | Candidates: **`live-restore: true`** — verified against wings v1.13.1 source: wings re-attaches to running containers on its own restart by design; after a dockerd restart the attach stream dies → brief spurious offline→crash→online flap, then the crash handler re-attaches (works, but best-effort, no upstream guarantee). Net win: game survives dockerd upgrades. Also `log-opts` max-size (json logs currently unbounded), builder GC limits. Daemon-wide `"cgroup-parent"` exists (default parent for all containers that don't set one) but is unusable here: wings can't override it, so either the game would land in a capped tier or stray containers would land in the protected tier. |
| 7 | Buildkit placement | Builder `keen_mestorf` (docker-container driver, buildx v0.35) runs unconfined. **Caveat (source-verified): the `cgroup-parent` driver-opt is silently ignored when dockerd uses the systemd cgroup driver** (this host does) — slice placement via buildx is impossible. The resource driver-opts (`memory`, `memory-swap`, `cpu-quota`, `cpu-shares`, `cpuset-cpus`; buildx ≥0.12) ARE applied unconditionally → recreate the builder with `--driver-opt memory=4g,cpu-shares=…` and add `buildx_buildkit_*` to setup-cgroups.sh bench targets for io.weight/io.max (§8 already planned). True slice placement would require running buildkitd as a systemd service (`Slice=besteffort.slice`) + a `remote`-driver builder. Note: plain `docker build` (docker driver) runs inside dockerd's own cgroup (system.slice/docker.service) — route all builds (incl. modern-debian-tools `release-bake.sh`/docker-repack) through the confined named builder via `BUILDX_BUILDER`. |
| 8 | Devcontainer/base-image docs | Yes: `modern-debian-tools-python-debug/templates/devcontainer.json` already has a `runArgs` array — add `"--cgroup-parent=interactive.slice"` (+ document the host-side slice prerequisite in DEVCONTAINER-LIFECYCLE.md). The image can't enforce placement (cgroup is fixed at container create by the host) but can *display* effective limits in the shell banner (read /sys/fs/cgroup limits from inside). |
| 9 | Tune zswap `max_pool_percent` (now 30)? | Only after measuring the hot/warm/cold split (§10). Target: pool ≈ compressed(warm set of all tenants). Too big steals RAM from hot; too small forces disk. With pak bypassing zswap (#2) and cold tails on disk (#1), pool demand *shrinks* — 30 % (4.8 G) may already be generous. |
| 10 | Host package prerequisites | `systemd-oomd` (NOT installed — all ManagedOOM* lines inert), `vmtouch` (pak hot-set measurement), damo (present in `scripts/damon-analysis/venv`). Add to install.sh. |
| 11 | ciu stack-wide limits | ciu shells out to `docker compose`; its spec has no resource keys, but dstdns templates already use a `[deploy.resources]` TOML convention (cpu_shares/mem_limit per service, `enabled=false` today). Best injection point (per SPEC S8 rationale): **ciu's overlay generator** (`composefile.generate_overlay()`, engine Step 15) — inject `cgroup_parent: besteffort.slice` + default `mem_limit` into every service unless the author set one. Values-only defaults via `ciu.global.defaults.toml.j2` also work today without ciu changes (set `deploy.resources.enabled=true` + mem_limits in dstdns). |
| 12 | Label-driven dynamic limits? | **Decision: docs-only alternative, not implemented.** Feasible for *limits*, not for slice placement (cgroup-parent is create-time only; post-hoc migration fights docker+systemd). Two post-hoc mechanisms would work from a label-watcher: `docker update --memory/--cpus` (docker-API-clean), and **direct cgroup writes for io.max/riops** — exactly what setup-cgroups.sh already does to bench/buildkit scopes, so IOPS caps do NOT need label support; the existing pattern (watcher + name/image matching) covers them. Prior art: none established (autoheal/watchtower prove the events+label pattern for restarts/updates only). Revisit only if a launch path appears that neither ciu, compose, devcontainer runArgs, nor setup-cgroups matching can reach. |
| 13 | `cgroup_parent` chicken-and-egg | Not a problem: slices are static systemd units; install unit files once (install.sh), `systemctl daemon-reload`. Compose references then work forever — systemd creates/activates the slice on demand. If the unit file is missing, systemd creates a transient slice with **no limits** (degrades gracefully, doesn't fail). |
| 14 | Protection floors (NEW — Finding A) | **Wings v1.13.1 source-verified: no `CgroupParent` support at all** (no config key, HostConfig.CgroupParent never set) — the game cannot be placed under `soulmask.slice`; it stays in `system.slice` for the foreseeable future. Therefore the ONLY way to make the floors real: `systemctl set-property system.slice MemoryMin=7G` (makes the game's explicit 6G effective; side-benefit: shields sshd/dockerd/wings → helps SSH-during-pressure) + `systemctl set-property soulmask.slice MemoryMin=<pak floor>`; wire both into setup-cgroups.sh so the watcher re-asserts. Weakens tiering only while non-prod containers still live in system.slice — moving them to interactive/besteffort slices (§3) restores clean separation. |

## 10. Measurement plan (DAMON + counters) — before the next tuning round

Tooling exists: `scripts/damon-analysis/` (damo v3.2.9 venv, `damon_cli.py
timeseries-pid`, `rcon_probe.py` RCON-latency correlator), `vmtouch` (to
install), cgroup counters via `soulmask-zswap-monitor.sh`.

**SLOs to tune against** (from SOULMASK.md calibration history):
game rflt/s ≤ 20/s sustained during play (spikes on area load OK);
p_mf/s (pak major faults) ≈ 0 during play; login success on first attempt;
PSI `memory.pressure full avg10` < 2 on the game cgroup.

| # | Measurement | How | Decision it gates |
|---|---|---|---|
| M1 | Game hot/warm/cold split, per scenario: idle, 3-player play, login burst, area transition, save/backup event | `damon_cli.py timeseries-pid <pid> --interval 5 --min-regions 100 --max-regions 2000` + `rcon_probe.py` in parallel; correlate with `memory.stat` refaults. NOTE: DAMON vaddr overstates (mmap) — use as *shape*, calibrate absolute via mempress stepping | memory.min per instance (#14), writeback=1 for game (#1), instance-2 floor budget |
| M2 | Pak hot set | Under natural pressure, `vmtouch -v /mnt/soulmask-paks/WS-LinuxServer.pak` snapshots over a play session (resident map = hot+warm); p_rf/s & p_mf/s from monitor | pak MemoryMin (#2) |
| M3 | Cold-tail size on disk | game `memory.swap.current − zswapped` over days + rflt/s | how much RAM writeback=1 actually frees (#1) |
| M4 | Login latency vs cold tail | scripted: idle server ≥2 h (tail cold), player login, measure wall-clock to in-game + mflt/s spike; repeat warm | GO/NO-GO for game writeback=1 (#1) |
| M5 | zswap pool utilization | debugfs `pool_total_size`/`stored_pages`, reject counters, per-cgroup `zswap.current/zswapped` ratios | max_pool_percent (#9) |
| M6 | Build-storm interference | run release-bake/docker-repack in confined builder while M1 runs on game | validates besteffort io.max + tier design (§7 step 5) |

_End of plan._
