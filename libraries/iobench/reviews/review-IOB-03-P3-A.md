# Review IOB-03 P3, scope A: safety and the real host

Target: `libraries/iobench`, worktree `.worktrees/iob-03-20261021`, HEAD `30a847beb`. Read-only review: no edits, no pytest, no gates, no devices touched.
Paths below are relative to `libraries/iobench/src/iobench/` unless they start with `tests/` or `docs/`.

Verdict: **NOT-READY**. There are 4 BLOCKING findings. Three of them are things FakeHost cannot show, because RealHost (`host.py`) is excluded from coverage and nothing tests it.

## BLOCKING

### B1. A refused or contended invocation deletes the live run's restore journal
- Where: `stages.py:227-229` (`except Refusal: self.cleanup(...)`), `stages.py:248-251` (`cleanup`), `restore.py:167-186` (`Safety.restore`, last line `self.host.remove(self.journal)`).
- Scenario:
  1. Run A holds the lock and has journalled a knob, for example `dirty_bytes`.
  2. Run B starts. `s0()` raises `Refusal("already_running")` at `acquire()`. The same happens for any `Refusal` before `acquire()`, such as the fio-version check.
  3. `execute()` calls `cleanup()`, which calls `Safety.restore()` on B's empty `Safety`. That function unconditionally ends with `host.remove(JOURNAL)`.
  4. A's `/run/iobench/restore.json` is gone. If A is then killed, nothing restores the knobs. This breaks SAF-02, and also SAF-08, which says the second invocation "writes nothing".
- Why the test is green: `tests/test_saf.py::test_saf_08_single_instance_in_a_run` (l.396) takes its snapshot when no journal exists. Deleting a non-existent file changes nothing, so the test cannot go red. The unit variant `test_saf_08_single_instance` never reaches `restore()`.
- Fix:
  - Make `cleanup()` and `restore()` act only when `self.locked` is true and `self.entries` or `self.files` is non-empty.
  - Remove the journal only when this `Safety` wrote it (track a `journal_written` flag).
  - Add a test that creates the journal as run A, makes B fail on the lock, and asserts A's journal bytes are unchanged.

### B2. Restoring `dirty_bytes` / `dirty_background_bytes` leaves the host with `dirty_ratio = 0`
- Where: `matrix.py:77-86` (`apply`), `restore.py:19-23` (allowlist: bytes knobs only), `restore.py:167-186`.
- Scenario:
  1. Production hosts normally run with `dirty_bytes = 0` and `dirty_ratio = 20`.
  2. `apply()` writes `dirty_bytes = X`. The kernel handler then forces `dirty_ratio = 0`. The same coupling exists between `dirty_background_bytes` and `dirty_background_ratio`.
  3. Restore writes `dirty_bytes = 0` back. The handler fires again because the value changed, and again forces the ratio to 0. Both read 0.
  4. The kernel now computes its dirty threshold from a ratio of 0, which means heavy writeback throttling on a live game-server host.
  5. The read-back check passes (it reads only `dirty_bytes`), so the report says `verified`.
  6. Side effect: the next `matrix` run reads `dirty_ratio` as 0 in `plan_base` (`matrix.py:121`) and derives a wrong `T_eff`.
- No test or journal entry covers the ratio knobs.
- Fix: journal `dirty_ratio` and `dirty_background_ratio` before the first change. On restore, write back whichever member of each pair was originally non-zero. If bytes was 0, write the ratio, which zeroes bytes. Verify the pair by read-back. Add a FakeHost model of the coupling so a test can go red. Confirm on r1002 in IOB-ACC-01.

### B3. In a container the mount-namespace check passes silently
- Where: `guard.py:130-146` (`mount_evidence`, `check_namespaces`), `host.py:178` (`read_text`).
- Scenario:
  1. In a PID-namespaced container (the devcontainer, docker), `/proc/1` is the container's own init.
  2. `/proc/self/mountinfo` equals `/proc/1/mountinfo`, so `own == init` and the check passes.
  3. A device mounted in the host mount namespace is invisible to both reads. `swap` and sysfs `holders` are global, but the mount evidence is not.
  4. This is the exact case SAF-09 and TGT-01 exist for, and it is silently accepted.
- Related: `LOCK` and `JOURNAL` live in `/run`, which is private per container. Two runs from different namespaces do not exclude each other, and a restore from another namespace cannot see the journal.
- Related: when `/proc/1/mountinfo` is unreadable (EACCES without ptrace), `RealHost.read_text` raises a raw `PermissionError`. The CLI reports it as exit 1, not as a refusal with a remedy.
- Fix: derive the fact instead of assuming it. In `s0`, `target init` and `load_target`, refuse unless the PID namespace is the initial one (`readlink /proc/self/ns/pid == pid:[4026531836]`) and `/proc/self/ns/mnt` equals `/proc/1/ns/mnt`. Catch `OSError` on the `/proc/1` read and turn it into `Refusal` (exit 2). Add a FakeHost knob for a non-initial PID namespace and a test that goes red without the check.

### B4. A failing `blkid`, `wipefs`, `docker ps` or `systemctl` is read as "nothing there"
This is the "absence for emptiness" anti-pattern from `AGENTS.md`. Return codes are never inspected.
- `guard.py:109-112` (`blkid_fields`), `guard.py:177-180` (`violations`): if `blkid -p` or `wipefs -n` fails (not root, device busy, missing binary on PATH), stdout is empty. The device then has no signature and `identity_missing` is not raised when the disk reports a serial. `init_device` then writes the marker over the last MiB of a device that may carry a GPT backup header or data. Real `blkid -p` exits 2 for "nothing found" and 0 for "found". Anything else is an error.
- `restore.py:110-111` (`live_preflight`): a failing `docker ps` gives zero containers, so production is declared quiet. `systemctl is-active` returns 3 for inactive and 4 for an unknown unit, so a typo in `--production-unit` is silently "inactive".
- Fix: accept only the documented return codes (`blkid` 0 or 2; `wipefs -n` 0; `docker ps` 0), otherwise `Refusal("probe_failed", ..., remedy)`. For units, use `systemctl show -p LoadState --value` and refuse on `not-found`.
- Tests: FakeHost `_blkid` returns rc 2 for an empty device and cannot return an error. Add a case where `blkid` fails and assert refusal.

