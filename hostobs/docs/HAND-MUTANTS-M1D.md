# Hand-mutation record for the P0 bug fold-in (`proto` and `store`)

Rust R2 (a `cargo-mutants` campaign judged by assay) is **not adopted yet** (`HOB-45`), so the
guards the P0 package adds are checked by hand mutation, in exactly the method of
[`HAND-MUTANTS-M1C.md`](HAND-MUTANTS-M1C.md). The earlier campaigns are
[`HAND-MUTANTS-M1.md`](HAND-MUTANTS-M1.md), [`HAND-MUTANTS-M1B.md`](HAND-MUTANTS-M1B.md) and
M1C. The package fixes `HOB-8`, `HOB-42`, `HOB-43` and the length half of `HOB-48`.

**Status: the campaign has not been run.** The stored patches were written and checked with
`--dry-run` (each fragment occurs in the source exactly once); no mutant has been executed,
because the Rust lane image was not available when this was written. Nothing below is a
measurement.

## Method

Every mutation is a **stored patch** in a replayable script, not prose:

```sh
python3 -I docs/hand-mutants/m1d-mutants.py            # the full campaign
python3 -I docs/hand-mutants/m1d-mutants.py --dry-run   # the patches only, each checked
python3 -I docs/hand-mutants/m1d-mutants.py --only M1d-3  # one finding's patches, re-run
```

Each step: record the file's SHA-256, replace one exact fragment, run the affected suite,
classify the outcome, restore the file (in a `finally`, so a crash cannot leave a mutation in
the tree), and assert the restored digest equals the original.

* Rust steps run in the lane image, `tester-unified-rust:local`, `--network none`, on the gates
  cgroup tier, with `tools/cargo-offline.sh test -p hostobs-<proto|store> --offline --locked`;
  the host mount is read from the cockpit's own bind mounts (the estate's dual-mount rule).
* Python steps run `python3 -I -m unittest discover -s python/tests`.

A mutation whose source does not type-check is **`NOT-BUILT`** and counts as unanswered, never
as a kill; a patch whose fragment no longer matches the source is **`NOT APPLIED`**, a stale
patch rather than a survivor. `--dry-run` also reports, without touching a file, whether each
fragment matches exactly once. A `SURVIVED` row means a test is missing: add the test and re-run
from scratch.

## The patches

| # | finding | patch | the test that must go red |
|---|---|---|---|
| M1d-1 | HOB-8 | `FrameHeader::parse` no longer refuses a length above the maximum; no longer refuses one below the header (two Rust patches); the same two in the Python reference | `parse_itself_bounds_the_declared_length_at_both_ends`; `test_the_frame_header_bounds_the_declared_length_at_both_ends` |
| M1d-2 | HOB-43 | an adopted `.corrupt` file is left out of the byte total; a file this scan quarantines is left out of it | `quarantined_bytes_count_against_the_cap_and_go_before_any_live_segment` |
| M1d-3 | HOB-43 | the cap evicts live segments before quarantined files | the same test; `debris_newer_than_live_history_is_still_evicted_first` |
| M1d-4 | HOB-43 | quarantined files are ordered newest first | `quarantined_bytes_count_against_the_cap_and_go_before_any_live_segment` |
| M1d-5 | HOB-43 | a look-alike `.corrupt` file is adopted (header proof dropped; tier-name proof dropped; segment-name proof replaced by a fabricated key), and so is counted and deleted | `a_quarantine_look_alike_is_reported_and_never_counted_or_removed` |
| M1d-6 | HOB-42 | the sealed-footer check is dropped (Rust and Python) | `a_sealed_name_that_lost_its_footer_is_quarantined_not_served`; `a_truncated_sealed_file_is_quarantined_whole_not_repaired`; `test_a_sealed_name_without_a_verified_footer_is_refused` |
| M1d-7 | HOB-42 | the header `seq` is not compared with the name's (Rust and Python) | `a_header_that_disagrees_with_its_name_is_quarantined`; `test_a_header_that_disagrees_with_its_name_is_refused_for_both_names` |
| M1d-8 | HOB-42 | the header `start_real_ns` is not compared with the name's (Rust and Python) | the same two tests |
| M1d-9 | HOB-42 | the seal re-derives its start second from the header with the current `segment_s` | `a_sealed_segment_keeps_the_start_of_its_open_name_across_a_step_change` |
| M1d-10 | HOB-43 | `keep_s` is not applied to quarantined files | `keep_s_removes_a_quarantined_file_by_the_key_its_name_keeps` |
| M1d-11 | HOB-48 (length) | the time section is sized at 14 bytes a bucket again (Rust and Python) | `the_time_section_is_fifteen_bytes_per_bucket_padded_to_eight`; `a_history_block_of_zero_to_nine_buckets_round_trips_through_the_frame`; the `history-five-buckets` golden fixture; `test_the_time_section_is_fifteen_bytes_per_bucket_padded_to_eight` |

