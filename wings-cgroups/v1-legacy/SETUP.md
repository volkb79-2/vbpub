# SETUP — deploying the patched Wings (patches 0001–0011) on a node

The one-stop, node-agnostic deployment guide: host prerequisites → compose →
`config.yml` → panel data → cutover → verification → rollback, in that order.
The tier folders (`t0-*`, `t1-*`, `t2-*`) explain each mechanism in isolation and
stay the reference for *why*; this is the *how* for the normal case — a node
running the full patch series with automatic per-server slices (0004).

Prerequisite: the image exists on the node's Docker daemon
(`wings-local:<ver>-cgroup.N`) — see [`BUILD-AND-INSTALL.md`](BUILD-AND-INSTALL.md).
A worked host-specific instance of this guide, including retirement of a legacy
watcher scheme, is `../scripts/gstammtisch-guide/WINGS-CGROUPS-ROLLOUT.md`.

## 1. Host prerequisites

### 1a. The node slice unit (placement anchor — required)

Everything nests under one named slice per node. Install it BEFORE enabling
anything in Wings — a missing or typo'd unit degrades silently into a limit-less
transient slice (the path looks right; the guarantees are absent):

```bash
cp t1-node-cgroup-parent/wings.slice /etc/systemd/system/   # tune values first
systemctl daemon-reload && systemctl enable --now wings.slice

# Mandatory pre-flight:
systemctl show wings.slice -p FragmentPath -p MemoryMin -p MemoryLow -p MemoryHigh
# FragmentPath MUST point at your unit file; values MUST match your plan.
```

Sizing rule (Finding A): `wings.slice` `MemoryMin` must be ≥ the sum of every
per-server floor you intend to grant — a child floor beyond the parent's is
silently dead. Patch 0004 enforces this arithmetic at runtime via
`memory_min_budget` (§3), but the unit file is where the tier's total reservation
is declared. Optional but recommended alongside it:
`t0-host-baseline/t0a-wings-self/wings-mgmt.slice`, which caps the Wings daemon
itself (compose `cgroup_parent: wings-mgmt.slice`) so it can't eat the tier's
headroom.

### 1b. cgroup2 mount flags (kernel prerequisite — check, usually free)

Floors are set on *slices*, but the container's pages are charged to the
`docker-*.scope` *below* the slice. Protection only reaches them when cgroup2 is
mounted with `memory_recursiveprot`. systemd ≥ 248 mounts it by default at boot,
but a runtime remount from the init cgroup namespace can strip it (observed in
the wild) — and then every `MemoryMin`/`MemoryLow` on every slice on the node
protects **nothing**, with zero errors anywhere.

```bash
grep cgroup2 /proc/mounts      # must list: nsdelegate,memory_recursiveprot
# if missing — from a HOST root shell; the kernel ignores this from any
# non-init cgroup namespace, so it cannot be done through a container:
mount -o remount,nsdelegate,memory_recursiveprot /sys/fs/cgroup
```

### 1c. Optional: BFQ scheduler

