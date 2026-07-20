# Wings cgroups rollout — prod node runbook (patch 0004 / cgroup.2)

Scope: switch the prod node from the interim state (patched `cgroup.1` wings
running, no per-server slices yet) to the final architecture: **automatic
per-server transient slices managed by Wings itself**
(`docker.per_server_slices`, patch 0004), and retire the legacy
watcher/`system.slice` scheme. Companion docs: `../../wings-cgroups/SETUP.md`
(the node-agnostic setup guide this runbook instantiates),
`../../wings-cgroups/STRATEGY.md` (design),
`../../wings-cgroups/patchstack/README.md` (build), `SOULMASK-BACKUP.md` (the
backup taken before this window).

## DONE — rollout complete (verified on the host 2026-07-17 ~12:30 local)

`cgroup.3` + panel restart landed it. Wings logged `cgroups: ensured per-server
slice` and every property is live on
`wings.slice/wings-b87c0a5b23874a1c8863ff23e6800a1d.slice`:

```
memory.min 6442450944   memory.low 12884901888   memory.high 7516192768
memory.max 21474836480  cpu.weight 1000          io.weight 1000 (io.bfq.weight 181)
```

Three follow-ups are open, none urgent — see "Post-rollout findings" below.
The step table is kept as the record of what was done.

| Step | State |
|---|---|
| §0 cgroup2 mount flags | ✅ `nsdelegate,memory_recursiveprot` present |
| §1 compose: image + D-Bus | ✅ `wings-local:1.13.1-cgroup.3`, `/run/dbus/system_bus_socket` mounted |
| §2 `config.yml` per-server slices | ✅ fixed 11:08 — `enabled: true`, `memory_min_budget: 8G`, `defaults.cpu_weight/io_weight: 200` (**intended**: every future server on this node starts at 2× the default CPU share of its siblings inside the tier, and carries no memory floor) |
| §1b D-Bus **reachability** | ✅ fixed by `cgroup.3` (the distroless image has no `/var/run`, where the D-Bus library looked; 0004 now dials `/run/dbus/...` itself) |
| §3 panel egg + variables | ✅ all 7 set (min 6G / low 12G / high 7G / max 20G / cpu 1000 / io 1000, `WINGS_CGROUP_PARENT` empty) |
| §4 legacy retirement | ✅ watcher + `gstammtisch-cgroups.service` gone, `system.slice MemoryMin=0`, `soulmask.slice` 1G kept |
| §5 cutover | ✅ container recreated into `wings-b87c0a5b23874a1c8863ff23e6800a1d.slice` |
| §6 verify | ✅ `ensured per-server slice` logged; all six properties effective in cgroupfs |

Trap worth remembering: `systemd-cgls` showing the derived slice proves only
**placement**. When Wings cannot reach systemd it fails open, and systemd
auto-creates the named slice implicitly as an empty unit — the tree looks
perfect and carries nothing. `Transient=yes` is the discriminator: Wings-created
slices are transient, systemd's implicit ones are not.

## Post-rollout findings (2026-07-17)

### 1. The IO weight is doing ~1/3 of what it looks like (worth fixing)

`WINGS_CG_IO_WEIGHT=1000` produces `io.bfq.weight=181`, not 10× anything. BFQ —
the active scheduler on `vda` — schedules on `io.bfq.weight` (1..1000) and
ignores `io.weight`; systemd derives the former from the latter, compressing
every ratio above 100 by ~11×. The old watcher scheme's 4950 → 540 (5.4×), so
this migration quietly gave away most of the game's IO priority. Same trap one
level up: `wings.slice` carries `IOWeight=500` → **bfq 136**, i.e. 1.36× against
the rest of the host, while its `CPUWeight=800` is a real 8×. Full explanation:
`../../wings-cgroups/CGROUP-SEMANTICS.md` Rule 7.

Fix (needs `cgroup.4`, which adds `WINGS_CG_IO_BFQ_WEIGHT` — state the BFQ
weight directly instead of reverse-engineering it):

```bash
# a) tier: give IO the same 8x priority CPU already has. Edit
#    /etc/systemd/system/wings.slice:  IOWeight=500  ->  IOWeight=7800   (= bfq 800)
systemctl daemon-reload      # no restart of anything needed
cat /sys/fs/cgroup/wings.slice/io.bfq.weight        # expect: default 800

# b) image: cgroup.3 -> cgroup.4 in the compose file, then
docker compose -f /root/ptero-wings-patched-cgroups/docker-compose.yml up -d --force-recreate wings
```