| M1d-12 | HOB-43 | the store never counts an evicted quarantined file; counts it as an evicted segment; `Evicted::is_empty` ignores quarantined files | `an_evicted_quarantined_file_is_counted_apart_from_evicted_segments` |
| M1d-13 | HOB-43 | removing a quarantined file does not take its bytes out of the total | `quarantined_bytes_count_against_the_cap_and_go_before_any_live_segment` |
| M1d-14 | HOB-8 | Python: the 12 to 15 byte block-frame guard is dropped | `test_the_frame_header_bounds_the_declared_length_at_both_ends`; the `block-without-directory` fixture |
| M1d-15 | HOB-43 | the `kind != File` guard on a `.corrupt` entry is dropped | none expected: **possibly an equivalent mutant**, because `read_window` fails on a directory and the name is reported anyway; if it survives, that is the explanation, not a missing test |
| M1d-16 | HOB-43 | a quarantined / a live file deleted by hand is not recognised as gone | `a_quarantined_file_deleted_by_hand_does_not_fail_every_later_append`; `a_live_segment_deleted_by_hand_leaves_the_ring_without_failing_the_cap` |
| M1d-17 | HOB-43 | a live / a quarantined name no longer reserves its sequence number | `a_name_never_comes_back_after_a_quarantine_and_a_restart` |
| M1d-18 | HOB-43 | the segment / the event write path does not subtract its own eviction | `the_write_paths_subtract_what_their_own_eviction_removed`; `the_event_write_path_subtracts_what_its_own_eviction_removed` |
| M1d-19 | HOB-42 | an open name that carries a footer is adopted (Rust and Python) | `an_open_name_that_carries_a_footer_is_quarantined_not_adopted`; `test_an_open_name_that_carries_a_footer_is_refused` |

Thirty-five stored patches in all (`--dry-run`: 35 stored patches, 0 stale). The campaign first
runs a BASELINE per suite on the unmutated tree and stops if it is not green; a patch is `killed`
only by a real test failure, and a docker or runner failure, a compile error or a Python import
error is `NOT-BUILT` (unanswered).

## Results

| # | verdict | tests that failed |
|---|---|---|
| all rows | to be run on the gate head | to be run on the gate head |

The per-patch verdict table and the survivor count are to be filled in from the campaign's
output once it has run on the gate head; until then this file states no kill count.

## Known limit: an unreachable second bound

`decode_block` keeps its own `declared > MAX_FRAME_BYTES` check (belt and braces: a future caller
that bypasses `parse` is still bounded). Through `FrameHeader::parse` that branch can no longer
be reached, because `parse` already refuses the same length first and `decode_block` calls it
before anything else. No test can drive the branch, so its body, the multi-line
`return Err(ProtoError::OversizedFrame { len: declared, max: MAX_FRAME_BYTES });` (six lines in
`frame.rs`), is uncovered for `selftest`/`assay-r1` and a mutant that deletes it would survive
as an equivalent mutant. It is
deliberately not among the patches above. If the coverage floor
(`assay.toml`, `coverage-floors.toml`) is judged on it, the answer is to remove the dead check or
to accept the line as the documented exception, never to lower a floor.
