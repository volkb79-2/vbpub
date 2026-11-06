"""cgpd: the cli-extended service entry (CGPCTL-SPEC.md section 3)."""

from __future__ import annotations

import json
import os
import signal
import tempfile

import pytest

import _fakectl
from cgprofile import access, daemon_config, serve
from cgprofile.cli import cgpd


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in daemon_config.STALE_ENV:
        monkeypatch.delenv(name, raising=False)
    # `run` exports the fixed keys for the modules that read them at call time
    for name in ("CGPROFILE_PROC_ROOT", "CGPROFILE_DAMON_LOCK_PATH", "CGPROFILE_BUSCTL"):
        monkeypatch.setenv(name, os.environ.get(name, "x"))
        monkeypatch.delenv(name)


@pytest.fixture()
def sockdir():
    with tempfile.TemporaryDirectory(prefix="cgd") as directory:
        yield directory


@pytest.fixture()
def cli(capsys):
    def run(*argv):
        code = cgpd.build_cli().run(argv=list(argv))
        captured = capsys.readouterr()
        return code, captured.out, captured.err
    return run


def write_config(sockdir, text="", name="cgpd.toml"):
    path = os.path.join(sockdir, name)
    with open(path, "w") as fh:
        fh.write(text)
    return path


# ── surface ────────────────────────────────────────────────────────────────

def test_identity_and_version(capsys, monkeypatch):
    monkeypatch.setattr(cgpd, "installed_version", lambda: "9.9.9")
    assert cgpd.identity().command == "cgpd"
    assert cgpd.main(["--version"]) == 0
    assert capsys.readouterr().out == "cgpd 9.9.9\n"


def test_main_reads_sys_argv_when_called_without_arguments(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["cgpd", "--version"])
    assert cgpd.main() == 0
    assert capsys.readouterr().out.startswith("cgpd ")


@pytest.mark.parametrize("argv", [["--help"], ["config", "--help"], ["run", "--help"]])
def test_help_never_starts_the_service(cli, monkeypatch, argv):
    monkeypatch.setattr(cgpd, "preflight", lambda *a: pytest.fail("help must not preflight"))
    code, out, _err = cli(*argv)
    assert code == 0 and "cgpd" in out


def test_help_lists_the_verbs_and_no_doctor_or_skills(cli):
    _code, out, _err = cli("--help")
    for verb in ("run", "reload", "config"):
        assert verb in out
    assert "doctor" in out and "skills" not in out


def test_malformed_arguments_never_start_the_service(cli, monkeypatch):
    monkeypatch.setattr(cgpd, "preflight", lambda *a: pytest.fail("must not preflight"))
    assert cli("run", "--bogus")[0] == 2
    assert cli("bogus")[0] == 2


def test_log_is_a_key_value_line(capsys):
    cgpd.log("start", version="2.0.0", config="/x")
    assert capsys.readouterr().err == "cgpd: event=start version=2.0.0 config=/x\n"
    cgpd.log("bare")
    assert capsys.readouterr().err == "cgpd: event=bare\n"


def test_config_location():
    class Args:
        config = "/tmp/x.toml"

    class Nothing:
        config = None

    assert cgpd.config_location(Args) == ("/tmp/x.toml", True)
    assert cgpd.config_location(Nothing) == (daemon_config.DEFAULT_CONFIG_PATH, False)


# ── config check ───────────────────────────────────────────────────────────

