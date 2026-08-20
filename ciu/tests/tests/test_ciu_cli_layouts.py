"""Deploy layout CLI wiring (S7.5c, CIU-34).

`ciu up --layout <name>` pushes per host in declaration order, delegating each
host to the existing up --host path with CIU_SERVICES_PROFILE +
CIU_LAYOUT/CIU_LAYOUT_HOST/CIU_DEPLOY_ENVIRONMENT prepended to the ONE remote
argv string. All transport boundaries are faked; the layout exports must never
leak into the LOCAL process environment.
"""
from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import cli

GLOBAL = {
    "deploy": {
        "profiles": {
            "core": {"phases": ["phase_1"]},
            "db": {"phases": ["phase_1"]},
            "worker-io": {"stacks": ["infra/worker"]},
        },
        "layouts": {
            "dev-local": {
                "environment": "dev",
                "hosts": {"devbox": {"bundles": ["core", "db"]}},
            },
            "three-host": {
                "environment": "prod",
                "hosts": {
                    "edge-a": {"bundles": ["core"]},
                    "edge-b": {"bundles": ["core"]},
                    "backend": {"bundles": ["db", "worker-io"]},
                },
            },
        },
    }
}

HOSTS = {"devbox": {}, "edge-a": {}, "edge-b": {}, "backend": {}}


@pytest.fixture
def remote(monkeypatch, tmp_path):
    """Deterministic inventory/transport/config fakes + a selected repo root."""
    monkeypatch.setenv("REPO_ROOT", str(tmp_path))
    for key in ("CIU_LAYOUT", "CIU_LAYOUT_HOST", "CIU_DEPLOY_ENVIRONMENT", "CIU_SERVICES_PROFILE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setitem(
        sys.modules,
        "ciu.deploy",
        SimpleNamespace(load_global_config=lambda _root: GLOBAL),
    )
    host = {"ssh_host": "web", "ssh_key": "/key", "known_host": "pinned", "bundle_dir": "/opt/app"}
    seen = {"hosts": [], "exec": [], "sync": []}

    import ciu.hosts as hosts
    import ciu.transport_ssh as transport

    monkeypatch.setattr(hosts, "load_hosts",
                        lambda root: HOSTS)
    monkeypatch.setattr(hosts, "get_host",
                        lambda root, name, admin=False: seen["hosts"].append((root, name, admin)) or dict(host))
    monkeypatch.setattr(transport, "ssh_exec",
                        lambda cfg, argv, *, config, repo_root, **kwargs:
                        seen["exec"].append((cfg, argv, config, repo_root, kwargs)) or 0)
    monkeypatch.setattr(transport, "ssh_sync",
                        lambda cfg, local, target, *, config, repo_root, **kwargs:
                        seen["sync"].append((cfg, local, target, config, repo_root, kwargs)) or 0)
    return seen


