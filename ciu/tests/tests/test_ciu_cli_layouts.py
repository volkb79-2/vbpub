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

import ciu.deploy as REAL_DEPLOY  # captured BEFORE any fixture stubs sys.modules
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
        SimpleNamespace(load_global_config=lambda _root: GLOBAL,
                        resolve_repo_root=REAL_DEPLOY.resolve_repo_root),
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


# ---------------------------------------------------------------------------
# ciu-P29 (hotfix) — the mutual-exclusion guard above was a denylist of EXACT
# flag spellings, while the remote parser it forwards into (deploy.parse_args)
# builds its ArgumentParser without allow_abbrev=False, i.e. with argparse's
# default allow_abbrev=True. An ABBREVIATED forbidden flag therefore walked
# past the local guard, was forwarded verbatim, and resolved remotely — so one
# CLI `--prof=core` silently overrode the layout's per-host bundles on EVERY
# host in the plan. The `--host=`/`--dir=` dispatch tests live here too because
# they are the same equals-form dispatch bug class this package closes.
# ---------------------------------------------------------------------------

# Every abbreviation length of every forbidden flag, plus the `=` variants. The
# point is not the individual spellings: it is that argparse's own resolution
# now backs the guard, so no length is special.
_ABBREVIATIONS = [
    # --profile
    "--p", "--pr", "--pro", "--prof", "--profi", "--profil",
    # --host / --dir
    "--h", "--ho", "--hos", "--d", "--di",
    # --thin / --bootstrap / --rollback
    "--t", "--th", "--thi", "--b", "--boot", "--bootstra", "--r", "--roll",
]


@pytest.mark.parametrize("abbrev", _ABBREVIATIONS)
def test_up_layout_refuses_every_abbreviated_forbidden_flag_space_form(
    remote, monkeypatch, capsys, abbrev
):
    """ciu-P29 O1: `--prof core` etc. is the SAME flag to the remote parser."""
    seen = remote
    assert _run(monkeypatch, ["up", "--layout", "three-host", abbrev, "x"]) == 2
    assert seen["sync"] == [] and seen["exec"] == []
    assert "[S7.5c] --layout is mutually exclusive with --host and --profile" in capsys.readouterr().err


@pytest.mark.parametrize("abbrev", _ABBREVIATIONS)
def test_up_layout_refuses_every_abbreviated_forbidden_flag_equals_form(
    remote, monkeypatch, capsys, abbrev
):
    """ciu-P29 O1: `--prof=core` — the exact spelling the review reproduced."""
    seen = remote
    assert _run(monkeypatch, ["up", "--layout", "three-host", f"{abbrev}=x"]) == 2
    assert seen["sync"] == [] and seen["exec"] == []
    assert "[S7.5c] --layout is mutually exclusive with --host and --profile" in capsys.readouterr().err


@pytest.mark.parametrize(
    "abbrev,resolved",
    [
        ("--prof=core", "--profile"),
        ("--pro", "--profile"),
        ("--hos=edge-a", "--host"),
        ("--di=.", "--dir"),
        ("--th", "--thin"),
        ("--boot", "--bootstrap"),
        ("--roll", "--rollback"),
    ],
)
def test_up_layout_refusal_names_the_resolved_flag(remote, monkeypatch, capsys, abbrev, resolved):
    """The refusal tells the operator WHICH flag the abbreviation resolved to,
    so `--prof=core` does not read as an unexplained rejection of a flag the
    operator never typed."""
    remote  # transport asserted zero in the sibling tests
    assert _run(monkeypatch, ["up", "--layout", "three-host", abbrev]) == 2
    assert f"Refused: {resolved}" in capsys.readouterr().err


def test_up_layout_three_host_prod_abbreviated_profile_pushes_to_nobody(remote, monkeypatch, capsys):
    """ciu-P29 O1, the review's EXACT reproduction: a 3-host prod layout invoked
    with `--prof=core`. Before the fix this exited 0 having pushed to all three
    hosts, each remote argv carrying `ciu up --prof=core` after the layout's own
    `export CIU_SERVICES_PROFILE=...` — so backend, whose bundles are
    `db,worker-io`, silently deployed `core`. The assertion that matters is the
    transport call COUNT, not the message: no host may be contacted at all."""
    seen = remote
    assert _run(monkeypatch, ["up", "--layout", "three-host", "--prof=core"]) == 2
    assert seen["sync"] == [] and seen["exec"] == []
    assert seen["hosts"] == []  # not even resolved from the inventory
    assert "[S7.5c]" in capsys.readouterr().err


