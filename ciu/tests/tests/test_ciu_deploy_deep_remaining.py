"""Deployment cleanup and container-boundary contracts not covered elsewhere.

Docker is a deterministic boundary in this module: these tests exercise the
public action outcomes while replacing only the Docker process itself.  They
protect S7.8's anchored project ownership and S8.5's shipped-stack cleanup
contract without requiring a daemon.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import deploy  # noqa: E402
from ciu.deploy_pkg.profiles import Profile  # noqa: E402


def _config() -> dict:
    return {"deploy": {"project_name": "ciu", "environment_tag": "test", "phases": {}}}


def _profile() -> Profile:
    return Profile(name="ops", phase_keys=None, config=_config())


def test_stop_uses_anchored_ownership_and_one_batched_docker_stop(monkeypatch):
    """Stopping CIU must never stop another project with a matching substring."""
    calls: list[list[str]] = []

    def fake_docker(argv, **_kwargs):
        calls.append(argv)
        if argv[0] == "ps":
            return subprocess.CompletedProcess(
                argv, 0, "ciu-test-api\nother-ciu-test-api\nciu-test-worker\n", ""
            )
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(deploy.procutil, "docker", fake_docker)

    assert deploy.action_stop(_config()) == 0
    assert calls == [
        ["ps", "--filter", "name=ciu-test-", "--format", "{{.Names}}"],
        ["stop", "ciu-test-api", "ciu-test-worker"],
    ]


def test_stop_returns_red_when_docker_cannot_stop_owned_container(monkeypatch, capsys):
    """A Docker stop failure remains a non-zero action verdict, never a warning-only green."""
    monkeypatch.setattr(deploy, "_matching_containers", lambda _cfg: ["ciu-test-api"])
    monkeypatch.setattr(
        deploy.procutil,
        "docker",
        lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 1, "", "permission denied"),
    )

    assert deploy.action_stop(_config()) == 1
    assert "docker stop failed: permission denied" in capsys.readouterr().out


def test_matching_containers_reports_daemon_failure_as_empty_actionable_set(monkeypatch, capsys):
    """An unavailable Docker listing cannot leak arbitrary names into a stop/remove command."""
    monkeypatch.setattr(
        deploy.procutil,
        "docker",
        lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 1, "", "daemon unavailable"),
    )

    assert deploy._matching_containers(_config(), all_states=True) == []
    out = capsys.readouterr().out
    assert "docker ps failed: daemon unavailable" in out


def test_clean_shipped_stack_skips_native_reset_but_enforces_project_cleanup(monkeypatch, tmp_path, capsys):
    """S8.5 shipped stacks have no rendered config yet a successful clean stays green.

    Before this contract, ``clean`` indexed ``rendered[rel]`` for a shipped
    service even though rendering correctly omits it, producing a false red.
    The outer container/volume cleanup is still required and is asserted here.
    """
    stack = tmp_path / "tools" / "status"
    stack.mkdir(parents=True)
    selection = [{"path": "tools/status", "service": {"shipped": True}}]
    cleanup_calls: list[object] = []

    def fake_matching(_cfg, *, all_states=False):
        cleanup_calls.append(("containers", all_states))
        return []

    monkeypatch.setattr(deploy, "_matching_containers", fake_matching)
    # S6.4a seams: no real Docker may leak into this unit test — in
    # tester-unified the label enumeration would fail (no daemon trust) and
    # turn an otherwise-green clean red.
    monkeypatch.setattr(deploy, "_list_stack_project_networks", lambda _projects: [])
    monkeypatch.setattr(deploy, "_workspace_identity_network", lambda _root: "")
    monkeypatch.setattr(deploy, "render_selected_stacks", lambda *_args, **_kw: {})
    monkeypatch.setattr(deploy, "_remove_project_volumes", lambda _cfg=None, **_kw: cleanup_calls.append("volumes") or [])
    monkeypatch.setattr(
        deploy.engine,
        "reset_service",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("shipped stack must not use native reset")),
    )

    assert deploy.action_clean(tmp_path, _profile(), selection, ignore_errors=False) == 0
    assert cleanup_calls == [("containers", True), "volumes", ("containers", True)]
    assert "Skipping CIU-native reset for shipped stack: tools/status" in capsys.readouterr().out
