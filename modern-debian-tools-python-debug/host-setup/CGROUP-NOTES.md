# cgroup v2 notes — what a slice unit can't express, and BFQ caveats

Reference for the two questions that come up every time someone reads
`host-setup/` and asks "why is there a script at all — isn't this just unit
files?":

1. [What a slice unit cannot express](#what-a-slice-unit-cannot-express), and
   which piece of `host-setup/` owns each gap.
2. [BFQ caveats](#bfq-caveats) — on a BFQ host `IOWeight` does not mean what it
   says, and the file you'd naturally read to check it is inert.

Companion docs: [`README.md`](README.md) (what gets installed, how),
[`host-setup.env.example`](host-setup.env.example) (every tunable),
[`../DEVCONTAINER-LIFECYCLE.md`](../DEVCONTAINER-LIFECYCLE.md) (the container
side of the same story).

---

## What a slice unit *can* express

Everything static and absolute, for a cgroup that has a name known ahead of
time. `MemoryHigh/Max/Low/Min/SwapMax`, `CPUWeight`/`CPUQuota`, `IOWeight`,
`IOReadBandwidthMax` and friends, `ManagedOOM*`, `TasksMax`,
`MemoryZSwapMax`/`MemoryZSwapWriteback` (systemd ≥ 256). These survive reboot
and `daemon-reload` by themselves and need zero runtime machinery — which is
why `dev-interactive.slice`/`dev-background.slice` carry as much as they possibly can.

The five things below are the entire reason `mdt-apply-dev-caps.sh` exists.

## What a slice unit cannot express

### 1. Which containers join the tier — placement is create-time only

A slice unit describes a container of resources; it cannot reach out and pull
processes into itself. Docker fixes a container's cgroup parent at **create**
time (`--cgroup-parent`, compose `cgroup_parent:`), and there is no supported
way to move a *running* container to another slice afterwards. Nothing running
inside or beside the container can fix this after the fact.

**Consequence that bites people:** editing a slice unit and reloading changes
the limits for containers already in it, but a container created before the
tier existed is in a *different* cgroup and is unaffected forever. After any
placement change you must **recreate** the container — rebuild the
devcontainer, `docker compose up -d --force-recreate`.

*Owned by:* [`../templates/devcontainer.json`](../templates/devcontainer.json)
`runArgs` (devcontainers), ciu governance (compose stacks). Not by this
companion at all — it only supplies the destination.

**Graceful degradation:** if the named slice has no unit file, systemd invents
a **transient, unlimited** slice of that name and the container starts
normally. That is why shipping the runArg is safe on ungoverned hosts — and
also why a missing unit fails *silently* rather than loudly. `systemd-cgls`
showing the slice proves placement only, never that any limit is in force;
`mdt-host-check.sh` checks the unit file, not the tree.

### 2. Transient docker scopes — the units don't exist until the container does

Two workloads can't be placed into a tier declaratively at all:

- **buildx/BuildKit workers** (`buildx_buildkit_*`) are created on demand by
  buildx. There is no compose file to put `cgroup_parent:` into and no unit
  file to write — the scope is named after a container ID that changes on every
  recreation.
- **The devcontainer's own scope** exists only while the container runs.

You can only reach these at runtime, on a unit name you discover by inspecting
the running container. Hence a sweep, not a unit.

*Owned by:* `mdt-apply-dev-caps.sh` (`docker ps` → `/proc/<pid>/cgroup` → the
scope name), re-run by `mdt-host-slices.timer` so containers created since boot
get caught within `SWEEP_INTERVAL`.

> BuildKit nests its own sub-cgroups *inside* the container, so PID 1's cgroup
> path continues below `docker-<id>.scope`. The script trims back to the
> `.scope` component — limits there cover the whole subtree.

### 3. Caps expressed as a percentage of what the disk actually does

`IOReadBandwidthMax=/dev/vda 31M` is an absolute number. "60% of this device's
sustained random-read IOPS" is not something a unit file can say, and it is the
only form of the rule that ports between hosts. Only a benchmark knows the
number.

*Owned by:* `mdt-io-baseline.py` (measures, caches) + `mdt-apply-dev-caps.sh`
(derives `DEV_IO_CAP_PCT`% and applies via `systemctl set-property --runtime`
to the **root `dev.slice`**, not per-child — host dev-tier cgroup governance
rollout: one absolute IOPS/bandwidth ceiling covers `dev-interactive.slice`
and `dev-background.slice` combined, cgroup v2's hierarchical accounting does
the rest, and neither child needs its own IO cap). The unit files keep
deliberately **tight** static caps on `dev.slice` as the boot-window fallback:
between boot and the first sweep, and forever on a host where nobody ran the
benchmark, those statics are the operative values.

**Why the cap sits at 60–80% and never 100%:** a device driven to saturation
queues everything behind the burst, which is precisely the stall the tiering
exists to prevent — the IDE (or a production tier on a shared host) must never
wait behind a build storm. Below ~60% you have stopped bounding a burst and
started throttling ordinary work. This is a **whole-estate** ceiling (it
bounds every dev/test/build/interactive container together, easily 10-15+ at
once) — genuine per-container guarantees are a separate mechanism (explicit
`docker run --memory`/`--cpus`/`--device-*-iops` flags in whatever spawns the
container, e.g. `cmru`'s tester-gate) that composes with this one: cgroup
limits nest, and the effective cap on any single container is the stricter of
its own flags and `dev.slice`'s aggregate ceiling.

### 4. Attributes systemd has no directive for

Not every cgroupfs file has a unit setting. Two matter here:

| Attribute | Directive? | Handling |
|---|---|---|
| `memory.zswap.writeback` | `MemoryZSwapWriteback=` — systemd ≥ 256 **only** | `install.sh` drops the line on older systemd; `mdt-apply-dev-caps.sh` raw-writes the file as fallback (harmless double-set on new hosts) |
| `io.bfq.weight` | **never** — systemd only knows `IOWeight` | raw write, see [BFQ caveats](#bfq-caveats) |

### 5. cgroup2 mount options — not a unit setting at all

`memory_recursiveprot` is a **mount flag** on `/sys/fs/cgroup`, not a property
of any cgroup. Without it, a slice's `MemoryLow`/`MemoryMin` does **not** reach
the container pages below it — every floor and soft protection in both tiers
silently protects nothing, while `systemctl show` happily reports the value you
set. systemd ≥ 248 mounts it by default at boot, but a runtime remount can
strip it (observed on the game host, 2026-07-17).

Only a process in the **init cgroup namespace** can restore it — i.e. a host
root shell, never anything inside a container (the kernel silently ignores the
change from a non-init namespace):

```bash
mount -o remount,nsdelegate,memory_recursiveprot /sys/fs/cgroup
```

*Owned by:* `mdt-apply-dev-caps.sh` (`CGROUP2_FLAGS=warn|fix`) and
`mdt-host-check.sh`, which **FAILs** — not warns — when it is missing.

---

## Why `set-property --runtime`, not raw cgroupfs writes

Where a systemd property *does* exist, use it. Docker scopes are transient
systemd units, and on **every** `systemctl daemon-reload` systemd re-applies
its own recorded properties to the scope's cgroup — silently wiping any value
written directly into cgroupfs. Any package that ships a unit file triggers a
reload, so this happens on ordinary `apt install` runs. (Observed on the game
host, 2026-07-07: installing `systemd-oomd` reset a scope's whole memory band
about an hour after it had been applied *and verified*.)

`systemctl set-property --runtime <unit> …` makes systemd the owner of the
value, so a reload **re-applies** it instead. `--runtime` writes a drop-in
under `/run`: it survives `daemon-reload`, and is gone after reboot — which is
correct here, because `mdt-host-slices.service` re-derives everything at every
boot from a baseline that may meanwhile have been re-measured.

The exception is attributes systemd has no property for (`io.bfq.weight`,
`memory.zswap.writeback` on systemd < 256). Those get raw writes — and because
systemd does not manage them, the raw write is *not* wiped by a reload.

---

## BFQ caveats

We select BFQ (`etc/udev/rules.d/60-bfq-scheduler.rules`) because it is the
only multi-queue scheduler that enforces cgroup v2 proportional IO at all:
under `none` or `mq-deadline`, `IOWeight` is completely inert. Four things
about that are not obvious.

### 1. `IOWeight` is rescaled — ratios above 100 are not what you wrote

systemd's `IOWeight` is `1..10000` (default 100). BFQ schedules on
`io.bfq.weight`, which is `1..1000` (default 100). systemd maps between them
piecewise-linearly, pinning both defaults at 100:

```
io_weight <= 100 :  bfq =    1 + (io_weight -   1) *  99 /   99   # identity
io_weight >  100 :  bfq =  100 + (io_weight - 100) * 900 / 9900   # ~11x compression
```

| `IOWeight` | `io.bfq.weight` | what you probably meant | what you get |
|---:|---:|---|---|
| 10 | 10 | 0.1× | 0.1× ✅ |
| 50 | 50 | 0.5× | 0.5× ✅ |
| 100 | 100 | 1× (default) | 1× ✅ |
| 200 | 109 | 2× | **1.09×** |
| 500 | 136 | 5× | **1.36×** |
| 1000 | 181 | 10× | **1.81×** |
| 4500 | 500 | 45× | 5× |
| 10000 | 1000 | 100× | 10× |

> **Rule of thumb: keep every `IOWeight` at or below 100 and express ratios by
> *lowering the loser*, never raising the winner.** Below 100 the mapping is
> the identity, so the ratio you write is the ratio you get.

This is exactly why the shipped tiers are `dev-interactive.slice IOWeight=100` vs
`dev-background.slice IOWeight=10` — a true 10:1 — rather than the 1000-vs-100 that
reads more emphatically and would actually deliver 1.81:1. Raising the
interactive weight to "make it stronger" makes it *weaker* relative to intent.

CPU has no equivalent trap: `CPUWeight` ratios are exact.

### 2. Verify on `io.bfq.weight`, never `io.weight`

Under BFQ the `io.weight` file is the *input* systemd wrote, not the value in
force. Reading it back tells you nothing about scheduling:

```bash
cat /sys/fs/cgroup/dev-interactive.slice/io.bfq.weight   # what BFQ actually uses
cat /sys/fs/cgroup/dev-background.slice/io.bfq.weight
```

`mdt-host-check.sh` prints both side by side for this reason.

### 3. `io.bfq.weight` has no systemd property — raw write only

There is no unit directive and no `set-property` for it. `mdt-apply-dev-caps.sh`
raw-writes `default 1` into the bench/buildkit scopes. That write is safe from
the daemon-reload wipe (systemd doesn't manage the attribute) but is gone when
the scope dies, i.e. when the container stops — the timer sweep re-applies it.

### 4. The io.max caps are scheduler-independent — BFQ is not load-bearing for them

`io.max` is enforced by blk-throttle, above the scheduler. Every cap in this
setup works identically under `none`, `mq-deadline` or BFQ. Only the *weights*
need BFQ. This is the whole reason the udev rule **deliberately does not match
NVMe**: at NVMe request rates BFQ's per-request cost usually outweighs what the
weights buy, so NVMe hosts keep `none` and rely on the caps alone. On such a
host `mdt-host-check.sh` warning "no disk uses BFQ" is the expected result, not
a defect.

**Three device classes, three answers** — `etc/udev/rules.d/60-bfq-scheduler.rules`
already covers the first two; the third is a documented option, not shipped
(see [io.cost vs BFQ](#iocost-vs-bfq--an-option-this-host-doesnt-use-yet) below):

| Device class | `KERNEL==` match | Weight fairness |
|---|---|---|
| virtio-guest (`vd[a-z]`) | matched | BFQ |
| bare-metal SATA/SCSI/SAS (`sd[a-z]`) | matched | BFQ |
| NVMe (`nvme*`) | **not** matched, on purpose | none today — `io.max` caps only; `io.cost` if weight fairness is ever needed there |

### 5. Weights only decide contention; caps bound absolutely

A weight does nothing on an idle device — it only settles who yields when two
cgroups queue against the same device at once. That is why both mechanisms
exist: the per-child `IOWeight`s sort out interactive-vs-background under
contention, while the root `dev.slice`'s `io.max` bounds the whole estate
**absolutely**, so a build storm cannot saturate the disk even when nothing
else is currently asking for it (the next latency-sensitive burst must not
have to queue behind it) — and even sustained interactive activity can't
either, which is the point of putting the cap on the shared parent instead of
duplicating it per child.

---

## Verification cheat sheet

```bash
mdt-host-check.sh                                    # everything below, with verdicts

grep cgroup2 /proc/mounts                            # memory_recursiveprot present?
systemctl show dev-interactive.slice -p FragmentPath     # unit file exists (not transient)?
cat /sys/fs/cgroup/dev.slice/io.max                      # aggregate cap in force (statics or measured?)
cat /sys/fs/cgroup/dev-interactive.slice/io.bfq.weight   # NOT io.weight
docker inspect -f '{{.HostConfig.CgroupParent}}' <c> # placement — create-time, recreate to change
journalctl -u mdt-host-slices.service -n 40          # what the last sweep did
```

## zswap writeback — who may page to disk

Policy on these hosts: **every tier may drain its coldest pages from zswap out
to disk swap.** zswap is a cache, not a destination — pinning one tier's cold
tail in it spends a fixed share of RAM (`max_pool_percent`) on pages nobody is
touching, and zswap's own LRU already evicts only the coldest-of-cold. The one
documented exception is a cgroup holding incompressible data, which is better
off bypassing the pool entirely (`memory.zswap.max=0`) than paying zstd for a
~1.0x ratio.

Two different knobs, easy to confuse:

| Knob | 0 means | 1 / non-zero means |
|---|---|---|
| `memory.zswap.writeback` | cold pages **stay** in the compressed pool, never reach disk | pool LRU may evict to disk swap |
| `memory.zswap.max` | **bypass** the pool — anon goes straight to disk swap | may use the pool, up to this many bytes |

**`memory.zswap.writeback` is hierarchical.** A `0` on any ancestor disables
writeback for the whole subtree, so a cgroup reading `1` can still be denied by
a parent. Check the ancestors, not just the leaf.

### Test

```bash
# Every cgroup that DENIES writeback. Empty output = the whole host allows it.
find /sys/fs/cgroup -name memory.zswap.writeback -exec sh -c \
  '[ "$(cat "$1")" = 0 ] && echo "DENIED: ${1%/memory.zswap.writeback}"' _ {} \;

# Every cgroup that BYPASSES the pool (straight to disk).
find /sys/fs/cgroup -name memory.zswap.max -exec sh -c \
  '[ "$(cat "$1")" = 0 ] && echo "BYPASS: ${1%/memory.zswap.max}"' _ {} \;

# Walk one cgroup's ancestors — the hierarchical rule above.
p=/sys/fs/cgroup/dev-interactive.slice
while [ "$p" != /sys/fs/cgroup ]; do
    printf '%-52s %s\n' "$p" "$(cat "$p/memory.zswap.writeback" 2>/dev/null)"
    p=$(dirname "$p")
done
printf '%-52s %s\n' /sys/fs/cgroup "$(cat /sys/fs/cgroup/memory.zswap.writeback)"

# systemd's own view for a unit (needs systemd >= 256).
systemctl show dev-interactive.slice -p MemoryZSwapWriteback

# Global pool state — writeback itself has no global switch in cgroup v2,
# it is per-cgroup only.
grep . /sys/module/zswap/parameters/* 2>/dev/null
```

### Toggle

```bash
# Dev tiers — use the supported knob, not a raw write:
#   /etc/mdt/host-setup.env :  DEV_INTERACTIVE_ZSWAP_WRITEBACK=yes|no
sudo "$PWD/install.sh"          # re-renders + reinstalls the slice units

# Any other unit, runtime only (gone at reboot, survives daemon-reload):
systemctl set-property --runtime <unit> MemoryZSwapWriteback=yes

# Any other unit, persistent (drop-in under /etc/systemd/system.control):
systemctl set-property <unit> MemoryZSwapWriteback=yes

# systemd < 256 has no directive — raw write, and NOT reload-safe:
echo 1 > /sys/fs/cgroup/<path>/memory.zswap.writeback
```

Setting it to `no` is defensible only when you have *measured* stalls caused by
swap-in on that tier. Weigh it against the pool RAM it permanently occupies:
that RAM is taken from every other tier, including production.

---

## io.cost vs BFQ — an option this host doesn't use (yet)

`io.latency` (the block-cgroup controller that would let a slice declare "keep
my reads under N µs, throttle everyone poorer than me until you do") is **not
compiled into this kernel** (`CONFIG_BLK_CGROUP_IOLATENCY` unset — a
kernel-rebuild question, not a config one; see the kernel-rebuild doc).
`io.cost` **is compiled in** (`CONFIG_BLK_CGROUP_IOCOST=y`) and gets you a
related but different guarantee. This section is what turning it on would
actually require — it is not done on this host today, only measured.

### What it is, and how it differs from BFQ

BFQ enforces proportional weight **unconditionally** — even on an idle device,
two cgroups queuing at the same instant are serviced in weight ratio. `io.cost`
only intervenes when the device is measured to be missing its own latency
target: on a quiet device every cgroup runs unthrottled regardless of weight,
and weights only start mattering once aggregate observed latency crosses the
QoS target you configured. For a workload whose entire complaint is "there is
no IO demand from us, only 3rd-party contention we want suppressed when it
happens" (Soulmask, see the [wings-cgroups README](../../wings-cgroups/v1-legacy/README.md)
finding), that is a closer philosophical match than BFQ's always-on model —
but it needs its inputs measured, not guessed, or it does nothing (target too
loose) or throttles constantly (target too tight).

### Two things must both be configured — a model alone does nothing

`io.cost.qos` and `io.cost.model` are **root-cgroup-only** files (they do not
exist on non-root cgroups — the policy is device-wide, keyed by the target
device's `<major>:<minor>`, not a per-subtree setting):

```bash
# The linear cost model — what iocost-calibrate.sh measures (see below).
echo "<major>:<minor> ctrl=user model=linear \
  rbps=<...> rseqiops=<...> rrandiops=<...> \
  wbps=<...> wseqiops=<...> wrandiops=<...>" \
  > /sys/fs/cgroup/io.cost.model

# The QoS target — THIS is the on/off switch. A model with no enable=1 QoS
# line sits inert, exactly like a calibrated fuel gauge on an engine that
# was never started.
echo "<major>:<minor> enable=1 ctrl=user \
  rpct=95.00 rlat=<usec> wpct=95.00 wlat=<usec> min=1 max=100" \
  > /sys/fs/cgroup/io.cost.qos
```

| `io.cost.qos` field | Meaning |
|---|---|
| `enable` | `1` turns the controller on for this device; `0` (default) means the model is stored but inert |
| `ctrl` | `auto` lets the kernel self-tune the model over time; `user` pins it to exactly what you wrote — use `user`, an auto-tuned model drifts without you noticing |
| `rpct`/`rlat`, `wpct`/`wlat` | "`rpct`% of reads must complete within `rlat` µs" (same shape for writes) — this is the actual protected metric; get it wrong and either nothing throttles (target looser than the device's real latency) or the device throttles permanently (target tighter than it can sustain) |
| `min`/`max` | Bounds (as % of the calibrated model) on how far vrate is allowed to move — floor prevents starving low-weight cgroups to zero, ceiling prevents over-crediting an idle device |

**What's blocking turning this on today:** the `rbps`/`rseqiops`/`rrandiops`/
`wbps`/`wseqiops`/`wrandiops` half is done — `iocost-calibrate.sh` (wrapping
the vendored, LVM-patched `iocost_coef_gen.py`, see `scripts/debian-install-v2/tools/`)
has produced two live runs on this host (`/root/iocost-results/`). The
`rlat`/`wlat` half has **not** — those are a latency baseline this host's own
device has never had measured (virtio here, but potentially NVMe or spinning
disk underneath depending on host; `mdt-io-baseline.py`'s existing 4-point
ceiling baseline measures *throughput*, not the *latency-at-a-given-load*
number `io.cost.qos` actually needs). Do not guess at `rlat`/`wlat` — write
the model, leave `enable=0`, and treat picking a target latency as its own
measurement task before ever setting `enable=1`.

### The elevator has to leave BFQ

`io.cost` needs the device scheduler to not also be doing its own per-request
cgroup accounting underneath it — set it to `none` (exactly what
`iocost_coef_gen.py` itself does for the calibration run, and consistent with
[BFQ caveats #4](#4-the-iomax-caps-are-scheduler-independent---bfq-is-not-load-bearing-for-them)
above: NVMe hosts already run `none` and rely on caps alone). This is a
device-wide switch, not additive with BFQ — a host either runs BFQ weights or
`io.cost`, never both on the same device.

### Existing `IOWeight`s do not carry over — they were tuned to fight BFQ's curve

This is the trap. [`wings.slice`](../../wings-cgroups/v1-legacy/t1-node-cgroup-parent/wings.slice)
sets `IOWeight=7800`, and its own header comment explains why: that number
exists *only* to land on `io.bfq.weight=800` after BFQ's compression (see
[rule 1 above](#1-ioweight-is-rescaled--ratios-above-100-are-not-what-you-wrote)) —
an intended 8:1 against the default 100.

`io.cost` reads the **plain, uncompressed** `io.weight` file systemd wrote —
no BFQ-side translation happens, because BFQ is no longer the thing reading
it. Leave `IOWeight=7800` in place after switching schedulers and you silently
get a **~78:1** ratio — nearly 10x stronger than what was actually intended.
**Every slice whose `IOWeight` was hand-picked above 100 must be reset to the
literal intended ratio before (or as part of) any switch to `io.cost`.** The
upside: once retuned, `io.cost`'s weight math has no compression curve to
account for — simpler to reason about than BFQ's going forward.

### `io.max` still applies — the two mechanisms compose, they don't compete

Yes, hard bandwidth/IOPS ceilings keep working. `io.max` is enforced by
blk-throttle, a *separate* rq-qos policy from both BFQ's weighting and
`io.cost`'s latency-triggered throttling — [rule 4 above](#4-the-iomax-caps-are-scheduler-independent---bfq-is-not-load-bearing-for-them)
already established this is scheduler-independent; the same independence
holds against `io.cost`. A request is bound by whichever mechanism is
stricter at that instant: `io.max` gives the absolute "this cgroup can never
exceed X regardless of anything else" ceiling (what a build storm is bounded
by even on an idle device), while `io.cost` adds the "when the device gets
busy, protect the latency-sensitive tenant's *responsiveness*, not just its
throughput share" behavior that a static `io.max` number cannot express on its
own (`io.max` doesn't know what latency the device is currently delivering,
only bytes/ops per second). Keeping `dev.slice`'s root `io.max` in place while
adding `io.cost` underneath it is the expected combination, not a redundancy.

### What's new provisioning work, not yet built

Every `io.cost.qos`/`io.cost.model` write above is a **runtime-only** kernel
knob — nothing currently applies it at boot. Actually switching over needs a
new step (mirroring `_configure_zswap()`'s pattern) that runs at boot, after
the elevator is set to `none` and before workloads start, re-applying the
calibrated model (and, once measured, the QoS latency target) — this doesn't
exist in `debian-install-v2` or `host-setup` yet.

### Verification cheat sheet

```bash
cat /sys/fs/cgroup/io.cost.model                    # active model, keyed by devno — empty if never written
cat /sys/fs/cgroup/io.cost.qos                       # enable=0 means inert regardless of the model above
cat /sys/block/vda/queue/scheduler                   # must show [none], not [bfq], for io.cost to be live
cat /sys/fs/cgroup/wings.slice/io.weight             # what io.cost reads directly — no BFQ-side translation
```

---

## A game-server-tuned custom kernel — what it would consider (not proposed, documentation only)

Not work to do — a reference for *if* a kernel is ever rebuilt specifically
for hosting game server(s) on this or a similar host. Nothing below is
installed or recommended by default; several trade real throughput/complexity
for worst-case latency, which is only worth paying with a demonstrated need.

- **`CONFIG_BLK_CGROUP_IOLATENCY=y`** — the original motivation for this list:
  enables `io.latency`, not compiled into this host's kernel today (see
  [io.cost vs BFQ](#iocost-vs-bfq--an-option-this-host-doesnt-use-yet) above
  for why `io.cost`, already compiled in, is the nearer-term option instead).
- **Preemption model:** full `CONFIG_PREEMPT` (or `CONFIG_PREEMPT_DYNAMIC`)
  over `CONFIG_PREEMPT_NONE`/`VOLUNTARY` — lower scheduling latency for a game
  tick thread. `CONFIG_PREEMPT_RT` is the "nuclear option" (most kernel
  spinlocks become preemptible, much better worst-case latency, real
  throughput/maintenance cost, increasingly upstream) — worth knowing by
  name, not a default recommendation.
- **`CONFIG_HZ_1000`** (higher timer tick) and `CONFIG_NO_HZ_FULL` +
  `isolcpus=`/`nohz_full=`/`rcu_nocbs=` boot params, if a game server's tick
  thread is ever pinned to dedicated cores away from interrupts and other
  host work (classic low-latency/HFT-style CPU isolation).
- **`CONFIG_CGROUP_SCHED`/`CONFIG_FAIR_GROUP_SCHED`/`CONFIG_CFS_BANDWIDTH`/
  `CONFIG_RT_GROUP_SCHED`** — the last one only if real-time
  (`SCHED_FIFO`/`SCHED_RR`) priority for the tick thread is ever wanted, a
  separate, more invasive lever than anything else on this list.
- **THP mode** (`madvise` vs `always`) — already partially owned by
  `debian-install-v2`'s `thp-config.service`; cross-reference rather than
  duplicate a second knob for it here.
- **Network side** — a game server's perceived "lag" is often packet
  latency, not disk: `fq`/`fq_codel` qdisc, `CONFIG_TCP_CONG_BBR`, and
  RPS/RFS/XPS IRQ steering to keep network interrupts off any
  isolated/dedicated cores.
- **`mitigations=` boot parameter** — Spectre/Meltdown mitigations cost CPU
  in syscall-heavy paths; a security/performance trade-off worth naming
  explicitly, never a silent recommendation to disable anything.
- **`CONFIG_BLK_CGROUP_IOCOST=y`** — already compiled in on this host; keep
  it in any rebuild too (see [io.cost vs BFQ](#iocost-vs-bfq--an-option-this-host-doesnt-use-yet)).
