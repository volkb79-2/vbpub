# mdt host-setup — dev-tier resource governance (cgroup v2 slices)

Prepares a Docker host so devcontainers, gate/lane containers, and long-running stacks
they spawn run in **bounded systemd slices** instead of the host's default
(unlimited) cgroup — the host-side counterpart of the
`"--cgroup-parent=dev-interactive.slice"` runArg shipped in
[`../templates/devcontainer.json`](../templates/devcontainer.json), the
`cgroup_parent: dev-background.slice` that ciu governance injects into compose
stacks, and `/etc/docker/daemon.json`'s `cgroup-parent` (the daemon-wide
fallback for anything that names no parent at all — this companion is the
**sole owner** of that file now, merging its managed keys into whatever else
is already there rather than overwriting it). Placement is CREATE-time only
and can never be expressed from inside a container or image — see
[`../docs/CONTAINER-DOCTRINE.md`](../docs/CONTAINER-DOCTRINE.md) and the
"Host resource governance" section of
[`../DEVCONTAINER-LIFECYCLE.md`](../DEVCONTAINER-LIFECYCLE.md).

**[`CGROUP-NOTES.md`](CGROUP-NOTES.md) is the conceptual half of this
directory:** what a slice unit fundamentally *cannot* express — and therefore
why there is a script and a timer here at all — plus the BFQ caveats. On a BFQ
host `IOWeight` does not mean what it says; read that before changing any
weight in `host-setup.env`.

## Tiering model

```
dev.slice                    ONE absolute IOPS/bandwidth/CPU/swap ceiling —
                              covers every child combined, even interactive/
                              IDE activity must not be able to starve
                              production. Every child below ALSO carries its
                              OWN tighter CPU/swap sub-ceiling so no single
                              tier can alone claim this whole parent budget.
├── dev-interactive.slice    devcontainers (IDE + AI agents) via
│                             devcontainer.json runArg
├── dev-gates.slice          gate and lane containers + placed lane leaves
│                             rg-<token> — the admission capacity object;
│                             see "dev-gates: why" below. ALSO gets a
│                             tighter IOPS sub-ceiling (bandwidth untouched)
├── dev-background.slice     long-running dev stacks (an app under development, ...) — explicit
│                            opt-in (compose cgroup_parent, docker run
│                            --cgroup-parent) OR caught by the Docker
│                            daemon-wide default (/etc/docker/daemon.json)
├── dev-memory_min_guaranteed.slice   individually-governed containers with a
│                            real memory.min floor (opt-in; see CGROUP-NOTES.md
│                            "Per-container memory.min guarantees")
└── dev-buildkitd.slice      host-managed BuildKit worker (mdt-buildkitd.
                             service) — a shared, long-lived builder, not
                             a per-invocation job; see
                             plan-buildkitd-service.md. ALSO gets the same
                             tighter IOPS sub-ceiling as dev-gates.slice
```

**CPU, swap, and zswap-writeback all get the same "one ceiling at dev.slice,
one tighter sub-ceiling per child" treatment as IO:**
- `CPUQuota`: `dev.slice` derives `nproc - MDT_EXCLUDED_CPU_CORES`; every child
  derives that minus `DEV_TIER_EXCLUDED_CPU_CORES` independently (both
  default 0; see "Capacity unavailable to MDT" below).
- `MemorySwapMax`: `dev.slice` is the disk swap MDT may use (`SWAP_MDT`, below);
  each child is `DEV_SWAP_CASCADE_PCT`% (default 80) of `dev.slice`'s number. The
  inotify watcher's per-container swap cap is conditional: it applies 80%
  again only when that slice has an explicitly configured per-container
  memory pair; shipped pairs are disabled.
- `MemoryZSwapWriteback`: explicit on all five. **Does not cascade from
  `dev.slice`** — it's a plain per-cgroup toggle, verified live (2026-09-12)
  to NOT propagate a parent's own value down; each slice states its own
  intent. `dev.slice`'s own value is a safety anchor only, since a `0` on
  ANY ancestor silently disables writeback for the whole subtree below it
  (`CGROUP-NOTES.md` "zswap writeback — who may page to disk" has the full
  mechanism).

`dev.slice` holds dev LOAD only, nothing else — the host-level
the profiler daemon (`cgpd.service`) is a host deployment, not dev load, and
runs in its own top-level `cgprofile.slice` instead of living under here (design amendment A1/D-29, `run-gate-project/nyxloom-trove/
DESIGN-2026-09-12-liveness-placement-admission.md`).

| Tier | Who joins | Character |
|---|---|---|
| `dev-interactive.slice` | devcontainers (IDE + AI agents) via devcontainer.json runArg | responsive: soft-protected working set (`MemoryLow`), generous `MemoryHigh`, cold tail compressed into zswap and allowed to drain to disk from there (`DEV_INTERACTIVE_ZSWAP_WRITEBACK`), never OOM-killed |
| `dev-gates.slice` | gate/lane containers + placed lane leaves `rg-<token>`, via explicit `docker run --cgroup-parent` or `$CGROUP_PARENT_DEV_GATES` | the admission capacity object: finite memory+swap caps; cgroup v2 reclaims to swap before OOM, and no `systemd-oomd` kill is enabled |
| `dev-background.slice` | long-running dev stacks (an app under development, ...), via compose `cgroup_parent`, explicit `docker run --cgroup-parent`, **or** the Docker daemon-wide default | finite hard memory+swap caps (large swap given ample host swap — size for yours); reclaim to swap runs before OOM, and no `systemd-oomd` kill is enabled |
| `dev-buildkitd.slice` | exactly one container: the host-managed rootless BuildKit worker (`mdt-buildkitd.service`, plain `docker run --cgroup-parent=`, never through a Buildx driver — see `plan-buildkitd-service.md`) | one shared build cache across every consuming project; hard `CPUQuota` derived as `nproc - MDT_EXCLUDED_CPU_CORES - DEV_TIER_EXCLUDED_CPU_CORES` cores (same as every other child) unless overridden; active-build latency-sensitive, so `CPUWeight`/`IOWeight` sit between interactive's and background's; also gets a tighter runtime IOPS sub-ceiling (bandwidth untouched), same as `dev-gates.slice` |
| (production tiers) | e.g. a production service's own top-level slice | owned elsewhere — this companion never touches them, it only keeps dev work from starving them |

`dev.slice` (the shared parent) carries the **one** absolute IOPS/bandwidth
ceiling for every child combined — not one per tier. That is
deliberate: even interactive/IDE work must not be able to starve production
I/O, so a build storm AND a heavy IDE session together still can't exceed the
estate's single cap. `CPUWeight`/memory/OOM policy stay per-child, since
interactive, background, and the shared builder genuinely need different
shapes there.

Per-container policy is optional and separate from the aggregate tier limit.
A caller's explicit `docker run --memory`/`--cpus`/`--device-*-iops` flags
always win. The memory inotify watcher can fill in a configured
`MemoryHigh`/`MemoryMax` pair for a Docker scope with no explicit Docker memory
limit; its shipped values are `-`, so it applies no additional leaf memory or
swap cap unless the operator configures one. CIU-managed Compose services
commonly set their own `mem_limit`, which makes this watcher leave that scope
alone. CIU can also set `MemoryMin`; configure any MDT leaf `MemoryHigh`/
`MemoryMax` so they remain compatible with that scope property. The IO events
watcher separately supplies the measured per-container `io.max` cap. A
slice's own limits still bound the *whole tier combined*, never one container
in it. See `AGENTS.md` in the repo root ("Host resource governance").

Weights (`CPUWeight`/`IOWeight`) settle contention *between* the two child
tiers; `dev.slice`'s `io.max` bounds the **whole estate** absolutely so a
build storm (or a heavy interactive session) can't saturate the disk even when
production is momentarily idle (its next burst must not queue behind it). IO
weights need the BFQ scheduler (installed/selected by this setup); the io.max
caps work on any scheduler.

⚠️ The shipped IO weights (dev-interactive 100, dev-background 10) are a true
10:1 *because both stay ≤ 100*. systemd rescales `IOWeight` above 100 into
BFQ's 1..1000 range, so "1000 vs 100" would be 1.81:1, not 10:1 — express IO
ratios by lowering the loser, never raising the winner.
[CGROUP-NOTES.md §BFQ](CGROUP-NOTES.md#bfq-caveats) has the mapping table.

## Capacity unavailable to MDT

Three numbers name the capacity that other services on the host (a game
server, a database, anything that is not MDT) use and that is therefore
**unavailable to MDT**. Everything else is MDT's. The operator supplies only
these; every dev-tier default is derived from them.

| Key | Meaning | Default |
|---|---|---|
| `MDT_EXCLUDED_RAM` | RAM the other services use | `0` |
| `MDT_EXCLUDED_SWAP` | DISK swap the other services occupy (zswap is ignored) | `0` |
| `MDT_EXCLUDED_CPU_CORES` | cores the other services use; one decimal place (`0.5`, `1.5`); `0` on a host without other load | `0` |
| `DEV_TIER_EXCLUDED_CPU_CORES` | cores no single tier may use on top of the above | `0` |
| `MDT_PROTECTED_UNITS` | the other services' systemd units, space-separated, verified and probed | empty |
| `MDT_HOST_SWAP_MARGIN` | disk swap kept for the host itself | `512M` |

```
RAM_MDT  = MemTotal  - MDT_EXCLUDED_RAM                          (refused below 1G)
SWAP_MDT = SwapTotal - MDT_EXCLUDED_SWAP - MDT_HOST_SWAP_MARGIN  (<= 0: MDT gets no disk swap)
CPU_MDT  = nproc     - MDT_EXCLUDED_CPU_CORES                    (refused below 0.5)
```

- `dev.slice`: `MemoryMax` = `RAM_MDT`, `MemoryHigh` = 92% of it,
  `MemorySwapMax` = `SWAP_MDT`, `CPUQuota` = `CPU_MDT` x 100%.
- Tiers (wizard proposals; an explicit value always wins): `MemoryMax` /
  `MemoryHigh` as percent of `RAM_MDT` - interactive 60/45 (no `MemoryLow`
  is proposed: soft protection is an open design question), background 70/50, gates 70/50,
  buildkitd 35/25. They sum to more than `RAM_MDT` on purpose: RAM and CPU are
  overcommitted by design and zswap and disk swap absorb the cold part. A tier's
  swap is `DEV_SWAP_CASCADE_PCT`% of `SWAP_MDT`; its `CPUQuota` is
  (`CPU_MDT` - `DEV_TIER_EXCLUDED_CPU_CORES`) x 100%.
- Protection of the other services is by weights (`CPUWeight`, `IOWeight`),
  `memory.min` mirroring `MDT_EXCLUDED_RAM` on their own cgroups, and pressure
  (PSI) as the control signal - not by carving RAM out of `dev.slice`.
- A host with no swap needs no confirmation. When swap exists but `SWAP_MDT` is
  not positive, the installer asks to confirm (or takes `--confirm-no-disk-swap`)
  and MDT gets `MemorySwapMax=0`.
- Lane workers and R2 jobs are sized by run-gate from measured per-worker
  profiles, not from these limits. The tier `MemoryMax` is the safety cap; the
  run-gate admission budget is the predictable need.

Worked examples:

| Host | Inputs | Result |
|---|---|---|
| Shared host | 8 cores, MemTotal 15.5 GiB, 69 GiB swap; `6500M` RAM, `14G` swap, `1.5` cores excluded | `RAM_MDT` 9.2 GiB, `SWAP_MDT` 54.5 GiB, `dev.slice` `CPUQuota` 650% |
| Small host | 4 cores, 7.75 GiB RAM, all excluded values 0 | `RAM_MDT` 7.75 GiB, `SWAP_MDT` = SwapTotal - 512M, `CPUQuota` 400% |

### Verifying the protection

`mdt-host-check.sh` (and the wizard summary) WARN, for the units named in
`MDT_PROTECTED_UNITS`, when:

- the effective `memory.min` summed over them is below `MDT_EXCLUDED_RAM`;
- the children's `memory.min` sum exceeds the parent's (the kernel then scales
  the children down proportionally);
- a unit's `CPUWeight` or `IOWeight` is below 4x that of `dev.slice`.

This is verify-only: the production units are operator-owned.
`sudo ./install.sh --apply-protection` is the opt-in alternative: after an
interactive confirmation it writes `50-mdt-protection.conf` drop-ins with
`MemoryMin=` (the excluded RAM, split by current use), `CPUWeight=` and
`IOWeight=` (4x `dev.slice`) for exactly the named units, then runs
`systemctl daemon-reload`. Nothing else is installed or restarted.

### Suggesting the numbers: `--probe-excluded`

```
mdt-host-setup-wizard.py --probe-excluded [--units wings.slice ...] [--seconds 600]
```

Read-only. For each named unit (default: `MDT_PROTECTED_UNITS` of the installed
config) it samples `memory.min`/`memory.current`/`memory.swap.current`, the
`cpu.stat` usage and the weights, over the window, and prints a table plus the
suggestions: RAM = the effective `memory.min` sum rounded up to 256M (the p95 of
`memory.current` flagged "unprotected" when no `memory.min` is set), swap = the
maximum of `memory.swap.current` rounded up to 512M, CPU = the p95 usage rate
rounded up to 0.5 core. Nothing is written unless `--accept-probe` is also given,
which updates the three numbers (and `MDT_PROTECTED_UNITS` when `--units` was
given) in the file named by `--output`.

### The PSI guard (`mdt-psi-guard`)

A small stdlib service in `system.slice` (outside `dev.slice`, so it keeps
running while the dev tiers are starved) that uses pressure (PSI) as the control
signal. It watches the protected units' `memory.pressure` `some` (10%), the host's
`/proc/pressure/memory` `full` (5%) and the protected units' `cpu.pressure`
`some` (25%); a signal fires after 10 s above its threshold and is released after
60 s below half of it. The stall share is the growth of the cumulative `total=`
counter, which is what a kernel PSI trigger measures (the guard polls the counters
instead of waiting on a trigger fd, so its logic is deterministic).

`MDT_PSI_GUARD` selects the mode: `off` (service disabled), `report` (shipped:
events only, nothing changed) or `enforce`. In `enforce`:

1. `CPUWeight` and `IOWeight` of `dev-gates`, `dev-background` and
   `dev-buildkitd` go to 1;
2. after 30 s still above, `MemoryHigh` of `dev-gates` and `dev-background` is
   lowered to `max(256M, 0.9 x memory.current)`.

Every change is reverted on release, on stop and at the next start (the
originals are kept in `/run/mdt/psi-guard/state.json`, which also lists the keys
the guard owns; the reconcile sweep writes none of them and only logs them).
`events.jsonl` is rotated at 1 MiB to `events.jsonl.1` (it is on tmpfs), so
readers consume both files. A `systemctl daemon-reload` or `set-property` during
an active episode re-applies the units' own values and undoes the throttle
(systemd is the overwriter, not the sweep), so do not reload during one. It
never freezes or kills anything: a wall-clock budget that expired while frozen
would change a result. Events are appended to `/run/mdt/psi-guard/events.jsonl`
for run-gate to read as a contamination source.

## Renamed keys (2026-11)

The `*_RESERVE_*` keys were renamed outright; there is no alias. The installer,
the wizard and `mdt-host-check.sh` refuse the old names with a message that names
the new key. Rename them in `/etc/mdt/host-setup.env` before the next install:

| Old key | New key | Value |
|---|---|---|
| `DEV_CPU_RESERVE_CORES=c` | `MDT_EXCLUDED_CPU_CORES` | `c` |
| `DEV_SUBSLICE_CPU_RESERVE_CORES=k` | `DEV_TIER_EXCLUDED_CPU_CORES` | `max(0, k - c)`, which keeps today's tier quota (the parent's quota already caps a tier when `k < c`); `0` is recommended |
| `DEV_MEMORY_RESERVE` | `MDT_EXCLUDED_RAM` | unchanged |

Add the other new keys from `host-setup.env.example` (`MDT_EXCLUDED_SWAP`,
`MDT_PROTECTED_UNITS`, `MDT_HOST_SWAP_MARGIN`, `MDT_PSI_GUARD`); an absent key is
refused as missing.

## Wizard's top-down example

The wizard shows this same hierarchy before it asks for values. The numbers
below are a concrete starting example for a host whose `RAM_MDT` is 16 GiB (no
RAM excluded); they are proposals, not facts. `auto` means derive from the host
or parent at install time. The IO percentage is applied only after the
identity-bound benchmark results are available.

| cgroup | MemoryHigh / MemoryMax | CPUQuota | MemorySwapMax | IO policy | Why it exists |
|---|---:|---:|---:|---|---|
| `dev.slice` (root estate) | `14.5G / 16G` | `800%` (8 cores) | `SWAP_MDT` | `60% of measured device` | One aggregate shield for production. |
| ├─ `dev-interactive.slice` (IDE) | `7G / 9.5G` | `800%` | `80% of parent` | parent + per-container leaf | Keep the IDE/devcontainer responsive. |
| ├─ `dev-background.slice` (long-running stacks) | `8G / 11G` | `800%` | `80% of parent` | parent + per-container leaf | Absorb ordinary stack load. |
| ├─ `dev-gates.slice` (short lanes) | `8G / 11G` | `800%` | `80% of parent` | `60% of parent IOPS` | Bound disposable lanes without starving them. |
| ├─ `dev-buildkitd.slice` (shared builder) | `4G / 5.5G` | `800%` | `80% of parent` | `60% of parent IOPS` | Protect the shared builder and its cache. |
| └─ `dev-memory_min_guaranteed.slice` (admitted floor) | optional | inherited | inherited | inherited | Share `MemoryMin` only when explicitly admitted. |

The tier limits sum to more than the 16 GiB on purpose (overcommit); the
`dev.slice` ceiling is what bounds them together.

