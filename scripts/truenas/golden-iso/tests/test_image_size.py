from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


GOLDEN_ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


STEP1 = load_module("golden_step1_size", GOLDEN_ROOT / "step1-install-prepare-truenas.py")
STEP2 = load_module("golden_step2_size", GOLDEN_ROOT / "step2-build-unattended-restore-iso.py")
RUNTIME = load_module("golden_restore_runtime_size", GOLDEN_ROOT / "runtime" / "truenas-restore.py")


@pytest.mark.parametrize("size_gib", [16, 20])
def test_step1_derives_whole_disk_size_from_qcow_metadata(size_gib: int) -> None:
    assert STEP1.source_disk_size_bytes({"virtual-size": size_gib * 1024**3}) == size_gib * 1024**3


@pytest.mark.parametrize("size_gib", [16, 20])
def test_restore_payload_name_matches_manifest_disk_size(size_gib: int) -> None:
    expected = f"truenas-27-{size_gib}GiB-golden.raw.zst"
    raw_size = size_gib * 1024**3

    assert STEP2.golden_image_payload_name(raw_size) == expected
    assert RUNTIME.golden_image_payload_name(raw_size) == expected


@pytest.mark.parametrize("size", [15 * 1024**3, 20 * 1024**3 + 512])
def test_restore_rejects_undersized_or_non_whole_gib_images(size: int) -> None:
    with pytest.raises(STEP2.BuildError):
        STEP2.golden_image_payload_name(size)
    with pytest.raises(RUNTIME.RestoreError):
        RUNTIME.golden_image_payload_name(size)


def test_step1_rejects_undersized_or_non_whole_gib_qcow_metadata() -> None:
    for size in (15 * 1024**3, 20 * 1024**3 + 512):
        with pytest.raises(STEP1.StepError):
            STEP1.source_disk_size_bytes({"virtual-size": size})


@pytest.mark.parametrize("original_kind", ["symlink", "file", "absent"])
def test_package_install_uses_builder_resolver_then_restores_live_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    original_kind: str,
) -> None:
    rootfs = tmp_path / "rootfs"
    etc = rootfs / "etc"
    etc.mkdir(parents=True)
    resolver = etc / "resolv.conf"
    original_bytes = b"# Debian Live resolver\nnameserver 192.0.2.53\n"
    original_target = "/run/systemd/resolve/stub-resolv.conf"
    if original_kind == "symlink":
        resolver.symlink_to(original_target)
    elif original_kind == "file":
        resolver.write_bytes(original_bytes)
        resolver.chmod(0o640)

    build_resolver = tmp_path / "build-resolv.conf"
    build_bytes = b"# Docker-managed builder resolver\nnameserver 127.0.0.11\n"
    build_resolver.write_bytes(build_bytes)
    monkeypatch.setattr(STEP2, "BUILD_RESOLV_CONF", build_resolver)
    monkeypatch.setattr(STEP2, "mount_chroot", lambda _args: None)

    def successful_command(argv: list[str], **_kwargs: object):
        if argv[0] == "chroot":
            assert not resolver.is_symlink()
            assert resolver.read_bytes() == build_bytes
            assert resolver.stat().st_mode & 0o777 == 0o644
        return STEP2.subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(STEP2, "run", successful_command)
    STEP2.install_runtime_packages(rootfs, "trixie")

    if original_kind == "symlink":
        assert resolver.is_symlink()
        assert resolver.readlink().as_posix() == original_target
    elif original_kind == "file":
        assert resolver.read_bytes() == original_bytes
        assert resolver.stat().st_mode & 0o777 == 0o640
    else:
        assert not resolver.exists()
        assert not resolver.is_symlink()


def test_package_install_restores_resolver_when_apt_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rootfs = tmp_path / "rootfs"
    resolver = rootfs / "etc" / "resolv.conf"
    resolver.parent.mkdir(parents=True)
    resolver.symlink_to("/run/systemd/resolve/stub-resolv.conf")
    original_target = resolver.readlink()
    build_resolver = tmp_path / "build-resolv.conf"
    build_resolver.write_text("nameserver 127.0.0.11\n", encoding="ascii")
    monkeypatch.setattr(STEP2, "BUILD_RESOLV_CONF", build_resolver)
    monkeypatch.setattr(STEP2, "mount_chroot", lambda _args: None)

    def fail_apt_update(argv: list[str], **_kwargs: object):
        if argv[0] == "chroot":
            assert resolver.read_text(encoding="ascii") == "nameserver 127.0.0.11\n"
            raise STEP2.BuildError("simulated apt DNS failure")
        return STEP2.subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(STEP2, "run", fail_apt_update)
    with pytest.raises(STEP2.BuildError, match="simulated apt DNS failure"):
        STEP2.install_runtime_packages(rootfs, "trixie")

    assert resolver.is_symlink()
    assert resolver.readlink() == original_target
