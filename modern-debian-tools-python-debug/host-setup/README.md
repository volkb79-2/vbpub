# mdt host-setup — dev-tier resource governance (cgroup v2 slices)

Prepares a Docker host so devcontainers and the test/build/gate containers
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
dev.slice                    ONE absolute IOPS/bandwidth ceiling — covers
                              all three children combined, even interactive/
                              IDE activity must not be able to starve production
├── dev-interactive.slice    devcontainers (IDE + AI agents) via
│                             devcontainer.json runArg
├── dev-background.slice     test/build/gate containers — explicit opt-in
│                            (compose cgroup_parent, docker run
│                            --cgroup-parent) OR caught by the Docker
│                            daemon-wide default (/etc/docker/daemon.json)
└── dev-buildkitd.slice      host-managed BuildKit worker (mdt-buildkitd.
                             service) — a shared, long-lived builder, not
                             a per-invocation job; see
                             plan-buildkitd-service.md
```

| Tier | Who joins | Character |
|---|---|---|
| `dev-interactive.slice` | devcontainers (IDE + AI agents) via devcontainer.json runArg | responsive: soft-protected working set (`MemoryLow`), generous `MemoryHigh`, cold tail compressed into zswap and allowed to drain to disk from there (`DEV_INTERACTIVE_ZSWAP_WRITEBACK`), never OOM-killed |
| `dev-background.slice` | test/build/gate containers, via compose `cgroup_parent`, explicit `docker run --cgroup-parent`, **or** the Docker daemon-wide default | bounded: hard memory+swap caps (relaxed swap given ample host swap — size for yours), `systemd-oomd` kills inside the tier first |
| `dev-buildkitd.slice` | exactly one container: the host-managed rootless BuildKit worker (`mdt-buildkitd.service`, plain `docker run --cgroup-parent=`, never through a Buildx driver — see `plan-buildkitd-service.md`) | one shared build cache across every consuming project; hard `CPUQuota` auto-detected as `nproc - 2` cores unless overridden; active-build latency-sensitive, so `CPUWeight`/`IOWeight` sit between interactive's and background's |
| (production tiers) | e.g. `wings.slice` for game servers | owned elsewhere — this companion never touches them, it only keeps dev work from starving them |

`dev.slice` (the shared parent) carries the **one** absolute IOPS/bandwidth
ceiling for all three children combined — not one per tier. That is
deliberate: even interactive/IDE work must not be able to starve production
I/O, so a build storm AND a heavy IDE session together still can't exceed the
estate's single cap. `CPUWeight`/memory/OOM policy stay per-child, since
interactive, background, and the shared builder genuinely need different
shapes there.

Genuine **per-container** guarantees (so N concurrent gate containers can't
each individually hog the tier) are a separate, complementary mechanism:
explicit `docker run --memory`/`--cpus`/`--device-*-iops` flags in whatever
spawns the container (e.g. `cmru`'s tester-gate) — a slice's own limits only
bound the *whole tier combined*, never one container in it. See
`AGENTS.md` in the repo root ("Host cgroup placement for spawned containers").

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

## Quick start

```bash
sudo ./install.sh                  # seeds /etc/mdt/host-setup.env on first run
sudo vi /etc/mdt/host-setup.env    # size the tiers for THIS host
sudo ./install.sh --with-baseline  # re-render + measure disk ceilings (~4 min saturated IO — quiet window!)
sudo mdt-host-check.sh             # verify
```

Then recreate the containers that should be governed (placement is
create-time): rebuild the devcontainer, `docker compose up -d --force-recreate`
the test stacks.

## What gets installed

| Artifact | Target | Role |
|---|---|---|
| `units/dev.slice.in`, `units/dev-interactive.slice.in`, `units/dev-background.slice.in`, `units/dev-buildkitd.slice.in` | `/etc/systemd/system/*.slice` | the tiers — **rendered** from `/etc/mdt/host-setup.env` |
| `units/mdt-buildkitd.service.in` | `/etc/systemd/system/mdt-buildkitd.service` (rendered, enabled) | host-managed rootless BuildKit worker — `docker run --cgroup-parent=dev-buildkitd.slice` as `ExecStart=`, see `plan-buildkitd-service.md` |
| `units/docker-scope-default-limits.conf.in` | `/etc/systemd/system/docker-.scope.d/50-default-limits.conf` | D-G8 backstop — a generous "never truly unbounded" floor for EVERY container's transient scope, regardless of which slice (or none) it named |
| `units/mdt-host-slices.service` | systemd (enabled) | boot-time apply of the runtime half |
| `units/mdt-host-slices.timer.in` | systemd (enabled) | periodic re-apply (default 5min) |
| `scripts/mdt-apply-dev-caps.sh` | `/usr/local/sbin/` | runtime half (see below) |
| `scripts/mdt-slice-audit.py` | `/usr/local/sbin/` | read-only audit — logs a `[WARN]` for any `memory.min`/`memory.low` under `dev.slice` that is a silent no-op because an ancestor lacks its own value (second `ExecStart=` on the same service/timer) |
| `scripts/mdt-io-baseline.py` | `/usr/local/sbin/` | fio benchmark → `/var/lib/mdt/io-baseline.env` (30-day cache) |
| `scripts/check.sh` | `/usr/local/sbin/mdt-host-check.sh` | health check, non-zero exit on failure |
| `etc/modules-load.d/bfq.conf`, `etc/udev/rules.d/60-bfq-scheduler.rules` | `/etc/…` (`mdt-` prefixed) | BFQ at boot so IO weights bite |
| (merged, not copied) | `/etc/docker/daemon.json` | `cgroup-parent` (D-G7 default) + `live-restore`/log rotation — this is the file's ONE owner now; every key is merged in, nothing else in the file is touched |

## Persistence model — why units AND a service/timer

Reboot-survival works in three layers; each exists because the previous one
cannot express the next:

1. **Static slice units** (`/etc/systemd/system/*.slice`) — memory knobs,
   weights, `ManagedOOM*`, zswap-writeback policy (systemd ≥ 256), and
   deliberately **tight** static IO caps as boot-window fallback. Survive
   reboot and `daemon-reload` by themselves; zero runtime machinery. Rendered
   from `host-setup.env` at install time so per-host tuning stays in one
   reviewable file.
2. **Boot service + periodic timer** (`mdt-host-slices.service/.timer` →
   `mdt-apply-dev-caps.sh`) — everything units *can't* declare:
   - the **measured** whole-estate IO caps on `dev.slice` (`DEV_IO_CAP_PCT`%
     of the fio baseline — covers `dev-interactive.slice` +
     `dev-background.slice` combined) — `systemctl set-property --runtime`,
     reapplied each boot;
   - **per-container** caps for `buildx_buildkit_*`, `*test-runner*` and
     devcontainer scopes (`SWEEP_IO_CAP_PCT`% io.max; bench and buildkit
     additionally get `IOWeight=1` — the devcontainer does **not**, it is the
     IDE): docker scopes are *transient*, they only exist while the container
     runs, so no unit file can pre-configure them, and buildkit workers are
     created on demand by buildx AND — source-verified, see
     [BUILD-ARCHITECTURE.md](../docs/BUILD-ARCHITECTURE.md) — Buildx's
     `cgroup-parent` driver-opt is unreliable under the systemd cgroup driver,
     so they can never be placed under `dev.slice` via compose either; this
     sweep is their only governance, full stop, not a backstop for a
     placement mechanism that also works. The timer sweep catches them within
     `SWEEP_INTERVAL`. Anything `cmru`/mdt itself spawns directly gets
     explicit per-container flags
     instead (see "Tiering model" above) — this sweep is only for containers
     nobody's own code controls the invocation of;
   - a **cgroup2 mount-flag check**: `memory_recursiveprot` (without which
     every slice-level `MemoryLow`/`MemoryMin` silently stops protecting the
     container pages below it) is a systemd boot default, but a runtime
     remount can strip it — `CGROUP2_FLAGS=warn|fix` in the env file;
   - a **read-only ancestor-chain audit** (`mdt-slice-audit.py`, a second
     `ExecStart=` on the same service): even with `memory_recursiveprot`
     correctly mounted, `memory.min`/`memory.low` protection is bounded by
     EVERY ancestor cgroup's own value, not just the one it's set on — a
     value declared anywhere under `dev.slice` (a stack's governance config,
     a hand-set property, a future per-container mechanism) is a complete
     no-op if `dev.slice`/`dev-background.slice`/`dev-interactive.slice`
     themselves don't ALSO carry one (which, as shipped, they mostly don't —
     see `CGROUP-NOTES.md`). This never applies anything; it only logs to
     the journal so the gap is discoverable instead of silent;
3. **Create-time placement** — the one thing the host cannot do at all:
   containers join their tier only where they are *created*
   (devcontainer.json `runArgs`, compose `cgroup_parent:`, or the Docker
   daemon-wide default in `daemon.json`). Graceful degradation: if the unit
   file is missing, systemd invents a transient *unlimited* slice of the same
   name and the container starts normally — the `docker-.scope.d` backstop
   (D-G8, see "What gets installed") exists specifically to put a floor under
   that failure mode.

Alternatives considered for layer 2: a boot-only oneshot misses buildkit
workers created mid-session; a docker-events watcher daemon reacts instantly
but is a long-running process with restart/failure modes — the idempotent
timer sweep is the smallest thing that stays correct. If sub-interval
enforcement ever matters, run `mdt-apply-dev-caps.sh` from a docker events
hook and keep the timer as backstop.

Full reasoning for each gap, and why raw cgroupfs writes lose to
`set-property`: [CGROUP-NOTES.md](CGROUP-NOTES.md).

## The IO baseline

`mdt-io-baseline.py` measures 4 sustained ceilings (r/w IOPS at 4k QD32, r/w
bandwidth at 128k QD8, libaio, incompressible buffers, ramp+runtime defaults
10+40s) and caches them as `KEY=VALUE` in `/var/lib/mdt/io-baseline.env`
(atomic write, 30-day freshness, `--force` to remeasure). **It saturates the
disk for ~4 minutes** — run it in a quiet window.

The caps derived from it sit in a **60–80% band** of the measured ceiling:
`DEV_IO_CAP_PCT=60` for the whole `dev.slice` estate (it bounds 10–15+
containers across both tiers together — protects PRODUCTION from the tier,
but not tier members from each other), `SWEEP_IO_CAP_PCT=80` per
bench/buildkit/devcontainer container (protects tier members from each
other — for buildkit specifically, its *only* governance, since Buildx
placement under `dev.slice` doesn't work at all; see above). Never 100% — a
saturated device queues everything behind the burst, which is the stall the
tiering exists to prevent; below ~60% you are just throttling ordinary work.
Where both apply, cgroup limits nest and the stricter wins — the two are
complementary layers answering different questions, not a redundant pair to
collapse into one number.

**Bootstrapping from gstammtisch.** `install.sh` copies
`/var/lib/gstammtisch/io-baseline.env` to `/var/lib/mdt/io-baseline.env` on
first run if the latter doesn't exist yet and the former does, rather than
re-running the ~4min benchmark — mdt owns its own copy at its own canonical
path from then on (no runtime cross-reference between the two companions).

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

`mdt-host-check.sh` checks: `memory_recursiveprot` mount flag, unit presence +
activity (`dev.slice`, `dev-interactive.slice`, `dev-background.slice`),
effective cgroupfs values (including `io.bfq.weight` next to `io.weight` —
under BFQ only the former is what schedules), zswap-writeback policy,
`dev.slice`'s `io.max` + baseline freshness, the `docker-.scope.d` backstop's
presence, BFQ scheduler, timer enablement, and lists every running
container's cgroup parent. Exit 0 = no failures (warnings possible).

The one failure it reports as FAIL rather than WARN is a missing
`memory_recursiveprot`: with that flag absent every `MemoryLow`/`MemoryMin` in
both tiers protects nothing, while `systemctl show` still reports the value you
set. See [CGROUP-NOTES.md §5](CGROUP-NOTES.md#5-cgroup2-mount-options--not-a-unit-setting-at-all).

## Uninstall

```bash
sudo systemctl disable --now mdt-host-slices.timer mdt-host-slices.service mdt-buildkitd.service
sudo docker volume rm mdt-buildkitd-cache 2>/dev/null || true
sudo rm /etc/systemd/system/{dev,dev-interactive,dev-background,dev-buildkitd}.slice \
        /etc/systemd/system/mdt-buildkitd.service \
        /etc/systemd/system/docker-.scope.d/50-default-limits.conf \
        /etc/systemd/system/mdt-host-slices.{service,timer} \
        /usr/local/sbin/{mdt-apply-dev-caps.sh,mdt-slice-audit.py,mdt-io-baseline.py,mdt-host-check.sh} \
        /etc/modules-load.d/mdt-bfq.conf /etc/udev/rules.d/60-mdt-bfq-scheduler.rules
sudo systemctl daemon-reload
sudo rm -rf /etc/mdt /var/lib/mdt        # config + cached baseline
# containers keep their (now transient, unlimited) slices until recreated.
# /etc/docker/daemon.json is NOT removed here — it's a merge, not a wholesale
# install; manually drop the cgroup-parent/live-restore/log-opts keys you no
# longer want and `systemctl restart docker` if you do.
```
