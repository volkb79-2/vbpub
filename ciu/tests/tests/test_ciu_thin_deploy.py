"""
CIU SPEC S14.6 — docker-optional push→activate (`--thin`) tests.

Covers the activate module (resolve_activation_command, make_tarball,
push_bundle rsync/scp fallback, run_activation, run_thin_up) and the CLI
`ciu up --host … --thin` / `ciu health --host … --thin` dispatch.

Unit-only: no real network, SSH, rsync, or Vault.
"""
from __future__ import annotations

import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import activate as act
from ciu import cli as cli_mod


# ---------------------------------------------------------------------------
# resolve_activation_command
# ---------------------------------------------------------------------------


class TestResolveActivationCommand:
    def test_string_entrypoint_appends_verb(self):
        cfg = {"activate": "sh deploy/activate.sh"}
        assert act.resolve_activation_command(cfg, "apply") == "sh deploy/activate.sh apply"
        assert act.resolve_activation_command(cfg, "bootstrap") == "sh deploy/activate.sh bootstrap"

    def test_table_form_returns_per_verb_command(self):
        cfg = {"activate": {"apply": "touch tmp/restart.txt", "health": "sh h.sh"}}
        assert act.resolve_activation_command(cfg, "apply") == "touch tmp/restart.txt"
        assert act.resolve_activation_command(cfg, "health") == "sh h.sh"

    def test_missing_activate_raises(self):
        with pytest.raises(ValueError, match="no 'activate' entrypoint"):
            act.resolve_activation_command({}, "apply")

    def test_table_missing_verb_raises(self):
        cfg = {"activate": {"apply": "x"}}
        with pytest.raises(ValueError, match="no 'rollback' command"):
            act.resolve_activation_command(cfg, "rollback")

    def test_empty_string_entrypoint_raises(self):
        with pytest.raises(ValueError, match="empty"):
            act.resolve_activation_command({"activate": "   "}, "apply")

    def test_unknown_verb_raises(self):
        with pytest.raises(ValueError, match="Unknown activation verb"):
            act.resolve_activation_command({"activate": "x"}, "restart")

    def test_wrong_type_raises(self):
        with pytest.raises(ValueError, match="must be a string entrypoint or"):
            act.resolve_activation_command({"activate": 42}, "apply")


# ---------------------------------------------------------------------------
# make_tarball
# ---------------------------------------------------------------------------


class TestMakeTarball:
    def _tree(self, tmp_path):
        (tmp_path / "app.py").write_text("print()")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "x.txt").write_text("x")
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("[core]")
        return tmp_path

    def test_tarball_contains_relative_members(self, tmp_path):
        src = self._tree(tmp_path)
        tb = act.make_tarball(str(src))
        try:
            with tarfile.open(tb) as tar:
                names = {n.lstrip("./") for n in tar.getnames()}
            assert "app.py" in names
            assert "sub/x.txt" in names
        finally:
            Path(tb).unlink()

    def test_excludes_drop_top_level_dir(self, tmp_path):
        src = self._tree(tmp_path)
        tb = act.make_tarball(str(src), excludes=[".git"])
        try:
            with tarfile.open(tb) as tar:
                names = {n.lstrip("./") for n in tar.getnames()}
            assert not any(n.startswith(".git") for n in names)
            assert "app.py" in names
        finally:
            Path(tb).unlink()


# ---------------------------------------------------------------------------
# push_bundle — mode selection + fallback
# ---------------------------------------------------------------------------


