"""No tracked text file may carry Git merge-conflict markers.

INT-01 review A-01: a 3-way port left `<<<<<<< ours` / `=======` / `>>>>>>> theirs` in a shipped
README and every gate stayed green because none of them reads that file. This guard lives in
run-gate-project (the estate's gate owner; `selftest` is a registered fast lane) and scans every
tracked file of the monorepo.

What counts: a line starting `<<<<<<< ` (conflict start), a line starting `>>>>>>> ` (conflict end),
and a line that is exactly `=======` ONLY inside a start..end region (a bare `=======` elsewhere is a
legal markdown setext underline). A lone start or end marker fails by itself.

When it may skip: only when this is not the full monorepo, which is a fact about the checkout
(`run-gate.root.toml` is absent). When that file is present, a failing `git ls-files` is a FAILURE with
Git's own message, never a skip: absence of a listing is not absence of markers.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import _testdeps   # missing optional test dependencies FAIL by default

REPO_ROOT = Path(__file__).resolve().parents[2]
ALLOWED: dict[str, str] = {}
MAX_BYTES = 5_000_000


def marker_lines(text: str) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    in_region = False
    for number, line in enumerate(text.splitlines(), 1):
        if line.startswith("<<<<<<< "):
            hits.append((number, line))
            in_region = True
        elif line.startswith(">>>>>>> "):
            hits.append((number, line))
            in_region = False
        elif line == "=======" and in_region:
            hits.append((number, line))
    return hits


def scan(root: Path, tracked: list[str]) -> tuple[list[str], int]:
    """(hits, number of files skipped for size, encoding, symlink or absence)."""
    hits: list[str] = []
    skipped = 0
    for relative in tracked:
        if relative in ALLOWED:
            continue
        path = root / relative
        if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_BYTES:
            skipped += 1
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            skipped += 1
            continue
        hits.extend(f"{relative}:{number}: {line[:40]}" for number, line in marker_lines(text))
    return hits, skipped


def tracked_files(root: Path) -> list[str]:
    """The tracked files; raises with Git's message when the listing cannot be produced."""
    try:
        done = subprocess.run(["git", "ls-files", "--cached", "-z"], cwd=root, capture_output=True)
    except OSError as exc:
        raise RuntimeError(f"git could not be run in {root}: {exc}") from exc
    if done.returncode != 0:
        raise RuntimeError(f"git ls-files failed in {root}: {done.stderr.decode('utf-8', 'replace').strip()}")
    return [raw.decode("utf-8") for raw in done.stdout.split(b"\0") if raw]


def check_repository(root: Path) -> tuple[list[str], int] | None:
    """None = not the full monorepo (skip). Otherwise (hits, skipped); a Git failure raises."""
    if not (root / "run-gate.root.toml").is_file():
        return None
    tracked = tracked_files(root)
    assert len(tracked) > 1000, "the listing is implausibly small; the scan would pass vacuously"
    return scan(root, tracked)


def test_a_real_conflict_block_fails_and_a_setext_heading_passes(tmp_path):
    (tmp_path / "bad.md").write_text("a\n<<<<<<< ours\nx\n=======\ny\n>>>>>>> theirs\n", encoding="utf-8")
    (tmp_path / "setext.md").write_text("Title\n=======\n\ntext\n\nOther\n-----\n", encoding="utf-8")
    (tmp_path / "midline.md").write_text("use <<<<<<< inside a line\n", encoding="utf-8")
    hits, skipped = scan(tmp_path, ["bad.md", "setext.md", "midline.md"])
    assert [h.split(":")[:2] for h in hits] == [["bad.md", "2"], ["bad.md", "4"], ["bad.md", "6"]]
    assert skipped == 0


def test_a_lone_start_or_end_marker_fails_and_a_stray_separator_after_a_region_does_not(tmp_path):
    (tmp_path / "start.md").write_text("<<<<<<< HEAD\nonly a start\n", encoding="utf-8")
    (tmp_path / "end.md").write_text("only an end\n>>>>>>> branch\n", encoding="utf-8")
    (tmp_path / "after.md").write_text("<<<<<<< a\n=======\n>>>>>>> b\nHeading\n=======\n", encoding="utf-8")
    hits, _ = scan(tmp_path, ["start.md", "end.md", "after.md"])
    assert [h.split(":")[:2] for h in hits] == [
        ["start.md", "1"], ["end.md", "2"], ["after.md", "1"], ["after.md", "2"], ["after.md", "3"],
    ]  # the setext underline on line 5, after the region closed, is not flagged


def test_skipped_files_are_counted_not_hidden(tmp_path):
    (tmp_path / "bin.dat").write_bytes(b"\xff\xfe\x00<<<<<<< x\n")
    hits, skipped = scan(tmp_path, ["bin.dat", "missing.md"])
    assert hits == [] and skipped == 2


def test_a_git_failure_in_a_full_monorepo_fails_instead_of_skipping(tmp_path):
    (tmp_path / "run-gate.root.toml").write_text("", encoding="utf-8")  # looks like the monorepo, is not a git tree
    with pytest.raises(RuntimeError, match="git ls-files failed"):
        check_repository(tmp_path)


def test_only_a_missing_monorepo_marker_file_means_skip(tmp_path):
    assert check_repository(tmp_path) is None


def test_no_tracked_file_carries_merge_conflict_markers():
    outcome = check_repository(REPO_ROOT)
    if outcome is None:
        _testdeps.missing("the monorepo checkout",
                          "not the full monorepo checkout (run-gate.root.toml is absent; monorepo-only by design)")
    hits, skipped = outcome
    assert hits == [], f"merge-conflict markers found ({skipped} files skipped for size/encoding): {hits}"
