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

## `vm.swappiness` — common settings, and how to tell if the current one is right

**Host-global only — there is no per-cgroup knob in cgroup v2** (confirmed
both by `ls /sys/fs/cgroup/**/memory.swappiness` finding nothing, and by
`MEASUREMENTS.md` M5's own note: *"There is no per-cgroup swappiness in
cgroup v2"*). It also does **not** distinguish anonymous pages from shmem —
both sit on the same swap-backed side of the ledger. Raising it doesn't
protect anon specifically; it makes the kernel more willing to reclaim the
whole combined anon+shmem pool earlier, in exchange for holding file-backed
page cache (executables, `.so`s, Docker layers) longer. It is a *balance*
knob between two pools, not a size knob on either one.

### Settings actually in use across this estate, and why each one is what it is

| Value | Where | Rationale |
|---|---|---|
| **100** | gstammtisch prod game host, staged (`gstammtisch-guide/files/etc/sysctl.d/99-gstammtisch-memory.conf`) | With zswap fronting swap, an anon reclaim costs ~3–5 µs (decompress from RAM) — cheap. The design *wants* cold anon pushed into that pool early so file cache (Docker layers, the game binary's own `.text`, build cache) stays resident, because a **file** refault always costs a real disk read. The game itself is protected from memory pressure by cgroup `memory.min`, deliberately *not* by lowering swappiness — see `MEMORY-ARCHITECTURE.md` §4 and `MEASUREMENTS.md` M5. |
| **50** | `debian-install-v2`'s generic `vm_swappiness` tool default (`debian_install_v2/README.md`, `templates.py`) | A conservative baseline for hosts where the zswap-cheap-anon argument hasn't been validated — "anon pages stay the precious tier even with zswap making reclaim cheap." Same tool's own docs point at gstammtisch's `100` as the validated zswap-aware alternative, and `5`–`10` as the opposite extreme (below). |
| **5–10** | Same README, documented for "latency-sensitive workloads (databases) that want almost no anon reclaim regardless of swap cost" | **This is a generic, non-zswap-aware heuristic — be skeptical of it on a zswap-fronted host.** It optimizes for "never touch anon," which is exactly the tradeoff M5's reasoning argues *against* once zswap makes that reclaim cheap: going low protects anon by evicting file pages (code!) instead, which is the expensive direction here. Relevant to the schema-gate-pg question directly, since "postgres wants low swappiness" is the same generic advice — it's not automatically right on this specific host's architecture (see the applied answer below). |
| **80 / 60 / 10** | Plain RAM-size heuristic (`docs/SWAP_CONFIGURATIONS.md`, `debian-install/setup-swap.sh`): ≤2 GB→80, ≥16 GB→10, else→60 | A different, non-zswap-aware design entirely: treats swapping as uniformly costly and to be minimized as RAM grows. Doesn't reason about a compressed-pool tier at all — inapplicable to this host's actual architecture, listed here only because it exists elsewhere in the estate and could otherwise look like conflicting guidance. |
| **30** | Superseded (`GAMINGHOST-SWAP-1.md`) | The earlier doc's own value, explicitly called **"backwards"** in the `SWAP-2` rewrite: it would hoard cold anon in *uncompressed* RAM at the expense of evicting useful file cache. Historical record only — do not reintroduce. |

**Live discrepancy on this host, verified this session:** `/proc/sys/vm/swappiness`
currently reads **`10`** (re-checked live; `plan-host-resource-governance.md`
§1.1's own earlier snapshot recorded `5` — the live value has drifted since,
but is still nowhere near the intended `100`). The staged `100` in
`99-gstammtisch-memory.conf` has never actually been applied — nothing under
`/etc/sysctl.d` persists it, matching that plan doc's explicit, deliberate
decision (§7 item 4) to persist it **only after** the oversubscription caps
land, not opportunistically. Net effect right now: this host is
accidentally running close to the generic "protect anon at all costs" `5`–`10`
regime, the opposite of the zswap-aware design it's actually built for.

### The tool/metric that tells you if a given setting is right

Not a single number — a **refault-source split**, already worked out in
`MEASUREMENTS.md` M5 (titled, verbatim, "is swappiness=100 right?" — the
recipe generalizes to any value):

```bash
# Global:
grep -E 'workingset_refault_file|workingset_refault_anon|pswpin|pswpout' /proc/vmstat
sleep 60
grep -E 'workingset_refault_file|workingset_refault_anon|pswpin|pswpout' /proc/vmstat
# per cgroup: grep -E '^(zswpin|zswpout|workingset_refault_(anon|file)) ' <cg>/memory.stat

# Is anyone actually stalling on it? (refaults tell you WHAT is being
# reclaimed; PSI tells you whether it's causing pain)
cat /proc/pressure/memory
```

| Observation | Meaning | Action |
|---|---|---|
| `workingset_refault_file` ≈ 0, anon reclaim mostly served by `zswpin`, `pswpin` ≈ 0 | Cache is big enough, and anon reclaim is absorbed by the cheap (zswap) path | Current setting is right — leave it |
| `workingset_refault_file` sustained ≫ 0 (and PSI shows real pressure) | Kernel is dropping needed **file** pages — code, not data | Raising swappiness further is already near-maxed-out territory (200 ceiling) — protect the cache by capping the anon hogs instead, not by tuning this knob |
| `pswpin`/`pswpout` (real disk swap, not `zswpin`/`zswpout`) sustained ≫ 0 | The "cold" anon tail isn't actually cold, or a floor (`memory.min`) is too low | Fix floors/writeback policy, not swappiness |

The asymmetry to keep in mind on a zswap host: an anon refault served by
`zswpin` costs microseconds; a **file** refault always costs a real disk
read (ms). That's why the recipe treats `workingset_refault_file` as the
alarm signal and `zswpin`/`pswpin` as expected background noise.

### Applying it to this host, right now (measured live this session)

Against the pasted snapshot (`MemTotal` 16.0G, `MemFree` 385M, `buff/cache`
3.49G, `avail` 3.08G, swap 70.6G total / 52.2G free / 18.5G used) — same
host, checked live in this session:

- `vm.swappiness` = **10** (confirms the drift noted above).
- Two independent samples (15 s and 60 s windows) both showed
  **`workingset_refault_file` sustained around 120–165/s** — not a blip, the
  same order of magnitude both times.
- But the ground-truth costly-path counters stayed low: real disk `pswpin`
  ≈ 0.4–0.7/s, and `/proc/pressure/memory` read `avg10=0.02 avg60=0.03
  avg300=0.00` — essentially zero stall time.
- Per the decision table: sustained file refaults with **no** accompanying
  PSI pressure or disk swap-in doesn't match either alarm row cleanly — it
  reads as "the cache is being churned but is currently absorbing it without
  pain," closer to the top row (setting is fine, don't shrink the cache)
  than the second (cache too small). **Honest caveat:** this sample was
  taken while another agent was concurrently building/deploying the new
  `schema-gate-pg` stack on this same host — exactly the kind of activity
  that produces file-cache churn on its own, so this is a *busy* sample, not
  a quiet baseline. Re-run the same 60 s `/proc/vmstat` delta during an idle
  window before treating 120–165/s as steady-state.
- Bottom line on "do we need the 3.5G page cache": nothing measured here
  says it's excess. The refault rate under load says real work is landing on
  it; PSI says it's coping. Shrinking it (via a lower swappiness, which
  would also fight the zswap-aware design intent above) is not supported by
  this evidence — if anything, today's live `swappiness=10` is already
  biased toward protecting anon over cache, the opposite of what the host's
  own design (`100`) calls for.

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

## Per-container `memory.min` guarantees — the `dev-memory_min_guaranteed` tier (not built, documentation only)

Not installed today. Researched in depth during dstdns's P165 sql-mutation-gate
incident (2026-09-03 — a disposable Postgres's 3GB `shared_buffers` produced
periodic zswap-refault CPU bursts that stalled the production game server's
network for several seconds every ~4 minutes) while designing a way to give a
**continuously-processing** container (unlike the bursty/transient population
of `dev-background.slice`) a genuine floor against thrashing, without letting
that floor compete with the game server's own guarantee. Recorded here because
it's the `memory.min` analog of an already-established pattern in this file
(§3 above): a whole-estate ceiling on a shared parent, composed with genuine
per-container guarantees applied at the leaf.

### Why not just add `MemoryMin` to `dev-background.slice`

`memory.min` is **not** a simple "protect whatever you set" knob — the kernel
distributes a slice's effective protection across every child *currently using
memory*, proportional to usage, regardless of whether that child declared
anything of its own. A child with no declared `memory.min` defaults to `0` as
its own base claim, but if the parent has protection left unclaimed by
explicitly-declaring siblings, that surplus still flows to it, proportional to
its own usage share. Concretely: add `MemoryMin=800M` directly to
`dev-background.slice` today, and every ordinary gate/test-runner container
already living there — none of which asked for any protection — would start
absorbing a share of that 800M under contention, simply by virtue of using
memory. That's a silent behavior change for a large, heterogeneous, shared
tier, not a no-op for anyone who doesn't opt in.

**Consequence:** the guarantee needs its own slice, with a population limited
to containers that intentionally want (and are individually admission-checked
for) a real floor — so the redistribution surplus has nowhere else to go.

### The design: static ceiling + admission control, not dynamic reconciliation

Two ways to make a lone (or small) set of children's guarantees actually
effective were considered:

1. **Dynamic**: a manager script recomputes the parent's `MemoryMin` on every
   container start/stop, summing whatever's currently claimed, and pushes it
   via `systemctl set-property --runtime` (see "Why set-property --runtime"
   above — this is the same reload-safe mechanism already used for IO caps,
   never a raw cgroupfs write, which a routine `apt install`-triggered
   `daemon-reload` would silently wipe).
2. **Static**: host-setup provisions a fixed ceiling once, as a human decision
   ("this is the total we will ever guarantee across all
   individually-governed containers combined"), and the consumer (ciu) does
   pure **admission control** — before starting a container with a declared
   claim, sum the currently-running claimants' own declared values (read
   straight from cgroupfs or from ciu's own governance config, no systemd
   interaction needed) and refuse to start if admitting this one would exceed
   the static ceiling.

**Chose (2).** Dynamic slice-property management needs host-level
`systemctl`-management privilege — meaningfully more than a container
orchestration tool needs today, and a bigger blast radius for its own bugs (a
mis-computed sum corrupts a *host* cgroup property, not just one stack). It
also creates a drift class of bug: a container that stops out-of-band (crash,
OOM-kill, a bare `docker stop`) isn't reliably observed, so a purely
start/stop-triggered reconciler ratchets the parent's claimed floor upward
over time unless a periodic GC pass also exists to catch it. The static
ceiling has none of that — nothing persistent to keep in sync, no privilege
expansion, and a refusal-to-start is exactly this codebase's existing
fail-fast doctrine (`AGENTS.md` §4.2a: a default is legitimate only when it's
a policy choice correct in the absence of information — a human-set ceiling
is exactly that; a silently-recomputed one risks becoming the "silent
invention" hazard that section warns against) applied to memory admission
instead of config values.

### The one invariant that actually matters: keep the parent's claim exactly matched, never generous

`dev-memory_min_guaranteed.slice`'s own `MemoryMin` only protects its children
if it has enough effective protection handed down from **its own** parent
(`dev.slice`) — the ancestor-chain rule already documented above (§5, and see
`../../scripts/gstammtisch-guide/` `soulmask_tmpfs.slice`, which independently
arrived at the identical constraint for the production game server's tmpfs
tiers: *"parent's MemoryMin must be ≥ children's, or the child's floor is
silently ineffective."*) So `dev.slice` needs a `MemoryMin` too — not just the
new leaf slice.

The subtle part: it is tempting to give `dev.slice` some **generous** headroom
"just in case" — exactly the mistake this section opened by warning against,
now one level up. If `dev.slice`'s own `MemoryMin` exceeds what
`dev-memory_min_guaranteed.slice` itself currently claims (its own declared
value, capped by its *actual live usage* — the kernel formula is
`min(usage, declared)`, so an under-utilized guaranteed slice claims less than
its ceiling even if it's entitled to more), the leftover surplus at the
`dev.slice` level flows to **`dev-interactive.slice` and
`dev-background.slice` too** — recreating the exact leak this whole design
exists to avoid, just one hop higher.

**The fix, and the reason for the `dev-memory_min_guaranteed.slice` name and
value pairing the operator proposed**: set `dev.slice`'s own `MemoryMin` to
*exactly* the same number as `dev-memory_min_guaranteed.slice`'s own ceiling —
never more. When the guaranteed slice is fully utilized (its declared
children's claims sum to its own ceiling), this leaves zero surplus at
`dev.slice`, so nothing leaks to the other two tiers. There is one residual,
bounded gap even with matched values: whenever the guaranteed slice is
*under*-utilized relative to its own ceiling (e.g. the ceiling is provisioned
for two 128M containers but only one is currently running), the unclaimed
portion at the **leaf** slice still has nowhere else to flow at the `dev.slice`
level *specifically because the two numbers are pinned equal* — verified by
walking the formula: `dev-memory_min_guaranteed`'s own `protected` value
already absorbs 100% of what `dev.slice` hands down whenever
`dev-memory_min_guaranteed`'s usage ≥ its own ceiling, and even below that
threshold the surplus term at `dev.slice` is bounded by `dev.slice`'s
`MemoryMin` value itself — i.e. the worst-case leak to the other two tiers is
capped at the size of the guaranteed-tier's own ceiling, not unbounded. Given
the ceiling is meant to be a small, deliberately conservative number (low
hundreds of MB, not GB — this host has ~367M genuinely free under normal
production load, see the P165 incident numbers), a bounded worst case of "the
same small number, occasionally" is an acceptable, quantified tradeoff — not
a reason to add reconciliation machinery.

### Consequences of alternative configurations (why each was rejected or deferred)

| Alternative | What actually happens | Verdict |
|---|---|---|
| `MemoryMin` directly on `dev-background.slice` | Surplus redistributes to every existing gate/test-runner container using memory, not just the intended one | Rejected — see above |
| `dev.slice` given a "generous" `MemoryMin` (more than the guaranteed slice's own ceiling) | Surplus leaks to `dev-interactive.slice`/`dev-background.slice`, unbounded by how generous the headroom was | Rejected — pin the two values exactly equal instead |
| Skip `dev.slice`, set `MemoryMin` only on `dev-memory_min_guaranteed.slice` | Ancestor-chain rule caps effective protection at what `dev.slice` (0 today) hands down — the leaf's `MemoryMin` is silently inert, `systemctl show` reports the value, nothing is actually protected | Rejected — both levels are required, not either/or |
| Disable `memory_recursiveprot` host-wide to force stricter, non-cascading semantics | It's a mount flag on `/sys/fs/cgroup`, not scoped to one hierarchy — `dev-interactive.slice`'s own `MemoryLow` and the game server's `soulmask_tmpfs.slice` hierarchy both already depend on it being enabled; turning it off to fix one new leaf slice breaks two unrelated, already-working protections | Rejected — see §5 above, `mdt-host-check.sh` already FAILs when this flag is missing, for good reason |
| Dynamic per-start/stop reconciliation (Option 1 above) | Works, but needs new host-systemd privilege for the consumer (ciu) and a periodic-GC-pass class of bug for out-of-band stops | Deferred — revisit only if the static ceiling proves too limiting in practice; tracked as ciu backlog `CIU-94` |
| A race between two concurrent admission checks against the shared ceiling (two custom-managed containers starting at once, no cross-root lock) | `memory.min` overcommit degrades gracefully — the kernel's own proportional formula divides an oversubscribed claim across more claimants than intended, so each gets a smaller-than-requested floor, never a hard failure, crash, or OOM | Accepted as a known, low-stakes limitation — not worth a cross-ciu-root lock (operator decision, 2026-09-03) |

### Sketch of the new slice (documentation only — not rendered/installed)

Following `dev-background.slice.in`'s own pattern (a dash-nested child of
`dev.slice`, no explicit `[Slice] Parent=` needed):

```ini
# units/dev-memory_min_guaranteed.slice.in (PROPOSED, not yet added)
[Unit]
Description=Individually-governed containers with a real memory.min floor (sql-mutation-gate, similar continuously-processing workloads)
Before=slices.target

[Slice]
# The ONE number that must always equal dev.slice's own MemoryMin exactly —
# see "the one invariant that actually matters" in CGROUP-NOTES.md. This is
# the total ever guaranteed across every container placed here combined;
# admission control (ciu, CIU-94) refuses to start a container whose own
# declared claim would push the live sum past this ceiling.
MemoryMin=@DEV_MEMORY_MIN_GUARANTEED_CEILING@

# No MemoryHigh/Max here deliberately -- those stay per-container (ciu's
# existing mem_limit/mem_reservation injection already does this on the
# container's own scope, no slice-level ceiling needed for them, unlike the
# min floor which requires ancestor cooperation).
```

Plus the matching addition to `dev.slice.in`:

```ini
# ADDED to units/dev.slice.in (PROPOSED):
MemoryMin=@DEV_MEMORY_MIN_GUARANTEED_CEILING@  # same value/env var as above, pinned equal, always
```

Both values must be rendered from the **same** `host-setup.env` variable
(`DEV_MEMORY_MIN_GUARANTEED_CEILING`) rather than two independently-set
variables that happen to start out equal — two variables invites exactly the
drift this design depends on not happening.

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
