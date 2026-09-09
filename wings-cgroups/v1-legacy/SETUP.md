# SETUP — deploying the patched Wings (patches 0001–0010) on a node

The one-stop, node-agnostic deployment guide: host prerequisites → compose →
`config.yml` → panel data → cutover → verification → rollback, in that order.
The tier folders (`t0-*`, `t1-*`, `t2-*`) explain each mechanism in isolation and
stay the reference for *why*; this is the *how* for the normal case — a node
running the full patch series with Wings-managed per-server container scope
properties (0004).

> **Shape, since 2026-09-08.** There is no per-server slice any more. Every
> container is placed directly in the node slice (`HostConfig.CgroupParent =
> wings.slice`) and Wings applies the per-server floors, ceilings and weights
> **to the container's own `docker-<id>.scope`** — the unit Docker creates —
> over systemd D-Bus. The tree is two levels, not three:
>
> ```
> wings.slice                    the node tier — a real unit file
> └─ docker-<id>.scope           the container: Wings' properties land here
> ```
>
> Wings never creates a unit; it only sets properties on one Docker already
> made. Consequences worth knowing before reading on: the properties can only
> be applied **after** `ContainerStart()` (§5), there is nothing left to orphan
> and therefore no boot-time slice GC, and there is no budget arithmetic — see
> §3.

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
silently dead. **Wings does no arithmetic about this.** It applies the floors
you configured, as stated, and the kernel arbitrates any overcommit by sharing
the tier's protection out in proportion to each server's usage below its own
floor ([`CGROUP-SEMANTICS.md`](CGROUP-SEMANTICS.md) Rule 5). Wings' only
contribution is a tripwire: if the floors it currently has applied across live
scopes sum above `wings.slice`'s own `MemoryMin` (read live from systemd), it
says so in the log and changes nothing. The unit file is where the tier's total
reservation is declared, and it is the number that actually binds. Optional but
recommended alongside it:
`t0-host-baseline/t0a-wings-self/wings-mgmt.slice`, which caps the Wings daemon
itself (compose `cgroup_parent: wings-mgmt.slice`) so it can't eat the tier's
headroom.

### 1b. cgroup2 mount flags (kernel prerequisite — check, usually free)

Protection declared on a *slice* only reaches the pages below it — which are
charged to the leaf `docker-*.scope`, never to the slice — when cgroup2 is
mounted with `memory_recursiveprot`. systemd ≥ 248 mounts it by default at boot,
but a runtime remount from the init cgroup namespace can strip it (observed in
the wild), and then such a slice-level `MemoryMin`/`MemoryLow` protects
**nothing**, with zero errors anywhere.

**This is no longer load-bearing for the floors Wings sets.** Since the 2026-09-08
redesign Wings writes them onto the container's own scope — the leaf that owns
the pages — so there is nothing to recurse through and the flag cannot silently
void them. It still matters, and is still required, in two places:

- the **node tier**: `wings.slice`'s own `MemoryMin`/`MemoryLow` (§1a) is a
  parent protection and does nothing for its children without the flag — and
  that tier floor is the whole aggregate guarantee now that Wings does no budget
  arithmetic;
- any **admin-managed slice** a server is pinned to with `WINGS_CGROUP_PARENT`
  (§4, patch 0002). That path is unchanged by the redesign: the server's
  container is a scope under that slice, and the slice's floors reach it only
  with the flag set.

So: still check it, still fix it, just no longer the single point of silent
failure it once was.

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

> **The panel's "Block IO Weight" now targets the same cgroup as
> `WINGS_CG_IO_WEIGHT` — mind the overlap.** Wings has always passed the panel
> value to Docker as `--blkio-weight`; on cgroup v2 runc writes it straight to
> `io.bfq.weight` on the container's `docker-*.scope`, on BFQ's own 10..1000
> scale, uncompressed (measured). Before the 2026-09-08 redesign the variables
> below acted one level up, on the server's slice, so the two composed
> multiplicatively (Rule 6). They no longer do: both land on the **same scope**,
> and `IOWeight=` re-derives `io.bfq.weight` from systemd's own compressed
> scale, overwriting what runc wrote. Set one or the other, not both, and if
> you set `WINGS_CG_IO_WEIGHT` expect it — not the panel field — to be what the
> scheduler sees. Note also the naming collision: the panel field and
> `defaults.io_weight` share a name but use different scales
> ([`CGROUP-SEMANTICS.md`](CGROUP-SEMANTICS.md) Rule 7 has the conversion
> table).

