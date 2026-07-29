"""Archive fallback coverage for neutral tar-member names."""
from __future__ import annotations

import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import activate


def test_make_tarball_keeps_neutral_member_and_excludes_requested_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The archive preserves deployable content without shipping excluded files."""
    source = tmp_path / "bundle"
    source.mkdir()
    (source / "keep.txt").write_text("safe payload")
    private = source / ".git"
    private.mkdir()
    (private / "config").write_text("must not be deployed")

    original_add = tarfile.TarFile.add
    neutral_member_seen = False

    def add_with_neutral_member(
        self: tarfile.TarFile, name: str, arcname: str | None = None, recursive: bool = True,
        *, filter: object = None
    ) -> None:
        nonlocal neutral_member_seen
        assert callable(filter)
        neutral = tarfile.TarInfo("")
        assert filter(neutral) is neutral
        neutral_member_seen = True
        original_add(self, name, arcname, recursive, filter=filter)

    monkeypatch.setattr(tarfile.TarFile, "add", add_with_neutral_member)
    tarball = activate.make_tarball(str(source), excludes=[".git"])
    try:
        with tarfile.open(tarball) as archive:
            names = {name[2:] if name.startswith("./") else name for name in archive.getnames()}
        assert neutral_member_seen
        assert "keep.txt" in names
        assert not any(name == ".git" or name.startswith(".git/") for name in names)
    finally:
        Path(tarball).unlink()