def test_up_layout_forbidden_flag_with_no_value_is_refused_not_argparse_error(
    remote, monkeypatch, capsys
):
    """A value-taking forbidden flag given LAST with no value must still reach
    the `[S7.5c]` guard: `nargs="?"`/`const=True` in _parse_layout_argv keeps
    argparse from failing first with a raw `expected one argument`."""
    seen = remote
    assert _run(monkeypatch, ["up", "--layout", "three-host", "--profile"]) == 2
    assert seen["sync"] == [] and seen["exec"] == []
    err = capsys.readouterr().err
    assert "[S7.5c]" in err and "Refused: --profile" in err
    assert "expected one argument" not in err


def test_up_layout_store_true_forbidden_flag_with_value_is_refused(remote, monkeypatch, capsys):
    """Mirror of the above for a store-true-shaped flag given a value
    (`--thin=1`), which argparse would otherwise reject with a raw
    `ignored explicit argument` before the guard could run."""
    seen = remote
    assert _run(monkeypatch, ["up", "--layout", "three-host", "--thin=1"]) == 2
    assert seen["sync"] == [] and seen["exec"] == []
    err = capsys.readouterr().err
    assert "[S7.5c]" in err and "Refused: --thin" in err
    assert "ignored explicit argument" not in err


def test_up_layout_refusal_names_every_forbidden_flag_supplied(remote, monkeypatch, capsys):
    """More than one forbidden flag: all are named, in declaration order."""
    seen = remote
    assert _run(monkeypatch, ["up", "--layout", "three-host", "--hos=w", "--prof=core"]) == 2
    assert seen["sync"] == [] and seen["exec"] == []
    assert "Refused: --profile, --host" in capsys.readouterr().err


def test_up_layout_clean_argv_still_forwards_unrelated_flags(remote, monkeypatch):
    """The guard must not become a general allowlist: a flag that is NOT
    forbidden still rides along in the remote argv exactly as before."""
    seen = remote
    assert _run(monkeypatch, ["up", "--layout", "dev-local", "--dry-run"]) == 0
    remote_cmd = seen["exec"][0][1][0]
    assert remote_cmd == _env_prefix("dev-local", "devbox", ["core", "db"], "dev") + _remote_suffix(
        "/opt/app", ["--dry-run"]
    )


def test_up_layout_equals_form_dispatches_through_the_same_path(remote, monkeypatch):
    """ciu-P29 O2: `--layout=NAME` previously failed the `"--layout" in rest`
    membership test, skipped the layout branch entirely, and fell through to the
    local profile deploy with a raw `unrecognized arguments: --layout=three-host`
    argparse error. It must now produce the IDENTICAL push sequence as the space
    form — same branch, not a parallel reimplementation."""
    seen = remote
    assert _run(monkeypatch, ["up", "--layout=three-host"]) == 0
    equals_form = list(seen["exec"])
    equals_hosts = list(seen["hosts"])
    seen["exec"].clear()
    seen["sync"].clear()
    seen["hosts"].clear()
    assert _run(monkeypatch, ["up", "--layout", "three-host"]) == 0
    assert seen["exec"] == equals_form
    assert seen["hosts"] == equals_hosts
    assert len(equals_form) == 3


def test_up_layout_equals_form_is_guarded_too(remote, monkeypatch, capsys):
    """The equals-form dispatch must land inside the guard, not around it."""
    seen = remote
    assert _run(monkeypatch, ["up", "--layout=three-host", "--prof=core"]) == 2
    assert seen["sync"] == [] and seen["exec"] == []
    assert "[S7.5c]" in capsys.readouterr().err


@pytest.mark.parametrize("verb", ["up", "down", "health", "render"])
def test_host_equals_form_dispatches_to_the_push_path(remote, monkeypatch, verb):
    """ciu-P29 O2 extension: `--host=NAME` hit the same plain-membership
    dispatch bug on every verb that accepts the S10.4 `--host` modifier. This
    one was WORSE than the layout case: `deploy.py` declares `--host` for its
    help text but never reads it, so the fall-through parsed cleanly and ran a
    LOCAL deploy of the active profile while the operator believed they had
    pushed to a remote host — exit 0, no warning."""
    seen = remote
    assert _run(monkeypatch, [verb, "--host=edge-a"]) == 0
    assert [h[1] for h in seen["hosts"]] == ["edge-a"]
    assert len(seen["exec"]) == 1
    # `up` syncs the bundle first; the read-only verbs exec directly.
    assert len(seen["sync"]) == (1 if verb == "up" else 0)