## 2. Wings compose adaptation

```yaml
services:
  wings:
    image: wings-local:1.13.3-cgroup.1        # the patched image
    cgroup_parent: wings-mgmt.slice           # optional (T0a): cap the daemon itself
    volumes:
      # …existing mounts (docker.sock, /etc/pterodactyl, /var/lib/pterodactyl, …)…
      # REQUIRED — per-server scope properties (patch 0004) are set over this
      # socket. No mount, no properties: Wings falls back to placement-only.
      # This is the mechanism, at every image version.
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
> `/var/run` at all, so the mount above is invisible to it and property
> management silently degrades to placement-only. The stock go-systemd connect helper then
> misreports it: it discards the system-bus error and falls back to the private
> socket, so the log blames `/run/systemd/private`, a path you never configured.
> Patch 0004 therefore dials each candidate itself (`/run/dbus/...`, then the
> library default, then the private socket) and names every attempt in the
> error. **On an image built before `cgroup.3` this fix is absent** — there you
> must set `DBUS_SYSTEM_BUS_ADDRESS` as above, or add a second mount at
> `/var/run/dbus/system_bus_socket`.

Fail-open by design: with no reachable bus, Wings logs that it could not apply
the scope properties and servers still start — placement-only. The container
lands in `wings.slice` exactly as it would have, inheriting the tier's
protection and nothing individual, so the cgroup tree looks completely correct
while every per-server floor, ceiling and weight is absent. `systemd-cgls`
cannot show you this; only reading the values can (§6).

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
  # is enabled: it IS the cgroup parent every container is placed in, and the
  # slice whose own MemoryMin backs every floor below it. Wings refuses to start
  # on enabled-without-parent. Bare slice unit name only.
  cgroup_parent: wings.slice

  # T2 (patch 0002) — allow-list for per-server WINGS_CGROUP_PARENT overrides.
  # Empty = only the built-in wings.slice/wings-*.slice namespace is accepted.
  allowed_cgroup_parents: []

  # T3b (patch 0004) — per-server properties on the container's own scope.
  per_server_slices:
    enabled: true
    # Node-wide defaults applied to every container scope; per-server WINGS_CG_*
    # egg variables override field-by-field. Leave empty on mixed-use nodes
    # (a default memory_min hands EVERY server a reservation).
    defaults:
      memory_min: ""          # e.g. "512M" — sizes take G/M/K or bytes
      memory_low: ""
      memory_high: ""
      memory_max: ""
      cpu_weight: 0           # 1..10000; 0 = unset. Ratios are exact.
      io_weight: 0            # 1..10000, systemd's scale; 0 = unset. On a BFQ
                              # node this is NOT the weight that schedules —
                              # systemd compresses it (1000 -> io.bfq.weight 181).
                              # Pick the number from CGROUP-SEMANTICS.md Rule 7's
                              # table; Wings does no conversion for you.
    # Startup band (patch 0006) — applied with the rest of the properties as soon
    # as the container's scope exists, then replaced by `defaults:` when the
    # WINGS_CG_STEADY_MATCH trigger fires (default: the egg's startup "done"
    # matcher) or startup_grace expires.
    #
    # Why this exists: a game's load-time peak dwarfs its steady working set,
    # and a cgroup is not protected from reclaim it inflicts on itself — exceeding
    # its own memory.high reclaims straight through its own memory.min floor. A
    # ceiling sized for the steady state therefore evicts the server through its
    # own floor while it is still loading — permanently, because nothing faults
    # those pages back except the workload. Leave empty to apply the steady band
    # from the start (behaviour before 0006).
    #
    # ENGAGEMENT: the startup band only does something when its memory_high is
    # ABOVE the steady defaults.memory_high. If they are equal, the phase change
    # and the ramp below are no-ops. Set startup memory_high high (or leave the
    # steady one low) so there is a ceiling to lift during load and lower after.
    #
    # TIMING: the scope does not exist until the container's init process is
    # running, so the startup band is applied just AFTER ContainerStart(), not
    # before container create. See §5.
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
```

