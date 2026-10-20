# tools/io-stall — host IO stall instruments and A/B runners

The scripts behind [HOST-IO-INTERFERENCE.md](../../HOST-IO-INTERFERENCE.md) (copied unchanged from the
2026-10-20 investigation on gstammtisch, where they ran from `/var/log/mdt-stall/`). Mechanism and
results are in that document; this file says what each script does, how to run it, what it changes and
which host-specific literals it contains.

**These scripts are NOT portable as shipped.** They contain literals from gstammtisch (below). Nothing
substitutes for them: read the "Literals" section and edit the scripts for your host before running,
and never run an `ab_*` script on a production host in a game session.

## Passive instruments (read-only)

Run as root on the host. Start each as a transient unit that stops itself, so a forgotten recorder
cannot run for ever:

```bash
GAME_PID=$(docker top <uuid> | awk '/WSServer-Linux-Shipping/{print $2; exit}')   # the game's host pid
D=/var/log/mdt-stall; mkdir -p $D; DAY=$(date +%Y%m%d)
systemd-run --unit=mdt-stall-rec    --property=RuntimeMaxSec=7200 python3 $D/stall_recorder.py "$GAME_PID" $D/rec-$DAY.jsonl
systemd-run --unit=mdt-stall-dstate --property=RuntimeMaxSec=7200 sh -c "bpftrace $D/dstall.bt $GAME_PID > $D/dstate-$DAY.log"
systemd-run --unit=mdt-stall-fps    --property=RuntimeMaxSec=7200 sh -c "soulmask-monitor.sh --json 2 > $D/fps-$DAY.jsonl"
```

| Script | What it does | Reads / writes |
|---|---|---|
| `stall_recorder.py PID OUT.jsonl` | 1 s recorder: vda counters and `inflight`, host and game-cgroup PSI, game `memory.stat`, `/proc/vmstat`, dev/system/wings `io.stat`, game net counters, every game thread in D state with `wchan` and stack | reads `/proc`, `/sys`; appends to `OUT.jsonl` |
| `dstall.bt TGID` | bpftrace: game threads blocked in D state for 20 ms or more, total ms and count per kernel stack, 10 s windows. Needs BTF, no headers | reads kernel tracepoints; prints to stdout (redirect it) |
| `wb_delay.bt TGID1 TGID2` | bpftrace: how long after a game `write()` to an **ext4** file does writeback put that inode on disk (`vfs_write` → `writeback_single_inode`). Prints one line per inode: file name, `first_write->writeback` ms, `last_write->writeback` ms, bytes, pages written. Answers "how large is the data-loss window when fsync is a no-op (eatmydata)". tmpfs logs never reach disk and are not shown. **Approximate:** state is keyed by inode number only and the `writeback_single_inode` tracepoint is not filtered by superblock, so an inode number reused on another filesystem can print a bogus line. Takes two tgids (e.g. MAIN and CLIENT; pass the same pid twice for one server) | reads kernel tracepoints and a kprobe; prints to stdout (redirect it). Read-only; run it as a transient unit like `dstall.bt`, e.g. `systemd-run --unit=mdt-stall-wb --property=RuntimeMaxSec=1800 sh -c "bpftrace $D/wb_delay.bt $PID1 $PID2 > $D/wbdelay-$DAY.log"`. Measured with it: MEASUREMENTS.md M9 |
| `stall_summary.py SECONDS [END_EPOCH]` | one screen for the last N seconds: vda queue and latency, who did the IO, PSI, fault and reclaim rates, fps distribution | reads the recorder and fps files below |

`soulmask-monitor.sh --json 2` (installed by this guide) is the RCON fps source. The analysers read JSON
lines carrying `epoch` and `games[].fps`; check your monitor's output has them.

## A/B runners — **CHANGES STATE**

