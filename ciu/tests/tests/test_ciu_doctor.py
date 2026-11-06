"""``ciu doctor`` checks (AC-20): every branch of every environment probe."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import doctor


class _Done:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_probe_returns_the_first_output_line(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Done(0, "29.1\nsecond\n"))
    assert doctor._probe(["docker", "version"]) == (0, "29.1")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Done(3, "", "boom\nmore"))
    assert doctor._probe(["x"]) == (3, "boom")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Done(0, "", ""))
    assert doctor._probe(["x"]) == (0, "")


def test_probe_maps_a_timeout_and_a_missing_binary_to_failure_codes(monkeypatch):
    def timeout(*_a, **_k):
        raise subprocess.TimeoutExpired("docker", 20)

    monkeypatch.setattr(subprocess, "run", timeout)
    code, line = doctor._probe(["docker", "version"])
    assert code == 124 and "timed out" in line

    def missing(*_a, **_k):
        raise FileNotFoundError("no docker")

    monkeypatch.setattr(subprocess, "run", missing)
    assert doctor._probe(["docker"]) == (127, "no docker")


def test_docker_check_all_three_outcomes(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda _n: None)
    assert doctor._docker(None, None).status == "fail"

    monkeypatch.setattr(doctor.shutil, "which", lambda _n: "/usr/bin/docker")
    monkeypatch.setattr(doctor, "_probe", lambda _a: (1, "permission denied"))
    down = doctor._docker(None, None)
    assert down.status == "fail" and down.lines == ("permission denied",)
    monkeypatch.setattr(doctor, "_probe", lambda _a: (1, ""))
    assert doctor._docker(None, None).lines == ()

    monkeypatch.setattr(doctor, "_probe", lambda _a: (0, "29.1"))
    ok = doctor._docker(None, None)
    assert ok.status == "ok" and ok.details["server"] == "29.1"


@pytest.mark.parametrize("probe", ["_compose", "_buildx"])
def test_plugin_checks_report_missing_and_present(monkeypatch, probe):
    check = getattr(doctor, probe)
    monkeypatch.setattr(doctor, "_probe", lambda _a: (1, "unknown command"))
    missing = check(None, None)
    assert missing.status == ("fail" if probe == "_compose" else "warn")
    assert missing.lines == ("unknown command",)
    monkeypatch.setattr(doctor, "_probe", lambda _a: (1, ""))
    assert check(None, None).lines == ()
    monkeypatch.setattr(doctor, "_probe", lambda _a: (0, "v2"))
    assert check(None, None).status == "ok"
    monkeypatch.setattr(doctor, "_probe", lambda _a: (0, ""))
    assert check(None, None).status == "ok"


def test_compose_check_reports_the_compose_version(monkeypatch):
    monkeypatch.setattr(doctor, "_probe", lambda _argv: (0, "v5.7.0"))

    check = doctor._compose(None, None)

    assert check.status == "ok"
    assert check.summary == "docker compose v5.7.0"
    assert check.details == {"version": "v5.7.0"}


def test_git_and_ssh_checks(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda _n: None)
    assert doctor._git(None, None).status == "fail"
    ssh = doctor._ssh(None, None)
    assert ssh.status == "warn" and ssh.details["missing"] == ["ssh", "ssh-keygen", "ssh-keyscan"]
    monkeypatch.setattr(doctor.shutil, "which", lambda n: f"/bin/{n}")
    assert doctor._git(None, None).status == "ok"
    assert doctor._ssh(None, None).status == "ok"


def test_repo_root_check_walks_up_to_the_marker(monkeypatch, tmp_path):
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    assert doctor._repo_root(None, None).status == "warn"
    (tmp_path / "ciu.global.defaults.toml.j2").write_text("", encoding="utf-8")
    found = doctor._repo_root(None, None)
    assert found.status == "ok" and found.details["root"] == str(tmp_path.resolve())


def test_every_check_is_named_for_the_library_verb():
    names = [check.name for check in doctor.doctor_checks()]
    assert names == ["docker", "compose", "buildx", "git", "ssh", "repo-root"]
