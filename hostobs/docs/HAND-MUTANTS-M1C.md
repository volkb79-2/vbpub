# Hand-mutation record for M1 part 3 (`store`)

Rust R2 (a `cargo-mutants` campaign judged by assay) is **not adopted yet** — it is R-3b in
[`assay/docs/RUST-METHODOLOGY.md`](../../assay/docs/RUST-METHODOLOGY.md), and
`tester-unified-rust` carries no `cargo-mutants`. The substitute is a hand mutation campaign,
recorded here rather than in git-ignored evidence so the claim survives the worktree. The two
earlier campaigns are [`HAND-MUTANTS-M1.md`](HAND-MUTANTS-M1.md) and
[`HAND-MUTANTS-M1B.md`](HAND-MUTANTS-M1B.md).

## Method

Every mutation is a **stored patch** in a replayable script, not prose:

```sh
python3 -I docs/hand-mutants/m1c-mutants.py            # the full campaign
python3 -I docs/hand-mutants/m1c-mutants.py --dry-run   # the patches only
```

Each step: record the file's SHA-256, replace one exact fragment, run the affected suite,
classify the outcome, restore the file (in a `finally`, so a crash cannot leave a mutation in
the tree), and assert the restored digest equals the original.

* Rust steps run in the lane image — `tester-unified-rust:local`, `--network none`, placed on
  the gates cgroup tier — with `tools/cargo-offline.sh test -p hostobs-store --offline
  --locked`. The host mount is READ from the cockpit's own bind mounts (the estate's
  dual-mount rule), never defaulted.
* Python steps run `python3 -I -m unittest discover -s python/tests`.

A mutation whose source does not type-check is reported as **`NOT-BUILT`** and counted as
unanswered, never as a kill; a patch whose stored fragment no longer matches the source is
**`NOT APPLIED`**, which is a stale patch rather than a survivor. `--dry-run` prints every
patch so a stale fragment is spotted by inspection.

## The nine failures the brief names

The brief lists nine ways this package could fail *quietly*. Each is a row below; two of them
needed a second patch, and three rows exercise the independent Python reference, because the
segment format's correctness is shared: a reference decoder that accepts a torn or corrupt
file certifies a segment the Rust reader refuses.

| # | patch | the test that must go red |
|---|---|---|
| M1c-1 | the byte cap is inclusive (`>` becomes `>=`) | `a_tier_exactly_at_its_byte_cap_keeps_every_segment` |
| M1c-2 | `keep_s` ignored (the expiry test always false) | `keep_s_evicts_a_segment_whose_whole_span_is_older_than_the_window` |
| M1c-3 | the ring ordered newest first | `eviction_takes_the_oldest_segment_and_the_ring_is_ordered_by_start_not_by_arrival` |
| M1c-4 | a torn tail accepted (no truncation, no report) | `a_restart_repairs_a_torn_tail_before_appending_and_counts_it` |
| M1c-5 | the seal without the rename (copy straight onto the final name) | `sealing_copies_the_open_segment_appends_the_footer_and_removes_the_open_name` |
| M1c-6 | a live incarnation retired | `retirement_removes_only_ids_no_segment_still_names` |
| M1c-7 | a dropped job not counted | `the_queue_drops_the_oldest_job_first_and_counts_every_drop` |
| M1c-8 | a foreign file deleted | `a_foreign_file_a_link_and_a_directory_are_left_alone_and_reported` |
| M1c-9 | a symlink followed | `a_link_is_never_read_written_truncated_or_removed` |

## Verdicts

Fifteen patches in the first round, **all fifteen killed**, and no patch left in the tree (every
restore is SHA-256-verified). All rows were kills on the first complete run **except M1c-3**,
which survived: flipping the ring's sort comparator left every existing test green, because
every test that evicted happened to list the files in the order the ring would have sorted
them anyway. The answer was a test that pins the *order* rather than the *outcome of one eviction*
— `eviction_takes_the_oldest_segment_and_the_ring_is_ordered_by_start_not_by_arrival` asserts
`oldest()`/`newest()` by name, then evicts at one segment over the cap and asserts that the
bucket-0 segment and never the bucket-60 one is gone. The campaign was re-run from scratch
after the new test landed. The fix round added fifteen patches (rows M1c-13 … M1c-24 below);
all thirty now run in one campaign, and the two definitive runs killed all of them, the second
covering the two MINOR fixes as well.

