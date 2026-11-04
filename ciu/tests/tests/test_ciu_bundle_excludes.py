"""CIU-175: the non-thin push sync never ships `.ciu/`, `ciu.env` or `.git`, and
applies the host's `bundle_excludes` (SPEC S14.2/S14.6)."""
from __future__ import annotations

import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from ciu import activate
from ciu import transport_ssh as tssh

# `tssh.subprocess` is the global module: keep the real run() before patching it.
_REAL_RUN = subprocess.run


def test_bundle_excludes_for_default_is_the_mandatory_set():
    assert activate.bundle_excludes_for({}) == [".git", ".ciu/", "ciu.env"]


def test_bundle_excludes_for_merges_host_list_without_duplicates():
    got = activate.bundle_excludes_for({"bundle_excludes": [".git", "ciu.instance.generated.toml"]})
    assert got == [".git", ".ciu/", "ciu.env", "ciu.instance.generated.toml"]


@pytest.mark.parametrize("bad", ["x", [1], {"a": 1}])
def test_bundle_excludes_for_rejects_a_non_string_list(bad):
    with pytest.raises(ValueError, match="list of strings"):
        activate.bundle_excludes_for({"bundle_excludes": bad})


def _tree(root: Path) -> None:
    (root / ".ciu").mkdir(parents=True)
    (root / ".ciu" / "ghcr-token").write_text("SECRET")
    (root / "stack" / ".ciu").mkdir(parents=True)
    (root / "stack" / ".ciu" / "pw").write_text("SECRET2")
    (root / "stack" / "ciu.env").write_text("X=1")
    (root / "ciu.env").write_text("DOCKER_GID=1")
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref")
    (root / "ciu.instance.generated.toml").write_text("control-host-instance")
    (root / "stack" / "compose.yml").write_text("services: {}")


def test_tarball_drops_secrets_at_any_depth_and_host_excludes(tmp_path):
    src = tmp_path / "src"
    _tree(src)
    ball = activate.make_tarball(
        str(src), excludes=activate.bundle_excludes_for(
            {"bundle_excludes": ["ciu.instance.generated.toml"]}))
    try:
        with tarfile.open(ball) as tar:
            names = sorted(n.removeprefix("./") for n in tar.getnames())
    finally:
        Path(ball).unlink()
    assert "stack/compose.yml" in names
    assert not any(".ciu" in n.split("/") or n.endswith("ciu.env") or n.startswith(".git")
                   or n == "ciu.instance.generated.toml" for n in names), names


def _generated_rsync(tmp_path, monkeypatch, host_extra=None):
    """Run the real ssh_sync argv builder with subprocess captured."""
    key = tmp_path / "id"
    key.write_text("KEY")
    host = {"ssh_host": "h", "ssh_user": "u", "ssh_key": str(key), "known_host": "k"}
    host.update(host_extra or {})
    seen: list[list[str]] = []
    monkeypatch.setattr(tssh.subprocess, "run",
                        lambda cmd, **kw: seen.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    monkeypatch.setattr(tssh, "_known_hosts_file", lambda cfg: None)
    assert tssh.ssh_sync(host, "SRC", "DST", config={}, repo_root=tmp_path,
                         excludes=activate.bundle_excludes_for(host)) == 0
    return seen[0]


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync not installed")
def test_generated_rsync_command_protects_target_instance_file_and_never_ships_secrets(
    tmp_path, monkeypatch
):
    cmd = _generated_rsync(tmp_path, monkeypatch,
                           {"bundle_excludes": ["ciu.instance.generated.toml"]})
    # Rewrite only the transport: drop `-e <ssh>` and point source/destination at temp dirs.
    i = cmd.index("-e")
    argv = cmd[:i] + [str(tmp_path / "src") + "/", str(tmp_path / "dst") + "/"]
    assert argv[0] == "rsync" and "--exclude" in argv
    src, dst = tmp_path / "src", tmp_path / "dst"
    _tree(src)
    dst.mkdir()
    (dst / "ciu.instance.generated.toml").write_text("target-instance")
    (dst / "ciu.env").write_text("target-env")
    _REAL_RUN(argv, check=True)
    assert (dst / "stack" / "compose.yml").read_text() == "services: {}"
    assert (dst / "ciu.instance.generated.toml").read_text() == "target-instance"
    assert (dst / "ciu.env").read_text() == "target-env"
    assert not (dst / ".ciu").exists()
    assert not (dst / "stack" / ".ciu").exists()
    assert not (dst / "stack" / "ciu.env").exists()
    assert not (dst / ".git").exists()
