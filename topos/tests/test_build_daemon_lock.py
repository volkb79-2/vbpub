"""The daemon lock generator parses wheel inputs and pins each universal wheel."""

from __future__ import annotations

import hashlib
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from topos.tools import build_daemon_lock as lock_builder


def _wheel(path: Path, name: str, version: str, requires: tuple[str, ...] = ()) -> Path:
    dist = name.replace("-", "_")
    metadata = ["Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}", ""]
    metadata.extend(f"Requires-Dist: {requirement}" for requirement in requires)
    metadata.append("")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{dist}-{version}.dist-info/METADATA", "\n".join(metadata))
    return path


def test_build_writes_hash_lock_sorted_and_excludes_first_party_distributions(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _wheel(
        dist / "topos-0.7.3-py3-none-any.whl",
        "topos",
        "0.7.3",
        ("textual>=8.2.8", "cli-extended>=0.5.2"),
    )

    def downloader(requirements, destination):
        assert requirements == ["textual>=8.2.8"]
        _wheel(destination / "zeta-1.0-py3-none-any.whl", "zeta", "1.0")
        _wheel(destination / "alpha-2.0-py3-none-any.whl", "alpha", "2.0")
        _wheel(destination / "topos-0.7.3-py3-none-any.whl", "topos", "0.7.3")
        _wheel(destination / "cli_extended-0.5.2-py3-none-any.whl", "cli-extended", "0.5.2")

    lock = lock_builder.build_daemon_lock(dist, downloader=downloader)
    lines = lock.read_text(encoding="utf-8").splitlines()
    assert [line.split("==", 1)[0] for line in lines] == ["alpha", "zeta"]
    for line in lines:
        assert " --hash=sha256:" in line
        assert len(line.rsplit(":", 1)[1]) == 64
    digest = hashlib.sha256(lock.read_bytes()).hexdigest()
    assert lock.with_name(lock.name + ".sha256").read_text() == f"{digest}  {lock.name}\n"
    assert lock.name == "topos-0.7.3-daemon.lock"


def test_platform_specific_dependency_wheel_is_refused_by_filename(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _wheel(dist / "topos-0.7.3-py3-none-any.whl", "topos", "0.7.3", ("textual>=8.2.8",))

    def downloader(_requirements, destination):
        _wheel(destination / "textual-8.2.8-py3-none-manylinux_2_17_x86_64.whl", "textual", "8.2.8")

    with pytest.raises(lock_builder.LockBuildError, match="textual-8.2.8-py3-none-manylinux.*py3-none-any"):
        lock_builder.build_daemon_lock(dist, downloader=downloader)


def test_filename_metadata_version_mismatch_is_refused(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _wheel(dist / "topos-0.7.3-py3-none-any.whl", "topos", "0.7.3", ("textual>=8.2.8",))

    def downloader(_requirements, destination):
        _wheel(destination / "textual-8.2.8-py3-none-any.whl", "textual", "8.2.9")

    with pytest.raises(lock_builder.LockBuildError, match="does not match metadata Version"):
        lock_builder.build_daemon_lock(dist, downloader=downloader)


def test_topos_wheel_name_and_version_are_checked_against_metadata(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _wheel(dist / "topos-0.7.3-py3-none-any.whl", "another-project", "0.7.3")
    with pytest.raises(lock_builder.LockBuildError, match="filename package does not match metadata"):
        lock_builder.build_daemon_lock(dist)

    (dist / "topos-0.7.3-py3-none-any.whl").unlink()
    _wheel(dist / "topos-0.7.3-py3-none-any.whl", "topos", "0.7.4")
    with pytest.raises(lock_builder.LockBuildError, match="does not match metadata Version"):
        lock_builder.build_daemon_lock(dist)


def test_empty_third_party_closure_writes_empty_lock_without_downloading(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    _wheel(dist / "topos-0.7.3-py3-none-any.whl", "topos", "0.7.3", ("cli-extended>=0.5.2",))
    lock = lock_builder.build_daemon_lock(dist, downloader=lambda *_args: pytest.fail("no dependencies to download"))
    assert lock.read_text(encoding="utf-8") == ""
    assert lock.with_name(lock.name + ".sha256").is_file()


def test_missing_or_multiple_built_wheels_are_refused(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    with pytest.raises(lock_builder.LockBuildError, match="found 0"):
        lock_builder.build_daemon_lock(dist)
    _wheel(dist / "topos-0.7.3-py3-none-any.whl", "topos", "0.7.3")
    _wheel(dist / "topos-0.7.4-py3-none-any.whl", "topos", "0.7.4")
    with pytest.raises(lock_builder.LockBuildError, match="found 2"):
        lock_builder.build_daemon_lock(dist)


def test_pip_download_uses_fresh_binary_only_resolution_and_reports_failure(tmp_path: Path) -> None:
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    lock_builder.pip_download(["textual>=8.2.8"], tmp_path, run=run, python="python-test")
    argv, kwargs = calls[0]
    assert argv == [
        "python-test", "-m", "pip", "download", "--only-binary", ":all:", "--dest", str(tmp_path),
        "--uploaded-prior-to", "P14D", "textual>=8.2.8",
    ]
    assert kwargs == {"capture_output": True, "text": True, "check": False}

    with pytest.raises(lock_builder.LockBuildError, match="pip download failed with exit 2"):
        lock_builder.pip_download([], tmp_path, run=lambda *_args, **_kwargs: SimpleNamespace(
            returncode=2, stdout="out", stderr="broken"
        ))
    with pytest.raises(lock_builder.LockBuildError, match="could not run pip download"):
        lock_builder.pip_download([], tmp_path, run=lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("missing pip")))


@pytest.mark.parametrize(
    ("name", "message"),
    [
        ("bad.txt", "not a wheel"),
        ("short.whl", "invalid wheel filename"),
        ("pkg-1.0-cp312-none-any.whl", "not py3-none-any"),
        ("pkg-1-build-a-b-py3-none-any.whl", "invalid wheel filename"),
    ],
)
def test_invalid_wheel_names_are_named(name: str, message: str) -> None:
    with pytest.raises(lock_builder.LockBuildError, match=message):
        lock_builder._wheel_tags(Path(name))


def test_requirement_without_a_package_name_is_refused() -> None:
    with pytest.raises(lock_builder.LockBuildError, match="cannot read dependency name"):
        lock_builder._dependency_requirements([" @ file:///bad.whl"])
