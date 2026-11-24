#!/usr/bin/env python3
"""Enforce the workspace lint and write-capability boundaries."""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path


class LintError(ValueError):
    pass


_EXPECTED_UNSAFE_OVERRIDES = {
    "crates/act": "allow",
    "crates/kernel": "allow",
}
# The **only** places a Rust source may open a file for writing, create a directory, or
# start a child process. The store's rule (plan v4 §A.4, milestone M1 part 3) is stronger than
# the M0 rule: the whole store crate used to be a boundary, and now exactly one module is —
# `crates/store/src/fsio.rs`, the single write path every segment append, seal, truncate,
# quarantine and rename goes through. A write added anywhere else in the store is a lint
# failure, which `tools/check-lints-mutation.py` proves by inserting one.
_WRITE_ALLOWED_DIRS = {"crates/act"}
_WRITE_ALLOWED_FILES = {"crates/store/src/fsio.rs"}
_EXPECTED_PACKAGES = {
    "crates/act",
    "crates/client",
    "crates/eval",
    "crates/kernel",
    "crates/model",
    "crates/proto",
    "crates/store",
    "bins/act",
    "bins/probe",
}
_FORBIDDEN_SOURCE_TOKENS = (
    "#[mutants::skip]",
    ".write(true)",
    "O_WRONLY",
    "O_RDWR",
    "Command::new",
    "create_dir(",
    "create_dir_all(",
    "remove_dir(",
    "remove_dir_all(",
    # The other ways to mutate the filesystem without `.write(true)` (review of pi-hostobs-m1c:
    # the M0 token set accepted `std::fs::write`, `File::create`, `remove_file`, `fs::rename`,
    # `set_len`, `set_permissions`, `.append(true)` and `.create_new(true)` anywhere).
    "fs::write(",
    "File::create(",
    "File::create_new(",
    ".create_new(true)",
    ".append(true)",
    ".truncate(true)",
    "remove_file(",
    "fs::rename(",
    "fs::copy(",
    "fs::hard_link(",
    "fs::symlink(",
    ".set_len(",
    "set_permissions(",
)


def _workspace_members(root: Path) -> list[tuple[str, Path, dict[str, object]]]:
    try:
        workspace = tomllib.loads((root / "Cargo.toml").read_text(encoding="utf-8"))["workspace"]
    except (OSError, UnicodeError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise LintError(f"cannot read root Cargo workspace: {exc}") from exc
    members: list[tuple[str, Path, dict[str, object]]] = []
    for pattern in workspace.get("members", []):
        paths = sorted(root.glob(pattern))
        if not paths:
            raise LintError(f"workspace member pattern matched no packages: {pattern}")
        for path in paths:
            manifest_path = path / "Cargo.toml"
            try:
                manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))
                package_name = manifest["package"]["name"]
            except (OSError, UnicodeError, KeyError, tomllib.TOMLDecodeError) as exc:
                raise LintError(f"cannot read workspace member {manifest_path}: {exc}") from exc
            if not isinstance(package_name, str):
                raise LintError(f"package name is not a string: {manifest_path}")
            members.append((path.relative_to(root).as_posix(), path, manifest))
    return members


def _source_files(root: Path):
    for path in sorted(root.rglob("*.rs")):
        if any(part in {"target", ".git"} for part in path.parts):
            continue
        yield path


def write_boundary(relative: str) -> bool:
    """True when `relative` (a repo-relative Rust source) may write, spawn, or mkdir.

    Both the tokens and the *location* decide: a source is a write path only if it is inside
    one of the allowed directories or is one of the allowed files. Everything else is refused,
    including another module of the same crate.
    """
    if relative in _WRITE_ALLOWED_FILES:
        return True
    return any(relative == boundary or relative.startswith(boundary + "/") for boundary in _WRITE_ALLOWED_DIRS)


