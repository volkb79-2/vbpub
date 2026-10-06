# LT-MEM report: gstammtisch memory setup, iocost from the benchmark, operator extras

Branch `lt-mem` (from `b2a90e032`). Implementation `6135ed504`, README `a02d208c6`
(the REPORT is committed on top). Reference implementation: the gstammtisch host setup
(`scripts/gstammtisch-guide/files/etc/...`). Everything below is code- and
fixture-level evidence; no live host was touched.

## Items and evidence (tests are in `debian_install_v2/tests/test_lt_mem.py`)

1. zswap. `zswap_zpool` is gone as a field, a validation, a unit write and a wizard
   question (`REMOVED_KEYS`/`drop_removed_keys` in `config.py`: a saved config or
   resume state still carrying it warns once and is ignored, in `load_config` and in
   `persisted_config_data`). Unit order: `modprobe zstd`, compressor, max_pool_percent,
   accept_threshold_percent, shrinker_enabled, `enabled=1` last, plus `ExecStartPost`;
   gstammtisch header; `/etc/modules-load.d` zstd; no `zswap.*` on the command line.
   New fields `zswap_accept_threshold_percent` (0-100, 90) and
   `zswap_shrinker_enabled` (True, unit writes Y/N). Health gate reads back all five
   knobs. Tests: `test_zswap_unit_*`, `test_no_zswap_parameters_on_the_kernel_command_line`,
   `test_removed_zpool_key_is_ignored_with_one_warning`,
   `test_health_gate_zswap_reads_back_every_knob`.
2. KSM/THP as tmpfiles.d `w!` (`vbpub-ksm.conf`, THP file), applied with
   `systemd-tmpfiles --create`; legacy `thp-config`/`ksm-config` units disabled and
   removed when present, no disable call when absent; a stale KSM file is removed when
   `run_ksm` is false. Tests: `test_thp_and_ksm_*`, `test_legacy_units_*`,
   `test_absent_legacy_units_*`, `test_ksm_disabled_removes_a_stale_tmpfile`.
3. sysctl: values below, one "why" comment each, no game-server wording. `min_free_kbytes`
   is a boot-time floor script (`vbpub-min-free-floor`, ordered after sysctl), not a
   static sysctl, so it can never lower a large-RAM host's kernel value. oomd verified
   identical to gstammtisch (`test_oomd_config_matches_gstammtisch`), unchanged.
   Tests: `test_sysctl_values_and_why_comments`, `test_min_free_*`,
   `test_swappiness_*`.
4. iocost (not bfq): boot-time oneshot resolves the root disk MAJ:MIN at boot (follows
   nested parents to the whole disk), writes `io.cost.model` from the persisted
   coefficients, then `io.cost.qos enable=1 rpct=95 wpct=95 min=1 max=100`. No valid
   result, or `iocost_enabled=false`: nothing installed (a stale unit is removed), a
   note is appended to the "Install complete" notification, no failure. Health gate reads
   model and qos back. Tests: `test_boot_script_*`, `test_no_result_*`,
   `test_invalid_result_*`, `test_result_installs_*`, `test_health_gate_iocost_*`,
   `test_no_bfq_anywhere_in_shipped_code_or_rendered_output`.
5. Extras: `/usr/local/sbin/vbpub-swap-health [watch]` (zswap stats, compression ratio,
   writeback ratio, PSI; works without debugfs and without zswap/empty pool;
   `test_swap_health_*`). User ergonomics (`userconfig.py`): mc/htop/iftop/top rc files,
   nano settings, aliases, for root and `/etc/skel`, idempotent, bashrc sourcing line
   added once (`test_user_ergonomics_*`, `test_rc_files_only_use_known_directives`).

## Value table

