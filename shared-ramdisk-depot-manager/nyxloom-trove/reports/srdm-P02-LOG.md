# srdm-P02 — LOG

Package: privileged systemd harness + the hold-unit charging probe
Handoff: `../handoffs/srdm-P02-privileged-harness.md`
Date: 2026-08-03

---

## The headline: the probe went the other way

The master plan specifies one transient unit per class, `Type=oneshot` with
`RemainAfterExit=yes`, so that "populate and hold are the *same* unit
precisely so the charge and the policy can never separate". It named the
fallback in advance and made the call an empirical one:

> if any systemd version fails to keep an active-but-empty service's cgroup
> alive, the fallback is an explicit minimal hold process — **the oracle
> decides**, the spec allows both.

**The oracle decided against the sketched shape.** On systemd 257 (Debian
trixie) the oneshot unit behaves as advertised at the *unit* level and not at
the *cgroup* level:

| | oneshot + RemainAfterExit | Type=exec, worker parks |
|---|---|---|
| `ActiveState` / `SubState` | `active` / `exited` | `active` / `running` |
| `MemoryMin` accepted by systemd | yes (`33554432`) | yes |
| `ControlGroup` | **empty** | `/system.slice/<unit>` |
| cgroup directory | **gone** | present |
| `MemoryCurrent` | **`[not set]`** | `67465216` |
| the 64 MiB of content | **reparented to `system.slice`** | `shmem 67108864` |
| `memory.min` in the cgroup | n/a — no cgroup | `33554432` |
| `memory.zswap.max` in the cgroup | n/a | `0` |

systemd reaps the cgroup as soon as the last process exits, and the content
reparents to the parent slice where the class floor does not apply. The
floor would be **arithmetically dead** — the same failure the single-token
slice name exists to prevent, arriving by a different route.

Recorded as **D-011**, with the consequences P04 has to carry: the worker
parks instead of exiting, a healthy hold unit's `SubState` is `running` not
`exited`, and the parked worker's own footprint is charged to the class
cgroup alongside the content.

**Both halves are pinned by oracles.** The negative has its own test, so a
future systemd that *does* keep the cgroup fails it — which is good news and
means re-opening D-011 to drop the parked worker, not editing the test.

## What was built

| | |
|---|---|
| `internal/cgroupfs` | cgroup v2 attribute reads: `memory.current`, `memory.stat`/`shmem`, `cgroup.stat`/`nr_dying_descendants`, existence. Root injectable, so every parser is unit-tested against fixtures. |
| `internal/systemdx` | transient unit start/inspect/stop via the systemd CLI. Runner injected, so argv construction and output parsing are unit-tested with no systemd. |
| `gate/Dockerfile` `e2e` | systemd as PID 1, following the recipe already proven on this host. |
| `tools/gate.sh` `e2e` | boot detached → wait for systemd → exec the suite → read the verdict from a file in a separate step. |

`shmem`, not `memory.current`, is what the charging assertions read: tmpfs
pages are anonymous-backed shmem, so it isolates generation content from the
slab, page tables and socket buffers that `memory.current` also counts.

Likewise the policy oracle reads the **cgroup files**, not `systemctl show`.
Show reports what was *requested*; `memory.min` reports what the kernel
*applied*. A silently dropped property reads exactly like an applied one
through show, which is the failure that would matter.

## Two oracles that were wrong before they were right

Both were caught by the harness rather than by review, which is the argument
for building it first.

**`nr_dying_descendants` climbing by one is not a leak.** The first version
asserted the count never increments across a teardown. It does: removing a
cgroup that still carries *any* charge — the parked worker's own anon pages
and page tables, not just content — creates a transient dying cgroup that
drains shortly after. "Stable" in acceptance oracle 12 means **does not
accumulate**, which is a different claim. The oracle now runs three
publish/teardown cycles and asserts the count returns to baseline; a real
leak grows it once per cycle and never gives it back. Measured: returns to
baseline after three cycles.

**An open file descriptor is not the teardown hazard.** The first version of
O8 modelled the still-held consumer with an open fd. An open file makes the
mount *busy*, so the unmount fails outright with `EBUSY` rather than
succeeding and leaving a ghost. The real mechanism is a **second mount** of
the same superblock: two mounts, one superblock, and only the last unmount
frees the pages. Measured: `shmem` stayed at exactly 67108864 across the
host-side unmount and went to 0 when the consumer's bind went away.

That is **D-012**, and it is not merely a test fix — it is the measurement
behind the design's choice to resolve holders through `/proc/*/mountinfo`
rather than by scanning for open descriptors, and it explains why the
2026-07-31 production symptom was a *ghost mount* rather than a failed
`umount`.

## Decisions filed

`D-009` systemd is driven through its CLI, not D-Bus (keeps the module
dependency-free; the runner is injected so a D-Bus swap is one file) ·
`D-010` the harness runs `--cgroupns=private`, not `host` ·
`D-011` populate-and-hold needs a parked worker · `D-012` the teardown
hazard is a surviving mount, not an open fd. **`D-004` closed** — the
`privileged-e2e` gate now has four oracles that can fail.

## Gaps

- **`--cgroupns=private` means the probe measures a container's own cgroup
  tree, not the host's.** The charging semantics are the kernel's and do not
  change, but a host-specific interaction (another tier's pressure, the
  real `srdm.slice`) is out of this harness's reach by construction.
- **`memory.zswap.max` was verified as applied, not as effective.** Whether
  pak pages actually bypass zswap under real pressure needs a memory-pressure
  oracle, which is not this package.
- **D-008 is now buildable but not built.** The harness runs privileged, so a
  `dm-flakey` power-cut oracle for `COMPLETE`-means-durable is possible. It
  is its own package, not a rider on this one.

## Verification

```
tools/gate.sh . unit    → gofmt, build, vet, all oracles green
tools/gate.sh . e2e     → 4 privileged oracles green (systemd 257, degraded state expected)
tools/canary-run.sh     → 8 canaries rejected, 0 survived
```

## Scope

`shared-ramdisk-depot-manager/**` only. `wings-cgroups/v1-legacy/test/e2e-systemd/`
was **read** for its proven container recipe and never written.
