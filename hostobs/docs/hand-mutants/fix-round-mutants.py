#!/usr/bin/env python3
"""Replayable hand-mutation campaign for the M1 fix round (HOB-12).

Rust R2 (`cargo-mutants`, R-3b) is not available in the lane image, so the fix round's
guards are checked by hand mutation. Unlike the M1 campaign in
`docs/HAND-MUTANTS-M1.md` (which records labels and verdicts as prose), every mutation
here is a stored patch that this script applies, measures, and restores:

    python3 docs/hand-mutants/fix-round-mutants.py            # full campaign
    python3 docs/hand-mutants/fix-round-mutants.py --dry-run   # patches only, no runs

Each step: record the file's SHA-256, replace one exact fragment, run the affected suite,
classify the outcome, restore the file, and assert the restored digest equals the original.
A `SURVIVED` row is a test gap and must be answered with a test before the campaign is
reported as all-killed.

Rust steps run in the lane image (`tester-unified-rust:local`, `--network none`, placed on
the gates cgroup tier); Python steps run the reference suite with `python3 -I`. Nothing
here is a release R2 pass: it is evidence about test quality, and the real mechanism stays
`cargo-mutants` once R-3b lands.
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
    language: str


MUTANTS: tuple[Mutant, ...] = (
    Mutant(
        "B1",
        "duplicate check only refuses a later index",
        "crates/proto/src/dict.rs",
        "            if interned != position {",
        "            if interned > position {",
        "rust",
    ),
    Mutant(
        "B1",
        "the duplicate comparison disabled entirely",
        "crates/proto/src/dict.rs",
        "            if interned != position {",
        "            if false {",
        "rust",
    ),
    Mutant(
        "B2",
        "only extra mask bits refused (a subset is accepted again)",
        "crates/proto/src/dict.rs",
        "                    if mask != expected_mask {",
        "                    if mask & !expected_mask != 0 {",
        "rust",
    ),
    Mutant(
        "R1",
        "row entry-kind check removed",
        "crates/proto/src/frame.rs",
        "                _ => return Err(ProtoError::RowReferenceNotEntity { index: row.ent }),",
        "                _ => {}",
        "rust",
    ),
    Mutant(
        "R1",
        "column-kind agreement check disabled",
        "crates/proto/src/frame.rs",
        "                    if column.kind != facts.kind {",
        "                    if false {",
        "rust",
    ),
    Mutant(
        "R1",
        "column entry-kind check removed",
        "crates/proto/src/frame.rs",
        "                _ => {\n                    return Err(ProtoError::ColumnReferenceNotMetric {\n                        index: column.metric,\n                    })\n                }",
        "                _ => {}",
        "rust",
    ),
    Mutant(
        "R2",
        "rowless block accepted when n_rows == 1",
        "crates/proto/src/frame.rs",
        "    if n_rows > 0 && block.rows.is_empty() && block.time.is_empty() {",
        "    if n_rows > 1 && block.rows.is_empty() && block.time.is_empty() {",
        "rust",
    ),
    Mutant(
        "R2",
        "decoder rowless bound weakened to n_rows > 1",
        "crates/proto/src/frame.rs",
        "    if n_rows > 0 && !saw_rows && !saw_time {",
        "    if n_rows > 1 && !saw_rows && !saw_time {",
        "rust",
    ),
    Mutant(
        "R2",
        "exclusivity by count instead of by presence",
        "crates/proto/src/frame.rs",
        "    if saw_rows && saw_time {",
        "    if !rows.is_empty() && !time.is_empty() {",
        "rust",
    ),
    Mutant(
        "hygiene",
        "row index need computed unchecked (usize::MAX overflows)",
        "crates/proto/src/frame.rs",
        "        let need = row.checked_add(1).ok_or(ProtoError::LengthOverflow)?;",
        "        let need = row + 1;",
        "rust",
    ),
    Mutant(
        "B1",
        "Python duplicate-identity check disabled",
        "python/reference/hostobs_wire_ref.py",
        "        if identity in seen_identities:",
        "        if False:",
        "python",
    ),
    Mutant(
        "B2",
        "Python mask check only refuses extra bits",
        "python/reference/hostobs_wire_ref.py",
        '            if mask != AGGREGATION_MASKS[COLUMN_KINDS_BY_CODE[kind]]:',
        '            if mask & ~AGGREGATION_MASKS[COLUMN_KINDS_BY_CODE[kind]]:',
        "python",
    ),
    Mutant(
        "R1",
        "Python row entry-kind check removed",
        "python/reference/hostobs_wire_ref.py",
        '            if entries[row["ent"]]["kind"] != "entity":',
        "            if False:",
        "python",
    ),
    Mutant(
        "R1",
        "Python column-kind agreement check removed",
        "python/reference/hostobs_wire_ref.py",
        '            if facts["kind"] != column["kind"]:',
        "            if False:",
        "python",
    ),
    Mutant(
        "R2",
        "Python exclusivity by count instead of by presence",
        "python/reference/hostobs_wire_ref.py",
        "    if saw_rows and saw_time:",
        '    if frame["rows"] and frame["time"]:',
        "python",
    ),
    Mutant(
        "R2",
        "Python rowless bound removed",
        "python/reference/hostobs_wire_ref.py",
        "    if n_rows > 0 and not saw_rows and not saw_time:",
        "    if False:",
        "python",
    ),
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def host_mount_for(path: Path) -> tuple[Path, Path]:
    """The (host source, target) mount of this running cockpit that contains `path`.

    The lane container sees the checkout under the HOST namespace, and a git worktree
    records the path of whichever namespace created it, so a single mount is not enough
    (the estate's dual-mount rule). The host path is READ from the cockpit's own bind
    mounts rather than invented or taken from a default: a wrong mount would run the suite
    against a different tree and report a mutation as surviving.
    """
    container = os.environ.get("HOSTNAME", "")
    if not container:
        raise SystemExit("HOSTNAME is not set: cannot read this container's mounts")
    run = subprocess.run(
        ["docker", "inspect", "--format", "{{range .Mounts}}{{.Source}}|{{.Destination}}\n{{end}}", container],
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
    """`path` as the lane container sees it, its host source, and the container target.

    A git worktree's `.git` gitfile records the path of whichever namespace created it, so
    the checkout is BOTH mounts in the lane (the estate's dual-mount rule): the host path
    from docker inspect, and the cockpit path it is visible at here.
    """
    source, destination = host_mount_for(path)
    host_path = source / path.relative_to(destination)
    return host_path, source, destination


def run_rust() -> tuple[bool, list[str]]:
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
        "export CARGO_TARGET_DIR=/tmp/t; tools/cargo-offline.sh test -p hostobs-proto --offline --locked",
    ]

    run = subprocess.run(command, capture_output=True, text=True, check=False)
    log = run.stdout + run.stderr
    if "test result: FAILED" in log:
        killed = True
    elif "could not compile" in log:
        killed = True
    else:
        killed = False
    failing = sorted({line.strip() for line in log.splitlines() if line.startswith("    ") and "::" in line})
    return killed, failing


def run_python() -> tuple[bool, list[str]]:
    run = subprocess.run(
        [sys.executable, "-I", "-m", "unittest", "discover", "-s", "python/tests"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    log = run.stdout + run.stderr
    failing = sorted(line.strip() for line in log.splitlines() if line.startswith(("FAIL:", "ERROR:")))
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
        print("CGROUP_PARENT_DEV_GATES is not set: refusing to place lane containers unconfined", file=sys.stderr)
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
        path.write_text(original.replace(mutant.old, mutant.new, 1), encoding="utf-8")
        killed, failing = run_rust() if mutant.language == "rust" else run_python()
        path.write_text(original, encoding="utf-8")
        after = digest(path)
        assert before == after, f"restore mismatch in {mutant.path}"
        verdict = "killed" if killed else "SURVIVED"
        if not killed:
            survivors += 1
        print(f"{verdict}\t{mutant.finding}\t{mutant.label}\t{failing[0] if failing else '-'}")
    print(f"\n{len(MUTANTS)} patches, {survivors} survived; every restore verified by SHA-256")
    return 1 if survivors else 0


if __name__ == "__main__":
    raise SystemExit(main())
