"""Strict, no-fuzz application of the adapter's unified diff files.

The TrueNAS live installer does not ship GNU ``patch``. This small applicator
supports the ordinary, single-file unified diffs used by the release adapters
and refuses offsets, fuzz, path traversal, new files, and deletes.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path, PurePosixPath


class UnifiedPatchError(RuntimeError):
    """A patch was malformed or did not match the exact target source."""


HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:.*)?$")


@dataclass(frozen=True)
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: tuple[str, ...]


@dataclass(frozen=True)
class FilePatch:
    path: PurePosixPath
    hunks: tuple[Hunk, ...]


def _safe_patch_path(value: str) -> PurePosixPath:
    if value == "/dev/null":
        raise UnifiedPatchError("release adapters may not create or delete files")
    if "\t" in value:
        value = value.split("\t", 1)[0]
    if value.startswith(("a/", "b/")):
        value = value[2:]
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise UnifiedPatchError(f"unsafe patch target path: {value!r}")
    return path


def parse_unified_patch(patch_text: str) -> tuple[FilePatch, ...]:
    """Parse one or more exact unified-diff file sections."""
    lines = patch_text.splitlines(keepends=True)
    result: list[FilePatch] = []
    index = 0
    while index < len(lines):
        if not lines[index].startswith("--- "):
            index += 1
            continue
        old_path = _safe_patch_path(lines[index][4:].rstrip("\r\n"))
        index += 1
        if index >= len(lines) or not lines[index].startswith("+++ "):
            raise UnifiedPatchError("unified diff is missing its new-file header")
        new_path = _safe_patch_path(lines[index][4:].rstrip("\r\n"))
        index += 1
        if old_path != new_path:
            raise UnifiedPatchError(f"rename patches are not supported: {old_path} -> {new_path}")

        hunks: list[Hunk] = []
        while index < len(lines) and lines[index].startswith("@@ "):
            match = HUNK_RE.fullmatch(lines[index].rstrip("\r\n"))
            if match is None:
                raise UnifiedPatchError(f"malformed hunk header: {lines[index].rstrip()}")
            old_start = int(match.group(1))
            old_count = int(match.group(2) or "1")
            new_start = int(match.group(3))
            new_count = int(match.group(4) or "1")
            index += 1
            hunk_lines: list[str] = []
            consumed_old = 0
            consumed_new = 0
            while consumed_old < old_count or consumed_new < new_count:
                if index >= len(lines):
                    raise UnifiedPatchError("unified diff ended in the middle of a hunk")
                line = lines[index]
                if line.startswith("\\ No newline at end of file"):
                    raise UnifiedPatchError("no-newline unified diff markers are not supported")
                if not line or line[0] not in " +-":
                    raise UnifiedPatchError("unified diff hunk has an invalid line prefix")
                prefix = line[0]
                if prefix in " -":
                    consumed_old += 1
                if prefix in " +":
                    consumed_new += 1
                if consumed_old > old_count or consumed_new > new_count:
                    raise UnifiedPatchError("unified diff hunk line counts do not match its header")
                hunk_lines.append(line)
                index += 1
            hunks.append(Hunk(old_start, old_count, new_start, new_count, tuple(hunk_lines)))
        if not hunks:
            raise UnifiedPatchError(f"patch for {old_path} has no hunks")
        result.append(FilePatch(old_path, tuple(hunks)))

    if not result:
        raise UnifiedPatchError("unified diff contains no file patches")
    return tuple(result)


def apply_patch_to_text(original: str, file_patch: FilePatch) -> str:
    """Apply one parsed file patch, requiring every context line at its offset."""
    original_lines = original.splitlines(keepends=True)
    output: list[str] = []
    old_cursor = 0
    new_cursor = 0

    for hunk in file_patch.hunks:
        expected_old = 0 if hunk.old_start == 0 else hunk.old_start - 1
        expected_new = 0 if hunk.new_start == 0 else hunk.new_start - 1
        if expected_old < old_cursor or expected_new < new_cursor:
            raise UnifiedPatchError(f"overlapping or out-of-order hunk in {file_patch.path}")
        output.extend(original_lines[old_cursor:expected_old])
        new_cursor += expected_old - old_cursor
        old_cursor = expected_old
        if new_cursor != expected_new:
            raise UnifiedPatchError(f"hunk offset mismatch in {file_patch.path}")

        for line in hunk.lines:
            prefix, content = line[0], line[1:]
            if prefix in " -":
                if old_cursor >= len(original_lines) or original_lines[old_cursor] != content:
                    raise UnifiedPatchError(
                        f"exact patch context mismatch in {file_patch.path} at line {old_cursor + 1}"
                    )
                old_cursor += 1
            if prefix in " +":
                output.append(content)
                new_cursor += 1

    output.extend(original_lines[old_cursor:])
    return "".join(output)


def apply_patch_batch(root: Path, patch_texts: list[str]) -> dict[PurePosixPath, str]:
    """Apply all patch text to an in-memory tree and return changed files.

    No filesystem changes are made. Each patch in the batch sees the results
    of the preceding patch, matching the release adapter's declared order.
    """
    root = root.resolve(strict=True)
    staged: dict[PurePosixPath, str] = {}
    for patch_text in patch_texts:
        for file_patch in parse_unified_patch(patch_text):
            path = file_patch.path
            target = root.joinpath(*path.parts)
            try:
                target.resolve(strict=True).relative_to(root)
            except (OSError, ValueError) as exc:
                raise UnifiedPatchError(f"patch target escapes or is missing: {path}") from exc
            if target.is_symlink() or not target.is_file():
                raise UnifiedPatchError(f"patch target is not a regular file: {path}")
            original = staged.get(path)
            if original is None:
                try:
                    original = target.read_text(encoding="utf-8")
                except (OSError, UnicodeError) as exc:
                    raise UnifiedPatchError(f"cannot read patch target {path}: {exc}") from exc
            staged[path] = apply_patch_to_text(original, file_patch)
    return staged