**c) panel — the egg has to be re-imported first.** `WINGS_CG_IO_BFQ_WEIGHT` is a
new egg variable (added 2026-07-17); until the egg knows it, there is no field to
put 540 into.

1. Admin → Nests → the Soulmask egg → *Update from file* →
   `vbpub/game_stuff/soulmask/egg-soulmask-rcon-ksm-cgroups.json` (now 8
   variables; the new one defaults to empty, so the import alone changes
   nothing). Update-in-place keeps the running server's egg association.
2. Admin → Servers → Soulmask → Startup: **clear `WINGS_CG_IO_WEIGHT`**, set
   `WINGS_CG_IO_BFQ_WEIGHT` = `540` (legacy parity — the old watcher's 4950).
   They are mutually exclusive: set both and Wings applies **neither**, and says
   so in the log.
3. Panel **Stop → Start** (placement and properties are create-time), then:

```bash
cat /sys/fs/cgroup/wings.slice/wings-b87c0a5b23874a1c8863ff23e6800a1d.slice/io.bfq.weight
# expect: default 540   (the wings log also now prints IOWeight=5940(io.bfq.weight=540))
```

Worth a second look while you are at it: `defaults.io_weight: 200` in
`config.yml` is **bfq 109** on this node — a 1.09× edge that reads like 2×. The
honest equivalent of the intended 2× is `defaults.io_bfq_weight: 200`, which
cgroup.4 accepts in the same `defaults:` block. **Replace the line, do not add
it:** in `defaults:` the two conflict *fatally* — `ValidatePerServerSlices()`
rejects the config and Wings will not boot. (Per-server, the same conflict is
only a logged skip; node config is validated strictly at startup, so a mistake
there is loud instead of silent.)

### 2. `memory.high=7G` is squeezing the game (decide, don't rush)

The floors work, and `high=7G` is now genuinely enforced — `memory.min=6G` does
not protect a cgroup from its *own* `high` (`CGROUP-SEMANTICS.md` Rule 3). Six
minutes after start: `memory.current` 6.94G pinned under the 7G ceiling, **8370
high-reclaim events**, 3.4G pushed into zswap (compressed to 1.2G) and ~3.7G out
to the swap device. Pressure has since settled (`avg10=0.00`, `avg300=3.13`), so
this is not thrashing — it shed cold startup memory and stabilised, which is
what the knob is for. But if the game stutters, `high` is the cause, and the
tier has room: `wings.slice` allows 14G and the game is the only server. Raising
`WINGS_CG_MEMORY_HIGH` toward 9–10G is the lever.

### 3. The derived slice is `Transient=no` (cosmetic, self-healing)

Wings adopted the empty slice systemd had auto-created during the broken window
(`SetUnitProperties` on the existing unit) rather than creating its own
transient one, so the properties live in
`/run/systemd/system.control/wings-b87c….slice.d/*.conf`. They are real
drop-ins: reload-safe, and gone at reboot — after which Wings will create the
slice properly as transient. The only consequence meanwhile is that 0004's
orphan GC skips it (the GC guard is `Transient=yes`, deliberately, so it never
touches admin-owned slices). Nothing to do.

## State assumed at start (verified 2026-07-17)

- Wings `wings-local:1.13.1-cgroup.1` running from
  `/root/ptero-wings-patched-cgroups/docker-compose.yml`, container under
  `wings.slice/wings-mgmt.slice` (T0a live).
- Unit files installed and active: `wings.slice` (MemoryMin=8G, Low=12G,
  High=14G, CPUWeight=800, IOWeight=500), `wings-mgmt.slice` (High=1G,
  Max=1536M).
- `/etc/pterodactyl/config.yml` has `cgroup_parent: ""` (unset).
- Soulmask server `b87c0a5b-…` stopped, world backed up.
- Legacy still present: `soulmask-cgroup-watcher.service` disabled **but
  running**; `gstammtisch-cgroups.service` enabled+active (re-runs
  `setup-cgroups.sh` at boot); persistent drop-ins
  `/etc/systemd/system.control/system.slice.d/50-MemoryMin.conf` (7G — must go)
  and `…/soulmask.slice.d/50-MemoryMin.conf` (1G pak chain — **keep**).
- Image `wings-local:1.13.1-cgroup.2` already on the node's daemon (built from
  the 4-patch series; unit+integration+systemd-e2e all green).

## Design mapping for this node

Old scheme → new scheme, same arithmetic:

