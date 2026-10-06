# LT-IOB report: io.cost benchmark step (branch `lt-iobench`)

Scope: `Installer._run_io_benchmark()` and its helpers, as specified in
`IO-BENCHMARK-DESIGN.md`. Nothing here was run against a real disk, loop
device, swap, fio or the VM harness; all evidence is unit tests against a
simulated disk (`debian_install_v2/tests/test_io_benchmark.py`, class `SimDisk`
behind the real `HostActions` allowlist). Live validation is the controller's
netcup run.

## Design as built

- `_stage2()` calls `_run_io_benchmark(swap_written=...)` right after
  `_verify_and_apply_root_shrink()` and before the swap shape (the later
  `_apply_known_swap_shape()` call). No-op unless `run_io_benchmark`.
- Refactor (behaviour-preserving, existing 577 tests stayed green): the
  forward write/partx/settle/readback block and the rollback block of
  `_apply_known_swap_shape()` became `_write_and_verify_partition_plan()` and
  `_restore_partition_table()`. Both swap placement and the benchmark use them,
  so there is one raw sfdisk path. `_validate_plan_geometry()` is NOT modified.
  The helper takes `record_in_dry_run` so the benchmark's actions appear in a
  dry-run plan; the swap shape still records none in dry-run, as before.
- Partition: tail of the disk, number `root_number + swap_file_count + 1`,
  GPT name `vbpub-iobench`, Linux filesystem type. Sizing
  `min(io_benchmark_max_size_gb, tail free - swap requirement - 1 GiB)`,
  2048-sector aligned; under 2 GiB -> step `skipped` with a reason.
  `_validate_benchmark_plan()` requires: exactly one added partition, all
  existing geometry unchanged, no overlap, start at least swap end + 1 GiB.
- Case B (hook already wrote swap): the existing partitions' end is the
  "requirement", so it benchmarks only if tail space remains (usually skips).
- Tool: `tools/iocost_coef_gen.py` next to the package; header
  `source-sha256`/`patch-series-sha256` are compared with the vendored source
  and `0*.patch` files (same formula as the builder). It is not regenerated.
  `fio` and `pv` are installed through `_packages()` (step `io_benchmark_packages`)
  only when the benchmark runs, before any partition is touched.
- Results parsed from the tool's last line, written to
  `state_dir/io-benchmark.json` (0600; timestamp, device, partition, devno,
  size GiB/sectors, duration, tool sha256 and header hashes, results), a compact
  `_mark_step` summary, and one `_notify`. No `io.cost.model`/`io.cost.qos` write.
- Failure policy: any failure up to and including the tool run is advisory
  (step `warned`, install continues). Cleanup runs in a `finally` whenever the
  partition write was attempted. Cleanup failure (umount fails, restored layout
  differs from the pre-benchmark layout, device node still present) raises
  `InstallerError("io benchmark cleanup failed ...")`, marks the step `failed`
  (not terminal, so resume re-checks) and stops before swap placement.
- Resume: terminal status (`success`/`skipped`/`warned`) skips. Otherwise a
  leftover `vbpub-iobench` partition is unmounted and deleted (live table minus
  that line) first; a partition at that number that is not ours is untouched
  and the step is skipped.
- Allowlist additions (`actions.py`): `mkfs.ext4` (-F -q -O -L), `mount`
  (-t -o), `umount` (only -v), `iocost_coef_gen.py` (--testfile,
  --testfile-size-gb, --duration, --quiet).

## Exact action sequence (real mode, benchmark runs)

1. `findmnt -rn -o SOURCE,TARGET,FSTYPE` (preflight), `apt-get update`,
   `apt-get install fio pv`
2. write backup `state_dir/backups/ptable-iobench-<ts>.sfdisk`
3. `sfdisk --force --no-reread /dev/D` (plan on stdin), `partx -a --nr N:N`,
   `udevadm settle`, `sfdisk --dump` (readback verify), wait for node
4. `mkfs.ext4 -F -q -O ^has_journal -L vbpub-iobench /dev/DN`
5. `mount -t ext4 -o noatime /dev/DN <mkdtemp>`
6. `tools/iocost_coef_gen.py --testfile <mnt>/iocost-coef-fio.testfile
   --testfile-size-gb X --duration S --quiet`
7. write `io-benchmark.json`
8. `findmnt` (mount check), `sync`, `umount /dev/DN`
9. `sfdisk --force /dev/D` (pre-benchmark table on stdin), `partx -d --nr N:N`,
   `partx -u`, `udevadm settle`, `sfdisk --dump` (verify equals pre-layout),
   node-gone check
10. then the unchanged swap shape (`sfdisk --force --no-reread` ...).

## Tests (`test_io_benchmark.py`, 37 collected cases incl. parametrized)

