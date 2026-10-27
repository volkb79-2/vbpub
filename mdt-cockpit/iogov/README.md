# iogov v1: recommend-only recorder of long-term virtual-disk capability shifts

Design and decisions: [../docs/IO-GOVERNOR-V1-DESIGN.md](../docs/IO-GOVERNOR-V1-DESIGN.md). Background and the blind opinions:
[../docs/IO-GOVERNOR-SECOND-OPINION-2026-10-27.md](../docs/IO-GOVERNOR-SECOND-OPINION-2026-10-27.md).

**Nothing in this package changes a cap, an io.cost model or QoS value, a queue setting or any cgroup file.** It measures with
tiny bounded probes, journals, recognises slow regime changes and prints recommendations. Python standard library only (3.11+
for `tomllib`). It is not installed or enabled by anything in this repository.

## Layout

```
mdt-cockpit/iogov/
  cli.py runner.py      commands, one tick / one bench, dry-run plan
  rawread.py            read-only raw-disk reads (O_DIRECT), coprime whole-device walk, witness blocks
  scratch.py bench.py   adaptive scratch region (the only thing ever written), probe lanes
  collectors.py         PSI (io and memory), /proc/vmstat swap counters, /sys/block stat+inflight, cgroup io.stat/io.pressure, settings epoch, "who else writes"
  covariates.py         fill, fstrim, discard, provider signal, queue/prio facts + the report-only prevention checker
  episodes.py           `watch`: protected-side distress episodes from io.pressure AND memory.pressure (stopping-distance data), no probe IO
  recognizer.py         robust baseline + two-sided CUSUM + hysteresis -> recommendations; demand-limited labels
  safeio.py             identity, directory checks, no-follow verified opens, locks, atomic JSON
  gating.py budget.py   kill switches, load gates, backoff, mid-run guard; token buckets, reserved daily budget, worst-case numbers
  journal.py report.py  append-only JSONL (schema v1) and state; `analyze`, `status`
  experiment.py         `quiet-experiment`: plan printer only
  units/*.in            systemd unit / udev templates (NOT installed)
  iogov.toml.example    configuration
  tests/                pytest, all on fakes and temp dirs
```

## Run it

From `mdt-cockpit/` (so that `import iogov` resolves), or with `PYTHONPATH=<dir containing iogov/>`:

```
python3 -m iogov plan                  # DRY RUN: devices, region, lanes, budget numbers, gates. Opens no device, writes nothing.
python3 -m iogov tick --dry-run        # same output; `bench --dry-run` too
python3 -m iogov devices               # the disks that would be probed (sysfs lookups only)
python3 -m iogov tick                  # raw 4 KiB reads + passive snapshot (timer: every 5 min); a gated tick still journals the passive snapshot (<= 1 per 15 min)
python3 -m iogov bench [--force]       # the ~6 h mini write bench (a no-op unless due; --force still honours gates and kill switches)
python3 -m iogov watch                 # optional resident sampler of protected-side distress episodes
python3 -m iogov analyze [PATH] [--json] [--lane r_cold] [--no-plots]   # offline: summary, shifts, text plots, harm, demand check
python3 -m iogov status                # kill switch, last runs, today's budget, recognizer state, prevention checks
python3 -m iogov annotate "provider optimise run"          # journal annotation (also: --action, --baseline-reset)
python3 -m iogov quiet-experiment --knob nr_requests --value 8   # prints a plan; executes nothing
python3 -m iogov prune [--dry-run]     # delete monthly journal files older than 400 days / beyond 512 MiB (also run daily by `tick`)
python3 -m iogov cleanup               # remove scratch files left by a killed run
python3 -m iogov config                # effective configuration
```

Config: `--config FILE`, `$IOGOV_CONFIG`, `/etc/iogov/iogov.toml`, else defaults. Flat TOML; an unknown key or a wrong type is an
error; a value beyond the hard limits in `config.py` is clamped to the limit (the clamps are printed by `plan` and `config`). Minimum
useful config: `data_paths = [...]` (the disks behind them are detected) and `protected_cgroups` / `containers` for the harm observations.

