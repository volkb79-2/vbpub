#!/usr/bin/env python3
"""The project's small lint boundary, mirroring hostobs/tools/check-lints.py in shape.

Three checks that a compiler cannot make, all cheap and all able to go red:

1. **No hard-coded host path.** Design §5/D9: every unit test takes an injected
   cgroup root and pressure path, so `/sys/fs/cgroup` may appear only in a
   documented default that the CLI or a test injects over -- never as a literal
   in a code path that acts on it. The scan therefore refuses the literal in
   `src/` outside the one declared default in `config.rs`, and fails the run if
   that allowance ever moves.
2. **No act path.** The `observe` cut compiles none: no write to a cgroup file,
   no `fs::write`/`File::create`/`OpenOptions` toward cgroupfs, and no signal
   or kill call. `src/` may only open files for reading.
3. **One audited `unsafe` item.** `activation.rs` claims a systemd-owned
   descriptor, which `std` can only do through `unsafe`. The crate denies
   `unsafe_code`; exactly one function may carry the allowance, and no other
   file may.

Usage: check-lints.py PROJECT-DIR
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CGROUP_LITERAL = '"/sys/fs/cgroup"'
# The one place the literal is allowed: config.rs's documented default, which
# every test overrides through `--cgroup-root`. Keep the allowance and the
# replacement in step: this list is asserted, not assumed.
CGROUP_LITERAL_ALLOWED = {"src/config.rs": 1}
UNSAFE_ALLOWED = {"src/activation.rs": 1}

WRITE_CALLS = re.compile(r"(fs::write|File::create|OpenOptions::new|fs::remove_|fs::rename|fs::set_permissions)")
SIGNAL_CALLS = re.compile(r"(libc::kill|nix::sys::signal|Command::new\(\"kill\"\)|\.kill\(\)|\bkill\()")


def source_files(project: Path) -> list[Path]:
    return sorted((project / "src").rglob("*.rs"))


def relative(project: Path, path: Path) -> str:
    return path.relative_to(project).as_posix()


def find(project: Path) -> list[str]:
    """Return every violation, one `path:line: reason` each (empty means clean)."""
    problems: list[str] = []
    seen_literal = 0
    seen_unsafe = 0
    for path in source_files(project):
        name = relative(project, path)
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("//"):
                continue
            if CGROUP_LITERAL in line:
                seen_literal += 1
                if name not in CGROUP_LITERAL_ALLOWED:
                    problems.append(
                        f"{name}:{number}: the host cgroup root is a literal here; take it from the injected root"
                    )
            if re.search(r"allow\(unsafe_code\)", line):
                seen_unsafe += 1
                if name not in UNSAFE_ALLOWED:
                    problems.append(f"{name}:{number}: unsafe_code may be allowed only in {sorted(UNSAFE_ALLOWED)}")
            if WRITE_CALLS.search(line):
                problems.append(
                    f"{name}:{number}: a file is written here; the observe cut compiles no act path and no cgroup write"
                )
            if SIGNAL_CALLS.search(line):
                problems.append(f"{name}:{number}: a signal is sent here; thrashguard never signals a unit")
    for name, allowed in CGROUP_LITERAL_ALLOWED.items():
        actual = sum(
            1
            for path in source_files(project)
            if relative(project, path) == name
            for line in path.read_text(encoding="utf-8").splitlines()
            if CGROUP_LITERAL in line and not line.strip().startswith("//")
        )
        if actual != allowed:
            problems.append(
                f"{name}: the declared cgroup-root literal allowance is {allowed} but the file has {actual}"
            )
    if seen_literal != sum(CGROUP_LITERAL_ALLOWED.values()):
        problems.append(
            "the cgroup-root literal allowance and the tree disagree; adjust CGROUP_LITERAL_ALLOWED deliberately"
        )
    if seen_unsafe != sum(UNSAFE_ALLOWED.values()):
        problems.append(
            "the unsafe allowance and the tree disagree; an unaudited unsafe item is not acceptable"
        )
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
