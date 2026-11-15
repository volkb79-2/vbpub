#!/usr/bin/env python3
"""Replayable hand-mutation campaign for M1 part 3 (`store`) — `HOB-12`.

Rust R2 (`cargo-mutants`, R-3b) is still not available in the lane image, so the guards this
package adds are checked by hand mutation: one stored patch at a time, applied, measured,
restored, and the restored file's SHA-256 compared with the original. A `SURVIVED` row is a
test gap and must be answered with a test before the campaign is reported as all-killed; a
`NOT APPLIED` row means the stored fragment no longer matches the source (a stale patch, not
a survivor), and `--dry-run` lists every patch so it can be spotted by inspection.

    python3 -I docs/hand-mutants/m1c-mutants.py            # the full campaign
    python3 -I docs/hand-mutants/m1c-mutants.py --dry-run  # the patches only

The nine failures the brief names, each with the test that kills it:

| # | patch | the test that must go red |
|---|---|---|
| M1c-1 | cap check off by one (`>` becomes `>=`) | `a_tier_exactly_at_its_byte_cap_keeps_every_segment` |
| M1c-2 | `keep_s` ignored (the expiry test always false) | `keep_s_evicts_a_segment_whose_whole_span_is_older_than_the_window` |
| M1c-3 | evict newest instead of oldest (two patches: the ring's order, then its tail) | `eviction_takes_the_oldest_segment_and_the_ring_is_ordered_by_start_not_by_arrival`, `a_byte_cap_evicts_the_oldest_whole_segment_first_and_holds_strictly` |
| M1c-4 | torn tail accepted (no truncation, no report), in the tier and in the reader | `a_restart_repairs_a_torn_tail_before_appending_and_counts_it`, `a_torn_tail_is_reported_and_truncated_to_the_last_good_record` |
| M1c-5 | seal without the rename (copy straight onto the final name) | `sealing_copies_the_open_segment_appends_the_footer_and_removes_the_open_name` |
| M1c-6 | a live incarnation retired | `retirement_removes_only_ids_no_segment_still_names` |
| M1c-7 | a dropped job not counted | `the_queue_drops_the_oldest_job_first_and_counts_every_drop` |
| M1c-8 | a foreign file deleted, and a foreign corpus adopted (two patches) | `a_foreign_file_a_link_and_a_directory_are_left_alone_and_reported`, `a_segment_of_another_corpus_in_this_directory_is_reported_and_never_touched` |
| M1c-9 | a symlink followed | `a_link_is_never_read_written_truncated_or_removed` |

The verdicts — including the one patch that survived the first pass (the ring's order) and the
test that killed it — are recorded in [`../HAND-MUTANTS-M1C.md`](../HAND-MUTANTS-M1C.md).

Python-side patches (the independent reference decoder) are included because the segment
format's correctness is shared: a reference that accepts a torn or corrupt file would certify
a segment the Rust reader refuses.
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
    package: str  # "store" or "python"


MUTANTS: tuple[Mutant, ...] = (
    # ------------------------------------------- the ring and its two bounds
    Mutant(
        "M1c-1",
        "the byte cap is inclusive (a tier exactly at its cap evicts)",
        "crates/store/src/tier.rs",
        "            while self.bytes > self.config.cap_bytes {",
        "            while self.bytes >= self.config.cap_bytes {",
        "store",
    ),
    Mutant(
        "M1c-2",
        "`keep_s` is ignored: no segment ever expires",
        "crates/store/src/tier.rs",
        "            let expired = span_end_ns.saturating_add(keep_ns) <= now_ns;",
        "            let expired = false && (span_end_ns.saturating_add(keep_ns) <= now_ns);",
        "store",
    ),
    Mutant(
        "M1c-3",
        "the ring is ordered newest first, so eviction takes the newest segment",
        "crates/store/src/tier.rs",
        "        segments.sort_by(|left, right| {\n            (left.parsed.start_s, left.parsed.seq).cmp(&(right.parsed.start_s, right.parsed.seq))\n        });",
        "        segments.sort_by(|left, right| {\n            (right.parsed.start_s, right.parsed.seq).cmp(&(left.parsed.start_s, left.parsed.seq))\n        });",
        "store",
    ),
    Mutant(
        "M1c-3",
        "the cap evicts the newest segment (`.last()` for `.first()`)",
        "crates/store/src/tier.rs",
        "                let Some(name) = self\n                    .scan\n                    .segments\n                    .first()",
        "                let Some(name) = self\n                    .scan\n                    .segments\n                    .last()",
        "store",
    ),
    # ---------------------------------------------------------- crash safety
    Mutant(
        "M1c-4",
        "a torn (incomplete) tail is accepted instead of truncated and reported",
        "crates/store/src/tier.rs",
        "                TailState::Incomplete { .. } => {\n                    scan.truncate_to_good_end(&self.dir)?;\n                    torn_repaired.push(entry.name.clone());",
        "                TailState::Incomplete { .. } => {\n                    let _ = &scan;\n                    let _: Vec<String> = Vec::new();",
        "store",
    ),
    Mutant(
        "M1c-4",
        "the reader treats an incomplete record as a complete one",
        "crates/store/src/segment.rs",
        "                if record_len > remaining {\n                    tail = TailState::Incomplete { offset };\n                    break;\n                }",
        "                if false {\n                    tail = TailState::Incomplete { offset };\n                    break;\n                }",
        "store",
    ),
    Mutant(
        "M1c-5",
        "the seal copies onto the final name, with no temp name and no rename",
        "crates/store/src/fsio.rs",
        "            .open(&temp_path)\n            .map_err(|error| StoreError::io(\"create\", temp, &error))?;",
        "            .open(&sealed_path)\n            .map_err(|error| StoreError::io(\"create\", sealed, &error))?;",
        "store",
    ),
    Mutant(
        "M1c-9",
        "a symbolic link is read instead of refused",
        "crates/store/src/fsio.rs",
        "        if meta.file_type().is_symlink() {\n            return Err(StoreError::Symlink {\n                name: name.to_string(),\n            });\n        }\n        if !meta.is_file() {",
        "        if false {\n            return Err(StoreError::Symlink {\n                name: name.to_string(),\n            });\n        }\n        if !meta.is_file() {",
        "store",
    ),
    # -------------------------------------------------- dictionary and queue
    Mutant(
        "M1c-6",
        "retirement ignores reachability (a live incarnation retires)",
        "crates/store/src/dictionary.rs",
        "        for inc in self.entries.keys().copied() {\n            if !reach(inc) {\n                doomed.push(inc);\n            }\n        }",
        "        for inc in self.entries.keys().copied() {\n            let _ = reach(inc);\n            doomed.push(inc);\n        }",
        "store",
    ),
    Mutant(
        "M1c-7",
        "a dropped job is removed without being counted",
        "crates/store/src/queue.rs",
        "            self.dropped = self.dropped.saturating_add(1);\n            self.dropped_records = self.dropped_records.saturating_add(1);",
        "            let _unused_dropped = 1;",
        "store",
    ),
    # ---------------------------------------------------- foreign and immutables
    Mutant(
        "M1c-8",
        "a name that is not this store's own segment is deleted instead of reported",
        "crates/store/src/tier.rs",
        "                Err(_) => {\n                    notes.push(TierNote {\n                        name: entry.name.clone(),\n                        reason: \"not a segment name this store writes\".to_string(),\n                    });\n                    continue;\n                }",
        "                Err(_) => {\n                    let _ = self.dir.remove(&entry.name);\n                    continue;\n                }",
        "store",
    ),
    Mutant(
        "M1c-8",
        "a segment whose header names another tier kind is adopted rather than reported",
        "crates/store/src/tier.rs",
        "            if header.kind != self.config.kind {",
        "            if false {",
        "store",
    ),
    # ------------------------------------------ the independent Python reference
    Mutant(
        "M1c-4",
        "Python: the reference decoder reports an incomplete tail as complete",
        "python/reference/hostobs_segment_ref.py",
        "            if error.refusal in (\"truncated\", \"payload-length\"):\n                tail = {\"state\": \"incomplete\", \"offset\": position}\n                break",
        "            if error.refusal in (\"truncated\", \"payload-length\"):\n                tail = {\"state\": \"complete\"}\n                break",
        "python",
    ),
    Mutant(
        "M1c-9",
        "Python: a record body that does not match its checksum is accepted",
        "python/reference/hostobs_segment_ref.py",
        "    if measured_body != body_crc:\n        raise SegmentError(\"record-crc\", index=index, declared=body_crc, computed=measured_body)",
        "    if False:\n        raise SegmentError(\"record-crc\", index=index, declared=body_crc, computed=measured_body)",
        "python",
    ),
    Mutant(
        "M1c-6",
        "Python: the footer's record count is not compared with the records",
        "python/reference/hostobs_segment_ref.py",
        "    if count != measured_count:\n        raise SegmentError(\"footer-count\", declared=count, measured=measured_count)",
        "    if False:\n        raise SegmentError(\"footer-count\", declared=count, measured=measured_count)",
        "python",
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
    parser.add_argument("--dry-run", action="store_true", help="print the patches and exit")
    args = parser.parse_args()
    if args.dry_run:
        for mutant in MUTANTS:
            print(f"{mutant.finding}\t{mutant.label}\t{mutant.path}")
        print(f"{len(MUTANTS)} stored patches")
        return 0
    if "CGROUP_PARENT_DEV_GATES" not in os.environ:
        print(
            "CGROUP_PARENT_DEV_GATES is not set: refusing to place lane containers unconfined",
            file=sys.stderr,
        )
        return 2
    survivors = 0
    for mutant in MUTANTS:
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
