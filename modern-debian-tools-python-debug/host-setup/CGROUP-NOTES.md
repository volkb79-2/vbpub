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
why `interactive.slice`/`besteffort.slice` carry as much as they possibly can.

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
(derives `BE_IO_CAP_PCT`% / `BENCH_IO_CAP_PCT`% and applies via
`systemctl set-property --runtime`). The unit files keep deliberately **tight**
static caps as the boot-window fallback: between boot and the first sweep, and
forever on a host where nobody ran the benchmark, those statics are the
operative values.

**Why the caps sit at 60–80% and never 100%:** a device driven to saturation
queues everything behind the burst, which is precisely the stall the tiering
exists to prevent — the IDE (or a production tier on a shared host) must never
wait behind a build storm. Below ~60% you have stopped bounding a burst and
started throttling ordinary work. The tier cap takes the low end (it bounds
10–15 containers together), the per-container cap the high end (it bounds one).
Where both apply, cgroup limits nest and the effective cap is the stricter.

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

This is exactly why the shipped tiers are `interactive.slice IOWeight=100` vs
`besteffort.slice IOWeight=10` — a true 10:1 — rather than the 1000-vs-100 that
reads more emphatically and would actually deliver 1.81:1. Raising the
interactive weight to "make it stronger" makes it *weaker* relative to intent.

CPU has no equivalent trap: `CPUWeight` ratios are exact.

### 2. Verify on `io.bfq.weight`, never `io.weight`

Under BFQ the `io.weight` file is the *input* systemd wrote, not the value in
force. Reading it back tells you nothing about scheduling:

```bash
cat /sys/fs/cgroup/interactive.slice/io.bfq.weight   # what BFQ actually uses
cat /sys/fs/cgroup/besteffort.slice/io.bfq.weight
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

### 5. Weights only decide contention; caps bound absolutely

A weight does nothing on an idle device — it only settles who yields when two
cgroups queue against the same device at once. That is why the tiers carry
both: the weights sort out interactive-vs-besteffort under contention, while
`besteffort.slice`'s `io.max` bounds the tier **absolutely**, so a build storm
cannot saturate the disk even when nothing else is currently asking for it (the
next latency-sensitive burst must not have to queue behind it).

---

## Verification cheat sheet

```bash
mdt-host-check.sh                                    # everything below, with verdicts

grep cgroup2 /proc/mounts                            # memory_recursiveprot present?
systemctl show interactive.slice -p FragmentPath     # unit file exists (not transient)?
cat /sys/fs/cgroup/besteffort.slice/io.max           # caps in force (statics or measured?)
cat /sys/fs/cgroup/interactive.slice/io.bfq.weight   # NOT io.weight
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
p=/sys/fs/cgroup/interactive.slice
while [ "$p" != /sys/fs/cgroup ]; do
    printf '%-52s %s\n' "$p" "$(cat "$p/memory.zswap.writeback" 2>/dev/null)"
    p=$(dirname "$p")
done
printf '%-52s %s\n' /sys/fs/cgroup "$(cat /sys/fs/cgroup/memory.zswap.writeback)"

# systemd's own view for a unit (needs systemd >= 256).
systemctl show interactive.slice -p MemoryZSwapWriteback

# Global pool state — writeback itself has no global switch in cgroup v2,
# it is per-cgroup only.
grep . /sys/module/zswap/parameters/* 2>/dev/null
```

### Toggle

```bash
# Dev tiers — use the supported knob, not a raw write:
#   /etc/mdt/host-setup.env :  INTERACTIVE_ZSWAP_WRITEBACK=yes|no
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
