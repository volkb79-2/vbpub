"""The golden-image source VM follows the estate storage layout (AGENTS.md)."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

GOLDEN_ROOT = Path(__file__).resolve().parents[1]
TRUENAS_ROOT = GOLDEN_ROOT.parent
spec = importlib.util.spec_from_file_location("golden_step1_layout", GOLDEN_ROOT / "step1-install-prepare-truenas.py")
assert spec is not None and spec.loader is not None
STEP1 = importlib.util.module_from_spec(spec)
sys.modules["golden_step1_layout"] = STEP1
spec.loader.exec_module(STEP1)

VM_LAB = TRUENAS_ROOT / "artifacts" / "vm-lab"


def test_source_vm_state_is_one_vm_lab_directory() -> None:
    for path in (
        STEP1.DEFAULT_VM_DISK,
        STEP1.DEFAULT_VM_VARS,
        STEP1.DEFAULT_VM_KEY,
        STEP1.DEFAULT_KNOWN_HOSTS,
        STEP1.DEFAULT_QEMU_LOG,
    ):
        assert path.parent == VM_LAB / "golden-source-27-20GiB", path


def test_built_outputs_stay_in_the_project_artifacts_directory() -> None:
    assert STEP1.DEFAULT_IMAGE.parent == GOLDEN_ROOT / "artifacts"
    assert STEP1.DEFAULT_SECRETS.parent == GOLDEN_ROOT / "artifacts"


def test_installed_source_disk_is_a_golden_base_and_never_cache_unsafe() -> None:
    assert STEP1.SOURCE_DISK_CACHE in {"writeback", "none"}


def test_vm_lab_placeholder_is_committed_with_readme_and_ignore_all_rule() -> None:
    assert (VM_LAB / "README.md").is_file()
    assert (VM_LAB / ".gitignore").read_text().splitlines() == ["*", "!.gitignore", "!README.md"]