| Old (watcher/setup-cgroups.sh) | New |
|---|---|
| game floors on `docker-<id>.scope` under `system.slice` (min=6G low=12G high=7G cpu 800 io 4950) | same values on `wings-<uuid32>.slice`, set per-server via egg variables |
| `system.slice` MemoryMin = Σ floors + 1G = 7G | `wings.slice` MemoryMin = 8G (unit file, already live) + `memory_min_budget: 8G` |
| watcher reconcile loop | Wings ensures the slice on every server start (containers are recreated every start) |

**Memory** defaults (`per_server_slices.defaults.memory_*`) stay **empty** on
this node: it is mixed-use, and two other panel servers exist in `Created`
state — a default floor would hand every future test server a 6G reservation and
trigger budget clamping. Floors travel as per-server variables on the Soulmask
server only; other servers get bare derived slices (clean accounting, no
reservations), which is exactly right.

**Weight** defaults are set (`cpu_weight: 200`, `io_weight: 200`) and that is
deliberate: weights reserve nothing, so a default costs nothing on an idle node
and only decides who yields under contention. As deployed it means every future
server starts at 2× the CPU share of a default sibling *inside the tier*. Note
`io_weight: 200` does not do the matching thing on this BFQ node — it is bfq 109,
a 1.09× edge (finding 1); `io_bfq_weight: 200` is the honest equivalent.

## 0. Preflight: cgroup2 mount flags (found missing 2026-07-17)

Everything below places floors on **slices** while the game's pages are
charged to the `docker-*.scope` **below** the slice. That indirection only
works when cgroup2 is mounted with `memory_recursiveprot` — without it, every
slice-level `MemoryMin`/`MemoryLow` on this node (wings, soulmask, interactive)
silently protects **nothing**. systemd ≥ 248 mounts the flag by default at
boot, but a runtime remount from the init cgroup namespace can strip it — and
on this node something did exactly that (verified 2026-07-17: neither
`nsdelegate` nor `memory_recursiveprot` present).

Run **on a host root shell** (not through a container — the kernel silently
ignores these flag changes from any non-init cgroup namespace):

```bash
grep cgroup2 /proc/mounts        # if nsdelegate,memory_recursiveprot present: done
mount -o remount,nsdelegate,memory_recursiveprot /sys/fs/cgroup
grep cgroup2 /proc/mounts        # verify both flags now listed
```

A reboot restores the flags anyway (systemd default); the mdt host-setup
companion's `mdt-apply-dev-caps.sh` re-checks periodically
(`CGROUP2_FLAGS=warn|fix`) and `mdt-host-check.sh` FAILs when it's missing.

## 1. Compose: image + D-Bus socket

Edit `/root/ptero-wings-patched-cgroups/docker-compose.yml`, wings service:

```yaml
    image: wings-local:1.13.1-cgroup.3     # was cgroup.2 — see below
    volumes:
      # …existing mounts unchanged…
      - "/run/dbus/system_bus_socket:/run/dbus/system_bus_socket"   # NEW: slice mgmt via systemd D-Bus
```

**Two ways to fix the D-Bus reachability blocker — pick one:**

| | Change | Gets you |
|---|---|---|
| **A. Deploy `cgroup.3`** (recommended) | bump the `image:` line only; **keep the D-Bus mount — it is the mechanism, not the workaround** | The dial fix in code, plus `budget_policy: distribute` (needed for the planned second Soulmask server) and the config-parse warnings that would have caught the 11:41 indentation loss. Image already built on this daemon. |
| **B. Keep `cgroup.2`** | add `environment: DBUS_SYSTEM_BUS_ADDRESS: "unix:path=/run/dbus/system_bus_socket"` | Only the D-Bus fix. Smallest possible change if you want to move one variable at a time. |

Both need `docker compose up -d --force-recreate wings` and then a panel
Stop/Start of the server (placement and properties are applied at container
create). Rollback for A is the `image:` line back to `cgroup.2`.

Wings connects to the system bus first and falls back to
`/run/systemd/private`; mounting the system-bus socket is the standard,
least-privilege choice. Without the mount nothing breaks — Wings logs
`could not ensure per-server slice; …placement only` and containers still
start (fail-open by design) — but floors would not be applied.

### Optional: separate config file (`config.wings-cgroups.yml`)

The image is `ENTRYPOINT ["wings"]` + `CMD ["--config", "/etc/pterodactyl/config.yml"]`,
so a compose override selects another file — there is no env var for this:

```yaml
    command: ["--config", "/etc/pterodactyl/config.wings-cgroups.yml"]
```

Caveats: the file must be a **complete** wings config (token, uuid, …), not a
delta — wings does not merge configs; and wings **rewrites its config file in
place** (schema updates, token rotation), so the alternate file will be
mutated too and can drift from `config.yml`. Recommendation stays: one
canonical `config.yml`; the block below is small.