`IOWeight`/`io.bfq.weight` (tier-level and the panel's per-server "Block IO
Weight" alike) are inert under `none`/`mq-deadline`; only BFQ enforces them.
`io.max`-style hard caps work on any scheduler. If you use IO weights:
`modprobe bfq` plus a udev rule selecting it — ready-made files in
`../modern-debian-tools-python-debug/host-setup/etc/`.

> **The panel's "Block IO Weight" is a different knob from the ones in §3–§4,
> and both are real.** Wings has always passed the panel value to Docker as
> `--blkio-weight`; on cgroup v2 runc writes it straight to `io.bfq.weight` on
> the container's `docker-*.scope`, on BFQ's own 10..1000 scale, uncompressed
> (measured). The variables below act one level up, on the server's *slice*.
> So the panel value settles containers against each other under one slice,
> and `WINGS_CG_IO_BFQ_WEIGHT` settles slices against each other under
> `wings.slice` — they multiply rather than override. Note the collision:
> the panel field and `defaults.io_weight` share a name but use different
> scales at different levels.

## 2. Wings compose adaptation

```yaml
services:
  wings:
    image: wings-local:1.13.1-cgroup.10        # the patched image
    cgroup_parent: wings-mgmt.slice           # optional (T0a): cap the daemon itself
    volumes:
      # …existing mounts (docker.sock, /etc/pterodactyl, /var/lib/pterodactyl, …)…
      # REQUIRED — per-server slice management (patch 0004) talks to systemd
      # over this socket. No mount, no properties: Wings falls back to
      # placement-only. This is the mechanism, at every image version.
      - "/run/dbus/system_bus_socket:/run/dbus/system_bus_socket"
```

Wings tries the system bus first and falls back to systemd's private socket
(`/run/systemd/private`, root-only, normally absent inside a container);
mounting the system-bus socket is the standard, least-privilege choice. If your
host keeps the socket somewhere else, point Wings at it explicitly:

```yaml
    environment:
      DBUS_SYSTEM_BUS_ADDRESS: "unix:path=/run/dbus/system_bus_socket"
```

> **The `/var/run` trap — why patch 0004 dials the socket itself.** The D-Bus
> client library dials a compile-time default of
> **`/var/run/dbus/system_bus_socket`**. That resolves anywhere `/var/run`
> symlinks to `/run` — but upstream Wings ships a **distroless** image with no
> `/var/run` at all, so the mount above is invisible to it and slice management
> silently degrades to placement-only. The stock go-systemd connect helper then
> misreports it: it discards the system-bus error and falls back to the private
> socket, so the log blames `/run/systemd/private`, a path you never configured.
> Patch 0004 therefore dials each candidate itself (`/run/dbus/...`, then the
> library default, then the private socket) and names every attempt in the
> error. **On an image built before `cgroup.3` this fix is absent** — there you
> must set `DBUS_SYSTEM_BUS_ADDRESS` as above, or add a second mount at
> `/var/run/dbus/system_bus_socket`.

Fail-open by design: with no reachable bus, Wings logs `could not ensure
per-server slice; … placement only` and servers still start. Containers land in
their derived slice — systemd auto-creates it as an implicit, **property-less**
unit — so the cgroup tree looks completely correct while every floor, ceiling and
weight is absent. `systemd-cgls` cannot show you this; only reading the values
can (§6).

**Optional — alternate config file.** There is no env var for this; the image is
`ENTRYPOINT ["/usr/bin/wings"]` + `CMD ["--config", "/etc/pterodactyl/config.yml"]`,
so override the CMD:

```yaml
    command: ["--config", "/etc/pterodactyl/config.wings-cgroups.yml"]
```

Caveats: the file must be a **complete** Wings config (token, uuid, …) — Wings
does not merge configs; and Wings rewrites its config file in place (§3), so the
alternate file gets mutated too and drifts from `config.yml`. Recommendation:
keep one canonical `config.yml`; the block below is small.

## 3. `config.yml` — the complete snippet

Merge into the **existing** `docker:` mapping (never add a second `docker:` key).

```yaml
docker:
  # T1 (patch 0001) — node default placement. REQUIRED whenever per_server_slices
  # is enabled: derived slices are named from it (wings.slice ->
  # wings-<uuid>.slice) and nest under it via systemd dash-naming. Wings refuses
  # to start on enabled-without-parent. Bare slice unit name only.
  cgroup_parent: wings.slice

  # T2 (patch 0002) — allow-list for per-server WINGS_CGROUP_PARENT overrides.
  # Empty = only the built-in wings.slice/wings-*.slice namespace is accepted.
  allowed_cgroup_parents: []

  # Phase 0 (patch 0011) — allow-list for per-server WINGS_CG_RAMDISK_UNITS.
  # Unlike allowed_cgroup_parents there is NO built-in fallback namespace:
  # empty means Wings may trigger NO unit this way at all. List the EXACT
  # unit name(s) an egg may ask Wings to `systemctl start` before container
  # create, e.g. the host's ramdisk-setup units:
  #   allowed_ramdisk_units: ["soulmask-pak-ramdisk.service", "soulmask-static-ramdisk.service"]
  allowed_ramdisk_units: []

  # T3b (patch 0004) — automatic per-server transient slices.
  per_server_slices:
    enabled: true
    # Node-wide defaults applied to every derived slice; per-server WINGS_CG_*
    # egg variables override field-by-field. Leave empty on mixed-use nodes
    # (a default memory_min hands EVERY server a reservation).
    defaults:
      memory_min: ""          # e.g. "512M" — sizes take G/M/K or bytes
      memory_low: ""
      memory_high: ""
      memory_max: ""
      cpu_weight: 0           # 1..10000; 0 = unset. Ratios are exact.
      # Pick ONE io weight spelling: they drive the same systemd property, and
      # setting both here is a FATAL config error (Wings refuses to boot —
      # unlike the per-server variables, where it is only a logged skip).
      io_weight: 0            # 1..10000, systemd's scale; 0 = unset. On a BFQ
                              # node this is NOT the weight that schedules —
                              # systemd compresses it (1000 -> io.bfq.weight 181).
      io_bfq_weight: 0        # 1..1000, BFQ's own scale; 0 = unset. Prefer this
                              # on BFQ nodes: the number you write is the number
                              # BFQ uses. (patch 0005)
    # Startup band (patch 0007) — applied when the slice is ensured, BEFORE the
    # container starts, then replaced by `defaults:` when the WINGS_CG_STEADY_MATCH
    # trigger fires (default: the egg's startup "done" matcher) or startup_grace
    # expires.
    #
    # Why this exists: a game's load-time peak dwarfs its steady working set,
    # and a cgroup is not protected from reclaim it inflicts on itself — exceeding
    # its own memory.high reclaims straight through its own memory.min floor. A
    # ceiling sized for the steady state therefore evicts the server through its
    # own floor while it is still loading — permanently, because nothing faults
    # those pages back except the workload. Leave empty to apply the steady band
    # from the start (behaviour before 0007).
    #
    # ENGAGEMENT: the startup band only does something when its memory_high is
    # ABOVE the steady defaults.memory_high. If they are equal, the phase change
    # and the ramp below are no-ops. Set startup memory_high high (or leave the
    # steady one low) so there is a ceiling to lift during load and lower after.
    startup_defaults:
      memory_min: ""          # e.g. "9G" — a higher floor while loading
      memory_low: ""
      memory_high: ""         # e.g. "64G" — effectively "no ceiling yet"
      memory_max: ""
    # How long the startup band may hold when the ready line never matches (a
    # broken egg, a game that changed its log format). "0" disables the timer.
    startup_grace: 15m
    # When the startup band gives way to the steady band, walk memory.high down
    # to its steady ceiling in steps of at most this size instead of dropping it
    # in one shot -- so a world's cold tail is freed progressively rather than
    # in a single squeeze. Only the ceiling is stepped, and only when the steady
    # ceiling is below current usage; the floor and everything else apply at
    # once. Empty or "0" = one-shot (the pre-ramp behaviour). 64M is a gentle
    # default; the ramp self-paces (each step waits for reclaim to catch up), so
    # this bounds per-step throttle, not the total time. NODE-WIDE ONLY — there
    # is no per-server WINGS_CG_* equivalent; it applies to every server's
    # startup->steady transition on this node.
    steady_ramp_step: 64M
    # Floor budget: Σ MemoryMin over all Wings-managed slices must stay ≤ this.
    # Set it = the wings.slice unit's MemoryMin. Empty = unlimited (not advised:
    # it also removes the overcommit log line).
    memory_min_budget: 8G
    # What to do when a floor request exceeds the remaining budget. Every other
    # property is applied unchanged in all three cases:
    #   clamp      (default) reduce the floor to the remainder, and log it.
    #              Every granted floor stays literally true, but a server that
    #              starts late is capped permanently, however busy it becomes.
    #   refuse     drop the floor entirely rather than grant a smaller one.
    #   distribute apply the floor as asked and let the kernel resolve it: on
    #              overcommit it shares the parent's protection out in
    #              proportion to each server's usage below its own floor, so
    #              busy servers keep more than idle ones and the split follows
    #              load. Individual floors stop being guarantees; the tier
    #              total still is. Right for co-operating servers.
    budget_policy: clamp
```

Choosing between them is a real decision, worked through with numbers in
[`CGROUP-SEMANTICS.md`](CGROUP-SEMANTICS.md).

### How Wings treats this file — read this before editing

Wings **rewrites `config.yml` in place at every boot**: it serializes the whole
schema back over the file, reordering keys and materializing defaults (that is
why a zero-valued `per_server_slices:` block appears even before you configure
one). Values it parsed are preserved — but anything it *didn't* parse is gone.
Three consequences:

1. **Parsing is non-strict** (plain `yaml.Unmarshal`). A key at the wrong
   indentation, or under the wrong parent, is **silently ignored** — no error, no
   warning, and the boot rewrite then erases it from the file. This is the single
   most common way this setup "doesn't work": the file looks edited to you, and
   Wings never saw it.
2. **Therefore the rewrite is your feedback loop.** After restarting Wings, read
   the block back (§6 step 1). The on-disk file after a restart is exactly what
   Wings parsed. If it still says `enabled: false`, your edit was misplaced —
   not ineffective.
3. **A panel-side node-config save silently reverts on-disk edits.** The Panel
   pushes the node configuration to `POST /api/system`; Wings merges it over its
   in-memory config and writes the whole file. Edits made on disk while Wings is
   running — and not yet loaded by a restart — are lost. Safest sequence: edit,
   then restart Wings immediately. To take Wings off the Panel's config leash
   entirely, set top-level `ignore_panel_config_updates: true`.

```bash
cd /root/<wings-compose-dir>
docker compose up -d --force-recreate wings
docker compose logs --tail 30 wings   # config validation runs at boot and fails
                                      # loudly on typos; also logs orphan-slice GC
```

## 4. Panel data (egg / server variables — all admin-only, optional)

Add these 17 admin-only variables to your egg. A complete worked example in
PTDL_v2 export format is `../game_stuff/soulmask/egg-soulmask-rcon-ksm-cgroups.json`
(import it over an existing egg to update in place — servers keep their egg
association); `t2-per-server-placement/egg-variable.snippet.json` carries just
`WINGS_CGROUP_PARENT`, for placement-only deployments. A changed value takes
effect at the next container (re)creation — panel **Stop → Start**, not restart.

> **Prerequisite:** import/patch the egg (panel → Nests → your egg → *Import*,
> over the existing egg) **before** these variables can be set on a server —
> a `WINGS_CG_*` variable that the egg does not define is not settable in the
> panel. Node-level `config.yml` `defaults:` apply without the egg, so per-server
> variables are genuinely optional; but any per-server override needs the egg
> imported first. The ramp itself (`steady_ramp_step`) is `config.yml`-only and
> has **no** egg variable.

| Variable | Meaning |
|---|---|
| `WINGS_CG_MEMORY_MIN` / `_LOW` / `_HIGH` / `_MAX` | per-server slice floors/ceilings (G/M/K or bytes) |
| `WINGS_CG_CPU_WEIGHT` | per-server CPU weight (1..10000). Ratios are honoured exactly. |
| `WINGS_CG_IO_BFQ_WEIGHT` | per-server IO weight on BFQ's scale (1..1000) — what BFQ actually schedules on, and what `io.bfq.weight` reads back. **Prefer it on BFQ nodes.** Needs patch 0005. |
| `WINGS_CG_IO_WEIGHT` | the same setting on systemd's scale (1..10000). **Compressed on BFQ nodes** — 1000 becomes `io.bfq.weight` 181. Keep for iocost/non-BFQ nodes. Mutually exclusive with `WINGS_CG_IO_BFQ_WEIGHT`: set both and neither is applied (logged). |
| `WINGS_CG_STEADY_MATCH` | the console line that ends the startup phase and applies the steady band **and** — if set — is the ONLY thing that ends it: this is now armed whether or not a startup memory band is staged (patch 0010 fixed a gap where an unstaged server silently ignored this variable and fell back to the egg's own done/running line instead). `regex:` prefix for a regular expression, anything else is a literal substring. **Empty falls back to the egg's own `startup.done` matcher** — which for a world-streaming game routinely fires *before* loading finishes, so set this explicitly if the two differ. **If you set `WINGS_CG_CHILD_SERVERS` on this server, treat this variable as load-bearing, not optional**: a child starts the moment this line appears (or, if you leave it empty, the moment the egg's own done/running line appears) — verify that line genuinely means "safe for a dependent to read/connect now", not merely "the Panel should show Running". |
| `WINGS_CG_PHASE_EVENTS` | optional, **informational only** — newline-separated `name=match` lines; the first console line matching each is recorded to the Panel activity log (`server:cgroups.phase`). Surfaces a game's long, opaque startup (steam update, world load). Drives no cgroup behaviour. |
| `WINGS_CG_STARTUP_GRACE` | per-server override of `startup_grace`; the backstop for a trigger that never fires. Go duration (`15m`, `90s`); `0` disables the timer. Empty = the node default. Armed whenever `WINGS_CG_STEADY_MATCH` is set too, even with no startup band staged — see that row. |
| `WINGS_CG_STARTUP_MEMORY_MIN` / `_LOW` / `_HIGH` / `_MAX` | the same four knobs, applied only while the server is starting and replaced by the steady values once it reports ready (or after `startup_grace`). Set `_HIGH` generously — a ceiling below the load-time peak breaches the floor and cannot be undone without a restart. Needs patch 0007. |
| `WINGS_CG_CHILD_SERVERS` | comma- or newline-separated **server UUIDs** (not egg names) on this same node to start once THIS server reaches its steady trigger (the row above) — ordinary cluster start-ordering, e.g. a server that must not read a shared bind mount until another one's write to it is confirmed done. Started via the normal Panel/API start path (`HandlePowerAction`), so a suspended child, one not managed by this node, or the server naming itself is logged and skipped, never fatal. **Never honoured on the `startup_grace` backstop** — a trigger that never fired means readiness was never confirmed. **Wings has no "do not autostart" flag**: keeping a child stopped whenever its parent is stopped is the operator's own responsibility — a child left running with its parent stopped, then started by hand, autostarts unconditionally on the next boot like any other server. A child and its configured parent that are BOTH running when the node reboots are handled safely (the child's boot-time restart is deferred to the parent's, not raced) — see `patchstack/README.md` patch 0010 and `internal/cgroups.DeferBootRestart`. Needs patch 0010. |
| `WINGS_CGROUP_PARENT` | **leave empty** — override/opt-out only. A set value beats the derived slice (and must pass the allow-list); setting it *to the node default* opts the server out of a derived slice entirely. |
| `WINGS_CG_RAMDISK_UNITS` | comma- or newline-separated **systemd unit names** (not mount paths) Wings triggers with a `systemctl start`-equivalent immediately before this server's container is created — typically a host ramdisk-setup unit shared by sibling servers, e.g. `soulmask-pak-ramdisk.service`. Wings never manages the mount itself, only asks systemd to (re)run a unit already installed on the host and waits for the job. **Every name must also appear in this node's `docker.allowed_ramdisk_units`** (config.yml) or it is rejected and logged, never acted on — unlike `WINGS_CGROUP_PARENT` there is no built-in fallback namespace, so an empty allow-list rejects everything. Never a restart: restarting an already-active oneshot would run its `ExecStop` first, which for these units means tearing down a sibling server's live bind mount. Safe to fire on every start — the units are idempotent oneshots. Fails open: an unreachable D-Bus, missing unit, or job timeout is logged and never blocks the server from starting. Needs patch 0011. |

Sizing guidance: `memory.min` = the working set that must never be reclaimed
under outside pressure; `memory.low` = soft protection above it (only meaningful
below `memory.high` — a cgroup's own `high` reclaims regardless of its own
protections); `memory.high` = where the server gets squeezed into zswap/reclaim;
`memory.max` above physical RAM is inert. Weights only settle sibling contention
*within* the node slice, and only under actual contention — a server's
`cpu_weight` is its share of the tier, never of the host, and the tier's share is
set one level up on the `wings.slice` unit.

Two traps when picking weights: `cpu_weight` ratios are honoured exactly, but
`io_weight` is rescaled by systemd into BFQ's 1..1000 range (`io.bfq.weight`),
which compresses everything above the default by ~11× — `io_weight: 1000` buys
1.8:1 against a default sibling, not 10:1. That is what `io_bfq_weight` exists
for: state what you want BFQ to do (`io_bfq_weight: 540` → a real 5.4:1) and let
Wings compute the `IOWeight` that produces it. Verify on the slice itself —
`cat /sys/fs/cgroup/…/io.bfq.weight`, never `io.weight`, which BFQ does not read.

Both traps — and the arithmetic that decides whether a floor is real, dead, or
shared — are worked through with examples in
[`CGROUP-SEMANTICS.md`](CGROUP-SEMANTICS.md) Rules 5–7. Read it before picking
numbers.

## 5. Cutover

Placement is create-time only, so each server needs one container recreation:
panel **Stop → Start** (Wings recreates the container on start; bind-mounted data
is untouched). `docker rm <uuid>` while stopped is equivalent and explicit.

Optional dry-run: start a never-started test server with no variables set and
confirm it lands in a bare derived slice before touching a live game.

## 6. Verify

```bash
# 1. Did Wings actually parse the config? (the rewrite is the source of truth)
grep -A3 per_server_slices /etc/pterodactyl/config.yml     # enabled: true, budget set
grep cgroup_parent /etc/pterodactyl/config.yml             # wings.slice

# 2. Placement + effective values
UUID=<server-uuid>; SLICE=wings-$(echo $UUID | tr -d -).slice
docker inspect -f '{{.HostConfig.CgroupParent}}' $UUID       # -> $SLICE (NOT bare wings.slice)
cat /proc/$(docker inspect -f '{{.State.Pid}}' $UUID)/cgroup # /wings.slice/$SLICE/docker-*.scope
cat /sys/fs/cgroup/wings.slice/$SLICE/memory.min             # your floor, in bytes
cat /sys/fs/cgroup/wings.slice/memory.min                    # parent floor (unit file)

# 3. Unit identity + reload survival
systemctl show $SLICE -p Transient -p MemoryMin              # floor set; Transient=yes
                                                             # unless Wings adopted an
                                                             # existing slice (see below)
systemctl daemon-reload \
  && cat /sys/fs/cgroup/wings.slice/$SLICE/memory.min        # unchanged

# 4. Wings' own account of it — one line per application, naming the slice,
#    the phase, what triggered it, and the values actually applied
docker logs <wings-container> 2>&1 | grep "cgroups:"
#   ... ensured per-server slice  slice=wings-<32hex>.slice phase=startup
#       reason="server starting" properties="MemoryMin=6G MemoryHigh=20G ..."
#   ... steady band applied; ramping memory.high down   memory_high_target=7G
#   ... memory.high ramped to the steady ceiling  memory_high=7G steps=NN
#   (steps=NN present only when steady_ramp_step is set; one-shot otherwise)

# 5. The ramp actually stepping the ceiling down (only with steady_ramp_step set)
watch -n1 "cat /sys/fs/cgroup/wings.slice/$SLICE/memory.high"   # 20G -> ... -> 7G

# 6. Phase events reached the Panel activity log (WINGS_CG_PHASE_EVENTS)
#    — visible in the panel's server → Activity tab as `server:cgroups.phase`,
#    and in the Wings log:
docker logs <wings-container> 2>&1 | grep "cgroups: phase"
#   ... cgroups: phase  phase=steam-update-started ...
#   ... cgroups: phase  phase=world-load-begin ...
#   ... cgroups: phase  phase=steady-reached ...

# 7. Ramdisk-unit trigger (WINGS_CG_RAMDISK_UNITS, patch 0011) — one line per
#    trigger attempt on every container create, whether accepted or rejected
docker logs <wings-container> 2>&1 | grep "ramdisk-setup unit\|RAMDISK_UNITS unit not listed"
#   ... triggered ramdisk-setup unit  unit=soulmask-pak-ramdisk.service
#   ... rejected WINGS_CG_RAMDISK_UNITS unit not listed in docker.allowed_ramdisk_units  unit=sshd.service
systemctl is-active soulmask-pak-ramdisk.service   # active (exited) after the first trigger
```

Lifecycle spot-checks worth doing once: panel Restart (slice re-ensured,
properties survive), delete a throwaway server (its slice is stopped), restart
Wings (boot log GCs only orphaned `wings-<32hex>.slice` units that are
Wings-owned, i.e. `Transient=yes`).

### Troubleshooting

| Symptom | Cause |
|---|---|
| Container sits in bare `wings.slice`; no `ensured per-server slice` log | `per_server_slices.enabled` is not `true` **as Wings parsed it** — check §6 step 1, then §3 "How Wings treats this file" |
| Container sits in bare `wings.slice`, config confirmed enabled | Container predates the config change — recreate it (§5) |
| Container in the derived slice, but all values 0/`max`/100 and `systemctl show <slice>` reports everything `[not set]` | Wings could not reach systemd, so the slice you see was auto-created by systemd as an implicit unit, not by Wings. Either the D-Bus socket is not mounted, or it is mounted but unreachable at the library's `/var/run` default — see the `/var/run` trap in §2. The log line blames `/run/systemd/private` in **both** cases; don't trust it. Judge by the *properties*, not by `Transient` — see the row below |
| `systemctl show <slice>` says `Transient=no`, but the properties **are** set | Normal, not a fault. If systemd had already materialized the slice implicitly (a container was placed under it during a window when Wings could not reach the bus), Wings adopts it with `SetUnitProperties` instead of creating it, and an adopted unit is not transient. The properties are live and reload-safe; they live in `/run/systemd/system.control/<slice>.d/` and are gone after a reboot, at which point Wings recreates the slice properly. One consequence: the boot-time orphan GC only stops units systemd marks `Transient=yes`, so an adopted slice is never garbage-collected |
| Everything looks right; floors still don't bite | `memory_recursiveprot` missing (§1b), or the child floor exceeds the parent's `MemoryMin` and was clamped (check the Wings log for the budget line) |
| Wings exits at boot | Config validation — the message names the key. `enabled: true` without `cgroup_parent` is the usual one |
| IO weights have no effect | Scheduler is not BFQ (§1c) |
| `WINGS_CG_RAMDISK_UNITS` set but never triggers, only a rejection log line | The unit name is not in this node's `docker.allowed_ramdisk_units` — add it and restart Wings; the variable itself is never sufficient on its own |
| Ramdisk trigger logged as failed, server still starts fine | Expected fail-open behaviour — D-Bus unreachable, unit missing, or job timed out. The server just keeps its own private copy of whatever the unit shares. Check `docker logs` for the specific error and, on the host, `systemctl status <unit>` |

## 7. Rollback

- **Per-server slices only:** `per_server_slices.enabled: false` → restart Wings
  → recreate containers → node-wide T1 placement remains, floors live at tier
  level only.
- **Full:** point the compose `image` back at `ghcr.io/pterodactyl/wings:latest`
  and force-recreate. Stock Wings ignores the extra YAML keys and every
  `WINGS_CG_*` variable. Already-placed containers keep their slice until their
  next recreation, so rollback costs no extra outage.