class TestConfigCheck:
    def test_valid_file(self, cli, sockdir):
        path = write_config(sockdir, "interval = 2.5\nkeep_days = 3\n")
        code, out, _ = cli("--config", path, "config", "check")
        assert code == 0 and f"config ok: {path} (2 key(s), digest " in out

    def test_json(self, cli, sockdir):
        path = write_config(sockdir, "interval = 2.5\n")
        code, out, _ = cli("--config", path, "config", "check", "--json")
        doc = json.loads(out.strip().splitlines()[-1])
        assert code == 0 and doc["ok"] is True and doc["keys"] == ["interval"] and doc["file"] == path

    def test_file_option_wins_over_config(self, cli, sockdir):
        good = write_config(sockdir, "interval = 2\n", "good.toml")
        bad = write_config(sockdir, "bogus = 1\n", "bad.toml")
        assert cli("--config", bad, "config", "check", "--file", good)[0] == 0

    @pytest.mark.parametrize("text, fragment", [
        ("bogus = 1\n", "bogus"),
        ("interval = 'fast'\n", "interval"),
        ("max_sessions = 9999\n", "max_sessions"),
        ("gates_slice = 'a--b.slice'\n", "usable slice name"),
        ("interval = \n", "cgpd.toml"),
    ])
    def test_invalid_file_is_exit_2(self, cli, sockdir, text, fragment):
        code, _out, err = cli("--config", write_config(sockdir, text), "config", "check")
        assert code == 2 and fragment in err

    def test_missing_explicit_file_is_an_error(self, cli, sockdir):
        code, _out, err = cli("--config", os.path.join(sockdir, "nope.toml"), "config", "check")
        assert code == 2 and "does not exist" in err

    def test_missing_default_file_means_defaults(self, cli, sockdir, monkeypatch):
        monkeypatch.setattr(daemon_config, "DEFAULT_CONFIG_PATH", os.path.join(sockdir, "absent.toml"))
        code, out, _ = cli("config", "check")
        assert code == 0 and "defaults (no file)" in out

    def test_empty_present_file(self, cli, sockdir):
        code, out, _ = cli("--config", write_config(sockdir, ""), "config", "check")
        assert code == 0 and "0 key(s)" in out


# ── preflight ──────────────────────────────────────────────────────────────

class Host:
    """Everything the preflight reads, defaulting to a healthy native host."""

    def __init__(self, sockdir, monkeypatch):
        self.sockdir = sockdir
        self.sessions = os.path.join(sockdir, "sessions")
        self.socket = os.path.join(sockdir, "control", "cgprofile.sock")
        os.makedirs(os.path.dirname(self.socket))
        observer = os.path.join(sockdir, "observer", "cgprofile.sock")
        os.makedirs(os.path.dirname(observer))
        self.config = write_config(sockdir, f'sessions_dir = "{self.sessions}"\n'
                                            f'damon_lock = "{sockdir}/damon.lock"\n'
                                            f'\n[sockets.control]\npath = "{self.socket}"\n'
                                            'group = "test-control"\nmode = "0660"\n'
                                            f'\n[sockets.observer]\npath = "{observer}"\n'
                                            'group = "test-observer"\nmode = "0660"\n')
        self.pid_ns = (7, 7)
        self.slice_problem = None
        monkeypatch.setattr(os, "geteuid", lambda: 0)
        monkeypatch.setattr(access, "local_namespace_inode", lambda name: self.pid_ns[0])
        monkeypatch.setattr(access, "namespace_inode", lambda pid, name, root=None: self.pid_ns[1])
        monkeypatch.setattr(serve, "daemon_slice_problem", lambda cgroup, proc: self.slice_problem)
        monkeypatch.setattr(cgpd, "OVERRIDES_PATH", os.path.join(sockdir, "overrides.toml"))

    def preflight(self, environ=None):
        return cgpd.preflight(self.config, True, environ or {})


@pytest.fixture()
def host(sockdir, monkeypatch):
    return Host(sockdir, monkeypatch)


