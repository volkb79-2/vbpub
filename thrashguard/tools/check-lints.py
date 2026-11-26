#!/usr/bin/env python3
"""The project's small lint boundary: the promises a compiler cannot check.

Three checks, all cheap and all able to go red:

1. **The host cgroup root is not a literal in the code.** Design §5/D9: every
   test takes an injected cgroup root (`--cgroup-root`) and pressure path
   (`--pressure-path`). Exactly ONE file may spell the real default (the CLI's
   flag default, the place a run resolves it), and the count is asserted, so the
   allowance cannot silently grow.
2. **No act path.** A lexical scan strips comments without stripping `//` inside
   strings, then rejects known cgroup control names, signal/process entry points
   and file-writing APIs outside the journal. `src/testutil.rs` is exempt from
   write checks only when both crate entry points gate it with `#[cfg(test)]`.
   This is not a Rust parser or proof: aliases, macro expansion, generated code,
   indirect APIs, FFI and unlisted spellings can evade these patterns.
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
# CLI flag default. `{worktree}` resolution and the injected `--cgroup-root` live
# in `src/cli.rs`, so exactly one literal is allowed there and the count is
# asserted: a second spelling anywhere else fails this check.
CGROUP_LITERAL_ALLOWED: dict[str, int] = {"src/cli.rs": 1}
# The ONLY release source file allowed to write anything at all.
WRITERS = {"src/events.rs"}
# The ONLY file allowed an `unsafe` block.
UNSAFE_FILE = "src/activation.rs"

FORBIDDEN_TARGETS = ("cgroup.freeze", "cgroup.events", "memory.reclaim", "memory.max")
FORBIDDEN_CALLS = (
    "libc::kill",
    "nix::sys::signal",
    "Command::new(",
    "process::Command",
    "std::process::Command",
    "process::abort",
    "std::process::abort",
)
WRITE_CALLS = re.compile(
    r"(fs::write|File::create|File::options|OpenOptions::new|fs::remove_file|fs::remove_dir|fs::rename)"
)
UNSAFE_ALLOW = re.compile(r"allow\(unsafe_code\)")
UNSAFE_BLOCK = re.compile(r"unsafe\s*{")


def source_files(project: Path) -> list[Path]:
    return sorted((project / "src").rglob("*.rs"))


def relative(project: Path, path: Path) -> str:
    return path.relative_to(project).as_posix()


def code_without_comments(source: str) -> str:
    """Strip Rust comments without treating markers inside strings as comments.

    This handles lexical forms needed by the guard, not the whole Rust grammar.
    """
    chars = list(source)
    index = 0
    in_string = False
    escaped = False
    raw_hashes: int | None = None
    block_depth = 0
    while index < len(chars):
        if raw_hashes is not None:
            if chars[index] == '"':
                end = index + 1
                while end < len(chars) and chars[end] == "#":
                    end += 1
                if end - (index + 1) == raw_hashes:
                    raw_hashes = None
                    index = end
                    continue
            index += 1
            continue

        if in_string:
            if escaped:
                escaped = False
            elif chars[index] == "\\":
                escaped = True
            elif chars[index] == '"':
                in_string = False
            index += 1
            continue

        if block_depth:
            if index + 1 < len(chars) and chars[index : index + 2] == ["/", "*"]:
                chars[index : index + 2] = [" ", " "]
                block_depth += 1
                index += 2
            elif index + 1 < len(chars) and chars[index : index + 2] == ["*", "/"]:
                chars[index : index + 2] = [" ", " "]
                block_depth -= 1
                index += 2
            else:
                if chars[index] not in "\r\n":
                    chars[index] = " "
                index += 1
            continue

        raw_prefix = index if chars[index] == "r" else (
            index + 1
            if chars[index] == "b" and index + 1 < len(chars) and chars[index + 1] == "r"
            else None
        )
        if raw_prefix is not None:
            quote = raw_prefix + 1
            while quote < len(chars) and chars[quote] == "#":
                quote += 1
            if quote < len(chars) and chars[quote] == '"':
                raw_hashes = quote - (raw_prefix + 1)
                index = quote + 1
                continue

        if chars[index] == '"':
            in_string = True
            index += 1
        elif index + 1 < len(chars) and chars[index : index + 2] == ["/", "/"]:
            while index < len(chars) and chars[index] != "\n":
                chars[index] = " "
                index += 1
        elif index + 1 < len(chars) and chars[index : index + 2] == ["/", "*"]:
            chars[index : index + 2] = [" ", " "]
            block_depth = 1
            index += 2
        else:
            index += 1
    return "".join(chars)


def scanner_self_check() -> list[str]:
    """Keep the comment handling and newly added lexical patterns observable."""
    sample = (
        'let url = "scheme://cgroup.freeze"; // cgroup.events\n'
        'let raw = r#"https://memory.reclaim"#; /* memory.max */\n'
    )
    code = code_without_comments(sample)
    problems = []
    if "scheme://cgroup.freeze" not in code or "https://memory.reclaim" not in code:
        problems.append("check-lints.py: comment stripping lost a marker inside a string")
    if "cgroup.events" in code or "memory.max" in code:
        problems.append("check-lints.py: comment stripping retained comment text")
    if "Command::new(" not in FORBIDDEN_CALLS:
        problems.append("check-lints.py: process-spawn pattern is missing")
    if not WRITE_CALLS.search("File::options()"):
        problems.append("check-lints.py: File::options writer pattern is missing")
    return problems


def find(project: Path) -> list[str]:
    """Return every violation, one `path:line: reason` each (empty means clean)."""
    problems: list[str] = scanner_self_check()
    literal_count = 0
    unsafe_allows: list[tuple[str, int]] = []
    unsafe_blocks = 0
    for path in source_files(project):
        name = relative(project, path)
        code_text = code_without_comments(path.read_text(encoding="utf-8"))
        for number, code in enumerate(code_text.splitlines(), 1):
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
            for call in FORBIDDEN_CALLS:
                if call in code:
                    problems.append(
                        f"{name}:{number}: {call} is a process or signal entry point forbidden in P1"
                    )
            if WRITE_CALLS.search(code) and name not in WRITERS and name != "src/testutil.rs":
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

    # The fixture helper is exempt only if both crate roots compile it under
    # cfg(test); it is not part of the release library or binary.
    lib = project / "src" / "lib.rs"
    main = project / "src" / "main.rs"
    if lib.is_file() and not re.search(
        r"#\[cfg\(test\)\]\s*#\[doc\(hidden\)\]\s*pub\s+mod\s+testutil\s*;",
        code_without_comments(lib.read_text(encoding="utf-8")),
    ):
        problems.append("src/lib.rs: the testutil module must be gated with #[cfg(test)]")
    if main.is_file() and not re.search(
        r"#\[cfg\(test\)\]\s*#\[path\s*=\s*\"testutil\.rs\"\]\s*mod\s+testutil\s*;",
        code_without_comments(main.read_text(encoding="utf-8")),
    ):
        problems.append("src/main.rs: the testutil module must be included only under #[cfg(test)]")
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
