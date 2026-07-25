"""P104 exact behavioral coverage for snapshot enrichment and bundles."""

from __future__ import annotations

import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from topos.drift.origin import ShowResult
from topos.snapshot.bundle import (
    _ancestor_keys,
    _copy_cgroup_files,
    _extract_archive,
    _hash_mismatches,
    _notable_files,
    _safe_extract,
    _unique_bundle_path,
    _write_archive,
    default_snapshot_dir,
)
from topos.snapshot.enrich import (
    _leaf_unit_name,
    collect_docker_inspect,
    collect_systemctl_show,
)


DOCKER_ID = "abc123def456abc123def456abc123def456abc123def456abc123def456abc1"
DOCKER_KEY = f"system.slice/docker-{DOCKER_ID}.scope"


def test_collect_systemctl_show_oserror() -> None:
    def failing_runner(_unit: str, _properties: tuple[str, ...]) -> ShowResult:
        raise OSError("unit not found")

    assert collect_systemctl_show("s.slice", runner=failing_runner) == (
        None,
        {"status": "error", "unit": "s.slice", "error": "unit not found"},
    )


def test_collect_systemctl_show_stderr_collected() -> None:
    def stderr_runner(_unit: str, _properties: tuple[str, ...]) -> ShowResult:
        return ShowResult(stdout="", stderr="  error output  ", returncode=1)

    assert collect_systemctl_show("s.slice", runner=stderr_runner) == (
        None,
        {
            "status": "error",
            "unit": "s.slice",
            "returncode": 1,
            "stderr": "error output",
        },
    )


def test_collect_systemctl_show_returncode_nonzero() -> None:
    def nonzero_runner(_unit: str, _properties: tuple[str, ...]) -> ShowResult:
        return ShowResult(
            stdout="ActiveState=inactive\n",
            stderr="",
            returncode=3,
        )

    assert collect_systemctl_show("s.slice", runner=nonzero_runner) == (
        "ActiveState=inactive\n",
        {"status": "error", "unit": "s.slice", "returncode": 3},
    )


def test_collect_docker_inspect_oserror() -> None:
    def failing_inspect(_container_id: str) -> object:
        raise OSError("permission denied")

    assert collect_docker_inspect(DOCKER_KEY, docker_inspect=failing_inspect) == (
        None,
        {
            "status": "error",
            "container_id": DOCKER_ID,
            "error": "permission denied",
        },
    )


def test_collect_docker_inspect_valueerror() -> None:
    def failing_inspect(_container_id: str) -> object:
        raise ValueError("bad json")

    assert collect_docker_inspect(DOCKER_KEY, docker_inspect=failing_inspect) == (
        None,
        {"status": "error", "container_id": DOCKER_ID, "error": "bad json"},
    )


def test_collect_docker_inspect_typeerror() -> None:
    def failing_inspect(_container_id: str) -> object:
        raise TypeError("not subscriptable")

    assert collect_docker_inspect(DOCKER_KEY, docker_inspect=failing_inspect) == (
        None,
        {
            "status": "error",
            "container_id": DOCKER_ID,
            "error": "not subscriptable",
        },
    )


def test_leaf_unit_name_reverse_search() -> None:
    assert _leaf_unit_name("system.slice/not-a-unit/d.service") == "d.service"


def test_leaf_unit_name_no_match() -> None:
    assert _leaf_unit_name("system/noprocess") is None


def test_leaf_unit_name_prefers_last_unit_segment() -> None:
    assert _leaf_unit_name("system/a.slice/b.scope") == "b.scope"


def test_default_snapshot_dir_with_xdg() -> None:
    with patch.dict("os.environ", {"XDG_STATE_HOME": "/custom/state"}):
        assert default_snapshot_dir() == Path("/custom/state/topos/incidents")


def test_default_snapshot_dir_without_xdg() -> None:
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("pathlib.Path.home", return_value=Path("/home/user")),
    ):
        assert default_snapshot_dir() == Path(
            "/home/user/.local/state/topos/incidents"
        )


def test_copy_cgroup_file_write_failure(tmp_path: Path) -> None:
    cgroup_root = tmp_path / "cgroup"
    cgroup_root.mkdir()
    (cgroup_root / "memory.min").write_text("100\n")
    destination = tmp_path / "destination"
    ancestor_destination = destination / "ancestors" / "root"
    ancestor_destination.mkdir(parents=True)
    (ancestor_destination / "memory.min").mkdir()

    _copy_cgroup_files(destination, cgroup_root, "")

    assert (destination / "memory.min").read_text() == "100\n"
    assert sorted(
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
    ) == [
        "ancestors",
        "ancestors/root",
        "ancestors/root/memory.min",
        "memory.min",
    ]
    assert (ancestor_destination / "memory.min").is_dir()


def test_safe_extract_rejects_absolute_member(tmp_path: Path) -> None:
    archive = tmp_path / "unsafe.tar"
    with tarfile.open(archive, "w") as tar:
        tar.addfile(tarfile.TarInfo(name="/etc/passwd"))

    with tarfile.open(archive, "r") as tar:
        with pytest.raises(
            RuntimeError,
            match=r"refusing unsafe archive member: /etc/passwd",
        ):
            _safe_extract(tar, tmp_path)


def test_hash_mismatches_skips_non_dict(tmp_path: Path) -> None:
    assert _hash_mismatches(tmp_path, {"files": ["not_a_dict"]}) == []


def test_notable_files_skips_non_dict() -> None:
    assert _notable_files({"files": ["not_a_dict"]}) == []


def test_notable_files_selects_and_sorts_expected_paths() -> None:
    assert _notable_files(
        {
            "files": [
                {"path": "frames.jsonl", "sha256": "y"},
                {"path": "entity/cgroup/memory.current", "sha256": "x"},
            ]
        }
    ) == ["entity/cgroup/memory.current", "frames.jsonl"]


def test_notable_files_ignores_unknown_path() -> None:
    assert _notable_files({"files": [{"path": "unknown", "sha256": "z"}]}) == []


def test_unique_bundle_path_reports_exhaustion() -> None:
    with patch.object(Path, "exists", return_value=True) as exists:
        with pytest.raises(
            RuntimeError,
            match=r"could not allocate unique snapshot path in /virtual",
        ):
            _unique_bundle_path(Path("/virtual"), "test", ".txt")

    assert exists.call_count == 10_000


def test_ancestor_keys_empty_string() -> None:
    assert _ancestor_keys("") == [""]


def test_ancestor_keys_returns_all_proper_ancestors() -> None:
    assert _ancestor_keys("a/b/c") == ["", "a", "a/b"]


def test_write_plain_tar_has_exact_member_set(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "test.txt").write_text("hello")
    bundle = tmp_path / "out.tar"

    _write_archive(root, bundle)

    with tarfile.open(bundle, "r") as tar:
        assert tar.getnames() == ["test.txt"]


def test_extract_archive_requires_zstandard(tmp_path: Path) -> None:
    with patch("topos.snapshot.bundle._zstd", None):
        with pytest.raises(
            RuntimeError,
            match=r"zstandard is required to inspect \.tar\.zst bundles",
        ):
            _extract_archive(tmp_path / "fake.zst", tmp_path)