## 2. config.yml: enable per-server slices

Merge into the existing `docker:` section of `/etc/pterodactyl/config.yml`
(do not create a second `docker:` key; wings validates all of this at startup
and fails loudly on typos):

```yaml
docker:
  cgroup_parent: wings.slice
  per_server_slices:
    enabled: true
    memory_min_budget: 8G      # = wings.slice MemoryMin (unit file)
    budget_policy: clamp
    # defaults: {}             # deliberately empty on this mixed-use node
```

`cgroup_parent: wings.slice` is already present and correct — the two keys to
change are `enabled` (`false` → `true`) and `memory_min_budget` (`""` → `8G`),
both **inside the existing `per_server_slices:` block** that wings already wrote
into the file. Do not add a second `docker:` key and do not re-indent the block.

> **Gotcha that bit us at 10:41 (why this step failed the first time).** Wings
> parses `config.yml` non-strictly and then **rewrites the whole file at boot**
> from what it parsed. A key at the wrong indent level is silently dropped — no
> error — and the rewrite erases the evidence, so the file looks untouched. The
> rewrite is therefore also the check: after restarting wings, read the block
> back. What is on disk is exactly what wings understood.

Then restart wings and verify the parse before anything else:

```bash
cd /root/ptero-wings-patched-cgroups
docker compose up -d --force-recreate wings
grep -A3 per_server_slices /etc/pterodactyl/config.yml   # MUST show enabled: true
docker compose logs --tail 30 wings      # config validation runs at boot;
                                         # also: "garbage-collected orphaned per-server slice" lines, if any
```

If it still reads `enabled: false`, the edit did not land where wings looks —
fix the indentation, do not restart-and-hope. (A panel-side node config save also
rewrites this file from wings' in-memory state; edit → restart immediately, or
set top-level `ignore_panel_config_updates: true`.)

## 3. Panel data: egg + per-server floors

1. Import `vbpub/game_stuff/soulmask/egg-soulmask-rcon-ksm-cgroups.json` over
   the existing egg (egg page → update from file), or add the admin-only
   variables manually. All have empty defaults — importing changes no behavior
   by itself. (The file carried 7 variables at rollout; it now has **8** —
   `WINGS_CG_IO_BFQ_WEIGHT` was added later the same day, see finding 1 below.
   Re-import to pick it up.)
2. Admin → Servers → Soulmask → Startup. **Done — verified set on the host
   2026-07-17:**
   - `WINGS_CG_MEMORY_MIN`  = `6G`
   - `WINGS_CG_MEMORY_LOW`  = `12G`
   - `WINGS_CG_MEMORY_HIGH` = `7G`
   - `WINGS_CG_MEMORY_MAX`  = `20G` — **inert** (> 15.6Gi RAM); harmless, could
     be cleared
   - `WINGS_CG_CPU_WEIGHT`  = `1000` — inside the tier only: 1000 vs
     `wings-mgmt.slice`'s 200 = 83% of whatever `wings.slice` wins at the root
     (where its CPUWeight=800 of Σ1720 ≈ 47% of the host). CPU ratios are exact.
   - `WINGS_CG_IO_WEIGHT`   = `1000` — **this is not 10:1, it is 1.8:1.**
     systemd rescales `IOWeight` into BFQ's 1..1000 range, and BFQ is what
     actually schedules here: 1000 → `io.bfq.weight` **181**, while
     `wings-mgmt.slice`'s 200 → **109** (measured on this host). The legacy
     scheme's 4950 → **540**, i.e. 5.4× against a default sibling — that
     asymmetry was deliberate and dropping it to 1000 gave most of it away. If
     you want the old IO priority back, use `4500` (→ bfq 500 = a true 5:1).
     Verify with
     `cat /sys/fs/cgroup/wings.slice/wings-<hex>.slice/io.bfq.weight`, never
     `io.weight`. Full mapping table: `../../wings-cgroups/CGROUP-SEMANTICS.md`
     Rule 7.
   - `WINGS_CGROUP_PARENT`  = **leave empty** — 0004 derives
     `wings-b87c0a5b23874a1c8863ff23e6800a1d.slice` automatically (full
     dashless UUID; the short `wings-b87c0a5b.slice` convention from the T2
     PoC is obsolete and was never created on this node).

## 4. Retire the legacy scheme (before starting the game)