class TestPreflight:
    def test_a_healthy_host_passes_and_returns_loaded_settings(self, host):
        settings = host.preflight()
        assert settings.get("sessions_dir") == host.sessions
        assert os.path.isdir(host.sessions)

    def test_1_needs_root(self, host, monkeypatch):
        monkeypatch.setattr(os, "geteuid", lambda: 1000)
        with pytest.raises(cgpd.CliFailure) as caught:
            host.preflight()
        assert caught.value.exit_code == 2 and "effective uid is 1000" in str(caught.value)

    def test_2_stale_environment_names_the_replacement(self, host):
        with pytest.raises(cgpd.CliFailure) as caught:
            host.preflight({"CGPROFILE_ALLOW_UIDS": "1000"})
        assert f"CGPROFILE_ALLOW_UIDS is no longer read; set allow_uids in {host.config}" in str(caught.value)

    def test_2_invalid_config_is_refused(self, host):
        write_config(host.sockdir, "bogus = 1\n")
        host.config = os.path.join(host.sockdir, "cgpd.toml")
        with pytest.raises(cgpd.CliFailure) as caught:
            host.preflight()
        assert "bogus" in str(caught.value) and caught.value.exit_code == 2

    @pytest.mark.parametrize("pid_ns", [(7, 8), (None, 7), (7, None)])
    def test_3_pid_namespace_mismatch(self, host, pid_ns):
        host.pid_ns = pid_ns
        with pytest.raises(cgpd.CliFailure) as caught:
            host.preflight()
        assert "pid namespace" in str(caught.value)

    def test_4_unbounded_slice(self, host):
        host.slice_problem = "memory.max of /cgprofile.slice is not a finite positive integer"
        with pytest.raises(cgpd.CliFailure) as caught:
            host.preflight()
        assert "cgprofile.slice does not provably contain this daemon" in str(caught.value)
        assert "memory.max" in str(caught.value)

    def test_5_sessions_dir_cannot_be_created(self, host):
        write_config(host.sockdir, "")
        blocker = os.path.join(host.sockdir, "file")
        write_config(host.sockdir, "x", "file")
        host.config = write_config(host.sockdir, f'sessions_dir = "{blocker}/sessions"\n', "c2.toml")
        with pytest.raises(cgpd.CliFailure) as caught:
            host.preflight()
        assert "cannot create sessions_dir" in str(caught.value)

    def test_5_sessions_dir_not_writable(self, host, monkeypatch):
        monkeypatch.setattr(os, "access", lambda path, mode: False)
        with pytest.raises(cgpd.CliFailure) as caught:
            host.preflight()
        assert "is not writable" in str(caught.value)

    def test_6_socket_path_is_not_probed_by_preflight(self, host):
        host.config = write_config(
            host.sockdir,
            f'sessions_dir = "{host.sessions}"\n\n[sockets.control]\n'
            f'path = "{host.sockdir}/not-created/control.sock"\ngroup = "test-control"\nmode = "0660"\n'
            '\n[sockets.observer]\npath = "/tmp/not-created/observer.sock"\n'
            'group = "test-observer"\nmode = "0660"\n',
            "unbound.toml",
        )
        assert host.preflight().get("sockets.control")["path"].endswith("control.sock")


class TestProbe:
    def test_nothing_listening(self, sockdir):
        assert cgpd.probe_daemon(os.path.join(sockdir, "none.sock")) is None

    def test_a_daemon_answers(self, sockdir):
        daemon = _fakectl.FakeDaemon(os.path.join(sockdir, "a.sock"), {"version": lambda a, c: dict(_fakectl.V2_VERSION)})
        try:
            assert cgpd.probe_daemon(daemon.path) == ("2.0.0", os.getpid())
        finally:
            daemon.close()

    def test_an_unversioned_answer(self, sockdir):
        daemon = _fakectl.FakeDaemon(os.path.join(sockdir, "a.sock"), {"version": lambda a, c: {"ok": True}})
        try:
            assert cgpd.probe_daemon(daemon.path) == ("unknown", os.getpid())
        finally:
            daemon.close()

    @pytest.mark.parametrize("reply", [
        {"ok": False, "error": {"code": "busy", "message": "x"}}, b"not json\n", b"",
    ])
    def test_a_daemon_that_does_not_answer_version_is_not_counted(self, sockdir, reply):
        daemon = _fakectl.FakeDaemon(os.path.join(sockdir, "a.sock"), {"version": lambda a, c: reply})
        try:
            assert cgpd.probe_daemon(daemon.path) is None
        finally:
            daemon.close()


# ── run ────────────────────────────────────────────────────────────────────

class StubServer:
    built = []
    result = 0
    error = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        StubServer.built.append(self)

    def serve_forever(self):
        if StubServer.error is not None:
            raise StubServer.error
        return StubServer.result


