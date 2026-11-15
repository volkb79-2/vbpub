# iogovmon: recommend-only recorder of long-term virtual-disk capability shifts

Retained acceptance record:
[install record 2026-10-27](docs/evidence/INSTALL-RECORD-2026-10-27.md).

Standalone background service for ANY Linux host with systemd and cgroup v2: no mdt-cockpit, no host-tuning, no iobench, no docker. Extracted from
`mdt-cockpit/iogov` (WP-IGM-a of the host-tuning program: a move and rename, with the reviewed empty `governed_cgroups` default and version provenance fixes).
Design and decisions: [docs/DESIGN-GUIDE.md](docs/DESIGN-GUIDE.md). Adopter examples: [docs/CONSUMERS.md](docs/CONSUMERS.md). Background and the blind opinions (they stay with mdt-cockpit):
[../mdt-cockpit/docs/IO-GOVERNOR-SECOND-OPINION-2026-10-27.md](../mdt-cockpit/docs/IO-GOVERNOR-SECOND-OPINION-2026-10-27.md).

**Status: unreleased.** The extracted project has not been installed by repository automation. The first release is gated on the live acceptance WP-IGM-ACC
(real install, 48 h of ticks, journal and budget verification, raw-read ACL review, kill switches); until that evidence is recorded no consumer installs it by default. Open work is in
[BACKLOG.md](BACKLOG.md).

**Nothing in this package changes a cap, an io.cost model or QoS value, a queue setting or any cgroup file.** It measures with
tiny bounded probes, journals, recognises slow regime changes and prints recommendations. Python standard library only (3.11+
for `tomllib`). Every unit runs in its own cgroup, `iogovmon.slice`. Its IOWeight is ADVISORY (it only takes effect with io.cost or bfq); the real
budget control is the PSI gating of the bench (it starts only below the io-pressure limits and aborts when they are exceeded) plus the byte budgets.

## Layout

```
iogovmon/
  pyproject.toml cmru.toml run-gate.toml             packaging (`iogovmond` + `iogovmonctl`) and the release/gate contracts
  src/iogovmon/
    cli.py runner.py      commands, one tick / one bench, dry-run plan
    rawread.py            read-only raw-disk reads (O_DIRECT), coprime whole-device walk, witness blocks
    scratch.py bench.py   adaptive scratch region (the only thing ever written), probe lanes
    collectors.py         PSI (io and memory), /proc/vmstat swap counters, /sys/block stat+inflight, cgroup io.stat/io.pressure, settings epoch, "who else writes"
    devices.py hostcheck.py  whole-disk resolution (sysfs/mountinfo only), `preflight` host-tool check
    covariates.py         fill, fstrim, discard, provider signal, queue/prio facts + the report-only prevention checker
    episodes.py           `watch`: protected-side distress episodes from io.pressure AND memory.pressure (stopping-distance data), no probe IO
    recognizer.py         robust baseline + two-sided CUSUM + hysteresis -> recommendations; demand-limited labels
    safeio.py             identity, directory checks, no-follow verified opens, locks, atomic JSON
    gating.py budget.py   kill switches, load gates, backoff, mid-run guard; token buckets, reserved daily budget, worst-case numbers
    journal.py report.py state.py  append-only JSONL (schema v1), validated state; `analyze`, `status`
    experiment.py         `quiet-experiment`: plan printer only
  install.sh            idempotent installer helper (--dry-run prints every action); becomes the `install` verb in WP-IGM-b
  tools/write_installed_version.py  stamps source-install version provenance
  units/*.in            systemd unit / slice / udev templates (rendered by install.sh, never installed as they are)
  iogovmon.toml.example configuration
  docs/DESIGN-GUIDE.md  the v1 design note
  tests/                pytest, all on fakes and temp dirs (no real device, no cgroup write)
```

## Executables

The wheel installs two console scripts. The daemon binary has only commands that run as services or timers; the control binary has the operator
and inspection commands. Both accept the shared `--config`, `--allow-root`, and `--version` options.

| Binary | Subcommands |
| --- | --- |
| `iogovmond` | `tick`, `watch`, `bench`, `cleanup` |
| `iogovmonctl` | `plan`, `analyze`, `status`, `preflight`, `devices`, `annotate`, `note`, `quiet-experiment`, `prune`, `config` |