**There is no floor budget and no budget policy.** Both were removed on
2026-09-08 along with the per-server slice; so were `memory_min_budget`,
`budget_policy` (`clamp`/`refuse`/`distribute`) and `io_bfq_weight`. If any of
those keys are still in your `config.yml`, Wings will report them as discarded
at boot (patch 0007) and the rewrite will drop them — that is the expected
migration path, not a fault.

What replaces them is nothing, deliberately. Wings applies the floors as
configured and never corrects them; overcommit is arbitrated by the kernel's own
proportional-by-usage sharing of the parent's protection
([`CGROUP-SEMANTICS.md`](CGROUP-SEMANTICS.md) Rule 5) — which is what
`budget_policy: distribute` already amounted to, minus the code. The one thing
Wings still does is tell you: if the floors it currently has applied across live
scopes sum above `wings.slice`'s own `MemoryMin` (read live from systemd), it
logs that the node is oversubscribed. That line changes no behaviour and never
shrinks anyone's floor. Sizing the tier is therefore entirely the `wings.slice`
unit file's job (§1a).

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
                                      # loudly on typos; discarded keys from an
                                      # older schema are named here (patch 0007)
```

## 4. Panel data (egg / server variables — all admin-only, optional)

Add these 15 admin-only variables to your egg. A complete worked example in
PTDL_v2 export format is `../game_stuff/soulmask/egg-soulmask-rcon-ksm-cgroups.json`
(import it over an existing egg to update in place — servers keep their egg
association); `t2-per-server-placement/egg-variable.snippet.json` carries just
`WINGS_CGROUP_PARENT`, for placement-only deployments. A changed value takes
effect at the next container (re)creation — panel **Stop → Start**, not restart.

> **Installer containers get placement, not properties.** They are created
> under the same tier slice as the runtime container (so `wings.slice`'s own
> limits still bound them) and are otherwise governed only by
> `docker.installer_limits`. Since the 2026-09-08 redesign they receive none
> of the per-server `WINGS_CG_*` values: an installer is short-lived, and
> handing it the server's `memory.min` would reserve a protection floor for
> work that is not the server running. Under the retired per-server-slice
> design the installer shared the server's slice and did inherit them.

> **Prerequisite:** import/patch the egg (panel → Nests → your egg → *Import*,
> over the existing egg) **before** these variables can be set on a server —
> a `WINGS_CG_*` variable that the egg does not define is not settable in the
> panel. Node-level `config.yml` `defaults:` apply without the egg, so per-server
> variables are genuinely optional; but any per-server override needs the egg
> imported first. The ramp itself (`steady_ramp_step`) is `config.yml`-only and
> has **no** egg variable.

| Variable | Meaning |
|---|---|
| `WINGS_CG_MEMORY_MIN` / `_LOW` / `_HIGH` / `_MAX` | per-server floors/ceilings (G/M/K or bytes), applied to the container's own scope |
| `WINGS_CG_CPU_WEIGHT` | per-server CPU weight (1..10000). Ratios are honoured exactly. |
| `WINGS_CG_IO_WEIGHT` | per-server IO weight on systemd's scale (1..10000). **Compressed on BFQ nodes** — 1000 becomes `io.bfq.weight` 181, so pick the number from [`CGROUP-SEMANTICS.md`](CGROUP-SEMANTICS.md) Rule 7's table rather than the one that looks right. Wings does no conversion; this is the same hand-arithmetic the `wings.slice` unit file's `IOWeight=` already requires. Note it now lands on the same cgroup as the panel's "Block IO Weight" and overwrites it (§1c). |
| `WINGS_CG_STEADY_MATCH` | the console line that ends the startup phase and applies the steady band **and** — if set — is the ONLY thing that ends it: this is armed whether or not a startup memory band is staged (patch 0009 fixed a gap where an unstaged server silently ignored this variable and fell back to the egg's own done/running line instead). `regex:` prefix for a regular expression, anything else is a literal substring. **Empty falls back to the egg's own `startup.done` matcher** — which for a world-streaming game routinely fires *before* loading finishes, so set this explicitly if the two differ. **If you set `WINGS_CG_CHILD_SERVERS` on this server, treat this variable as load-bearing, not optional**: a child starts the moment this line appears (or, if you leave it empty, the moment the egg's own done/running line appears) — verify that line genuinely means "safe for a dependent to read/connect now", not merely "the Panel should show Running". |
| `WINGS_CG_PHASE_EVENTS` | optional, **informational only** — newline-separated `name=match` lines; the first console line matching each is recorded to the Panel activity log (`server:cgroups.phase`). Surfaces a game's long, opaque startup (steam update, world load). Drives no cgroup behaviour. |
| `WINGS_CG_STARTUP_GRACE` | per-server override of `startup_grace`; the backstop for a trigger that never fires. Go duration (`15m`, `90s`); `0` disables the timer. Empty = the node default. Armed whenever `WINGS_CG_STEADY_MATCH` is set too, even with no startup band staged — see that row. |
| `WINGS_CG_STARTUP_MEMORY_MIN` / `_LOW` / `_HIGH` / `_MAX` | the same four knobs, applied only while the server is starting and replaced by the steady values once it reports ready (or after `startup_grace`). Set `_HIGH` generously — a ceiling below the load-time peak breaches the floor and cannot be undone without a restart. Needs patch 0006. |
| `WINGS_CG_CHILD_SERVERS` | comma- or newline-separated **server UUIDs** (not egg names) on this same node to start once THIS server reaches its steady trigger (the row above) — ordinary cluster start-ordering, e.g. a server that must not read a shared bind mount until another one's write to it is confirmed done. Started via the normal Panel/API start path (`HandlePowerAction`), so a suspended child, one not managed by this node, or the server naming itself is logged and skipped, never fatal. **Never honoured on the `startup_grace` backstop** — a trigger that never fired means readiness was never confirmed. **Wings has no "do not autostart" flag**: keeping a child stopped whenever its parent is stopped is the operator's own responsibility — a child left running with its parent stopped, then started by hand, autostarts unconditionally on the next boot like any other server. A child and its configured parent that are BOTH running when the node reboots are handled safely (the child's boot-time restart is deferred to the parent's, not raced) — see `patchstack/README.md` patch 0009 and `internal/cgroups.DeferBootRestart`. Needs patch 0009. |
| `WINGS_CGROUP_PARENT` | **leave empty** — override/opt-out only. A set value pins the container into that admin-managed slice instead of the node default, and must pass the allow-list. Unchanged by the 2026-09-08 redesign, and the one path where `memory_recursiveprot` is still load-bearing (§1b): the floors declared on an admin-managed slice only reach the container's scope below it with the flag set. A pinned server still gets its **explicit `WINGS_CG_*` values** applied to its own scope, but **never the node-wide `defaults`** — that slice's policy is the administrator's, and stamping node defaults onto a scope inside it would fight the override. It is also left out of the node's floor ledger, so its `memory.min` does not count toward the oversubscription line (its floor draws on the admin's slice, not this tier). Setting this to *exactly* the node-wide `docker.cgroup_parent` value is the documented per-server **opt-out**: placement is unchanged and Wings leaves that server's cgroup properties entirely alone. |

Sizing guidance: `memory.min` = the working set that must never be reclaimed
under outside pressure; `memory.low` = soft protection above it (only meaningful
below `memory.high` — a cgroup's own `high` reclaims regardless of its own
protections); `memory.high` = where the server gets squeezed into zswap/reclaim;
`memory.max` above physical RAM is inert. Weights only settle sibling contention
*within* the node slice, and only under actual contention — a server's
`cpu_weight` is its share of the tier, never of the host, and the tier's share is
set one level up on the `wings.slice` unit. With the per-server slice gone, the
siblings a server competes against are simply the other servers' scopes in
`wings.slice`, which is what the ratios were always meant to express.

One trap when picking weights: `cpu_weight` ratios are honoured exactly, but
`io_weight` is rescaled by systemd into BFQ's 1..1000 range (`io.bfq.weight`),
which compresses everything above the default by ~11× — `io_weight: 1000` buys
1.8:1 against a default sibling, not 10:1. Wings does not convert this for you;
pick the `IOWeight` from the table in
[`CGROUP-SEMANTICS.md`](CGROUP-SEMANTICS.md) Rule 7 (a real 5.4:1 needs
`io_weight: 4950`), exactly as the node's own `wings.slice` unit file already
does — its live `IOWeight=7800` was hand-chosen to land on `io.bfq.weight ≈ 800`,
for parity with `CPUWeight=800`. Verify on the scope itself —
`cat /sys/fs/cgroup/…/io.bfq.weight`, never `io.weight`, which BFQ does not read.

That trap — and the arithmetic that decides whether a floor is real, dead, or
shared — is worked through with examples in
[`CGROUP-SEMANTICS.md`](CGROUP-SEMANTICS.md) Rules 5–7. Read it before picking
numbers.

## 5. Cutover

Placement is create-time only, so each server needs one container recreation:
panel **Stop → Start** (Wings recreates the container on start; bind-mounted data
is untouched). `docker rm <uuid>` while stopped is equivalent and explicit.

Optional dry-run: start a never-started test server with no variables set and
confirm it lands directly in `wings.slice` before touching a live game.

> **The post-start window — known, accepted, not a bug.** A container's
> `docker-<id>.scope` does not exist until its init process launches, so there
> is no unit to set properties on before that moment. Wings therefore applies
> the whole property set immediately **after** `ContainerStart()` returns. For
> the few milliseconds in between, the container runs with only the tier's
> protection from `wings.slice` and none of its own. This gap is real and was
> weighed: a game server's load-time peak arrives seconds to minutes later, so
> nothing of consequence happens in that window, and closing it would mean
> going back to creating a unit ahead of the container — which is exactly what
> this redesign removed. Do not "fix" it by racing the container create.

## 6. Verify

```bash
# 1. Did Wings actually parse the config? (the rewrite is the source of truth)
grep -A3 per_server_slices /etc/pterodactyl/config.yml     # enabled: true
grep cgroup_parent /etc/pterodactyl/config.yml             # wings.slice