| # | patch | mutation | verdict | tests that failed |
|---|---|---|---|---|
| 1 | M1c-1 | `cap_bytes` check `>` → `>=` (equality evicts) | killed | `tier::tests::a_tier_exactly_at_its_byte_cap_keeps_every_segment`; `tier::tests::eviction_takes_the_oldest_segment_and_the_ring_is_ordered_by_start_not_by_arrival` |
| 2 | M1c-2 | the `keep_s` expiry condition replaced by `false` | killed | `tier::tests::keep_s_evicts_a_segment_whose_whole_span_is_older_than_the_window`; `tier::tests::a_foreign_file_a_link_and_a_directory_are_left_alone_and_reported`; `store::tests::a_store_routes_writes_enforces_bounds_and_retires_by_reachability` |
| 3 | M1c-3 | `sort_by` key reversed (newest first) | killed *(survived first pass)* | `tier::tests::eviction_takes_the_oldest_segment_and_the_ring_is_ordered_by_start_not_by_arrival` |
| 4 | M1c-3 | the cap loop takes `.last()` instead of `.first()` | killed | `tier::tests::a_byte_cap_evicts_the_oldest_whole_segment_first_and_holds_strictly`; `tier::tests::eviction_takes_the_oldest_segment_and_the_ring_is_ordered_by_start_not_by_arrival` |
| 5 | M1c-4 | `rescan` ignores `TailState::Incomplete` (no truncation, no `torn_repaired`) | killed | `tier::tests::a_restart_repairs_a_torn_tail_before_appending_and_counts_it` |
| 6 | M1c-4 | `scan_bytes` treats a short record as complete | killed | `segment::tests::a_torn_tail_is_reported_and_truncated_to_the_last_good_record`; `segment::tests::an_open_segment_with_a_damaged_tail_refuses_to_open_and_repairs_are_typed`; `tier::tests::a_restart_repairs_a_torn_tail_before_appending_and_counts_it` |
| 7 | M1c-4 | Python: the reference reports an incomplete tail as `complete` | killed | `ERROR: test_a_torn_tail_is_a_state_and_a_corrupt_record_is_a_named_refusal` |
| 8 | M1c-5 | `seal_open_segment` writes the footer onto the final name, no temp name and no rename | killed | `fsio::tests::sealing_copies_the_open_segment_appends_the_footer_and_removes_the_open_name`; `segment::tests::a_sealed_segment_is_never_opened_for_appending`; `events::tests::an_events_store_records_ring_and_persists_in_its_own_tier` |
| 9 | M1c-6 | `retire_with_set` retires every ephemeral id, reachable or not | killed | `dictionary::tests::retirement_removes_only_ids_no_segment_still_names`; `dictionary::tests::a_retirement_pass_calls_reach_once_per_live_entry_and_finishes`; `store::tests::a_store_routes_writes_enforces_bounds_and_retires_by_reachability` |
| 10 | M1c-6 | Python: the footer's record count is not compared with the records | killed | `FAIL: test_a_footer_that_disagrees_with_the_records_is_refused` |
| 11 | M1c-7 | `submit` removes the oldest job without counting it | killed | `queue::tests::the_queue_drops_the_oldest_job_first_and_counts_every_drop`; `queue::tests::the_bound_holds_after_every_single_operation`; `queue::tests::the_oldest_job_is_what_a_drop_removes_not_the_newest` |
| 12 | M1c-8 | `remove_segment` unlinks any look-alike name | killed | `tier::tests::a_foreign_file_a_link_and_a_directory_are_left_alone_and_reported` |
| 13 | M1c-8 | `rescan` adopts a segment whose header names another corpus | killed | `tier::tests::a_segment_of_another_corpus_in_this_directory_is_reported_and_never_touched` |
| 14 | M1c-9 | `StoreDir` follows a symbolic link (no `symlink_metadata` refusal) | killed | `fsio::tests::a_link_is_never_read_written_truncated_or_removed` |
| 15 | M1c-9 | Python: a record body that does not match its checksum is accepted | killed | `FAIL: test_a_torn_tail_is_a_state_and_a_corrupt_record_is_a_named_refusal` |

### Why two rows are one finding

Rows 3 and 4 are both M1c-3 ("evict newest instead of oldest"), approached from opposite sides:
reversing the ring's order, and keeping the order while taking the tail of it. A guard that
only fails one of them is a guard that depends on which side someone breaks it from, so both
are kept.

Rows 5 and 6 are the two halves of M1c-4: the *reader* (which must report and truncate) and
the *tier's rescan* (which must call the reader and keep the count). A fix in one would not
save the other, and the row that survived would have been the one nobody looked at.

## The fix round's rows (M1c-13 … M1c-22)

