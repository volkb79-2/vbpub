"""RG-87: the conftest session guard must catch any test that writes into a
real PATH directory or $HOME/.local/bin (the 2026-10-06 incident class)."""

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

CONFTEST = Path(__file__).with_name("conftest.py")


def _load_conftest():
    spec = importlib.util.spec_from_file_location("rg87_conftest_copy",
                                                  CONFTEST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_guarded_dirs_cover_path_and_home_bins():
    mod = _load_conftest()
    dirs = mod.guarded_dirs({"PATH": "/a/bin::/b/bin:/a/bin", "HOME": "/h"})
    assert [str(d) for d in dirs] == [
        "/a/bin", "/b/bin", "/h/.local/bin", "/h/.venv/bin"]


def test_diff_detects_created_modified_removed_and_new_dir(tmp_path):
    mod = _load_conftest()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "keep").write_text("1")
    (bin_dir / "gone").write_text("1")
    absent = tmp_path / "absent"
    before = mod.snapshot_dirs([bin_dir, absent])
    assert mod.diff_snapshots(before, mod.snapshot_dirs([bin_dir, absent])) == []
    (bin_dir / "assay").write_text("planted")
    (bin_dir / "keep").write_text("changed-size")
    (bin_dir / "gone").unlink()
    absent.mkdir()
    problems = mod.diff_snapshots(before, mod.snapshot_dirs([bin_dir, absent]))
    assert f"{bin_dir}/assay: CREATED" in problems
    assert f"{bin_dir}/keep: MODIFIED" in problems
    assert f"{bin_dir}/gone: REMOVED" in problems
    assert f"{absent}: directory was CREATED" in problems


def _run_session(tmp_path, plant: str):
    """Run a real pytest session under a copy of run-gate's conftest, with a
    fake HOME and a fake PATH[0]; `plant` picks where the inner test writes."""
    work = tmp_path / "session"
    work.mkdir()
    shutil.copy(CONFTEST, work / "conftest.py")
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    path_bin = tmp_path / "pathbin"
    path_bin.mkdir()
    (work / "test_inner.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "def test_inner():\n"
        f"    plant = {plant!r}\n"
        "    if plant == 'path':\n"
        "        Path(os.environ['PATH'].split(':')[0], 'assay').write_text('x')\n"
        "    elif plant == 'home':\n"
        "        Path(os.environ['HOME'], '.local', 'bin', 'assay')"
        ".write_text('x')\n")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("PYTEST", "COV_", "COVERAGE"))}
    env["HOME"] = str(home)
    env["PATH"] = f"{path_bin}:{os.environ['PATH']}"
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
        cwd=work, env=env, capture_output=True, text=True, timeout=120)


def test_clean_session_passes(tmp_path):
    result = _run_session(tmp_path, "none")
    assert result.returncode == 0, result.stdout + result.stderr


def test_planted_write_into_path_dir_fails_the_session(tmp_path):
    result = _run_session(tmp_path, "path")
    assert result.returncode != 0, result.stdout + result.stderr
    assert "RG-87 PATH-WRITE GUARD" in result.stderr
    assert "pathbin/assay: CREATED" in result.stderr


def test_planted_write_into_home_local_bin_fails_the_session(tmp_path):
    result = _run_session(tmp_path, "home")
    assert result.returncode != 0, result.stdout + result.stderr
    assert ".local/bin/assay: CREATED" in result.stderr