# 2. Placement + effective values. The unit to look at is the CONTAINER's scope,
#    named from its full container id, and it only exists while it runs.
UUID=<server-uuid>; CID=$(docker inspect -f '{{.Id}}' $UUID); SCOPE=docker-$CID.scope
docker inspect -f '{{.HostConfig.CgroupParent}}' $UUID       # -> wings.slice (flat, no per-server slice)
cat /proc/$(docker inspect -f '{{.State.Pid}}' $UUID)/cgroup # /wings.slice/$SCOPE
cat /sys/fs/cgroup/wings.slice/$SCOPE/memory.min             # your floor, in bytes
cat /sys/fs/cgroup/wings.slice/memory.min                    # tier floor (unit file)

# 3. Property identity + reload survival. Docker created the scope; Wings only
#    set properties on it, which live in /run/systemd/system.control/ and are
#    reload-safe. Transient=yes here is Docker's doing, not Wings'.
systemctl show $SCOPE -p MemoryMin -p MemoryHigh -p CPUWeight -p IOWeight
systemctl daemon-reload \
  && cat /sys/fs/cgroup/wings.slice/$SCOPE/memory.min        # unchanged

# 4. Wings' own account of it — one line per application, naming the scope,
#    the phase, what triggered it, and the values actually applied
docker logs <wings-container> 2>&1 | grep "cgroups:"
#   ... applied scope properties  scope=docker-<id>.scope phase=startup
#       reason="server starting" properties="MemoryMin=6G MemoryHigh=20G ..."
#   ... steady band applied; ramping memory.high down   memory_high_target=7G
#   ... memory.high ramped to the steady ceiling  memory_high=7G steps=NN
#   (steps=NN present only when steady_ramp_step is set; one-shot otherwise)
#   (wording is indicative — grep "cgroups:" and read what is there)

