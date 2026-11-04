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
    (tmp_path / "ciu.global.defaults.toml.j2").write_text("", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    # Remote command construction is the subject here; give it a successful
    # config-load seam rather than relying on an unrelated on-disk fixture.
    monkeypatch.setitem(
        sys.modules,
        "ciu.deploy",
        SimpleNamespace(load_global_config=lambda _root, **_kw: {},
                        resolve_repo_root=REAL_DEPLOY.resolve_repo_root),
    )
    host = {"ssh_host": "web", "ssh_key": "/key", "known_host": "pinned"}
    seen = {"hosts": [], "exec": [], "sync": [], "preflight": []}

    import ciu.hosts as hosts
    import ciu.transport_ssh as transport

    monkeypatch.setattr(hosts, "get_host",
                        lambda root, name, admin=False: seen["hosts"].append((root, name, admin)) or host)
    def fake_exec(cfg, argv, *, config, repo_root, **kwargs):
        # The CIU-169 preflight is one `command -v` probe before the push; keep it
        # out of the command log so each test reads its own remote command.
        if argv and argv[0].startswith("command -v"):
            seen["preflight"].append((cfg, argv))
            return seen.get("preflight_rc", 0)
        seen["exec"].append((cfg, argv, config, repo_root, kwargs))
        return 0

    monkeypatch.setattr(transport, "ssh_exec", fake_exec)
    monkeypatch.setattr(transport, "ssh_sync",
                        lambda cfg, local, target, *, config, repo_root, **kwargs:
                        seen["sync"].append((cfg, local, target, config, repo_root, kwargs)) or 0)
    return seen, host


def _run(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["ciu", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return exc.value.code


# CIU-170: every post-push remote verb runs inside the bundle with its ciu.env exported.
BUNDLE = 'cd /opt/ciu/current && eval "$(ciu env print)" && '


@pytest.mark.parametrize(
    ("verb", "args", "expected"),
    [
        (["render"], ["--host", "web", "--profile", "a b"], f"{BUNDLE}ciu render --profile 'a b'"),
        (["down"], ["--host", "web", "--profile", "a b"], f"{BUNDLE}ciu down --profile 'a b'"),
        (["status", "--gate"], ["--host", "web", "--profile", "a b"],
         f"{BUNDLE}ciu status --gate --profile 'a b'"),
    ],
)
def test_remote_verbs_preserve_selection_as_one_shell_argument(remote, monkeypatch, verb, args, expected):
    """A selection value cannot terminate the remote command and inject another one."""
    seen, _ = remote
    assert _run(monkeypatch, [*verb, *args]) == 0
    assert seen["exec"][0][1] == [expected]


# ---------------------------------------------------------------------------
# ciu-P45 / CIU-54: these `--host` branches now resolve repo_root via
# `deploy.resolve_repo_root` (S1.1) instead of a bare `REPO_ROOT`-or-cwd
# fallback that ignored `--root-folder` entirely -- and, since it forwarded
# the whole "leftover" argv into the remote command string, an operator who
# DID pass `--root-folder` on one of these had it silently shipped to the
# remote host's own `ciu`, a LOCAL path re-parsed in a foreign context.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("verb", [["render"], ["down"], ["status", "--gate"]])
def test_remote_verbs_define_root_resolves_and_is_not_forwarded(remote, monkeypatch, verb, tmp_path):
    seen, _ = remote
    monkeypatch.delenv("REPO_ROOT", raising=False)
    assert _run(monkeypatch, [*verb, "--host", "web", "--root-folder", str(tmp_path)]) == 0
    cfg, argv, config, repo_root, kwargs = seen["exec"][0]
    assert repo_root == tmp_path.resolve()
    assert not any("--root-folder" in a for a in argv)


def test_remote_up_host_define_root_resolves_and_is_not_forwarded(remote, monkeypatch, tmp_path):
    seen, _ = remote
    monkeypatch.delenv("REPO_ROOT", raising=False)
    assert _run(monkeypatch, ["up", "--host", "web", "--root-folder", str(tmp_path)]) == 0
    assert len(seen["sync"]) == 1
    _cfg, _local, _target, _config, repo_root, _kw = seen["sync"][0]
    assert repo_root == tmp_path.resolve()
    remote_command = seen["exec"][0][1][0]
    assert "--root-folder" not in remote_command and str(tmp_path) not in remote_command


def test_remote_verb_refuses_when_repo_root_not_set_and_no_define_root(monkeypatch, capsys):
    """Breaking (CIU-54): previously silently fell back to cwd."""
    monkeypatch.delenv("REPO_ROOT", raising=False)
    monkeypatch.setitem(
        sys.modules,
        "ciu.deploy",
        SimpleNamespace(load_global_config=lambda _root, **_kw: {},
                        resolve_repo_root=REAL_DEPLOY.resolve_repo_root),
    )
    assert _run(monkeypatch, ["render", "--host", "web"]) == 2
    err = capsys.readouterr().err
    assert "[ERROR]" in err and "no-ciu-root" in err


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


def test_ssh_host_lookup_failure_is_a_clean_cli_error(remote, monkeypatch, capsys):
    """Host inventory errors are reported before any SSH transport starts."""
    seen, _ = remote
    import ciu.hosts as hosts

    monkeypatch.setattr(
        hosts,
        "get_host",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad host")),
    )

    assert _run(monkeypatch, ["ssh", "web"]) == 2
    assert seen["exec"] == []
    from ciu.cli_utils import cli_headline

    assert capsys.readouterr().err.splitlines() == [
        cli_headline(),
        "[ERROR] bad host",
    ]


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

    define_root, remaining = cli._extract_define_root(["--root-folder", "/x", "--host", "web"])
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
    assert _run(monkeypatch, ["status", "--gate", "--host", "web", "--thin", "--profile", "apps"]) == 0
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
    assert "not allowed with" in capsys.readouterr().err
    assert _run(monkeypatch, ["up", "--host", "web", "--bootstrap"]) == 2
    assert "--bootstrap requires --thin" in capsys.readouterr().err
    assert seen["exec"] == [] and seen["sync"] == []


# ---------------------------------------------------------------------------
# CIU-168..CIU-171: the push-deploy preconditions and the remote verb chain.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("verb", "expected"),
    [
        (["down"], f"{BUNDLE}ciu down --dir whoami"),
        (["status", "--gate"], f"{BUNDLE}ciu status --gate --dir whoami"),
    ],
)
def test_remote_down_and_health_accept_dir_and_run_in_the_bundle(remote, monkeypatch, verb, expected):
    """CIU-170: `--dir` is accepted after `--host` and forwarded inside the bundle prefix."""
    seen, _ = remote
    assert _run(monkeypatch, [*verb, "--host", "web", "--dir", "whoami"]) == 0
    assert seen["exec"][0][1] == [expected]


def test_remote_up_accepts_dir_with_host(remote, monkeypatch):
    """CIU-170: `up --host H --dir STACK` (SPEC S14.2) is not a mode conflict."""
    seen, _ = remote
    assert _run(monkeypatch, ["up", "--host", "web", "--dir", "whoami"]) == 0
    assert seen["exec"][0][1][0].endswith("ciu up --dir whoami")


def test_remote_ciu_host_key_replaces_the_bare_ciu_everywhere(remote, monkeypatch):
    """CIU-169: `remote_ciu` is used by the probe, the env chain and every verb."""
    seen, host = remote
    host["remote_ciu"] = "/opt/venv/bin/ciu"
    assert _run(monkeypatch, ["up", "--host", "web"]) == 0
    assert seen["preflight"][0][1][0].startswith("command -v /opt/venv/bin/ciu ")
    up = seen["exec"][0][1][0]
    assert "/opt/venv/bin/ciu env generate && /opt/venv/bin/ciu render && /opt/venv/bin/ciu up" in up
    assert _run(monkeypatch, ["down", "--host", "web"]) == 0
    assert seen["exec"][1][1] == [
        'cd /opt/ciu/current && eval "$(/opt/venv/bin/ciu env print)" && /opt/venv/bin/ciu down'
    ]


def test_remote_preflight_creates_a_nested_bundle_dir(remote, monkeypatch):
    """CIU-169: rsync makes only the last path element, so the probe mkdir -p's it first."""
    seen, host = remote
    host["bundle_dir"] = "/home/runner/ciu-remote-test/current"
    assert _run(monkeypatch, ["up", "--host", "web"]) == 0
    probe = seen["preflight"][0][1][0]
    assert probe.endswith("mkdir -p /home/runner/ciu-remote-test/current || exit 126")
    assert len(seen["sync"]) == 1


def test_remote_preflight_names_the_fix_when_ciu_is_not_on_the_path(remote, monkeypatch, capsys):
    """CIU-169: exit 127 from the probe stops before any sync and names both remedies."""
    seen, _ = remote
    import ciu.transport_ssh as transport
    monkeypatch.setattr(transport, "ssh_exec", lambda cfg, argv, **kw: 127)
    assert _run(monkeypatch, ["up", "--host", "web"]) == 127
    err = capsys.readouterr().err
    assert "'ciu' is not on the remote non-login PATH" in err
    assert "/usr/local/bin" in err and 'remote_ciu = "/absolute/path/to/ciu"' in err
    assert seen["sync"] == []


def test_remote_preflight_names_the_unwritable_bundle_dir(remote, monkeypatch, capsys):
    """CIU-169: exit 126 means the bundle directory cannot be created."""
    seen, _ = remote
    import ciu.transport_ssh as transport
    monkeypatch.setattr(transport, "ssh_exec", lambda cfg, argv, **kw: 126)
    assert _run(monkeypatch, ["up", "--host", "web"]) == 126
    assert "cannot create bundle_dir '/opt/ciu/current'" in capsys.readouterr().err
    assert seen["sync"] == []


def test_remote_preflight_passes_other_ssh_failures_through_silently(remote, monkeypatch, capsys):
    """An ssh failure (255) is the transport's own to report: no invented remedy."""
    import ciu.transport_ssh as transport
    monkeypatch.setattr(transport, "ssh_exec", lambda cfg, argv, **kw: 255)
    assert _run(monkeypatch, ["up", "--host", "web"]) == 255
    assert "[ERROR]" not in capsys.readouterr().err


def test_remote_config_load_needs_no_machine_facts_and_writes_nothing(tmp_path, monkeypatch):
    """CIU-168: a control host without ciu.env / DOCKER_GID still loads the config for a push."""
    for key in ("DOCKER_GID", "CONTAINER_GID", "CONTAINER_UID", "DOCKER_NETWORK_INTERNAL",
                "REPO_ROOT", "INSTANCE_ID"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / "ciu.global.defaults.toml.j2").write_text(
        '[deploy]\ngid = "$DOCKER_GID"\nnet = "$DOCKER_NETWORK_INTERNAL"\nroot = "$REPO_ROOT"\n',
        encoding="utf-8",
    )
    config = cli._load_remote_config(tmp_path)
    assert config["deploy"]["gid"] == "0"
    assert config["deploy"]["net"] == "ciu-remote-placeholder"
    assert config["deploy"]["root"] == str(tmp_path)
    assert not (tmp_path / "ciu.global.toml").exists()


def test_remote_config_load_keeps_real_environment_values(tmp_path, monkeypatch):
    """A fact the control host does have is never replaced by a placeholder."""
    monkeypatch.setenv("DOCKER_GID", "990")
    kwargs = cli._remote_render_kwargs(tmp_path)
    assert kwargs["environ"]["DOCKER_GID"] == "990"
    assert kwargs["write_rendered"] is False


def test_local_status_gate_dir_gates_the_one_stack(remote, monkeypatch):
    """CIU-170: `status --gate --dir STACK` applies the single-stack S7.7 gate locally."""
    import ciu.engine as engine
    calls = []
    monkeypatch.setattr(engine, "_run_single_stack_healthcheck",
                        lambda stack, define_root, config: calls.append((stack, define_root, config)) or 0)
    sys.modules["ciu.deploy"].load_global_config = lambda _root, **_kw: {"cfg": 1}
    assert _run(monkeypatch, ["status", "--gate", "--dir", "whoami"]) == 0
    assert calls == [(Path("whoami"), None, {"cfg": 1})]


def test_local_status_gate_dir_reports_a_bad_config_as_exit_2(remote, monkeypatch, capsys):
    def boom(_root, **_kw):
        raise ValueError("broken config")
    sys.modules["ciu.deploy"].load_global_config = boom
    assert _run(monkeypatch, ["status", "--gate", "--dir", "whoami"]) == 2
    assert "[ERROR] broken config" in capsys.readouterr().err