@pytest.mark.parametrize("verb", ["up", "down", "health", "render"])
def test_host_equals_form_matches_the_space_form(remote, monkeypatch, verb):
    """Same branch, so the remote argv must be byte-identical between forms."""
    seen = remote
    assert _run(monkeypatch, [verb, "--host=edge-a"]) == 0
    equals_form = list(seen["exec"])
    seen["exec"].clear()
    seen["sync"].clear()
    assert _run(monkeypatch, [verb, "--host", "edge-a"]) == 0
    assert seen["exec"] == equals_form


def test_up_dir_equals_form_dispatches_to_the_engine_path(remote, monkeypatch):
    """ciu-P29 O2 extension: `ciu up --dir=/srv` fell through to deploy.py,
    which has no `--dir`, and died on `unrecognized arguments`."""
    seen = remote
    calls = []
    monkeypatch.setitem(sys.modules, "ciu.engine",
                        SimpleNamespace(main=lambda argv: calls.append(argv) or 0))
    assert _run(monkeypatch, ["up", "--dir=/srv/app", "--dry-run"]) == 0
    assert calls == [["-d", "/srv/app", "--dry-run"]]
    assert seen["exec"] == [] and seen["sync"] == []


def test_up_dir_equals_form_matches_the_space_form(remote, monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "ciu.engine",
                        SimpleNamespace(main=lambda argv: calls.append(argv) or 0))
    assert _run(monkeypatch, ["up", "--dir=/srv/app"]) == 0
    assert _run(monkeypatch, ["up", "--dir", "/srv/app"]) == 0
    assert calls[0] == calls[1]


def test_flag_given_matches_space_and_equals_forms():
    assert cli._flag_given(["--host", "web"], "--host") is True
    assert cli._flag_given(["--host=web"], "--host") is True
    assert cli._flag_given(["--hostile"], "--host") is False
    assert cli._flag_given([], "--host") is False
    assert cli._flag_given(["--layout=prod"], "--layout") is True
    assert cli._flag_given(["--dir=/srv"], "--dir") is True


# ---------------------------------------------------------------------------
# ciu-P29 second pass (adversarial review REJECT). The first pass made dispatch
# exact-or-`=` and documented "an abbreviation still fails loudly at whichever
# parser it reaches, so it can never deploy the wrong thing". That claim was
# FALSE for `--host`: deploy.py declares `--host` (line 3592) for its help text
# and reads it NOWHERE, so `ciu up --hos=edge-a` parsed cleanly downstream, had
# the host silently discarded, and ran a LOCAL deploy of the active profile —
# exit 0, zero hosts contacted. `--host` dispatch is now abbreviation-aware.
#
# These tests wrap the REAL deploy.main / deploy.parse_args. A probe that stubs
# them is VACUOUS here: a dispatch regression would raise ImportError or hit a
# lambda instead of doing the actual wrong thing, masking both the bug and the
# fix. That is the whole point of the `real_deploy` fixture below.
# ---------------------------------------------------------------------------


@pytest.fixture
def real_deploy(remote, monkeypatch):
    """Restore the GENUINE `ciu.deploy` and spy on it instead of stubbing it.

    The `remote` fixture replaces `ciu.deploy` with a SimpleNamespace. Here the
    real module is put back, `load_global_config` is redirected at this file's
    GLOBAL, and `main`/`parse_args` are wrapped in recording spies that run the
    REAL parser — so a fall-through is observable as what it really is: a local
    deploy, with the real parser's own verdict on the argv recorded next to it.
    """
    # NOT `import ciu.deploy` — `remote` has already put a SimpleNamespace in
    # sys.modules, and an import here would just hand that stub back.
    real = REAL_DEPLOY
    monkeypatch.setitem(sys.modules, "ciu.deploy", real)
    monkeypatch.setattr(real, "load_global_config", lambda _root: GLOBAL)
    calls = {"main": [], "parse_args": []}
    real_parse_args = real.parse_args

    def spy_main(argv=None):
        argv = list(argv or [])
        try:
            parsed = f"host={getattr(real_parse_args(argv), 'host', None)!r}"
        except SystemExit as exc:  # the loud-failure arm (--lay/--di fall-through)
            calls["main"].append((argv, f"SystemExit({exc.code})"))
            raise
        calls["main"].append((argv, parsed))
        return 0

    def spy_parse_args(argv=None):
        calls["parse_args"].append(list(argv or []))
        return real_parse_args(argv)

    monkeypatch.setattr(real, "main", spy_main)
    monkeypatch.setattr(real, "parse_args", spy_parse_args)
    return calls


