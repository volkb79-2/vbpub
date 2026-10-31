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
