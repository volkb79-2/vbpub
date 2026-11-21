#!/usr/bin/env python3
"""Replayable hand-mutation campaign for the P0 bug fold-in (`HOB-8`, `HOB-42`, `HOB-43`)
and its P0b follow-up (`HOB-55`, `HOB-56`, `HOB-58`).

Rust R2 (`cargo-mutants`, R-3b) is still not available in the lane image, so the guards this
package adds are checked by hand mutation, exactly as `m1c-mutants.py` does it: one stored patch
at a time, applied, measured, restored, and the restored file's SHA-256 compared with the
original. A `SURVIVED` row is a test gap and must be answered with a test before the campaign
is reported as all-killed; a `NOT APPLIED` row means the stored fragment no longer matches the
source (a stale patch, not a survivor); a `NOT-BUILT` row means the mutation did not compile and
is counted as unanswered, never as a kill.

    python3 -I docs/hand-mutants/m1d-mutants.py            # the full campaign
    python3 -I docs/hand-mutants/m1d-mutants.py --dry-run  # the patches only, each checked
                                                           # to match the source exactly once
    python3 -I docs/hand-mutants/m1d-mutants.py --only M1d-3  # one finding's patches, re-run
    python3 -I docs/hand-mutants/m1d-mutants.py --timeout-s 900  # a shorter per-run limit

`--dry-run` lists every patch and also reports, without changing any file, whether its stored
fragment occurs in the source exactly once; a fragment that does not is a stale patch.

The patches, each with the test that must go red:

| # | patch | the test that must go red |
|---|---|---|
| M1d-1 | `FrameHeader::parse` no longer refuses a length above the maximum / below the header (two Rust patches, two Python) | `parse_itself_bounds_the_declared_length_at_both_ends`, `test_the_frame_header_bounds_the_declared_length_at_both_ends` |
| M1d-2 | quarantined bytes are left out of the tier's byte total (an adopted `.corrupt` file; a file this scan quarantines) | `quarantined_bytes_count_against_the_cap_and_go_before_any_live_segment` |
| M1d-3 | the cap evicts live segments before quarantined files | `quarantined_bytes_count_against_the_cap_and_go_before_any_live_segment`, `debris_newer_than_live_history_is_still_evicted_first` |
| M1d-4 | quarantined files are ordered newest first | `quarantined_bytes_count_against_the_cap_and_go_before_any_live_segment` |
| M1d-5 | a look-alike `.corrupt` file is adopted (header proof dropped; tier name proof dropped; segment-name proof dropped) and then deleted | `a_quarantine_look_alike_is_reported_and_never_counted_or_removed` |
| M1d-6 | the sealed-footer check is dropped (Rust and Python) | `a_sealed_name_that_lost_its_footer_is_quarantined_not_served`, `a_truncated_sealed_file_is_quarantined_whole_not_repaired`, `test_a_sealed_name_without_a_verified_footer_is_refused` |
| M1d-7 | the header `seq` is not compared with the name's (Rust and Python) | `a_header_that_disagrees_with_its_name_is_quarantined`, `test_a_header_that_disagrees_with_its_name_is_refused_for_both_names` |
| M1d-8 | the header `start_real_ns` is not compared with the name's (Rust and Python) | the same two tests |
| M1d-9 | the seal re-derives its start second from the header with the current step | `a_sealed_segment_keeps_the_start_of_its_open_name_across_a_step_change` |
| M1d-10 | `keep_s` is not applied to quarantined files | `keep_s_removes_a_quarantined_file_by_the_key_its_name_keeps` |
| M1d-11 | the time section is sized at 14 bytes a bucket again (Rust and Python; HOB-48, length half) | `the_time_section_is_fifteen_bytes_per_bucket_padded_to_eight`, `a_history_block_of_zero_to_nine_buckets_round_trips_through_the_frame`, the `history-five-buckets` fixture, `test_the_time_section_is_fifteen_bytes_per_bucket_padded_to_eight` |
| M1d-12 | the store never counts an evicted quarantined file; counts it as an evicted segment; `Evicted::is_empty` ignores quarantined files | `an_evicted_quarantined_file_is_counted_apart_from_evicted_segments` |
| M1d-13 | removing a quarantined file does not take its bytes out of the total | `quarantined_bytes_count_against_the_cap_and_go_before_any_live_segment` |
| M1d-14 | Python: the 12 to 15 byte block-frame guards are dropped (two patches) | `test_the_frame_header_bounds_the_declared_length_at_both_ends`, the `block-without-directory` fixture, `test_a_block_frame_of_twelve_to_fifteen_bytes_is_a_directory_truncation` |
| M1d-15 | the `kind != File` guard for a `.corrupt` entry is dropped | `a_quarantine_look_alike_is_reported_and_never_counted_or_removed` (the directory's note reads `is a directory, not a regular file`, not a header-read failure) |
| M1d-16 | a quarantined / a live file deleted by hand is not recognised as gone | `a_quarantined_file_deleted_by_hand_does_not_fail_every_later_append`, `a_live_segment_deleted_by_hand_leaves_the_ring_without_failing_the_cap` |
| M1d-17 | a live / a quarantined name no longer reserves its sequence number | `a_name_never_comes_back_after_a_quarantine_and_a_restart` |
| M1d-18 | the segment / the event write path does not recompute the byte total from the tiers (HOB-56) | `the_store_total_is_the_tiers_own_total_after_every_operation`, `the_write_paths_subtract_what_their_own_eviction_removed`, `the_event_write_path_subtracts_what_its_own_eviction_removed` |
| M1d-19 | an open name that carries a footer is adopted (Rust and Python) | `an_open_name_that_carries_a_footer_is_quarantined_not_adopted`, `test_an_open_name_that_carries_a_footer_is_refused` |
| M1d-20 | a sequence above `SEQ_NAME_MAX` is accepted by the name parser / minted by `open_segment` / accepted by the Python parser (HOB-55) | `a_sequence_above_the_name_bound_is_not_a_segment_name`, `a_name_above_the_sequence_bound_is_a_look_alike_and_never_poisons_the_water_mark`, `a_name_at_the_sequence_bound_is_adopted_and_the_next_segment_is_refused`, `test_a_sequence_above_the_name_bound_is_a_look_alike_in_every_spelling` |
| M1d-21 | the byte total is incremental arithmetic again: the write path, `Store::evict`, or `seal_all` does not recompute it (HOB-56) | `the_store_total_is_the_tiers_own_total_after_every_operation`, `the_store_total_drops_a_quarantined_file_deleted_by_hand`, `the_store_total_drops_a_live_segment_deleted_by_hand` |
| M1d-22 | the eviction reports are not filled / not copied into the store's ring (write path and `evict`) / the ring has no bound / `Store::notes` leaves them out (HOB-58) | `a_quarantined_file_deleted_by_hand_does_not_fail_every_later_append`, `a_quarantined_file_that_cannot_be_removed_is_reported_to_the_caller`, `eviction_reports_from_the_write_path_reach_the_store_and_are_bounded`, `a_write_path_report_stays_in_the_store_after_its_tier_rescans`, `an_evict_report_stays_in_the_store_after_its_tier_rescans`, `the_store_total_drops_a_quarantined_file_deleted_by_hand`, `the_store_total_drops_a_live_segment_deleted_by_hand` |

The campaign first runs a BASELINE on the unmutated tree for each suite it will use and stops if
it is not green. A patch is `killed` only by a real test failure; a docker or runner failure,
a compile error or a Python import/syntax error is `NOT-BUILT` and counts as unanswered. A suite
run that exceeds `--timeout-s` (default 1800 s for a Rust suite, 300 s for a Python one; the
baseline uses the same limits) is `KILLED-TIMEOUT`: a mutant that makes the suite hang is detected,
as in standard mutation testing, and it is counted apart from `killed` in the summary. Under host
load a timeout is INCONCLUSIVE: rerun each timed-out mutant with the baseline's own duration in
mind (the limit must exceed the baseline's wall time) before counting it as a kill. The container
of a Rust run is named `hostobs-mut-<pid>-<n>`; a stale container of that exact name is removed
before the run, and the container is removed by that exact name on a timeout or on any other
exception (KeyboardInterrupt included); a failed `docker rm -f` is reported on stderr.

The results are recorded in [`../HAND-MUTANTS-M1D.md`](../HAND-MUTANTS-M1D.md) once the campaign
has been run on the gate head.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "tester-unified-rust:local"
# A suite run that exceeds its limit is a mutant that hangs the suite: detected, as in standard
# mutation testing, and classified KILLED-TIMEOUT (HOB-57). `--timeout-s` overrides both.
RUST_TIMEOUT_S = 1800
PYTHON_TIMEOUT_S = 300
# Every Rust run names its container `hostobs-mut-<pid>-<n>`, so a timed-out run is stopped by
# its exact name and never by a filter or an ancestor.
_RUN_NUMBERS = itertools.count()


@dataclass(frozen=True)
class Mutant:
    """One stored patch: replace `old` with `new` in `path`, then measure."""

    finding: str
    label: str
    path: str
    old: str
    new: str
    package: str  # "store", "proto" or "python"


MUTANTS: tuple[Mutant, ...] = (
    # ------------------------------------------------ HOB-8: the frame header bound
    Mutant(
        "M1d-1",
        "`FrameHeader::parse` no longer refuses a declared length above the maximum",
        "crates/proto/src/frame.rs",
        "        if declared > MAX_FRAME_BYTES {\n            return Err(ProtoError::OversizedFrame {\n                len: declared,\n                max: MAX_FRAME_BYTES,\n            });\n        }\n        if declared < FRAME_HEADER_LEN {",
        "        if false && declared > MAX_FRAME_BYTES {\n            return Err(ProtoError::OversizedFrame {\n                len: declared,\n                max: MAX_FRAME_BYTES,\n            });\n        }\n        if declared < FRAME_HEADER_LEN {",
        "proto",
    ),
    Mutant(
        "M1d-1",
        "`FrameHeader::parse` no longer refuses a declared length below the header",
        "crates/proto/src/frame.rs",
        "        if declared < FRAME_HEADER_LEN {",
        "        if false && declared < FRAME_HEADER_LEN {",
        "proto",
    ),
    Mutant(
        "M1d-1",
        "Python: the reference decoder does not refuse a declared length above the maximum",
        "python/reference/hostobs_wire_ref.py",
        "    if length > MAX_FRAME_BYTES:\n        raise WireError(\"oversized-frame\", len=length, max=MAX_FRAME_BYTES)\n    if length < FRAME_HEADER_LEN:",
        "    if False:\n        raise WireError(\"oversized-frame\", len=length, max=MAX_FRAME_BYTES)\n    if length < FRAME_HEADER_LEN:",
        "python",
    ),
    Mutant(
        "M1d-1",
        "Python: the reference decoder does not refuse a declared length below the header",
        "python/reference/hostobs_wire_ref.py",
        "    if length < FRAME_HEADER_LEN:",
        "    if False:",
        "python",
    ),
    # ----------------------------------------- HOB-43: quarantined files in the cap
    Mutant(
        "M1d-2",
        "an adopted quarantined file is left out of the tier's byte total",
        "crates/store/src/tier.rs",
        "                    Ok(header) if header.kind == self.config.kind => {\n                        bytes = bytes.saturating_add(entry.len);\n",
        "                    Ok(header) if header.kind == self.config.kind => {\n",
        "store",
    ),
    Mutant(
        "M1d-2",
        "a file this scan quarantines (corrupt tail) is left out of the byte total",
        "crates/store/src/tier.rs",
        "                    match scan.quarantine(&self.dir, &quarantine) {\n                        Ok(()) => {\n                            quarantined.push(entry.name.clone());\n                            bytes = bytes.saturating_add(scan.file_len);\n",
        "                    match scan.quarantine(&self.dir, &quarantine) {\n                        Ok(()) => {\n                            quarantined.push(entry.name.clone());\n",
        "store",
    ),
    Mutant(
        "M1d-3",
        "the cap evicts live segments before quarantined files",
        "crates/store/src/tier.rs",
        "                if !self.scan.quarantined_files.is_empty() {\n                    self.remove_quarantined(&mut evicted);\n                    continue;\n                }",
        "                if !self.scan.quarantined_files.is_empty() && self.scan.segments.is_empty() {\n                    self.remove_quarantined(&mut evicted);\n                    continue;\n                }",
        "store",
    ),
    Mutant(
        "M1d-4",
        "quarantined files are ordered newest first, so the newest debris goes first",
        "crates/store/src/tier.rs",
        "        quarantined_files.sort_by_key(|f| (f.parsed.start_s, f.parsed.seq, f.name.clone()));",
        "        quarantined_files.sort_by_key(|f| std::cmp::Reverse((f.parsed.start_s, f.parsed.seq, f.name.clone())));",
        "store",
    ),
    Mutant(
        "M1d-5",
        "a look-alike `.corrupt` file is adopted without the header proof",
        "crates/store/src/tier.rs",
        "                    Ok(header) if header.kind == self.config.kind => {\n                        bytes = bytes.saturating_add(entry.len);\n                        quarantined_files.push(QuarantinedFile {\n                            name: entry.name.clone(),",
        "                    Ok(_) | Err(_) => {\n                        bytes = bytes.saturating_add(entry.len);\n                        quarantined_files.push(QuarantinedFile {\n                            name: entry.name.clone(),",
        "store",
    ),
    Mutant(
        "M1d-5",
        "a quarantine name of another tier is adopted",
        "crates/store/src/tier.rs",
        "                if parsed.tier != self.config.name {\n                    notes.push(TierNote {\n                        name: entry.name.clone(),\n                        reason: \"names another tier\".to_string(),\n                    });\n                    continue;\n                }\n                // Not yet proved this tier's own: reserves at most `UNPROVEN_SEQ_MAX`.\n                highest_seen = highest_seen.max(Some(parsed.seq.min(UNPROVEN_SEQ_MAX)));\n                if entry.kind != EntryKind::File {",
        "                if false && parsed.tier != self.config.name {\n                    notes.push(TierNote {\n                        name: entry.name.clone(),\n                        reason: \"names another tier\".to_string(),\n                    });\n                    continue;\n                }\n                // Not yet proved this tier's own: reserves at most `UNPROVEN_SEQ_MAX`.\n                highest_seen = highest_seen.max(Some(parsed.seq.min(UNPROVEN_SEQ_MAX)));\n                if entry.kind != EntryKind::File {",
        "store",
    ),
    Mutant(
        "M1d-5",
        "a `.corrupt` name that is not a segment name is adopted under a fabricated key",
        "crates/store/src/tier.rs",
        "                let Ok(parsed) = SegmentFileName::parse_quarantined(&entry.name) else {",
        "                let Ok(parsed) = SegmentFileName::parse_quarantined(&entry.name)\n                    .or_else(|_| SegmentFileName::parse(\"hot-0-0.seg\"))\n                else {",
        "store",
    ),
    Mutant(
        "M1d-10",
        "`keep_s` is not applied to quarantined files",
        "crates/store/src/tier.rs",
        "            if span_end_ns.saturating_add(keep_ns) > now_ns {\n                break;\n            }\n            self.remove_quarantined(&mut evicted);",
        "            if true || span_end_ns.saturating_add(keep_ns) > now_ns {\n                break;\n            }\n            self.remove_quarantined(&mut evicted);",
        "store",
    ),
    # ------------------------------------------ HOB-42: a name is a claim to back
    Mutant(
        "M1d-6",
        "a sealed name is trusted without a verified footer",
        "crates/store/src/segment.rs",
        "        if !parsed.open && self.footer.is_none() {",
        "        if false && !parsed.open && self.footer.is_none() {",
        "store",
    ),
    Mutant(
        "M1d-6",
        "Python: a sealed name is trusted without a verified footer",
        "python/reference/hostobs_segment_ref.py",
        "    if not parsed[\"open\"] and decoded[\"footer\"] is None:",
        "    if False:",
        "python",
    ),
    Mutant(
        "M1d-7",
        "the header `seq` is not compared with the name's",
        "crates/store/src/segment.rs",
        "        if self.header.seq != parsed.seq {",
        "        if false && self.header.seq != parsed.seq {",
        "store",
    ),
    Mutant(
        "M1d-7",
        "Python: the header `seq` is not compared with the name's",
        "python/reference/hostobs_segment_ref.py",
        "    if header[\"seq\"] != parsed[\"seq\"]:",
        "    if False:",
        "python",
    ),
    Mutant(
        "M1d-8",
        "the header `start_real_ns` is not compared with the name's",
        "crates/store/src/segment.rs",
        "        if self.header.start_real_ns != parsed.start_real_ns() {",
        "        if false && self.header.start_real_ns != parsed.start_real_ns() {",
        "store",
    ),
    Mutant(
        "M1d-8",
        "Python: the header `start_real_ns` is not compared with the name's",
        "python/reference/hostobs_segment_ref.py",
        "    if header[\"start_real_ns\"] != expected_ns:",
        "    if False:",
        "python",
    ),
    Mutant(
        "M1d-9",
        "the seal re-derives its start second from the header with the current step",
        "crates/store/src/tier.rs",
        "        let start_s = match SegmentFileName::parse(open.name()) {\n            Ok(parsed) => parsed.start_s,\n",
        "        let start_s = match self.start_for(open.header().start_real_ns.max(0)) {\n            Ok(start_s) => start_s,\n",
        "store",
    ),
    # ------------------------------ HOB-48 (length half): the time section's bucket size
    Mutant(
        "M1d-11",
        "the time section is sized at 14 bytes a bucket again (Rust)",
        "crates/proto/src/frame.rs",
        "const TIME_BUCKET_BYTES: usize = 8 + 4 + 2 + 1;",
        "const TIME_BUCKET_BYTES: usize = 8 + 4 + 2;",
        "proto",
    ),
    Mutant(
        "M1d-11",
        "the time section is sized at 14 bytes a bucket again (Python)",
        "python/reference/hostobs_wire_ref.py",
        "TIME_BUCKET_BYTES = 8 + 4 + 2 + 1",
        "TIME_BUCKET_BYTES = 8 + 4 + 2",
        "python",
    ),
    # ----------------------------------- the review's fixes: counters, deleted files, names
    Mutant(
        "M1d-12",
        "the store never counts an evicted quarantined file",
        "crates/store/src/store.rs",
        "        self.evicted_quarantined = self\n            .evicted_quarantined\n            .saturating_add(u64::try_from(done.quarantined.len()).unwrap_or(u64::MAX));\n",
        "",
        "store",
    ),
    Mutant(
        "M1d-12",
        "evicted quarantined files are counted as evicted segments",
        "crates/store/src/store.rs",
        "            .saturating_add(u64::try_from(done.len()).unwrap_or(u64::MAX));",
        "            .saturating_add(u64::try_from(done.len() + done.quarantined.len()).unwrap_or(u64::MAX));",
        "store",
    ),
    Mutant(
        "M1d-12",
        "`Evicted::is_empty` ignores quarantined files",
        "crates/store/src/tier.rs",
        "        self.segments.is_empty() && self.quarantined.is_empty()",
        "        self.segments.is_empty()",
        "store",
    ),
    Mutant(
        "M1d-13",
        "removing a quarantined file does not take its bytes out of the total",
        "crates/store/src/tier.rs",
        "        self.bytes = self.bytes.saturating_sub(file.len);\n",
        "",
        "store",
    ),
    Mutant(
        "M1d-14",
        "Python: a 12 or 13 byte block frame reports the wrong `need` (the 2-byte "
        "directory guard is gone)",
        "python/reference/hostobs_wire_ref.py",
        "    if have < 2:\n",
        "    if False:\n",
        "python",
    ),
    Mutant(
        "M1d-14",
        "Python: a 14 or 15 byte block frame reaches `struct.unpack_from` (the 4-byte "
        "directory-header guard is gone)",
        "python/reference/hostobs_wire_ref.py",
        "    if have < 4:\n",
        "    if False:\n",
        "python",
    ),
    Mutant(
        "M1d-15",
        "a `.corrupt` entry that is not a regular file is not refused by kind (the note then "
        "names a header-read failure instead of the kind)",
        "crates/store/src/tier.rs",
        "                if entry.kind != EntryKind::File {",
        "                if false && entry.kind != EntryKind::File {",
        "store",
    ),
    Mutant(
        "M1d-16",
        "a quarantined file deleted by hand is not recognised as gone",
        "crates/store/src/tier.rs",
        "        let gone = matches!(self.dir.metadata(&file.name), Ok(None));",
        "        let gone = false;",
        "store",
    ),
    Mutant(
        "M1d-16",
        "a live segment deleted by hand is not recognised as gone",
        "crates/store/src/tier.rs",
        "        let gone = self.dir.metadata(name)?.is_none();",
        "        let gone = false;",
        "store",
    ),
    Mutant(
        "M1d-17",
        "an unproven live name no longer reserves its sequence number",
        "crates/store/src/tier.rs",
        "            highest_seen = highest_seen.max(Some(parsed.seq.min(UNPROVEN_SEQ_MAX)));\n            match entry.kind {",
        "            match entry.kind {",
        "store",
    ),
    Mutant(
        "M1d-17",
        "a quarantined name no longer reserves its sequence number",
        "crates/store/src/tier.rs",
        "                // Not yet proved this tier's own: reserves at most `UNPROVEN_SEQ_MAX`.\n                highest_seen = highest_seen.max(Some(parsed.seq.min(UNPROVEN_SEQ_MAX)));\n                if entry.kind != EntryKind::File {",
        "                if entry.kind != EntryKind::File {",
        "store",
    ),
    Mutant(
        "M1d-18",
        "the segment write path does not recompute the byte total from the tiers after an append",
        "crates/store/src/store.rs",
        "        self.refresh_bytes();\n        let outcome = appended?;",
        "        let outcome = appended?;",
        "store",
    ),
    Mutant(
        "M1d-18",
        "the event write path does not recompute the byte total from the tiers after a record",
        "crates/store/src/store.rs",
        "        self.refresh_bytes();\n        let outcome = recorded?;",
        "        let outcome = recorded?;",
        "store",
    ),
    Mutant(
        "M1d-19",
        "an open name that carries a footer is adopted",
        "crates/store/src/segment.rs",
        "        if parsed.open && self.footer.is_some() {",
        "        if false && parsed.open && self.footer.is_some() {",
        "store",
    ),
    Mutant(
        "M1d-19",
        "Python: an open name that carries a footer is accepted",
        "python/reference/hostobs_segment_ref.py",
        "    if parsed[\"open\"] and decoded[\"footer\"] is not None:",
        "    if False:",
        "python",
    ),
    # ----------------------------------- HOB-55: the sequence bound of a segment name
    Mutant(
        "M1d-20",
        "the name parser accepts a sequence above `SEQ_NAME_MAX` again",
        "crates/store/src/format.rs",
        "        if seq > SEQ_NAME_MAX {\n            // Not a sequence this store ever mints (HOB-55): a look-alike, never ours.\n            return Err(StoreError::SegmentName {\n                name: name.to_string(),\n            });\n        }\n",
        "",
        "store",
    ),
    Mutant(
        "M1d-20",
        "`open_segment` mints a sequence above `SEQ_NAME_MAX` instead of failing",
        "crates/store/src/tier.rs",
        "        if seq > SEQ_NAME_MAX {\n            // A name above the bound is not a segment name of this store (HOB-55), so the\n            // sequence space is exhausted rather than continued under a name no scan accepts.\n            return Err(StoreError::LengthOverflow);\n        }\n",
        "",
        "store",
    ),
    Mutant(
        "M1d-20",
        "Python: the reference parser accepts a sequence above `SEQ_NAME_MAX`",
        "python/reference/hostobs_segment_ref.py",
        "    if int(start_s) >= 1 << 64 or int(seq) > SEQ_NAME_MAX:",
        "    if int(start_s) >= 1 << 64 or int(seq) >= 1 << 64:",
        "python",
    ),
    Mutant(
        "M1d-20",
        "`open_segment` refuses the bound itself (`>` becomes `>=`)",
        "crates/store/src/tier.rs",
        "        if seq > SEQ_NAME_MAX {\n            // A name above the bound",
        "        if seq >= SEQ_NAME_MAX {\n            // A name above the bound",
        "store",
    ),
    Mutant(
        "M1d-20",
        "an unproven name reserves its full sequence (the cap at the bound minus one is gone)",
        "crates/store/src/tier.rs",
        "            highest_seen = highest_seen.max(Some(parsed.seq.min(UNPROVEN_SEQ_MAX)));\n            match entry.kind {",
        "            highest_seen = highest_seen.max(Some(parsed.seq));\n            match entry.kind {",
        "store",
    ),
    Mutant(
        "M1d-20",
        "an adopted segment at the bound does not reserve it (the proven maximum is dropped)",
        "crates/store/src/tier.rs",
        "        highest_seen = highest_seen.max(proven_max);",
        "        let _ = proven_max;",
        "store",
    ),
    # ------------------------------ HOB-56: the byte total is the tiers' own total
    Mutant(
        "M1d-21",
        "the segment write path is incremental arithmetic again (adds written bytes, subtracts evicted bytes)",
        "crates/store/src/store.rs",
        "        self.refresh_bytes();\n        let outcome = appended?;",
        "        let outcome = appended?;\n        self.stats.bytes = self.stats.bytes.saturating_add(outcome.bytes_written);\n        self.stats.bytes = self.stats.bytes.saturating_sub(outcome.evicted.bytes);",
        "store",
    ),
    Mutant(
        "M1d-21",
        "`Store::evict` subtracts the evicted bytes instead of recomputing (a vanished file drifts the total)",
        "crates/store/src/store.rs",
        "        // The tiers' own totals are the truth, after a refusal as well.\n        self.refresh_bytes();\n        result?;",
        "        for done in evicted.values() {\n            self.stats.bytes = self.stats.bytes.saturating_sub(done.bytes);\n        }\n        result?;",
        "store",
    ),
    Mutant(
        "M1d-21",
        "`seal_all` does not recompute the byte total (the footers are missing from it)",
        "crates/store/src/store.rs",
        "        let sealed = self.seal_each();\n        // A seal replaces the open name by the sealed one (footer bytes), so the total moves.\n        self.refresh_bytes();\n        sealed",
        "        self.seal_each()",
        "store",
    ),
    # --------------------------------------- HOB-58: eviction reports reach the caller
    Mutant(
        "M1d-22",
        "`Tier::report` does not fill `Evicted::notes`",
        "crates/store/src/tier.rs",
        "        evicted.notes.push(note.clone());\n        let kept =",
        "        let kept =",
        "store",
    ),
    Mutant(
        "M1d-22",
        "the write path does not copy the eviction's notes into the store's ring",
        "crates/store/src/store.rs",
        "        keep_notes(&mut self.eviction_notes, &outcome.evicted);\n        Ok(WriteOutcome {",
        "        Ok(WriteOutcome {",
        "store",
    ),
    Mutant(
        "M1d-22",
        "`Store::evict` does not copy a tier's notes into the store's ring",
        "crates/store/src/store.rs",
        "            let done = state.tier.evict(now_ns)?;\n            keep_notes(&mut self.eviction_notes, &done);\n",
        "            let done = state.tier.evict(now_ns)?;\n",
        "store",
    ),
    Mutant(
        "M1d-22",
        "the store's notes ring has no bound",
        "crates/store/src/store.rs",
        "        if ring.len() >= MAX_EVICTION_NOTES {\n            ring.pop_front();\n        }\n",
        "",
        "store",
    ),
    Mutant(
        "M1d-22",
        "the scan notes' eviction reports have no cap",
        "crates/store/src/tier.rs",
        "        if kept >= MAX_EVICTION_NOTES {\n            self.scan.notes.remove(self.reports_from);\n        }\n",
        "",
        "store",
    ),
    Mutant(
        "M1d-22",
        "`Store::notes` hides a tier's note about its own name",
        "crates/store/src/store.rs",
        "        Ok(parsed) => parsed.tier == producer || !owned.contains(&parsed.tier.as_str()),",
        "        Ok(parsed) => !owned.contains(&parsed.tier.as_str()),",
        "store",
    ),
    Mutant(
        "M1d-22",
        "`Store::notes` shows one tier's note about another tier's file in a shared directory",
        "crates/store/src/store.rs",
        "        Ok(parsed) => parsed.tier == producer || !owned.contains(&parsed.tier.as_str()),",
        "        Ok(_) => true,",
        "store",
    ),
    Mutant(
        "M1d-22",
        "`Store::notes` shows a report twice (the ring's copy is not deduplicated)",
        "crates/store/src/store.rs",
        "            if !notes.contains(note) {\n                notes.push(note.clone());\n            }\n",
        "            notes.push(note.clone());\n",
        "store",
    ),
    Mutant(
        "M1d-22",
        "`Store::notes` leaves the ring's reports out (seen only after the tier's own scan notes are reset by a rescan)",
        "crates/store/src/store.rs",
        "        for note in &self.eviction_notes {\n            if !notes.contains(note) {\n                notes.push(note.clone());\n            }\n        }\n",
        "",
        "store",
    ),
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _failing_rust_tests(log: str) -> list[str]:
    names: set[str] = set()
    for line in log.splitlines():
        stripped = line.strip()
        if stripped.startswith("FAIL ") and "::" in stripped:
            names.add(stripped.split("]")[-1].strip() if "]" in stripped else stripped)
        if stripped.startswith("test ") and stripped.endswith("FAILED"):
            names.add(stripped[len("test ") : -len(" ... FAILED")].strip())
        if stripped.startswith("---- ") and stripped.endswith(" stdout ----"):
            names.add(stripped[len("---- ") : -len(" stdout ----")].strip())
    return sorted(names)


def host_mount_for(path: Path) -> tuple[Path, Path]:
    """The (host source, target) mount of this running cockpit that contains `path`."""
    container = os.environ.get("HOSTNAME", "")
    if not container:
        raise SystemExit("HOSTNAME is not set: cannot read this container's mounts")
    run = subprocess.run(
        [
            "docker",
            "inspect",
            "--format",
            "{{range .Mounts}}{{.Source}}|{{.Destination}}\n{{end}}",
            container,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if run.returncode != 0:
        raise SystemExit(
            f"cannot inspect container {container} to learn the host mount path: {run.stderr.strip()}"
        )
    candidates = []
    for line in run.stdout.splitlines():
        if "|" not in line:
            continue
        source, destination = line.split("|", 1)
        destination_path = Path(destination)
        try:
            path.relative_to(destination_path)
        except ValueError:
            continue
        candidates.append((len(destination), Path(source), destination_path))
    if not candidates:
        raise SystemExit(f"no bind mount of {container} contains {path}")
    _length, source, destination = max(candidates)
    return source, destination


def host_namespace(path: Path) -> tuple[Path, Path, Path]:
    source, destination = host_mount_for(path)
    host_path = source / path.relative_to(destination)
    return host_path, source, destination


def remove_container(name: str) -> None:
    """Remove one container by its EXACT name (never a filter or an ancestor).

    A container that does not exist is the normal case and is silent; any other failure of
    `docker rm -f` is reported on stderr, never discarded, because a container left running
    keeps a lane's cgroup slot and CPU.
    """
    run = subprocess.run(
        ["docker", "rm", "-f", name], capture_output=True, text=True, check=False
    )
    if run.returncode != 0 and "No such container" not in run.stderr:
        print(
            f"WARNING: `docker rm -f {name}` failed (exit {run.returncode}): {run.stderr.strip()}",
            file=sys.stderr,
        )


def run_rust(package: str, timeout_s: int) -> tuple[str, list[str]]:
    environment = os.environ.copy()
    host_path, host_source, cockpit_path = host_namespace(ROOT)
    container = f"hostobs-mut-{os.getpid()}-{next(_RUN_NUMBERS)}"
    command = [
        "docker",
        "run",
        "--rm",
        "--name",
        container,
        "--network",
        "none",
        "--cgroup-parent",
        environment["CGROUP_PARENT_DEV_GATES"],
        "-e",
        f"CGROUP_PARENT_DEV_GATES={environment['CGROUP_PARENT_DEV_GATES']}",
        "-v",
        f"{host_source}:{host_source}",
        "-v",
        f"{host_source}:{cockpit_path}",
        "-w",
        str(host_path),
        IMAGE,
        "bash",
        "-c",
        f"export CARGO_TARGET_DIR=/tmp/t; tools/cargo-offline.sh test -p hostobs-{package} --offline --locked",
    ]
    # A container of this exact name left by an earlier process with the same pid (pid reuse)
    # would make `docker run --name` refuse: remove it first, by its exact name.
    remove_container(container)
    try:
        run = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=timeout_s
        )
    except subprocess.TimeoutExpired:
        # `subprocess.run` stops the docker client, not the container it started: remove that
        # one container by its exact name (never by a filter or an ancestor).
        remove_container(container)
        return "timeout", [f"no verdict within {timeout_s} s"]
    except BaseException:
        # Any other exit, KeyboardInterrupt included, must not leave the container running.
        remove_container(container)
        raise
    log = run.stdout + run.stderr
    if run.returncode == 0:
        return "survived", []
    failing = _failing_rust_tests(log)
    if "could not compile" in log or "error[E" in log:
        # A mutation that does not type-check is **not** a kill: it proves nothing about the
        # tests, so it is reported separately and counted as unanswered.
        return "not-built", ["does not compile"]
    if failing or "test result: FAILED" in log:
        return "killed", failing
    # A non-zero exit with no failing test and no compile error (docker could not start, the
    # image is missing, the runner died) says nothing about the tests either.
    return "not-built", [f"no test verdict (exit {run.returncode})"]


def run_python(timeout_s: int) -> tuple[str, list[str]]:
    try:
        run = subprocess.run(
            [sys.executable, "-I", "-m", "unittest", "discover", "-s", "python/tests"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return "timeout", [f"no verdict within {timeout_s} s"]
    log = run.stdout + run.stderr
    if run.returncode == 0:
        return "survived", []
    # A mutation that makes a module unimportable (a syntax error) fails discovery itself;
    # that is a patch that does not run, not a test that went red.
    if "SyntaxError" in log or "_FailedTest" in log or "ImportError" in log:
        return "not-built", ["does not import"]
    failing = sorted(
        line.strip() for line in log.splitlines() if line.startswith(("FAIL:", "ERROR:"))
    )
    if failing:
        return "killed", failing
    return "not-built", [f"no test verdict (exit {run.returncode})"]


def measure(package: str, timeout_s: int | None = None) -> tuple[str, list[str]]:
    """One suite run: `killed`, `survived` (green), `timeout` (hung) or `not-built` (no verdict).

    `timeout_s` overrides the per-suite default (1800 s for Rust, 300 s for Python); the
    baseline uses the same limits as the mutants.
    """
    if package == "python":
        return run_python(PYTHON_TIMEOUT_S if timeout_s is None else timeout_s)
    return run_rust(package, RUST_TIMEOUT_S if timeout_s is None else timeout_s)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the patches, check each fragment matches the source exactly once, and exit",
    )
    parser.add_argument(
        "--only",
        metavar="FINDING",
        help="run only the patches whose finding id is this (a focused re-run, never a substitute)",
    )
    parser.add_argument(
        "--timeout-s",
        type=int,
        metavar="SECONDS",
        help=(
            "kill a suite run (the baseline included) after this many seconds; default "
            f"{RUST_TIMEOUT_S} for a Rust suite and {PYTHON_TIMEOUT_S} for a Python one. A mutant "
            "whose run times out is KILLED-TIMEOUT: it hangs the suite, which detects it"
        ),
    )
    args = parser.parse_args()
    if args.timeout_s is not None and args.timeout_s < 1:
        parser.error("--timeout-s must be at least 1")
    if args.dry_run:
        stale = 0
        for mutant in MUTANTS:
            count = (ROOT / mutant.path).read_text(encoding="utf-8").count(mutant.old)
            state = "matches once" if count == 1 else f"STALE ({count} matches)"
            if count != 1:
                stale += 1
            print(f"{mutant.finding}\t{mutant.label}\t{mutant.path}\t{state}")
        print(f"{len(MUTANTS)} stored patches, {stale} stale")
        return 1 if stale else 0
    if "CGROUP_PARENT_DEV_GATES" not in os.environ:
        print(
            "CGROUP_PARENT_DEV_GATES is not set: refusing to place lane containers unconfined",
            file=sys.stderr,
        )
        return 2
    # A BASELINE on the unmutated tree first, per suite the campaign will use: a kill is only
    # meaningful against a suite that is green without the patch. A baseline that is not green
    # (including a runner that cannot start) stops the campaign before any file is touched.
    wanted = [m for m in MUTANTS if not args.only or m.finding == args.only]
    for package in sorted({m.package for m in wanted}):
        outcome, detail = measure(package, args.timeout_s)
        print(f"BASELINE\t{package}\t{outcome}\t{'; '.join(detail) if detail else '-'}")
        sys.stdout.flush()
        if outcome != "survived":
            print(f"the unmutated {package} suite is not green; no mutant was applied", file=sys.stderr)
            return 2
    survivors = 0
    killed = 0
    killed_timeout = 0
    for mutant in MUTANTS:
        if args.only and mutant.finding != args.only:
            continue
        path = ROOT / mutant.path
        original = path.read_text(encoding="utf-8")
        if mutant.old not in original:
            print(f"NOT APPLIED\t{mutant.label}: the patch no longer matches {mutant.path}")
            survivors += 1
            continue
        before = digest(path)
        verdict = "SURVIVED"
        failing: list[str] = []
        try:
            path.write_text(original.replace(mutant.old, mutant.new, 1), encoding="utf-8")
            outcome, failing = measure(mutant.package, args.timeout_s)
            verdict = {
                "killed": "killed",
                "survived": "SURVIVED",
                "not-built": "NOT-BUILT",
                "timeout": "KILLED-TIMEOUT",
            }[outcome]
        finally:
            # The restore happens even when the measurement raises: a crashed campaign must
            # never leave a mutation in the tree.
            path.write_text(original, encoding="utf-8")
        after = digest(path)
        assert before == after, f"restore mismatch in {mutant.path}"
        if verdict == "killed":
            killed += 1
        elif verdict == "KILLED-TIMEOUT":
            killed_timeout += 1
        else:
            survivors += 1
        print(
            f"{verdict}\t{mutant.finding}\t{mutant.label}\t"
            + ("; ".join(failing[:3]) if failing else "-")
        )
        sys.stdout.flush()
    print(
        f"\n{len(MUTANTS)} patches: {killed} killed, {killed_timeout} killed by timeout, "
        f"{survivors} not killed (survived, not built or not applied); "
        "every restore verified by SHA-256"
    )
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