`python -m iogovmon` remains a combined developer entry point. It is not installed as a console script. There are no current `install`, `uninstall`,
`doctor`, `export`, or `stats` subcommands: `install.sh` is the source-tree installer, `preflight` checks host requirements, and future operator
verbs such as `iogovmonctl install` belong on the control binary.

## Run it

```
pip install iogovmon-*.whl        # or, from a checkout: PYTHONPATH=iogovmon/src python3 -m iogovmon ...
iogovmonctl plan                  # DRY RUN: devices, region, lanes, budget numbers, gates. Opens no device, writes nothing.
iogovmond tick --dry-run          # same output; `iogovmond bench --dry-run` too
iogovmonctl preflight             # BEFORE installing: every missing host tool/package with its apt name; exit 1 if any is missing
iogovmonctl devices               # the disks that would be probed (sysfs lookups only); unresolvable paths are explained, never guessed
iogovmond tick                    # raw 4 KiB reads + passive snapshot (timer: every 5 min); a gated tick still journals the passive snapshot (<= 1 per 15 min)
iogovmond bench [--force]         # the ~6 h mini write bench (a no-op unless due; --force still honours gates and kill switches)
iogovmond watch                   # optional resident sampler of protected-side distress episodes
iogovmonctl analyze [PATH] [--json] [--lane r_cold] [--no-plots]   # offline: summary, shifts, text plots, harm, demand check
iogovmonctl status                # kill switch, last runs, today's budget, recognizer state, prevention checks
iogovmonctl annotate "provider optimise run"     # journal annotation (also: --action, --baseline-reset)
iogovmonctl quiet-experiment --knob nr_requests --value 8   # prints a plan; executes nothing
iogovmonctl prune [--dry-run]     # delete monthly journal files older than 400 days / beyond 512 MiB (also run daily by `tick`)
iogovmond cleanup                 # remove scratch files left by a killed run
iogovmonctl config                # effective configuration
```