## SHOULD

### S1. `RealHost` does not bind the argv to the authorised extent; the allowlist is unused at runtime
- Where: `host.py:294-304` (`write_range`, `discard_range`), `host.py:141-148` (`run`, `spawn`), `devio.py:151-170` (`_ALLOWED`, `"fio": lambda a: True`).
- `FakeHost.write_range` (`testing.py:858-866`) rejects an argv whose `filename`, `offset` or `size` differ from `(target, offset, length)`. `RealHost.write_range` checks the extent parameters and then spawns whatever `argv` it was given. The parity the notes claim is "by construction" does not hold here.
- `RealHost.run` and `spawn` apply only a denylist (`check_argv`). `is_allowed_argv` is used only in tests. So `RealHost.run(["blkdiscard", "-o", "0", "-l", "9999999999", "/dev/sda"])` passes `check_argv`.
- Today the callers are consistent, so there is no live exploit. This is the only enforcement point for ARC-05 and TGT-03 on the real host.
- Fix:
  - In `RealHost`, `parse_job(argv)` and assert `filename == target.path`, `offset`, `size` and `end <= size - TAIL_BYTES`.
  - For discard, compare `argv` to the builder output.
  - Make `run` and `spawn` call `devio.is_allowed_argv`. Move the allowlist into `host.py` to avoid the circular import.
  - Tighten `"fio"` to refuse a `--filename` that is not the target or under the work dir.

### S2. Residual TOCTOU: nothing makes the kernel enforce "not in use"
Verification is a stat plus `blkid` immediately before launch. A mount, md assembly or dm table can still appear before fio opens the path, and the path itself is re-resolved by fio.
- Fix: add `--exclusive_open=1` to every fio argv for a device target and open with `O_EXCL` in `write_marker`. The kernel then returns EBUSY for a mounted or claimed device, which also covers holders that are active but not mounted. Store the canonical path (`realpath`) in the `Target` and re-check `realpath(path) == stored` in `_verify_live`.
- Also (`guard.py:184`): an unmounted but active md/dm holder only produces the overridable `signature` violation. Refuse any non-empty `holders` outright.

### S3. `--abort-psi` and `--protect-cgroup` are required but not validated
- `--abort-psi nan` is accepted by `float()`. `psi > nan` is always False, so the abort never fires. A negative value or one above 100 is accepted too. See `restore.py:77-83` and `cli.py:63`.
- The cgroup's `io.pressure` is not read at preflight. A typo shows up as `FileNotFoundError` one second after the first fio has started writing (`stages.py:118-122`).
- `AbortWatch(cgroup=set, threshold=None)` under `--installer` raises `TypeError` (`restore.py:77`).
- Fix: validate in `live_preflight`: `math.isfinite(x) and 0 < x <= 100`; read `io.pressure` once and refuse with a remedy if it is missing (PSI disabled or cgroup absent).

### S4. A stale `/run/iobench/abort` file aborts every later run after it has already written
`ABORT_FILE` (`restore.py:15`, `:74`) is never cleared or checked at start. The abort is seen on the first tick, after S1 or the first writer has launched.
- Fix: refuse in `s0` if the file exists (`Refusal("stale_abort_file", ..., "remove /run/iobench/abort and rerun")`). Do not delete it silently.

### S5. `verify_io_max` before `begin()` gives a false exit 9
- Where: `stages.py:248-255` (`cleanup`), `restore.py:189-197`.
- `io_max_before` is `{}` until `begin()`. Any failure between `load_target` and `begin()` (a `HostError` or `RunFailed` in the `s0` identity step) sets `target`, so `verify_io_max()` compares a real snapshot with `{}` and reports exit 9 "io.max changed". That masks the real failure.
- Separately, the walk compares dict keys. A container started or stopped during a long run (production hosts do this) changes the key set and gives exit 9 although no `io.max` value changed. SAF-04 reads "every slice's io.max". A cgroup removed between `exists` and `read_text` raises instead.
- Fix: track `snapshot_taken`, and compare only the paths present in both snapshots (report added and removed paths as info). Make the walk tolerate `FileNotFoundError`.

### S6. Restore compares `current != applied` against a value the kernel may have normalised
- `restore.py:171-178`. `nr_requests` and `max_sectors_kb` are clamped or rounded by the kernel. If the written value reads back differently, restore reports `changed_by_other`, skips the knob and exits 0, leaving the host changed.
- Also, `apply()` returns False on any `OSError`, including a journal write failure on `/run`. A failing journal write is then indistinguishable from "kernel rejected the value".
- Fix: after writing, re-read the knob and journal that value as `applied`. Let a journal I/O error propagate.

### S7. Restore stops at the first exception and re-validates nothing it reads from the journal
- `restore.py:171-186`: an `OSError` from `read_text` or `write_sysfs` (device gone) aborts the loop. The remaining knobs and the leaked files are left behind, and the exception reaches the user as a generic exit 1.
- A journal entry's `path` is not re-checked with `knob_allowed()`, and `files` are unlinked without checking the `iobench-` or `.iobench` name pattern. A corrupt or edited journal could write any sysfs file or unlink any file as root.
- Fix: iterate all entries, collect errors, then raise `VerificationFailure` (exit 9) with the full list. Re-check `knob_allowed(path)` and the file name pattern on load. The regex also accepts `..` as a disk name (`[A-Za-z0-9_.-]+`).

### S8. Orphan fio on SIGKILL, and on a failed second spawn
- `host.py:121-137`: `_ProcHandle` has no process group and no `PR_SET_PDEATHSIG`. If iobench is SIGKILLed, fio keeps writing to the device for up to its runtime. The next start's `recover_previous` restores knobs while that fio is still running.
- `matrix.py:105` and `:155`: `[self.spawn(writer), self.spawn(fsyncj), ...]`. If the second spawn raises, the first fio is not killed, because the exception is outside `supervise`.
- Fix: spawn with `start_new_session=True` and kill the process group. Set PDEATHSIG through `preexec_fn` or a ctypes prctl. Wrap the multi-spawn in a try/kill. At `s0`, refuse if an `iobench-` fio from an earlier run is still alive.