class TestRun:
    @pytest.fixture()
    def wired(self, host, monkeypatch):
        StubServer.built, StubServer.result, StubServer.error = [], 0, None
        monkeypatch.setattr(serve, "SessionServer", StubServer)
        return host

    def test_run_builds_the_server_from_the_settings(self, cli, wired, capsys):
        code, _out, err = cli("--config", wired.config, "run", "--self-bind")
        assert code == 0
        kwargs = StubServer.built[0].kwargs
        assert kwargs["sessions_dir"] == wired.sessions and kwargs["socket_path"] == wired.socket
        assert kwargs["observer_socket_path"].endswith("/observer/cgprofile.sock")
        assert kwargs["proc_root"] == "/proc" and kwargs["settings"].digest
        assert "cgpd: event=start" in err and f"socket={wired.socket}" in err
        assert os.environ["CGPROFILE_PROC_ROOT"] == "/proc"
        assert os.environ["CGPROFILE_DAMON_LOCK_PATH"].endswith("/damon.lock")
        assert os.environ["CGPROFILE_BUSCTL"] == "busctl"

    def test_allow_uids_come_from_the_config_not_the_environment(self, cli, wired):
        # a top-level key must precede the [sockets.*] tables of the fixture config
        with open(wired.config) as fh:
            existing = fh.read()
        with open(wired.config, "w") as fh:
            fh.write("allow_uids = [0, 1000]\n" + existing)
        assert cli("--config", wired.config, "run", "--self-bind")[0] == 0
        assert StubServer.built[0].kwargs["settings"].get("allow_uids") == [0, 1000]

    def test_a_stale_allow_uids_variable_refuses_to_start(self, cli, wired, monkeypatch):
        monkeypatch.setenv("CGPROFILE_ALLOW_UIDS", "0,1000")
        code, _out, err = cli("--config", wired.config, "run", "--self-bind")
        assert code == 2 and "CGPROFILE_ALLOW_UIDS is no longer read; set allow_uids in" in err
        assert StubServer.built == []

    def test_a_lost_socket_exit_code_is_returned(self, cli, wired):
        StubServer.result = 3
        assert cli("--config", wired.config, "run", "--self-bind")[0] == 3

    def test_a_socket_that_cannot_be_bound_is_a_refusal(self, cli, wired):
        StubServer.error = serve.ControlSocketError(0, "another daemon still answers")
        code, _out, err = cli("--config", wired.config, "run", "--self-bind")
        assert code == 2 and "cgpd refuses to start: another daemon still answers" in err

    def test_a_failed_preflight_never_builds_a_server(self, cli, wired, monkeypatch):
        monkeypatch.setattr(os, "geteuid", lambda: 1000)
        assert cli("--config", wired.config, "run", "--self-bind")[0] == 2
        assert StubServer.built == []

    def test_run_without_activation_or_self_bind_is_refused(self, cli, wired):
        code, _out, err = cli("--config", wired.config, "run")
        assert code == 2 and "no systemd listening descriptors" in err
        assert StubServer.built == []

    def test_run_passes_activated_descriptors_to_the_server(self, cli, wired, monkeypatch):
        sockets = {"control": object()}
        monkeypatch.setattr(serve, "adopt_systemd_sockets", lambda environ: sockets)
        assert cli("--config", wired.config, "run")[0] == 0
        assert StubServer.built[0].kwargs["activated_sockets"] is sockets

    def test_run_refuses_empty_systemd_socket_set(self, cli, wired, monkeypatch):
        monkeypatch.setattr(serve, "adopt_systemd_sockets", lambda environ: {})
        code, _out, err = cli("--config", wired.config, "run")
        assert code == 2 and "LISTEN_FDS=0" in err and StubServer.built == []


# ── reload ─────────────────────────────────────────────────────────────────