The systemd units run `/usr/local/bin/iogovmond` from the root-owned package copy (`install.sh`); the wheel installs the same daemon and control entry points.
`--version` and each `run_start.version` use wheel metadata first, then the source-installer's generated version file, then `0+source`. The source version comes
from `pyproject.toml` and adds `+g<short-sha>` when Git can identify the checkout; see [the versioning decision](docs/DESIGN-GUIDE.md#version-provenance).

Config: `--config FILE`, `$IOGOVMON_CONFIG`, `/etc/iogovmon/iogovmon.toml`, else defaults. Flat TOML; an unknown key or a wrong type is an
error; a value beyond the hard limits in `config.py` is clamped to the limit (the clamps are printed by `plan` and `config`). Minimum
useful config: `data_paths = [...]` (the disks behind them are detected) and `protected_cgroups` / `containers` for the harm observations.
`governed_cgroups` defaults to EMPTY (no host-specific slice is assumed; list the cgroups whose writes should be tracked as "governed", globs allowed).
With none governed the governed series are empty and "who else writes" lists every top-level cgroup except iogovmon's own slice; automatic
discovery of the top-level slices is WP-IGM-b.
The config path must be a regular file, or a root-owned symlink to one (a managed-config layout) whose whole chain is trusted: every hop
root-owned, and every directory above every hop and above the final file root-owned and not group/other-writable; the target must be
root- or self-owned, not writable by group/others, and at most 1 MiB. A FIFO, device node, non-root symlink, or a link into a directory the
service user could write is refused. "Trusted directory" means root-owned, not other-writable, and group-writable only with the sticky bit:
a link into Debian's `/usr/local` (2775 root:staff) is refused (any staff member could swap the link; use `/etc/iogovmon`), a link into a
`/nix/store`-style 1775 directory is accepted. At most 8 symlink hops.

State hygiene: `state.json` is validated against one schema on every load (`src/iogovmon/state.py`); invalid fields are dropped to their defaults,
the repaired file is saved at once and one `note` (`kind: state_repair`) says what was dropped. A damaged budget slot, or a state file that
exists but cannot be parsed, counts today's byte caps as spent (fail closed: one day without probes, never a reset cap). Keys a newer
version wrote are preserved.

The service units are `Type=oneshot`, so their runtime bound is `TimeoutStartSec` (tick 10 min, bench 30 min) followed by `TimeoutStopSec`;
a run stuck in a kernel syscall is killed by that, and the next run cleans up (design note section 5). The rendered units are checked with
`systemd-analyze verify` by the tests.

`analyze` and `status` are read-only and are run by **root or the service user** (`sudo -u iogovmon iogovmonctl status`): the state directory
is 0700 and service-owned, so no third identity can reach it; the readers accept any file owner (root reading the service user's files), open
every file no-follow with the type checked before the first read (a FIFO or device node planted in the directory cannot hang them), and bound
what they read (`safeio.MAX_JSON_BYTES`, `journal.MAX_LINE_READ` / `MAX_FILE_BYTES` / `MAX_RECORDS`). A file they cannot read or use is listed
as `UNREADABLE` / `corrupt state` / `TRUNCATED` / `LIMIT`, never silently dropped, so "0 records" always means an empty journal.

Upgrades: a config key that a later release removed is ignored with a warning (listed by `plan` and `config`) for one release; an unknown new
key is still an error.

Identity: **never root by default** (`allow_root = true` or `--allow-root` is required, and then every state/scratch directory must be
root-owned 0700). State, journal and scratch directories must be real directories owned by the running user and not group/world writable;
files are opened `O_NOFOLLOW` and verified through the descriptor; `tick` and `bench` exclude each other with `state_dir/run.lock`
(a second run exits quietly), `watch` has `watch.lock`, state writes take `state.lock`.

Kill switches (any one stops every run and aborts a running one at its next safe point): `touch /etc/iogovmon/disabled`,
`touch <state_dir>/DISABLED`, `IOGOVMON_DISABLE=1`, `enabled = false`, `systemctl stop|mask` of the timers.

Ownership of what the service trusts: `/etc/iogovmon` must be root-owned 0755, the config file root-owned and not writable by anyone else
(a config file writable by group/others, or owned by another user, is refused), and the kill-switch file root-owned. Every safety limit in
the config is clamped to the hard limits in `config.py` (the clamps are listed by `plan` and `config`).

Containers: prefer explicit `container_cgroups = { name = "/sys/fs/cgroup/..." }`. `containers = [...]` alone makes the service read the
daemon's `config.v2.json` files, which also contain container environment variables (secrets); that access widens what a compromised service sees.

Run the tests through the registered gate: `cd iogovmon && run-gate r0-r1`. The lane runs pytest and branch-aware coverage in `tester-unified`;
`bash` and `systemd-analyze` are required by installer and unit-template checks. The gate lanes are declared in `run-gate.toml`.

## Prerequisites (checked by `iogovmonctl preflight`, which names the apt package of every missing item and exits 1)

| Item | Debian package | Needed for |
| --- | --- | --- |
| `python3` >= 3.11 | `python3` | `tomllib`; no third-party Python package is used |
| `setfacl` | **`acl`** | the udev rule's read-only ACL on the disk node (step 3); NOT present on a minimal host |
| `udevadm` | `udev` | `ID_PATH` lookup, `udevadm test`/`trigger` (step 3) |
| `systemctl`, `systemd-analyze` | `systemd` | installing the units, `systemd-analyze verify` of the rendered units |
| `useradd` | `passwd` | the service user (step 1) |
| `install`, `dd` | `coreutils` | directories/files with owner and mode, the O_DIRECT read check |
| cgroup v2 with the `io` controller, PSI (`/proc/pressure/io`), sysfs | kernel | harm observations, load gates, device detection |
| `docker` (only to look up container cgroup paths by hand) | `docker.io` | optional, reported as `absent`, never required |

Run `apt-get install acl` (or whatever `preflight` prints) first. The preflight reads files and executes nothing.

Device detection (`data_paths`) reads sysfs and `/proc/self/mountinfo` only (no root, no `btrfs-progs`, no `lsblk`): `st_dev` -> `/sys/dev/block`,
then partitions, device-mapper (LVM, dm-crypt), md and multipath slaves recursively to the whole disk(s). A filesystem with an anonymous device
number (btrfs) is resolved through its mount (`mountinfo`: source device) and all member devices in `/sys/fs/btrfs/<uuid>/devices`. When
anything cannot be resolved the note lists what was seen (st_dev, mount, fs type, source) and detection reports no disk: it never guesses; set
`devices = [...]` explicitly. An explicit `devices` entry must exist as seen by the process (a missing path or dangling symlink is a clear note,
with a hint when the same name exists under the other of `/dev/disk/by-id` and `/dev/disk/by-path`); a partition or mapper node is replaced by
its whole disk(s).

## Installer helper

`install.sh` (in this directory; a source-tree install, the wheel-only `iogovmonctl install` verb is WP-IGM-b) performs the setup below for the report-only
tick, idempotently: `./install.sh --dry-run` prints every action and changes nothing; `sudo ./install.sh` does it (preflight first, stops on any
missing package); `--enable-tick` additionally enables `iogovmond-tick.timer` (step 5, only after a manual `iogovmonctl plan` and one `iogovmond tick`). It installs a
root-owned copy of the package (default `/usr/local/lib/iogovmon-pkg`, `--lib-dir` to change; Debian's `/usr/local/lib` is root:staff 2775, so
`--lib-dir /opt/iogovmon` is stricter), installs the `iogovmond` and `iogovmonctl` launchers in `/usr/local/bin`, creates the user and 0700
state/scratch directories, and a config skeleton (an existing config is never overwritten; with the example `data_paths` it stops with exit 3 so
you edit it). It renders and verifies the units and installs the read-only ACL rule for exactly one disk (`--disk-node`/`--id-path`, else taken
from the config and `udevadm`). It never enables the bench timer or the watch service, never
runs docker or touches any container, and `--destdir DIR` stages the files under DIR without useradd/chown/systemctl/udevadm. The manual steps below
remain the reference.

Two properties to know: `ID_PATH` is a bus-path identity, so a different disk placed in the same port inherits the read grant (re-check the rule
after any hardware swap); and `--dry-run` still runs the real preflight, so it requires the preflight to pass before it prints the plan (use
`--skip-preflight` only to see the plan on a host that is missing packages). All paths and values are validated (`--destdir`/`--lib-dir`
absolute without `..`; `--disk-node` a whole disk, cross-checked against udev's `ID_PATH`: a real install refuses unless `udevadm` reports an `ID_PATH` equal to `--id-path`; `--dry-run` refuses a
mismatch and prints an UNVERIFIED warning when udev reports nothing; the check is not done with `--destdir`). Mapped/raid (`dm-*`, `md*`) and
partition nodes are refused as "not a whole disk". The package is swapped in with two renames (old aside, new in), so a SIGKILL between them
leaves `iogovmon.old.<pid>`; the next run restores it when `iogovmon` is missing and removes stale `iogovmon.old.*`/`iogovmon.new.*` otherwise.
`IOGOVMON_SYSFS_ROOT` is a test hook for the partition check and is honoured only together with `--destdir`.

The raw-read ACL is a permission change on a (possibly production) disk node. It is reviewed in WP-IGM-ACC; if that review rejects it, a root-owned
helper that hands the service a read-only file descriptor replaces the rule.

Deployment as a host install versus a container: design note section 12.

## Install steps (the installer helper above does these; the list is the reference)

1. Create the user and directories: `useradd --system iogovmon`; `install -d -o iogovmon -g iogovmon -m 700 /var/lib/iogovmon /var/lib/iogovmon/scratch`
   (the scratch directory is refused unless it is 0700; `StateDirectory=iogovmon` in the units would create the state dir 0700 anyway);
   `install -d -o root -g root -m 755 /etc/iogovmon` (root-owned: the service user must not be able to edit its config or remove the kill switch).
2. Copy the package to a root-owned location and `iogovmon.toml.example` to `/etc/iogovmon/iogovmon.toml` (root-owned 0644); edit `data_paths` etc.
3. Read-only access to the one disk: render `units/99-iogovmon-raw-read.rules.in` into `/etc/udev/rules.d/`, check with
   `udevadm test`, then `udevadm trigger`. Verify with the service user: reading works (`dd if=<node> bs=4k count=1 iflag=direct of=/dev/null`) and
   `test -w <node>` is false. Do not use the `disk` group and do not add CAP_SYS_RAWIO.
4. Install the `iogovmond` and `iogovmonctl` launchers, then render `units/*.in` (the services use `Slice=iogovmon.slice`, `StateDirectory=iogovmon`, `ProtectKernelTunables`, `ProtectControlGroups`, an empty `CapabilityBoundingSet`, `PrivateNetwork`, `SystemCallFilter=@system-service`) into `/etc/systemd/system/`; run `systemctl daemon-reload`.
   Start with `iogovmonctl plan` as the service user, then `systemctl start iogovmond-tick.service` once and read its journal records.
5. Enable `iogovmond-tick.timer` first (read probes and passive evidence only) and, optionally, `iogovmond.service`. Run the FIRST write bench by hand,
   in a window where the protected workload is idle, as the service user: `iogovmond bench --force` (gates and kill switches still apply),
   and compare `watch` episodes before and after it. Only then enable `iogovmond-bench.timer` (otherwise its first run is whenever the gate first passes).
6. Let it observe for a week before reading anything into `analyze` (the recognizer needs `baseline_windows` = 5 valid daily windows).

Template tokens: `@IOGOVMON_USER@` service user, `@IOGOVMON_PYTHONPATH@` directory containing `iogovmon/`,
`@IOGOVMON_CONFIG@` config file, `@IOGOVMON_STATE_DIR@` state dir, `@IOGOVMON_SCRATCH_DIR@` scratch dir, `@IOGOVMON_KILL_SWITCH_FILE@` flag file,
`@IOGOVMON_DISK_NODE@` stable `/dev/disk/by-id/...` path of the disk, `@IOGOVMON_DISK_ID_PATH@` udev `ID_PATH` of that disk,
`@IOGOVMON_IO_WEIGHT@` io weight of the slice (default 50, advisory), `@IOGOVMON_TICK_INTERVAL@` tick period (default 5min).

The scratch directory must be on the filesystem of the data disks (otherwise the bench measures another device). The service's
`ReadWritePaths` are the state and scratch dirs only.

## Rename map from `mdt-cockpit/iogov` (no aliases, no compat shim)

| Old | New |
| --- | --- |
| `python -m iogov` (from `mdt-cockpit/`) | `iogovmond` (service commands) / `iogovmonctl` (operator commands); `python -m iogovmon` is developer-only |
| `/etc/iogov/iogov.toml`, `$IOGOV_CONFIG` | `/etc/iogovmon/iogovmon.toml`, `$IOGOVMON_CONFIG` |
| `/var/lib/iogov`, user `iogov` | `/var/lib/iogovmon`, user `iogovmon` |
| kill switch `/etc/iogov/disabled`, `IOGOV_DISABLE=1` | `/etc/iogovmon/disabled`, `IOGOVMON_DISABLE=1` |
| units `iogov-{tick,bench,watch}.*`, slice `iogov-probe.slice` | `iogovmond-{tick,bench}.*`, `iogovmond.service`, slice `iogovmon.slice` |
| `99-iogov-raw-read.rules`, tokens `@IOGOV_*@` | `99-iogovmon-raw-read.rules`, `@IOGOVMON_*@` |
| scratch files `iogov-scratch-*` | `iogovmon-scratch-*` |
| default `governed_cgroups = ["/sys/fs/cgroup/dev.slice"]` | `[]` |

## Migrating from the old mdt iogov install

Nothing in this repository installed the old `mdt-cockpit/iogov` project on a host. Only hosts where an operator ran its `install.sh` by hand are
affected. That script created the `iogov` system user, `/etc/iogov`, `/var/lib/iogov` and `/usr/local/lib/iogov-pkg/iogov`; installed
`iogov-probe.slice`, `iogov-tick.service`, `iogov-tick.timer`, `iogov-watch.service`, `iogov-bench.service`, and `iogov-bench.timer`; and installed
`/etc/udev/rules.d/99-iogov-raw-read.rules`, which granted that user read access to the configured whole-disk node.

The cleanup removes the old config and journal; archive `/var/lib/iogov` first if that history is needed. If the old script was run with `--user` or
`--lib-dir`, substitute those overrides in the commands. Remove the ACL from the same whole-disk node selected by the old configuration, then remove the old files and user:

```sh
sudo systemctl disable --now iogov-tick.timer iogov-bench.timer
sudo systemctl stop iogov-tick.service iogov-watch.service iogov-bench.service
sudo rm -f /etc/systemd/system/iogov-probe.slice /etc/systemd/system/iogov-tick.service /etc/systemd/system/iogov-tick.timer /etc/systemd/system/iogov-watch.service /etc/systemd/system/iogov-bench.service /etc/systemd/system/iogov-bench.timer
sudo systemctl daemon-reload
sudo rm -f /etc/udev/rules.d/99-iogov-raw-read.rules
sudo udevadm control --reload-rules
sudo setfacl -x u:iogov /dev/<old-whole-disk-node>
sudo userdel iogov
sudo rm -rf /etc/iogov /var/lib/iogov /usr/local/lib/iogov-pkg/iogov
sudo rmdir /usr/local/lib/iogov-pkg
```

Replace `/dev/<old-whole-disk-node>` with the exact device node selected by the old install. These commands are documentation only; they have not been run.
