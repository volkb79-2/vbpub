# mdt host-setup — dev-tier resource governance (cgroup v2 slices)

Prepares a Docker host so devcontainers and the test/build containers they
spawn run in **bounded systemd slices** instead of the host's default
(unlimited) cgroup — the host-side counterpart of the
`"--cgroup-parent=interactive.slice"` runArg shipped in
[`../templates/devcontainer.json`](../templates/devcontainer.json) and the
`cgroup_parent: besteffort.slice` that ciu governance injects into compose
stacks. Placement is CREATE-time only and can never be expressed from inside a
container or image — see
[`../docs/CONTAINER-DOCTRINE.md`](../docs/CONTAINER-DOCTRINE.md) and the
"Host resource governance" section of
[`../DEVCONTAINER-LIFECYCLE.md`](../DEVCONTAINER-LIFECYCLE.md).

**[`CGROUP-NOTES.md`](CGROUP-NOTES.md) is the conceptual half of this
directory:** what a slice unit fundamentally *cannot* express — and therefore
why there is a script and a timer here at all — plus the BFQ caveats. On a BFQ
host `IOWeight` does not mean what it says; read that before changing any
weight in `host-setup.env`.

## Tiering model

| Tier | Who joins | Character |
|---|---|---|
| `interactive.slice` | devcontainers (IDE + AI agents) via devcontainer.json runArg | responsive: soft-protected working set (`MemoryLow`), generous `MemoryHigh`, cold tail pinned in zswap (never disk swap), never OOM-killed |
| `besteffort.slice` | test/build/CI stacks via compose `cgroup_parent` | bounded: hard memory+swap caps, `systemd-oomd` kills inside the tier first, whole-tier **IO caps** at a percent of the *measured* device ceiling |
| (production tiers) | e.g. `wings.slice` for game servers | owned elsewhere — this companion never touches them, it only keeps dev work from starving them |

Weights (`CPUWeight`/`IOWeight`) settle contention *between* tiers; the io.max
caps bound besteffort **absolutely** so a build storm can't saturate the disk
even when production is momentarily idle (its next burst must not queue behind
a build). IO weights need the BFQ scheduler (installed/selected by this setup);
the io.max caps work on any scheduler.

⚠️ The shipped IO weights (interactive 100, besteffort 10) are a true 10:1
*because both stay ≤ 100*. systemd rescales `IOWeight` above 100 into BFQ's
1..1000 range, so "1000 vs 100" would be 1.81:1, not 10:1 — express IO ratios
by lowering the loser, never raising the winner.
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
| `units/interactive.slice.in`, `units/besteffort.slice.in` | `/etc/systemd/system/*.slice` | the tiers — **rendered** from `/etc/mdt/host-setup.env` |
| `units/mdt-host-slices.service` | systemd (enabled) | boot-time apply of the runtime half |
| `units/mdt-host-slices.timer.in` | systemd (enabled) | periodic re-apply (default 5min) |
| `scripts/mdt-apply-dev-caps.sh` | `/usr/local/sbin/` | runtime half (see below) |
| `scripts/mdt-io-baseline.py` | `/usr/local/sbin/` | fio benchmark → `/var/lib/mdt/io-baseline.env` (30-day cache) |
| `scripts/check.sh` | `/usr/local/sbin/mdt-host-check.sh` | health check, non-zero exit on failure |
| `etc/modules-load.d/bfq.conf`, `etc/udev/rules.d/60-bfq-scheduler.rules` | `/etc/…` (`mdt-` prefixed) | BFQ at boot so IO weights bite |

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
   - the **measured** besteffort IO caps (`BE_IO_CAP_PCT`% of the fio
     baseline) — `systemctl set-property --runtime`, reapplied each boot;
   - **per-container** caps for `buildx_buildkit_*`, `*test-runner*` and
     devcontainer scopes (`BENCH_IO_CAP_PCT`% io.max; bench and buildkit
     additionally get `IOWeight=1` — the devcontainer does **not**, it is the
     IDE): docker scopes are *transient*, they only exist while the container
     runs, so no unit file can pre-configure them, and buildkit workers are
     created on demand by buildx (no compose file to put `cgroup_parent:`
     into). The timer sweep catches them within `SWEEP_INTERVAL`;
   - a **cgroup2 mount-flag check**: `memory_recursiveprot` (without which
     every slice-level `MemoryLow`/`MemoryMin` silently stops protecting the
     container pages below it) is a systemd boot default, but a runtime
     remount can strip it — `CGROUP2_FLAGS=warn|fix` in the env file.
3. **Create-time placement** — the one thing the host cannot do at all:
   containers join their tier only where they are *created*
   (devcontainer.json `runArgs`, compose `cgroup_parent:`). Graceful
   degradation: if the unit file is missing, systemd invents a transient
   *unlimited* slice of the same name and the container starts normally.

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
`BE_IO_CAP_PCT=60` for the whole besteffort tier (it bounds 10–15 containers
together), `BENCH_IO_CAP_PCT=80` per bench/buildkit/devcontainer container.
Never 100% — a saturated device queues everything behind the burst, which is
the stall the tiering exists to prevent; below ~60% you are just throttling
ordinary work. Where both apply, cgroup limits nest and the stricter wins.

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
activity, effective cgroupfs values (including `io.bfq.weight` next to
`io.weight` — under BFQ only the former is what schedules), zswap-writeback
policy, besteffort `io.max` + baseline freshness, BFQ scheduler, timer
enablement, and lists every running container's cgroup parent. Exit 0 = no
failures (warnings possible).

The one failure it reports as FAIL rather than WARN is a missing
`memory_recursiveprot`: with that flag absent every `MemoryLow`/`MemoryMin` in
both tiers protects nothing, while `systemctl show` still reports the value you
set. See [CGROUP-NOTES.md §5](CGROUP-NOTES.md#5-cgroup2-mount-options--not-a-unit-setting-at-all).

## Uninstall

```bash
sudo systemctl disable --now mdt-host-slices.timer mdt-host-slices.service
sudo rm /etc/systemd/system/{interactive,besteffort}.slice \
        /etc/systemd/system/mdt-host-slices.{service,timer} \
        /usr/local/sbin/{mdt-apply-dev-caps.sh,mdt-io-baseline.py,mdt-host-check.sh} \
        /etc/modules-load.d/mdt-bfq.conf /etc/udev/rules.d/60-mdt-bfq-scheduler.rules
sudo systemctl daemon-reload
sudo rm -rf /etc/mdt /var/lib/mdt        # config + cached baseline
# containers keep their (now transient, unlimited) slices until recreated
```