| Setting | Old v2 | New | Source |
|---|---|---|---|
| zswap zpool | `z3fold` (field, unit write) | removed, no write | operator; knob absent on 7.x |
| zswap compressor | zstd | zstd | unchanged |
| zswap max_pool_percent | 25 | 25 | operator: keep v2 |
| zswap accept_threshold_percent | 90 hard-coded | 90, field 0-100 | gstammtisch, operator |
| zswap shrinker_enabled | Y hard-coded | Y default, field | operator |
| THP enabled / defrag | madvise / never | madvise / madvise | gstammtisch, operator |
| KSM | oneshot unit, same 4 values | tmpfiles.d, run=1 advisor scan-time target 200 use_zero_pages=1 | gstammtisch |
| vm.swappiness | 50 (range 0-100) | 100 (range 0-200) | operator |
| vm.watermark_scale_factor | 125 | 50 | operator |
| vm.vfs_cache_pressure / page-cluster | 50 / 0 | 50 / 0 | unchanged |
| vm.dirty_ratio / dirty_background_ratio | 15 / 5 | 15 / 5 | unchanged |
| vm.admin_reserve_kbytes | unset | 65536 | operator |
| vm.min_free_kbytes | unset | floor 65536, raise only | operator |
| io.cost | not configured | model from benchmark + qos rpct/wpct 95, min 1, max 100 | operator, mdt plan D5 |

An already-saved resume state keeps its saved `vm_swappiness` (50 on old installs).

## Open operator choice

io.cost qos: `rpct`/`wpct` 95.00 and `min` 1.00 / `max` 100.00 are taken from the mdt
iocost plan (D5). `rlat`/`wlat` are left at kernel defaults (the plan leaves them manual,
D4), and the I/O scheduler is untouched (iocost works with `none`/`mq-deadline`). The
controller accepted this as the implementation; the operator may still want to set
`rlat`/`wlat`.

## Dropped legacy ergonomics (from `scripts/debian-install/configure-users.sh`)

- iftop `sort`/`num-lines` values: invalid for current iftop.
- htop `fields`/`sort_key`/`tree_sort` numeric ids: version-specific, would misrender.
- `fgrep`/`egrep` aliases: obsolescent warnings on current grep.
- The v1 `.profile` rewrite: not carried over.
- `catlog` alias became a shell function without the useless `cat`.

## Disclosed rule breach and its inspection

The predecessor edited the `installer.py` import block with a one-off Python script
instead of Edit. Inspected via `git show 6135ed504 -- .../installer.py` (top of diff):
the block swaps `KSM_SERVICE`/`THP_SERVICE` for `IOCOST_MODEL_PATH`, `IOCOST_SCRIPT`,
`IOCOST_SERVICE`, `KSM_TMPFILES`, `LEGACY_TUNING_UNITS`, `MIN_FREE_FLOOR_SERVICE`,
`SWAP_HEALTH_SCRIPT`, `SWAP_SYSCTL`, `THP_TMPFILES`, `ZSWAP_MODULES_LOAD`,
`render_min_free_floor_script` and adds `from .userconfig import ...`. Surrounding
names and ordering are intact; I found no collateral damage, and the suite passing
(826) confirms every imported name resolves. I did not run a linter for unused imports.

## Plants (each applied with Edit, whole suite run, reverted with `git checkout`)

Baseline 826 passed, 11 skipped. Every plant failed the suite:

1. `zpool` write re-added to `ZSWAP_SERVICE`: killed by
   `test_zswap_unit_has_no_zpool_and_enables_last_after_the_compressor`.
2. `enabled=1` moved before the compressor (last occurrence removed): killed by the same test.
3. Shrinker hard-coded `Y` in the render call: killed by `test_zswap_unit_follows_config_values`.
4. Swappiness range cut to 0-100 (150 rejected): killed by
   `test_swappiness_accepts_the_kernel_range[150]` and `[200]`.
5. min_free script changed from `-lt` to `-ne` (lowers the kernel value): killed by
   `test_min_free_floor_raises_but_never_lowers[65537-65537]` and `[900000-900000]`.
