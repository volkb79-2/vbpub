<!-- origin: 2026-11-05; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/div2-data-area-v2-FIX-REPORT.md -->
<!-- relation: data-area implementation evidence; verbatim source text follows -->
<!-- Evidence scrub 2026-11-10: environment-specific home, checkout, worktree, and per-run temporary paths were normalized to neutral placeholders; command output and verdict text are otherwise unchanged. -->

# div2-data-area-v2 review fix report

Worktree: `<worktree>`  
Branch: `div2-data-area-v2-20261105`  
Commit: `93a1e19256bcb29cb77530e54754337fd8061664`

## Findings

| Finding | Status | Fix and regression evidence |
|---|---|---|
| F1 — pool end / backup GPT | Fixed | `_pool_end` and `_plan_swap_partitions` use the shared aligned GPT-safe pool boundary for swap on and off. `test_pool_end_is_shared_and_gpt_safe_for_all_plan_shapes` covers 168 combinations: seven disk sizes from 20 GiB through 8 TiB, four odd-sector remainders, both swap policies, and all three requested plan shapes. It checks contiguous extents, alignment, position-based numbering, accounting, non-overlap, and the backup-GPT boundary. |
| F2 — raw benchmark partition | Fixed | Added the always-planned `iobench-raw` partition (default 8 GiB, configurable 2–10240 GiB, or explicitly disabled), placed after data/reserve and before swap. It is never formatted or mounted; the installer verifies its PARTLABEL and geometry, wipes it before direct `iobench` use, and keeps it in the final table. The temporary-tail path is removed. `test_default_iobench_partition_is_raw_fixed_sized_and_precedes_swap`, `test_install_benchmark_wipes_then_runs_on_the_planned_raw_partition_only`, and `test_root_shrink_interim_table_has_no_hole_before_raw_partition_or_swap` cover sizing, execution and the shrink-table hole. README, design, config and wizard/plan output describe it; the 512 GiB example now accounts for it. |
| F3 — impossible plans / offline resolver | Fixed | Config load and custom-script bundle construction reject fixed sizes without a growable entry, percentage totals other than 100%, and percent/fixed mixtures. Added `plan --disk-size SIZE` with a resolved GiB table. `test_impossible_pool_shapes_are_refused_at_schema_validation`, `test_customscript_refuses_static_impossible_size_shapes`, and `test_plan_cli_resolves_a_disk_size_without_host_inspection` cover these contracts. Netcup consumer claims now match the config and plan behavior. |
| F4 — root keep refusal | Fixed | `root: "keep"` with no data allocation, or with only fixed `reserve_gb`, now raises a clear refusal. Covered by `test_keep_without_an_entry_refuses_instead_of_indexing_an_empty_list`. |
| F5 — Netcup CoW partition lookup | Fixed | Removed the runner-side carve path and `COW_DEVICE` hint. The runner resolves exactly one planned `PARTLABEL=MDT_COW`, refuses missing/duplicate labels, and checks filesystem and mount agreement. Updated `runner.env.example`, consumer docs, and runner tests. The registered Netcup suite passed, including the renumbering, missing-label and duplicate-label cases in `test_cow_select.py`. |
| F6 — protected system mounts | Fixed | Config validation refuses mounts on protected system paths and parents that would cover protected state, naming the protected path. No exact system mountpoint is pre-populated by design. `test_mounts_that_hide_a_system_tree_are_refused` exercises protected roots and descendants; the design and consumer docs list them. |
| F7 — resolved refusal output | Fixed | Live `show_plan` includes the resolved GiB table; plan refusals add the table when disk facts permit resolution. Offline fit/minimum refusals include a resolved table or capacity breakdown. Covered by `test_plan_refusal_shows_the_resolvable_gib_table`, `test_auto_swap_requires_ram_and_fit_refusal_prints_capacity_table`, and `test_root_floor_refusal_prints_the_resolvable_partition_table`. |
| F8 — surviving mutants | Fixed | Added killing tests for all three named survivors: `test_pool_end_is_shared_and_gpt_safe_for_all_plan_shapes` (M-swapoff-pool-end), `test_installer_data_recognition_requires_the_planned_partlabel` / `test_foreign_linux_partition_after_root_is_not_recognized_without_partlabel` (M-data-recognition-without-PARTLABEL), and `test_gpt_128th_partition_is_accepted_and_129th_is_refused` (M-128-partition-limit). These ran in R0/R1. |
| F9 — stale Netcup `data_area` text | Fixed | Removed stale `data_area` wording from the Netcup runner, tests, backlog and docs. A scoped `rg` check found no remaining `data_area` matches in those paths. |
| F10 — root growth / legacy policy | Fixed | Documented root-only `sfdisk -N <root-number>` growth in `CHANGES.md`, followed by `partx -u` and exact geometry verification. Removed the plan-less `root_resize_policy` path; the old key is refused. Existing root-growth argv/crash tests and `test_config_prerequisites` cover the behavior and refusal. |

## Gate results

- Debian installer registered `gate`: **PASS** — R0/R1: 1,507 passed, 16 deselected; R3: 3 canaries rejected, 0 survived.
- Netcup registered `suite`: **PASS** — 1,015 passed. It ran with `--allow-dirty` because the cross-project worktree was intentionally uncommitted when the gate first checked cleanliness.
- The VM lane was not run, as instructed. No real devices or host provisioning were used.

## Resolved plan tables

All six previews used one representative config: root `30%`, one Btrfs `MDT_COW` data partition `70%`, `iobench_raw_gb: 8`, and `preserve_root_size_gb: 2`. For swap on, the explicit target is 2 GiB split into two partitions. The offline resolver assumes root starts at sector 2048 and there are no preserved pre-root partitions. Sizes and starts below are GiB; partition numbers follow physical order.

| Disk | Swap | Resolved entries (`PARTLABEL`: start + size) |
|---|---|---|
| 20 GiB | on | 1 root: 0.00 + 3.00; 2 `MDT_COW`: 3.00 + 7.00; 3 `iobench-raw`: 10.00 + 8.00; 4 `vbpub-swap4`: 18.00 + 1.00; 5 `vbpub-swap5`: 19.00 + 1.00 |
| 20 GiB | off | 1 root: 0.00 + 3.60; 2 `MDT_COW`: 3.60 + 8.40; 3 `iobench-raw`: 12.00 + 8.00 |
| 512 GiB | on | 1 root: 0.00 + 150.60; 2 `MDT_COW`: 150.60 + 351.40; 3 `iobench-raw`: 502.00 + 8.00; 4 `vbpub-swap4`: 510.00 + 1.00; 5 `vbpub-swap5`: 511.00 + 1.00 |
| 512 GiB | off | 1 root: 0.00 + 151.20; 2 `MDT_COW`: 151.20 + 352.80; 3 `iobench-raw`: 504.00 + 8.00 |
| 2 TiB | on | 1 root: 0.00 + 611.40; 2 `MDT_COW`: 611.40 + 1,426.60; 3 `iobench-raw`: 2,038.00 + 8.00; 4 `vbpub-swap4`: 2,046.00 + 1.00; 5 `vbpub-swap5`: 2,047.00 + 1.00 |
| 2 TiB | off | 1 root: 0.00 + 612.00; 2 `MDT_COW`: 612.00 + 1,428.00; 3 `iobench-raw`: 2,040.00 + 8.00 |

New head: `93a1e19256bcb29cb77530e54754337fd8061664`.