The Sonnet review of the first round (`reports/pi-hostobs-m1c-REVIEW.md`) left four blockers
and six major findings. B3 was fixed in the review's own commit; the other nine are rows here,
replayed on the fix-round head. **All thirty stored patches were killed**, each restore verified
by SHA-256, and the two patches that had gone stale while the code moved (`M1c-5`'s seal copy and
the first `M1c-20` fragment) were repaired before the definitive run rather than counted as
survivors:

| # | finding | patch | verdict | the test that failed |
|---|---|---|---|---|
| M1c-13 | B1 | retirement drops the queue's pending ids | killed | `store::tests::a_queued_job_keeps_an_id_live_after_its_segment_is_evicted` |
| M1c-13 | B1 | retirement drops the unwritten pins | killed | `store::tests::a_minted_id_is_live_until_its_record_reaches_a_segment_and_only_then_retirable` |
| M1c-14 | B2 | the stale open twin is adopted beside its sealed segment | killed | `tier::tests::a_stale_open_twin_of_a_sealed_segment_is_removed_never_counted_or_reopened` |
| M1c-15 | B4 | a file past the bound is skipped, not counted | killed | `tier::tests::a_file_past_the_bound_is_quarantined_and_counted_not_skipped` |
| M1c-16 | M6 | a zero-filled tail is corrupt (Rust) | killed | `segment::tests::a_zero_filled_tail_is_an_unwritten_tail_and_the_valid_prefix_stays_readable`; `tier::tests::a_zero_filled_tail_is_an_unwritten_tail_not_a_corrupt_segment` |
| M1c-16 | M6 | the Python reference calls a zero-filled tail corrupt | killed | `FAIL: test_a_zero_filled_tail_is_an_unwritten_tail_and_an_other_tail_is_corrupt` |
| M1c-17 | M1 | the ring is appended at the end | killed | `tier::tests::a_clock_step_backwards_still_leaves_the_ring_oldest_first` |
| M1c-18 | M4 | the first mount at a point wins | killed | `tmpfs::tests::the_last_mount_at_a_point_is_the_one_the_kernel_exposes` |
| M1c-19 | M4 | the tmpfs check does not resolve the path | killed | `tmpfs::tests::a_path_that_is_relative_contains_a_parent_or_crosses_a_link_is_refused` |
| M1c-20 | M5 | an unreadable header aborts the scan | killed | `tier::tests::a_rescan_keeps_going_when_one_store_file_cannot_be_read` |
| M1c-21 | M2 | a refused seal is not restored | killed | `tier::tests::a_failed_seal_puts_the_ring_entry_and_the_handle_back_and_leads_no_temp_behind` |
| M1c-22 | M3 | `FILE_MODE` is 0666 | killed | `tier::tests::files_and_directories_the_store_creates_are_owner_only` |
| M1c-22 | M3 | `DIR_MODE` is 0777 | killed | `tier::tests::files_and_directories_the_store_creates_are_owner_only` |
| M1c-23 | MINOR | `StoreStats::written` reports the queue's hand-offs | killed | `store::tests::a_store_routes_writes_enforces_bounds_and_retires_by_reachability` |
| M1c-24 | MINOR | `QueueStats::stats` reports a guessed zero | killed | `queue::tests::a_queue_that_never_overflows_is_lossless`, `the_bound_holds_after_every_single_operation`, `the_queue_drops_the_oldest_job_first_and_counts_every_drop` |

Two rows of the original fifteen also changed their killing test: `M1c-4`'s tier patch now
kills on two tests (the repaired tail test and the new zero-tail one, because the repair branch
is shared), and `M1c-2`'s expiry patch also fails the three new retirement tests, which drive
clock moves through a store. Neither is a weaker guard; both are more tests on one behaviour.

## What the campaign does not cover

* No concurrency: the store's single-writer property is a design constraint (one `Store`
  holds the queue), not a lock, so there is nothing to mutate. `HOB-33` tracks a real
  concurrency proof for the daemon package.
* No filesystem fault injection beyond `ENOSPC` (a typed error path, tested by
  `error::tests`), so a partial `write` or a failed `fsync` is exercised only where the API
  surface allows a fake. `HOB-32` tracks that gap, `HOB-30` the missing session spools
  (deliberately out of this package's scope), and `HOB-28` the O(records) scan that a range
  query pays per candidate file.
* The `lint` lane's own boundary check is mutated separately, by
  `tools/check-lints-mutation.py` (thirteen planted writes, one per forbidden form, and two allowances), because a lint
  that cannot go red on a planted write certifies nothing (plan v4 §A.3).
