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


LOADED = "LoadState=loaded\nFragmentPath=/etc/systemd/system/x.slice\n"
TRANSIENT = "LoadState=loaded\nFragmentPath=\n"


class _Docker:
    """Records every subprocess call in order and answers the host probe.

    ``systemctl_stdout`` answers ``systemctl show`` for the BUILD slice and
    ``parent_stdout`` for the probe parent; ``probe_error`` makes probing the
    build slice raise.
    """

    def __init__(
        self, systemctl_stdout: str = "", *, probe_error: Exception | None = None,
        parent_stdout: str = LOADED, parent_error: Exception | None = None,
    ):
        self.calls: list[list[str]] = []
        self.systemctl_stdout = systemctl_stdout
        self.probe_error = probe_error
        self.parent_stdout = parent_stdout
        self.parent_error = parent_error

    def __call__(self, argv, **_kwargs):
        argv = list(argv)
        self.calls.append(argv)
        if "systemctl" in argv:
            asked = argv[argv.index("show") + 1]
            if asked == PROBE_PARENT:
                if self.parent_error is not None:
                    raise self.parent_error
                return SimpleNamespace(returncode=0, stdout=self.parent_stdout, stderr="")
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

    assert len(docker.probes) == 2 and len(docker.builder_runs) == 1
    assert docker.calls[:2] == docker.probes
    # First the probe parent is checked with tester-gate's self-check contract
    # (a short read-only probe placed in the slice it asks about) ...
    self_check, probe = docker.probes
    assert self_check[self_check.index("show") + 1] == PROBE_PARENT
    assert "--cgroup-parent=dev-gates.slice" in self_check
    # ... and only then does the verified parent host the probe of the BUILD
    # slice, which asks the HOST systemd, runs the declared pinned image, and
    # is never placed in the slice being verified.
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
    assert docker.builder_runs == []  # nothing but the probes ever started
    assert all("systemctl" in call for call in docker.calls)
    assert len(docker.probes) == 2  # the build slice was asked about, after its parent
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


@pytest.mark.parametrize(
    ("kwargs", "named"),
    [
        ({"parent_stdout": TRANSIENT}, "TRANSIENT"),
        ({"parent_stdout": "LoadState=not-found\nFragmentPath=\n"}, "not installed"),
        ({"parent_stdout": ""}, "could not determine"),
        ({"parent_error": subprocess.TimeoutExpired("docker", 30)}, "could not probe"),
    ],
)
def test_an_unverified_probe_parent_refuses_naming_the_gates_input_and_starts_no_builder(
    project, monkeypatch, capsys, kwargs, named,
):
    # The build slice itself is perfectly good: only the gates tier is a typo.
    docker = _Docker(LOADED, **kwargs)
    monkeypatch.setattr(subprocess, "run", docker)

    with pytest.raises(SystemExit) as refused:
        _run(project)

    assert refused.value.code == exit_codes.PREREQ_MISSING
    assert docker.builder_runs == []
    assert len(docker.probes) == 1  # the build slice was never probed from an unverified parent
    assert (project / "dist" / "stale-1.0.0-py3-none-any.whl").exists()  # output preserved
    message = capsys.readouterr().err
    assert "CMRU_TESTER_CGROUP_PARENT" in message and PROBE_PARENT in message and named in message


def test_probe_inputs_declared_only_in_the_enclosing_config_are_found_and_used(
    tmp_path, monkeypatch,
):
    """No environment value: both inputs come from the registered project's [env].

    The regression this pins: the config lookup used to return "" for every declared
    value (its loop variable shadowed the key), so a correctly declared estate refused
    with "is required" while the same values in the environment worked.
    """
    from tests.test_cli_dispatch import _valid_config

    _valid_config(
        tmp_path,
        project_append=(
            "\n[env]\n"
            f'CMRU_TESTER_CGROUP_PROBE_IMAGE = "{PROBE_IMAGE}"\n'
            f'CMRU_TESTER_CGROUP_PARENT = "{PROBE_PARENT}"\n'
        ),
    )
    for name in ("CMRU_TESTER_CGROUP_PROBE_IMAGE", "CMRU_TESTER_CGROUP_PARENT"):
        monkeypatch.delenv(name, raising=False)
    nested = tmp_path / "alpha" / "src"
    nested.mkdir(parents=True)
    docker = _Docker("LoadState=loaded\nFragmentPath=/etc/systemd/system/dev-background.slice\n")
    monkeypatch.setattr(subprocess, "run", docker)
    monkeypatch.setattr(handlers.shutil, "which", lambda _name: "/usr/bin/docker")

    handlers.require_installed_cgroup_parent(BUILD_SLICE, nested)  # must not refuse

    self_check, probe = docker.probes
    assert self_check[self_check.index("show") + 1] == PROBE_PARENT
    assert PROBE_IMAGE in probe and probe[probe.index("show") + 1] == BUILD_SLICE
    assert "--cgroup-parent=dev-gates.slice" in probe


