"""The first-release R2 lane moves its measured heavy files to the end."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from tests.conftest import (
    HEAVY_LAST_FILES,
    HEAVY_LAST_ORDER_ENV,
    HEAVY_LAST_ORDER_VALUE,
    pytest_collection_modifyitems,
)

TESTS_DIR = Path(__file__).resolve().parent


def test_heavy_last_files_all_exist():
    missing = sorted(name for name in HEAVY_LAST_FILES if not (TESTS_DIR / name).is_file())
    assert not missing, f"heavy-last files were renamed or removed: {missing}"


def test_collection_order_changes_only_when_the_lane_enables_it(monkeypatch):
    def item(name: str):
        return SimpleNamespace(path=TESTS_DIR / name)

    original = [
        item("test_release_end_to_end_real_git.py"),
        item("test_alpha.py"),
        item("test_installer_w1.py"),
        item("test_beta.py"),
    ]

    monkeypatch.delenv(HEAVY_LAST_ORDER_ENV, raising=False)
    pytest_collection_modifyitems(None, original)
    assert [Path(node.path).name for node in original] == [
        "test_release_end_to_end_real_git.py",
        "test_alpha.py",
        "test_installer_w1.py",
        "test_beta.py",
    ]

    monkeypatch.setenv(HEAVY_LAST_ORDER_ENV, HEAVY_LAST_ORDER_VALUE)
    pytest_collection_modifyitems(None, original)
    assert [Path(node.path).name for node in original] == [
        "test_alpha.py",
        "test_beta.py",
        "test_release_end_to_end_real_git.py",
        "test_installer_w1.py",
    ]
