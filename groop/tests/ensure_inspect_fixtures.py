"""Ensure inspect-files test fixtures that cannot be stored in git (FIFO,
symlinks, directories) exist before running tests.

Run once before ``pytest groop/tests/test_inspect_files.py``::

    python groop/tests/ensure_inspect_fixtures.py
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

BASE = Path(__file__).resolve().parent / "fixtures" / "inspect_files"


def _ensure_fifo(path: Path) -> None:
    if path.exists():
        if stat.S_ISFIFO(path.stat().st_mode):
            return
        path.unlink()
    os.mkfifo(str(path))
    print(f"  created FIFO  {path}")


def _ensure_symlink(path: Path, target: str) -> None:
    if path.is_symlink():
        return
    if path.exists():
        path.unlink()
    path.symlink_to(target)
    print(f"  created symlink  {path} -> {target}")


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def main() -> None:
    print("Ensuring inspect-files test fixtures...")

    # cgroup_nonreg — special file types for _confine_and_open tests
    nr = BASE / "cgroup_nonreg" / "system.slice" / "ssh.service"
    nr.mkdir(parents=True, exist_ok=True)

    _ensure_symlink(nr / "memory.current", "/etc/passwd")
    _ensure_dir(nr / "cpu.stat")
    (nr / "cpu.stat" / ".gitkeep").touch()
    _ensure_fifo(nr / "pids.current")

    # Regular files that must exist alongside the special ones
    for name, content in [("pids.max", "512\n"), ("memory.min", "0\n")]:
        p = nr / name
        if not p.exists():
            p.write_text(content)
            print(f"  created regular  {p}")

    # _danger special files
    danger = BASE / "_danger"
    danger.mkdir(parents=True, exist_ok=True)
    _ensure_fifo(danger / "test_fifo")
    if not (danger / "regular_file").exists():
        (danger / "regular_file").write_text("regular\n")
    _ensure_symlink(danger / "passwd_link", "/etc/passwd")

    print("Done.")


if __name__ == "__main__":
    main()
