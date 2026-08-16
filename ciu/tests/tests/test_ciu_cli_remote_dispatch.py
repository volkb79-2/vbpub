"""Remote CLI dispatch contracts: argv safety and fail-closed orchestration.

All transport and activation boundaries are faked.  These tests deliberately
exercise ``cli.main`` because command construction is a security boundary: the
remote login shell parses the one command string handed to SSH.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import cli


@pytest.fixture
def remote(monkeypatch, tmp_path):
    """Install deterministic inventory/transport fakes and select a repo root."""
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    # Remote command construction is the subject here; give it a successful
    # config-load seam rather than relying on an unrelated on-disk fixture.
    monkeypatch.setitem(
        sys.modules,
        "ciu.deploy",
        SimpleNamespace(load_global_config=lambda _root: {}),
    )
    host = {"ssh_host": "web", "ssh_key": "/key", "known_host": "pinned"}
    seen = {"hosts": [], "exec": [], "sync": []}

    import ciu.hosts as hosts
    import ciu.transport_ssh as transport

    monkeypatch.setattr(hosts, "get_host",
                        lambda root, name, admin=False: seen["hosts"].append((root, name, admin)) or host)
    monkeypatch.setattr(transport, "ssh_exec",
                        lambda cfg, argv, *, config, repo_root, **kwargs:
                        seen["exec"].append((cfg, argv, config, repo_root, kwargs)) or 0)
    monkeypatch.setattr(transport, "ssh_sync",
                        lambda cfg, local, target, *, config, repo_root, **kwargs:
                        seen["sync"].append((cfg, local, target, config, repo_root, kwargs)) or 0)
    return seen, host


def _run(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["ciu", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return exc.value.code


@pytest.mark.parametrize(
    ("verb", "args", "expected"),
    [
        ("render", ["--host", "web", "--profile", "a b"], "ciu render --profile 'a b'"),
        ("down", ["--host", "web", "--profile", "a b"], "ciu down --profile 'a b'"),
        ("health", ["--host", "web", "--profile", "a b"], "ciu health --profile 'a b'"),
    ],
)
def test_remote_verbs_preserve_selection_as_one_shell_argument(remote, monkeypatch, verb, args, expected):
    """A selection value cannot terminate the remote command and inject another one."""
    seen, _ = remote
    assert _run(monkeypatch, [verb, *args]) == 0
    assert seen["exec"][0][1] == [expected]


def test_remote_up_sync_failure_short_circuits_remote_execution(remote, monkeypatch):
    """A failed bundle transfer must never run a possibly stale remote tree."""
    seen, _ = remote
    import ciu.transport_ssh as transport
    monkeypatch.setattr(transport, "ssh_sync", lambda *args, **kwargs: 23)
    assert _run(monkeypatch, ["up", "--host", "web"]) == 23
    assert seen["exec"] == []


def test_remote_up_quotes_bundle_and_selection_values(remote, monkeypatch):
    """Inventory paths and CLI values are data, not remote-shell syntax."""
    seen, host = remote
    host["bundle_dir"] = "/srv/ciu current; touch SHOULD_NOT_RUN"
    assert _run(monkeypatch, ["up", "--host", "web", "--profile", "apps; id"]) == 0
    assert len(seen["sync"]) == 1
    remote_command = seen["exec"][0][1]
    assert remote_command == [
        "cd '/srv/ciu current; touch SHOULD_NOT_RUN' && ciu env generate && "
        "ciu render && ciu up --profile 'apps; id'"
    ]


def test_remote_config_load_failure_stops_before_opening_transport(remote, monkeypatch, capsys):
    """A remote operation never downgrades an unreadable config to `{}`."""
    seen, _ = remote
    # Supply only the import seam that CLI needs.  This keeps the contract test
    # independent of deploy's unrelated runtime closure.
    monkeypatch.setitem(
        sys.modules,
        "ciu.deploy",
        SimpleNamespace(load_global_config=lambda root: (_ for _ in ()).throw(OSError("unreadable"))),
    )
    assert _run(monkeypatch, ["render", "--host", "web"]) == 2
    assert seen["exec"] == []
    assert "could not load global configuration for remote operation" in capsys.readouterr().err


def test_thin_health_passes_selection_to_activation_without_push(remote, monkeypatch):
    """Thin health is the activation contract's health verb and never synchronizes."""
    seen, host = remote
    host["bundle_dir"] = "/opt/app"
    import ciu.activate as activate
    captured = []
    monkeypatch.setattr(activate, "run_activation",
                        lambda cfg, verb, *, config, repo_root, bundle_dir, remaining=None:
                        captured.append((cfg, verb, config, repo_root, bundle_dir, remaining)) or 0)
    assert _run(monkeypatch, ["health", "--host", "web", "--thin", "--profile", "apps"]) == 0
    assert seen["sync"] == []
    assert len(captured) == 1
    cfg, verb, config, repo_root, bundle_dir, remaining = captured[0]
    assert (cfg, verb, config, repo_root, bundle_dir, remaining) == (
        host, "health", {}, seen["hosts"][0][0], "/opt/app", ["--profile", "apps"],
    )


def test_thin_validation_rejects_conflicting_or_docker_only_flags_before_activation(remote, monkeypatch, capsys):
    """Bootstrap/rollback selection is unambiguous and cannot leak into activation."""
    seen, _ = remote
    import ciu.activate as activate
    monkeypatch.setattr(activate, "run_thin_up", lambda *args, **kwargs: pytest.fail("must not activate"))
    assert _run(monkeypatch, ["up", "--host", "web", "--thin", "--bootstrap", "--rollback"]) == 2
    assert "mutually exclusive" in capsys.readouterr().err
    assert _run(monkeypatch, ["up", "--host", "web", "--bootstrap"]) == 2
    assert "require --thin" in capsys.readouterr().err
    assert seen["exec"] == [] and seen["sync"] == []