### S9. `wait_inflight_zero` result is ignored and signals are deferred
- `stages.py:134`: after kill, a False return (inflight did not reach 0 in 30 s) is dropped and knobs are restored anyway, with no warning in the result. On a file target the whole disk's inflight, including production IO, is read.
- `RealHost.install_signals` only sets a flag, and `time.sleep` resumes after the handler (PEP 475). Latency is bounded by `TICK_S = 1`. But a long blocking `host.run`/`discard_range` (blkdiscard of several GiB) or `handle.wait()` defers SIGTERM until it returns, and a second SIGTERM does nothing.
- Fix: record `inflight_not_drained` in `warnings`. Run the blocking calls in a polling loop or `Popen.wait(timeout)` slices.

### S10. `check_argv` is a narrow denylist and misses real `io.max` paths
- `host.py:69-81`: only tokens starting with `IO` or `-pIO` are checked. `systemd-run --property=IOWriteBandwidthMax=...`, `-p BlockIOWriteBandwidth=...` (legacy, translated to `io.max`) and `-p=IO...` pass. `tests/test_arc.py::test_arc_05_*` and `tests/test_saf.py:139-142` only test the `-p IOWeight=10` form.
- `check_sysfs_path` (`host.py:84`) checks the suffix `io.max` only. A `..` or symlink to an `io.max` file would pass.
- Fix: normalise (strip `--property=`, `-p`, case-fold), and refuse `IO*` and `BlockIO*` in any form. Better, allow only the exact `-p RuntimeMaxSec=N` produced by `probe_unit_argv`. Resolve `realpath` in `check_sysfs_path` first.

### S11. File-target filesystem identification is textual
- `guard.py:312-319` (`fs_info`), `guard.py:428` (`on_root = fs.mountpoint == "/"`), `guard.py:331-361` (`init_file`).
- `fs_info` uses a prefix match on the unresolved path. `/mnt/data/../x` is classified under `/mnt/data`, so the root-filesystem quiet-window rule and the 10 %-of-free rule are skipped for a file that is really on `/`.
- "Root filesystem" is `mountpoint == "/"`. A btrfs subvolume (`/home`) or a bind mount of the same filesystem is not recognised as root.
- Mountpoints with spaces appear escaped as `\040` in mountinfo and are never unescaped.
- Fix: `realpath(dirname(path))` first; compare filesystem identity (st_dev or fs UUID) with that of `/`; unescape mountinfo.
- Also, a path containing `:` in a fio `--filename` splits into several files in fio. Reject `:` in target and work-dir paths.

### S12. A stale sidecar is treated as "matching"
- `guard.py:343`: `st is None or host.exists(sidecar_path(path))`. TGT-06 requires a *matching* sidecar (inode, size, fs uuid). Only existence is tested.
- A user file later created at a path with a leftover sidecar passes, and `write_marker` then overwrites `size - 1 MiB` of that user file with the operator-supplied `--size`.
- Fix: load the sidecar and compare inode, size and uuid against `lstat`. Also re-check `nlink` and "not a symlink" in `_check_file` on every later stage. These are only checked at init.

### S13. `--dry-run` answers "ok" for invocations a real run refuses, and it writes
- `dryrun.py` never runs `live_preflight`, the namespace check or the work-dir checks. `calibrate --target X --dry-run` exits 0, while the real command exits 2 (`abort_guard_required`, SAF-06).
- `init_file(dry_run=True)` calls `RealHost.probe_odirect` (`host.py:202-210`), which creates and unlinks `.iobench-odirect-probe` in the target directory. The open uses `O_CREAT` without `O_EXCL | O_NOFOLLOW`, so it would unlink a pre-existing file of that name.
- Fix: have the dry run call the same preflight in "report" mode and print which refusal would fire (exit 2). Use `O_EXCL | O_NOFOLLOW` for the probe and skip it in dry-run.

### S14. `RealHost.devno()` raises where FakeHost returns None
- `host.py:192-197` calls `os.stat`, so a missing path raises `FileNotFoundError`. `FakeHost.devno` returns `None`, so the tested path is `Refusal("not_block_device")` (exit 2). The real CLI gives exit 1 for a typo'd device path.
- Same family: a `HostError` from `write_marker` identity re-verification during `target init` also exits 1. SPEC §17 says refusal is 2.
- Fix: catch `FileNotFoundError`/`NotADirectoryError`/`PermissionError` in `RealHost.devno` and `read_device` and raise `Refusal`. Map `HostError` raised by identity checks to exit 2.

## NIT

- N1. `stages.py:187`: `check_workdir(planned_volume=GIB)` is a literal standing in for the real planned volume (shadowing default). The effective floor is always 4 GiB. Pass the sum of the file caps.
- N2. `matrix.py:151-153` / `devio.probe_unit_argv`: `systemd-run` is launched without `--wait`, so `handles[2].wait()` returns when the unit is queued. `read_text(out_path)` right after `supervise` can race the probe. On abort, nothing stops the unit (no `systemctl stop` builder); `RuntimeMaxSec` bounds it to about 90 s but the work-dir files are removed under it.
- N3. `host.py:62-67`: `_ProcHandle.kill` does not reap or wait. A SIGKILLed fio in D-state keeps the zombie until `wait()`.
- N4. `host.py:289-291`: `read_marker` of a device is not O_DIRECT; harmless on Linux but worth a comment. `write_marker` with `partuuid is None` (serial-only devices) re-verifies only devno and size.
- N5. `guard.py:233-239`: `--expect-serial` identifies the parent disk, not the partition. Naming one partition's disk serial authorises any partition of that disk. Document it, or require the PARTUUID whenever one exists.
- N6. `cli.py`: device-only and file-only flags are accepted silently together (`--confirm-quiet-window` on a device init; `--state-dir` on `restore`, whose journal is the fixed `/run/iobench/restore.json`; `--work-dir` on `calibrate`). Reject with a usage error.
- N7. `restore.py:83`: three consecutive 1-second ticks over a 10-second-average figure are strongly correlated. The spec says so, so this is only a note for the acceptance probe.
- N8. `root_target_too_large` and `workdir_on_root` remedies offer the override flag (`--allow-large-root-target`) next to "a smaller --size". Order the safe remedy first.
- N9. The scratch directory `/run/iobench/<run_id>` and fio logs are left on abort. They are tmpfs and small; remove them in `cleanup`.
- N10. `dirty_bytes` candidates in `matrix --knob` are unbounded. A tiny value (a few pages) accepted by the kernel stalls production writes during the run. Bound candidates (for example `<= T_eff`, `>= 16 MiB`) at CLI level.

