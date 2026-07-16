"""
Tests for S8.7 — instance-scoped compose project.

Covers:
  compose_project_name          {project}-{env_tag}-{stack} derivation
  legacy_project_containers     detection of THIS instance's legacy containers
  guard_legacy_compose_project  abort vs CIU_ADOPT_LEGACY_PROJECT=1 migration
  execute_docker_compose_with_logs  -p injection into the compose argv

No real docker calls: procutil.run_cmd and subprocess.Popen are monkeypatched.
"""
from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine  # noqa: E402
from ciu import procutil  # noqa: E402


_CFG = {"deploy": {"project_name": "dstdns", "environment_tag": "98535c"}}


class TestComposeProjectName:
    def test_derivation(self, tmp_path):
        stack = tmp_path / "consul-server"
        stack.mkdir()
        assert engine.compose_project_name(_CFG, stack) == "dstdns-98535c-consul-server"

    def test_missing_project_name_raises(self, tmp_path):
        with pytest.raises(ValueError, match="S8.7"):
            engine.compose_project_name({"deploy": {"environment_tag": "x"}}, tmp_path)

    def test_missing_environment_tag_raises(self, tmp_path):
        with pytest.raises(ValueError, match="S8.7"):
            engine.compose_project_name({"deploy": {"project_name": "x"}}, tmp_path)


def _fake_run_cmd(stdout: str = "", returncode: int = 0, capture: list | None = None):
    def fake(cmd, **kwargs):
        if capture is not None:
            capture.append(list(cmd))
        return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")
    return fake


class TestLegacyProjectContainers:
    def test_detects_own_instance_legacy_containers(self, tmp_path, monkeypatch):
        stack = tmp_path / "consul-server"
        stack.mkdir()
        monkeypatch.setattr(
            engine.procutil, "run_cmd",
            _fake_run_cmd(stdout="dstdns-98535c-consul\nother-abc123-consul\n"),
        )
        found = engine.legacy_project_containers(stack, "dstdns-98535c-consul-server")
        assert found == ["dstdns-98535c-consul"]  # other instance's container ignored

    def test_docker_failure_returns_empty(self, tmp_path, monkeypatch):
        stack = tmp_path / "consul-server"
        stack.mkdir()
        monkeypatch.setattr(engine.procutil, "run_cmd", _fake_run_cmd(returncode=1))
        assert engine.legacy_project_containers(stack, "dstdns-98535c-consul-server") == []

    def test_legacy_equals_expected_short_circuits(self, tmp_path, monkeypatch):
        stack = tmp_path / "same"
        stack.mkdir()
        called = []
        monkeypatch.setattr(engine.procutil, "run_cmd", _fake_run_cmd(capture=called))
        assert engine.legacy_project_containers(stack, "same") == []
        assert called == []  # no docker query needed


class TestGuardLegacyComposeProject:
    def test_no_legacy_containers_passes(self, tmp_path, monkeypatch):
        stack = tmp_path / "consul-server"
        stack.mkdir()
        monkeypatch.setattr(engine.procutil, "run_cmd", _fake_run_cmd(stdout=""))
        engine.guard_legacy_compose_project(stack, "dstdns-98535c-consul-server")

    def test_legacy_containers_raise_with_migration_hint(self, tmp_path, monkeypatch):
        stack = tmp_path / "consul-server"
        stack.mkdir()
        monkeypatch.delenv("CIU_ADOPT_LEGACY_PROJECT", raising=False)
        monkeypatch.setattr(
            engine.procutil, "run_cmd", _fake_run_cmd(stdout="dstdns-98535c-consul\n")
        )
        with pytest.raises(engine.ComposeError, match="CIU_ADOPT_LEGACY_PROJECT"):
            engine.guard_legacy_compose_project(stack, "dstdns-98535c-consul-server")

    def test_adopt_flag_removes_legacy_containers(self, tmp_path, monkeypatch):
        stack = tmp_path / "consul-server"
        stack.mkdir()
        monkeypatch.setenv("CIU_ADOPT_LEGACY_PROJECT", "1")
        calls: list[list[str]] = []
        monkeypatch.setattr(
            engine.procutil, "run_cmd",
            _fake_run_cmd(stdout="dstdns-98535c-consul\n", capture=calls),
        )
        engine.guard_legacy_compose_project(stack, "dstdns-98535c-consul-server")
        assert calls[-1][:3] == ["docker", "rm", "-f"]
        assert "dstdns-98535c-consul" in calls[-1]


class TestComposeUpProjectArg:
    def _run(self, monkeypatch, tmp_path, project):
        captured: dict = {}

        class FakeProc:
            returncode = 0
            stdout = iter(())

            def wait(self):
                return 0

        def fake_popen(cmd, **kwargs):
            captured["cmd"] = list(cmd)
            return FakeProc()

        monkeypatch.setattr(engine.subprocess, "Popen", fake_popen)
        engine.execute_docker_compose_with_logs(
            ["-f", "ciu.compose.yml"], cwd=tmp_path, project=project
        )
        return captured["cmd"]

    def test_project_injected_as_dash_p(self, monkeypatch, tmp_path):
        cmd = self._run(monkeypatch, tmp_path, "dstdns-98535c-consul-server")
        assert cmd[:4] == ["docker", "compose", "-p", "dstdns-98535c-consul-server"]
        assert cmd[-2:] == ["up", "-d"]

    def test_none_project_preserves_legacy_argv(self, monkeypatch, tmp_path):
        cmd = self._run(monkeypatch, tmp_path, None)
        assert cmd[:2] == ["docker", "compose"]
        assert "-p" not in cmd


class TestResetDownProjectScoping:
    """S8.7: reset's compose down is scoped when the naming pair exists."""

    def _reset(self, tmp_path, monkeypatch, config):
        stack = tmp_path / "consul-server"
        stack.mkdir()
        (stack / "ciu.compose.yml").write_text("services: {}\n")
        calls: list[list[str]] = []
        monkeypatch.setattr(
            engine.procutil, "run_cmd", _fake_run_cmd(stdout="", capture=calls)
        )
        engine.reset_service(config, stack)
        return [c for c in calls if c[:2] == ["docker", "compose"]]

    def test_down_scoped_when_pair_present(self, tmp_path, monkeypatch):
        cfg = {"deploy": {"project_name": "dstdns", "environment_tag": "98535c",
                          "labels": {"prefix": "ciu"}}}
        compose_calls = self._reset(tmp_path, monkeypatch, cfg)
        assert compose_calls, "expected a docker compose down call"
        down = compose_calls[0]
        assert down[2:4] == ["-p", "dstdns-98535c-consul-server"]

    def test_down_legacy_when_pair_absent(self, tmp_path, monkeypatch):
        cfg = {"deploy": {"project_name": "dstdns", "labels": {"prefix": "ciu"}}}
        compose_calls = self._reset(tmp_path, monkeypatch, cfg)
        assert compose_calls, "expected a docker compose down call"
        assert "-p" not in compose_calls[0]
