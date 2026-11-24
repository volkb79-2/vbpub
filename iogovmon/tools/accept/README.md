# Acceptance-2 tooling (fill stage, reference probe, synthetic shift check, stats)

Imported from the 2026-11-18 run; its report is `docs/evidence/ACCEPT2-STAGE-2026-11-19.md`. `stage.py`, `refprobe.py` and
`stats.py` are stdlib-only (Python 3.11+, `tomllib`); `synth_shift_check.py` also imports the installed `iogovmon` package
(`$IOGOVMON_SRC`, default `/opt/iogovmon`). The runtime also needs `fio`, `fstrim`, `findmnt`, `systemctl` and `iogovmonctl`.
Host-free tests: `tests/test_accept_stage.py`, `tests/test_accept_tools.py` (lane `r0-r1`; `tools/` is outside the coverage scope).

## What the lease must cover
- Exclusive use of a host with a thin disk and no protected workload, for about 30 h (T0 to cleanup: 27 h in the 2026-11-18 run). A
  `/srv/cow` that is a SEPARATE mount from `/` is mandatory (st_dev check); the tools refuse to start otherwise.
- Root. The stage stops `fstrim.timer`, comments out the cron `fstrim` line (`cron_fstrim_file`), remounts `nodiscard` (B5a), fills
  `/srv/cow` to 94 % and `/` to 85 % (hard limits 95 % / 88 %), and writes about 26 GiB of probe files. Say so in the lease.
- iogovmon installed, recommend-only, with the condensed config (`window_hours = 1`, `read_max_attempts = 128`). Transient units die with a reboot.

## Run
1. `cp stage.example.toml <workdir>/stage.toml`; fix `[paths]` (every path is configurable; `--config` defaults to `/root/iogovmon-accept2/stage.toml`).
   The fill dirs must be `iogov-fill` and the stage files `iogov-*` directly under `/srv/cow` or `/var/tmp`, else the run refuses to start.
2. Rehearse first: `stage.py --rehearsal` (seconds-long phases, 1 GiB budget, dry annotations; own log, snap, summary and phases files, but the same lock, STOP file and fill dirs as a real run).
3. Reference probe: `systemd-run --unit=iogov-a2-refprobe --property=CPUQuota=20% --property=Nice=10 --property=MemoryHigh=256M python3 refprobe.py --run`
   (`--workdir` moves its lock, STOP file and CSVs; keep it equal to the stage workdir).
4. Stage, with T0 = the fresh iogovmon start (at most `t0_max_age_s` old): `systemd-run --unit=iogov-a2-stage --property=CPUQuota=100%
   --property=Nice=10 --property=MemoryHigh=3G --property=TimeoutStopSec=900 "--property=ExecStopPost=/usr/bin/python3 <dir>/stage.py --cleanup" --collect
   python3 <dir>/stage.py --run --t0 <ISO>`. Units: `iogov-a2-stage`, `iogov-a2-refprobe`.

## Phases (1 h windows aligned to the UTC hour; W0 = first boundary at least `w0_min_lead_s` after T0)
A-pre, A0 baseline (6 windows, nothing runs), then B1 (`/srv/cow` 50 %), B2 (`/srv/cow` 80 %, `/` 60 %), B3 (two 30 min churn bursts), B4 (94 % / 85 %),
B5a (delete, no trim, `nodiscard`), B5b (discard restored, `fstrim -v`). Each loaded step is followed by quiet windows. A1/A2 (live control) are off.

## STOP, resume, cleanup
- `touch <workdir>/STOP`: clean exit, deletes everything it created, restores fstrim/discard/cron, renames the file to `STOP.consumed-<epoch>`.
  `touch <workdir>/REFPROBE-STOP` does the same for the probe (the probe files are kept for a restart).
- `stage.py --resume --run --t0 T --w0 W` replaces a stage during A0 only (the same T0 and W0, checked against `stage-state.json`).
- `stage.py --cleanup`: after a crash or a host reboot. Rc 0 = clean; rc 4 = something remains (read `stage.log`). It also kills orphan fio and resets the throttle.
- Refprobe: wait until `iogov-a2-refprobe` is inactive, then `refprobe.py --cleanup` (about 26 GiB; refuses while the probe holds the lock).
- Restores to verify after any end: `systemctl is-active fstrim.timer` = active; the cron fstrim line has no `#IOGOV-A2-DISABLED# ` prefix;
  `findmnt -no OPTIONS /srv/cow` shows the discard option again; no `fstrim-timer-was-active`, `discard-restore.json`, `cron-fstrim-disabled` in the workdir;
  no `iogov-fill` dirs; `systemctl show iogovmon.slice -p IOReadIOPSMax --value` is empty.

## Reading the journal
Read in windows, never whole, and below the measured IO: `nice -n 19 ionice -c3 journalctl -u iogov-a2-stage --since '2026-11-18 02:00' --until '2026-11-18 08:00'
--no-pager -o json`. journald vacuums at 85 % used on `/`, so `stage.py` also saves `snap/journald-pre-B4.txt` before B4.

## Analysis
- `stage.py --summary [--journal-dir D --phases-file F --summary-out O]` rebuilds `stage-summary.txt`.
- `python3 -I stats.py <extracted-root> > stats.md`: per-phase p50/p95/p99 from `<root>/var/lib/iogovmon/journal/journal-2026-11.jsonl`,
  `<root>/root/iogovmon-accept2/stage-phases.json` and `<root>/root/iogovmon-accept2/refprobe/*.csv`. The 2026-11-18 output is `docs/evidence/accept2-stage-2026-11-19/stats.md`.
- `synth_shift_check.py [--live-dir D --config C --synth-dir S --attempts N]`: offline recognizer check on a copy of the live state; the live dir is only read.