_HOST_ABBREVIATIONS = ["--hos=edge-a", "--ho=edge-a", "--hos edge-a",
                       "--ho edge-a", "--h edge-a", "--host=edge-a"]


@pytest.mark.parametrize("verb", ["up", "down", "health", "render"])
@pytest.mark.parametrize("spelling", _HOST_ABBREVIATIONS)
def test_abbreviated_host_dispatches_and_never_becomes_a_local_deploy(
    remote, real_deploy, monkeypatch, verb, spelling
):
    """The review's exact matrix, against the REAL parser chain. Before this
    fix every one of these returned exit 0 having run a LOCAL deploy of the
    active profile and contacted ZERO remote hosts."""
    seen = remote
    assert _run(monkeypatch, [verb, *spelling.split()]) == 0
    # It reached the remote push path...
    assert [h[1] for h in seen["hosts"]] == ["edge-a"]
    assert len(seen["exec"]) == 1
    assert len(seen["sync"]) == (1 if verb == "up" else 0)
    # ...and never fell through to the local deploy. This is the blocker's
    # assertion: `deploy.main` must not be reached at all.
    assert real_deploy["main"] == []


@pytest.mark.parametrize("verb", ["up", "down", "health", "render"])
def test_abbreviated_host_matches_the_full_spelling_exactly(
    remote, real_deploy, monkeypatch, verb
):
    """`--hos=edge-a` must produce the same remote argv as `--host edge-a` —
    the same branch, not a lookalike."""
    seen = remote
    assert _run(monkeypatch, [verb, "--hos=edge-a"]) == 0
    abbreviated = list(seen["exec"])
    seen["exec"].clear()
    seen["sync"].clear()
    assert _run(monkeypatch, [verb, "--host", "edge-a"]) == 0
    assert seen["exec"] == abbreviated
    assert real_deploy["main"] == []


def test_bare_abbreviated_host_still_errors_in_the_branchs_own_parser(
    remote, real_deploy, monkeypatch
):
    """`nargs="?"` in the dispatch predicate must not swallow the missing-value
    error: `ciu up --hos` dispatches, then the branch's own parser rejects it,
    exactly as `ciu up --host` always did."""
    seen = remote
    assert _run(monkeypatch, ["up", "--hos"]) == 2
    assert seen["exec"] == [] and seen["sync"] == []
    assert real_deploy["main"] == []


def test_dispatch_abbreviation_premise_against_the_real_deploy_parser(capsys):
    """Pins the ASYMMETRY `_flag_given` encodes, against the real parser.

    `--host` is abbreviation-aware at dispatch only because deploy.py accepts
    (and silently ignores) its abbreviations; `--layout`/`--dir` are not,
    because deploy.py rejects theirs loudly. If deploy.py ever grows a `--dir`
    or `--layout`, or stops accepting `--hos`, this fails — rather than the
    docstring quietly going stale and the hazard reopening."""
    real = REAL_DEPLOY

    # --host EXISTS downstream and is discarded, so a fall-through is SILENT.
    for argv in (["--hos=edge-a"], ["--ho", "edge-a"], ["--host=edge-a"]):
        assert real.parse_args(argv).host == "edge-a"
    assert "--host" in cli._ABBREV_DISPATCH_FLAGS

    # --layout / --dir do NOT exist downstream: every abbreviation fails LOUDLY,
    # so dispatch for them stays exact-or-`=` and no hazard is left open.
    for argv in (["--lay", "x"], ["--layout=x"], ["--di=/srv"],
                 ["--dir=/srv"], ["--d", "/srv"]):
        with pytest.raises(SystemExit) as exc:
            real.parse_args(argv)
        assert exc.value.code == 2
    assert "--layout" not in cli._ABBREV_DISPATCH_FLAGS
    assert "--dir" not in cli._ABBREV_DISPATCH_FLAGS
    capsys.readouterr()  # swallow argparse's usage dumps

    assert set(cli._ABBREV_DISPATCH_FLAGS) <= set(cli._DISPATCH_FLAGS)