## Item-by-item answers

1. Writes outside the target:
   - The argv are built by typed builders and the extent is re-checked in `_check_job` and `RealHost._check_extent`. The reserved tail is reachable only through `write_marker`.
   - Gaps: S1 (argv not bound), S2 (no exclusive open; path re-resolved), S11/S12 (file target identity).
   - Punch-hole and the 10 % rule exist (`guard.py:352-358`) but have the S11 bypass.
   - The fill's free-space re-check runs at each chunk and is real.
2. Both mountinfo files are read. Inside a container `/proc/1` is the container's own init, so the check silently passes (B3).
3. The journal is written before the first change (`set_knob` and `register_file`). Restore covers exit, exception, signal flag and next start. B1 deletes another run's journal. B2 is an incomplete restore. S6 and S7 cover clamped values, partial restore and unchecked paths. The `current != applied` comparison is the right operand but fails on kernel-normalised values.
4. `io.max` is never written (the suffix check and token denylist hold for the shapes tested). S10 lists bypasses through systemd forms. The before/after comparison is real but gives false exit 9 (S5).
5. The PSI path is real (`/sys/fs/cgroup/<cg>/io.pressure`, `some avg10`). Both flags are required with no defaults, but `nan` passes (S3) and a stale abort file triggers an abort after writes begin (S4). The wait for inflight is real but its result is dropped (S9).
6. A stale lock is not a problem: `flock` is released at process death and the file is only truncated. The weaknesses are the private `/run` per namespace (B3) and that `restore()` removes the journal without holding the lock (B1).
7. Exit codes match §17 for `Refusal` 2, `Aborted` 8, `VerificationFailure` 9 and `check` 3-7. Exceptions: S14 (exit 1 for typo'd path or identity change), S5 (spurious 9). Refusal messages prescribe safe remedies; only N8 is a nit.

## Spot-check of generated CLI review cases (`docs/cli-review.toml`, 173 cases)

All 173 expected values were recorded from code behaviour on a FakeHost. Distribution: every `calibrate`, `matrix`, `run`, `restore` and `target init` case uses `--dry-run`; 12 `check` cases all exit 7; there is no case with exit 3, 4, 5, 6, 8 or 9 and no guard refusal (mounted device, production running, missing `--abort-psi`). The stdout assertion is a single substring (`"command"`), so most cases cannot distinguish behaviours.

Fifteen spot-checks:

1. `calibrate --target /dev/vda7 --dry-run` (exit 0): rationale "smallest valid invocation". Without `--dry-run` the same command exits 2 (SAF-06). The recorded expectation matches the code, not the spec. Fails semantics (S13).
2. `calibrate ... --dry-run --abort-psi 40` (exit 0): SAF-06 needs both `--protect-cgroup` and `--abort-psi`; a half-specified guard is blessed.
3. `calibrate ... --dry-run --protect-cgroup wings.slice` (exit 0): the mirror of 2.
4. `matrix ... --dry-run --knob nr_requests --candidates 16777216,33554432` (exit 0): the candidate values are `dirty_bytes` magnitudes, 16 M for `nr_requests` and 16 GiB for `max_sectors_kb` (a sibling case). Templated nonsense recorded as reviewed.
5. `matrix ... --dry-run --knob max_sectors_kb --candidates 16777216,33554432` (exit 0): same as 4.
6. `restore --dry-run --state-dir /var/lib/iobench` (exit 0): `--state-dir` has no effect on restore (fixed `/run/iobench/restore.json`); an inert flag is recorded as meaningful (N6).
7. `target init /dev/vda7 ... --dry-run --confirm-quiet-window` (exit 0): the flag is documented as file-target only.
8. `calibrate ... --dry-run --work-dir /bench/w` (exit 0): calibrate uses no work dir (`need_workdir=False`); silently ignored.
9. `run --target /dev/vda7 --dry-run` (stdout contains `"command"`): `run` should print a calibration or matrix plan. The assertion cannot tell which; an existing calibration makes it a matrix plan.
10. `check --section iocost_model`, `--section caps`, `--calibration` (all exit 7): plausible for the fixture, but all 12 `check` cases share exit 7. States 0, 3, 4, 5, 6 are never reviewed here.
11. `show --calibration --ceilings` and the other nine pairs (exit 2, stderr `[ERROR]`): correct per mutual exclusion; the stderr assertion does not name the conflicting options.
12. `matrix --target ... --candidates ... --dry-run` (exit 2) and `--repeat --dry-run` (exit 2): correct per the `Requires` constraints. This is the one group that is spec-derived.
13. `ceilings add ... --bps 1e9 --duration-s 600 --dry-run` (x17): plausible, but 14 near-identical variants add nothing. There is no negative case for `--duration-s < 60` (CEI-01) or an unknown `--workload`.
14. `target init --file /bench/t.img --size 2147483648 ... --dry-run` (exit 0): fine on the fixture filesystem. There is no case for the root-filesystem 10 % refusal, which is the claimed TGT-06 behaviour, and the dry run creates a probe file (S13).
15. Duplicate invocations (`calibrate --target /dev/vda7 --dry-run` x3, `show --proposals` x3, `matrix --dry-run` x3): same behaviour recorded under different ids. They inflate the count without reviewing anything.

Conclusion on the cases: they pass the surface audit but are not an independent semantic review. They encode S13 and N6 as accepted behaviour. Add reviewed cases for at least: guard refusal exit 2 (`calibrate` without `--abort-psi`, without `--confirm-quiet-window` with containers), `--abort-psi nan`, `check` exits 3 to 6, and `restore` with a leftover journal.

## Safety test reasoning (would it go red if the guard were removed?)

| Test | Verdict |
|---|---|
| `tests/test_saf.py::test_saf_08_single_instance_in_a_run` | Green with B1: the journal does not exist when the snapshot is taken. |
| `tests/test_tgt.py::test_tgt_03_reserved_tail_one_per_writer_type` | Exercises FakeDevice `check_range`, not `RealHost._check_extent`. Deleting the real guard stays green. |
| `test_saf_04_io_max_never_written` | Red if `check_sysfs_path` or `check_argv` is removed. Covers only `-p IOWeight`, not `--property=` or `BlockIO*` (S10). |
| `test_tgt_01_refuses_mounted_other_namespace` and `test_saf_09_..._in_a_run` | Red if `check_namespaces` is removed. No case where self and PID 1 agree but are both blind (B3). |
| `test_tgt_04_*_refused`, `test_tgt_04_installer_flag_does_not_bypass_refused` | Red if `verify_target` is removed. Good. |
| `test_saf_02_does_not_clobber_newer_value` | Red if the `current != applied` check is removed. No kernel-clamped value case (S6). |
| `test_saf_02_restore_on_exception_and_signal` | Calls `s.restore()` by hand and asserts only the fake's `signal_pending`. Passes with the real handler removed. The in-run variants (l.291, l.302) are the real tests, and still use FakeHost signals, not `RealHost.install_signals`. |
| `test_saf_03_readback_mismatch_exit_9` | Red if the read-back is removed. It does not catch B2, because the pair coupling is not modelled. |
| `test_saf_06_abort_paths_and_required_flags` | Red if the PSI threshold or the required-flags check is removed. No `nan`, missing-cgroup or stale-abort-file case (S3, S4). |
| `test_saf_06_abort_in_a_run_kills_fio_and_restores` | Red if kill or restore is dropped. FakeHandle has one process, so the group and orphan cases cannot appear (S8). |
| `test_tgt_01_refuses_*` / `test_tgt_02_override_matrix` | Red as expected. FakeHost `blkid` cannot fail, so B4 is unreachable in tests. |
| `tests/test_arc.py` argv tests | `FakeHost._log` calls the real `check_argv`, so these do guard the denylist. They do not guard the missing allowlist (S1). |

Overall: the FakeHost tests are strong for logic and weak at the host boundary. A `RealHost` contract test that runs the same scenarios against a loop-backed file (no device) would catch S1, S14 and parts of B3/B4.

## Verdict

**NOT-READY.** B1 to B4 must be fixed first. S1 to S6 should be fixed before r1002 acceptance. The rest can be folded into the same wave or tracked in the backlog.

---

## Re-review r2 @201903edf

Scope: the five commits `30a847beb..201903edf`, read-only. I read `restore.py`, `guard.py`, `host.py`, `devio.py` and the safety parts of `stages.py`, `matrix.py`, `testing.py`, plus `tests/test_review_a.py`. I ran `tests/test_review_a.py`, `tests/test_saf.py` and `tests/test_tgt.py` in-process, serially, under nice/ionice: 117 passed. I did not spot-revert; I traced each fix against the scenario I wrote instead (reasoned below).

**Verdict: READY for scope A (safety and the real host).** All four BLOCKING findings are closed and I found no regression. One item the implementer left open, **N2 (`systemd-run` without `--wait`), is BLOCKING for the P4 matrix stage on r1002**. It is a correctness defect, not a safety one; see "Open items". A calibrate-only P4 can start before it is fixed.

### Finding by finding

| Finding | Status | Why |
|---|---|---|
| B1 journal deleted by a refused run | **closed** | `Safety.restore()` returns at once unless `journal_written`. `_write_journal`, `recover_previous` and `Runner.cleanup` all require the lock. `recover_previous` marks the prior session as owner only while this session holds the lock. `test_rev_a_b1_*` seeds a real journal first, so it would go red now (the old test could not). |
| B2 `dirty_ratio` left at 0 | **closed** | Ratio knobs are journalled before the first bytes write. Restore writes the originally non-zero member last, then reads back both members of the pair. I walked three cases: bytes=0/ratio=20, bytes nonzero/ratio=0, and two cells on one knob (first original wins). FakeHost now models the coupling. |
| B3 container passes the mount check | **closed** | See "B3 specifics" below. |
| B4 probe return codes | **closed** | `devio.probe` refuses any rc outside `ok`: blkid (0, 2), wipefs (0), docker ps (0), fio --version (0), `systemctl show` for LoadState (a typo gives `not-found`, a refusal), `is-active` (0, 3). `_verify_live` applies the same rule to its own blkid. |
| S2 holders and exclusive open | **closed, one residual** | A non-empty `holders` is now a hard `holder_active` refusal (not overridable, also on `load_target`). The device marker open uses `O_EXCL`. Residual: see N-r2-1. The fio `--exclusive_open` half is out of scope by your instruction. |
| S3 abort guards | **closed** | `abort_psi` must be finite and in (0, 100]; `io.pressure` must be readable at preflight; an installer run with half a guard is refused; `AbortWatch` tolerates `threshold=None`. |
| S4 stale abort file | **closed** | `stale_abort_file` refuses in S0 before any write. |
| S5 io.max verdict | **closed** | No verdict without a before snapshot; only cgroups present both times are compared; appearances and disappearances are warnings; a vanishing cgroup mid-walk is tolerated. |
| S6 clamped values | **closed** | `applied` is the value read back; a rejected write drops the entry; a journal I/O error propagates. `apply()` returns False when the kernel clamps, so the cell is skipped rather than recorded under the wrong setting. |
| S7 restore stops at first error | **closed** | Every entry and file is attempted; failures are aggregated into one exit-9 `VerificationFailure`. `parse_journal` validates knob allowlist, file naming (`iobench-*` or `.iobench*`, absolute, no `..`) and the coupled map before anything is written or unlinked. |
| S8 orphans, partial spawn | **closed (RealHost untested)** | Own session plus `killpg`, `PR_SET_PDEATHSIG`, reap with a 5 s cap. `spawn_all` kills the earlier fio if a later one fails. `orphan_fio` refuses the next run, with the lock held, before any restore. Only the RealHost half is unverifiable until IOB-ACC-01. |
| S9 drain ignored | **half closed** | `inflight_not_drained` is now a warning in the restore report. The deferred second SIGTERM during a blocking `host.run` is still open (see below). |
| S10 argv denylist | **closed** | One regex covers `IO*`, `BlockIO*` and `-p`, `-p=`, `--property=` spellings, case-insensitive. I checked the systemd property families by hand: Weight, Device(Weight/Latency), Read/Write bandwidth and IOPS Max. |
| S11 file-target identity | **closed, one residual** | `realpath` before the mountinfo lookup, octal unescape, root detection by device or `/dev/` source, `:` refused for target and work dir. Residual: N-r2-2. A ZFS dataset (source is not under `/dev/`) of the root pool would not count as root; irrelevant for a block-device P4. |
| S12 stale sidecar | **closed** | The sidecar must match inode, size and fs uuid; symlink and `nlink` are re-checked at every stage in `_check_file`. |
| S13 dry run | **not re-reviewed** | Out of scope. (The O_DIRECT probe file is no longer created in a dry run and the real probe is `O_EXCL | O_NOFOLLOW`; fine.) |
| S14 exit codes | **half closed** | `RealHost.devno` returns None for unusable paths, and `HostError` from the marker claim becomes `Refusal` (exit 2). But see N-r2-1: the kernel's EBUSY is an `OSError`, not a `HostError`. |

Regressed: none. I looked specifically for regressions in the four areas you named and found none.

### Requested specifics

- **B1 lock-holder rule.** Sound. The only path that deletes the journal is `restore()` on a session with `journal_written`, which in turn needs the lock. A failed restore clears `journal_written`, so the journal stays on disk for `iobench restore`. Residual NIT: after such a partial failure the on-disk journal still lists entries that were already restored, so the retry reports them as `changed_by_other` (harmless), and if the failure was the ratio write, the retry skips the pair fix because the bytes entry now reads as not `applied`. The operator would have to set the ratio by hand. The failure message names the path, so I rate it NIT.
- **B2 write order and read-back.** Correct as walked above. The pair is verified by reading both members. The failing-ratio-write case is covered by `test_rev_a_b2_a_failing_ratio_write_is_reported_not_raised_raw`.
- **B3 specifics.**
  - `INIT_PID_NS = "pid:[4026531836]"` is the kernel's fixed `PROC_PID_INIT_INO` (0xEFFFFFFC = 4026531836). Correct.
  - Both `/proc/self/ns/pid` and `/proc/1/ns/pid` must equal it. A host whose initial inode differs (a non-initial PID namespace, an LXC host, a nested VM-less sandbox) is refused with `container_pid_namespace`, and the message prints own, PID 1 and the expected value. That is a clear refusal, not a silent pass.
  - The check cannot be satisfied inside a container: a container's own PID namespace has a different inode, and a `--pid=host` container passes the PID check but fails the mount-namespace comparison against host PID 1 (`mount_namespace_mismatch`). Unreadable links give `namespace_unprovable`.
  - It runs in `Runner.s0` first, and in `mount_evidence`, so `target init DEVICE`, `load_target` and every `verify_target` repeat it.
  - Gap (N-r2-2): `target init --file` and `load_file_target` do not call it directly; `s0` does at run time, so the gap exists only at init. See below.
- **B4.** No new failure mode: `systemctl show -p LoadState --value` exits 0 for an unknown unit and prints `not-found`, which is exactly what the code tests.

### Open items: judgement for the P4 live run (r1002, real block device)

| Item | BLOCKING for P4? | Why |
|---|---|---|
| A-S1 argv allowlist at runtime | **No** | Every device-touching argv comes from `Runner.spawn` with the job's own offset and size, and `_check_job` plus `RealHost._check_extent` bound them (size 0 is refused). There is no current path that builds a mismatched argv. It is defence in depth against a future caller; the P4 probe will exercise the real path. Keep it in the backlog. |
| A-S9 second half (deferred second SIGTERM) | **No** | Signal latency is bounded by `TICK_S = 1` while fio runs. The deferral applies only to blocking `blkdiscard` (seconds) and `fallocate`. SIGKILL is covered by PDEATHSIG and the next-start restore. Operator remedy exists (`/run/iobench/abort`, next-start restore). |
| N1 `planned_volume=GIB` literal | **No** | The effective floor (4 GiB free) is documented in the spec; P4 uses a roomy work dir. |
| **N2 `systemd-run` without `--wait`** | **Yes, for the matrix S4 stage** | `probe_unit_argv` has no `--wait`/`--pipe`, so `handles[2]` finishes as soon as the unit is queued. The probe script writes `out` only after its `duration` loop ends, which is after the fio jobs finish (the unit starts later than they do). `run_cell` then calls `read_text(out_path)` right after `supervise` and `finish`. On a real host this is a race that the unit will lose in most runs: `FileNotFoundError` (run fails after restoring) or a stale or partial file feeding `sweep.cell_record` with wrong latencies. FakeHost writes the file instantly, so tests cannot see it. Not a safety problem (the unit has `RuntimeMaxSec`), but P4's matrix stage would give nothing usable. Fix: add `--wait` (and `--collect` is already there) or poll for the out file with a bounded wait, and add `systemd-run --wait` to the live-probe list. A calibrate-only P4 is unaffected. |
| N4 (marker with `partuuid=None` re-verifies only devno and size; `read_marker` not O_DIRECT) | No | r1002 target has a PARTUUID (device identity is fully checked). |
| N5 (`--expect-serial` identifies the disk, not the partition) | No | Operator-supplied expectation; document it. |
| N6 (silent flags: `--state-dir` on `restore`, file flags on a device init) | No | Cosmetic. |
| N7 (3 correlated PSI ticks) | No | Per spec; calibrate the threshold in the acceptance probe. |
| N8 (override flag listed in a remedy) | No | Wording. |
| N9 (scratch dir under `/run/iobench` left on abort) | No | Small tmpfs files. |
| N10 (unbounded `dirty_bytes` candidates in `--knob`) | No for P4 | The default matrix plan derives its values; only an operator-typed `--candidates` can be silly, and the PSI guard still applies. Worth a bound before wider use. |

### New findings

No new BLOCKING findings. Two SHOULD and three NIT, all introduced or exposed by the fixes:

- **N-r2-1 (SHOULD, `host.py:303-311`, `guard.py:296-302`).** The `O_EXCL` open of the device marker raises `OSError` (EBUSY) when the kernel finds the device claimed. `init_device` only catches `HostError`, so the very case the new comment describes ("the kernel's exclusive open said no: a refusal, exit 2") ends as a generic exit-1 report instead of exit 2 with a remedy. It fails closed, so not blocking. The test `test_rev_a_s14_...` raises `HostError` by hand and therefore never exercises this. Fix: in `RealHost.write_marker`, catch `OSError` from `os.open` and raise `HostError(f"{path} is in use (EBUSY): mounted, swap-active or claimed by md/dm")`.
- **N-r2-2 (SHOULD, `guard.py:379-417`, `guard.py:436-450`).** `init_file` and `load_file_target` decide "root filesystem", free space and fs type from `/proc/self/mountinfo` without `require_host_namespaces`. Run `target init --file` inside a container with a host directory bind-mounted: the container sees an `ext4` mount that is not its `/`, so `on_root_fs` is False and the quiet-window and 10 %-rule are skipped on what is really the host root. `s0` does catch it later at run time (and refuses the container), so this only affects what `init` will create. Fix: call `require_host_namespaces(host)` at the top of `init_file` and `load_file_target`.
- **N-r2-3 (NIT, `host.py:121-124`).** `PR_SET_PDEATHSIG` has the usual race: if iobench dies between fork and the prctl, the child is not killed. Check `os.getppid()` after the prctl and exit if it changed.
- **N-r2-4 (NIT, `restore.py:~150`).** `orphan_fio` matches any fio whose `--name` starts `run-`. A fio from some other tool with such a name would block every run with a remedy that says only "stop it". The message shows the pid, so the operator can judge; acceptable.
- **N-r2-5 (NIT, `restore.py:~214`).** See the B1 note: after a partial restore failure, the retry cannot repair a coupled ratio.

### Counts

- B1-B4: 4 closed.
- S2-S14: 10 closed (S2, S3, S4, S5, S6, S7, S8, S10, S11, S12), 2 half-closed (S9, S14), 1 not re-reviewed (S13), with S2b (fio `--exclusive_open`) not re-reviewed.
- Regressed: 0.
- New BLOCKING: 0. New SHOULD: 2. New NIT: 3.
- Open item blocking P4: 1 (N2, matrix stage only).

**READY** (scope A), with N2 to be fixed before the P4 matrix stage, and N-r2-1 and N-r2-2 recommended in the same wave.

---

## Re-review r3 @c7d28cea3

Scope: the delta `201903edf..c7d28cea3` (`host.py`, `devio.py`, `guard.py`, `stages.py`, `matrix.py`, `dryrun.py`, `testing.py`, `tests/test_review_c.py`). Read-only. I ran `test_review_c.py`, `test_review_a.py` and `test_saf.py` in-process, serially, under nice/ionice: 102 passed. As before, RealHost is exercised by few tests, so most real-host conclusions below are by reading and reasoning.

**Verdict: READY.** No new BLOCKING finding and no regression. 4 SHOULD and 3 NIT remain, none of which blocks P4.

**P4 on r1002 (calibrate first, then the matrix): nothing blocks either stage.** Two facts must be confirmed live, both cheap and both fail closed if wrong (see "P4 probe checklist").

### Finding by finding

| Item | Status | Detail |
|---|---|---|
| N2 `systemd-run --wait` | **closed** | See below. |
| N-r2-1 EBUSY on the marker open | **closed** | `RealHost.write_marker` converts the `OSError` to `HostError`; `init_device` turns that into `Refusal("identity_changed")`, exit 2. Nit N-r3-6. |
| N-r2-2 file targets check the namespace | **closed** | `init_file` and `load_file_target` call `require_host_namespaces` first, so every stage's `_verify_file` repeats it. |
| N-r2-3 PDEATHSIG race | **closed** | The parent pid is captured at construction (the lambda's default argument is evaluated in the parent), and the child does `_exit(1)` if `getppid()` changed after the prctl. Correct. |
| A-S1 allowlist at runtime, bound argv | **closed** | See below. |
| A-S9 second half | **closed with a residual** | See below. |
| A-S2b `--exclusive_open=1` and the probe | **closed with a nit** | See below. |
| A-S13 dry run | **closed** | Plan output carries `preflight: not_run` plus a note. |

### 1. N2: `systemd-run --wait`
- **Return code:** checked. `run_cell` now raises `RunFailed` for any non-zero unit status, with the unit's stderr. `supervise` polls the systemd-run handle too, so the stage honours ticks (abort, PSI) until the unit ends. FakeHost now reproduces the race: the output file appears only at the end and the client returns at that moment only with `--wait`; the "without `--wait` reads before it exists" test pins that.
- **Unit fails or times out:** `RuntimeMaxSec` stops the unit, `systemd-run --wait` returns non-zero, the cell raises `RunFailed`, the run is marked failed, knobs are restored (exit 1). Acceptable.
- **`--collect`:** needed and present in the argv. It makes systemd garbage-collect a unit that ended in `failed` state, so a failed probe does not linger as a loaded failed unit that would block a reuse of the name. Keep it.
- **Killed by our abort path:** `_ProcHandle.kill` kills the `systemd-run` client's process group, not the unit. The unit keeps running up to `seconds + PROBE_SLACK_S` (about 90 s). Its script then writes `out_path` after `cleanup` has already removed the registered files. This leaves a stray `.iobench-probe-<run>-<tag>` file in the work dir, and the next run refuses with `workdir_not_empty`. See N-r3-1.

### 4. A-S1: allowlist and extent binding
- The allowlist now lives in `host.py` and `check_argv` enforces it for `run`, `spawn` and `write_range`. `FakeHost` shares the function, so parity holds.
- Extent binding: `assert_writer_bound` parses the argv and requires `filename == target.path`, `offset == offset`, `size == length`, `length > 0`, `offset >= 0` and `job.end <= size - TAIL_BYTES`. `_check_extent` repeats the tail rule. I checked for widening:
  - Off by one: none. The end is half-open and `end == size - TAIL` is accepted, which is correct.
  - Units: everything is bytes. fio reads a bare `--offset`/`--size` integer as bytes, and `blkdiscard -o/-l` are bytes. No sector multiplication anywhere in the path.
  - Overflow: Python ints; a negative or zero length is refused; a non-integer or `50%` size fails `int()` in `parse_job` and is refused (exit 1, fail closed).
  - Discard: the argv must equal the typed builder output (`blkdiscard_argv` or `punch_hole_argv`) for exactly `(path, offset, length)`.
- Residual: the fio allowlist is looser than the claim (N-r3-3, N-r3-4).

### 5. A-S9: `_run_polled`
- The first signal still only sets a flag; a second signal while one is pending sets `_escalated`; the polling loop (0.5 s slices) kills the whole process group (`start_new_session=True` + `killpg`) and raises `Aborted`. No child is leaked by the group kill.
- Journal consistency: `host.run` and `discard_range` never run while `set_knob` or `register_file` is in progress (the run is sequential), so a killed discard cannot leave the journal half-written. The `Aborted` goes through `execute()` to the normal cleanup. An interrupted discard may have discarded part of the fresh extent; the run fails anyway and that extent holds no kept data.
- Residual (N-r3-2): after `killpg` the code calls `proc.communicate()` without a timeout, which can block forever if the child is in uninterruptible sleep on a stalled device, which is exactly when an operator escalates. `_ProcHandle.kill` already uses a 5 s bound; do the same. Also `killpg` can raise `ProcessLookupError` if the child exited in the same instant.

### 6. A-S2b: `--exclusive_open=1` and the probe
- Every device job gets it (`exclusive=self.target.kind == "device" and filename is None or == target.path`); work-dir jobs do not. No stage runs two jobs against the target at once, so two exclusive opens cannot conflict. A partition beside mounted siblings (vda7 beside vda3) is claimable.
- The probe runs `fio --cmdhelp=exclusive_open` through `devio.probe`, so a non-zero rc is a `probe_failed` refusal, then requires a stdout line that starts with the option name and no `Bad option`. Real fio writes "Bad option" to stderr and prints no help line, so an unknown option gives empty stdout and is refused either way.
- Strictness: the line test is `line.strip().startswith(option)`, a prefix match, so a help line (or a continuation line) beginning with the name, or a longer option name sharing the prefix, would pass. For this one literal option name and today's fio there is no such collision, so it is a NIT (N-r3-5). Parse `name, _, _ = line.partition(":")` and require `name.strip() == option`.
- What the probe does not prove is that fio really opens with `O_EXCL`. It proves only that the installed fio knows the option name. I cannot verify the behaviour here (no fio); see the checklist.

### New findings (all non-blocking)

- **N-r3-1 (SHOULD, `matrix.py:150-160`, `host.py` `_ProcHandle.kill`).** Abort kills the systemd-run client, not the unit. For up to about 90 s a root-owned unit keeps fsyncing in the work dir and then recreates its output file after the restore removed it; the next run refuses on a non-empty work dir. Fix: add a typed `systemctl stop UNIT` builder (allowlisted only for names starting `iobench-probe-`) and call it from the abort path, or have the script delete its own output if its parent unit was asked to stop; at minimum register the unit's files again after the wait.
- **N-r3-2 (SHOULD, `host.py:_run_polled`).** Unbounded `communicate()` after `killpg` (see section 5), plus the `ProcessLookupError` race and no `PR_SET_PDEATHSIG` for polled commands (an orphan `blkdiscard` survives a SIGKILL of iobench). Use `communicate(timeout=5)` in a try, catch `ProcessLookupError`, and reuse `_die_with_parent`.
- **N-r3-3 (SHOULD, `devio.py:assert_writer_bound`).** The host-layer binding does not require `job.exclusive` for a device target, so the O_EXCL protection depends solely on `Runner.job()` setting it. Add `if target.kind == "device" and not job.exclusive: raise HostError(...)`.
- **N-r3-4 (SHOULD, `host.py:_ALLOWED["fio"]`).** The fio rule only requires `--`-prefixed tokens and one `--filename=`; it forbids a short deny list. It still accepts `--output=PATH` (fio overwrites that file as root), `--trigger-cmd`, `--ioengine=exec`, and a repeated `--filename=` (`parse_job` keeps the last, while fio's own handling of repeats is not something I can rely on). Nothing in iobench builds such an argv; this only matters for a future caller bug. Invert it: accept exactly the option names `FioJob.argv` emits, each once, with `ioengine` pinned to `libaio`. The `systemd-run` rule (`any RuntimeMaxSec=`) is equally loose; pin the full shape from `probe_unit_argv`.
- **N-r3-5 (NIT).** Prefix match in `require_fio_option` (above).
- **N-r3-6 (NIT, `host.py:write_marker`).** Every `OSError` on the exclusive open is reported as "mounted, swap-active or claimed by md/dm", including EACCES or ENOENT. Claim that cause only for EBUSY; otherwise report `strerror` alone.
- **N-r3-7 (NIT).** `_escalated` is never reset and `run()` is also reachable during cleanup; today nothing in cleanup runs a slow `host.run`, but a future one would be killed with `Aborted` after a second signal. Document or reset after the abort is recorded.

### P4 probe checklist (live, r1002; none of these blocks starting)

1. On a scratch partition that is mounted read-only elsewhere, run one `iobench calibrate --dry-run`-adjacent probe that opens the device with the generated fio argv and confirm EBUSY. This shows that `--exclusive_open=1` really maps to `O_EXCL`, which `--cmdhelp` cannot show.
2. Run one `systemd-run --wait` probe unit that exceeds `RuntimeMaxSec` and confirm the client exits non-zero and `--collect` removes the unit.
3. Run a deliberate SIGTERM-twice during a blkdiscard on the scratch device and confirm the second signal ends it within about a second.

### Counts

- Items reviewed: 8. Closed: 8 (N2, N-r2-1, N-r2-2, N-r2-3, A-S1, A-S9, A-S2b, A-S13); 3 of them carry a residual that is listed above (A-S9, A-S2b, N2).
- Regressed: 0.
- New: 0 BLOCKING, 4 SHOULD (N-r3-1..4), 3 NIT (N-r3-5..7).
- Blocks P4 calibrate: no. Blocks P4 matrix: no (N2 is fixed).

**READY.**