Lifecycle order via `SimDisk` (create -> verify -> mkfs -> mount -> tool ->
umount -> delete -> verify -> swap write, and exactly two table writes) and the
same order in a real `dry_run=True` plan; final layout has no benchmark
partition; skip-when-too-small (swap still applied, no benchmark actions);
size cap by swap requirement and by config max; boundary at exactly 2 GiB;
Case B swap-already-written; leftover removal before swap (including unmount);
foreign partition untouched; benchmark failure (tool error, unparseable output,
mkfs error, readback mismatch, bad tool artifact) -> cleanup -> install
continues; cleanup failures (umount, layout not restored, node still present)
stop before swap; `failed` marker not terminal; terminal marker skips; disabled
does nothing; result parsing (valid + 5 malformed); generator header
verification (real artifact, missing, stale hash, no header, changed patch);
`io-benchmark.json` mode 0600 and contents; allowlist refusals. `SimDisk`
refuses `write_file` outside the test tmp dir.

Evidence run: full suite in the worktree, serial, `nice -n 19 ionice -c 3`
under `flock`: 614 passed, 11 skipped (before: 577 passed, 11 skipped).

## Plant mutations (each applied by hand, full suite run, then reverted)

| Mutation | Result |
|---|---|
| skip cleanup (`if False and created:`) | killed, 14 failures |
| wrong order: `_run_io_benchmark` moved after `_apply_known_swap_shape` | killed, 5 failures |
| benchmark failure fatal (`except Exception: raise`) | killed, 5 failures |
| size not capped by swap requirement (dropped `- swap_required_end`) | killed, 4 failures |
| extra: cleanup layout verification disabled | killed, 1 failure |

(On the third row my first attempt was contaminated by a mis-restored
`_stage2()` call, giving 12 failures; I fixed the call and re-ran, giving the 5
reported. After the last revert the full suite was green again, 614 passed.)

## Gate

`flock .../gate.lock ./run-gate.py --worktree /workspaces/vbpub/.worktrees/lt-iobench r0-r1`
verdict (read in a separate step from the saved output): lane `r0-r1` PASS,
exit 0, 614 passed, 11 skipped, total coverage 95%. r2 (mutation) and the
VM lane were not run.

## Deviations / open items

- Cleanup restores the pre-benchmark table with `sfdisk --force` (the existing
  rollback primitive) instead of `sfdisk --delete`, which is not allowlisted.
- Header-hash check instead of regeneration (see above).
- `HostActions.run` has no timeout: a hung fio hangs stage 2. Not changed.
- The tool sets the whole disk's scheduler to `none` for the run (inherent to
  the generator); the mount directory is `tempfile.mkdtemp` under `/tmp`.
- Not exercised by any test: real udev/partx timing, real fio/pv, ext4 mkfs on
  a just-created node, the tool's own sysfs access on a partition-backed file
  target (patch 0002 is trusted, not re-tested here).
- Config, wizard, `_validate_plan_geometry`, controller-key and docker code
  were not touched.


## Review fix round 1 + W9b merge

Fix commit `64aa2adc1`; merge of `cli-ext-w9b-debian` (d74b0beb5) is `8c72a3f38`.
Still nothing run against a real disk, loop device, swap, fio or the VM harness; the
only real child processes were harmless `sh`/`sleep` in the timeout tests.

### Item -> fix -> test

1. Timeouts/scheduler: `HostActions.run(timeout=)` (passed only when supplied via `Installer._run`),
   `Popen(start_new_session=True)` + `communicate(timeout)` (not `subprocess.run`), SIGTERM to the
   process group, grace, SIGKILL, then `ActionUnreapable` if still not reaped (also `ActionTimeout`).
   Tool timeout `6*duration*3 + testfile_gb*30 + 120` (690 s in the tests); sync/umount 120, mkfs 300,
   dd 120. Scheduler and `nomerges` snapshotted before the tool, restored in a `finally` with `tee` +
   `input=`. An unreapable child marks the step `failed`, skips all cleanup actions and stops.
   Tests: `test_commands_carry_timeouts_and_tool_timeout_formula`, `test_hung_tool_cleans_up_restores_scheduler_and_install_continues`,
   `test_hung_umount_or_sync_stops_the_install`, `test_unreapable_tool_child_stops_the_install`,
   `test_unreapable_umount_stops_the_install`, `test_timeout_kwarg_is_only_forwarded_when_supplied`,
   `test_bounded_run_*` (real group kill of a grandchild, TERM-ignoring child, fake unreapable child, new session).
2. Case B resume wedge: `_stage2()` also derives "swap already written" from the live table
   (`_swap_partitions_already_planned_in_table`: all planned swap partitions present with exact start/size/type);
   partial/mismatching presence still refuses; `_validate_plan_geometry()` untouched.
   Tests: `test_resume_with_swap_partitions_already_written_does_not_wedge` (reviewer repro),
   `test_resume_after_benchmark_cleanup_stop_continues_to_swap_activation`, `test_partial_swap_presence_still_refuses`,
   `test_mismatching_swap_presence_still_refuses`, `test_swap_written_flag_decides_the_requirement`.
   I did not run the new regression test against the pre-fix code except via the "resume derivation disabled" plant, which reproduces the wedge.