def check(root: Path) -> list[str]:
    try:
        workspace = tomllib.loads((root / "Cargo.toml").read_text(encoding="utf-8"))["workspace"]
    except (OSError, UnicodeError, KeyError, tomllib.TOMLDecodeError) as exc:
        raise LintError(f"cannot read root Cargo workspace: {exc}") from exc
    rust_lints = workspace.get("lints", {}).get("rust", {})
    if rust_lints.get("unsafe_code") != "forbid":
        raise LintError("[workspace.lints.rust] must set unsafe_code = \"forbid\"")

    members = _workspace_members(root)
    paths = {member[0] for member in members}
    if paths != _EXPECTED_PACKAGES:
        raise LintError(f"workspace package paths differ from the M0 layout: {sorted(paths)}")

    for relative, _path, manifest in members:
        lints = manifest.get("lints", {})
        inherits_workspace = lints.get("workspace") is True
        rust_override = lints.get("rust", {}).get("unsafe_code")
        expected = _EXPECTED_UNSAFE_OVERRIDES.get(relative)
        if expected is None:
            if not inherits_workspace:
                raise LintError(f"{relative}/Cargo.toml must inherit [workspace.lints]")
            if rust_override is not None:
                raise LintError(f"{relative} must not override workspace unsafe_code = \"forbid\"")
        else:
            if inherits_workspace:
                raise LintError(
                    f"{relative} cannot inherit workspace lints and override unsafe_code; "
                    "Cargo rejects this combination"
                )
            if rust_override != expected:
                raise LintError(f"{relative} must retain the plan's unsafe_code = \"{expected}\" allowance")

    unsafe_allowances: list[tuple[str, int]] = []
    for source in _source_files(root):
        relative = source.relative_to(root).as_posix()
        try:
            text = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise LintError(f"cannot read Rust source {relative}: {exc}") from exc
        if "#[mutants::skip]" in text:
            raise LintError(f"{relative} uses forbidden #[mutants::skip]")
        if not relative.startswith("crates/act/"):
            occurrences = text.count("allow(unsafe_code)")
            if occurrences:
                unsafe_allowances.append((relative, occurrences))
        if write_boundary(relative):
            continue
        for token in _FORBIDDEN_SOURCE_TOKENS[1:]:
            if token in text:
                raise LintError(
                    f"{relative} uses {token!r}; writes belong in "
                    f"{sorted(_WRITE_ALLOWED_FILES)} or {sorted(_WRITE_ALLOWED_DIRS)}"
                )

    expected_allowance = [("crates/kernel/src/activation.rs", 1)]
    if unsafe_allowances != expected_allowance:
        raise LintError(
            "allow(unsafe_code) must occur exactly once outside crates/act, in "
            f"crates/kernel/src/activation.rs; found {unsafe_allowances}"
        )
    kernel_lib = root / "crates/kernel/src/lib.rs"
    try:
        kernel_text = kernel_lib.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise LintError(f"cannot read {kernel_lib}: {exc}") from exc
    if not any(
        line.strip() == "#![deny(unsafe_code)]"
        for line in kernel_text.splitlines()
    ):
        raise LintError("crates/kernel/src/lib.rs must contain #![deny(unsafe_code)]")

    mutants_path = root / ".cargo" / "mutants.toml"
    if mutants_path.exists():
        try:
            mutants_doc = tomllib.loads(mutants_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
            raise LintError(f"cannot parse {mutants_path}: {exc}") from exc
        entries = mutants_doc.get("mutants", {}).get("exclude_re", [])
        if not isinstance(entries, list):
            raise LintError(".cargo/mutants.toml [mutants].exclude_re must be an array")
        raw_lines = mutants_path.read_text(encoding="utf-8").splitlines()
        positions = [index for index, line in enumerate(raw_lines) if line.lstrip().startswith("exclude_re")]
        if len(positions) != len(entries):
            raise LintError("each .cargo/mutants.toml exclude_re entry must be on its own line")
        for position, entry in zip(positions, entries, strict=True):
            if not isinstance(entry, str) or not entry:
                raise LintError("every exclude_re entry must be a non-empty string")
            previous = position - 1
            while previous >= 0 and not raw_lines[previous].strip():
                previous -= 1
            if previous < 0 or not raw_lines[previous].lstrip().startswith("# reason:"):
                raise LintError(f"exclude_re {entry!r} needs a preceding '# reason:' comment")
    return [f"checked {len(members)} workspace packages and {sum(1 for _ in _source_files(root))} Rust sources"]


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) == 2 else Path(__file__).resolve().parents[1]
    try:
        print("\n".join(check(root.resolve())))
    except LintError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