# 5. The ramp actually stepping the ceiling down (only with steady_ramp_step set)
watch -n1 "cat /sys/fs/cgroup/wings.slice/$SCOPE/memory.high"   # 20G -> ... -> 7G

# 6. Phase events reached the Panel activity log (WINGS_CG_PHASE_EVENTS)
#    — visible in the panel's server → Activity tab as `server:cgroups.phase`,
#    and in the Wings log:
docker logs <wings-container> 2>&1 | grep "cgroups: phase"
#   ... cgroups: phase  phase=steam-update-started ...
#   ... cgroups: phase  phase=world-load-begin ...
#   ... cgroups: phase  phase=steady-reached ...

# 7. The oversubscription tripwire (informational only — nothing is corrected)
docker logs <wings-container> 2>&1 | grep -i "budget\|oversubscribed"
```

Lifecycle spot-checks worth doing once: panel Restart (a **new** scope is
created and the properties are applied to it afresh — the id changes, so do not
expect the old scope back), delete a throwaway server (nothing to clean up: the
scope died with its container), restart Wings (no slice GC any more — there is
nothing left that can be orphaned).

### Troubleshooting

| Symptom | Cause |
|---|---|
| Scope exists under `wings.slice` but carries no properties, and no `cgroups:` log line | `per_server_slices.enabled` is not `true` **as Wings parsed it** — check §6 step 1, then §3 "How Wings treats this file" |
| Container is not under `wings.slice` at all | Placement is create-time only and the container predates the config change — recreate it (§5) |
| Scope in the right place, but all values 0/`max`/100 and `systemctl show <scope>` reports everything `[not set]` | Wings could not reach systemd, so nothing was applied — Docker's own scope is all you are looking at. Either the D-Bus socket is not mounted, or it is mounted but unreachable at the library's `/var/run` default — see the `/var/run` trap in §2. The log line blames `/run/systemd/private` in **both** cases; don't trust it |
| `systemctl show <scope>` says `Transient=yes` | Expected, and it says nothing about Wings. Docker creates the container's scope as a transient unit; Wings only sets properties on it. There is no Transient-vs-adopted distinction to make any more — Wings never creates a unit, so it never has to tell its own units from anyone else's |
| Everything looks right; floors still don't bite | The tier floor is too small — `wings.slice`'s own `MemoryMin` caps every scope below it (§1a, Rule 1), and nothing in Wings will warn you beyond the oversubscription line. If the server is pinned with `WINGS_CGROUP_PARENT`, also check `memory_recursiveprot` (§1b) |
| Floors briefly absent right after a start | Expected: properties are applied just after `ContainerStart()` (§5). If they are *still* absent seconds later, it is the D-Bus row above |
| Wings exits at boot | Config validation — the message names the key. `enabled: true` without `cgroup_parent` is the usual one |
| Boot log names `budget_policy` / `memory_min_budget` / `io_bfq_weight` / `allowed_ramdisk_units` as discarded keys | Expected on the first boot after upgrading: those keys were removed on 2026-09-08 (§3). Delete them from `config.yml`; the boot rewrite drops them anyway |
| IO weights have no effect | Scheduler is not BFQ (§1c) |
| IO weight is set but the scheduler shows the panel's number instead | Both now write the same file on the same scope (§1c). Whichever was applied last wins, and Wings applies after container start |

## 7. Rollback

- **Per-server properties only:** `per_server_slices.enabled: false` → restart
  Wings → recreate containers → node-wide T1 placement remains, floors live at
  tier level only.
- **Full:** point the compose `image` back at `ghcr.io/pterodactyl/wings:latest`
  and force-recreate. Stock Wings ignores the extra YAML keys and every
  `WINGS_CG_*` variable. Already-placed containers keep their placement until
  their next recreation, so rollback costs no extra outage; the properties Wings
  set are runtime-only and die with the scope.