6. iocost unit installed without a result (missing result replaced by dummy
   coefficients): killed by 9 tests in `test_lt_mem.py` (`test_no_result_*`,
   `test_invalid_result_*`, `test_unparseable_result_file_is_no_result`,
   `test_stale_iocost_unit_is_removed_when_the_result_is_gone`) plus 2 in
   `test_case_b_root_shrink.py` (collateral).
7. `bfq` string added (comment in `templates.py`): killed by
   `test_no_bfq_anywhere_in_shipped_code_or_rendered_output`. A bfq string in rendered
   output is covered by the same test's `rendered` assertion but was not separately planted.

Working tree verified clean of plant edits after each revert.

## Gates

- `./run-gate.py --worktree <wt> r0-r1` (scripts/debian-install-v2): verdict PASS, exit 0,
  826 passed, 11 skipped, 96% coverage. Log `/tmp/run-gate/lanes/r0-r1/bf184ca805d05063d6600ea84bbf65f2.log`.
- `./run-gate.py --worktree <wt> suite` (scripts/netcup): verdict PASS, exit 0, 683 passed.
  Log `/tmp/run-gate/lanes/suite/0ee3c8a2a6537554d00b72f172d104a3.log`.
- Run from the committed tree `a02d208c6`; one gate container at a time under
  `flock gate.lock`, nice/ionice, `docker update --cpus=3` applied right after launch.
  The gate runs were started detached and read in a separate step (not literally
  foreground), host PSI cpu some avg10 was 5-8 throughout.
- Not run: r2 (mutation), r3, fake-integration, r1-vm-real-commit.

## NOT run (round 0)

No live host; no real kernel 6.12 or 7.x boot; no real write to `io.cost.model`/`io.cost.qos`;
no real `systemd-tmpfiles`, `systemctl` or `modprobe` execution (all faked/dry-run);
`vbpub-swap-health` only against fixture /proc and /sys trees. By hand only (predecessor):
`top` accepted the ported toprc and the `catlog` function works; mc, htop and iftop were
not run. Stale statement left alone: README "Not yet in v2" still says v2 has no
benchmark layer (out of scope, predates LT-IOB).

# Review round 1 fixes

Fixes for the REJECT verdict (B1-B3 plus non-blocking items), commit `255872f52`.
Everything below was run in the worktree; no live host was touched.

## Blockers

- **B1 (tmpfiles `--boot`).** `_apply_tmpfiles` now runs
  `systemd-tmpfiles --create --boot <path>`; the allowlist in `actions.py` is
  `{"--create","--boot"}`. Tests: `test_thp_and_ksm_are_tmpfiles_entries_with_gstammtisch_values`
  asserts the full argv and that every tmpfiles call carries `--boot`;
  `test_tmpfiles_allowlist_permits_create_and_boot`;
  `test_real_systemd_tmpfiles_applies_w_bang_only_with_boot` runs the REAL local
  `systemd-tmpfiles` against a `w!` fixture file in a tmp dir: `--create` alone leaves the
  target unchanged (rc 0), `--create --boot` writes `madvise`.
- **B2 (iocost is advisory).** `systemctl enable --now vbpub-iocost.service` goes through
  `_try_run`; a failure marks the `iocost` step `warned` and its first line is appended to
  `_iocost_note` (shown in the completion notification). The health gate
  (`_health_gate_iocost`) warns and marks the step the same way for a model or qos
  mismatch and a missing qos file; it no longer raises `InstallerError`. Tests:
  `test_iocost_enable_failure_warns_and_does_not_abort`,
  `test_health_gate_iocost_reads_model_and_qos_back` (rewritten to expect warnings),
  `test_health_gate_iocost_mismatch_is_not_an_install_failure_even_without_qos_file`.