class TestReload:
    @pytest.fixture()
    def signalled(self, monkeypatch):
        sent = []
        monkeypatch.setattr(os, "geteuid", lambda: 0)
        monkeypatch.setattr(os, "kill", lambda pid, sig: sent.append((pid, sig)))
        return sent

    def _daemon(self, sockdir, **over):
        table = {"version": lambda a, c: dict(_fakectl.V2_VERSION),
                 "config-reset": lambda a, c: {"ok": True, "contract": 2, "config-reset": {"reset": [], "values": {}}}}
        table.update(over)
        return _fakectl.FakeDaemon(os.path.join(sockdir, "r.sock"), table)

    def _config(self, sockdir):
        return write_config(sockdir, '[sockets.control]\n'
                                      f'path = "{sockdir}/r.sock"\n'
                                      'group = "control"\nmode = "0660"\n')

    def test_needs_root(self, cli, sockdir, monkeypatch):
        monkeypatch.setattr(os, "geteuid", lambda: 1000)
        code, _out, err = cli("--config", self._config(sockdir), "reload")
        assert code == 2 and "needs root" in err

    def test_an_unreadable_config_is_refused(self, cli, sockdir, signalled):
        code, _out, err = cli("--config", os.path.join(sockdir, "nope.toml"), "reload")
        assert code == 2 and "does not exist" in err

    def test_no_daemon_is_exit_3(self, cli, sockdir, signalled):
        code, _out, err = cli("--config", self._config(sockdir), "reload")
        assert code == 3 and "no daemon answers" in err and signalled == []

    def test_default_socket_when_the_file_does_not_name_one(self, cli, sockdir, signalled, monkeypatch):
        monkeypatch.setattr(daemon_config, "DEFAULT_CONFIG_PATH", write_config(sockdir, ""))
        monkeypatch.setitem(daemon_config.KEYS, "sockets.control", daemon_config.KeySpec(
            "sockets.control", "socket", {"path": os.path.join(sockdir, "r.sock"),
                                           "group": "control", "mode": "0660"},
            False, False, None, None))
        daemon = self._daemon(sockdir)
        try:
            code, out, _ = cli("reload")
        finally:
            daemon.close()
        assert code == 0 and signalled == [(os.getpid(), signal.SIGHUP)]

    def test_sends_sighup_to_the_listener(self, cli, sockdir, signalled):
        daemon = self._daemon(sockdir)
        try:
            code, out, _ = cli("--config", self._config(sockdir), "reload")
        finally:
            daemon.close()
        assert code == 0 and signalled == [(os.getpid(), signal.SIGHUP)]
        assert f"reload signalled to pid {os.getpid()} (cgpd 2.0.0)" in out
        assert [r["verb"] for r in daemon.requests] == ["version"]

    def test_json(self, cli, sockdir, signalled):
        daemon = self._daemon(sockdir)
        try:
            code, out, _ = cli("--config", self._config(sockdir), "reload", "--json")
        finally:
            daemon.close()
        doc = json.loads(out.strip().splitlines()[-1])
        assert doc == {"schema": 1, "ok": True, "pid": os.getpid(), "version": "2.0.0",
                       "reset_overrides": False}

    def test_reset_overrides_goes_through_the_socket_first(self, cli, sockdir, signalled):
        daemon = self._daemon(sockdir)
        try:
            code, _out, _ = cli("--config", self._config(sockdir), "reload", "--reset-overrides")
        finally:
            daemon.close()
        assert code == 0 and signalled == [(os.getpid(), signal.SIGHUP)]
        assert [(r["verb"], r["args"], r["contract"]) for r in daemon.requests][-1] == (
            "config-reset", {"all": True}, 2)

    def test_reset_overrides_refused_sends_no_signal(self, cli, sockdir, signalled):
        refusal = {"ok": False, "error": {"code": "peer-refused", "message": "uid 0 only"}}
        daemon = self._daemon(sockdir, **{"config-reset": lambda a, c: refusal})
        try:
            code, _out, err = cli("--config", self._config(sockdir), "reload", "--reset-overrides")
        finally:
            daemon.close()
        assert code == 2 and "uid 0 only" in err and signalled == []

    def test_reset_overrides_against_a_contract_1_daemon(self, cli, sockdir, signalled):
        daemon = self._daemon(sockdir, version=lambda a, c: dict(_fakectl.V1_VERSION))
        try:
            code, _out, err = cli("--config", self._config(sockdir), "reload", "--reset-overrides")
        finally:
            daemon.close()
        assert code == 3 and "needs ctl contract 2" in err and signalled == []
