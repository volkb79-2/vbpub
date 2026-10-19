"""No tracked text file may carry Git merge-conflict markers.

INT-01 review A-01: a 3-way port left `<<<<<<< ours` / `=======` / `>>>>>>> theirs` in a shipped
README and every gate stayed green because none of them reads that file. This guard lives in
run-gate-project (the estate's gate owner; `selftest` is a registered fast lane) and scans every
tracked file of the monorepo. It is monorepo-only by design: it skips when the repository root
cannot be listed with Git (an exported copy of this project alone).

A marker line is one that starts with `<<<<<<< ` or `>>>>>>> ` or is exactly `=======`. A file that
legitimately needs such a line (a quoted conflict in a report, a setext heading) is named in
ALLOWED with the reason; the list is empty because none exists today.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
ALLOWED: dict[str, str] = {}
MAX_BYTES = 5_000_000


def is_marker(line: str) -> bool:
    return line.startswith("<<<<<<< ") or line.startswith(">>>>>>> ") or line == "======="


def marker_lines(text: str) -> list[tuple[int, str]]:
    return [(number, line) for number, line in enumerate(text.splitlines(), 1) if is_marker(line)]


def scan(root: Path, tracked: list[str]) -> list[str]:
    hits = []
    for relative in tracked:
        if relative in ALLOWED:
            continue
        path = root / relative
        if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        hits.extend(f"{relative}:{number}: {line[:40]}" for number, line in marker_lines(text))
    return hits


def _tracked(root: Path) -> list[str] | None:
    try:
        listed = subprocess.run(
            ["git", "ls-files", "--cached", "-z"], cwd=root, check=True, capture_output=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return [raw.decode("utf-8") for raw in listed.split(b"\0") if raw]


def test_the_scan_sees_markers_in_a_fixture_and_ignores_clean_text(tmp_path):
    (tmp_path / "bad.md").write_text("a\n<<<<<<< ours\nx\n=======\ny\n>>>>>>> theirs\n", encoding="utf-8")
    (tmp_path / "clean.md").write_text("Title\n-----\nuse <<<<<<< inside a line\n", encoding="utf-8")
    (tmp_path / "bin.dat").write_bytes(b"\xff\xfe\x00<<<<<<< x\n")
    hits = scan(tmp_path, ["bad.md", "clean.md", "bin.dat", "missing.md"])
    assert [hit.split(":")[0:2] for hit in hits] == [["bad.md", "2"], ["bad.md", "4"], ["bad.md", "6"]]


def test_no_tracked_file_carries_merge_conflict_markers():
    tracked = _tracked(REPO_ROOT)
    if not tracked or not (REPO_ROOT / "run-gate.root.toml").is_file():
        pytest.skip("not the full monorepo checkout (the guard is monorepo-only by design)")
    assert len(tracked) > 1000, "the listing is implausibly small; the scan would pass vacuously"
    assert scan(REPO_ROOT, tracked) == []