def _config_declaring(tmp_path, value: str) -> Path:
    """A registered project whose own [env] declares CMRU_TESTER_CGROUP_PARENT; returns a nested cwd."""
    from tests.test_cli_dispatch import _valid_config

    _valid_config(
        tmp_path, project_append=f'\n[env]\nCMRU_TESTER_CGROUP_PARENT = "{value}"\n',
    )
    nested = tmp_path / "alpha" / "src"
    nested.mkdir(parents=True)
    return nested


def test_a_declared_input_with_no_environment_value_is_read_from_the_enclosing_config(
    tmp_path, monkeypatch,
):
    """The env key is ABSENT and the config value is PRESENT: the config answers (handlers.py:411)."""
    nested = _config_declaring(tmp_path, " from-config.slice ")
    monkeypatch.delenv("CMRU_TESTER_CGROUP_PARENT", raising=False)

    assert handlers._declared_input(nested, "CMRU_TESTER_CGROUP_PARENT") == "from-config.slice"


@pytest.mark.parametrize(
    ("environment_value", "expected"),
    [(" from-env.slice ", "from-env.slice"), ("", "")],
    ids=["explicit-value-wins", "explicit-empty-value-still-wins"],
)
def test_a_declared_input_set_in_the_environment_wins_over_the_config(
    tmp_path, monkeypatch, environment_value, expected,
):
    """Precedence is unchanged: a PRESENT env key answers (stripped), even empty, without reading config."""
    nested = _config_declaring(tmp_path, "from-config.slice")
    monkeypatch.setenv("CMRU_TESTER_CGROUP_PARENT", environment_value)
    monkeypatch.setattr(
        handlers, "_configured_env_value",
        lambda *_a: pytest.fail("the config must not be read when the environment declares the key"),
    )

    assert handlers._declared_input(nested, "CMRU_TESTER_CGROUP_PARENT") == expected


def test_a_gates_tier_declared_from_an_unset_variable_refuses_before_any_container(
    project, tmp_path, monkeypatch, capsys,
):
    """The estate declares ``CMRU_TESTER_CGROUP_PARENT = "${CGROUP_PARENT_DEV_GATES}"``.

    With the variable unset there is no value to probe from and none is invented: the
    config load itself refuses, naming the variable, before ANY docker call, and nothing
    already built is deleted.
    """
    from tests.test_cli_dispatch import _valid_config

    _valid_config(
        tmp_path,
        project_append='\n[env]\nCMRU_TESTER_CGROUP_PARENT = "${CGROUP_PARENT_DEV_GATES}"\n',
    )
    alpha = tmp_path / "alpha"
    (alpha / "dist").mkdir()
    stale = alpha / "dist" / "stale-1.0.0-py3-none-any.whl"
    stale.write_bytes(b"stale")
    monkeypatch.delenv("CMRU_TESTER_CGROUP_PARENT")
    monkeypatch.delenv("CGROUP_PARENT_DEV_GATES", raising=False)
    docker = _Docker(LOADED)
    monkeypatch.setattr(subprocess, "run", docker)

    with pytest.raises(SystemExit) as refused:
        _run(alpha)

    assert refused.value.code == exit_codes.CONFIG_ERROR
    assert docker.calls == []
    assert stale.exists()
    assert "CGROUP_PARENT_DEV_GATES" in capsys.readouterr().err