Verify the `dev-gates.slice` size against the lanes you run: its
`MemoryMax` must admit the contract-2 mutation lanes, which declare
`2G` per container. Before running those lanes, tune the gate slice's
`MemoryHigh` and `MemoryMax` above the sum of the active lane caps, with room
for container and controller overhead. Two concurrent `2G` lanes need more
than `4G`; a `6G` MDT full-assay lane needs more than `6G` by itself. Check the
ancestor `dev.slice` limits and current use too. Run-Gate refuses a request
when it can read the declared slice's `memory.max` and `memory.current` and the
request exceeds that slice's remaining budget. If those files are hidden or
unreadable, Run-Gate warns and continues under its other shared-infrastructure
checks; it does not establish ancestor or whole-host headroom. Cgprofile reports
a candidate as inconclusive if a shared ancestor limit changes during
execution. Keep the smaller default when that extra capacity is unavailable,
record the effective slice and ancestor limits before a campaign, and serialize
high-memory lanes unless measured headroom supports running them together.

Why each row exists:

- `dev.slice` is one aggregate shield: all dev children together stay below
  the measured IO and root CPU/swap ceilings, protecting production.
- The ordinary child slices have different memory and weight profiles because
  IDE work, background stacks, gates, and the shared builder have different
  latency/failure tradeoffs.
- Gates and BuildKit receive a tighter IOPS sub-ceiling; bandwidth continues to
  inherit the parent pool.
- The guaranteed sibling is inert until a container explicitly joins it and
  claims the shared `MemoryMin` floor.

## Managed BuildKit backend (mandatory)

The installer owns one rootless `mdt-buildkitd.service` in
`dev-buildkitd.slice` and registers one durable Buildx remote builder named
`mdt-managed` at the endpoint published in the host endpoint record. This is the only
normal backend for MDT builds, including release builds. The service is a
plain `docker run --cgroup-parent=dev-buildkitd.slice`; it does not rely on a
Buildx container-driver cgroup option.

The runtime directory is writable while rootless BuildKit creates its socket.
Before systemd reports the service ready, the post-start check makes the
root-owned directory non-writable (mode `0711`), rechecks that the endpoint is
a real socket, verifies its owner from inside the service container's user
namespace, then assigns the socket to host group `docker` with mode `0660`.
The runtime directory is preserved across service stops and restarts so an
already-running devcontainer keeps the same bind-mounted directory inode. The
host reboot recreates it because `/run` is tmpfs. A service start corrects the
directory permissions to `01777` and removes only a stale socket entry before
launching BuildKit; it does not delete or recreate the directory. This closes
a path-replacement race and keeps the owner check valid with Docker
`userns-remap`. Devcontainer
clients must receive the same host group; the directory itself is never made
broadly writable as a substitute for socket permissions.