3. Escaping: failure text wrapped in `_code()`. Test: `test_failure_notification_is_html_escaped`.
4. Cleanup robustness: `udevadm settle` before the restore; restore uses `--no-reread` (deliberate change to the
   shared rollback path; `test_fake_integration.py::test_mismatched_readback_rolls_back` updated: rollback is the second
   `--no-reread` write, settle count 3); `partx -d`/`-u` retried 5x with 1 s backoff. Tests: `test_cleanup_settles_before_restore_and_restore_uses_no_reread`,
   `test_cleanup_partx_retries_then_succeeds`, `test_cleanup_partx_gives_up_after_five_attempts`.
5. Stale signature: `dd if=/dev/zero of=<part> bs=1M count=1 conv=fsync` after umount, before the restore (skipped when the
   device node does not exist, e.g. a node-less leftover). Tests: `test_stale_signature_zeroed_before_partition_is_deleted`,
   `test_leftover_without_a_device_node_skips_the_zeroing`.
6. Tool integrity: `build-iocost-generator.py` writes `tools/iocost_coef_gen.py.sha256` and `--check` verifies it
   (ran: regenerate, `--check` OK; the generated tool bytes did not change). Installer compares the body digest before running;
   mismatch -> step `warned` "tool integrity check failed", benchmark skipped, install continues.
   Tests: `test_tampered_tool_body_with_valid_header_is_refused`, `test_committed_digest_matches_shipped_tool`,
   `test_integrity_failure_marks_warned_skips_benchmark_and_continues`, `test_generator_verification_rejects_bad_artifacts` (updated).
7. Readback strictness: post-restore readback also compares uuid and name of preserved partitions and the disk label-id.
   Test: `test_cleanup_readback_compares_uuid_name_and_label_id` (uuid, name, label-id variants).
8. Case B decision: tail-partition approach kept; `IO-BENCHMARK-DESIGN.md` documents it (runs after swap partitions exist in the table,
   before mkswap/activation, needs >= 2 GiB tail; the "usually skips" claim is withdrawn).
9. Missing tests: `test_apt_failure_is_advisory`, `test_partial_write_failure_still_triggers_cleanup`,
   `test_swap_written_flag_decides_the_requirement`, `test_success_notification_is_sent_with_the_values`.

### Plant table (each applied by hand-script to a committed tree, full suite run, reverted; suite was 648 passed before)

| Plant | Result (tests that failed) |
|---|---|
| apt failure fatal | killed (test_apt_failure_is_advisory) |
| `created=True` moved after the write | killed (test_partial_write_failure_still_triggers_cleanup) |
| `swap_written` ignored | killed (test_swap_written_flag_decides_the_requirement) |
| success notification removed | killed (test_success_notification_is_sent_with_the_values) |
| tool timeout removed | killed (test_commands_carry_timeouts_...) |
| umount timeout removed | killed (same) |
| group kill -> `os.kill` of the leader only | killed ONLY by the fake-unreapable test (signals recorded); the real grandchild test survives it because the post-reap straggler `killpg(SIGKILL)` still kills the group (partly equivalent) |
| unreapable -> plain `ActionTimeout` | killed (test_bounded_run_unreapable_child_raises_unreapable) |
| no SIGKILL escalation | killed (2 tests) |
| scheduler not restored | killed (6 tests) |
| unreapable treated as advisory | killed (test_unreapable_tool_child_stops_the_install) |
| unreapable still runs cleanup | killed (same) |
| resume derivation disabled | killed (2 tests; this is the reviewer's wedge) |
| resume ignores start mismatch | killed (test_mismatching_swap_presence_still_refuses) |
| resume ignores type mismatch | killed (same) |
| escaping removed | killed (test_failure_notification_is_html_escaped) |
| no settle before restore | killed (2 tests) |
| restore without `--no-reread` | killed (2 tests) |
| partx single attempt | killed (2 tests) |
| no dd zeroing | killed (3 tests) |
| identity (uuid/name/label-id) check removed | killed (3 tests) |
| tool body sha not checked | killed (2 tests) |

All 22 plants were captured (the runner's output is `scratchpad/ltplant/plants.out`); the tree was clean after the run.

### W9b merge (`8c72a3f38`)

Single textual conflict, in the `installer.py` import block: kept `ActionError, ActionUnreapable, HostActions` (this branch) and
`persisted_config_data` (w9b). Everything else auto-merged: the LT-KEY/LT-REG changes (controller-key retention, docker default
address pools, stage2 config handling) came from `cli-ext-w9b-debian`; the benchmark step and the round-1 fixes from this branch.
`git diff --check` clean; no conflict markers left.

### Gates on the merged head (verdicts read in a separate step from the saved output)

- debian-install-v2 `r0-r1`: PASS, exit 0, 751 passed, 11 skipped.
- netcup `suite` (scripts/netcup): PASS, exit 0, 683 passed.
- r2 (mutation) and the VM lane were not run.