```bash
# 4.1 the watcher: disabled but still running
systemctl stop soulmask-cgroup-watcher.service

# 4.2 the boot-time re-apply unit
systemctl disable --now gstammtisch-cgroups.service

# 4.3 the persistent system.slice floor (survives reboot as a drop-in)
ls /etc/systemd/system.control/system.slice.d/    # expect only 50-MemoryMin.conf
systemctl revert system.slice
systemctl show system.slice -p MemoryMin          # expect MemoryMin=0

# 4.4 KEEP the pak chain: soulmask.slice 1G drop-in, pak-ramdisk + graceful-stop
#     services stay untouched.

# 4.5 leftover limit-less transients from old smoke tests, if still loaded
systemctl stop wings-smoke.slice wingscg.slice wingscg-smoke.slice 2>/dev/null || true
```

**Caveat on 4.2:** `setup-cgroups.sh` used to be multi-purpose — besides the
game/`system.slice` floors this step replaces, it also applied the besteffort IO
caps, IO weights on other containers, and the pak-slice zswap knob. Disabling
the unit would have silently lost those at the next reboot. Both other halves
now have real owners, so disabling it is safe:

| Former responsibility | Owner now |
|---|---|
| game floors, `system.slice` MemoryMin | Wings per-server slices (this rollout) |
| besteffort measured IO caps, per-container bench/buildkit/devcontainer caps, interactive zswap policy, the fio baseline | **mdt host-setup companion** — `modern-debian-tools-python-debug/host-setup/` (`install.sh` + `mdt-host-slices.service`/`.timer`). Install it in the same window: `sudo host-setup/install.sh --with-baseline`, then `mdt-host-check.sh` |
| pak-slice zswap bypass (`memory.zswap.max=0`) | still `setup-cgroups.sh`, and also declared in the `soulmask-paks.slice` unit itself (`MemoryZSwapMax=0`) — verify with `systemctl show soulmask-paks.slice -p MemoryZSwapMax` |

`setup-cgroups.sh` in the repo is now **game-side only** (the dev logic was
removed, not just superseded) — a host still running the pre-migration copy has
two writers for `besteffort.slice`'s `io.max`, so re-run `scripts/install.sh`
to update it.

Floor ledger after retirement: `wings.slice` 8G + `soulmask.slice` 1G = 9G of
15.6Gi; everything else unfloored, as designed.

## 5. Cut over the game server

Panel **Stop → Start** is sufficient and is what you want here: wings recreates
the container on start (confirmed on this host — the 10:48 Start produced a
container `Created` at 10:48). `docker rm <uuid>` while stopped is the explicit
equivalent; bind-mounted world data is untouched either way and the backup
exists.

(Optional dry-run first: start one of the never-started test servers —
`31e20408…` — with no variables set and confirm it lands in a bare derived
slice before touching Soulmask.)

## 6. Verify

```bash
UUID=b87c0a5b-2387-4a1c-8863-ff23e6800a1d
SLICE=wings-b87c0a5b23874a1c8863ff23e6800a1d.slice

docker inspect -f '{{.HostConfig.CgroupParent}}' $UUID          # -> $SLICE
cat /proc/$(docker inspect -f '{{.State.Pid}}' $UUID)/cgroup    # 0::/wings.slice/$SLICE/docker-<id>.scope

cat /sys/fs/cgroup/wings.slice/$SLICE/memory.min    # 6442450944
cat /sys/fs/cgroup/wings.slice/$SLICE/memory.high   # 7516192768
cat /sys/fs/cgroup/wings.slice/$SLICE/cpu.weight    # 1000
cat /sys/fs/cgroup/wings.slice/memory.min           # 8589934592 (parent, unit file)

systemctl show $SLICE -p Transient -p MemoryMin     # Transient=yes, MemoryMin=6442450944
systemctl daemon-reload && cat /sys/fs/cgroup/wings.slice/$SLICE/memory.min   # unchanged

docker compose -f /root/ptero-wings-patched-cgroups/docker-compose.yml logs wings \
  | grep "ensured per-server slice"                 # slice + properties logged at create
```

Lifecycle checks worth doing once: panel Restart (slice re-ensured, properties
survive), server delete on a throwaway server (slice stopped/gone), wings
restart (boot log shows orphan GC only for slices with no matching server).

## Rollback

- Per-server slices only: `per_server_slices.enabled: false` in config.yml,
  restart wings, recreate the game container → node-wide `wings.slice`
  placement (T1 behavior), floors then live only at tier level.
- Full: compose image back to `ghcr.io/pterodactyl/wings:latest` +
  force-recreate; stock wings ignores the config block and all egg variables.
  Already-placed containers keep their slice until next recreation. Re-enable
  `gstammtisch-cgroups.service` + watcher only if you also want the old
  `system.slice` floor scheme back.