The installer verifies Docker and Buildx before changing host state, waits for
the service socket, verifies the service container's image/cgroup/labels, then
creates or verifies the remote builder (the root-owned reference in `/etc/mdt/buildx`). Users
do not build with that directory: Buildx writes build state into its config directory on every
build, so each user gets the same `remote` node in their own `~/.docker/buildx`, registered by the
`docker-buildx` shim on first use from the published BuildKit endpoint facts. The installer installs that shim to
`/usr/local/lib/docker/cli-plugins/docker-buildx`, then checks a fresh user with no Buildx or Docker overrides
against the derived endpoint facts (exit 2 otherwise). After the BuildKit
socket is ready, it publishes its host facts in
`/etc/host-endpoints.d/mdt-host-setup.env` with `augeaswrap endpoints publish`;
augeaswrap maintains the aggregate environment files and login-shell loader.
The reasoning is in [docs/BUILD-ARCHITECTURE.md](../docs/BUILD-ARCHITECTURE.md#making-every-build-use-mdt-managed).
The devcontainer template reads `BUILDX_BUILDER`, `BUILDKIT_HOST`,
`MDT_BUILDKIT_SOCKET`, and its directory from that endpoint record, sets its
own persistent `BUILDX_CONFIG` below the already-mounted `/home/vscode/.config`,
and bind-mounts the published directory. The mount is mandatory, so missing
endpoint facts fail container start.

The accidental-worker guard is an event-driven systemd service. It inspects
reserved `buildx_buildkit_*` and `buildkit_buildkit_*` candidates, approves only
the exact managed service identity (name, configured image, cgroup parent, and
labels), and logs every decision. `BUILDX_ACCIDENTAL_CONTAINER_POLICY` is the
closed vocabulary: `report-only` (default) detects and logs without removal;
`terminate` removes an unapproved BuildKit worker and is an explicit opt-in.
The wizard asks for a second confirmation because termination can interrupt a
live Buildx build. Missing or invalid policy configuration stops the guard.
IO caps are handed to systemd as `/dev/<DEVNAME>` of the whole disk (derived from the same MAJ:MIN the
read-back verifies, via sysfs `uevent`; never the configured mapper node), with every IO key reset and set in the same `set-property` call; see
[DESIGN-GUIDE](../docs/DESIGN-GUIDE.md) (MDT-HS-04) for the measurement and why. The installer renders the
`dev.slice` static lines the same way, and the log lines name the path given to systemd. systemd keeps one
limit entry per path spelling and a reset leaves a stale all-max entry that can override the cap while
`systemctl show` looks right; when the kernel disagrees with `show`, the reconcile warns, runs ONE
`systemctl daemon-reload`, re-applies and re-verifies. The reload is rate-limited across runs by a stamp
file (`/run/mdt/io-selfheal-reload.stamp`, 30 minute window, cleared by a clean run); a heal that does not
help fails with the other causes to check (io controller, device, kernel refusal), not another reload.

The guard reads each `docker events` line's container id from `Actor.ID`, the documented
current field: Docker 29 (API 1.56) no longer sends the deprecated top-level `id`/`status`/`from`.
A legacy top-level `id` is accepted only when `Actor` is absent (older daemons) or agrees with it;
a disagreement is refused loudly and neither id is acted on. The command filters on
`type=container`, so a non-container event is logged and ignored. One malformed line does not kill
the guard: it is logged as an ERROR and answered by an immediate full reconcile; five in a row, or 20 within 60 seconds, make
the guard exit so systemd shows the failure. The immediate reconcile is throttled to once per rescan
interval. A container that vanishes between listing and `docker inspect` (`No such container` /
`No such object`) is skipped; any other inspect failure still exits. Events must carry a full
64-hex id and the sweep lists with `ps -aq --no-trunc`. `check.sh` reports the guard as not installed, not running, or active with N
restarts, warns on malformed events in the last hour, and says so when the journal cannot be read. A
`docker rm -f` that finds the container already gone is treated as gone too.

The wizard's second confirmation is recorded durably in
`/var/lib/mdt/guard-terminate.confirmed` (policy, BuildKit image, timestamp, and a digest
of policy + image + this host's machine-id; an audit trail, not an access control).
The guard service verifies the same digest before it acts in terminate mode and runs
report-only, loudly, when there is none; it withdraws a stale record when the policy
is report-only; `mdt-host-check.sh` warns about terminate without a valid record.
Changing the policy, the BuildKit image or the host invalidates the record. A plain re-install with an unchanged, already-confirmed `terminate` proceeds
and keeps the running guard. Only a new or changed `terminate` without a record refuses,
and the refusal happens BEFORE anything is touched: a guard running under an earlier
confirmation is left exactly as it was. Choosing `report-only` removes the record, so a
later `terminate` needs confirming again.

The wizard also configures `DEVCONTAINER_MISSING_BIND_SOURCE_POLICY` for the
host-side `initialize_container_environment.py` that runs before a devcontainer
starts. Before preparing mount sources, that bootstrap derives the slice names
from the vendored `devcontainer.json` and requires each to be loaded from an
installed unit file. It refuses before Docker creates a devcontainer if a
slice is absent, runtime-generated, or unqueryable. Install host setup and run
`mdt-host-check.sh` before rebuilding. Its default, `create-by-spelling`, creates a missing source ending in
`/` as a directory and a missing source without `/` as an empty file with mode
`0600`.
The `fail` option refuses every missing `$HOME` source managed by the bootstrap.
Existing sources are classified from the host filesystem; filenames and suffixes
do not select their type. Keep
the trailing `/` on directory sources in `devcontainer.json` so the bootstrap
can create the intended kind. Docker itself does not interpret this MDT marker.
Change the value by running `sudo ./install.sh --wizard` from this directory on
the Docker host; the bootstrap reads the saved host config at its next run.

The wizard is slice-first. For every governed slice it asks `MemoryMin`,
`MemoryLow`, `MemoryHigh`, and `MemoryMax`; an operator may enter an explicit
empty value where that slice should omit a directive. It explains `MemoryMin`
as hard hierarchical protection, `MemoryLow` as soft best-effort protection,
`MemoryHigh` as soft reclaim throttling, and `MemoryMax` as the hard RAM cap.
It converts systemd binary units to KiB and rejects/re-prompts unless every
configured chain satisfies `Min <= Low <= High <= Max`. A single child
`MemoryHigh` or `MemoryMax` above its configured parent is re-prompted; sibling
`MemoryLow`/`MemoryHigh` totals above the parent are shown as advisory warnings
and are not treated as a summed budget. A child `MemoryMin`/`MemoryLow` without
matching parent protection remains allowed but is reported because it may be
ineffective. Live `MemAvailable` is context only, while starting proposals use
physical `MemTotal`.
`DEV_MEMORY_MIN_GUARANTEED_CEILING` is the single authoritative root `dev.slice`
MemoryMin and is mirrored on the guaranteed sibling; `DEV_MEMORY_LOW/HIGH/MAX`
are the other root controls.
Each child slice flow includes `CPUWeight`, `CPUQuota`, `IOWeight`, swap, and
zswap choices. See [`../docs/BUILD-ARCHITECTURE.md`](../docs/BUILD-ARCHITECTURE.md#managed-buildkit-backend)
for the design decisions and [`../docs/CONSUMERS.md`](../docs/CONSUMERS.md) for
pasteable consumer configuration.

### Host endpoint facts and upgrade refusal

The `mdt-host-setup` endpoint fragment carries the Docker group GID, the live
BuildKit socket path and GID, the Docker API socket path, and the
`BUILDX_BUILDER`/`BUILDKIT_HOST` defaults. Install requires the released
`augeaswrap` 0.1.0 command on `PATH`; `--dry-run-endpoints` prints the exact
fragment derived from the host facts without publishing it. Set
`AUGEASWRAP_BIN` to a command name or path when the CLI is not named
`augeaswrap`. Check the endpoint records with
`sudo augeaswrap endpoints check --json`.

`sudo` resets the environment, so pass the override through it:
`sudo env AUGEASWRAP_BIN=/path/to/augeaswrap ./install.sh` (root's `PATH` is
the `secure_path` of sudoers). Only the command's presence is checked; the
documented `augeaswrap` 0.1.0 is not version-enforced. `--dry-run-endpoints`
needs root, systemd and a live BuildKit socket, so it cannot preview on a host
that has not yet installed the managed BuildKit.

Before changing host state, install refuses if files from the retired
cgprofile/topos provisioning remain, if `/etc/profile.d/mdt-buildkit.sh`
remains, or if `/etc/environment` still has the old `# BEGIN mdt-buildkit`
block. The refusal precedes every mode (including `--admission-only`,
`cpu-benchmark` and `--apply-protection`); `--help` and `--dry-run-endpoints`
run before it. Remove the legacy artifacts only as part of the service cutover
(cgprofile `docs/NATIVE-DEPLOYMENT.md` section 3, sockets plan 5.3), in one
sitting and in this order. The tmpfiles/sysusers rules and the topos drop-in
are what recreate `/run/cgprofile`, `/run/cgprofile/observer`, `/run/topos`
and the topos socket group at boot: after removing them the running old
daemons keep working, but a reboot before `cgprofiled install` and
`toposd install` leaves them without runtime directories, so do not reboot in
between. None of the commands touches `/run/*`, `/opt/cgprofile`,
`/var/lib/*`, groups, containers or game-server state.

```bash
# 1. service files, then reload (required after removing the topos drop-in)
sudo rm -f -- \
  /etc/tmpfiles.d/mdt-cgprofile.conf \
  /etc/tmpfiles.d/mdt-cgprofile-observer.conf \
  /etc/tmpfiles.d/mdt-topos.conf \
  /etc/sysusers.d/mdt-cgprofile.conf \
  /etc/systemd/system/topos.service.d/50-mdt-socket-group.conf \
  /usr/local/sbin/mdt-cgprofile-install.sh
sudo systemctl daemon-reload
# 2. last, immediately before install (new login sessions lose BUILDX_BUILDER/BUILDKIT_HOST until it publishes)
sudo rm -f -- /etc/profile.d/mdt-buildkit.sh
sudoedit /etc/environment  # remove only the old mdt-buildkit block (# BEGIN mdt-buildkit ... # END mdt-buildkit)
# 3. publish (same BuildKit values, now through the fragment), then the service installers
sudo ./install.sh
```

If publication fails after BuildKit was configured (`install.sh` exits with
augeaswrap's code), BuildKit is running with the new shim but the endpoint
record is missing or stale: builds fail with the shim's message until
`install.sh` is re-run (it is idempotent).

#### After the cutover, log in again

The shim no longer carries a socket default: it needs `MDT_BUILDKIT_SOCKET`
(published with `BUILDX_BUILDER` and `BUILDKIT_HOST`) and exits 2 with
"MDT_BUILDKIT_SOCKET is unset" without it. Processes that started before the
upgrade keep the old environment and so fail their builds: log in again and
restart long-lived sessions (the VS Code Remote-SSH server, tmux/screen
sessions) and systemd units or services, which never read `/etc/environment`
and must be given the variables. The devcontainer templates, `.devcontainer`,
`cmru.orchestration.toml` and the run-gate lanes keep their literal defaults
until the phase-2 cutover moves them onto the published facts.

The aggregate `/etc/environment` block and `/etc/profile.d/host-endpoints.sh`
are owned by augeaswrap. To remove host-setup, withdraw its fragment before
disabling services or removing their files; the complete manual sequence is in
[Uninstall](#uninstall).

## dev-gates: why

`dev-gates.slice` is a SIBLING of `dev-interactive.slice`/`dev-background.slice`
under `dev.slice`, not a child of either — every gate/lane container AND
every placed lane leaf (`rg-<token>`, created by a future host-level daemon
under `cgprofile/`, out of this project's own scope) lives
under it instead of sharing `dev-background.slice` with long-running dev
stacks. It is the **admission capacity object**: its `memory.max` is what
`run-gate` (RG-56) and ciu v8 (S16.6.1) key admission decisions on, and its
`memory.pressure` is the pressure signal admission reads — not swap usage
(design doc `run-gate-project/nyxloom-trove/
DESIGN-2026-09-12-liveness-placement-admission.md` D-6/D-19).

Filed from a real, measured incident (2026-08-04): gates used to share
`dev-background.slice` with a ~4 GiB long-running application stack, so a gate
started with only ~2 GiB of headroom before
`dev-background.slice`'s own `memory.high` began throttling it — an unrelated
long-running stack was silently eating a gate's budget. `dev-gates.slice`
gives gates their own ceiling, no `systemd-oomd` kill opt-in (the finite
cap plus swap is the backpressure, D-19), and a `MemorySwapMax` sized against this
host's own real swap capacity rather than dev-background's.

**`dev-gates.slice` contains dev LOAD only — it does not hold the profiler
daemon.** An earlier design draft placed a `dev-infra.slice` for
the profiler daemon here too; that was withdrawn (design amendment A1/D-29,
same doc): the daemon is a host DEPLOYMENT, not dev load, and runs in its own
top-level `cgprofile.slice` — see that project's own docs. `dev.slice`'s job stays
"contain dev load and nothing else."

**Optional per-container memory defaults.** `mdt-container-memory-inotify-watcher.py`
watches `dev-interactive.slice`, `dev-background.slice`, and `dev-gates.slice`
for new `docker-*.scope` units. Its six per-container
`WATCHER_*_MEMORY_HIGH/MAX` values default to `-`, which means no leaf memory
or swap cap. The wizard asks each setting and explains that parent slice limits
continue to govern the whole tier. An operator can set a value for a workload
that needs a separate leaf limit; the wizard requires an explicit confirmation
before applying any non-`-` pair. `WATCHER_GATES_MEMORY_HIGH` and
`WATCHER_GATES_MEMORY_MAX` are its own per-container pair. Interactive and
background have separate `WATCHER_INTERACTIVE_MEMORY_HIGH/MAX` and
`WATCHER_BACKGROUND_MEMORY_HIGH/MAX` pairs, so one workload's setting is not
silently reused for another. Today's run-gate lane containers pass no
`--memory` of their own and are bounded by `dev-gates.slice`'s aggregate
limits. A CIU-managed service with explicit Docker `mem_limit` is left alone;
any CIU `MemoryMin` and an explicitly configured watcher cap must fit together.
`WATCHER_MEMORY_POLICY=disabled` is the default and requires all six values to
be `-`. Set it to `enabled` only when at least one value is configured; the
installer refuses inconsistent policy/value pairs, and the health check
reports if the service is active against a disabled policy. The wizard asks
this service setting explicitly. On upgrades from configs without the policy
key, it preserves existing non-`-` values for review rather than treating them
as defaults to erase.

**Sizing choices (RW-32/D2, 2026-11-24 correction).** No dev-tier template
renders `ManagedOOMMemoryPressure`, `ManagedOOMMemoryPressureLimit` or
`ManagedOOMSwap`. A finite `MemoryMax` plus a large `MemorySwapMax` is the
overcommit design: cgroup v2 reclaims the tier to swap before it OOM-kills;
only exhausted or unreclaimable swap reaches the kernel OOM path. The operator
directive reserves freezing to fleetd at host memory PSI `full avg10 >= 3`.
`CPUWeight=20`/`IOWeight=10` —
identical to `dev-background.slice`'s own — stand as designed (D-19): a
fourth same-weight sibling does lower interactive's share under
simultaneous gates + background contention (the shared non-interactive
weight rises from 20, dev-background alone, to 40 once dev-gates.slice
joins it, so interactive's CPUWeight share drops from `200/220` (~91%) to
`200/240` (~83%) — the same ratio on the IOWeight side, `100/110` to
`100/120`, since both tiers scale interactive's own weight by the same
10x). **That 83%/83% pair is not the actual worst case** (round-2 review
S16): `dev-buildkitd.slice` (`CPUWeight=50`/`IOWeight=50`) and
`dev-memory_min_guaranteed.slice` (declares neither directive, so
systemd's own default of 100 applies to both) are also runnable
siblings. Recomputed from every rendered unit's own weight, all five
siblings contending at once: CPU `200/(200+20+20+50+100)` = **51.3%**
(`200/290` = 69.0% with buildkitd alone added, before the guaranteed
tier's default-100 also joins); IO `100/(100+10+10+50+100)` = **37.0%**.
Accepted because all five tiers rarely saturate CPU/IO simultaneously in
practice, and cgroup weights only bite under real contention in the first
place (an idle sibling claims nothing); revisit once real usage data
exists (design §7), not by guessing further.

Env keys (`host-setup.env.example`): `DEV_GATES_MEMORY_HIGH`/`_MAX`/
`_SWAP_MAX`/`_CPU_WEIGHT`/`_CPU_QUOTA`/`_IO_WEIGHT`/
`_ZSWAP_WRITEBACK`, sized for a 16 GiB host and expected to be re-tuned from
real admission-usage data (design doc §7: "measure first"); also gets a
runtime IOPS sub-ceiling from `DEV_SUBSLICE_IOPS_PCT` (dev.slice section, no
separate gates-specific key). `WATCHER_MEMORY_POLICY` enables/disables the
optional per-container memory watcher; the three `WATCHER_*_MEMORY_HIGH/MAX`
pairs are its per-slice knobs. `CGROUP_PARENT_DEV_GATES=dev-gates.slice`
travels the same export path as `CGROUP_PARENT_DEV_INTERACTIVE`/`_BACKGROUND` —
`templates/devcontainer.json`'s `containerEnv` — and gate consumers require it
before launch. A devcontainer that was not rebuilt simply lacks the variable,
so its gate tools fail before creating a container; it is not allowed to fall
back to the background tier. A host that was not upgraded still needs the
slice unit installed; a `--cgroup-parent` naming a slice with no unit file
fails **open** — systemd auto-creates an
unlimited transient slice and the container starts normally
(`units/docker-scope-default-limits.conf.in`, `AGENTS.md`), which is
exactly why the `docker-.scope.d` backstop exists. It is caught by
`mdt-host-check.sh` (`dev-gates.slice has no unit file`, a hard FAIL) and
by a consumer that verifies `LoadState=loaded` before launch, as
`AGENTS.md` already requires — **not** by docker (round-2 review B8;
round-1 review S10 asked for this rule stated precisely, and the round-1
repair's substitute claim — that `docker run` itself fails outright — was
wrong in the fail-open direction and is withdrawn here).

**Rebuild your devcontainer.** `containerEnv` changes only take effect on
container **recreate** — `CGROUP_PARENT_DEV_GATES` is not visible inside an
already-running devcontainer until it is rebuilt, even on a host that has
fully upgraded. Both operator sequences below end at `mdt-host-check.sh`;
after that, rebuild/recreate the devcontainer too so the new variable
actually reaches whatever tool inside it reads it (round-1 review S10).

**Onward propagation is now wired.** The host setup exports
`CGROUP_PARENT_DEV_GATES`, and the gate consumers resolve and validate it
before launch: `run-gate`, CMRU's `tester-gate`, Assay's tester-unified gate
driver, the `tester-unified/run` launcher, SRDM's gate helper, and the
debian-install-v2 VM runner. Their container argv carries the same value as
both `--cgroup-parent` and the in-container `CGROUP_PARENT_DEV_GATES` fact.
CMRU and tester-unified also forward `CGROUP_PARENT_DEV_BACKGROUND` only when
a gate test deliberately starts a long-running application stack; that stack
then remains in its background tier. Missing gate placement is a hard error,
so no consumer silently returns to `dev-background.slice` or Docker's
unbounded default.

Service endpoint fragments are combined by augeaswrap, but the generic
devcontainer template does not yet deliver cgprofile or topos endpoint
environment and numeric group IDs into its session. The test-host runner
launcher does load and forward its endpoint record. This generic-template gap
is tracked by MDT-086; do not infer that a socket-directory mount alone meets
the Run-Gate or Assay contract. Before changing a host or rolling out an image,
run [`estate_doctor.py`](estate_doctor.py) and read the
[machine-readable estate requirements](../docs/ESTATE-REQUIREMENTS.toml).

`mdt-cockpit/host-setup` no longer creates cgprofile or topos directories,
groups, tmpfiles entries, or service drop-ins. Install those services through
`cgprofiled install` and `toposd install`; each service owns its socket
contract, endpoint fragment, and doctor. See [Host service
installation and endpoint publication](#host-service-installation-and-endpoint-publication)
and the [pasteable service commands](../docs/CONSUMERS.md#host-service-endpoints).

## Changes

**Historical change:** the 2026-09-12 RG-55 rollout added cgprofile socket
mounting and host-setup-owned tmpfiles/group provisioning. H1g' removes that
duplicate ownership. Current service requirements and installation order are
documented in [Host service installation and endpoint publication](#host-service-installation-and-endpoint-publication).

**Fresh host, never ran mdt host-setup before:** run `install.sh --wizard`.
The wizard writes a host-sized candidate in temporary storage, validates it,
and only then lets the installer create `/etc/mdt`, render units, and activate
the policy. A plain first run refuses the intentionally incomplete example and
does not persist it. Only a host that ran `install.sh` BEFORE `dev-gates.slice`
existed needs the upgrade sequence immediately below.

## Upgrading a host that already runs mdt host-setup

**From before `dev-gates.slice` existed:** a plain re-run of `install.sh`
validates the existing config before touching host state and refuses if it is
missing any newly required key. It never replaces an existing config with the
incomplete example. To preserve existing tuning while adding the new fields,
use `--wizard`; it presents existing values as defaults, validates the
completed candidate, then backs up and installs it before rendering:

```bash
sudo cp /etc/mdt/host-setup.env /etc/mdt/host-setup.env.bak-$(date +%F)
diff <(grep -oE '^[A-Z_]+=' host-setup/host-setup.env.example | sort) \
     <(grep -oE '^[A-Z_]+=' /etc/mdt/host-setup.env | sort)      # shows the
                                                                   # new keys
sudo vi /etc/mdt/host-setup.env    # paste in the missing keys (the
                                    # dev-gates.slice block,
                                    # WATCHER_GATES_MEMORY_HIGH/MAX);
                                    # size them for this host
sudo ./install.sh                  # ONE render/install/activate pass --
                                    # now fails if a declared key is missing
sudo mdt-host-check.sh             # verify: dev-gates.slice's effective
                                    # memory.max/high now FAIL loudly (not
                                    # print unbounded silently) if a key is
                                    # still missing; endpoint facts are checked
```
Then rebuild/recreate your devcontainer (above).

`--wizard` with an existing config preserves its values as defaults and, after
the candidate passes validation, backs up and replaces that config. Add
`--reset` only when you deliberately want to start the answers from the
current repository example plus live-host proposals instead, for example to
review every new default after a schema change. The old file is still backed
up; validation happens before either backup or replacement. Deleting the old
file first is different: it loses the source of the old defaults and has no
automatic backup if the wizard is interrupted. The wizard walks every section
it knows, preserves non-prompted values represented by the template, and
rejects per-slice hierarchy violations before rendering. A child
MemoryHigh/MemoryMax larger than its configured parent is re-prompted; sibling
totals larger than the parent are reported for review but do not block
rendering. If the config predates `WATCHER_MEMORY_POLICY`, existing explicit
per-container memory values select `enabled` as the wizard's starting answer;
each pair is still reviewed and confirmed before the candidate is written.

## Quick start

Run the commands below from a shell on the Docker host. Do not run host-setup
from the MDT devcontainer: container root cannot modify the host's systemd,
cgroup, or `/etc` state, and the installer refuses that context.

```bash
sudo ./install.sh --wizard         # preserve current values; derive missing host values
sudo ./install.sh --wizard --reset # deliberately re-seed from example + live host
# or: edit a complete, host-specific /etc/mdt/host-setup.env, then run install.sh
sudo ./install.sh --with-baseline  # re-render + measure disk ceilings (~12 min saturated IO — quiet window!)
sudo mdt-host-check.sh             # verify
```

**Required checkout set.** `install.sh` reads files outside `mdt-cockpit/host-setup/`, so a host checkout of `mdt-cockpit/` alone is not enough. The checkout
must contain, at their repo-relative paths, `mdt-cockpit/` and `scripts/debian-install-v2/tools/` (`iocost_coef_gen.py`, `iocost_plausibility.py`, `cpu_benchmark.py`,
`cpu_benchmark.py.sha256`, `cpu_benchmark.pins.json`). Before changing anything the installer checks every such file (one list, `preflight_checkout_set` in
`install.sh`) and exits 2 naming the missing paths and this set; `--help`, `--dry-run-endpoints`, `--apply-protection`, `cpu-benchmark` (which checks its own three `cpu_benchmark*` files) and the `--admission-only` publish do not need them. The preflight also runs the `cpu_benchmark.py.sha256` check, so a modified tool is refused before anything is rendered.
A sparse checkout therefore needs `git sparse-checkout set mdt-cockpit scripts/debian-install-v2/tools`.

### Host service installation and endpoint publication

`mdt-cockpit/host-setup` no longer provisions cgprofile or topos runtime files,
service settings, or doctor checks. Those services own their lifecycle and
endpoint facts: install them through `cgprofiled install` and `toposd install`;
each service publishes its own fragment through `augeaswrap`. The host-setup
installer publishes only the `mdt-host-setup` fragment after its BuildKit and
Docker API facts are ready. See the
[service endpoint design](../docs/DESIGN-GUIDE.md#host-service-endpoint-ownership)
and [pasteable host service commands](../docs/CONSUMERS.md#host-service-endpoints).

The old `CGPROFILE_*` host-setup keys and the `--observer-only` and
`--cgprofile-only` options have been removed. Old managed files are refused
before host changes; follow the cleanup commands in
[Host endpoint facts and upgrade refusal](#host-endpoint-facts-and-upgrade-refusal).

### run-gate gates admission cap (MDT-077)

run-gate's count admission is a project switch (`[admission] enabled = true` in the consumer's `run-gate.toml`,
dstdns sets it). The cap it enforces is not in any file: it is a Docker-daemon object, a never-started
`ciu-admission-<generation>` container whose label `ciu.admission.tiers.gates.max_concurrent` is the number, and
only `run-gate admission set --max-concurrent N` creates it. A reinstalled host has a new daemon and so no cap,
and every lane of an admission-enabled project then ends `NOT_RUN no-headroom` ("no gates limit is published")
while the host idles. The limit is the host's to state (AGENTS.md, "limits are host-owned"), so host-setup derives
it from the capacity it already enforces on `dev-gates.slice` and publishes it. Section 9 of
`host-setup.env.example` holds the keys; all are optional in an older `/etc/mdt/host-setup.env`.

**Formula**

```
cap = max(1, min(cpu_cap, memory_cap))
cpu_cap    = floor(gates CPU cores / ADMISSION_CPUS_PER_GATE)          # default 2 CPUs per concurrent gate
memory_cap = floor(gates MemoryMax (dedicated-dev) or MemoryHigh / ADMISSION_MEMORY_PER_GATE)   # default 2G per gate; no term when the tier has neither
```

`gates CPU cores` is the effective minimum of the parent `dev.slice` and `dev-gates.slice` CPUQuota. `mdt_capacity`
supplies the auto-derived shares, while explicit `DEV_CPU_QUOTA` and `DEV_GATES_CPU_QUOTA` percentages cap those
shares; `-` omits that level's quota. For example, `DEV_CPU_QUOTA=200%` on an 8-core host limits the gate tier to
2 cores even when its own quota is larger. The gates memory ceiling is what the tier is rendered with: on `HOST_ROLE=dedicated-dev`
its MemoryMax from "Memory of the dedicated-dev role" (`mdt_admission.gates_memory_ceiling()`: dev.slice's Max; that role has
no MemoryHigh), otherwise `DEV_GATES_MEMORY_HIGH`. The defaults are
planning figures, not limits (the tier is overcommitted by design and PSI is what bounds real load): one gate per
2 vCPU is the conservative figure the operator published by hand on v1001, 2G is the memory budget of a typical
lane. Examples: a 4 vCPU / 8 GB dedicated-dev host (v1001, r1002) gives 2, an 8 vCPU / 16 GB one gives 4, a
shared-prod host with the example's `DEV_GATES_MEMORY_HIGH=800M` gives the minimum 1. A host that should run more
(r1002 was hand-raised to 4 for a dstdns campaign) says so in `host-setup.env`: `ADMISSION_MAX_CONCURRENT=4`, or
`ADMISSION_CPUS_PER_GATE=1` and `ADMISSION_MEMORY_PER_GATE=1G` to keep the formula. See the [publication policy rationale](../docs/DESIGN-GUIDE.md#mdt-077-admission-cap-publication-policy)
and [pasteable operator steps](../docs/CONSUMERS.md#run-gate-admission-cap).
`python3 mdt_admission.py derive /etc/mdt/host-setup.env` prints the number and the arithmetic behind it without
touching anything.

**Keys**

| Key | Default | Meaning |
|---|---|---|
| `ADMISSION_PUBLISH` | `auto` | `auto`: a full install publishes only when no readable cap exists and keeps a differing readable cap; `sudo ./host-setup/install.sh --admission-only` reconciles differences. `missing`: publish only when no readable cap exists and never replace. `replace`: full install and `--admission-only` replace differences. `off`: leave the cap alone. |
| `ADMISSION_MAX_CONCURRENT` | `auto` | `auto` = the formula; a whole number of at least 1 pins the desired cap and always takes precedence over the computed value |
| `ADMISSION_UNREADABLE_POLICY` | `refuse` | published with the cap: what a client does when it cannot read one (`refuse` = `NOT_RUN`, `unbudgeted` = run without a ticket) |
| `ADMISSION_CPUS_PER_GATE` / `ADMISSION_MEMORY_PER_GATE` | `2` / `2G` | the formula's two budgets |
| `ADMISSION_COCKPIT` | empty | empty = run-gate on the host; a container name = that running cockpit, as `ADMISSION_COCKPIT_USER` (default `vscode`) |
| `ADMISSION_RUN_GATE` | empty | the run-gate to call (a path on the host, or inside the cockpit); empty = `run-gate` on `PATH` |
| `ADMISSION_TICKET_IMAGE` | empty | a LOCAL image that provides `/bin/true` (run-gate never pulls it); empty = the cockpit's own image. There is no default on a host without a cockpit |

**What runs, when.** The full install runs the step after the tiers and services are up, and
`sudo ./host-setup/install.sh --admission-only` runs only it (strictly validating the installed config first, no wizard, no
other change). It writes a minimal `run-gate.toml` (`[admission]` with the ticket image and policy; no lanes) into
a private temporary directory or, for a cockpit, over stdin into one inside the container, exports
`CGROUP_PARENT_DEV_GATES=dev-gates.slice`, and runs `run-gate admission show` first. A cap equal in value and
policy is left alone (no new generation). A missing cap is published with `run-gate admission set`. Under the
default `auto`, the full install keeps a differing readable cap; the explicit `--admission-only` command reconciles
it with `--replace`. `replace` opts the full install into the same reconciliation, while `missing` never replaces.
The explicit `--admission-only` action also uses `--replace` to repair a published object whose limit cannot be
parsed. The cap is read back after every write. `run-gate` on the HOST is not expected on a runner-container gate worker (for example r1002 after the mdt-cockpit deploy): there run-gate lives inside the cockpit
container (`mdt-cockpit-runner`, see `scripts/netcup/runner/README.md`), so the NOTE `run-gate (...) is not installed on the host` means `ADMISSION_COCKPIT` is still empty.
Set `ADMISSION_COCKPIT=mdt-cockpit-runner` (and `ADMISSION_TICKET_IMAGE` if needed) in `/etc/mdt/host-setup.env`, then run `sudo ./host-setup/install.sh --admission-only`.
On a host that runs gates directly (no cockpit container), run-gate must instead be on `PATH` or named by `ADMISSION_RUN_GATE`.
Prerequisites a fresh host does not have yet (Docker CLI missing,
Docker daemon unavailable, run-gate missing on the host or in the cockpit, cockpit stopped, ticket image absent)
are distinct printed `NOTE`s and exit 0. After reinstall, operators must run
`sudo ./host-setup/install.sh --admission-only` once run-gate and the ticket image exist. A publish that was
attempted and failed (run-gate refuses or read-back differs) is printed with run-gate's reason; the rest of the
install completes and the run exits non-zero.

**Verification.** `mdt-host-check.sh` reads the cap through the same code: FAIL when no cap is published, OK when it
equals the host policy, WARN when it differs, FAIL when it differs while `ADMISSION_MAX_CONCURRENT` is pinned, and
WARN when a prerequisite prevents reading it (naming which). A difference remains a warning under `missing`;
`off` reports INFO. Admission is local to one
Docker daemon: nothing here applies to a remote daemon, and projects that leave `[admission]` off are unaffected
whatever is published.

### Writeback and root-queue settings (MDT-HS-05)

The wizard (step 8, after the IO caps) asks for four host-global keys and install.sh renders, applies and
verifies them: `IO_WRITEBACK_DIRTY_BYTES` and `IO_WRITEBACK_DIRTY_BACKGROUND_BYTES` (`vm.dirty_bytes`,
`vm.dirty_background_bytes`), and `IO_ROOT_QUEUE_NR_REQUESTS` and `IO_ROOT_QUEUE_MAX_SECTORS_KB` (the root disk's
`queue/nr_requests` and `queue/max_sectors_kb`). Each is a positive integer (bytes; KB for `max_sectors_kb`) or the
word `kernel` (leave the kernel's value untouched and render no line; empty means the same). They are
runtime-optional: an env file without them reads as `kernel`. Bounds: `dirty_background_bytes` below `dirty_bytes`,
`nr_requests` at least 4, `max_sectors_kb` between 4 and the disk's `max_hw_sectors_kb` (read from the derived disk at
install time). The wizard proposes from a current benchmark result when one exists (the IOB-02 `result.json`,
`/var/lib/iobench/result.json` or `$IOBENCH_RESULT`; absent means no benchmark), else the existing value, else
`kernel`; the values measured on the reference host are printed as a labelled example, never a default. install.sh writes
`/etc/sysctl.d/90-mdt-io-writeback.conf` (only the set keys) and `/etc/udev/rules.d/61-mdt-io-queue.rules` (matching
`KERNEL==<DEVNAME>` of the whole disk under `/`, derived with the MDT-HS-04 helpers; it sorts after
`60-mdt-bfq-scheduler.rules` because a scheduler change resets `nr_requests`), applies them, reads `/proc/sys/vm/*` and
`/sys/block/<disk>/queue/*` back and exits non-zero on a mismatch, naming the file and the remedy. Only after that does
it retire an interim file (`90-mdt-io-writeback-interim.conf`, `61-mdt-io-queue-interim.rules`), and only one whose
every key (parsed from the interim file) is carried by MDT's own rendered file and verified live; otherwise the interim file
is KEPT with a WARN naming the missing keys and the remedy (run the wizard and set them), because with `kernel` everywhere
it would be the only thing persisting the live values. Values are validated strictly: no leading zeros, `dirty_bytes` at
least two pages (the kernel ignores less), `max_sectors_kb` at least one page, at most the disk's `max_hw_sectors_kb`, and a
multiple of its logical block size. The disk is always the whole disk under `/` (the wizard asks the same derivation as
install.sh and check.sh, not `IO_DEV_PATH`), and a benchmark result is defaulted only when its device number AND at
least one of model/serial/size/wwn match that disk. A
key set back to `kernel` loses MDT's line but its running value cannot be restored (the kernel's boot value is unknown at
runtime): reboot or set it by hand. `mdt-host-check.sh` reports each key as OK, MISMATCH, not managed, could not read, or LIVE ONLY, NOT PERSISTED (in force, but MDT's file is missing), and notices a later `sysctl.d` / `/etc/sysctl.conf` / udev file that sets the same key and would override MDT at boot.
Why these values and what they cost (sequential reads fell from about 1.2-1.4 GB/s to 0.25-0.3 GB/s at 64 KB): see the
DESIGN-GUIDE section on MDT-HS-05 and the reference host's guide at the repository root (`scripts/<host>-guide/HOST-IO-INTERFERENCE.md`, sections 4.2-4.5 and 5).

### Docker restart: the end-of-run reminder and `--restart-docker`

Two of install.sh's changes reach the running `dockerd` only at its next start: the merged
`/etc/docker/daemon.json` keys (cgroup-parent, live-restore, log options) and the additional API
listener `/run/docker-api/docker.sock` (the `docker.socket` drop-in
`/etc/systemd/system/docker.socket.d/50-mdt-dedicated-api-socket.conf`, the directory the devcontainer
template bind-mounts; `dockerd` picks up a newly added listen socket only at its own startup). install.sh
never restarts Docker on its own: it ends with a REMINDER only while something is pending (daemon.json
newer than `docker.service`'s main process, or the socket not listening), so a re-run after the restart
is silent. `--restart-docker` performs `systemctl restart docker.socket docker.service` at the end of
the run instead; it restarts every running container, so schedule it.

### inotify watch budget (MDT-003)

VS Code watches every directory of the open workspace, and all of the host user's inotify consumers share one
`fs.inotify.max_user_watches` budget. The kernel default (about 8k) is exhausted by a large repository, which VS Code
reports as "unable to watch for file changes in this large workspace (ENOSPC)". install.sh copies
`etc/sysctl.d/99-inotify.conf` (524288) to `/etc/sysctl.d/99-mdt-inotify.conf` and applies it with `sysctl -p`
only when the live value is below it (a failure to apply is a warning, the file applies at the next boot);
`mdt-host-check.sh` FAILs when the live value is below the installed file's value and warns when the persisted
file is missing or no longer carries that value. The installed file is unconditional: a host that sets a higher
value in an earlier-sorting `sysctl.d` file is lowered to 524288 at the next boot — raise the value in
`/etc/sysctl.d/99-mdt-inotify.conf` (or an even later file) in that case.
A raised limit is only half the fix: generated trees (git worktrees, run-gate and pytest output) can still consume most
of it. The devcontainer template therefore sets `files.watcherExclude` and `search.exclude` under
`customizations.vscode.settings`; trim those patterns to the consumer repo. The cost: the Source Control view does not
live-refresh inside a worktree under `.worktrees/`. These settings land in the persisted home's
`~/.vscode-server/data/Machine/settings.json`, which a rebuild does not rewrite: after the template block changes, apply
it as `templates/vscode/README.md` ("Template settings and the persisted home") describes.

### IO scheduler and io.cost (MDT-HS-07)

The wizard (step 9, after the writeback keys) asks three more keys and install.sh renders, applies and verifies them:
`IO_ROOT_SCHEDULER` (`mq-deadline`, `none`, `bfq` or `kernel`; it must be one the derived root disk lists in
`queue/scheduler`), `IO_COST_MODEL` (`kernel`, which writes `ctrl=auto`, or the six linear coefficients `rbps rseqiops
rrandiops wbps wseqiops wrandiops`, quoted) and `IO_COST_QOS` (`off`, which writes `enable=0`, or `rpct rlat wpct wlat min
max`, quoted; the policy default is `off`). They are runtime-optional: EMPTY or ABSENT means not managed, an old env file
keeps the legacy BFQ rule and writes no io.cost line until the wizard is run (a NOTICE says so). The wizard proposes the
model from an identity-verified IOB-02 benchmark result, else the existing value, else `kernel`; the reference host's values are a
labelled example only. install.sh renders `60-mdt-io-scheduler.rules` for the derived whole disk (replacing the BFQ rule;
a non-MDT `60-bfq-scheduler.rules` is retired only when it is byte-identical to a known repo copy, otherwise kept with a WARN
and, if it would override, the scheduler is not reported as verified), applies the queue rule (61) after it, then writes
`/sys/fs/cgroup/io.cost.model` and `io.cost.qos` for the whole disk's MAJ:MIN and reads both back field by field. At boot
`mdt-io-cost.service` (a oneshot after the udev trigger and a settle) re-applies them from the env. With io.cost the weight
is `io.weight` (1..10000), not `io.bfq.weight`; `mdt-host-check.sh` reports the weight mechanism that is live (io.cost, bfq or
none), derived from the live files, and the scheduler, model and QoS each as OK, MISMATCH, not managed, could not read or LIVE
ONLY, NOT PERSISTED, plus a NOTICE for a later or non-MDT scheduler rule. A scheduler that `queue/scheduler` does not list yet is accepted when its module is available (the read-back proves it). The minute-by-minute
reconcile verifies the scheduler, the queue values and the io.cost model and QoS against the env and re-applies on drift with a `WARN: drift:`
log line (a disk re-add or a scheduler switch reverts them); empty keys are never touched. Why: DESIGN-GUIDE (MDT-HS-07).

### Memory watermarks and boost (not managed yet)

host-setup does not own `vm.watermark_scale_factor`, `vm.watermark_boost_factor` or `vm.min_free_kbytes` yet; on the reference host they are set by hand
(`wsf` 50, `boost` 0 since 2026-10-20), and a stuck watermark boost once forced all reclaim into the allocating threads. The wizard and
`mdt-host-check.sh` follow-up is recorded as MDT-021 in `KNOWN_ISSUES_TODO_BACKLOG.md`. Why, and the measured failure: DESIGN-GUIDE, "Memory
watermarks and watermark boost are kernel settings this host-setup does not own yet", which points at the canonical record in
the reference host's guide (`scripts/<host>-guide/MEMORY-ARCHITECTURE.md` at the repository root).

### Pausing the re-assert for IO experiments, and restarting mdt-buildkitd (MDT-HS-07)

The minute-by-minute reconcile re-applies the scheduler and queue values (udev trigger), `vm.dirty_*` (`sysctl -p`) and the io.cost model and QoS
(`_mdt_apply_iocost`) whenever they drift from `host-setup.env`, so a hand change is undone within a minute. To run an IO experiment on a managed
host, pause it for a bounded time: `mdt-io-reassert-pause <minutes 1..240> <reason>` (also `--status`, `--clear`). While the pause is valid the
reconcile logs "paused until ... (reason)" every minute and re-asserts none of those four; the io.max caps stay enforced. The pause is the file
`/run/mdt/io-reassert.pause` (`<expiry-epoch> <reason>`, gone at reboot); an expired file is removed and a malformed one is ignored, each with a
WARN, and `mdt-host-check.sh` shows an active pause. The reconcile enforces the 240-minute bound itself (an expiry further out is ignored with a WARN)
and only trusts a regular file (no symlink) owned by the user the reconcile runs as (root) in a directory that is not group/world-writable. A re-assert that did not fix the drift is one ERROR and then backs off for 30 minutes (no
per-minute trigger storm).

`install.sh` restarts `mdt-buildkitd.service` only when its rendered unit/config changed or the running service was started from different content
(the start stamp `/run/mdt/buildkitd.started-digest`; a service that predates the stamp counts as pending once). While a build may be active (any
process other than the idle daemon chain `rootlesskit` -> `exe` -> `buildkitd` (matched by name AND parent chain, as measured with `docker top`) in the buildkitd CONTAINER scope under `dev-buildkitd.slice`, notably any `buildkit-runc`, or the state cannot be read) it is NOT restarted: the
install prints a WARN, keeps the old service and exits 2 until you re-run it with `--restart-buildkitd` at a moment you choose. `mdt-host-check.sh`
and the reconcile report "restart pending" until the running service matches.

### Per-host CPU benchmark: `install.sh cpu-benchmark` (HB-3)

The host's CPU speed is measured ONCE with Geekbench and persisted in host state, so run-gate/cgpd can normalise
timings across machines without guessing from a table. Hosts installed by debian-install-v2 get this from its
`run_cpu_benchmark` step; every other host (the dev host, older r1002/v1001) uses:

```
sudo ./host-setup/install.sh cpu-benchmark            # measure once (several minutes of full CPU, needs internet)
sudo ./host-setup/install.sh cpu-benchmark --resolve  # fill in the scores of a partial record, never runs Geekbench
sudo ./host-setup/install.sh cpu-benchmark --force    # deliberate re-measure (new hardware, resized VM)
```

- It is the same tool as the installer's, never a second implementation: `scripts/debian-install-v2/tools/cpu_benchmark.py`,
  pinned by its `.sha256`. `install.sh` verifies the checkout copy against the pin before installing it to
  `/usr/local/lib/mdt/cpu_benchmark.py`, and the wrapper `/usr/local/sbin/mdt-host-cpu-benchmark.py` verifies it again before
  every run. The Geekbench version and archive sha256 come from `cpu_benchmark.pins.json` (shared with the installer); an
  architecture without a pin is refused. A normal `install.sh` run installs the helper but never measures.
- It refuses when `/var/lib/mdt/host-facts/cpu-benchmark.json` already exists (without `--force`) and while CPU pressure
  (`some avg10`) is 5 or more, even with `--force`. Run it in a quiet window; a measurement on a busy host would be a wrong number.
- Files (root-owned): `cpu-benchmark.json` 0644 (scores, CPU model, vCPU count, conditions, result URL) and
  `cpu-benchmark.claim.json` 0600 (the Geekbench claim link: open it once to attach the result to your account; nothing
  reads it). The result URL identifies the machine on the public Geekbench page: it never goes into a published record.
- `mdt-host-check.sh` ("per-host CPU benchmark") is the doctor: OK when the file matches this host, WARN when it is missing,
  partial (no scores), unreadable, or STALE (the stored `cpu_model` or `cpus_online` differs from the live host after a resize or
  migration; consumers then fall back to the table), with the command to run. It never measures.
- Live checks are tracked as MDT-067 (dev host, then r1002 and v1001) and MDT-068 (aarch64 pin).

### Optional DAMON_STAT policy

The wizard asks whether to disable the kernel-wide `DAMON_STAT` monitor so
cgprofile can use generic DAMON sysfs sessions and collect per-session samples.
mdt defaults to disabling `DAMON_STAT` (operator decision 2026-10-20): the shipped
`host-setup.env.example` has `DAMON_STAT_DISABLE_DEFAULT="yes"`, the wizard's default
answer is `yes`, and the cgroup-profiler daemon needs the DAMON sysfs interface that
`DAMON_STAT` excludes. `no` preserves the kernel's current default. A failed immediate write does not abort the install: it warns loudly and
continues (the boot-time rule is already installed); `install.sh --require-damon-disable` makes it fatal. A `yes` choice
installs `/etc/tmpfiles.d/mdt-damon-stat.conf` and applies it immediately when
the running kernel exposes the parameter. systemd-tmpfiles applies it again at
boot. Changing back to `no` removes MDT's boot override; the current kernel
value remains unchanged until reboot. `mdt-host-check.sh` verifies the
selected state.

Host setup is host-only. Run `install.sh` and the wizard from a shell on the
Docker host being configured, not from the MDT devcontainer. UID 0 in a
devcontainer is container root, not host root, and cannot safely apply the
host's `/etc`, systemd, or cgroup policy. The installer and wizard refuse that
context before reading or writing host-setup state. If a diagnostic run inside
a devcontainer reports `overlay`, stop and leave the container: that is
evidence that the command is looking at the container's mount namespace. From
the host shell, enter a `/dev/...` block-device node if discovery still cannot
expose the Docker-data device. The `/dev/...` validation remains shape-only as
defense in depth; it does not pretend that a path from another namespace can be
`stat`ed locally.

Then recreate the containers that should be governed (placement is
create-time): rebuild the devcontainer, `docker compose up -d --force-recreate`
the test stacks.

## What gets installed

| Artifact | Target | Role |
|---|---|---|
| `units/dev.slice.in`, `units/dev-interactive.slice.in`, `units/dev-background.slice.in`, `units/dev-gates.slice.in`, `units/dev-memory_min_guaranteed.slice.in`, `units/dev-buildkitd.slice.in` | `/etc/systemd/system/*.slice` | the tiers — **rendered** from `/etc/mdt/host-setup.env` |
| `units/mdt-buildkitd.service.in` | `/etc/systemd/system/mdt-buildkitd.service` (rendered, enabled) | host-managed rootless BuildKit worker — `docker run --cgroup-parent=dev-buildkitd.slice` as `ExecStart=`, see `plan-buildkitd-service.md` |
| `units/mdt-damon-stat.conf` | `/etc/tmpfiles.d/mdt-damon-stat.conf` (unless `DAMON_STAT_DISABLE_DEFAULT=no`) | optionally disable the kernel-global DAMON_STAT monitor now and at boot |
| `buildkitd.toml.in` | `/etc/mdt/buildkitd.toml` | managed BuildKit daemon configuration, including `DEV_BUILDKITD_MAX_PARALLELISM` |
| `units/mdt-buildkit-guard.service`, `scripts/mdt-buildkit-guard.py` | systemd (enabled) + `/usr/local/sbin/` | Docker-events guard for unapproved Buildx/BuildKit workers |
| `scripts/mdt_buildkit_builder.py` | `/usr/local/sbin/mdt-buildkit-builder.py` | idempotent `mdt-managed` remote registration/verification; never creates a container-driver worker |
| `scripts/docker-buildx-shim.sh` | `/usr/local/lib/docker/cli-plugins/docker-buildx` | Docker CLI plugin shim: `mdt-managed` is the default builder for every caller, registered per user on first use |
| `scripts/mdt-host-endpoints.sh` | `/etc/host-endpoints.d/mdt-host-setup.env`, through `augeaswrap endpoints publish` | Docker, BuildKit, Docker API, and managed-builder endpoint facts |
| `units/docker-scope-default-limits.conf.in` | `/etc/systemd/system/docker-.scope.d/50-default-limits.conf` | D-G8 backstop — a generous "never truly unbounded" floor for EVERY container's transient scope, regardless of which slice (or none) it named |
| `units/mdt-dev-governance-reconcile.service` | systemd (enabled) | boot-time reconciliation of measured IO, zswap, transient IO caps, mount flags, and the memory min/low audit |
| `units/mdt-dev-governance-reconcile.timer.in` | systemd (enabled) | periodic reconciliation (default 5min) — the event watchers are immediate paths; this is the backstop |
| `units/mdt-container-io-events-watcher.service` | systemd (enabled, unconditional) | `docker events` watcher applying measured IO caps to governed scopes and named out-of-tree Buildx workers |
| `units/mdt-container-memory-inotify-watcher.service` | systemd (enabled only when inotify works, `WATCHER_MEMORY_POLICY=enabled`, and at least one per-container value is explicitly configured) | optional cgroupfs inotify watcher applying configured per-container memory caps to new scopes; shipped policy disables the unit |
| `scripts/mdt-dev-governance-reconcile.sh` | `/usr/local/sbin/` | reconciliation implementation (see below) |
| `scripts/mdt-cgroup-io-limits.sh` | `/usr/local/sbin/` | read-only IO limit listing with EFFECTIVE values and `--systemd` drop-in paths; reads cgroup file *content* (size is always 0) |
| `scripts/mdt-container-io-events-watcher.sh` | `/usr/local/sbin/` | `docker events` event watcher — instant counterpart to reconciliation's per-container IO pass |
| `scripts/mdt-container-io-caps.lib.sh` | `/usr/local/sbin/` | shared `_mdt_*` matching/cap-application functions — sourced by both of the above, one definition of "how a container gets capped" |
| `scripts/mdt_cli.py` | `/usr/local/sbin/` | shared argparse diagnostics imported by installed Python entrypoints |
| `scripts/mdt-container-memory-inotify-watcher.py` | `/usr/local/sbin/` (iff `INOTIFY_OK=1`) | optional inotify watcher for explicitly configured per-container `MemoryHigh`/`MemoryMax`; the periodic sweep does not apply memory caps |
| `scripts/mdt-slice-memory-min-low-audit.py` | `/usr/local/sbin/` | read-only audit of only `memory.min`/`memory.low` — logs a `[WARN]` when an ancestor makes a value ineffective |
| `scripts/mdt-io-baseline.py` | `/usr/local/sbin/` | shared Linux-derived `io.cost` coefficient matrix against a persistent file → `/var/lib/mdt/io-baseline.env` (identity-bound benchmark results) |
| `mdt-host-setup-wizard.py` (a sibling of `install.sh`, not under `scripts/`, because it is never installed onto the host) | not installed — run from this directory via `install.sh --wizard`, or directly as `sudo ./mdt-host-setup-wizard.py` to reconfigure an already-installed host | interactive `/etc/mdt/host-setup.env` builder (see Quick start); template surgery on `host-setup.env.example`, never generated from scratch |
| `mdt_admission.py` | `/usr/local/sbin/mdt_admission.py` | derives the run-gate gates admission cap from host capacity and publishes it (`install.sh`, `install.sh --admission-only`); read by `mdt-host-check.sh` (MDT-077) |
| `scripts/check.sh` | `/usr/local/sbin/mdt-host-check.sh` | health check, non-zero exit on failure |
| `etc/modules-load.d/bfq.conf`, `etc/udev/rules.d/60-bfq-scheduler.rules` | `/etc/…` (`mdt-` prefixed) | the LEGACY BFQ path, kept only while `IO_ROOT_SCHEDULER` is not set; with it set, `60-mdt-io-scheduler.rules` replaces it and the module file is installed only for `bfq` |
| (merged, not copied) | `/etc/docker/daemon.json` | `cgroup-parent` (D-G7 default) only; every key is merged in, nothing else in the file is touched. `live-restore`, log options, address pools and the embedded builder's `builder.gc` cache cap belong to `debian-install-v2` (config keys `docker_builder_gc_max_used_gb`/`_reserved_gb`) |

## Persistence model — why units AND a service/timer

The managed BuildKit service and named remote are persistent state. Re-rendering
the service and restarting it interrupts active builds; the installer says so
and waits for the new socket before verifying/reusing the builder. Existing
`mdt-managed` drift is refused rather than deleted. Schedule the restart, run
host setup, then rebuild devcontainers so their mandatory socket mount and
explicit environment are present. The preserved runtime directory means this
restart does not invalidate an already-running container's bind mount. If a
container was created before this preservation was installed and its mount
source shows `//deleted`, rebuild that container once to bind the current host
directory. A running devcontainer is not required to register the builder.

Reboot-survival works in three layers; each exists because the previous one
cannot express the next:

1. **Static slice units** (`/etc/systemd/system/*.slice`) — memory knobs,
   weights, zswap-writeback policy (systemd ≥ 256), and
   deliberately **tight** static IO caps as boot-window fallback. Survive
   reboot and `daemon-reload` by themselves; zero runtime machinery. Rendered
   from `host-setup.env` at install time so per-host tuning stays in one
   reviewable file.

   The reconcile never withdraws `dev.slice`'s IO limits by clearing them. In
   systemd an empty `IO*Max=` assignment resets the whole list, unit-file values
   included (measured live, MDT-016), so with no usable baseline (missing, stale
   after a kernel update, or implausible) `mdt-dev-governance-reconcile.sh`
   re-asserts the explicit pinned or static values with
   `set-property --runtime` and then reads the kernel `io.max` back; a mismatch
   fails the service. Removing a runtime drop-in and running `daemon-reload` was
   measured NOT to re-apply the unit-file value, which is why re-asserting is the
   mechanism. With no block device discovered it writes nothing: it refuses
   loudly if `dev.slice` carries limits and otherwise has nothing to cap.
   On a no-baseline transition MDT also clears its previously
   applied transient caps from currently governed containers; otherwise an old
   cap would remain indefinitely. Governed containers then receive **no guessed
   per-container IO cap** until valid benchmark results are available. A
   missing or invalid measurement is indeterminate, so the Docker-events watcher remains
   connected but skips cap application, and the periodic sweep applies
   measured caps after the baseline is installed; restart the watcher after
   creating a baseline so its one-time configuration load sees it. The explicit
   `DEV_STATIC_*` values (or the pinned `DEV_IO_*_MAX`) remain the fallback for the
   root unit and are a separate, operator-tunable policy.
2. **Boot service + periodic timer** (`mdt-dev-governance-reconcile.service/.timer` →
   `mdt-dev-governance-reconcile.sh`) — everything units *can't* declare:
   - the **measured** whole-estate IO caps on `dev.slice` (`DEV_IO_CAP_PCT`%
     of the benchmark results — covers all governed children combined) — `systemctl set-property --runtime`,
     reapplied each boot;
   - **per-container** caps for every Docker scope under a governed child
     (`WATCHER_IO_CAP_PCT`% io.max; gates and BuildKit also get `IOWeight=1`).
     Correctly placed containers are selected by cgroup path, not image/name.
     The configured Buildx name pattern is used only for an accidental worker
     outside the governed tree. Docker scopes are *transient*, so no unit file
     can pre-configure them. **This sweep is the
     BACKSTOP, not the primary mechanism:** `mdt-container-io-events-watcher.service`
     applies the same caps within the same second a matching container
     starts, via `docker events` rather than periodic re-scanning — see
     "Reactive counterpart" below for why `docker events` and not inotify
     (the `mdt-container-memory-inotify-watcher.py` pattern). The sweep still matters for
     whatever the watcher missed across its own restart window, and it
     shares every line of matching/application logic with the watcher
     (`mdt-container-io-caps.lib.sh`) so the two can't drift apart. Anything
     `cmru`/mdt itself spawns directly gets explicit
     per-container flags instead (see "Tiering model" above) — this sweep
     (and the watcher) are only for containers nobody's own code controls
     the invocation of;
   - a **cgroup2 mount-flag check**: `memory_recursiveprot` (without which
     every slice-level `MemoryLow`/`MemoryMin` silently stops protecting the
     container pages below it) is a systemd boot default, but a runtime
     remount can strip it — `CGROUP2_FLAGS=warn|fix` in the env file;
   - a **read-only ancestor-chain audit** (`mdt-slice-memory-min-low-audit.py`, a second
     `ExecStart=` on the same service): even with `memory_recursiveprot`
     correctly mounted, `memory.min`/`memory.low` protection is bounded by
     EVERY ancestor cgroup's own value, not just the one it's set on — a
     value declared anywhere under `dev.slice` (a stack's governance config,
     a hand-set property, a future per-container mechanism) is a complete
     no-op if `dev.slice`/`dev-background.slice`/`dev-interactive.slice`
     themselves don't ALSO carry one (which, as shipped, they mostly don't —
     see `CGROUP-NOTES.md`). This never applies anything; it only logs to
     the journal so the gap is discoverable instead of silent.

**Reactive counterpart to layer 2's per-container caps.**
`mdt-container-io-events-watcher.service` (`mdt-container-io-events-watcher.sh`) watches `docker
events` for container starts and applies the same IO cap to every Docker scope
under a governed child within the same second. Only an out-of-tree Buildx
worker is selected by name pattern. The timer sweep above is therefore a
backstop rather than the only mechanism. This is a
DIFFERENT reactive mechanism than `mdt-container-memory-inotify-watcher.py`'s inotify watch
(RW-32/D1, above) on purpose: the memory watcher watches the fixed,
already-known child-slice directories, while the IO watcher must also see an
out-of-tree Buildx worker whose placement is the fault. `docker events` comes
from the daemon's bookkeeping regardless of where that container's cgroup
ended up. Both reactive watchers use `Restart=always`, and the periodic sweep
remains the backstop for their restart windows.
3. **Create-time placement** — the one thing the host cannot do at all:
   containers join their tier only where they are *created*
   (devcontainer.json `runArgs`, compose `cgroup_parent:`, or the Docker
   daemon-wide default in `daemon.json`). Graceful degradation: if the unit
   file is missing, systemd invents a transient *unlimited* slice of the same
   name and the container starts normally — the `docker-.scope.d` backstop
   (D-G8, see "What gets installed") exists specifically to put a floor under
   that failure mode.

**Historical Docker/BuildKit placement finding (verified live,
2026-09-12).** The `daemon.json` `cgroup-parent` default correctly places
ordinary `docker run`/`docker create` containers, but it did not reliably govern
BuildKit's embedded or container-driver worker execution under this host's
systemd cgroup driver. Those paths are not the normal MDT backend anymore.

**Re-measured 2026-11-17 (Docker 29.9.2, `daemon.json` `cgroup-parent: dev-background.slice`).**
A `RUN` step of a build on the embedded `default` builder ran in the literal cgroup directory
`/system.slice/dev-background.slice:docker:<id>`, not in a scope under
`/dev.slice/dev-background.slice`: `cpu.weight=100`, `io.weight=default 100`, `memory.max=max`
(the slice itself has weight 20, IOWeight 10, MemoryMax 10G). Ordinary containers are placed
correctly. This is a Docker behaviour (BuildKit's executor builds the cgroup path itself), not a
host-setup defect: nothing in `dev-background.slice` limits embedded-builder builds, and
`mdt-buildkit-guard` cannot see them because they live inside `dockerd`, not in a
`buildx_buildkit_*` container. Enforcement is therefore by routing only: every MDT caller
exports `BUILDX_BUILDER=mdt-managed`; an explicit `--builder default` bypasses governance. Do not
use it for real builds. Its cache is capped by `builder.gc` in `daemon.json` (owned by `debian-install-v2`),
which bounds disk but not CPU or IO.

**Use `mdt-buildkitd` for MDT builds.** It is a rootless host-managed
`buildkitd` service created with `--cgroup-parent=dev-buildkitd.slice`, and the
`mdt-managed` Buildx remote points at the endpoint published by host-setup.
`templates/devcontainer.json` consumes `BUILDX_BUILDER`, `BUILDKIT_HOST`,
`MDT_BUILDKIT_SOCKET`, and its directory from that record; it keeps only the
container-specific `BUILDX_CONFIG` setting. The in-container finalizer validates
and reuses that remote on every start. A missing or
inconsistent variable, endpoint, builder, or host service is an error; MDT does
not fall through to embedded BuildKit or create a per-container worker.

Alternatives considered for layer 2: a boot-only oneshot misses buildkit
workers created mid-session; a docker-events watcher daemon reacts instantly
but is a long-running process with restart/failure modes — the idempotent
timer sweep was, for a while, the smallest thing that stays correct.
**Sub-interval enforcement did end up mattering (2026-09-12)**:
`mdt-container-io-events-watcher.service` is exactly the docker-events hook this
paragraph anticipated, with the timer kept as backstop — `Restart=always`
plus that backstop is judged sufficient mitigation for the restart/failure-
mode concern that originally held this back (systemd's own restart
machinery, not a hand-rolled retry loop).

Full reasoning for each gap, and why raw cgroupfs writes lose to
`set-property`: [CGROUP-NOTES.md](CGROUP-NOTES.md).

## Host role: shared-prod or dedicated-dev (MDT-TESTHOST-IO)

`HOST_ROLE` in `host-setup.env` (section 4.0) says who else uses the disk. The wizard asks it FIRST.

| role | `dev.slice` absolute IO ceiling | no pin, no measurement |
|---|---|---|
| `shared-prod` (also empty, with a NOTICE) | pin, else `DEV_IO_CAP_PCT`% of a baseline, else static `DEV_STATIC_*` | static fallback applies |
| `dedicated-dev` | pin per value, else the MEASURED iobench cap (`proposals.caps.dev_aggregate` of `/var/lib/iobench/result.json`) | `install.sh` refuses (exit 2), the reconcile keeps the cap in force and fails; the remedy is printed |

Why: on the sole-tenant test hosts r1002 and v1001 the shared-host static defaults (500 IOPS, 150M, gates sub-ceiling 60% = 300 IOPS) throttled
every gate lane together while the CPUs were 70% idle and the disk 1-2% utilised; pinning 8000/4000 IOPS and 300M/250M dropped IO pressure from 52% to 8%.
The goal is high utilisation with responsiveness, so limits are not removed: the ceiling follows the measured capacity (iobench already leaves its
utilisation headroom `U`), and the guards stay: gate/BuildKit IOPS sub-ceilings (`DEV_SUBSLICE_IOPS_PCT`), IOWeight, the PSI guard and the optional
`IO_COST_QOS` latency targets (latency target proposals: backlog MDT-064).

A measured value is used only when the result is schema 1, `run.status=complete`, `quality.plausible`, and its disk identity (devno plus at least one of
model/serial/size/wwn) equals the root disk's; `proven` and `unproven` values count (unproven reasons are shown), `absent` ones do not. The reader is
`scripts/mdt_iobench_caps.py`, installed to `/usr/local/sbin`; the lib resolves it through `_mdt_load_iobench_caps`. Measure with
`iobench calibrate` (`libraries/iobench/docs/CONSUMERS.md` section 2) or pin `DEV_IO_*_MAX`. Producing the result during debian-install-v2 is backlog MDT-063;
until then run the calibration by hand on the test hosts.

### Memory of the dedicated-dev role (MDT-TESTHOST-MEM, P-plat-19e)

Full design, kernel semantics and review dispositions: `docs/DESIGN-DEDICATED-DEV-MEMORY.md`. Operator requirement: protect only the host's basic services, put
no `MemoryHigh` on test containers, give unprotected memory to the load and let swap absorb the rest. `shared-prod` and an empty role keep sections 2.4 and 3
of the env file exactly as configured; nothing below applies to them (new `mdt-host-check.sh` findings are WARN there).

**Host reserve K = F + M.** F = the `MemoryMin` floors (the table below, the derived `system.slice` sum, cgprofile.slice's own 128M, `user.slice` 64M = 565M
today), M = `DEV_HOST_MEMORY_MARGIN` (default 512M, at least 64M; unmeasured until the acceptance run) so K = 1077M. `DEV_HOST_MEMORY_KEEP` is only a pin of K now
(RAM kept outside dev.slice, not RAM+swap as before; install.sh prints a NOTICE when it is set; refused below F + 128M). Refused before any write: F above 10% of
MemTotal, a derived `system.slice` floor above 460M, a `DEV_MEMORY_MAX` pin above `MemTotal - MDT_EXCLUDED_RAM - K`, `DEV_INTERACTIVE_MEMORY_MIN` above its Low.

| control | shared-prod | dedicated-dev |
|---|---|---|
| `dev.slice` MemoryMax | `DEV_MEMORY_MAX` (empty = none) | pin, else `MemTotal - MDT_EXCLUDED_RAM - K` (r1002: 7191363584 = 6858.22 MiB); above it the tree pages |
| `dev.slice` MemoryHigh | `DEV_MEMORY_HIGH` | none (a High throttles while RAM is free); `DEV_MEMORY_HIGH` and the tier HIGH pins are ignored and named |
| `dev.slice` MemoryMin / MemoryLow | guaranteed ceiling / `DEV_MEMORY_LOW` | the sum of every child Min key (cockpit 512M, `DEV_INTERACTIVE_MEMORY_MIN`) / the sum of the tiers' MemoryLow |
| `dev-gates` / `dev-background` MemoryMax | `DEV_{GATES,BACKGROUND}_MEMORY_MAX` | pin (at most dev's Max), else dev.slice's own MemoryMax (P-plat-19h: no minus-P; the interactive `MemoryLow` protection is unchanged) (r1002 design inputs: 7191363584 = 6858.22 MiB; today's pinned r1002, `DEV_HOST_MEMORY_KEEP=1560M`: 6684901376 = 6375.22 MiB); no MemoryHigh |
| `dev-gates` / `dev-background` MemorySwapMax | `..._MEMORY_SWAP_MAX` | pin, else dev swap max minus P (floor 256M) |
| `dev-interactive` / `dev-buildkitd` | per tier keys | no ceiling of their own (bounded by dev.slice); swap equals dev.slice's; `..._HIGH/_MAX` ignored |
| ssh, docker, containerd, journald, mdt-psi-guard, cgprofiled MemoryMin | untouched | `95-mdt-memory-min.conf` per unit: 10M, 210M, 92M, 24M, 5M, 4M (`DEV_HOST_MEMORY_MIN` = `unit=size` rows, `-` = none); raise-only against a higher drop-in |
| `system.slice` / `user.slice` MemoryMin | untouched | 373M (the post-raise sum of its units + 32M) / 64M |
| `system.slice` MemoryLow | untouched | derived from K, never below its Min (equal at defaults: no line) |
| host services (`DEV_HOST_PROTECTED_UNITS`; never ssh) | untouched | OOMScoreAdjust=-900 drop-in |
| `docker-.scope` | MemoryMax 12G, swap 16G | MemoryMax **8G** (empty `DOCKER_SCOPE_BACKSTOP_MEMORY_MAX` renders 8G here, 12G on every other role; an explicit value wins), swap 16G, `OOMPolicy=kill` |
| cockpit devcontainers | oom_score_adj 0 | `--oom-score-adj=-900` (devcontainer.json runArg `${localEnv:MDT_COCKPIT_OOM_SCORE_ADJ:0}`; host-setup publishes -900 on this role only; create-time, rebuild an existing cockpit) |
| mdt-psi-guard | weights, then tier `memory.high` | weights only (`HIGH_TIERS` empty: no MemoryHigh stage) |

Where a container's memory goes: its own `--memory` if declared (runc writes it into the scope's `memory.max` even though systemd's property reads 8G; the
acceptance run checks a `--memory 2G` container reads 2G before and after a daemon-reload), else dev Max (every dev tier: the gates/background tier Max equals it); the 8G scope default never binds RAM on 8 GiB hosts. An undeclared runaway fills dev.slice's RAM
and pages its unprotected dev neighbours (the other load tier included); at 16G of its own swap dev.slice OOMs and the victim is normally the runaway, whose whole container dies (`OOMPolicy=kill` sets `memory.oom.group=1` on every `docker-<id>.scope`; needs systemd >= 253,
otherwise skipped with a NOTICE). **For the cockpit container this means** an OOM kill of any process in it kills the whole cockpit: its protection is the Min
floor, the Low, the -900 score (every OOM domain picks a load task first) and no tier ceiling, not a per-process exemption. Running containers keep their old scope
value until re-created; nothing lowers scopes live.

Admission's memory term keeps existing: on this role `gates_memory_ceiling()` returns the gates MemoryMax (r1002: floor(7191363584 / 2G) = 3 on the design inputs, floor(6684901376 / 2G) = 3 at the pinned 6375M dev.slice), other roles
`DEV_GATES_MEMORY_HIGH`. PSI `full avg10 >= 3` stays the stop signal; fleetd's freeze is backlog P-fd-05.

**Converge.** `mdt-dev-governance-reconcile.sh` (run by install.sh after the reload, and by the timer) makes the kernel agree with systemd's properties for dev.slice, its
tiers, system.slice, user.slice and the floored units: it writes the cgroup file directly, reads it back, never `set-property`, never touches a docker scope, and skips
a key psi-guard owns. A lower `memory.max` is written only when `memory.current` is at least 64M below it; otherwise `memory.reclaim` runs (chunks of 256M, at most 8 per
run; for dev.slice the load tiers dev-gates and dev-background are reclaimed, never dev.slice itself, because the cockpit lives there) and the lowering is deferred
(`/run/mdt/reconcile/memory-deferred`; the timer retries). The 64M rule protects only the reconcile's own write: a `systemctl daemon-reload` (install.sh's, the IO reload, an
apt hook) can make systemd itself write the lower `memory.max` below `memory.current`, and the kernel then reclaims and OOM-kills in that cgroup. That is why a lowering
is only done with the lanes drained (see the migration below).

**Do not hand-set memory properties.** No `systemctl set-property` (runtime files under `/run/systemd/system.control/` outrank `/etc`), no `systemctl revert` for
memory or oomd keys on system.slice, user.slice or the dev slices: `mdt-host-check.sh` FAILs on such files on this role (WARN elsewhere) and on a foreign memory
drop-in (`90-vbpub-memory-min.conf`, MemoryMin only, is allowed; the runner's `20-memory-policy.conf` is a WARN naming `provision-gates-limits.sh`).

**Migrating an installed host** (main controller, root; r1002): (0) only on `HOST_ROLE=dedicated-dev`; drain the lanes AND HOLD the drain until step 4 is verified (no run-gate or mutation lane, vm-lab or edge demo stack in dev-gates or
dev-background; a reload, install.sh's or the reconcile timer's IO reload, may apply the lower `memory.max` itself); (1) `mdt-host-check.sh` and
`grep -rs ManagedOOM /etc/systemd/system/{system,user,dev*}.slice.d/`: nothing may set `kill`; (2) copy aside, then delete, the memory and oomd keys of the runtime files of
`{system,user,dev*}.slice.d` under BOTH `/run/systemd/system.control/` and `/etc/systemd/system.control/` (the places `mdt-host-check.sh` FAILs on; files with other content are
left for hand handling; never `systemctl revert`, no hand daemon-reload); (3) in `/etc/mdt/host-setup.env` SET `DEV_MEMORY_HIGH=`, `DEV_MEMORY_MAX=`, `DEV_GATES_MEMORY_HIGH=`, `DEV_GATES_MEMORY_MAX=`,
`DEV_BACKGROUND_MEMORY_HIGH=`, `DEV_BACKGROUND_MEMORY_MAX=` and `DOCKER_SCOPE_BACKSTOP_MEMORY_MAX=` (or `8G`) to empty and never delete those lines (install.sh refuses an env that lacks a
declared key; an old example's `DEV_BACKGROUND_MEMORY_MAX=8G` is refused and its `DEV_GATES_MEMORY_MAX=1.5G` would silently pin the gates), empty `DEV_HOST_MEMORY_KEEP=`, leave
`DEV_HOST_MEMORY_MIN` and `DEV_HOST_MEMORY_MARGIN` empty (append after a trailing newline), keep `MDT_PSI_GUARD=report`; then derive the targets with
`python3 mdt_capacity.py dedicated-memory /etc/mdt/host-setup.env --meminfo /proc/meminfo` and check each `memory.current` <= target - 64M; (4) `sudo ./install.sh`; the dev (7.5 to
6.70 GiB) lowering is safe because of step 0 (the tiers now equal dev's Max, 5.5 to 6.70 GiB: a raise); the converge defers its own write if a lane is still above the target and the timer finishes it; (5) rebuild the cockpit devcontainer when convenient; (6) the acceptance and measurement
run of the design note (section 4.1 load and window, then write the measured floors, `DEV_HOST_MEMORY_MARGIN` and `DEV_HOST_MEMORY_MEASURED`, re-run install.sh; on failure pin
`DEV_HOST_MEMORY_KEEP=` at the K the eval prints in its FAIL header and file the result). `mdt-host-check.sh` NOTEs while `DEV_HOST_MEMORY_MEASURED` is empty.

ssh is deliberately not protected by a score: sshd restores the OOM score it started with into every login session, so `OOMScoreAdjust=-900` on `ssh.service` would make every login
shell -900 (`DEV_HOST_PROTECTED_UNITS` refuses ssh/sshd; ssh gets a `MemoryMin` floor instead). install.sh removes a stale `ssh.service.d/50-mdt-dedicated-dev.conf` an earlier
version rendered. Drop-ins are mode 0644. A killed lane is an infrastructure event (its history record survives), never a product verdict. No dev tier opts into systemd-oomd.
zswap (zstd, `max_pool_percent` 25, writeback `yes` on every tier) is unchanged. Open and unmeasured (R3, MDT-072): `io.max` on dev.slice throttles swap writeback, so reclaim at a
`memory.max` can exhaust its retries on pages under writeback; the acceptance run gates it.

### Acceptance and measurement kit of the dedicated-dev memory model (P-plat-19m, v2: the squeeze test and the DAMON floors)

Step 6 of the migration, root on the host (details: the header of each script, its `usage` on an unknown argument, `accept-eval.py --help`). Keep admission and the edge demo stacks drained
(section 8 step 0) and start exactly one gate lane by hand during the run; the run itself holds `flock /run/mdt-accept.lock` for its whole length, which pauses the reconcile's
memory block with a NOTE and makes `install.sh` refuse (exit 3, nothing done) instead of writing a lower `memory.max` under a live squeeze.

The design is `docs/DESIGN-ACCEPT-KIT-V2.md`. v1 (P-plat-19g) put two capped probes under a 30 min window and judged refaults; v2 inflates **one** dummy by 128M per step with
`memory.min` keeping its protected part resident, judges **pressure and ssh/service latency** per step (refaults are now `INFO` only), and takes the floors from DAMON's
`sz_ops_filter_passed` per leaf.

Before step 0, stop the reconcile timer on r1002 and verify the reconcile service is inactive. Keep the timer stopped through setup, the run and restore. If any command
refuses or exits before the post-run block, do not continue: finish `accept-restore.sh` first if the unit started or its WAL remains, then run
`systemctl start mdt-dev-governance-reconcile.timer` and `systemctl start mdt-dev-governance-reconcile.service`; verify the timer with
`systemctl is-active mdt-dev-governance-reconcile.timer`.

   ```bash
   set -euo pipefail
   systemctl stop mdt-dev-governance-reconcile.timer
   TIMER_STATE=$(systemctl is-active mdt-dev-governance-reconcile.timer 2>&1 || true)
   [ "$TIMER_STATE" = inactive ] || { echo "reconcile timer state is $TIMER_STATE, expected inactive" >&2; exit 1; }
   SERVICE_STATE=$(systemctl is-active mdt-dev-governance-reconcile.service 2>&1 || true)
   [ "$SERVICE_STATE" = inactive ] || { echo "reconcile service state is $SERVICE_STATE, expected inactive" >&2; exit 1; }
   ```

0. **Deliver the exact source archive from the cockpit.** The host has no git checkout. Use the full commit SHA that will be run, and archive the three required trees from the cockpit checkout:

   ```bash
   set -euo pipefail
   REPO=/workspaces/vbpub
   SHA=<full-40-character-commit-sha>
   ARCHIVE_NAME="vbpub-accept-$SHA.tar"
   RESOLVED=$(git -C "$REPO" rev-parse "$SHA^{commit}" 2>/dev/null || true)
   [ "$RESOLVED" = "$SHA" ] || { echo "commit $SHA is not present in $REPO" >&2; exit 1; }
   git -C "$REPO" archive --format=tar "$SHA" mdt-cockpit scripts/debian-install-v2/tools scripts/damon-analysis/lib >"/tmp/$ARCHIVE_NAME"
   (cd /tmp && sha256sum "$ARCHIVE_NAME" >"$ARCHIVE_NAME.sha256")
   scp "/tmp/$ARCHIVE_NAME" "/tmp/$ARCHIVE_NAME.sha256" root@r1002:/root/
   ```

   On r1002, verify the transferred archive before extracting it. `sha256sum -c` is captured so an outer `set -e` does not stop before the explicit refusal:

   ```bash
   set -euo pipefail
   SHA=<same-full-40-character-commit-sha>
   ARCHIVE_NAME="vbpub-accept-$SHA.tar"
   VERIFY=$(cd /root && sha256sum -c "$ARCHIVE_NAME.sha256" 2>&1 || true)
   [ "$VERIFY" = "$ARCHIVE_NAME: OK" ] || { echo "archive checksum failed: $VERIFY" >&2; exit 1; }
   CK="/root/vbpub-accept-${SHA:0:12}"
   [ ! -e "$CK" ] || { echo "$CK already exists; inspect it and choose a fresh evidence checkout path" >&2; exit 1; }
   mkdir -m 700 "$CK"
   tar -xf "/root/$ARCHIVE_NAME" -C "$CK"
   ```

   This archive contains `mdt-cockpit/` (the kit and `mdt_capacity.py`), `scripts/debian-install-v2/tools/` (`install.sh` exits 2 without it) and
   `scripts/damon-analysis/lib/` (the DAMON `SysfsInterface`). Do not substitute a host checkout or a different SHA.

1. **One-time loopback SSH prerequisite.** Run on r1002 as root. This dedicated key is used only by the kit. Its `authorized_keys` entry is restricted to loopback and has no forwarding permissions;
   the known-hosts key comes from this host's `/etc/ssh/ssh_host_ed25519_key.pub`:

   ```bash
   set -euo pipefail
   ROOT_UID=$(id -u 2>/dev/null || true)
   [ "$ROOT_UID" = 0 ] || { echo "run the SSH prerequisite as root" >&2; exit 1; }
   SSH_KEYGEN=$(command -v ssh-keygen 2>/dev/null || true)
   [ -n "$SSH_KEYGEN" ] || { echo "ssh-keygen is missing" >&2; exit 1; }
   install -d -m 700 /root/.ssh
   KEY=/root/.ssh/mdt-accept-loopback_ed25519
   AUTH=/root/.ssh/authorized_keys
   KNOWN=/root/.ssh/known_hosts
   [ -s "$KEY" ] || "$SSH_KEYGEN" -q -t ed25519 -N '' -f "$KEY"
   touch "$AUTH" "$KNOWN"
   chmod 600 "$AUTH" "$KNOWN"
   read -r KEY_TYPE KEY_DATA _ <"$KEY.pub" || { echo "the dedicated public key is unreadable" >&2; exit 1; }
   AUTH_LINE="from=\"127.0.0.1,::1\",no-agent-forwarding,no-port-forwarding,no-X11-forwarding $KEY_TYPE $KEY_DATA mdt-accept-loopback"
   AUTH_FOUND=$(grep -F -x -- "$AUTH_LINE" "$AUTH" 2>/dev/null || true)
   [ "$AUTH_FOUND" = "$AUTH_LINE" ] || printf '%s\n' "$AUTH_LINE" >>"$AUTH"
   [ -r /etc/ssh/ssh_host_ed25519_key.pub ] || { echo "host Ed25519 key is missing" >&2; exit 1; }
   read -r HOST_TYPE HOST_DATA _ </etc/ssh/ssh_host_ed25519_key.pub || { echo "host Ed25519 key is unreadable" >&2; exit 1; }
   HOST_LINE="127.0.0.1 $HOST_TYPE $HOST_DATA"
   HOST_FOUND=$(grep -F -x -- "$HOST_LINE" "$KNOWN" 2>/dev/null || true)
   [ "$HOST_FOUND" = "$HOST_LINE" ] || printf '%s\n' "$HOST_LINE" >>"$KNOWN"
   LOCAL_BOOT=$(tr -d '[:space:]' </proc/sys/kernel/random/boot_id 2>/dev/null || true)
   REMOTE_BOOT=$(ssh -i "$KEY" -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10 root@127.0.0.1 cat /proc/sys/kernel/random/boot_id 2>/dev/null || true)
   [ -n "$LOCAL_BOOT" ] && [ "$REMOTE_BOOT" = "$LOCAL_BOOT" ] || { echo "loopback SSH did not return this host's boot_id" >&2; exit 1; }
   ```

2. **Read-only preflight** (design §7 step 2). Keep admission and the edge demo stacks drained and **hold** the drain. Critical preconditions below stop explicitly; read-only
   probes that can return non-zero are captured so the controller's outer `set -e` does not abort before the check:

   ```bash
   set -euo pipefail
   SHA=<same-full-40-character-commit-sha>
   CK="/root/vbpub-accept-${SHA:0:12}"
   K="$CK/mdt-cockpit/host-setup/tools/accept"
   KEY=/root/.ssh/mdt-accept-loopback_ed25519
   PATH=/opt/mdt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
   [ -f "$K/accept-hotset.py" ] || { echo "archived accept-hotset.py is missing from $K" >&2; exit 1; }
   PYTHON=$(command -v python3 2>/dev/null || true)
   [ -n "$PYTHON" ] || { echo "python3 is missing from PATH" >&2; exit 1; }
   ENDPOINTS=/etc/host-endpoints.env
   [ -r "$ENDPOINTS" ] || { echo "$ENDPOINTS is missing or unreadable" >&2; exit 1; }
   for name in CGPROFILE_CONTROL_SOCKET CGPROFILE_CONTROL_SOCKET_DIR CGPROFILE_CONTROL_GID CGPROFILE_OBSERVER_SOCKET CGPROFILE_OBSERVER_SOCKET_DIR CGPROFILE_OBSERVER_GID; do unset "$name"; done
   set -a
   . "$ENDPOINTS" || { echo "could not read $ENDPOINTS" >&2; exit 1; }
   set +a
   [ -n "${CGPROFILE_CONTROL_SOCKET:-}" ] || { echo "$ENDPOINTS does not define CGPROFILE_CONTROL_SOCKET" >&2; exit 1; }
   HOST_CHECK_CMD=$(command -v mdt-host-check.sh 2>/dev/null || true)
   [ -n "$HOST_CHECK_CMD" ] || { echo "mdt-host-check.sh is missing from PATH" >&2; exit 1; }
   HOST_FACTS=$(grep -E '^(HOST_ROLE|MDT_PSI_GUARD|DEV_MEMORY_MAX)=' /etc/mdt/host-setup.env 2>/dev/null || true)
   printf '%s\n' "$HOST_FACTS"
   grep -F -x 'HOST_ROLE=dedicated-dev' /etc/mdt/host-setup.env >/dev/null 2>&1 || { echo "HOST_ROLE must be dedicated-dev" >&2; exit 1; }
   grep -F -x 'MDT_PSI_GUARD=report' /etc/mdt/host-setup.env >/dev/null 2>&1 || { echo "MDT_PSI_GUARD must be report" >&2; exit 1; }
   grep -F -x 'DEV_MEMORY_MAX=' /etc/mdt/host-setup.env >/dev/null 2>&1 || { echo "DEV_MEMORY_MAX must be empty" >&2; exit 1; }
   HOST_CHECK_RESULT=$({ mdt-host-check.sh; printf '\n__HOST_CHECK_RC=%s\n' "$?"; } 2>&1 || true)
   HOST_CHECK_RC=$(printf '%s\n' "$HOST_CHECK_RESULT" | sed -n 's/^__HOST_CHECK_RC=//p' | tail -n 1 || true)
   HOST_CHECK=$(printf '%s\n' "$HOST_CHECK_RESULT" | sed '/^__HOST_CHECK_RC=/d' || true)
   printf '%s\n' "$HOST_CHECK"
   [ "$HOST_CHECK_RC" = 0 ] || { echo "mdt-host-check.sh exited ${HOST_CHECK_RC:-unknown}" >&2; exit 1; }
   HOST_FAILS=$(printf '%s\n' "$HOST_CHECK" | grep -E '(^|[[:space:]])FAIL([[:space:]]|$)' || true)
   [ -z "$HOST_FAILS" ] || { echo "mdt-host-check.sh reported FAIL" >&2; exit 1; }
   TIMER_STATE=$(systemctl is-active mdt-dev-governance-reconcile.timer 2>&1 || true)
   [ "$TIMER_STATE" = inactive ] || { echo "reconcile timer state is $TIMER_STATE, expected inactive" >&2; exit 1; }
   SERVICE_STATE=$(systemctl is-active mdt-dev-governance-reconcile.service 2>&1 || true)
   [ "$SERVICE_STATE" = inactive ] || { echo "reconcile service state is $SERVICE_STATE, expected inactive" >&2; exit 1; }
   for s in dev.slice dev-gates.slice dev-background.slice; do
     RELOAD=$(systemctl show -p NeedDaemonReload --value "$s" 2>&1 || true)
     [ "$RELOAD" = no ] || { echo "$s NeedDaemonReload=$RELOAD, expected no" >&2; exit 1; }
   done
   DEFERRED=$(ls /run/mdt/reconcile/memory-deferred 2>/dev/null || true)
   [ -z "$DEFERRED" ] || { echo "memory reconcile is deferred: $DEFERRED" >&2; exit 1; }
   DAMON_STAT=$(cat /sys/module/damon_stat/parameters/enabled 2>/dev/null || true)
   [ -z "$DAMON_STAT" ] || [ "$DAMON_STAT" = N ] || { echo "DAMON_STAT=$DAMON_STAT, expected N or absent" >&2; exit 1; }
   CGPROFILE_RESULT=$({ cgprofilectl --json status; printf '\n__CGPROFILE_RC=%s\n' "$?"; } 2>&1 || true)
   CGPROFILE_RC=$(printf '%s\n' "$CGPROFILE_RESULT" | sed -n 's/^__CGPROFILE_RC=//p' | tail -n 1 || true)
   CGPROFILE_STATUS=$(printf '%s\n' "$CGPROFILE_RESULT" | sed '/^__CGPROFILE_RC=/d' || true)
   printf '%s\n' "$CGPROFILE_STATUS"
   [ "$CGPROFILE_RC" = 0 ] || { echo "cgprofilectl --json status exited ${CGPROFILE_RC:-unknown}" >&2; exit 1; }
   LIVE=$(printf '%s\n' "$CGPROFILE_STATUS" | sed -nE 's/.*"sessions_live"[[:space:]]*:[[:space:]]*"?([0-9]+)"?.*/\1/p' | tail -n 1 || true)
   [ "$LIVE" = 0 ] || { echo "cgprofilectl did not confirm sessions_live=0" >&2; exit 1; }
   LOCAL_BOOT=$(tr -d '[:space:]' </proc/sys/kernel/random/boot_id 2>/dev/null || true)
   REMOTE_BOOT=$(ssh -i /root/.ssh/mdt-accept-loopback_ed25519 -o IdentitiesOnly=yes -o BatchMode=yes -o ConnectTimeout=10 root@127.0.0.1 cat /proc/sys/kernel/random/boot_id 2>/dev/null || true)
   [ -n "$LOCAL_BOOT" ] && [ "$REMOTE_BOOT" = "$LOCAL_BOOT" ] || { echo "loopback SSH did not return this host's boot_id" >&2; exit 1; }
   UNITS='docker.service=system.slice/docker.service ssh.service=system.slice/ssh.service containerd.service=system.slice/containerd.service systemd-journald.service=system.slice/systemd-journald.service mdt-psi-guard.service=system.slice/mdt-psi-guard.service cgprofiled.service=cgprofile.slice/cgprofiled.service user.slice=user.slice dev-interactive.slice=dev.slice/dev-interactive.slice'
   "$PYTHON" "$K/accept-hotset.py" preflight --evidence /root/accept-preflight --units "$UNITS" 2>&1 || { echo "accept-kit read-only preflight refused; no acceptance run was started" >&2; exit 1; }
   IMAGE_RC=0
   IMAGE_ID=$(docker image inspect tester-unified:local --format '{{.Id}}' 2>&1) || IMAGE_RC=$?
   [ "$IMAGE_RC" = 0 ] && [ -n "$IMAGE_ID" ] || { echo "tester-unified:local image inspect failed (rc $IMAGE_RC): $IMAGE_ID" >&2; exit 1; }
   ```

   The kit repeats its SSH, cgprofile, DAMON_STAT, DAMON sysfs presence/ownership, and DAMON capability checks read-only immediately before its first sysfs write.

   **Snapshot before the run.** This block is standalone; run it as root on r1002 after preflight:

   ```bash
   set -euo pipefail
   snap() {
     for s in dev.slice dev.slice/dev-gates.slice; do for f in memory.max memory.min memory.current; do V=$(cat "/sys/fs/cgroup/$s/$f" 2>&1 || true); echo "$s/$f=$V"; done; done
     for s in dev.slice dev-gates.slice; do V=$(systemctl show -p MemoryMax -p MemoryMin -p NeedDaemonReload "$s" 2>&1 || true); printf '%s\n' "$V" | sed "s/^/$s /"; done
     d=/sys/kernel/mm/damon/admin/kdamonds; NR=$(cat "$d/nr_kdamonds" 2>&1 || true); echo "nr_kdamonds=$NR"
     for k in "$d"/[0-9]*; do V=$(cat "$k/state" 2>&1 || true); if [ -n "$V" ]; then echo "$k/state=$V"; fi; done
     docker inspect mdt-accept-squeeze --format '{{.State.Status}}' 2>&1 | tail -n 1 || true
     systemctl is-active mdt-dev-governance-reconcile.timer 2>&1 || true
   }
   snap > /root/accept-snap-before.txt
   ```

3. **The squeeze run**, launched detached through `systemd-run`. This gives the unit the host endpoint variables from `/etc/host-endpoints.env`; transient units do not inherit them.
   The repository defines that record path in `.devcontainer/initialize_container_environment.py` (`ENDPOINT_RECORD_PATH`). The command requires `CGPROFILE_CONTROL_SOCKET` (the client-required key) and forwards every other published cgprofile endpoint key when present:

   ```bash
   set -euo pipefail
   SHA=<same-full-40-character-commit-sha>
   CK="/root/vbpub-accept-${SHA:0:12}"
   K="$CK/mdt-cockpit/host-setup/tools/accept"
   ENDPOINTS=/etc/host-endpoints.env
   [ -r "$ENDPOINTS" ] || { echo "$ENDPOINTS is missing or unreadable" >&2; exit 1; }
   for name in CGPROFILE_CONTROL_SOCKET CGPROFILE_CONTROL_SOCKET_DIR CGPROFILE_CONTROL_GID CGPROFILE_OBSERVER_SOCKET CGPROFILE_OBSERVER_SOCKET_DIR CGPROFILE_OBSERVER_GID; do unset "$name"; done
   set -a
   . "$ENDPOINTS" || { echo "could not read $ENDPOINTS" >&2; exit 1; }
   set +a
   [ -n "${CGPROFILE_CONTROL_SOCKET:-}" ] || { echo "$ENDPOINTS does not define CGPROFILE_CONTROL_SOCKET" >&2; exit 1; }
   CGPROFILE_ENV=()
   for name in CGPROFILE_CONTROL_SOCKET CGPROFILE_CONTROL_SOCKET_DIR CGPROFILE_CONTROL_GID CGPROFILE_OBSERVER_SOCKET CGPROFILE_OBSERVER_SOCKET_DIR CGPROFILE_OBSERVER_GID; do
     value=${!name-}
     [ -n "$value" ] && CGPROFILE_ENV+=(--setenv="$name=$value")
   done
   PATH_VALUE=/opt/mdt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
   EV="/root/accept-$(date -u +%Y%m%dT%H%M%SZ)-1"
   KEY=/root/.ssh/mdt-accept-loopback_ed25519
   systemd-run --unit mdt-accept-1 --collect --slice=mdt-accept.slice -p WorkingDirectory=/ -p KillMode=mixed -p TimeoutStopSec=600 \
     -p MemoryMin=64M "${CGPROFILE_ENV[@]}" --setenv=HOME=/root --setenv="PATH=$PATH_VALUE" --setenv="MDT_ACCEPT_SOURCE_SHA=$SHA" \
     --setenv=MDT_ACCEPT_SSH_TARGET=root@127.0.0.1 --setenv="MDT_ACCEPT_SSH_KEY=$KEY" \
     -p ExecStopPost="$K/accept-restore.sh --from-unit --lock-timeout 30 --evidence $EV" \
     "$K/accept-run.sh" --evidence "$EV" --steps 6 --step 128M --dwell 300
   journalctl -u mdt-accept-1 --no-pager -n 100 || true
   ```

   `HOME`, `PATH`, and `/` are explicit for the noninteractive unit. Operator part during the run: one gate lane, the cockpit attached and idle, one SSH login.
   Progress is in `journalctl -u mdt-accept-1`; measured PASS/FAIL steps print `K=<K>M`, and INCONCLUSIVE steps print no K. **Duration ≈ 55 min** (warm-up 600 s,
   seven 300 s dwells for steps 0–6, the DAMON-off control pass, plus margin); eval and apply add about 10 min. Exit codes: 0 a measured step passed and no blocking
   inconclusive occurred, 1 step 0 failed, 4 INCONCLUSIVE, 3 refused.

4. **Eval, restore, snapshot and restart the reconcile timer.** Run this as root on r1002 in a new shell after the unit ended. Replace `EV` with the exact directory printed by step 3. The EXIT trap starts the timer and reconcile service even if a later command fails:

   ```bash
   set -euo pipefail
   PATH=/opt/mdt/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
   export PATH
   restart_reconcile() {
     local status=$?
     trap - EXIT
     if ! systemctl start mdt-dev-governance-reconcile.timer; then
       echo "could not restart mdt-dev-governance-reconcile.timer; restart it manually" >&2
       [ "$status" -ne 0 ] || status=1
     fi
     if ! systemctl is-active --quiet mdt-dev-governance-reconcile.timer; then
       echo "mdt-dev-governance-reconcile.timer is not active after restart" >&2
       [ "$status" -ne 0 ] || status=1
     fi
     if ! systemctl start mdt-dev-governance-reconcile.service; then
       echo "could not start mdt-dev-governance-reconcile.service; start it manually" >&2
       [ "$status" -ne 0 ] || status=1
     fi
     exit "$status"
   }
   trap restart_reconcile EXIT
   snap() {
     for s in dev.slice dev.slice/dev-gates.slice; do for f in memory.max memory.min memory.current; do V=$(cat "/sys/fs/cgroup/$s/$f" 2>&1 || true); echo "$s/$f=$V"; done; done
     for s in dev.slice dev-gates.slice; do V=$(systemctl show -p MemoryMax -p MemoryMin -p NeedDaemonReload "$s" 2>&1 || true); printf '%s\n' "$V" | sed "s/^/$s /"; done
     d=/sys/kernel/mm/damon/admin/kdamonds; NR=$(cat "$d/nr_kdamonds" 2>&1 || true); echo "nr_kdamonds=$NR"
     for k in "$d"/[0-9]*; do V=$(cat "$k/state" 2>&1 || true); if [ -n "$V" ]; then echo "$k/state=$V"; fi; done
     docker inspect mdt-accept-squeeze --format '{{.State.Status}}' 2>&1 | tail -n 1 || true
     systemctl is-active mdt-dev-governance-reconcile.timer 2>&1 || true
   }
   SHA=REPLACE_WITH_FINAL_40_CHARACTER_COMMIT_SHA
   CK="/root/vbpub-accept-${SHA:0:12}"
   K="$CK/mdt-cockpit/host-setup/tools/accept"
   EV=/root/accept-REPLACE_WITH_EXACT_EVIDENCE_DIRECTORY
   [ -d "$EV" ] || { echo "set EV to the exact evidence directory printed by step 3" >&2; exit 1; }
   UNIT_STATE=$(systemctl is-active mdt-accept-1 2>&1 || true)
   echo "unit active state: $UNIT_STATE"
   journalctl -u mdt-accept-1 --no-pager 2>&1 | grep -E 'Main process exited|status=|Deactivated|Succeeded|Failed' | tail -n 5 || true
   EVAL_RC=0
   "$K/accept-eval.py" "$EV" >"$EV/eval.txt" 2>&1 || EVAL_RC=$?
   cat "$EV/eval.txt" || true
   echo "eval rc=$EVAL_RC"
   journalctl -u mdt-accept-1 --no-pager >"$EV/unit.log" 2>&1 || true
   RESTORE_STATE=$(grep -E '^(restore|squeeze_|restore_)|RESTORE-PENDING|DAMON-RESTORE-PENDING' "$EV/manifest.env" 2>/dev/null || true)
   printf '%s\n' "$RESTORE_STATE"
   STATE_FILE=$(ls "$EV/squeeze-state.env" 2>/dev/null || true)
   if [ -n "$STATE_FILE" ]; then RESTORE_OUT=$("$K/accept-restore.sh" --evidence "$EV" 2>&1 || true); printf '%s\n' "$RESTORE_OUT"; fi
   snap > /root/accept-snap-after.txt
   diff /root/accept-snap-before.txt /root/accept-snap-after.txt 2>&1 || true  # only memory.current and timer may differ; nr_kdamonds must match
   cp /root/accept-snap-*.txt "$EV/"
   tar -C /root -czf "$EV.tgz" "$(basename "$EV")"
   ```

   `accept-restore.sh` exits 0 when all kit-owned cgroup values and DAMON counts are restored, 1 when restoration is pending or a foreign value was left alone, and 3 when
   there is nothing to do or it refuses. `restore=ok` is written only when every owned value is back at its start value; a pending WAL remains for a later restore. If restoration is still pending or reports a foreign value, keep the WAL and state untouched; never force a foreign cgroup value or DAMON count.
   `accept-run.sh` removes the dummy by its exact name in every exit path. A run whose evidence has no `restore=ok` is not complete. If any earlier step refused, restore the WAL first if needed, then start the timer and service as described above.

5. **Apply and verify** the eval's printed `DEV_HOST_MEMORY_MIN`/`DEV_HOST_MEMORY_MARGIN`/`DEV_HOST_MEMORY_MEASURED` in `/etc/mdt/host-setup.env` (**no KEEP pin**: K is derived again),
   then run `install.sh`, `mdt-host-check.sh`, and `tools/accept/daemon-reload-check.sh`. The latter refuses while a lowering is deferred or systemd and the kernel disagree on a dev `MemoryMax`.
   A third run at the proposed K is step 0 only (`accept-run.sh --evidence <EV3> --steps 0`). A FAIL there is R again and proposes no K to pin; the header names only the tested K0.

6. **Clean the one-time loopback SSH prerequisite after the run and restore are complete.** This removes only its exact authorization, host-key line, and dedicated key files:

   ```bash
   set -euo pipefail
   EV=/root/accept-REPLACE_WITH_EXACT_EVIDENCE_DIRECTORY
   [ -d "$EV" ] || { echo "set EV to the exact evidence directory printed by step 3" >&2; exit 1; }
   KEY=/root/.ssh/mdt-accept-loopback_ed25519
   AUTH=/root/.ssh/authorized_keys
   KNOWN=/root/.ssh/known_hosts
   RESTORE_OK=$(grep -F -x 'restore=ok' "$EV/manifest.env" 2>/dev/null || true)
   [ -n "$RESTORE_OK" ] || { echo "restore=ok is absent; finish restoration before SSH cleanup" >&2; exit 1; }
   [ -r "$KEY.pub" ] && [ -r "$AUTH" ] && [ -r "$KNOWN" ] || { echo "loopback SSH cleanup inputs are missing" >&2; exit 1; }
   read -r KEY_TYPE KEY_DATA _ <"$KEY.pub" || { echo "dedicated public key is unreadable" >&2; exit 1; }
   AUTH_LINE="from=\"127.0.0.1,::1\",no-agent-forwarding,no-port-forwarding,no-X11-forwarding $KEY_TYPE $KEY_DATA mdt-accept-loopback"
   read -r HOST_TYPE HOST_DATA _ </etc/ssh/ssh_host_ed25519_key.pub || { echo "host Ed25519 key is unreadable" >&2; exit 1; }
   HOST_LINE="127.0.0.1 $HOST_TYPE $HOST_DATA"
   AUTH_GREP_RC=0
   grep -F -v -x -- "$AUTH_LINE" "$AUTH" >"$AUTH.tmp" 2>/dev/null || AUTH_GREP_RC=$?
   case "$AUTH_GREP_RC" in
     0|1) ;;
     *) rm -f "$AUTH.tmp" "$KNOWN.tmp"; echo "authorized_keys filter grep failed (rc $AUTH_GREP_RC); no cleanup file was installed" >&2; exit 1 ;;
   esac
   KNOWN_GREP_RC=0
   grep -F -v -x -- "$HOST_LINE" "$KNOWN" >"$KNOWN.tmp" 2>/dev/null || KNOWN_GREP_RC=$?
   case "$KNOWN_GREP_RC" in
     0|1) ;;
     *) rm -f "$AUTH.tmp" "$KNOWN.tmp"; echo "known_hosts filter grep failed (rc $KNOWN_GREP_RC); no cleanup file was installed" >&2; exit 1 ;;
   esac
   chmod 600 "$AUTH.tmp" "$KNOWN.tmp"
   mv "$AUTH.tmp" "$AUTH"
   mv "$KNOWN.tmp" "$KNOWN"
   AUTH_LEFT=$(grep -F -x -- "$AUTH_LINE" "$AUTH" 2>/dev/null || true)
   KNOWN_LEFT=$(grep -F -x -- "$HOST_LINE" "$KNOWN" 2>/dev/null || true)
   [ -z "$AUTH_LEFT" ] && [ -z "$KNOWN_LEFT" ] || { echo "exact loopback SSH lines remain; inspect before removing key material" >&2; exit 1; }
   rm -f "$KEY" "$KEY.pub"
   ```

### Shared tree cache of the dedicated-dev role (AD2-2, MDT-074)

Many lanes read the same checkout and assay template. Copying it per lane costs RAM once per lane; the design
(`PARALLELISATION-DESIGN-MEMO-ADDENDUM-2`, sections 2-3) keeps one read-only tree in a host tmpfs and gives each worker an overlay on top of it. This
section is the **host half**: where and under which policy. run-gate decides when and where a lane or helper is placed, assay owns the content and key, cowtree
owns the publish/lease mechanism. `HOST_ROLE=dedicated-dev` only; any other role renders and mounts none of it, and a host that leaves the role gets the owned
units removed by the next `install.sh` (a tmpfs that is still mounted stays until reboot or an operator `umount`).

| Piece | What | Keys |
|---|---|---|
| `dev-cache.slice` | persistent, under dev.slice, started by `install.sh`; `MemoryLow` only (no High/Max/SwapMax: it is bounded by the tmpfs size) | `DEV_CACHE_MEMORY_LOW` (empty or `-` = none), `DEV_CACHE_ZSWAP_WRITEBACK` |
| tree cache tmpfs | `MDT_TREE_CACHE_ROOT`, unit `srv-tree\x2dcache.mount`: `size`, `nr_inodes`, `mode=2770`, `gid` of the runner group, `nosuid,nodev,huge=never`, not `noexec`, never `noswap` | `MDT_TREE_CACHE_ROOT` (`/srv/tree-cache`), `MDT_TREE_CACHE_SIZE` (empty = 25 % of RAM, `-` = whole feature off), `MDT_TREE_CACHE_INODES`, `MDT_TREE_CACHE_GROUP` (`runner`) |
| upper tmpfs | `MDT_TREE_UPPER_ROOT`, per-worker overlay `upperdir`/`workdir` (`<token>/w<k>/{u,w}`), same flags; pages charged to the writing lane | `MDT_TREE_UPPER_ROOT` (`/srv/tree-upper`), `MDT_TREE_UPPER_SIZE` (empty = 10 %, `-` = none), `MDT_TREE_UPPER_INODES` |
| GC | `mdt-tree-cache-gc.timer` runs `mdt_tree_cache.py gc` | `MDT_TREE_CACHE_GC_IDLE` (6h), `MDT_TREE_UPPER_GC_IDLE` (1d), `MDT_TREE_CACHE_GC_INTERVAL` (15min) |
| capacity document | `tree_cache` block merged into `/etc/mdt/host-capacity.json` (root, slice, `size_bytes`, `upper_root`, `upper_size_bytes`, `overlay_opts`, `group`, `mode`, `gc_idle_seconds`); other keys untouched, file created if absent | derived |

Why the slice is persistent: when a cgroup that wrote or swapped pages dies, the charge moves to its parent (measured, `ad2-measurements-REPORT` M-4/M-5).
A populate helper started with `--cgroup-parent dev-cache.slice` therefore leaves the tree's tmpfs pages and swap entries charged to this slice, not to a
lane. The slice must exist before run-gate uses it (`systemctl show dev-cache.slice -p LoadState`); `mdt-host-check.sh` FAILs when it does not.
`DEV_CACHE_MEMORY_LOW` is part of the protected-Low sum: dev.slice's MemoryLow includes it, and the load tiers' MemoryHigh and MemorySwapMax (gates, background) subtract it
(`mdt_capacity.py`, `_PROTECTED_LOW_KEYS`; their MemoryMax is dev High and does not), so protecting the cache never lets dev exceed its High. Sizes are bounded: cache + upper at most half of RAM.
The overlay volumes are created by dockerd as `local` volumes (`redirect_dir=on,xino=auto`), which works with no new privilege; the options are published
in the capacity block so consumers do not hard-code them.

**This is optional and vbpub-specific.** The neutral contract any host can meet without mdt-cockpit (tmpfs path, mode/owner, lease layout, GC rules,
charging slice, a ten-line systemd example) is `libraries/cowtree/HOST-CONTRACT.md`; the keys and units here are one implementation of it, and consumers
fall back to private copies when it is absent. cowtree and assay never name host-setup keys.

**Directory and lease format** (the contract between cowtree's `cache` module, which publishes, and the host GC, which removes; the module docstring of
`mdt_tree_cache.py` is the normative text): `<root>/<key>/{COMPLETE,tree/,.lease,.last-used}`; a build happens in `<key>.tmp-<id>` under an exclusive flock on
`<root>/.locks/<key>` and is `rename`d to `<key>` after `COMPLETE` is written; every lane holds `LOCK_SH` on `<key>/.lease` for its lifetime and touches
`.last-used`. GC removes a key only if idle longer than the idle time, `LOCK_EX|LOCK_NB` on `.lease` succeeds, and no process's mountinfo holds it (a bind of
`<key>/...` of the cache tmpfs, or an overlay `lowerdir=`/`upperdir=`/`workdir=` naming it); stale `<key>.tmp-*` builds when `.locks/<key>` is free; orphan
upper `<token>` directories when idle and no overlay names them. Any doubt keeps the entry; GC refuses a root that is not a tmpfs mount point.

Apply and verify: `install.sh`, then `systemctl show dev-cache.slice -p LoadState -p MemoryLow`, `findmnt /srv/tree-cache /srv/tree-upper`,
`systemctl list-timers mdt-tree-cache-gc.timer`, `cat /etc/mdt/host-capacity.json` and `mdt-host-check.sh`. Remove again: `HOST_ROLE=shared-prod` or
`MDT_TREE_CACHE_SIZE=-`, then `install.sh`. A host rebuilt from the repo plus `/etc/mdt/host-setup.env` gets all of it back; nothing here is host state.

### Observation: read volume under a low IOPS cap

One R2 campaign container read 15-30 GB within an hour on a 8 GB host while the dev.slice cap was 300 IOPS. No cause is claimed; possible factors
(page-cache churn on little RAM, btrfs read amplification on `/srv/cow`, the workload) are under measurement: backlog MDT-065, assay B217/B218, dstdns
`docs/testing/PARALLELISM-AND-PERFORMANCE.md`.

## The IO baseline

`mdt-io-baseline.py` and `scripts/debian-install-v2/tools/iocost-calibrate.sh`
use the same generated Linux-derived `iocost_coef_gen.py` in
`scripts/debian-install-v2/tools/`. The pristine source and patch series are
under `debian_install_v2/vendor/`; run
`python3 scripts/debian-install-v2/tools/build-iocost-generator.py --check`
to detect drift. Both paths produce the sequential and random read/write
matrix against a persistent file by default. MDT's test-file identity check
requires that its file and Docker data share a filesystem, so MDT does not
currently use a separate scratch partition. The debian-install-v2 design
prefers a mounted file target on a dedicated partition over raw `--testdev`
mode. The six raw matrix values and four `io.max` derived ceilings
are written as `KEY=VALUE` benchmark results in `IO_BASELINE_ENV` (default
`/var/lib/mdt/io-baseline.env`) with an atomic write. The persistent target is
`IO_BASELINE_TESTFILE` (default `/var/lib/mdt/iocost-coef-fio.testfile`) and
must be on the same filesystem/device as Docker data. It is persistent
benchmark scratch: calibration rewrites its contents on every run. Existing
targets with the wrong type, link count, or size are refused without being
replaced. **The benchmark saturates
that device for about 12 minutes at default settings** — run it in a quiet
window.

The installed/operator Python entrypoints accept top-level `--version`; for
example, `sudo mdt-io-baseline.py --version` prints exactly one `MDT <version>`
line and exits 0 before host checks or writes. Parser diagnostics begin with
the MDT headline at every parser depth. Service watchers and timer-only audits
are not separate versioned CLI entrypoints.

There is no arbitrary age expiry. The results are reusable only when the target
path and size, Docker-data filesystem, resolved block-device identity/topology,
device facts, kernel release and benchmark-generator fingerprint still match.
A disk replacement, Docker-data move, target move, kernel change or generator
change makes the results non-current. `--force` deliberately remeasures an
otherwise current target; it does not weaken the identity checks during or
after the run.

The mapping into `io.max` is conservative and explicit: read/write IOPS use
the lower of that direction's sequential and random matrix values; read/write
bandwidth use sequential bytes per second. `DEV_IO_CAP_PCT` is applied to the
resulting whole-estate `dev.slice` pool. `WATCHER_IO_CAP_PCT` is applied to each
governed container's cap (and a matching out-of-tree Buildx worker), so it is a
percentage of the measured device ceiling,
not a percentage of the already-reduced `dev.slice` cap. Nested cgroups still
enforce the tighter effective limit. The baseline tool does not enable the
`io.cost` controller; it measures ceilings for the existing `io.max` policy.

The baseline must also be run from the Docker host shell. `/var/lib/mdt` and
`IO_BASELINE_TESTFILE` are host paths, and the test file must be on the same
host block device as `IO_DEV_PATH`; otherwise the recorded numbers describe the
wrong device. If the launcher sees a devcontainer, it refuses before the
benchmark can write misleading container-local results. A host-side IO probe
that cannot expose a `/dev/...` source simply leaves static IO caps omitted and
must be reviewed by the operator.

At the start of the wizard, the benchmark is offered before the resource
questions; when started, it runs in the background and the wizard waits for it
before asking the final IO settings. The wizard also asks for four explicit static fallback
values: `DEV_STATIC_RIOPS`, `DEV_STATIC_WIOPS`, `DEV_STATIC_RBW`, and
`DEV_STATIC_WBW`. These are real hard caps on the aggregate `dev.slice` root,
not per-container values. They apply from boot and whenever the identity-bound
baseline is unavailable. After a successful baseline, the reconciliation
service temporarily replaces them with `DEV_IO_CAP_PCT` percent of the
measured ceilings. Without current results, the static root values remain in
force and no per-container rate is guessed.

### What is really applied: pins, plausibility, read-back (MDT-016)

The effective `dev.slice` cap per value is, in order: the operator-pinned
absolute key (`DEV_IO_RBPS_MAX`, `DEV_IO_WBPS_MAX`, `DEV_IO_RIOPS_MAX`,
`DEV_IO_WIOPS_MAX`; runtime keys: edit `host-setup.env`, the next reconcile
picks them up; positive integers or K/M/G/T suffixes, `0` refused), else
`DEV_IO_CAP_PCT`% of a current, **plausible** baseline, else the static
fallback; when a pin and a baseline both exist the smaller wins. The pins also
become the static fallback in the rendered `dev.slice` unit. On a virtual disk
**pin them from a sustained measurement**: a baseline can read the hypervisor's
write-back cache (one host measured 4.3 GB/s from a disk that saturated at
~20 MB/s), and the reconcile refuses an obviously cache-inflated one (a rotational-flagged device above 1 GiB/s sequential or 20,000 random 4 KiB IOPS). virtio-blk reports `rotational=1` by default, so a genuinely fast virtio disk is refused too: set `IO_BASELINE_ROTATIONAL_FLAG=ignore` only if you know it is fast, and pin the caps either way. On a host whose swap or root sits on md/LVM spanning several disks the runtime caps cannot be applied or verified (one clear ERROR per run; the unit-file statics stay the only cap). A
**kernel update** invalidates the baseline until `mdt-io-baseline.py --force`
is re-run; pins are unaffected.

The reconcile never resets the runtime IO list to empty (an empty `IO*Max=`
assignment also wipes the unit-file values, measured live), and after every
apply it reads the kernel `io.max` back: a mismatch logs `ERROR` and fails the
service. It also warns `UNCAPPED SWAP` when an active swap device lies on a
disk with no `dev.slice` `io.max` entry. Check any host with
`mdt-cgroup-io-limits.sh dev.slice --all --systemd` (limits, EFFECTIVE value
along the ancestor path, resolved device names, systemd properties and
drop-in paths). Why: [CGROUP-NOTES section 3](CGROUP-NOTES.md#3-caps-expressed-as-a-percentage-of-what-the-disk-actually-does);
adoption steps: [CONSUMERS](../docs/CONSUMERS.md#dev-tier-io-governance-pin-verify-swap).

**Swap stays ample; the gate tier is throttled by IO instead (decided).** The
default (`DEV_SWAP_CASCADE_PCT=80` of host swap, cascading) is kept. A gate that
swaps is throttled by its own tight IO cap, because swap-out IO is charged to the
page owner's cgroup (per kernel source, not measured here: swap writeback bios are associated with the page owner's blkcg via `bio_associate_blkg_from_page` in `mm/page_io.c`): set `DEV_GATES_IO_RBPS_MAX`, `DEV_GATES_IO_WBPS_MAX`,
`DEV_GATES_IO_RIOPS_MAX`, `DEV_GATES_IO_WIOPS_MAX` (runtime keys; the same four
exist for `INTERACTIVE`, `BACKGROUND` and `BUILDKITD`; IOPS values combine with the
`DEV_SUBSLICE_IOPS_PCT` sub-ceiling as min). Illustration from the incident host:
`dev.slice` 80M/40M/800/600, gates 40M/20M/300/200. A throttled gate runs slowly:
that is an infrastructure-classified slowdown, not a product failure. A smaller
swap allowance (`DEV_SWAP_MAX`, `DEV_GATES_MEMORY_SWAP_MAX`) stays available but is
not the protection (D-HS-01-3). The withdraw path re-asserts values because a
`daemon-reload` after removing a runtime drop-in was measured NOT to re-apply the
unit-file value (D-HS-01-1).

**On a pin-only host the slice-level caps are the whole protection.** On a virtual disk the
baseline is normally absent or refused (implausible, stale after a kernel update), and the
per-container caps (`WATCHER_IO_CAP_PCT`), the Docker-events watcher's caps and the per-scope
`IOWeight=1`/`io.bfq.weight` deprioritisation of gate and BuildKit containers are applied only
from a valid baseline, so on such a host they are inactive. What remains, and is enough, is the
slice level: the `dev.slice` pins, the per-tier `DEV_<TIER>_IO_*_MAX` caps and the slices' own
`IOWeight` (`DEV_GATES_IO_WEIGHT`, `DEV_BUILDKITD_IO_WEIGHT`, rendered into the units). The
per-scope `IOWeight=1` was deliberately NOT decoupled from the baseline: it is a relative
share on top of the slice weight, applying it needs the same per-scope sweep and clear path
as the caps, and doing so would change the reconcile logic of a change whose purpose is to
make the slice-level caps verifiable.

The caps derived from it sit in a **60-80% band** of the measured ceiling:
`DEV_IO_CAP_PCT=60` for the whole `dev.slice` estate (it bounds every
governed child together — protects PRODUCTION from the tier, but not tier
members from each other), `WATCHER_IO_CAP_PCT=80` per governed container
(and for a matching out-of-tree Buildx worker). Never 100% — a
saturated device queues everything behind the burst, which is the stall the
tiering exists to prevent; below ~60% you are just throttling ordinary work.
Where both apply, cgroup limits nest and the stricter wins — the two are
complementary layers answering different questions, not a redundant pair to
collapse into one number.

**Sharing the measurement with ciu.** ciu governance caps individual compose
services from the same file format (deriving `read_iops` as 2/3 of
`RIOPS_MAX` — same band), but searches its own path, *not* `/var/lib/mdt/`.
Measure once and point ciu at it, so the tier caps and the per-service caps
can't disagree:

```bash
echo 'CIU_GOV_BASELINE_PATH=/var/lib/mdt/io-baseline.env' >> /etc/environment
```

(Or set `IO_BASELINE_ENV=/var/lib/ciu/io-baseline.env` in `host-setup.env` and
let ciu find it at its own default.) Reusing a baseline measured on comparable
hardware: point `IO_BASELINE_ENV` at it or copy the file.

## Verification

`mdt-cgroup-io-limits.sh dev.slice --all --systemd` shows what IO limits are
really in force; `mdt-host-check.sh` checks: `UNCAPPED SWAP` coverage,
`memory_recursiveprot` mount flag, unit presence +
activity (`dev.slice`, `dev-interactive.slice`, `dev-background.slice`,
`dev-gates.slice`, `dev-buildkitd.slice`),
effective cgroupfs values (including `io.bfq.weight` next to `io.weight` —
under BFQ only the former is what schedules), zswap-writeback policy,
`dev.slice`'s `io.max` + baseline device/target identity, the `docker-.scope.d` backstop's
presence, BFQ scheduler, timer enablement, and lists every running
container's cgroup parent, and the published run-gate gates admission cap against the host policy (MDT-077,
above). Exit 0 = no failures (warnings possible).

It also FAILs when systemd-oomd can act on the dev tier (MDT-108): `ManagedOOMMemoryPressure` or `ManagedOOMSwap` is not `auto` on any `dev*.slice` or on `-.slice`, or
`oomctl dump` lists a `dev*.slice` or the root slice. A stale drop-in, a `systemctl set-property` override or a distro ancestor default is the usual cause; host-setup never
removes what it did not render. WARN (not FAIL) when `oomctl` is absent, its dump fails, or its output lacks the `Memory Pressure Monitored CGroups:` section (format unrecognised, never read as clean).

The other failure it reports as FAIL rather than WARN is a missing
`memory_recursiveprot`: with that flag absent every `MemoryLow`/`MemoryMin` in
both tiers protects nothing, while `systemctl show` still reports the value you
set. See [CGROUP-NOTES.md §5](CGROUP-NOTES.md#5-cgroup2-mount-options--not-a-unit-setting-at-all).

## Uninstall

Withdraw the endpoint fragment first so augeaswrap removes only the facts owned by
mdt-host-setup and re-renders the shared environment files. Then remove host-setup's
units and files:

```bash
sudo augeaswrap endpoints withdraw --owner mdt-host-setup
sudo systemctl disable --now mdt-dev-governance-reconcile.timer mdt-dev-governance-reconcile.service mdt-buildkitd.service \
        mdt-container-io-events-watcher.service mdt-container-memory-inotify-watcher.service
sudo docker volume rm mdt-buildkitd-cache 2>/dev/null || true
sudo rm /etc/systemd/system/{dev,dev-interactive,dev-background,dev-gates,dev-memory_min_guaranteed,dev-buildkitd}.slice \
        /etc/systemd/system/mdt-buildkitd.service \
        /etc/systemd/system/mdt-container-io-events-watcher.service /etc/systemd/system/mdt-container-memory-inotify-watcher.service \
        /etc/systemd/system/docker-.scope.d/50-default-limits.conf \
        /etc/systemd/system/mdt-dev-governance-reconcile.{service,timer} \
        /usr/local/sbin/{mdt-dev-governance-reconcile.sh,mdt-container-io-events-watcher.sh,mdt-container-io-caps.lib.sh,mdt-container-memory-inotify-watcher.py,mdt-slice-memory-min-low-audit.py,mdt-io-baseline.py,mdt-host-check.sh} \
        /etc/modules-load.d/mdt-bfq.conf /etc/udev/rules.d/60-mdt-bfq-scheduler.rules \
        /etc/sysctl.d/99-mdt-inotify.conf /etc/systemd/system/docker.socket.d/50-mdt-dedicated-api-socket.conf
sudo systemctl daemon-reload
# The docker.socket drop-in removal takes effect at the next scheduled
# `systemctl restart docker.socket docker.service` (restarts every container).
sudo rm -rf /etc/mdt /var/lib/mdt        # config + benchmark results
# existing containers keep their current placement until recreated. A later
# MDT devcontainer rebuild refuses startup until the installed slice units return.
# /etc/docker/daemon.json is NOT removed here — it's a merge, not a wholesale
# install; manually drop the cgroup-parent/live-restore/log-opts keys you no
# longer want and `systemctl restart docker` if you do.
```