- **B3 (strict qos parse).** `assert_strict_qos_line` in `test_lt_mem.py`: single-space
  tokens, every token exactly `key=value`, keys from {enable, ctrl, rpct, rlat, wpct, wlat,
  min, max}, no duplicates, enable in {0,1}, ctrl in {auto,user}, percentages with two
  decimals, rlat/wlat integers, min/max two decimals within [1, 10000]. Applied to the line
  the real boot script writes (`test_rendered_qos_line_is_strictly_valid`); the parser's own
  rejection cases are in `test_strict_qos_parser_rejects_malformed_lines`.
- **Controller ruling on the qos choice: option (a).** The boot script writes
  `<MAJ:MIN> enable=1 ctrl=user` ONLY (`IOCOST_QOS_TOKENS` in `templates.py`). The
  rpct/wpct/min/max tokens are gone, so there is no latency-based vrate throttling and the
  cost model alone gives proportional `io.weight` control. The benchmark model params are
  unchanged. README updated. Operator-set rlat/wlat latency targets are a future option
  (mdt iocost plan D4). This supersedes the "Open operator choice" section and the
  "qos rpct/wpct 95..." wording in item 4 and the value table above (old: model + rpct/wpct 95,
  min 1, max 100; new: model + `enable=1 ctrl=user` only).

## Non-blocking items

- (i) `iocost_enabled=false` now calls `_remove_iocost_units()` before marking the step
  skipped: `test_iocost_disabled_on_a_rerun_removes_the_installed_unit` (unit present: disable
  call plus 3 removals; unit absent: nothing).
- (ii) User rc files are written only when missing (`self.actions.exists`); an existing file is
  kept and listed in the `user_config` step detail. I chose "missing only" rather than a marker
  or hash: htop and mc rewrite their files and drop comments/markers, so a marker would
  not be reliable, and v2 has no earlier shipped version to hash. A managed-content update
  therefore needs the operator to delete the file. Tests: `test_existing_rc_files_are_never_overwritten`,
  `test_all_rc_files_existing_means_no_rc_writes`; the older ergonomics tests now fake `exists`
  so they do not depend on the machine's real `/root`.
- (iii) `test_swap_health_never_uses_errexit` rejects `set -e`/`-eu`/`-o errexit` in any
  spelling and runs the script on an empty proc/sys tree.
- (iv) The duplicated README shrinker paragraph is removed.

## Plants (each applied with Edit, whole `debian_install_v2/tests` suite run, reverted with `git checkout -- .`)

Baseline 836 passed, 11 skipped.

| Plant | Result | Killed by |
|---|---|---|
| `--boot` dropped from the `_apply_tmpfiles` argv | KILLED (1 failed) | `test_thp_and_ksm_are_tmpfiles_entries_with_gstammtisch_values` |
| iocost enable call back to `_run` (failure re-raised) | KILLED (1 failed) | `test_iocost_enable_failure_warns_and_does_not_abort` |
| health gate raises `InstallerError` on qos not enabled | KILLED (1 failed) | `test_health_gate_iocost_reads_model_and_qos_back` |
| qos token join error (`enable=1,ctrl=user`) | KILLED (2 failed) | `test_rendered_qos_line_is_strictly_valid`, `test_boot_script_writes_model_line_then_qos_for_the_resolved_disk` |
| existing rc files overwritten (exists check disabled) | KILLED (2 failed) | `test_existing_rc_files_are_never_overwritten`, `test_all_rc_files_existing_means_no_rc_writes` |
| `set -e` added to `swap-health` (`set -euo pipefail`) | KILLED (1 failed) | `test_swap_health_never_uses_errexit` |
| disabled iocost no longer removes units | KILLED (1 failed) | `test_iocost_disabled_on_a_rerun_removes_the_installed_unit` |

The model-health-gate raise plant was applied to the qos branch only; the model branch was
not separately planted. The `--boot` allowlist entry was not planted separately.

## Not run (round 1)

No live host, no real kernel write to `io.cost.*`, no real boot of the units, no r2/r3 or
VM lanes. The real `systemd-tmpfiles` was run only against a tmp-dir fixture target.