class TestPushBundle:
    def test_auto_uses_rsync_when_it_succeeds(self, tmp_path, monkeypatch):
        calls = {}

        def fake_sync(host_cfg, local, remote, *, config, repo_root, excludes=None):
            calls["sync"] = {"excludes": excludes}
            return 0

        def fake_scp(*a, **k):  # must NOT be called
            calls["scp"] = True
            return 0

        monkeypatch.setattr(act, "ssh_sync", fake_sync)
        monkeypatch.setattr(act, "_push_scp", fake_scp)
        rc = act.push_bundle({}, "/local", "/remote", config={}, repo_root=tmp_path)
        assert rc == 0
        assert "sync" in calls and "scp" not in calls
        # default bundle_excludes applied
        assert calls["sync"]["excludes"] == act.DEFAULT_BUNDLE_EXCLUDES

    def test_auto_falls_back_when_control_rsync_missing(self, tmp_path, monkeypatch):
        def fake_sync(*a, **k):
            raise FileNotFoundError("rsync")

        called = {}
        monkeypatch.setattr(act, "ssh_sync", fake_sync)
        monkeypatch.setattr(act, "_push_scp",
                            lambda *a, **k: (called.__setitem__("scp", True), 0)[1])
        rc = act.push_bundle({}, "/l", "/r", config={}, repo_root=tmp_path)
        assert rc == 0
        assert called.get("scp") is True

    def test_auto_falls_back_on_remote_rc_127(self, tmp_path, monkeypatch):
        monkeypatch.setattr(act, "ssh_sync", lambda *a, **k: 127)
        called = {}
        monkeypatch.setattr(act, "_push_scp",
                            lambda *a, **k: (called.__setitem__("scp", True), 0)[1])
        rc = act.push_bundle({}, "/l", "/r", config={}, repo_root=tmp_path)
        assert rc == 0
        assert called.get("scp") is True

    def test_rsync_mode_does_not_fall_back(self, tmp_path, monkeypatch):
        monkeypatch.setattr(act, "ssh_sync", lambda *a, **k: 127)
        monkeypatch.setattr(act, "_push_scp",
                            lambda *a, **k: pytest.fail("scp must not be used in rsync mode"))
        rc = act.push_bundle({"push_mode": "rsync"}, "/l", "/r", config={}, repo_root=tmp_path)
        assert rc == 127  # remote 127 propagates, no fallback

    def test_rsync_mode_reraises_control_missing(self, tmp_path, monkeypatch):
        def fake_sync(*a, **k):
            raise FileNotFoundError("rsync")
        monkeypatch.setattr(act, "ssh_sync", fake_sync)
        with pytest.raises(FileNotFoundError):
            act.push_bundle({"push_mode": "rsync"}, "/l", "/r", config={}, repo_root=tmp_path)

    def test_scp_mode_never_calls_rsync(self, tmp_path, monkeypatch):
        monkeypatch.setattr(act, "ssh_sync",
                            lambda *a, **k: pytest.fail("rsync must not run in scp mode"))
        called = {}
        monkeypatch.setattr(act, "_push_scp",
                            lambda *a, **k: (called.__setitem__("scp", True), 0)[1])
        rc = act.push_bundle({"push_mode": "scp"}, "/l", "/r", config={}, repo_root=tmp_path)
        assert rc == 0 and called.get("scp") is True

    def test_unknown_push_mode_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Unknown push_mode"):
            act.push_bundle({"push_mode": "ftp"}, "/l", "/r", config={}, repo_root=tmp_path)

    def test_custom_bundle_excludes_passed_through(self, tmp_path, monkeypatch):
        seen = {}
        monkeypatch.setattr(act, "ssh_sync",
                            lambda *a, excludes=None, **k: seen.setdefault("ex", excludes) or 0)
        act.push_bundle({"bundle_excludes": ["a", "b"]}, "/l", "/r",
                        config={}, repo_root=tmp_path)
        assert seen["ex"] == ["a", "b"]

    def test_bad_bundle_excludes_type_raises(self, tmp_path):
        with pytest.raises(ValueError, match="must be a list"):
            act.push_bundle({"bundle_excludes": "x"}, "/l", "/r",
                            config={}, repo_root=tmp_path)


# ---------------------------------------------------------------------------
# _push_scp — orchestration (mkdir -> scp -> extract), all mocked
# ---------------------------------------------------------------------------


