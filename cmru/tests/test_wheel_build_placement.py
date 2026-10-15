"""Wheel-build placement is proven against the host's systemd BEFORE any build.

A typo'd ``--cgroup-parent`` is not an error to Docker/systemd: systemd
auto-vivifies an unlimited transient slice and the container starts. The
handler must therefore run CMRU's host-probe contract first and refuse
without starting any wheel-builder workload.
"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import exit_codes, handlers

PROBE_IMAGE = "debian@sha256:" + "d" * 64
PROBE_PARENT = "dev-gates.slice"
BUILD_SLICE = "dev-background.slice"


class _Docker:
    """Records every subprocess call in order and answers the host probe."""

    def __init__(self, systemctl_stdout: str = "", *, probe_error: Exception | None = None):
        self.calls: list[list[str]] = []
        self.systemctl_stdout = systemctl_stdout
        self.probe_error = probe_error

    def __call__(self, argv, **_kwargs):
        argv = list(argv)
        self.calls.append(argv)
        if "systemctl" in argv:
            if self.probe_error is not None:
                raise self.probe_error
            return SimpleNamespace(returncode=0, stdout=self.systemctl_stdout, stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    @property
    def probes(self) -> list[list[str]]:
        return [call for call in self.calls if "systemctl" in call]

    @property
    def builder_runs(self) -> list[list[str]]:
        return [call for call in self.calls if "/opt/wheel-builder-venv/bin/python" in call]


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "cmru"
    (root / "dist").mkdir(parents=True)
    (root / "dist" / "stale-1.0.0-py3-none-any.whl").write_bytes(b"stale")
    for name, value in (
        (handlers._WHEEL_BUILDER_IMAGE_ENV, "wheel-builder:local"),
        (handlers._DOCKER_CGROUP_PARENT_ENV, BUILD_SLICE),
        ("CMRU_TESTER_CGROUP_PROBE_IMAGE", PROBE_IMAGE),
        ("CMRU_TESTER_CGROUP_PARENT", PROBE_PARENT),
    ):
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(handlers.shutil, "which", lambda _name: "/usr/bin/docker")
    monkeypatch.setattr(handlers, "_git_common_dir", lambda _cwd: tmp_path / ".git")
    monkeypatch.setattr(handlers, "_host_bind_source", lambda path: f"/host{path}")
    monkeypatch.setattr(handlers, "_wheel_builder_git_mount_args", lambda _src, **_kw: [])
    monkeypatch.setattr(handlers, "_git_toplevel", lambda _cwd: None)
    return root


def _run(project: Path) -> None:
    handlers.cmd_wheel_build(argparse.Namespace(cwd=str(project)))


def test_a_verified_host_slice_probes_first_and_then_builds(project, monkeypatch):
    docker = _Docker("LoadState=loaded\nFragmentPath=/etc/systemd/system/dev-background.slice\n")
    monkeypatch.setattr(subprocess, "run", docker)

    _run(project)

    assert len(docker.probes) == 1 and len(docker.builder_runs) == 1
    # The probe is first, asks the HOST systemd about the build slice, runs the
    # declared pinned image, and is itself placed in the declared gates tier —
    # never in the slice being verified.
    assert docker.calls[0] is docker.probes[0]
    probe = docker.probes[0]
    assert "--cgroup-parent=dev-gates.slice" in probe
    assert PROBE_IMAGE in probe and "--pull=never" in probe
    assert probe[probe.index("show") + 1] == BUILD_SLICE
    builder = docker.builder_runs[0]
    assert builder[builder.index("--cgroup-parent") + 1] == BUILD_SLICE
    assert not (project / "dist" / "stale-1.0.0-py3-none-any.whl").exists()


@pytest.mark.parametrize(
    ("stdout", "named"),
    [
        # systemd auto-vivified a typo'd name: loaded, but no on-disk unit.
        ("LoadState=loaded\nFragmentPath=\n", "TRANSIENT"),
        ("LoadState=not-found\nFragmentPath=\n", "not installed"),
        # The probe produced nothing usable: indeterminate is a refusal too.
        ("", "could not determine"),
    ],
)
def test_a_typo_or_unproven_slice_refuses_before_any_builder_workload(
    project, monkeypatch, capsys, stdout, named,
):
    docker = _Docker(stdout)
    monkeypatch.setattr(subprocess, "run", docker)

    with pytest.raises(SystemExit) as refused:
        _run(project)

    assert refused.value.code == exit_codes.PREREQ_MISSING
    assert docker.builder_runs == []  # nothing but the probe ever started
    assert all("systemctl" in call for call in docker.calls)
    assert (project / "dist" / "stale-1.0.0-py3-none-any.whl").exists()  # nothing deleted
    message = capsys.readouterr().err
    assert BUILD_SLICE in message and named in message


def test_an_unavailable_probe_refuses_and_names_the_slice(project, monkeypatch, capsys):
    docker = _Docker(probe_error=subprocess.TimeoutExpired("docker", 30))
    monkeypatch.setattr(subprocess, "run", docker)

    with pytest.raises(SystemExit) as refused:
        _run(project)

    assert refused.value.code == exit_codes.PREREQ_MISSING
    assert docker.builder_runs == []
    assert f"{BUILD_SLICE!r}" in capsys.readouterr().err


@pytest.mark.parametrize(
    "missing", ["CMRU_TESTER_CGROUP_PROBE_IMAGE", "CMRU_TESTER_CGROUP_PARENT"],
)
def test_missing_probe_inputs_refuse_without_starting_any_container(
    project, monkeypatch, capsys, missing,
):
    docker = _Docker("LoadState=loaded\nFragmentPath=/x\n")
    monkeypatch.setattr(subprocess, "run", docker)
    monkeypatch.setenv(missing, "")

    with pytest.raises(SystemExit) as refused:
        _run(project)

    assert refused.value.code == exit_codes.PREREQ_MISSING
    assert docker.calls == []
    assert missing in capsys.readouterr().err


def test_an_unpinned_probe_image_refuses_before_any_container(project, monkeypatch):
    docker = _Docker("LoadState=loaded\nFragmentPath=/x\n")
    monkeypatch.setattr(subprocess, "run", docker)
    monkeypatch.setenv("CMRU_TESTER_CGROUP_PROBE_IMAGE", "debian:trixie-slim")

    with pytest.raises(SystemExit, match="not digest-pinned"):
        _run(project)

    assert docker.calls == []
