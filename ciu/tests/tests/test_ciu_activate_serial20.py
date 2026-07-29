"""Regression coverage for thin-activation archive-build cleanup."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import activate


def test_archive_build_error_is_not_masked_when_tempfile_cleanup_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Operators receive the archive failure, even if its best-effort cleanup fails."""
    source = tmp_path / "repo"
    source.mkdir()
    tarball = tmp_path / "ciu_bundle.tar.gz"
    fd = os.open(tarball, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
    monkeypatch.setattr(activate.tempfile, "mkstemp", lambda **_kwargs: (fd, str(tarball)))

    def archive_disk_full(*_args: object, **_kwargs: object) -> object:
        raise OSError("archive disk full")

    def cleanup_unavailable(_path: str) -> None:
        raise OSError("cleanup unavailable")

    monkeypatch.setattr(activate.tarfile, "open", archive_disk_full)
    monkeypatch.setattr(activate.os, "unlink", cleanup_unavailable)

    with pytest.raises(OSError, match="archive disk full"):
        activate.make_tarball(str(source))

    assert tarball.exists()