class TestPushScp:
    def test_sequence_mkdir_scp_extract(self, tmp_path, monkeypatch):
        src = tmp_path / "repo"
        src.mkdir()
        (src / "f").write_text("data")

        exec_cmds = []
        scp_args = {}

        monkeypatch.setattr(act, "ssh_exec",
                            lambda host, argv, *, config, repo_root: exec_cmds.append(argv[0]) or 0)
        monkeypatch.setattr(act, "scp_file",
                            lambda host, local, remote, *, config, repo_root: (
                                scp_args.update(local=local, remote=remote), 0)[1])
        rc = act._push_scp({}, str(src), "/remote/dir", config={}, repo_root=tmp_path)
        assert rc == 0
        # first exec = mkdir, second exec = tar extract
        assert exec_cmds[0].startswith("mkdir -p /remote/dir")
        assert "tar xzf" in exec_cmds[1] and "-C /remote/dir" in exec_cmds[1]
        assert scp_args["remote"].endswith(act._REMOTE_TARBALL)

    def test_mkdir_failure_short_circuits(self, tmp_path, monkeypatch):
        src = tmp_path / "repo"
        src.mkdir()
        monkeypatch.setattr(act, "ssh_exec", lambda *a, **k: 5)
        monkeypatch.setattr(act, "scp_file",
                            lambda *a, **k: pytest.fail("scp must not run if mkdir failed"))
        rc = act._push_scp({}, str(src), "/remote", config={}, repo_root=tmp_path)
        assert rc == 5

    def test_tarball_cleaned_up_on_scp_failure(self, tmp_path, monkeypatch):
        src = tmp_path / "repo"
        src.mkdir()
        made = {}
        real_make = act.make_tarball

        def spy_make(local_dir, *, excludes=None):
            p = real_make(local_dir, excludes=excludes)
            made["path"] = p
            return p

        monkeypatch.setattr(act, "make_tarball", spy_make)
        monkeypatch.setattr(act, "ssh_exec", lambda *a, **k: 0)      # mkdir ok
        monkeypatch.setattr(act, "scp_file", lambda *a, **k: 9)      # scp fails
        rc = act._push_scp({}, str(src), "/remote", config={}, repo_root=tmp_path)
        assert rc == 9
        assert not Path(made["path"]).exists()  # cleaned in finally


# ---------------------------------------------------------------------------
# run_activation / run_thin_up
# ---------------------------------------------------------------------------


class TestRunActivation:
    def test_builds_cd_and_command_one_argv(self, tmp_path, monkeypatch):
        cfg = {"activate": "sh deploy/activate.sh"}
        captured = {}
        monkeypatch.setattr(act, "ssh_exec",
                            lambda host, argv, *, config, repo_root: (captured.__setitem__("argv", argv), 0)[1])
        rc = act.run_activation(cfg, "apply", config={}, repo_root=tmp_path,
                                bundle_dir="/opt/app", remaining=["--profile", "apps"])
        assert rc == 0
        argv = captured["argv"]
        assert len(argv) == 1  # ONE element for the remote login shell
        assert argv[0] == "cd /opt/app && sh deploy/activate.sh apply --profile apps"


class TestRunThinUp:
    def _cfg(self):
        return {"activate": "sh deploy/activate.sh", "known_host": "k"}

    def test_apply_pushes_then_applies(self, tmp_path, monkeypatch):
        order = []
        monkeypatch.setattr(act, "push_bundle",
                            lambda *a, **k: order.append("push") or 0)
        monkeypatch.setattr(act, "run_activation",
                            lambda cfg, verb, **k: order.append(verb) or 0)
        rc = act.run_thin_up(self._cfg(), config={}, repo_root=tmp_path, bundle_dir="/b")
        assert rc == 0
        assert order == ["push", "apply"]

    def test_bootstrap_runs_before_apply(self, tmp_path, monkeypatch):
        order = []
        monkeypatch.setattr(act, "push_bundle", lambda *a, **k: order.append("push") or 0)
        monkeypatch.setattr(act, "run_activation",
                            lambda cfg, verb, **k: order.append(verb) or 0)
        rc = act.run_thin_up(self._cfg(), config={}, repo_root=tmp_path,
                             bundle_dir="/b", bootstrap=True)
        assert rc == 0
        assert order == ["push", "bootstrap", "apply"]

    def test_rollback_skips_push(self, tmp_path, monkeypatch):
        order = []
        monkeypatch.setattr(act, "push_bundle",
                            lambda *a, **k: pytest.fail("rollback must not push"))
        monkeypatch.setattr(act, "run_activation",
                            lambda cfg, verb, **k: order.append(verb) or 0)
        rc = act.run_thin_up(self._cfg(), config={}, repo_root=tmp_path,
                             bundle_dir="/b", rollback=True)
        assert rc == 0
        assert order == ["rollback"]

    def test_push_failure_aborts_before_activation(self, tmp_path, monkeypatch):
        monkeypatch.setattr(act, "push_bundle", lambda *a, **k: 3)
        monkeypatch.setattr(act, "run_activation",
                            lambda *a, **k: pytest.fail("activation must not run if push failed"))
        rc = act.run_thin_up(self._cfg(), config={}, repo_root=tmp_path, bundle_dir="/b")
        assert rc == 3

    def test_bootstrap_failure_aborts_before_apply(self, tmp_path, monkeypatch):
        monkeypatch.setattr(act, "push_bundle", lambda *a, **k: 0)
        seen = []

        def fake_activation(cfg, verb, **k):
            seen.append(verb)
            return 4 if verb == "bootstrap" else 0
        monkeypatch.setattr(act, "run_activation", fake_activation)
        rc = act.run_thin_up(self._cfg(), config={}, repo_root=tmp_path,
                             bundle_dir="/b", bootstrap=True)
        assert rc == 4
        assert seen == ["bootstrap"]  # apply never reached