> **HAZARD (found 2026-10-20): run `systemctl daemon-reload` after every `ab_*` script, then let the MDT
> reconcile re-verify.** systemd 257 keeps one in-memory IO limit entry per device path spelling. The
> scripts clear the slice limits and restore them via `/dev/vda`, while the MDT reconcile uses
> `/dev/mapper/…-root`; the cleared entry stays behind as a stale all-`max` entry, and on the live host it
> won at apply time: `dev.slice`'s kernel `io.max` was left **empty, silently uncapped**. `daemon-reload`
> rebuilds the list from the drop-ins. The scripts are the recorded instruments and are deliberately not
> edited; this is a documented post-run step. Check afterwards: `cat /sys/fs/cgroup/dev.slice/io.max`
> must show the expected caps (`systemctl show` hides all-max entries, so it cannot confirm this).
> Details: [HOST-IO-INTERFERENCE.md §6](../../HOST-IO-INTERFERENCE.md#6-cgroup-io-controllers-what-fits-and-what-does-not).

These **change host state** and restore it on exit (an `EXIT` trap). They set `vm.dirty_*`,
`nr_requests`, `max_sectors_kb` and the `io.max` of `dev.slice` and `dev-gates.slice`, and start
write load (a dd loop plus fio) and an fdatasync probe unit. They create `/var/tmp/mdt-loadtest`
and `/var/lib/pterodactyl/mdt-probe` on the root filesystem and remove them afterwards. Run detached,
as a transient unit, outside player hours, and read the log:

```bash
systemd-run --unit=mdt-ab --property=RuntimeMaxSec=14400 /var/log/mdt-stall/ab_matrix.sh
tail -f /var/log/mdt-stall/ab-phases.log        # phase markers and the RESTORED line
# after the RESTORED line (post-run steps, mandatory):
systemctl daemon-reload                         # drops the stale per-spelling IO limit entries
cat /sys/fs/cgroup/dev.slice/io.max             # confirm the caps are back; wait for/trigger the MDT reconcile to verify
```

| Script | Experiment (guide section) | Phases |
|---|---|---|
| `ab_runner.sh` | A/B, 240 s phases (§4.2) | BASELINE, A0 defaults, A1 `dirty_bytes` 256M/64M, A2 + `nr_requests` 32, A3 + dev caps |
| `ab_a4.sh` | A4 (§4.2): bounded bytes in flight | `dirty_bytes` 64M/16M, `nr_requests` 16, `max_sectors_kb` 256, no caps |
| `ab_matrix.sh` | 13-cell matrix, 60 s cells (§4.3) | dirty limit x queue depth x request size, REF first and last |
| `ab_matrix2.sh` | 16-cell run (8 settings × 2 repetitions), 90 s cells, 64/128/256 KB requests (§4.4) | same method, small request sizes |

**Restore hazard on a persisted bound (live now).** `ab_runner.sh` and `ab_a4.sh` do NOT refuse to start when
`vm.dirty_bytes` is already set, and their `restore()` writes `vm.dirty_ratio`/`vm.dirty_background_ratio`,
which in the kernel resets `dirty_bytes` to 0 (ratio mode). The interim bounds are persisted on gstammtisch
(`/etc/sysctl.d/90-mdt-io-writeback-interim.conf`, `/etc/udev/rules.d/61-mdt-io-queue-interim.rules`,
HOST-IO-INTERFERENCE.md §4.7), so running either script silently reverts the `dirty_bytes` bound until the
next reboot (or `sysctl --system`) and installs the constant dev caps below. Only the matrix scripts refuse (exit 2) on a non-zero `dirty_bytes`.

Cell method (`ab_matrix*.sh`): set the knobs, read them back and skip the cell on a mismatch, drain
dirty and writeback below 4 MiB, 20 s quiet, then the load. The matrix scripts refuse to start (exit
2) when `vm.dirty_bytes`/`vm.dirty_background_bytes` are already non-zero. `LOAD_SECS`, `QUIET_SECS`
and `DRAIN_MAX` are environment overrides.

Analysers (read-only): `ab_analyze.py` (per-phase table for `ab_runner.sh`/`ab_a4.sh`) and
`ab_matrix_analyze.py [SINCE_EPOCH]` (per-cell table for `ab_matrix*.sh`; defaults to the last
`MATRIX ORIG` line in `ab-phases.log`). They combine `ab-phases.log`, `ab-probe.jsonl` (the fdatasync
probe), the recorder file, the fps file, the D-state log and the `ab-fio-*.json` outputs.

## Literals: edit for your host

Honest list; none of these is derived at run time.

| Literal | Where | Edit it to |
|---|---|---|
| `/dev/vda`, `/sys/block/vda/...` | all `ab_*` scripts, `stall_recorder.py` (`/sys/block/vda/stat`, `inflight`), `stall_summary.py` (the `vda` label) | the whole disk under `/` (resolve with `lsblk -s -no NAME,TYPE "$(findmnt -no SOURCE /)"`) |
| `dev.slice`, `dev-gates.slice`, `/sys/fs/cgroup/dev.slice/io.max` | all `ab_*` scripts, `stall_recorder.py` (`dev.slice`, `system.slice`, `wings.slice`, `soulmask_tmpfs.slice`) | your tier slice names |
| **Restore values for the dev caps**: dev `80M/40M/800/600`, gates `60M/30M/600/450` (read bw / write bw / read iops / write iops) | the `restore()` of every `ab_*` script and `ab_runner.sh`'s A3 phase | **the caps your host had before the test.** The scripts restore these constants, they do not read the originals: on any other host they would silently install gstammtisch's caps. Read yours first (`systemctl show dev.slice -p IOReadBandwidthMax ...`) and replace them |
| `dirty_ratio=20`, `dirty_background_ratio=10` | `ab_a4.sh` `restore()` (a constant); the other runners read the originals | your original values |
| `/var/log/mdt-stall` | all scripts | your log directory |
| `/var/lib/pterodactyl/mdt-probe`, `/var/tmp/mdt-loadtest` | `ab_*` scripts | a directory on the SAME filesystem (journal) as the game volumes; the test is meaningless elsewhere |
| `rec-20261020.jsonl`, `fps-20261020.jsonl`, `dstate-20261020.log` | `stall_summary.py`, `ab_analyze.py`, `ab_matrix_analyze.py` (dated file names) | the date of your recording |
| the game pid | `stall_recorder.py PID`, `dstall.bt TGID` | the pid you resolve at run time (it changes at every game restart; `dstall.bt`'s `$1` is the tgid, which for a single-process game equals the pid) |
| unit names `mdt-ab-probe`, `mdt-ab-load-seq`, `mdt-ab-load-rand` | `ab_*` scripts (and their `systemctl stop` in `restore()`) | leave, unless they collide |
| load shape | dd 1 GiB files from `/dev/urandom`, fio 4k randwrite, 2 jobs, `fsync=16` | keep it to compare with the guide's numbers |

Requirements: root, systemd (`systemd-run`), `fio`, `bpftrace` (for `dstall.bt`), `python3`, `sysctl`.