def _run(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["ciu", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    return exc.value.code


def _env_prefix(layout, host, bundles, environment):
    return (
        f"export CIU_SERVICES_PROFILE={shlex.quote(','.join(bundles))}; "
        f"export CIU_LAYOUT={shlex.quote(layout)}; "
        f"export CIU_LAYOUT_HOST={shlex.quote(host)}; "
        f"export CIU_DEPLOY_ENVIRONMENT={shlex.quote(environment)}; "
    )


def _remote_suffix(bundle_dir, remaining=()):
    suffix = f"cd {shlex.quote(str(bundle_dir))} && ciu env generate && ciu render && "
    return suffix + shlex.join(["ciu", "up", *remaining])


def test_up_layout_deploys_hosts_in_declared_order(remote, monkeypatch):
    seen = remote
    assert _run(monkeypatch, ["up", "--layout", "three-host"]) == 0
    assert [h[1] for h in seen["hosts"]] == ["edge-a", "edge-b", "backend"]
    assert len(seen["sync"]) == 3 and len(seen["exec"]) == 3
    for i, (host, bundles) in enumerate(
        [("edge-a", ["core"]), ("edge-b", ["core"]), ("backend", ["db", "worker-io"])]
    ):
        remote_cmd = seen["exec"][i][1][0]
        assert remote_cmd == _env_prefix("three-host", host, bundles, "prod") + _remote_suffix("/opt/app")


def test_up_layout_remote_argv_is_one_string_and_no_local_leak(remote, monkeypatch):
    seen = remote
    assert _run(monkeypatch, ["up", "--layout", "dev-local"]) == 0
    remote_cmd = seen["exec"][0][1]
    assert len(remote_cmd) == 1
    assert remote_cmd[0] == _env_prefix("dev-local", "devbox", ["core", "db"], "dev") + _remote_suffix("/opt/app")
    assert os.environ.get("CIU_LAYOUT") is None
    assert os.environ.get("CIU_LAYOUT_HOST") is None
    assert os.environ.get("CIU_DEPLOY_ENVIRONMENT") is None
    assert os.environ.get("CIU_SERVICES_PROFILE") is None


def test_up_layout_quotes_bundle_dir_in_remote_argv(remote, monkeypatch):
    seen = remote
    seen["hosts"].clear()
    import ciu.hosts as hosts
    host = {"bundle_dir": "/srv/ciu current; touch SHOULD_NOT_RUN"}
    monkeypatch.setattr(hosts, "get_host", lambda root, name, admin=False: dict(host))
    assert _run(monkeypatch, ["up", "--layout", "dev-local"]) == 0
    remote_cmd = seen["exec"][0][1][0]
    assert "cd '/srv/ciu current; touch SHOULD_NOT_RUN' &&" in remote_cmd


def test_up_layout_sync_failure_aborts_naming_host_and_remainder(remote, monkeypatch, capsys):
    seen = remote
    import ciu.transport_ssh as transport
    monkeypatch.setattr(
        transport,
        "ssh_sync",
        lambda cfg, local, target, **kwargs: seen["sync"].append((cfg, local, target)) or 23,
    )
    assert _run(monkeypatch, ["up", "--layout", "three-host"]) == 23
    assert len(seen["sync"]) == 1  # aborted after the first host
    assert seen["exec"] == []
    err = capsys.readouterr().err
    assert "layout 'three-host': bundle sync failed on host 'edge-a' (23)" in err
    assert "not deployed: edge-b, backend" in err


def test_up_layout_exec_failure_aborts_naming_host_and_remainder(remote, monkeypatch, capsys):
    seen = remote
    import ciu.transport_ssh as transport
    monkeypatch.setattr(
        transport,
        "ssh_exec",
        lambda cfg, argv, **kwargs: seen["exec"].append((cfg, argv)) or 7,
    )
    assert _run(monkeypatch, ["up", "--layout", "three-host"]) == 7
    assert len(seen["sync"]) == 1
    assert len(seen["exec"]) == 1
    err = capsys.readouterr().err
    assert "layout 'three-host': up failed on host 'edge-a' (7)" in err
    assert "not deployed: edge-b, backend" in err


def test_up_layout_unknown_layout_exits_2_without_transport(remote, monkeypatch, capsys):
    seen = remote
    assert _run(monkeypatch, ["up", "--layout", "nope"]) == 2
    assert seen["sync"] == [] and seen["exec"] == []
    assert "[S7.5c] Unknown layout 'nope'" in capsys.readouterr().err


def test_up_layout_host_missing_from_inventory_exits_2(remote, monkeypatch, capsys):
    seen = remote
    import ciu.hosts as hosts
    monkeypatch.setattr(hosts, "load_hosts", lambda root: {})
    assert _run(monkeypatch, ["up", "--layout", "dev-local"]) == 2
    assert seen["sync"] == [] and seen["exec"] == []
    assert "[S7.5c] Layout 'dev-local': host 'devbox' is not in the hosts inventory" in capsys.readouterr().err


def test_up_layout_get_host_failure_exits_2_before_transport(remote, monkeypatch, capsys):
    """A host entry that fails to LOAD (not just to resolve) still stops before
    any sync/exec — the layout validated against the inventory, then get_host
    refused the record. B3 nit: the abort must ALSO name the layout and the
    remainder, same as a sync/exec failure ('(none)' here since devbox is the
    only host in dev-local)."""
    seen = remote
    import ciu.hosts as hosts
    monkeypatch.setattr(
        hosts,
        "get_host",
        lambda root, name, admin=False: (_ for _ in ()).throw(ValueError(f"[SPEC J] Host '{name}' unreadable")),
    )
    assert _run(monkeypatch, ["up", "--layout", "dev-local"]) == 2
    assert seen["sync"] == [] and seen["exec"] == []
    err = capsys.readouterr().err
    assert "layout 'dev-local': [SPEC J] Host 'devbox' unreadable" in err
    assert "not deployed: (none)" in err


def test_up_layout_get_host_failure_on_later_host_names_remainder(remote, monkeypatch, capsys):
    """Same abort, but the FAILING host is not the first — the remainder must
    list the hosts still to come, not '(none)'."""
    seen = remote
    import ciu.hosts as hosts
    good = {"ssh_host": "web", "ssh_key": "/key", "known_host": "pinned", "bundle_dir": "/opt/app"}

    def fake_get_host(root, name, admin=False):
        seen["hosts"].append((root, name, admin))
        if name == "edge-b":
            raise ValueError(f"[SPEC J] Host '{name}' unreadable")
        return dict(good)

    monkeypatch.setattr(hosts, "get_host", fake_get_host)
    assert _run(monkeypatch, ["up", "--layout", "three-host"]) == 2
    assert len(seen["sync"]) == 1  # edge-a pushed fine before edge-b's get_host failed
    err = capsys.readouterr().err
    assert "layout 'three-host': [SPEC J] Host 'edge-b' unreadable" in err
    assert "not deployed: backend" in err


def test_up_layout_last_host_failure_reports_no_remainder(remote, monkeypatch, capsys):
    """When the LAST host in the sequence fails, nothing remains undeployed —
    the '(none)' arm of `', '.join(not_deployed) or '(none)'` (previously
    dead: every prior failure test used a non-last host)."""
    seen = remote
    import ciu.transport_ssh as transport

    def fake_exec(cfg, argv, **kwargs):
        seen["exec"].append((cfg, argv))
        return 9 if len(seen["exec"]) == 3 else 0

    monkeypatch.setattr(transport, "ssh_exec", fake_exec)
    assert _run(monkeypatch, ["up", "--layout", "three-host"]) == 9
    assert len(seen["sync"]) == 3 and len(seen["exec"]) == 3  # ran through to the last host
    err = capsys.readouterr().err
    assert "layout 'three-host': up failed on host 'backend' (9)" in err
    assert "not deployed: (none)" in err


def test_up_layout_mutually_exclusive_with_host(remote, monkeypatch, capsys):
    seen = remote
    assert _run(monkeypatch, ["up", "--layout", "dev-local", "--host", "web"]) == 2
    assert seen["sync"] == [] and seen["exec"] == []
    assert "[S7.5c] --layout is mutually exclusive with --host and --profile" in capsys.readouterr().err


def test_up_layout_mutually_exclusive_with_profile(remote, monkeypatch, capsys):
    seen = remote
    assert _run(monkeypatch, ["up", "--layout", "dev-local", "--profile", "core"]) == 2
    assert seen["sync"] == [] and seen["exec"] == []
    assert "[S7.5c] --layout is mutually exclusive with --host and --profile" in capsys.readouterr().err


def test_up_layout_mutually_exclusive_with_profile_equals_form(remote, monkeypatch, capsys):
    """B2: the `--profile=core` single-token form previously slipped through
    (the old check was exact list membership against the literal `--profile`
    token) straight into the forwarded remote argv, silently overriding the
    layout's exported CIU_SERVICES_PROFILE."""
    seen = remote
    assert _run(monkeypatch, ["up", "--layout", "dev-local", "--profile=core"]) == 2
    assert seen["sync"] == [] and seen["exec"] == []
    assert "[S7.5c] --layout is mutually exclusive with --host and --profile" in capsys.readouterr().err


def test_up_layout_mutually_exclusive_with_dir(remote, monkeypatch, capsys):
    """B2: --dir was not guarded at all before this fix."""
    seen = remote
    assert _run(monkeypatch, ["up", "--layout", "dev-local", "--dir", "."]) == 2
    assert seen["sync"] == [] and seen["exec"] == []
    assert "[S7.5c] --layout is mutually exclusive with --host and --profile" in capsys.readouterr().err


@pytest.mark.parametrize("flag", ["--thin", "--bootstrap", "--rollback"])
def test_up_layout_mutually_exclusive_with_host_only_flags(remote, monkeypatch, capsys, flag):
    """B2: --thin/--bootstrap/--rollback only make sense on the --host push
    path and previously forwarded into the remote argv, dying opaquely."""
    seen = remote
    assert _run(monkeypatch, ["up", "--layout", "dev-local", flag]) == 2
    assert seen["sync"] == [] and seen["exec"] == []
    assert "[S7.5c] --layout is mutually exclusive with --host and --profile" in capsys.readouterr().err


def test_layouts_verb_lists_declared_layouts(remote, monkeypatch, capsys):
    assert _run(monkeypatch, ["layouts"]) == 0
    out = capsys.readouterr().out
    assert "dev-local: environment=dev hosts=[devbox]" in out
    assert "three-host: environment=prod hosts=[edge-a, edge-b, backend]" in out


def test_layouts_verb_no_layouts_declared(remote, monkeypatch, capsys):
    monkeypatch.setitem(
        sys.modules,
        "ciu.deploy",
        SimpleNamespace(load_global_config=lambda _root: {"deploy": {}}),
    )
    assert _run(monkeypatch, ["layouts"]) == 0
    assert "(no layouts declared)" in capsys.readouterr().out