`analyze` and `status` are read-only and are run by **root or the service user** (`sudo -u iogov python3 -m iogov status`): the state directory
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

Kill switches (any one stops every run and aborts a running one at its next safe point): `touch /etc/iogov/disabled`,
`touch <state_dir>/DISABLED`, `IOGOV_DISABLE=1`, `enabled = false`, `systemctl stop|mask` of the timers.

Ownership of what the service trusts: `/etc/iogov` must be root-owned 0755, the config file root-owned and not writable by anyone else
(a config file writable by group/others, or owned by another user, is refused), and the kill-switch file root-owned. Every safety limit in
the config is clamped to the hard limits in `config.py` (the clamps are listed by `plan` and `config`).

Containers: prefer explicit `container_cgroups = { name = "/sys/fs/cgroup/..." }`. `containers = [...]` alone makes the service read the
daemon's `config.v2.json` files, which also contain container environment variables (secrets); that access widens what a compromised service sees.

Run the tests: `cd mdt-cockpit && python3 -m pytest iogov/tests -q`. A missing optional dependency fails the test (see
`tests/_testdeps.py`); `IOGOV_ALLOW_MISSING_TEST_DEPS=1` turns that into a skip on a developer machine.

## Install steps (documentation only; do them by hand, nothing here does)

1. Create the user and directories: `useradd --system iogov`; `install -d -o iogov -g iogov -m 700 /var/lib/iogov /var/lib/iogov/scratch`
   (the scratch directory is refused unless it is 0700; `StateDirectory=iogov` in the units would create the state dir 0700 anyway);
   `install -d -o root -g root -m 755 /etc/iogov` (root-owned: the service user must not be able to edit its config or remove the kill switch).
2. Copy the package to a root-owned location and `iogov.toml.example` to `/etc/iogov/iogov.toml` (root-owned 0644); edit `data_paths` etc.
3. Read-only access to the one disk: render `units/99-iogov-raw-read.rules.in` into `/etc/udev/rules.d/`, check with
   `udevadm test`, then `udevadm trigger`. Verify with the service user: reading works (`dd if=<node> bs=4k count=1 iflag=direct of=/dev/null`) and
   `test -w <node>` is false. Do not use the `disk` group and do not add CAP_SYS_RAWIO.
4. Render `units/*.in` (the services use `StateDirectory=iogov`, `ProtectKernelTunables`, `ProtectControlGroups`, an empty `CapabilityBoundingSet`, `PrivateNetwork`, `SystemCallFilter=@system-service`) by replacing the tokens below, install into `/etc/systemd/system/`, `systemctl daemon-reload`.
   Start with `python3 -m iogov plan` as the service user, then `systemctl start iogov-tick.service` once and read its journal records.
5. Enable `iogov-tick.timer` first (read probes and passive evidence only) and, optionally, `iogov-watch.service`. Run the FIRST write bench by hand,
   in a window where the protected workload is idle, as the service user: `python3 -m iogov bench --force` (gates and kill switches still apply),
   and compare `watch` episodes before and after it. Only then enable `iogov-bench.timer` (otherwise its first run is whenever the gate first passes).
6. Let it observe for a week before reading anything into `analyze` (the recognizer needs `baseline_windows` = 5 valid daily windows).

Template tokens: `@IOGOV_USER@` service user, `@IOGOV_PYTHON@` interpreter path, `@IOGOV_PYTHONPATH@` directory containing `iogov/`,
`@IOGOV_CONFIG@` config file, `@IOGOV_STATE_DIR@` state dir, `@IOGOV_SCRATCH_DIR@` scratch dir, `@IOGOV_KILL_SWITCH_FILE@` flag file,
`@IOGOV_DISK_NODE@` stable `/dev/disk/by-id/...` path of the disk, `@IOGOV_DISK_ID_PATH@` udev `ID_PATH` of that disk,
`@IOGOV_IO_WEIGHT@` io weight of the probe slice (default 50), `@IOGOV_TICK_INTERVAL@` tick period (default 5min).

The scratch directory must be on the filesystem of the data disks (otherwise the bench measures another device). The service's
`ReadWritePaths` are the state and scratch dirs only.