# ---------------------------------------------------------------------------
# CLI dispatch — ciu up --host … --thin  /  ciu health --host … --thin
# ---------------------------------------------------------------------------


def _write_thin_hosts(tmp_path, extra="") -> Path:
    key_file = tmp_path / "id_rsa"
    key_file.write_text("KEY")
    hosts = tmp_path / ".ciu.hosts.toml"
    hosts.write_text(
        f'[deploy.hosts.web]\n'
        f'ssh_host = "web.example.com"\nssh_user = "d"\nssh_port = 22\n'
        f'ssh_key = "{key_file}"\n'
        f'known_host = "ssh-ed25519 AAAA..."\n'
        f'bundle_dir = "/opt/app"\n'
        f'activate = "sh deploy/activate.sh"\n'
        f'{extra}'
    )
    return hosts


class TestCliThinDispatch:
    def test_up_thin_apply(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REPO_ROOT", str(tmp_path))
        _write_thin_hosts(tmp_path)
        captured = {}
        monkeypatch.setattr(act, "run_thin_up",
                            lambda host_cfg, *, config, repo_root, bundle_dir, bootstrap=False, rollback=False, remaining=None:
                            captured.update(bundle_dir=bundle_dir, bootstrap=bootstrap,
                                            rollback=rollback, remaining=remaining) or 0)
        monkeypatch.setattr(sys, "argv",
                            ["ciu", "up", "--host", "web", "--thin", "--profile", "apps"])
        with pytest.raises(SystemExit) as exc:
            cli_mod.main()
        assert exc.value.code == 0
        assert captured["bundle_dir"] == "/opt/app"
        assert captured["bootstrap"] is False and captured["rollback"] is False
        assert captured["remaining"] == ["--profile", "apps"]

    def test_up_thin_bootstrap_flag(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REPO_ROOT", str(tmp_path))
        _write_thin_hosts(tmp_path)
        captured = {}
        monkeypatch.setattr(act, "run_thin_up",
                            lambda host_cfg, *, config, repo_root, bundle_dir, bootstrap=False, rollback=False, remaining=None:
                            captured.update(bootstrap=bootstrap, rollback=rollback) or 0)
        monkeypatch.setattr(sys, "argv", ["ciu", "up", "--host", "web", "--thin", "--bootstrap"])
        with pytest.raises(SystemExit) as exc:
            cli_mod.main()
        assert exc.value.code == 0
        assert captured["bootstrap"] is True

    def test_up_thin_rollback_flag(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REPO_ROOT", str(tmp_path))
        _write_thin_hosts(tmp_path)
        captured = {}
        monkeypatch.setattr(act, "run_thin_up",
                            lambda host_cfg, *, config, repo_root, bundle_dir, bootstrap=False, rollback=False, remaining=None:
                            captured.update(rollback=rollback) or 0)
        monkeypatch.setattr(sys, "argv", ["ciu", "up", "--host", "web", "--thin", "--rollback"])
        with pytest.raises(SystemExit) as exc:
            cli_mod.main()
        assert exc.value.code == 0
        assert captured["rollback"] is True

    def test_up_thin_bootstrap_and_rollback_mutually_exclusive(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REPO_ROOT", str(tmp_path))
        _write_thin_hosts(tmp_path)
        monkeypatch.setattr(sys, "argv",
                            ["ciu", "up", "--host", "web", "--thin", "--bootstrap", "--rollback"])
        with pytest.raises(SystemExit) as exc:
            cli_mod.main()
        assert exc.value.code == 2

    def test_up_bootstrap_without_thin_errors(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REPO_ROOT", str(tmp_path))
        _write_thin_hosts(tmp_path)
        monkeypatch.setattr(sys, "argv", ["ciu", "up", "--host", "web", "--bootstrap"])
        with pytest.raises(SystemExit) as exc:
            cli_mod.main()
        assert exc.value.code == 2

    def test_up_thin_missing_activate_exits_2(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REPO_ROOT", str(tmp_path))
        monkeypatch.delenv("CIU_SSH_INSECURE_TOFU", raising=False)
        # host WITHOUT an activate key; push is mocked to succeed so we reach
        # activation, which must raise ValueError -> exit 2.
        key_file = tmp_path / "id_rsa"
        key_file.write_text("KEY")
        (tmp_path / ".ciu.hosts.toml").write_text(
            f'[deploy.hosts.web]\nssh_host = "h"\nssh_user = "u"\nssh_port = 22\n'
            f'ssh_key = "{key_file}"\nknown_host = "k"\nbundle_dir = "/opt/app"\n'
        )
        monkeypatch.setattr(act, "push_bundle", lambda *a, **k: 0)
        monkeypatch.setattr(sys, "argv", ["ciu", "up", "--host", "web", "--thin"])
        with pytest.raises(SystemExit) as exc:
            cli_mod.main()
        assert exc.value.code == 2

    def test_docker_host_path_unchanged_without_thin(self, tmp_path, monkeypatch):
        """Regression: the docker render-on-target --host path still runs when --thin absent."""
        monkeypatch.setenv("REPO_ROOT", str(tmp_path))
        monkeypatch.delenv("CIU_SSH_INSECURE_TOFU", raising=False)
        _write_thin_hosts(tmp_path)
        sync_calls, exec_calls = [], []
        import ciu.transport_ssh as tssh
        monkeypatch.setattr(tssh, "ssh_sync",
                            lambda *a, **k: sync_calls.append(1) or 0)
        monkeypatch.setattr(tssh, "ssh_exec",
                            lambda host, argv, *, config, repo_root: exec_calls.append(argv[0]) or 0)
        monkeypatch.setattr(act, "run_thin_up",
                            lambda *a, **k: pytest.fail("thin path must not run without --thin"))
        monkeypatch.setattr(sys, "argv", ["ciu", "up", "--host", "web"])
        with pytest.raises(SystemExit) as exc:
            cli_mod.main()
        assert exc.value.code == 0
        assert len(sync_calls) == 1
        assert exec_calls[0].startswith("cd /opt/app && ciu env generate && ciu render && ciu up")

    def test_docker_optional_host_nudges_on_docker_path(self, tmp_path, monkeypatch, capsys):
        """docker_optional host + docker --host path => stderr nudge, still proceeds (S14.6)."""
        monkeypatch.setenv("REPO_ROOT", str(tmp_path))
        monkeypatch.delenv("CIU_SSH_INSECURE_TOFU", raising=False)
        _write_thin_hosts(tmp_path, extra="docker_optional = true\n")
        import ciu.transport_ssh as tssh
        monkeypatch.setattr(tssh, "ssh_sync", lambda *a, **k: 0)
        monkeypatch.setattr(tssh, "ssh_exec", lambda host, argv, *, config, repo_root: 0)
        monkeypatch.setattr(sys, "argv", ["ciu", "up", "--host", "web"])
        with pytest.raises(SystemExit) as exc:
            cli_mod.main()
        assert exc.value.code == 0
        assert "docker_optional" in capsys.readouterr().err

    def test_health_thin_runs_health_verb(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REPO_ROOT", str(tmp_path))
        _write_thin_hosts(tmp_path)
        captured = {}
        monkeypatch.setattr(act, "run_activation",
                            lambda host_cfg, verb, *, config, repo_root, bundle_dir, remaining=None:
                            captured.update(verb=verb, bundle_dir=bundle_dir, remaining=remaining) or 0)
        monkeypatch.setattr(sys, "argv", ["ciu", "health", "--host", "web", "--thin"])
        with pytest.raises(SystemExit) as exc:
            cli_mod.main()
        assert exc.value.code == 0
        assert captured["verb"] == "health"
        assert captured["bundle_dir"] == "/opt/app"

    def test_health_host_without_thin_unchanged(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REPO_ROOT", str(tmp_path))
        monkeypatch.delenv("CIU_SSH_INSECURE_TOFU", raising=False)
        _write_thin_hosts(tmp_path)
        exec_calls = []
        import ciu.transport_ssh as tssh
        monkeypatch.setattr(tssh, "ssh_exec",
                            lambda host, argv, *, config, repo_root: exec_calls.append(argv[0]) or 0)
        monkeypatch.setattr(act, "run_activation",
                            lambda *a, **k: pytest.fail("thin health must not run without --thin"))
        monkeypatch.setattr(sys, "argv", ["ciu", "health", "--host", "web"])
        with pytest.raises(SystemExit) as exc:
            cli_mod.main()
        assert exc.value.code == 0
        assert exec_calls[0] == "ciu health"
