#!/usr/bin/env python3
"""The project's small lint boundary: the promises a compiler cannot check.

Three checks, all cheap and all able to go red:

1. **The host cgroup root is not a literal in the code.** Design §5/D9: every
   test takes an injected cgroup root (`--cgroup-root`) and pressure path
   (`--pressure-path`). Exactly ONE file may spell the real default (the CLI's
   flag default, the place a run resolves it), and the count is asserted, so the
   allowance cannot silently grow.
2. **No act path.** Cgroup control files are named only in prose: an executable
   (non-comment) line mentioning `cgroup.freeze`, `cgroup.events`,
   `memory.reclaim` or `memory.max` fails, and a file-write API
   (`fs::write`, `File::create`, `OpenOptions::new`, `fs::remove_*`) is allowed
   only in the journal writer (`src/events.rs`) and the test-only fixture helper
   (`src/testutil.rs`, which `lib.rs` must gate with `#[cfg(test)]`). A write to a
   cgroup file therefore has no way to exist.
3. **One audited `unsafe` item, at most.** `activation.rs` claims a
   systemd-owned descriptor, which `std` can only do through `unsafe`. The crate
   denies `unsafe_code`, so the number of `#[allow(unsafe_code)]` attributes must
   equal the number of `unsafe {` blocks, all in that one file.

Usage: check-lints.py PROJECT-DIR
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CGROUP_LITERAL = '"/sys/fs/cgroup"'
# The ONLY file allowed to spell the real cgroup root, and how many times: the
# CLI flag default. Empty today because no module has landed yet that needs it;
# P1 subtask 4 (`cli.rs`) sets {"src/cli.rs": 1} in the same commit that adds the
# flag, so the allowance never describes a file that does not exist.
CGROUP_LITERAL_ALLOWED: dict[str, int] = {}
# The ONLY files allowed to write anything at all.
WRITERS = {"src/events.rs", "src/testutil.rs"}
# The ONLY file allowed an `unsafe` block.
UNSAFE_FILE = "src/activation.rs"

FORBIDDEN_TARGETS = ("cgroup.freeze", "cgroup.events", "memory.reclaim", "memory.max")
WRITE_CALLS = re.compile(r"(fs::write|File::create|OpenOptions::new|fs::remove_file|fs::remove_dir)")
UNSAFE_ALLOW = re.compile(r"allow\(unsafe_code\)")
UNSAFE_BLOCK = re.compile(r"unsafe\s*{")


def source_files(project: Path) -> list[Path]:
    return sorted((project / "src").rglob("*.rs"))


def relative(project: Path, path: Path) -> str:
    return path.relative_to(project).as_posix()


def find(project: Path) -> list[str]:
    """Return every violation, one `path:line: reason` each (empty means clean)."""
    problems: list[str] = []
    literal_count = 0
    unsafe_allows: list[tuple[str, int]] = []
    unsafe_blocks = 0
    for path in source_files(project):
        name = relative(project, path)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("//", 1)[0]
            if CGROUP_LITERAL in code:
                literal_count += 1
                if name not in CGROUP_LITERAL_ALLOWED:
                    problems.append(
                        f"{name}:{number}: the host cgroup root is a literal here; it comes from --cgroup-root"
                    )
            for target in FORBIDDEN_TARGETS:
                if target in code:
                    problems.append(
                        f"{name}:{number}: {target} is named in executable code; the observe cut compiles no act path"
                    )
            if WRITE_CALLS.search(code) and name not in WRITERS:
                problems.append(
                    f"{name}:{number}: a file is written here; only {sorted(WRITERS)} may write"
                )
            if UNSAFE_ALLOW.search(code):
                unsafe_allows.append((name, number))
                if name != UNSAFE_FILE:
                    problems.append(f"{name}:{number}: unsafe_code may be allowed only in {UNSAFE_FILE}")
            unsafe_blocks += len(UNSAFE_BLOCK.findall(code))

    for allowed_name, allowed_count in CGROUP_LITERAL_ALLOWED.items():
        if (project / allowed_name).is_file():
            continue
        problems.append(
            f"{allowed_name}: declared a cgroup-root literal allowance of {allowed_count} "
            "but the file does not exist"
        )
    expected = sum(CGROUP_LITERAL_ALLOWED.values())
    if literal_count != expected:
        problems.append(
            f"the cgroup-root literal allowance is {expected} but the tree has {literal_count}; "
            "change CGROUP_LITERAL_ALLOWED deliberately"
        )
    if len(unsafe_allows) != unsafe_blocks:
        problems.append(
            f"{len(unsafe_allows)} unsafe_code allowance(s) for {unsafe_blocks} unsafe block(s); "
            "every unsafe block needs exactly one audited allowance"
        )

    # The test-only fixture helper must stay test-only: `lib.rs` gates its module.
    lib = project / "src" / "lib.rs"
    if lib.is_file():
        text = lib.read_text(encoding="utf-8")
        if "pub mod testutil;" in text and "#[cfg(test)]\npub mod testutil;" not in text:
            problems.append("src/lib.rs: pub mod testutil; is not gated by #[cfg(test)]")
    return problems


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: check-lints.py PROJECT-DIR", file=sys.stderr)
        return 2
    project = Path(argv[1])
    if not (project / "src").is_dir():
        print(f"ERROR: {project}/src does not exist", file=sys.stderr)
        return 2
    problems = find(project)
    if problems:
        for problem in problems:
            print(f"ERROR: {problem}", file=sys.stderr)
        return 1
    print(f"lint boundary: clean ({len(source_files(project))} source file(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
