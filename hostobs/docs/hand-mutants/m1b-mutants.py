#!/usr/bin/env python3
"""Replayable hand-mutation campaign for M1 part 2 (`HOB-12`, recorded in
`docs/HAND-MUTANTS-M1B.md`).

Rust R2 (`cargo-mutants`, R-3b) is still not available in the lane image, so the guards this
package adds are checked by hand mutation: one stored patch at a time, applied, measured,
restored, and the restored file's SHA-256 compared with the original. A `SURVIVED` row is a
test gap and must be answered with a test before the campaign is reported as all-killed; a
`NOT APPLIED` row means the stored fragment no longer matches the source (it is a stale
patch, not a survivor, and `--dry-run` lists every patch so it can be spotted by inspection).

    python3 docs/hand-mutants/m1b-mutants.py            # full campaign
    python3 docs/hand-mutants/m1b-mutants.py --dry-run  # print the patches and exit

Rust steps run in the lane image (`tester-unified-rust:local`, `--network none`, placed on the
gates cgroup tier); Python steps run the reference suite with `python3 -I`. Neither is a
release R2 pass: it is evidence about test quality, and the real mechanism stays
`cargo-mutants` once R-3b lands.

Three classes of guard are covered, in the order the package's items are lettered:

* **A** — the three luna cross-model findings (`proto`/Python): a block shape under another
  frame type, a section directory out of canonical order, a truncated directory that used to
  raise `struct.error`.
* **B** — the `kernel` collectors: unknown device topology as a state, `max` limits as a
  state, an unparsable field never a zero, word-boundary truncation, redaction, identity
  zero-field refusals, and the refusal to turn a wall jump into an interval.
* **C** — the SP-2 evidence: an interval across a reboot/epoch boundary refused, a recycled
  inode/pid distinguished.
* **D** — the fix round after the M1b review: the `/proc/<pid>/stat` field mapping (K1, three
  patches: minflt's field, majflt's field, and the offset itself), the iocost overflow rule,
  and the unbounded-read limit.
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
    package: str  # "kernel", "proto" or "python"


MUTANTS: tuple[Mutant, ...] = (
    # ---------------------------------------------------------------- A: the luna findings
    Mutant(
        "A1",
        "decode_block stops requiring FrameType::Block",
        "crates/proto/src/frame.rs",
        "    if header_frame.frame_type != FrameType::Block {\n        return Err(ProtoError::NotABlock {\n            frame_type: header_frame.frame_type.code(),\n        });\n    }",
        "    if false {\n        return Err(ProtoError::NotABlock {\n            frame_type: header_frame.frame_type.code(),\n        });\n    }",
        "proto",
    ),
    Mutant(
        "A2",
        "the decoder's canonical-order check disabled",
        "crates/proto/src/frame.rs",
        "            if rank < previous {\n                return Err(ProtoError::SectionOrder { index, kind: *kind });",
        "            if false {\n                return Err(ProtoError::SectionOrder { index, kind: *kind });",
        "proto",
    ),
    Mutant(
        "A2",
        "canonical_rank puts Dictionary after Rows",
        "crates/proto/src/frame.rs",
        "        SectionKind::Dictionary => 1,",
        "        SectionKind::Dictionary => 3,",
        "proto",
    ),
    Mutant(
        "A1",
        "Python: the decoder's frame-type check removed",
        "python/reference/hostobs_wire_ref.py",
        "    if type_code != FRAME_TYPES[\"block\"]:",
        "    if False:",
        "python",
    ),
    Mutant(
        "A2",
        "Python: the canonical-order check removed",
        "python/reference/hostobs_wire_ref.py",
        "    _check_canonical_order(directory)",
        "    pass",
        "python",
    ),
    Mutant(
        "A3",
        "Python: the directory read unbounded again (struct.error returns)",
        "python/reference/hostobs_wire_ref.py",
        "        if offset + SECTION_DIR_ENTRY_LEN > len(data):",
        "        if False:",
        "python",
    ),
    # ---------------------------------------------------------------- B: the kernel collectors
    Mutant(
        "B5",
        "cgroup: an unknown topology is counted as a bottom device (the sampler's old bug)",
        "crates/kernel/src/cgroup.rs",
        "    if unknown {\n        return Err(KernelError::UnknownTopology);\n    }",
        "    if unknown {\n        // counted as bottom\n    }",
        "kernel",
    ),
    Mutant(
        "B5",
        "cgroup: a stacked device's counters are summed too",
        "crates/kernel/src/cgroup.rs",
        "        let bottom = topology_answer == DeviceTopology::Bottom;",
        "        let bottom = true;",
        "kernel",
    ),
    Mutant(
        "B6",
        "cgroup: `memory.max`'s `max` becomes the number 0",
        "crates/kernel/src/cgroup.rs",
        '    if raw == b"max" {\n        return Ok(Limit {\n            file,\n            value: None,',
        '    if raw == b"max" {\n        return Ok(Limit {\n            file,\n            value: Some(0),',
        "kernel",
    ),
    Mutant(
        "B2",
        "cgroup: an unparsable counter is published as zero instead of unreadable",
        "crates/kernel/src/cgroup.rs",
        '        match parse_u64(value) {\n            Ok(raw) => readings.measured(metric, Value::U64(raw)),\n            Err(_) => readings.missing(metric, QualityCode::UNREADABLE),\n        }\n    }\n    if !saw_entry {',
        '        match parse_u64(value) {\n            Ok(raw) => readings.measured(metric, Value::U64(raw)),\n            Err(_) => readings.measured(metric, Value::U64(0)),\n        }\n    }\n    if !saw_entry {',
        "kernel",
    ),
    Mutant(
        "B2",
        "cgroup: an unparsable io counter still yields a partial total",
        "crates/kernel/src/cgroup.rs",
        "        if bad {\n            readings.missing(*metric, QualityCode::UNREADABLE);\n        } else {",
        "        if false {\n            readings.missing(*metric, QualityCode::UNREADABLE);\n        } else {",
        "kernel",
    ),
    Mutant(
        "B6",
        "cgroup: an absent controller file reports `unsupported`",
        "crates/kernel/src/cgroup.rs",
        "            Absence::Controller => QualityCode::ABSENT,",
        "            Absence::Controller => QualityCode::UNSUPPORTED,",
        "kernel",
    ),
    Mutant(
        "B3",
        "cmdline: a long line is returned whole instead of cut at a word",
        "crates/kernel/src/cmdline.rs",
        "    if content <= max {\n        return bytes;\n    }",
        "    if true {\n        return bytes;\n    }",
        "kernel",
    ),
    Mutant(
        "B3",
        "cmdline: the redactor forgets that the next word is an option's value",
        "crates/kernel/src/cmdline.rs",
        "        let (replacement, next) = redact_word(word);\n        hide_next = next;",
        "        let (replacement, _next) = redact_word(word);\n        hide_next = false;",
        "kernel",
    ),
    Mutant(
        "B3",
        "cmdline: a vanished or empty command line becomes an empty string",
        "crates/kernel/src/cmdline.rs",
        "    if raw.is_empty() {\n        return Err(CmdlineError::Missing);\n    }",
        "    if false {\n        return Err(CmdlineError::Missing);\n    }",
        "kernel",
    ),
    Mutant(
        "B2",
        "procfs: a missing status field is published as zero",
        "crates/kernel/src/procfs.rs",
        "            readings.missing(\n                format!(\"proc.status.count.{field}.0\"),\n                QualityCode::UNSUPPORTED,\n            );",
        "            readings.measured(format!(\"proc.status.count.{field}.0\"), Value::U64(0));",
        "kernel",
    ),
    Mutant(
        "B1",
        "procfs: a pid listing keeps any directory name",
        "crates/kernel/src/procfs.rs",
        "        let Ok(pid) = entry.name.parse::<u32>() else {\n            continue;\n        };",
        "        let pid = entry.name.parse::<u32>().unwrap_or(1);",
        "kernel",
    ),
    Mutant(
        "B4",
        "identity: an upper-case boot id is accepted as the lower-case one",
        "crates/kernel/src/identity.rs",
        "            if character.is_ascii_uppercase() {\n                return Err(IdentityError::BootIdNonHex { index: *index });\n            }",
        "            if false {\n                return Err(IdentityError::BootIdNonHex { index: *index });\n            }",
        "kernel",
    ),
    Mutant(
        "B4",
        "identity: a starttime of 0 is accepted (a bare pid becomes an identity)",
        "crates/kernel/src/identity.rs",
        "        if start_ticks == 0 {\n            return Err(IdentityError::StartTicksZero);\n        }",
        "        if false {\n            return Err(IdentityError::StartTicksZero);\n        }",
        "kernel",
    ),
    Mutant(
        "B4",
        "identity: device 0 is accepted as an identity",
        "crates/kernel/src/identity.rs",
        "        if device == 0 {\n            return Err(IdentityError::DeviceZero);\n        }",
        "        if false {\n            return Err(IdentityError::DeviceZero);\n        }",
        "kernel",
    ),
    Mutant(
        "B2",
        "parse: a negative unsigned field wraps instead of being refused",
        "crates/kernel/src/parse.rs",
        "    if text.starts_with(b\"-\") {\n        return Err(ParseError::Negative);\n    }",
        "    if false {\n        return Err(ParseError::Negative);\n    }",
        "kernel",
    ),
    Mutant(
        "B2",
        "value: a `u64` accessor answers for an unmeasured reading",
        "crates/kernel/src/value.rs",
        "            Value::U64(raw) if self.quality.is_measurement() => Some(raw),",
        "            Value::U64(raw) => Some(raw),",
        "kernel",
    ),
    Mutant(
        "B2",
        "value: `missing(ok)` is accepted",
        "crates/kernel/src/value.rs",
        "        if quality.is_measurement() {\n            return Err(ReadingError::MissingCannotBeOk);\n        }",
        "        if false {\n            return Err(ReadingError::MissingCannotBeOk);\n        }",
        "kernel",
    ),
    Mutant(
        "C2",
        "clock: an interval across a reboot/epoch boundary is subtracted anyway",
        "crates/kernel/src/clock.rs",
        "    if previous.timeline != current.timeline {",
        "    if false {",
        "kernel",
    ),
    Mutant(
        "C2",
        "clock: a wall jump forwards is the exact tolerance test (>= instead of >)",
        "crates/kernel/src/clock.rs",
        "        self.wall_ns - self.mono_ns as i128 > tolerance_ns",
        "        self.wall_ns - self.mono_ns as i128 >= tolerance_ns",
        "kernel",
    ),
    Mutant(
        "C1",
        "SP-2: a recreated cgroup at one path keeps the earlier identity",
        "crates/kernel/tests/sp2_evidence.rs",
        "    fs.directory(\"/cg/demo\", 101);",
        "    fs.directory(\"/cg/demo\", 100);",
        "kernel",
    ),
    # -------------------------------------- D: the fix round (K1 and the smaller notes)
    Mutant(
        "D1",
        "identity: minflt read from field 11 (cminflt) instead of field 10",
        "crates/kernel/src/identity.rs",
        "            10 => minflt = parsed,",
        "            11 => minflt = parsed,",
        "kernel",
    ),
    Mutant(
        "D1",
        "identity: majflt read from field 13 (cmajflt) instead of field 12",
        "crates/kernel/src/identity.rs",
        "            12 => majflt = parsed,",
        "            13 => majflt = parsed,",
        "kernel",
    ),
    Mutant(
        "D1",
        "identity: the proc(5) field offset shifted by one (every field reads its neighbour)",
        "crates/kernel/src/identity.rs",
        "pub const STAT_FIELD_OFFSET: usize = 3;",
        "pub const STAT_FIELD_OFFSET: usize = 2;",
        "kernel",
    ),
    Mutant(
        "D2",
        "io cost sums saturate at u64::MAX instead of refusing an overflow",
        "crates/kernel/src/cgroup.rs",
        "                        Ok(raw) => match cost[index].checked_add(raw) {\n                            Some(total) => cost[index] = total,\n                            None => bad_cost = true,\n                        },",
        "                        Ok(raw) => cost[index] = cost[index].saturating_add(raw),",
        "kernel",
    ),
    Mutant(
        "D3",
        "RealFs::read's `max_bytes + 1` wraps (an unbounded read returns an empty file)",
        "crates/kernel/src/fs.rs",
        "        let limit = u64::try_from(max_bytes)\n            .unwrap_or(u64::MAX)\n            .saturating_add(1);",
        "        let limit = u64::try_from(max_bytes).unwrap_or(u64::MAX) + 1;",
        "kernel",
    ),
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _failing_rust_tests(log: str) -> list[str]:
    """The test names a run reported as failing (nextest and libtest spellings)."""
    names: set[str] = set()
    for line in log.splitlines():
        stripped = line.strip()
        # nextest: "        FAIL [   0.001s] hostobs-kernel::tests::name"
        if stripped.startswith("FAIL ") and "::" in stripped:
            names.add(stripped.split("]")[-1].strip() if "]" in stripped else stripped)
        # libtest: "test module::case ... FAILED"
        if stripped.startswith("test ") and stripped.endswith("FAILED"):
            names.add(stripped[len("test ") : -len(" ... FAILED")].strip())
        # libtest's section header: "---- module::case stdout ----"
        if stripped.startswith("---- ") and stripped.endswith(" stdout ----"):
            names.add(stripped[len("---- ") : -len(" stdout ----")].strip())
    return sorted(names)
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
    """`path` as the lane container sees it, its host source, and the container target."""
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
        # A mutation that does not type-check is **not** a kill (the M1 campaign's rule):
        # it proves nothing about the tests, so it is reported separately and counted as
        # unanswered rather than silently folded into "killed".
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
        line.strip()
        for line in log.splitlines()
        if line.startswith(("FAIL:", "ERROR:"))
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
                verdict = {"killed": "killed", "survived": "SURVIVED", "not-built": "NOT-BUILT"}[outcome]
        finally:
            # The restore happens even when the measurement itself raises: a crashed
            # campaign must never leave a mutation in the tree (an exception once did, and
            # the next run then reported the patch as "NOT APPLIED" — a survivor that was
            # actually a leftover edit).
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