def test_dispatch_flags_have_distinct_second_characters():
    """Residual guard suggested by the review. Both argparse-resolution sites
    register several long flags on one parser and rely on none being a prefix
    of another. A future flag colliding with an existing one (say `--hub`
    beside `--host`) must fail HERE, at authoring time, rather than as an
    `ambiguous option` at a user's terminal."""
    for group in (cli._DISPATCH_FLAGS, ("--layout",) + cli._LAYOUT_FORBIDDEN):
        seconds = [flag[2] for flag in group]
        assert len(set(seconds)) == len(seconds), group
    assert len(set(cli._DISPATCH_FLAGS)) == len(cli._DISPATCH_FLAGS)


def test_up_layout_abbreviation_does_not_dispatch_but_cannot_deploy(
    remote, real_deploy, monkeypatch
):
    """`ciu up --lay three-host --prof core` does NOT reach the layout branch
    (dispatch stays exact-or-`=` for --layout), and must therefore fail LOUDLY
    at the REAL deploy parser with zero transport — never a silent deploy. The
    real parser is wrapped here precisely so "fails loudly" is a measured
    outcome and not, as in the first pass, an assumption."""
    seen = remote
    assert _run(monkeypatch, ["up", "--lay", "three-host", "--prof", "core"]) == 2
    assert seen["sync"] == [] and seen["exec"] == [] and seen["hosts"] == []


def test_up_dir_abbreviation_does_not_dispatch_but_cannot_deploy(
    remote, real_deploy, monkeypatch
):
    """Same for `--dir`: `--di=/srv` is `unrecognized arguments` downstream and
    `--d /srv` is genuinely ambiguous there against `--define-root PATH`, so
    both must fail loudly rather than be silently claimed as `--dir`."""
    seen = remote
    for spelling in (["--di=/srv"], ["--d", "/srv"]):
        assert _run(monkeypatch, ["up", *spelling]) == 2
    assert seen["sync"] == [] and seen["exec"] == [] and seen["hosts"] == []


def test_layouts_verb_lists_declared_layouts(remote, monkeypatch, capsys):
    assert _run(monkeypatch, ["layouts"]) == 0
    out = capsys.readouterr().out
    assert "dev-local: environment=dev hosts=[devbox]" in out
    assert "three-host: environment=prod hosts=[edge-a, edge-b, backend]" in out


def test_layouts_verb_no_layouts_declared(remote, monkeypatch, capsys):
    monkeypatch.setitem(
        sys.modules,
        "ciu.deploy",
        SimpleNamespace(load_global_config=lambda _root: {"deploy": {}},
                        resolve_repo_root=REAL_DEPLOY.resolve_repo_root),
    )
    assert _run(monkeypatch, ["layouts"]) == 0
    assert "(no layouts declared)" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# ciu-P45 / CIU-54: `layouts` and `up --layout` now resolve repo_root via
# `deploy.resolve_repo_root` (S1.1) instead of a bare `REPO_ROOT`-or-cwd
# fallback that ignored `--define-root` entirely -- previously the ONLY
# local option `layouts` (and this branch of `up`) took was none at all.
# ---------------------------------------------------------------------------

def test_layouts_verb_accepts_define_root_with_no_ambient_repo_root(
    remote, monkeypatch, capsys, tmp_path
):
    monkeypatch.delenv("REPO_ROOT", raising=False)
    assert _run(monkeypatch, ["layouts", "--define-root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "dev-local: environment=dev hosts=[devbox]" in out


def test_up_layout_define_root_resolves_and_is_not_forwarded_to_remote_argv(
    remote, monkeypatch, tmp_path
):
    """--define-root is consumed LOCALLY -- it must never appear in the one
    remote argv string every host in the layout receives."""
    seen = remote
    assert _run(monkeypatch, [
        "up", "--layout", "dev-local", "--define-root", str(tmp_path),
    ]) == 0
    assert len(seen["exec"]) == 1
    cfg, argv, config, repo_root, kwargs = seen["exec"][0]
    assert repo_root == tmp_path.resolve()
    assert not any("--define-root" in a or "--root-folder" in a for a in argv)


def test_up_layout_define_root_disagreeing_with_ambient_repo_root_refuses(
    remote, monkeypatch, capsys, tmp_path
):
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv("REPO_ROOT", str(other))
    seen = remote
    assert _run(monkeypatch, [
        "up", "--layout", "dev-local", "--define-root", str(tmp_path),
    ]) == 2
    assert seen["sync"] == [] and seen["exec"] == []
    err = capsys.readouterr().err
    assert "[ERROR]" in err
    assert str(tmp_path.resolve()) in err and str(other.resolve()) in err
