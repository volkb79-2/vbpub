#!/usr/bin/env python3
"""Prove that the write-boundary lint actually *catches* a write outside `fsio`.

`tools/check-lints.py` states a conclusion: "no Rust source outside the allowed write paths
opens a file for writing, creates a directory or starts a process". This script is the
comparison behind that conclusion: it copies the workspace's Rust tree into a scratch
directory, inserts exactly the writes the lint forbids (and one that it must allow), and asserts
which way each case goes. A lint whose allowlist entry was added without a mutation check is a
message nobody has tested; this one runs in the `lint` lane, so the claim is re-proven on every
gate.

Cases (each asserts a specific outcome, so the check cannot pass by being noisy):

* baseline, unmodified copy — the lint passes;
* `.write(true)` in `crates/store/src/queue.rs` — refused (the store's writes belong to
  `fsio.rs`, not to the whole crate any more);
* `.write(true)` in `crates/store/src/fsio.rs` — allowed (this is the one allowed module);
* `std::fs::create_dir` in `crates/store/src/tier.rs` — refused;
* `Command::new` in `bins/probe/src/main.rs` — refused (no child processes outside `act`);
* an `.rs` file that does not exist in the real tree, planted in `crates/store/src/`,
  with a write — refused: the rule is about *location*, not about a list of known files;
* `#[mutants::skip]` — refused everywhere, including `fsio.rs`.

Usage: `python3 tools/check-lints-mutation.py [WORKSPACE]` (default: this repository).
"""

from __future__ import annotations

import importlib
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
check_lints = importlib.import_module("check-lints")  # the module name carries a hyphen


def copy_rust_tree(source: Path, target: Path) -> None:
    """Copy the workspace manifest, its `.cargo` config, and every `.rs` file with its manifest.

    Only what the lint reads is copied: `Cargo.toml` files, `.cargo/`, and `*.rs`. That keeps
    the scratch tree small and makes the mutation cases unambiguous.
    """
    shutil.copy2(source / "Cargo.toml", target / "Cargo.toml")
    cargo_dir = source / ".cargo"
    if cargo_dir.is_dir():
        shutil.copytree(cargo_dir, target / ".cargo")
    for top in ("crates", "bins"):
        for path in sorted((source / top).rglob("*")):
            if not path.is_file():
                continue
            if path.suffix == ".rs" or path.name == "Cargo.toml":
                destination = target / path.relative_to(source)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)


def expect_ok(root: Path, label: str) -> None:
    try:
        messages = check_lints.check(root)
    except check_lints.LintError as exc:
        raise SystemExit(f"FAIL: {label}: the lint refused a clean tree: {exc}") from exc
    if not messages:
        raise SystemExit(f"FAIL: {label}: the lint reported nothing at all")


def expect_refusal(root: Path, label: str, needle: str) -> None:
    try:
        check_lints.check(root)
    except check_lints.LintError as exc:
        if needle not in str(exc):
            raise SystemExit(f"FAIL: {label}: refused for the wrong reason: {exc}") from exc
        return
    raise SystemExit(f"FAIL: {label}: the lint accepted a forbidden write")


def insert(root: Path, relative: str, needle: str, addition: str) -> None:
    path = root / relative
    if not path.exists():
        raise SystemExit(f"FAIL: {relative} does not exist in the real tree")
    text = path.read_text(encoding="utf-8")
    if needle not in text:
        raise SystemExit(f"FAIL: {relative} no longer contains {needle!r}; update this check")
    path.write_text(text.replace(needle, addition, 1), encoding="utf-8")


def main() -> int:
    source = Path(sys.argv[1]).resolve() if len(sys.argv) == 2 else Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix="hostobs-lint-mutation-") as scratch:
        base = Path(scratch)
        copy_rust_tree(source, base)
        expect_ok(base, "baseline")

        def fresh() -> Path:
            case = base / "case"
            if case.exists():
                shutil.rmtree(case)
            case.mkdir()
            copy_rust_tree(source, case)
            return case

        case = fresh()
        insert(
            case,
            "crates/store/src/queue.rs",
            "use std::collections::VecDeque;",
            "use std::collections::VecDeque;\n// a write added outside fsio\nfn _forbidden() { let _ = std::fs::OpenOptions::new().write(true).open(\"x\"); }\n",
        )
        expect_refusal(case, "write in queue.rs", "queue.rs uses '.write(true)'")

        case = fresh()
        insert(
            case,
            "crates/store/src/tier.rs",
            "use std::collections::BTreeSet;",
            "use std::collections::BTreeSet;\n// a mkdir added outside fsio\nfn _forbidden() { let _ = std::fs::create_dir(\"x\"); }\n",
        )
        expect_refusal(case, "mkdir in tier.rs", "tier.rs uses 'create_dir('")

        case = fresh()
        insert(
            case,
            "bins/probe/src/main.rs",
            "fn main()",
            "fn _forbidden() { let _ = std::process::Command::new(\"/bin/true\"); }\nfn main()",
        )
        expect_refusal(case, "command in bins/probe", "uses 'Command::new'")

        case = fresh()
        planted = case / "crates" / "store" / "src" / "planted.rs"
        planted.write_text(
            "// a write planted in a file the real tree does not have\n"
            "fn _forbidden() { let _ = std::fs::OpenOptions::new().write(true).open(\"x\"); }\n",
            encoding="utf-8",
        )
        expect_refusal(case, "write in a planted file", "planted.rs uses '.write(true)'")

        case = fresh()
        insert(
            case,
            "crates/store/src/fsio.rs",
            "use std::fs::{self, File, OpenOptions};",
            "use std::fs::{self, File, OpenOptions};\n"
            "// a mutants::skip attribute is refused even here\n#[allow(dead_code)] fn _skip_marker() {}\n"
            "// #[mutants::skip]\n",
        )
        expect_refusal(case, "mutants::skip in fsio.rs", "uses forbidden #[mutants::skip]")

        # The allowed module stays allowed, and the allowed directory too.
        case = fresh()
        expected = (case / "crates/store/src/fsio.rs").read_text(encoding="utf-8")
        assert ".write(true)" in expected, "fsio.rs must be the writer this check exercises"
        expect_ok(case, "fsio.rs as the allowed write path")
    print(
        "lint mutation check: 5 refusals, 2 allowances, all as expected "
        "(write outside fsio, mkdir, Command::new, planted file, mutants::skip; "
        "fsio.rs and crates/act allowed)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
