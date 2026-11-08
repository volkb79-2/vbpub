"""The release-flow driver of the image-runtime lane (MDT-012), tested without Docker."""
from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import run_image_runtime_lane as driver  # noqa: E402


def test_the_lane_is_registered_with_explicit_targets_and_no_typed_image_refs() -> None:
    lane = tomllib.loads((ROOT / "run-gate.toml").read_text(encoding="utf-8"))["lanes"]["image-runtime-built"]
    assert lane["environment"] == "bare-host" and lane["clean_tree"] is True
    assert lane["required_env"] == [
        "CGROUP_PARENT_DEV_GATES", "BUILDX_BUILDER", "BUILDKIT_HOST",
        "MDT_BUILDKIT_SOCKET", "MDT_BUILDKIT_SOCKET_DIR", "MDT_BUILDKIT_GID", "MDT_HOST_DOCKER_GID",
        "MDT_DOCKER_API_SOCKET", "MDT_DOCKER_API_SOCKET_DIR", "CGPROFILE_CONTROL_SOCKET",
        "CGPROFILE_CONTROL_SOCKET_DIR", "CGPROFILE_CONTROL_GID", "CGPROFILE_OBSERVER_SOCKET",
        "CGPROFILE_OBSERVER_SOCKET_DIR", "TOPOS_OBSERVE_SOCKET", "TOPOS_OBSERVE_SOCKET_DIR", "TOPOS_OBSERVE_GID",
    ]
    assert lane["argv"][:2] == ["python3", "scripts/run_image_runtime_lane.py"]
    assert (ROOT / lane["argv"][1]).is_file()
    bake = (ROOT / "docker-bake.hcl").read_text(encoding="utf-8")
    for target in (lane["argv"][lane["argv"].index("--native") + 1], lane["argv"][lane["argv"].index("--vsc") + 1]):
        assert f'target "{target}"' in bake
    assert "MDT_RUNTIME_IMAGE_NATIVE" not in " ".join(lane["argv"])


def test_the_runtime_environment_carries_the_private_tags_over_any_inherited_refs() -> None:
    native, vsc = driver.tags_for(4242)
    env = driver.runtime_environ({"MDT_RUNTIME_IMAGE_NATIVE": "stale:latest", "PATH": "/bin"}, native, vsc)
    assert env["MDT_RUNTIME_IMAGE_NATIVE"] == native and env["MDT_RUNTIME_IMAGE_VSC"] == vsc and env["PATH"] == "/bin"
    assert native != vsc and ":latest" not in native + vsc


def test_the_driver_refuses_without_the_gate_tier_and_without_explicit_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit):
        driver.parse_args([])
    monkeypatch.delenv("CGROUP_PARENT_DEV_GATES", raising=False)
    with pytest.raises(SystemExit, match="CGROUP_PARENT_DEV_GATES"):
        driver.main(["--native", "a", "--vsc", "b"])


def test_a_build_that_left_no_layouts_fails_the_lane(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CGROUP_PARENT_DEV_GATES", "dev-gates.slice")
    monkeypatch.setattr(driver.oci, "layout_root", lambda: tmp_path)
    with pytest.raises(SystemExit, match="no image was built"):
        driver.main(["--native", "a", "--vsc", "b"])


def test_the_private_tags_are_removed_by_exact_name_even_when_the_import_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CGROUP_PARENT_DEV_GATES", "dev-gates.slice")
    monkeypatch.setattr(driver.oci, "layout_root", lambda: tmp_path)
    monkeypatch.setattr(driver.oci, "find_layout", lambda root, target: tmp_path / target)

    def boom(layout: Path, tag: str) -> None:
        raise SystemExit("import failed")

    calls: list[list[str]] = []
    monkeypatch.setattr(driver.oci, "import_image", boom)
    monkeypatch.setattr(driver.subprocess, "run", lambda argv, **kw: calls.append(list(argv)) or subprocess.CompletedProcess(argv, 0))
    with pytest.raises(SystemExit, match="import failed"):
        driver.main(["--native", "a", "--vsc", "b"])
    native, vsc = driver.tags_for(driver.os.getpid())
    assert calls == [["docker", "rmi", native], ["docker", "rmi", vsc]]
