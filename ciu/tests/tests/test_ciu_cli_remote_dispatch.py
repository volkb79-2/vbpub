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

import ciu.deploy as REAL_DEPLOY  # captured BEFORE any fixture stubs sys.modules
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
        SimpleNamespace(load_global_config=lambda _root: {},
                        resolve_repo_root=REAL_DEPLOY.resolve_repo_root),
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


# ---------------------------------------------------------------------------
# ciu-P45 / CIU-54: these `--host` branches now resolve repo_root via
# `deploy.resolve_repo_root` (S1.1) instead of a bare `REPO_ROOT`-or-cwd
# fallback that ignored `--define-root` entirely -- and, since it forwarded
# the whole "leftover" argv into the remote command string, an operator who
# DID pass `--define-root` on one of these had it silently shipped to the
# remote host's own `ciu`, a LOCAL path re-parsed in a foreign context.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("verb", ["render", "down", "health"])
def test_remote_verbs_define_root_resolves_and_is_not_forwarded(remote, monkeypatch, verb, tmp_path):
    seen, _ = remote
    monkeypatch.delenv("REPO_ROOT", raising=False)
    assert _run(monkeypatch, [verb, "--host", "web", "--define-root", str(tmp_path)]) == 0
    cfg, argv, config, repo_root, kwargs = seen["exec"][0]
    assert repo_root == tmp_path.resolve()
    assert not any("--define-root" in a for a in argv)


def test_remote_up_host_define_root_resolves_and_is_not_forwarded(remote, monkeypatch, tmp_path):
    seen, _ = remote
    monkeypatch.delenv("REPO_ROOT", raising=False)
    assert _run(monkeypatch, ["up", "--host", "web", "--define-root", str(tmp_path)]) == 0
    assert len(seen["sync"]) == 1
    _cfg, _local, _target, _config, repo_root, _kw = seen["sync"][0]
    assert repo_root == tmp_path.resolve()
    remote_command = seen["exec"][0][1][0]
    assert "--define-root" not in remote_command and str(tmp_path) not in remote_command


def test_remote_verb_refuses_when_repo_root_not_set_and_no_define_root(monkeypatch, capsys):
    """Breaking (CIU-54): previously silently fell back to cwd."""
    monkeypatch.delenv("REPO_ROOT", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "ciu.deploy",
        SimpleNamespace(load_global_config=lambda _root: {},
                        resolve_repo_root=REAL_DEPLOY.resolve_repo_root),
    )
    assert _run(monkeypatch, ["render", "--host", "web"]) == 2
    err = capsys.readouterr().err
    assert "[ERROR]" in err and "REPO_ROOT not set" in err


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
        SimpleNamespace(load_global_config=lambda root: (_ for _ in ()).throw(OSError("unreadable")),
                        resolve_repo_root=REAL_DEPLOY.resolve_repo_root),
    )
    assert _run(monkeypatch, ["render", "--host", "web"]) == 2
    assert seen["exec"] == []
    assert "could not load global configuration for remote operation" in capsys.readouterr().err


def test_resolve_repo_root_deploy_helper_reraises_as_clean_system_exit(monkeypatch, capsys):
    """Unit-level: `cli._resolve_repo_root_deploy` turns ANY ValueError from
    `deploy.resolve_repo_root` into `[ERROR] ...` on stderr + a clean
    `SystemExit(2)` -- the sibling contract to `_resolve_repo_root_cli`'s own
    (test_ciu_dev.py), for the deploy-routed side of CIU-54's 8 sites."""
    monkeypatch.setattr(
        REAL_DEPLOY, "resolve_repo_root",
        lambda *_a, **_kw: (_ for _ in ()).throw(ValueError("boom")),
    )

    with pytest.raises(SystemExit) as exc_info:
        cli._resolve_repo_root_deploy(None)

    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert "[ERROR]" in err and "boom" in err


def test_extract_define_root_does_not_abbreviate(monkeypatch):
    """`_extract_define_root` uses `allow_abbrev=False` on purpose (see its
    own docstring): a short prefix like `--d`/`--r` must fall through
    UNCLAIMED so each site's own parser (or `_parse_layout_argv`'s forbidden-
    flag guard) still gets to resolve it -- only the exact spelling (or its
    `=value` form) is consumed here."""
    define_root, remaining = cli._extract_define_root(["--d", "/x", "--host", "web"])
    assert define_root is None
    assert remaining == ["--d", "/x", "--host", "web"]

    define_root, remaining = cli._extract_define_root(["--define-root", "/x", "--host", "web"])
    assert define_root == Path("/x")
    assert remaining == ["--host", "web"]

    define_root, remaining = cli._extract_define_root(["--root-folder=/y"])
    assert define_root == Path("/y")
    assert remaining == []


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
