#!/usr/bin/env python3
"""Replayable hand-mutation campaign for the P0 bug fold-in (`HOB-8`, `HOB-42`, `HOB-43`).

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

The results are recorded in [`../HAND-MUTANTS-M1D.md`](../HAND-MUTANTS-M1D.md) once the campaign
has been run on the gate head.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "tester-unified-rust:local"


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
        "                if !self.scan.quarantined_files.is_empty() {\n                    self.remove_quarantined(&mut evicted)?;\n                    continue;\n                }",
        "                if !self.scan.quarantined_files.is_empty() && self.scan.segments.is_empty() {\n                    self.remove_quarantined(&mut evicted)?;\n                    continue;\n                }",
        "store",
    ),
    Mutant(
        "M1d-4",
        "quarantined files are ordered newest first, so the newest debris goes first",
        "crates/store/src/tier.rs",
        "            (left.parsed.start_s, left.parsed.seq, &left.name).cmp(&(\n                right.parsed.start_s,\n                right.parsed.seq,\n                &right.name,\n            ))",
        "            (right.parsed.start_s, right.parsed.seq, &right.name).cmp(&(\n                left.parsed.start_s,\n                left.parsed.seq,\n                &left.name,\n            ))",
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
        "                if parsed.tier != self.config.name {\n                    notes.push(TierNote {\n                        name: entry.name.clone(),\n                        reason: \"names another tier\".to_string(),\n                    });\n                    continue;\n                }\n                if entry.kind != EntryKind::File {",
        "                if false && parsed.tier != self.config.name {\n                    notes.push(TierNote {\n                        name: entry.name.clone(),\n                        reason: \"names another tier\".to_string(),\n                    });\n                    continue;\n                }\n                if entry.kind != EntryKind::File {",
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
        "            if span_end_ns.saturating_add(keep_ns) > now_ns {\n                break;\n            }\n            self.remove_quarantined(&mut evicted)?;",
        "            if true || span_end_ns.saturating_add(keep_ns) > now_ns {\n                break;\n            }\n            self.remove_quarantined(&mut evicted)?;",
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


def run_rust(package: str) -> tuple[str, list[str]]:
    environment = os.environ.copy()
    host_path, host_source, cockpit_path = host_namespace(ROOT)
    command = [
        "docker",
        "run",
        "--rm",
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
    run = subprocess.run(command, capture_output=True, text=True, check=False)
    log = run.stdout + run.stderr
    failing = _failing_rust_tests(log)
    if "could not compile" in log and not failing:
        # A mutation that does not type-check is **not** a kill: it proves nothing about the
        # tests, so it is reported separately and counted as unanswered.
        return "not-built", ["does not compile"]
    if "test result: FAILED" in log or failing:
        return "killed", failing
    return "survived", []


def run_python() -> tuple[bool, list[str]]:
    run = subprocess.run(
        [sys.executable, "-I", "-m", "unittest", "discover", "-s", "python/tests"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    log = run.stdout + run.stderr
    failing = sorted(
        line.strip() for line in log.splitlines() if line.startswith(("FAIL:", "ERROR:"))
    )
    return run.returncode != 0, failing


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
    args = parser.parse_args()
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
    survivors = 0
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
            if mutant.package == "python":
                killed, failing = run_python()
                verdict = "killed" if killed else "SURVIVED"
            else:
                outcome, failing = run_rust(mutant.package)
                verdict = {"killed": "killed", "survived": "SURVIVED", "not-built": "NOT-BUILT"}[
                    outcome
                ]
        finally:
            # The restore happens even when the measurement raises: a crashed campaign must
            # never leave a mutation in the tree.
            path.write_text(original, encoding="utf-8")
        after = digest(path)
        assert before == after, f"restore mismatch in {mutant.path}"
        if verdict != "killed":
            survivors += 1
        print(
            f"{verdict}\t{mutant.finding}\t{mutant.label}\t"
            + ("; ".join(failing[:3]) if failing else "-")
        )
        sys.stdout.flush()
    print(f"\n{len(MUTANTS)} patches, {survivors} survived; every restore verified by SHA-256")
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
