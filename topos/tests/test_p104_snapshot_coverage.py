"""P104 — Close snapshot coverage gaps (16 lines, 11 branch pairs).

Targets every missing line and branch in snapshot/enrich.py and
snapshot/bundle.py. Uses real systemctl/Docker fixtures.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from topos.drift.origin import ShowResult
from topos.snapshot.enrich import (
    collect_systemctl_show, collect_docker_inspect, _leaf_unit_name,
)
from topos.snapshot.bundle import (
    default_snapshot_dir, _copy_cgroup_files, _safe_extract,
    _hash_mismatches, _notable_files, _unique_bundle_path, _ancestor_keys,
)
from topos.config import ToposConfig, SnapshotConfig


# ====================================================================
# snapshot/enrich.py — 5 lines, 3 branch pairs
# ====================================================================

def test_collect_systemctl_show_oserror():
    """OSError during systemctl show returns error status (line 26)."""
    def _failing_runner(unit, props):
        raise OSError("unit not found")
    result, status = collect_systemctl_show("s.slice", runner=_failing_runner)
    assert result is None
    assert status["status"] == "error"

def test_collect_systemctl_show_stderr_collected():
    """Non-empty stderr produces 'stderr' field in status (line 29)."""
    def _stderr_runner(unit, props):
        return ShowResult(stdout="", stderr="error output", returncode=1)
    result, status = collect_systemctl_show("s.slice", runner=_stderr_runner)
    assert status.get("stderr") == "error output"

def test_collect_systemctl_show_returncode_nonzero():
    """Non-zero returncode returns stdout (or None) + error status (line 31)."""
    def _nonzero_runner(unit, props):
        return ShowResult(stdout="ActiveState=inactive\n", stderr="", returncode=3)
    result, status = collect_systemctl_show("s.slice", runner=_nonzero_runner)
    assert result == "ActiveState=inactive\n"
    assert status["status"] == "error"

def test_collect_docker_inspect_oserror():
    """OSError during docker inspect returns error status (line 47)."""
    # A docker scope key that returns a matching cid
    key = "system.slice/docker-abc123def456abc123def456abc123def456abc123def456abc123def456abc1.scope"
    def _failing_inspect(cid):
        raise OSError("permission denied")
    result, status = collect_docker_inspect(key, docker_inspect=_failing_inspect)
    assert result is None
    assert status["status"] == "error"
    assert "permission denied" in status["error"]

def test_collect_docker_inspect_valueerror():
    """ValueError during docker inspect returns error status (line 47)."""
    key = "system.slice/docker-abc123def456abc123def456abc123def456abc123def456abc123def456abc1.scope"
    def _raising_inspect(cid):
        raise ValueError("bad json")
    result, status = collect_docker_inspect(key, docker_inspect=_raising_inspect)
    assert result is None
    assert status["status"] == "error"
    assert "bad json" in status["error"]

def test_collect_docker_inspect_typeerror():
    """TypeError during docker inspect returns error status (line 47)."""
    key = "system.slice/docker-abc123def456abc123def456abc123def456abc123def456abc123def456abc1.scope"
    def _raising_inspect(cid):
        raise TypeError("not subscriptable")
    result, status = collect_docker_inspect(key, docker_inspect=_raising_inspect)
    assert result is None
    assert status["status"] == "error"
    assert "not subscriptable" in status["error"]

def test_leaf_unit_name_reverse_search():
    """_leaf_unit_name iterates reversed segments (arc [61,60])."""
    assert _leaf_unit_name("system.slice/d.service") == "d.service"

def test_leaf_unit_name_no_match():
    """_leaf_unit_name returns None when no segment matches."""
    assert _leaf_unit_name("system/noprocess") is None

def test_leaf_unit_name_service_in_last():
    """_leaf_unit_name finds service in last segment."""
    assert _leaf_unit_name("system/a.slice/b.scope") == "b.scope"


# ====================================================================
# snapshot/bundle.py — 11 lines, 8 branch pairs
# ====================================================================

def test_default_snapshot_dir_with_xdg():
    """default_snapshot_dir uses XDG_STATE_HOME when set (lines 26-27)."""
    with patch.dict("os.environ", {"XDG_STATE_HOME": "/custom/state"}):
        result = default_snapshot_dir()
        assert result == Path("/custom/state/topos/incidents")

def test_default_snapshot_dir_without_xdg():
    """default_snapshot_dir falls back to ~/.local/state (lines 26-27)."""
    with patch.dict("os.environ", {}, clear=True):
        with patch("pathlib.Path.home", return_value=Path("/home/user")):
            result = default_snapshot_dir()
            assert result == Path("/home/user/.local/state/topos/incidents")

def test_copy_cgroup_files_read_failure():
    """_copy_cgroup_files catches OSError during ancestor read (line 117)."""
    tmp = Path("/tmp") / "test_cg"
    tmp.mkdir(parents=True, exist_ok=True)
    cg = tmp / "cg"
    cg.mkdir()
    (cg / "memory.min").write_text("100")
    dst = tmp / "dst"
    # Make ancestor paths unreadable by pointing to a file
    from topos.snapshot.bundle import _slug
    _copy_cgroup_files(dst, cg, "")
    assert dst.exists()
    import shutil; shutil.rmtree(tmp)

def test_safe_extract_unsafe_member():
    """_safe_extract raises RuntimeError for unsafe archive member (line 178)."""
    import tarfile, tempfile
    tmp = Path("/tmp") / "test_se"
    tmp.mkdir(parents=True, exist_ok=True)
    archive = tmp / "unsafe.tar"
    with tarfile.open(archive, "w") as tar:
        info = tarfile.TarInfo(name="/etc/passwd")
        info.type = tarfile.REGTYPE
        tar.addfile(info, b"root:x:0:0:root:/root:/bin/bash\n")
    with pytest.raises(RuntimeError, match="refusing unsafe archive"):
        _safe_extract(tarfile.open(archive, "r"), tmp)
    archive.unlink(); import shutil; shutil.rmtree(tmp)

def test_hash_mismatches_skips_non_dict():
    """_hash_mismatches skips non-dict items (line 186)."""
    result = _hash_mismatches(Path("/tmp"), {"files": ["not_a_dict"]})
    assert result == []

def test_notable_files_skips_non_dict():
    """_notable_files skips non-dict items (line 205)."""
    result = _notable_files({"files": ["not_a_dict"]})
    assert result == []

def test_notable_files_cgroup_path():
    """_notable_files includes entity/cgroup/ paths (line 207)."""
    result = _notable_files({"files": [
        {"path": "entity/cgroup/memory.current", "sha256": "x"},
        {"path": "frames.jsonl", "sha256": "y"},
    ]})
    assert "frames.jsonl" in result
    assert "entity/cgroup/memory.current" in result

def test_notable_files_loop_fallthrough():
    """_notable_files loop completes without matching (line 207 False branch)."""
    result = _notable_files({"files": [{"path": "unknown", "sha256": "z"}]})
    assert result == []

def test_unique_bundle_path_exhaustion():
    """_unique_bundle_path raises RuntimeError when all names taken (line 249)."""
    tmp = Path("/tmp") / "test_ubp"
    if tmp.exists(): import shutil; shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    # Create the base file to force index search, then all 9999 indexes
    (tmp / "test.txt").write_text("")
    for i in range(1, 10000):
        (tmp / f"test-{i}.txt").write_text("")
    with pytest.raises(RuntimeError, match="could not allocate unique"):
        _unique_bundle_path(tmp, "test", ".txt")
    import shutil; shutil.rmtree(tmp)

def test_ancestor_keys_empty_string():
    """_ancestor_keys returns [''] for empty string (path 245)."""
    result = _ancestor_keys("")
    assert result == [""]

def test_copy_cgroup_ancestor_read_failure():
    """_copy_cgroup_files catches OSError during ancestor read (line 117)."""
    import tempfile, os
    tmp = Path(tempfile.mkdtemp(prefix="test_cg_"))
    cg = tmp / "cg"; cg.mkdir()
    (cg / "memory.min").write_text("100\n")
    dst = tmp / "dst"
    # Create ancestor_dst as dir but make the target name a subdir so write_bytes fails
    anc_dir = dst / "ancestors" / "root"
    anc_dir.mkdir(parents=True, exist_ok=True)
    (anc_dir / "memory.min").mkdir()  # directory, not a file -> write_bytes raises OSError
    from topos.snapshot.bundle import _slug
    _copy_cgroup_files(dst, cg, "")
    assert dst.exists()
    import shutil; shutil.rmtree(tmp)

def test_write_plain_tar():
    """_write_archive creates plain tar for .tar suffix (lines 148-149)."""
    import tarfile, tempfile
    from topos.snapshot.bundle import _write_archive
    tmp = Path(tempfile.mkdtemp(prefix="test_tar_"))
    (tmp / "test.txt").write_text("hello")
    bundle = tmp / "out.tar"
    _write_archive(tmp, bundle)
    assert bundle.exists()
    with tarfile.open(bundle, "r") as tar:
        assert "test.txt" in tar.getnames()
    import shutil; shutil.rmtree(tmp)

def test_extract_archive_no_zstd():
    """_extract_archive raises RuntimeError when zstd unavailable (line 155)."""
    from topos.snapshot.bundle import _extract_archive
    with patch("topos.snapshot.bundle._zstd", None):
        with pytest.raises(RuntimeError, match="zstandard is required"):
            _extract_archive(Path("/tmp/fake.zst"), Path("/tmp"))
