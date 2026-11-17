"""The vm-lab project as a package: console script, wrapper, QEMU image build, image resolution, environment hints.

No Docker, no QEMU and no network: `docker` is a fake runner or a patched `docker_run` in every test.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from vm_lab import GENERIC_PROFILE, RunnerError
from vm_lab import cli as vm_lab_cli
from vm_lab import core as vm_lab

PROJECT = Path(__file__).resolve().parents[1]
REPO = PROJECT.parent


def completed(argv, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(argv, returncode, stdout, stderr)


class FakeRunner:
    def __init__(self, returncode=0):
        self.calls = []
        self.returncode = returncode

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs))
        return completed(argv, self.returncode)


def build_args(tag=vm_lab.DEFAULT_QEMU_IMAGE_TAG, context=None):
    return SimpleNamespace(tag=tag, context=context)


# --- image build -----------------------------------------------------------------------------------------------------


def test_image_build_runs_docker_build_with_the_default_tag_on_the_project_directory(capsys):
    runner = FakeRunner()
    assert vm_lab.cmd_image_build(GENERIC_PROFILE, build_args(), runner=runner) == 0
    assert runner.calls == [
        (["docker", "build", "--tag", "vm-lab-qemu:local", str(PROJECT / "qemu-image")], {"check": False})
    ]
    assert "built vm-lab-qemu:local" in capsys.readouterr().out


def test_image_build_takes_a_tag_and_a_context(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM scratch\n")
    runner = FakeRunner()
    vm_lab.cmd_image_build(GENERIC_PROFILE, build_args("mine:1", str(tmp_path)), runner=runner)
    assert runner.calls[0][0] == ["docker", "build", "--tag", "mine:1", str(tmp_path)]


def test_image_build_failure_is_an_error_with_the_exit_code():
    with pytest.raises(RunnerError, match=r"docker build failed \(3\): docker build --tag vm-lab-qemu:local"):
        vm_lab.cmd_image_build(GENERIC_PROFILE, build_args(), runner=FakeRunner(returncode=3))


def test_image_build_without_a_dockerfile_names_the_context_and_the_flag(tmp_path):
    runner = FakeRunner()
    with pytest.raises(RunnerError, match=r"no Dockerfile in .*--context"):
        vm_lab.cmd_image_build(GENERIC_PROFILE, build_args(context=str(tmp_path)), runner=runner)
    assert runner.calls == []


def test_the_cli_verb_image_build_reaches_docker_build_with_the_default_tag(monkeypatch):
    runner = FakeRunner()
    monkeypatch.setattr(vm_lab.subprocess, "run", runner)
    assert vm_lab_cli.main(["image", "build"]) == 0
    assert runner.calls[0][0][:4] == ["docker", "build", "--tag", "vm-lab-qemu:local"]
    runner.calls.clear()
    assert vm_lab_cli.main(["image", "build", "--tag", "other:2"]) == 0
    assert runner.calls[0][0][:4] == ["docker", "build", "--tag", "other:2"]


def test_the_dockerfile_is_plain_trixie_with_qemu_and_no_ovmf_or_user_pin():
    text = (PROJECT / "qemu-image" / "Dockerfile").read_text()
    assert "FROM debian:trixie-slim" in text
    assert "qemu-system-x86" in text and "qemu-utils" in text
    instructions = [line for line in text.splitlines() if line and not line.startswith("#")]
    assert not any(line.startswith("USER ") for line in instructions)
    assert not any("ovmf" in line.lower() for line in instructions)


# --- image resolution ------------------------------------------------------------------------------------------------


def fake_docker(monkeypatch, returncode=0, stderr=""):
    calls = []

    def docker_run(argv, *, check=True, timeout=30):
        calls.append(list(argv))
        return completed(argv, returncode, "", stderr)

    monkeypatch.setattr(vm_lab, "docker_run", docker_run)
    return calls


def test_image_resolution_flag_wins_over_everything(monkeypatch):
    calls = fake_docker(monkeypatch)
    assert vm_lab.resolve_qemu_image(GENERIC_PROFILE, "from-flag:1", {"VM_LAB_QEMU_IMAGE": "from-env:1"}) == "from-flag:1"
    assert calls == []


def test_image_resolution_environment_wins_over_the_local_tag(monkeypatch):
    calls = fake_docker(monkeypatch)
    assert vm_lab.resolve_qemu_image(GENERIC_PROFILE, None, {"VM_LAB_QEMU_IMAGE": "from-env:1"}) == "from-env:1"
    assert calls == []


def test_image_resolution_uses_the_local_tag_when_docker_has_it(monkeypatch):
    calls = fake_docker(monkeypatch, returncode=0)
    assert vm_lab.resolve_qemu_image(GENERIC_PROFILE, None, {}) == "vm-lab-qemu:local"
    assert calls == [["docker", "image", "inspect", "vm-lab-qemu:local"]]


def test_image_resolution_error_names_image_build_when_the_tag_is_absent(monkeypatch):
    fake_docker(monkeypatch, returncode=1, stderr="Error response from daemon: No such image: vm-lab-qemu:local")
    with pytest.raises(RunnerError) as caught:
        vm_lab.resolve_qemu_image(GENERIC_PROFILE, None, {})
    message = str(caught.value)
    assert "pass --image or set VM_LAB_QEMU_IMAGE; no image fallback is selected" in message
    assert "vm-lab-qemu:local does not exist" in message
    assert "`vm-lab image build`" in message


def test_an_unreachable_docker_is_not_reported_as_an_unbuilt_image(monkeypatch):
    fake_docker(monkeypatch, returncode=1, stderr="Cannot connect to the Docker daemon at unix:///var/run/docker.sock")
    with pytest.raises(RunnerError, match=r"could not be checked: .*Cannot connect to the Docker daemon") as caught:
        vm_lab.resolve_qemu_image(GENERIC_PROFILE, None, {})
    assert "does not exist" not in str(caught.value)


def test_a_missing_docker_client_is_not_reported_as_an_unbuilt_image(monkeypatch):
    def docker_run(argv, *, check=True, timeout=30):
        raise RunnerError("command failed to run (docker image inspect vm-lab-qemu:local): no docker")

    monkeypatch.setattr(vm_lab, "docker_run", docker_run)
    with pytest.raises(RunnerError, match="could not be checked: command failed to run"):
        vm_lab.resolve_qemu_image(GENERIC_PROFILE, None, {})


def test_a_profile_without_a_default_tag_never_probes_docker_for_one(monkeypatch):
    calls = fake_docker(monkeypatch)
    other = vm_lab.dataclasses.replace(GENERIC_PROFILE, default_image=None, image_env="OTHER_IMAGE")
    with pytest.raises(RunnerError) as caught:
        vm_lab.resolve_qemu_image(other, None, {})
    assert str(caught.value) == "pass --image or set OTHER_IMAGE; no image fallback is selected"
    assert calls == []


def test_the_truenas_front_has_no_local_tag_fallback():
    spec = importlib.util.spec_from_file_location("tcg_vm_project_test", REPO / "scripts" / "truenas" / "tcg_vm.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.PROFILE.default_image is None


# --- environment defaults -------------------------------------------------------------------------------------------


def test_unset_docker_network_says_where_the_value_comes_from(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(vm_lab, "vm_start_lock_path", lambda: tmp_path / "admission.lock")
    monkeypatch.setenv("CGROUP_PARENT_DEV_BACKGROUND", "dev-background.slice")
    monkeypatch.delenv("DOCKER_NETWORK_INTERNAL", raising=False)
    arguments = [
        "start", "--name", "deb1", "--base-disk", "base.qcow2",
        "--ovmf-code", "OVMF_CODE_4M.fd", "--ovmf-vars", "OVMF_VARS_4M.fd",
        "--display-device", "std", "--state-dir", str(tmp_path),
        "--memory-mib", "2048", "--cpus", "2", "--forward", "2222:22",
    ]
    assert vm_lab.main(GENERIC_PROFILE, arguments) == 1
    error = capsys.readouterr().err
    assert "DOCKER_NETWORK_INTERNAL is unset" in error
    assert "`ciu env print`" in error
    assert "ciu stack environment or the devcontainer" in error


# --- console script, wrapper, python -m --------------------------------------------------------------------------------


def test_the_console_script_entry_point_imports_and_is_the_cli_main():
    scripts = tomllib.loads((PROJECT / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts == {"vm-lab": "vm_lab.cli:main"}
    module_name, _, attribute = scripts["vm-lab"].partition(":")
    assert getattr(importlib.import_module(module_name), attribute) is vm_lab_cli.main


def test_the_project_depends_on_vmscreen_and_not_on_cli_extended():
    dependencies = tomllib.loads((PROJECT / "pyproject.toml").read_text())["project"]["dependencies"]
    assert dependencies == ["vmscreen"]


def test_the_wrapper_is_executable_and_runs_the_source_next_to_it():
    wrapper = PROJECT / "vm-lab"
    assert wrapper.is_file() and os.access(wrapper, os.X_OK)
    text = wrapper.read_text()
    assert text.startswith("#!/bin/sh")
    assert 'PYTHONPATH="$here/src' in text
    assert "python3 -m vm_lab" in text
    assert (PROJECT / "src" / "vm_lab" / "__main__.py").is_file()


def test_the_wrapper_runs_help_from_a_bare_checkout():
    result = subprocess.run(
        [str(PROJECT / "vm-lab"), "--help"], text=True, capture_output=True, check=False, cwd="/",
        env={**os.environ, "PYTHONPATH": ""},
    )
    assert result.returncode == 0, result.stderr
    assert "image" in result.stdout


def test_python_dash_m_help_lists_the_verbs():
    result = subprocess.run(
        [sys.executable, "-m", "vm_lab", "--help"], text=True, capture_output=True, check=False, cwd="/",
        env={**os.environ, "PYTHONPATH": str(PROJECT / "src")},
    )
    assert result.returncode == 0, result.stderr
    for verb in ("start", "stop", "status", "destroy", "image", "disk", "console"):
        assert verb in result.stdout, verb


# --- documentation ---------------------------------------------------------------------------------------------------


def test_the_user_documents_carry_the_install_forms_and_the_path_recipes():
    readme = (PROJECT / "README.md").read_text()
    consumers = (PROJECT / "docs" / "CONSUMERS.md").read_text()
    design = (PROJECT / "docs" / "DESIGN-GUIDE.md").read_text()
    for phrase in ("vm-lab image build", "vm-lab/vm-lab", "vmscreen", "VM_LAB_QEMU_IMAGE", "vm-lab-qemu:local"):
        assert phrase in readme, phrase
    for phrase in (
        "libraries/cowtree/repo-path vm-lab --project scripts/debian-rollback",
        "libraries/cowtree/repo-path vm-images",
        "pip install",
        "vm-lab image build",
    ):
        assert phrase in consumers, phrase
    assert "qemu-image" in design
    assert "scripts/vm-lab" not in readme + consumers + design
