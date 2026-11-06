"""Service config, unit rendering, socket activation, and toposd behavior."""

from __future__ import annotations

import fcntl
import json
import os
import select
import signal
import socket
import subprocess
import sys
import sysconfig
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

from topos.service.activation import ActivationError, adopt_listeners
from topos.service.config import (
    DEFAULT_CONFIG_PATH,
    DEFAULTS,
    ConfigError,
    defaults,
    load_config,
    parse_config,
)
from topos.service.units import render_units


REPO_ROOT = Path(__file__).resolve().parents[2]
TOPOS_SRC = REPO_ROOT / "topos" / "src"
CLI_EXTENDED_SRC = REPO_ROOT / "libraries" / "cli-extended" / "src"
CLI_SCRIPT = "from topos.service.cli import main; raise SystemExit(main())"
DEFAULT_CONFIG_TEXT = '''state_dir = "/run/topos/state"

[sockets.observe]
path = "/run/topos/observe/topos.sock"
group = "topos-observe"
mode = "0660"
'''


@contextmanager
def _descriptors_at_systemd_start(listeners: list[socket.socket]) -> Iterator[None]:
    """Temporarily place duplicated descriptors at fd 3 onward."""
    targets = list(range(3, 3 + len(listeners)))
    backups: list[int | None] = []
    backup_inheritable: list[bool | None] = []
    for target in targets:
        try:
            backups.append(os.dup(target))
            backup_inheritable.append(os.get_inheritable(target))
        except OSError:
            backups.append(None)
            backup_inheritable.append(None)
    sources = [fcntl.fcntl(item.fileno(), fcntl.F_DUPFD_CLOEXEC, 64) for item in listeners]
    try:
        for target, source in zip(targets, sources, strict=True):
            os.dup2(source, target, inheritable=True)
        yield
    finally:
        for target, backup, inheritable in zip(targets, backups, backup_inheritable, strict=True):
            try:
                os.close(target)
            except OSError:
                pass
            if backup is not None:
                os.dup2(backup, target, inheritable=bool(inheritable))
                os.close(backup)
        for source in sources:
            os.close(source)


def _listening_socket(path: Path) -> socket.socket:
    path.parent.mkdir(parents=True, exist_ok=True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen()
    return listener


def _write_config(path: Path, *, socket_path: Path | None = None) -> Path:
    content = DEFAULT_CONFIG_TEXT
    if socket_path is not None:
        content = content.replace("/run/topos/observe/topos.sock", str(socket_path))
    path.write_text(content, encoding="utf-8")
    return path


def _clean_environment() -> dict[str, str]:
    environ = os.environ.copy()
    for key in ("LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES"):
        environ.pop(key, None)
    environ["PYTHONPATH"] = os.pathsep.join((str(TOPOS_SRC), str(REPO_ROOT / "topos"), str(CLI_EXTENDED_SRC)))
    return environ


def _toposd_command(*args: str | Path) -> list[str]:
    return [sys.executable, "-c", CLI_SCRIPT, *(str(arg) for arg in args)]


def _spawn_toposd(
    args: list[str | Path],
    *,
    listeners: list[socket.socket] | None = None,
    names: str = "observe",
) -> subprocess.Popen[str]:
    if not listeners:
        return subprocess.Popen(
            _toposd_command(*args),
            cwd=REPO_ROOT,
            env=_clean_environment(),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    launcher = '''
import fcntl, os, sys
fds = [int(value) for value in sys.argv[1].split(",")]
names = sys.argv[2]
sources = [fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 64) for fd in fds]
for target, source in enumerate(sources, 3):
    os.dup2(source, target, inheritable=True)
for source in sources:
    os.close(source)
os.environ["LISTEN_PID"] = str(os.getpid())
os.environ["LISTEN_FDS"] = str(len(fds))
os.environ["LISTEN_FDNAMES"] = names
os.execv(sys.executable, [sys.executable, "-c", sys.argv[3], *sys.argv[4:]])
'''
    return subprocess.Popen(
        [
            sys.executable,
            "-c",
            launcher,
            ",".join(str(listener.fileno()) for listener in listeners),
            names,
            CLI_SCRIPT,
            *(str(arg) for arg in args),
        ],
        pass_fds=tuple(listener.fileno() for listener in listeners),
        cwd=REPO_ROOT,
        env=_clean_environment(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _request_hello(path: Path) -> dict[str, object]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(10.0)
        client.connect(str(path))
        client.sendall(b'{"id":"service-test","op":"hello","v":1}\n')
        client.shutdown(socket.SHUT_WR)
        data = bytearray()
        while chunk := client.recv(65536):
            data.extend(chunk)
    return json.loads(data.decode("utf-8"))


def _wait_for_hello(process: subprocess.Popen[str], path: Path) -> tuple[dict[str, object], str]:
    deadline = time.monotonic() + 10.0
    if process.stdout is None:
        pytest.fail("toposd stdout is unavailable")
    startup_line = bytearray()
    while not startup_line.endswith(b"\n"):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            pytest.fail("toposd did not announce its bound listener before the deadline")
        readable, _, _ = select.select([process.stdout], [], [], remaining)
        if not readable:
            pytest.fail("toposd did not announce its bound listener before the deadline")
        chunk = os.read(process.stdout.fileno(), 1)
        if not chunk:
            out, err = process.communicate()
            pytest.fail(
                f"toposd exited before serving: rc={process.returncode}\n"
                f"startup={startup_line.decode(errors='replace')}\nstdout={out}\nstderr={err}"
            )
        startup_line.extend(chunk)
    startup_text = startup_line.decode("utf-8")
    try:
        return _request_hello(path), startup_text
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        pytest.fail(f"toposd announced its listener but did not answer hello: {exc}")


def _stop_process(process: subprocess.Popen[str]) -> tuple[str, str]:
    if process.poll() is None:
        process.send_signal(signal.SIGTERM)
    try:
        stdout, stderr = process.communicate(timeout=10.0)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
        pytest.fail(f"toposd did not stop after SIGTERM\nstdout={stdout}\nstderr={stderr}")
    return stdout, stderr


def test_missing_implicit_file_uses_defaults_and_named_missing_file_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import topos.service.config as service_config

    implicit = tmp_path / "toposd.toml"
    monkeypatch.setattr(service_config, "DEFAULT_CONFIG_PATH", implicit)
    assert defaults() == DEFAULTS
    assert load_config() == DEFAULTS
    with pytest.raises(ConfigError, match="config file .* does not exist"):
        load_config(tmp_path / "named-missing.toml")


def test_named_config_read_error_is_reported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "unreadable.toml"
    original_read_text = Path.read_text

    def fail_named_config(path: Path, *args, **kwargs):
        if path == config_path:
            raise PermissionError(13, "Permission denied", str(path))
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_named_config)
    with pytest.raises(ConfigError, match="cannot read config file .* Permission denied"):
        load_config(config_path)


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("unknown = true\n", "unknown key 'unknown'"),
        ("[sockets.control]\npath = '/run/topos/control.sock'\n", "unknown key 'sockets.control'"),
        ("[sockets.observe]\nlegacy = ['/run/topos/topos.sock']\n", "unknown key 'sockets.observe.legacy'"),
        ("[sockets.observe]\npath = 'relative.sock'\n", "sockets.observe.path must be absolute"),
        ("[sockets.observe]\npath = '/run/topos/observe/bad path.sock'\n", "sockets.observe.path must not contain"),
        ("[sockets.observe]\npath = '/run/topos/observe/%n.sock'\n", "sockets.observe.path must not contain"),
        ("[sockets.observe]\npath = '/run/topos/observe/../observe.sock'\n", "sockets.observe.path must not contain '..'"),
        ("[sockets.observe]\npath = '/run/topos/observe/" + chr(92) + "bad.sock'\n", "sockets.observe.path must not contain"),
        ("[sockets.observe]\npath = \"/run/topos/observe/bad" + chr(92) + "u0001.sock\"\n", "sockets.observe.path must not contain"),
        ("[sockets.observe]\npath = 7\n", "sockets.observe.path must be a string"),
        ("[sockets.observe]\npath = '/run/topos/observe/topos'\n", "sockets.observe.path must end in .sock"),
        ("[sockets.observe]\ngroup = 'Topos'\n", "sockets.observe.group must match"),
        ("[sockets.observe]\ngroup = '9abc'\n", "sockets.observe.group must match"),
        ("[sockets.observe]\ngroup = 7\n", "sockets.observe.group must match"),
        ("[sockets.observe]\nmode = '660'\n", "sockets.observe.mode must be a 4-digit octal string"),
        ("[sockets.observe]\nmode = '08a0'\n", "sockets.observe.mode must be a 4-digit octal string"),
        ("[sockets.observe]\nmode = 660\n", "sockets.observe.mode must be a 4-digit octal string"),
        ("[sockets.observe]\nmode = '0770'\n", "sockets.observe.mode contains bits outside 0o666"),
        ("state_dir = 'relative'\n", "state_dir must be absolute"),
        ("state_dir = 7\n", "state_dir must be a string"),
        ("state_dir = '/var/lib/topos'\n", "state_dir must lie under /run/"),
        ("state_dir = '/run'\n", "state_dir must lie under /run/"),
        ("state_dir = '/run/topos'\n", "state_dir must be neither a parent nor a child"),
        ("state_dir = '/run/topos/state dir'\n", "state_dir must not contain"),
        ("state_dir = '/run/topos/%n'\n", "state_dir must not contain"),
        ("state_dir = '/run/topos/../state'\n", "state_dir must not contain '..'"),
        ("state_dir = '/run/topos/" + chr(92) + "state'\n", "state_dir must not contain"),
        ("state_dir = \"/run/topos/state" + chr(92) + "u0001bad\"\n", "state_dir must not contain"),
        (
            "state_dir = '/run/topos/state'\n[sockets.observe]\npath = '/run/topos/state/topos.sock'\n",
            "sockets.observe.path must not be inside state_dir",
        ),
        (
            "state_dir = '/run/topos/observe/state'\n[sockets.observe]\npath = '/run/topos/observe/topos.sock'\n",
            "state_dir must be neither a parent nor a child",
        ),
    ],
)
def test_config_validation_names_invalid_keys_and_values(contents: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        parse_config(contents)


def test_config_rejects_wrong_table_shapes() -> None:
    with pytest.raises(ConfigError, match="sockets must be a table"):
        parse_config("sockets = []\n")
    with pytest.raises(ConfigError, match="sockets must contain exactly one level: observe"):
        parse_config("[sockets]\n")
    with pytest.raises(ConfigError, match="sockets.observe must be a table"):
        parse_config("[sockets]\nobserve = 'bad'\n")


def test_config_rejects_newline_injection_in_unit_path() -> None:
    contents = (
        '[sockets.observe]\npath = "/run/topos/observe/topos.sock'
        + chr(92)
        + 'nExecStart=/tmp/attacker.sock"\n'
    )
    with pytest.raises(ConfigError, match="sockets.observe.path must not contain"):
        parse_config(contents)


def test_config_rejects_invalid_toml_with_source_name() -> None:
    with pytest.raises(ConfigError, match="service.toml: invalid TOML"):
        parse_config("[sockets.observe\n", source="service.toml")


def test_rendered_default_units_match_packaged_assets_byte_for_byte() -> None:
    assets_dir = TOPOS_SRC / "topos" / "assets" / "systemd"
    assets = {
        "topos-observe.socket": (assets_dir / "topos-observe.socket").read_text(encoding="utf-8"),
        "topos.service": (assets_dir / "topos.service").read_text(encoding="utf-8"),
        "topos.slice": (assets_dir / "topos.slice").read_text(encoding="utf-8"),
        "topos.sysusers": (assets_dir / "topos.sysusers").read_text(encoding="utf-8"),
    }
    assert assets == render_units(DEFAULTS)
    assert "RestartPreventExitStatus=2\n" in assets["topos.service"]


def test_mapped_observe_group_is_only_a_sysusers_comment() -> None:
    cfg = parse_config('[sockets.observe]\ngroup = "docker"\n')
    assert render_units(cfg)["topos.sysusers"] == "# observe level mapped to existing group docker; nothing created\n"


def test_config_check_prints_ok_and_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from topos.service.cli import main

    config_path = _write_config(tmp_path / "toposd.toml")
    assert main(["config", "check", "--config", str(config_path)]) == 0
    assert capsys.readouterr().out == "ok\n"
    assert main(["config", "check", "--json", "--config", str(config_path)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert main(["config", "check", "--config", str(tmp_path / "missing.toml")]) == 2
    assert "config file" in capsys.readouterr().err


def test_unit_print_emits_all_files_or_one_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from topos.service.cli import main

    config_path = _write_config(tmp_path / "toposd.toml")
    assert main(["unit", "print", "--config", str(config_path)]) == 0
    all_output = capsys.readouterr().out
    for name in render_units(DEFAULTS):
        assert f"# --- {name}\n" in all_output
    assert main(["unit", "print", "--config", str(config_path), "--unit", "topos-observe.socket"]) == 0
    assert capsys.readouterr().out == render_units(DEFAULTS)["topos-observe.socket"]
    assert main(["unit", "print", "--json", "--config", str(config_path)]) == 0
    assert json.loads(capsys.readouterr().out)["units"] == render_units(DEFAULTS)


def test_unit_check_clean_drift_and_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from topos.service.cli import main

    config_path = _write_config(tmp_path / "toposd.toml")
    root = tmp_path / "root"
    units = render_units(DEFAULTS)
    for name, contents in units.items():
        path = (
            root / "etc" / "sysusers.d" / "topos.conf"
            if name == "topos.sysusers"
            else root / "etc" / "systemd" / "system" / name
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")

    assert main(["unit", "check", "--config", str(config_path), "--root", str(root)]) == 0
    assert capsys.readouterr().out == "ok\n"

    socket_file = root / "etc" / "systemd" / "system" / "topos-observe.socket"
    socket_file.write_text(socket_file.read_text(encoding="utf-8") + "# drift\n", encoding="utf-8")
    assert main(["unit", "check", "--config", str(config_path), "--root", str(root), "--diff"]) == 1
    drift = capsys.readouterr().out
    assert f"drifted: {socket_file}" in drift
    assert "@@" in drift

    (root / "etc" / "systemd" / "system" / "topos.slice").unlink()
    assert main(["unit", "check", "--config", str(config_path), "--root", str(root), "--diff"]) == 1
    missing = capsys.readouterr().out
    assert "missing:" in missing
    assert "--- /dev/null" in missing


def test_unit_check_reports_unreadable_installed_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from topos.service.cli import main

    config_path = _write_config(tmp_path / "toposd.toml")
    root = tmp_path / "root"
    target = root / "etc/systemd/system/topos-observe.socket"
    target.parent.mkdir(parents=True)
    target.write_text("", encoding="utf-8")
    original_read_text = Path.read_text

    def fail_target(path: Path, *args, **kwargs):
        if path == target:
            raise PermissionError(13, "Permission denied", str(path))
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_target)
    assert main(["unit", "check", "--config", str(config_path), "--root", str(root)]) == 2
    assert "cannot read installed file" in capsys.readouterr().err


def test_self_bind_chmod_failure_closes_server_and_unlinks_owned_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import topos.daemon.api as daemon_api
    from topos.daemon import FrameBroker

    path = tmp_path / "chmod-failure.sock"
    broker = FrameBroker([])

    def fail_chmod(*_args, **_kwargs):
        raise PermissionError("mode update denied")

    monkeypatch.setattr(daemon_api.os, "chmod", fail_chmod)
    with pytest.raises(PermissionError, match="mode update denied"):
        daemon_api.serve_versioned_unix_socket(path, broker, daemon_api.DaemonApi(broker))
    assert not path.exists()


def test_passed_listener_is_adopted_without_unlinking_its_node(tmp_path: Path) -> None:
    import topos.daemon.api as daemon_api
    from topos.daemon import FrameBroker

    path = tmp_path / "passed.sock"
    listener = _listening_socket(path)
    broker = FrameBroker([])
    server = daemon_api.serve_versioned_listener(
        listener, broker, daemon_api.DaemonApi(broker), owned_path=None
    )
    try:
        assert server.socket is listener
        assert server.server_address == str(path)
        assert listener.get_inheritable() is False
    finally:
        server.server_close()
    assert path.exists()


def test_passed_listener_setup_failure_closes_adopted_listener(tmp_path: Path) -> None:
    import topos.daemon.api as daemon_api
    from topos.daemon import FrameBroker

    class FailingListener:
        closed = False

        def getsockname(self) -> str:
            return str(tmp_path / "failed-adoption.sock")

        def set_inheritable(self, _value: bool) -> None:
            raise OSError("close-on-exec denied")

        def close(self) -> None:
            self.closed = True

    listener = FailingListener()
    broker = FrameBroker([])
    with pytest.raises(OSError, match="close-on-exec denied"):
        daemon_api.serve_versioned_listener(
            listener, broker, daemon_api.DaemonApi(broker), owned_path=None
        )
    assert listener.closed is True


def test_adopt_listeners_accepts_listening_unix_stream_and_cleans_environment(tmp_path: Path) -> None:
    listener = _listening_socket(tmp_path / "observe.sock")
    environ = {"LISTEN_PID": str(os.getpid()), "LISTEN_FDS": "1", "LISTEN_FDNAMES": "observe"}
    try:
        with _descriptors_at_systemd_start([listener]):
            adopted = adopt_listeners({"observe"}, environ=environ)
            assert adopted is not None
            assert len(adopted["observe"]) == 1
            assert adopted["observe"][0].family == socket.AF_UNIX
            assert not adopted["observe"][0].get_inheritable()
            adopted["observe"][0].close()
        assert not {"LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES"}.intersection(environ)
    finally:
        listener.close()


def test_adopt_listeners_returns_none_without_touching_mismatched_pid() -> None:
    environ = {"LISTEN_PID": "41", "LISTEN_FDS": "bad", "LISTEN_FDNAMES": "bad"}
    before = environ.copy()
    assert adopt_listeners({"observe"}, environ=environ, pid=lambda: 42) is None
    assert environ == before
    assert adopt_listeners({"observe"}, environ={}, pid=lambda: 42) is None


def test_adopt_listeners_rejects_malformed_pid_and_zero_count() -> None:
    with pytest.raises(ActivationError, match="LISTEN_PID must be an integer"):
        adopt_listeners({"observe"}, environ={"LISTEN_PID": "bad"})
    with pytest.raises(ActivationError, match="LISTEN_FDS must be an integer greater than or equal to 1"):
        adopt_listeners(
            {"observe"},
            environ={"LISTEN_PID": str(os.getpid()), "LISTEN_FDS": "0", "LISTEN_FDNAMES": ""},
        )


def test_adopt_listeners_refuses_count_and_name_errors(tmp_path: Path) -> None:
    listener = _listening_socket(tmp_path / "observe.sock")
    try:
        with _descriptors_at_systemd_start([listener]):
            with pytest.raises(ActivationError, match="LISTEN_FDS"):
                adopt_listeners(
                    {"observe"},
                    environ={"LISTEN_PID": str(os.getpid()), "LISTEN_FDS": "bad", "LISTEN_FDNAMES": "observe"},
                )
            with pytest.raises(ActivationError, match="LISTEN_FDNAMES"):
                adopt_listeners(
                    {"observe"},
                    environ={"LISTEN_PID": str(os.getpid()), "LISTEN_FDS": "2", "LISTEN_FDNAMES": "observe"},
                )
            with pytest.raises(ActivationError, match="LISTEN_FDNAMES.*other"):
                adopt_listeners(
                    {"observe"},
                    environ={"LISTEN_PID": str(os.getpid()), "LISTEN_FDS": "1", "LISTEN_FDNAMES": "other"},
                )
    finally:
        listener.close()


def test_adopt_listeners_refuses_non_listening_descriptor(tmp_path: Path) -> None:
    path = tmp_path / "bound-but-not-listening.sock"
    path.parent.mkdir(parents=True, exist_ok=True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    try:
        with _descriptors_at_systemd_start([listener]):
            with pytest.raises(ActivationError, match=r"fd 3 is not listening"):
                adopt_listeners(
                    {"observe"},
                    environ={"LISTEN_PID": str(os.getpid()), "LISTEN_FDS": "1", "LISTEN_FDNAMES": "observe"},
                )
    finally:
        listener.close()


@pytest.mark.parametrize(
    ("listener", "message"),
    [
        (lambda: socket.socket(socket.AF_INET, socket.SOCK_STREAM), "fd 3 is not AF_UNIX"),
        (lambda: socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM), "fd 3 is not SOCK_STREAM"),
    ],
)
def test_adopt_listeners_refuses_non_unix_stream_sockets(
    tmp_path: Path, listener, message: str
) -> None:
    if "SOCK_STREAM" in message:
        path = tmp_path / "datagram.sock"
        sock = listener()
        sock.bind(str(path))
    else:
        sock = listener()
        sock.bind(("127.0.0.1", 0))
        sock.listen()
    try:
        with _descriptors_at_systemd_start([sock]):
            with pytest.raises(ActivationError, match=message):
                adopt_listeners(
                    {"observe"},
                    environ={"LISTEN_PID": str(os.getpid()), "LISTEN_FDS": "1", "LISTEN_FDNAMES": "observe"},
                )
    finally:
        sock.close()


def test_adopt_listeners_wraps_descriptor_and_socket_inspection_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import topos.service.activation as activation

    base_environ = {
        "LISTEN_PID": str(os.getpid()),
        "LISTEN_FDS": "1",
        "LISTEN_FDNAMES": "observe",
    }

    def invalid_descriptor(*, fileno: int):
        assert fileno == 3
        raise OSError("bad descriptor")

    monkeypatch.setattr(activation.socket, "socket", invalid_descriptor)
    with pytest.raises(ActivationError, match="fd 3 from LISTEN_FDS is not a socket"):
        adopt_listeners({"observe"}, environ=base_environ.copy())

    class FakeListener:
        family = socket.AF_UNIX

        def __init__(self, fail_at: str) -> None:
            self.fail_at = fail_at
            self.closed = False

        def set_inheritable(self, _value: bool) -> None:
            if self.fail_at == "inheritable":
                raise OSError("close-on-exec denied")

        def getsockopt(self, _level: int, option: int) -> int:
            if option == socket.SO_TYPE:
                if self.fail_at == "type":
                    raise OSError("type query denied")
                return socket.SOCK_STREAM
            if option == socket.SO_ACCEPTCONN and self.fail_at == "accepting":
                raise OSError("accept query denied")
            return 1

        def close(self) -> None:
            self.closed = True

    for fail_at, message in (
        ("inheritable", "fd 3 could not be marked close-on-exec"),
        ("type", "fd 3 could not be checked for SOCK_STREAM"),
        ("accepting", "fd 3 could not be checked for listening state"),
    ):
        listener = FakeListener(fail_at)
        monkeypatch.setattr(activation.socket, "socket", lambda *, fileno: listener)
        with pytest.raises(ActivationError, match=message):
            adopt_listeners({"observe"}, environ=base_environ.copy())
        assert listener.closed is True


def test_toposd_import_isolated_from_daemon_ui_and_collection() -> None:
    stdlib_paths = [
        sysconfig.get_path("stdlib"),
        sysconfig.get_path("platstdlib"),
        sysconfig.get_config_var("DESTSHARED"),
    ]
    paths = [str(TOPOS_SRC), str(CLI_EXTENDED_SRC), *(path for path in stdlib_paths if path)]
    script = '''
import sys
sys.path[:] = sys.argv[1].split(":")
import topos.service.cli
assert not any(name == "topos.daemon" or name.startswith("topos.daemon.") for name in sys.modules)
assert not any(name == "topos.ui" or name.startswith("topos.ui.") for name in sys.modules)
assert "textual" not in sys.modules
assert not any(name == "topos.collect" or name.startswith("topos.collect.") for name in sys.modules)
'''
    result = subprocess.run(
        [sys.executable, "-S", "-c", script, ":".join(paths)],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_toposd_module_entry_point_runs_main(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import runpy

    config_path = _write_config(tmp_path / "toposd.toml")
    loaded_cli = sys.modules.pop("topos.service.cli", None)
    monkeypatch.setattr(sys, "argv", ["toposd", "config", "check", "--config", str(config_path)])
    try:
        with pytest.raises(SystemExit) as exc:
            runpy.run_module("topos.service.cli", run_name="__main__")
    finally:
        if loaded_cli is not None:
            sys.modules["topos.service.cli"] = loaded_cli
    assert exc.value.code == 0


def test_run_refuses_without_activation_or_explicit_self_bind() -> None:
    result = subprocess.run(
        _toposd_command("run"),
        cwd=REPO_ROOT,
        env=_clean_environment(),
        text=True,
        capture_output=True,
        timeout=10.0,
        check=False,
    )
    assert result.returncode == 2
    assert "toposd run needs the socket of topos-observe.socket" in result.stderr


def test_run_refuses_socket_override_without_self_bind(tmp_path: Path) -> None:
    result = subprocess.run(
        _toposd_command("run", "--socket", tmp_path / "observe.sock"),
        cwd=REPO_ROOT,
        env=_clean_environment(),
        text=True,
        capture_output=True,
        timeout=10.0,
        check=False,
    )
    assert result.returncode == 2
    assert "--socket is accepted only with --self-bind" in result.stderr


def test_run_refuses_unknown_and_multiple_observe_descriptors(tmp_path: Path) -> None:
    config_path = _write_config(tmp_path / "toposd.toml", socket_path=tmp_path / "observe.sock")
    other = _listening_socket(tmp_path / "other.sock")
    try:
        process = _spawn_toposd(["run", "--config", config_path], listeners=[other], names="other")
        stdout, stderr = process.communicate(timeout=10.0)
        assert process.returncode == 2
        assert "LISTEN_FDNAMES" in stderr and "other" in stderr
        assert not stdout
    finally:
        other.close()

    first = _listening_socket(tmp_path / "observe-a.sock")
    second = _listening_socket(tmp_path / "observe-b.sock")
    try:
        process = _spawn_toposd(
            ["run", "--config", config_path],
            listeners=[first, second],
            names="observe:observe",
        )
        stdout, stderr = process.communicate(timeout=10.0)
        assert process.returncode == 2
        assert "more than one fd named observe passed" in stderr
        assert not stdout
    finally:
        first.close()
        second.close()


def test_run_reports_missing_observe_mapping_from_activation_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import topos.service.cli as service_cli

    monkeypatch.setattr(service_cli, "adopt_listeners", lambda _expected: {})
    config_path = _write_config(tmp_path / "toposd.toml")
    assert service_cli.main(["run", "--config", str(config_path)]) == 2
    assert "no fd named observe passed; check topos-observe.socket" in capsys.readouterr().err


def test_run_direct_refusals_report_socket_and_activation_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import topos.service.cli as service_cli

    config_path = _write_config(tmp_path / "toposd.toml")
    assert service_cli.main(["run", "--config", str(config_path), "--socket", str(tmp_path / "bad.sock")]) == 2
    assert "--socket is accepted only with --self-bind" in capsys.readouterr().err

    def activation_error(_expected: set[str]):
        raise ActivationError("activation environment is invalid")

    monkeypatch.setattr(service_cli, "adopt_listeners", activation_error)
    assert service_cli.main(["run", "--config", str(config_path)]) == 2
    assert "activation environment is invalid" in capsys.readouterr().err

    monkeypatch.setattr(service_cli, "adopt_listeners", lambda _expected: None)
    assert service_cli.main(["run", "--config", str(config_path)]) == 2
    assert "toposd run needs the socket of topos-observe.socket" in capsys.readouterr().err


def test_run_direct_dispatches_self_bind_and_one_activated_listener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import topos.daemon.run as daemon_run
    import topos.service.cli as service_cli

    config_path = _write_config(tmp_path / "toposd.toml")
    self_bind_path = tmp_path / "self-bind-override.sock"
    calls: list[tuple[Path, socket.socket | None, int]] = []

    def fake_run_daemon(path: Path, *, listener, socket_mode: int, **_kwargs) -> int:
        calls.append((path, listener, socket_mode))
        return 0

    monkeypatch.setattr(daemon_run, "run_daemon", fake_run_daemon)
    monkeypatch.setattr(service_cli, "adopt_listeners", lambda _expected: None)
    assert service_cli.main(
        ["run", "--self-bind", "--socket", str(self_bind_path), "--config", str(config_path)]
    ) == 0
    assert calls[-1] == (self_bind_path, None, 0o660)

    activated_path = tmp_path / "activated.sock"
    listener = _listening_socket(activated_path)
    monkeypatch.setattr(service_cli, "adopt_listeners", lambda _expected: {"observe": [listener]})
    try:
        assert service_cli.main(["run", "--config", str(config_path)]) == 0
        assert calls[-1] == (DEFAULTS.sockets.observe.path, listener, 0o660)
    finally:
        listener.close()


def test_run_uses_service_state_dir_for_bpf_snapshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import topos.daemon.run as daemon_run
    import topos.service.cli as service_cli

    state_dir = Path("/run/topos/custom-state")
    config_path = tmp_path / "toposd.toml"
    config_path.write_text(
        f'state_dir = "{state_dir}"\n\n[sockets.observe]\n'
        'path = "/run/topos/observe/topos.sock"\n',
        encoding="utf-8",
    )
    calls: list[dict[str, object]] = []

    def fake_run_daemon(_path: Path, **kwargs: object) -> int:
        calls.append(kwargs)
        return 0

    monkeypatch.setattr(daemon_run, "run_daemon", fake_run_daemon)
    monkeypatch.setattr(service_cli, "adopt_listeners", lambda _expected: None)

    assert service_cli.main(["run", "--self-bind", "--config", str(config_path)]) == 0
    assert calls[0]["bpf_state_dir"] == state_dir / "bpf"

    override = tmp_path / "explicit-bpf-state"
    assert service_cli.main(
        ["run", "--self-bind", "--config", str(config_path), "--bpf-state-dir", str(override)]
    ) == 0
    assert calls[1]["bpf_state_dir"] == override


def test_run_refuses_self_bind_when_systemd_passed_a_listener(
    tmp_path: Path,
) -> None:
    config_path = _write_config(tmp_path / "toposd.toml")
    listener = _listening_socket(tmp_path / "observe.sock")
    process = _spawn_toposd(
        ["run", "--self-bind", "--config", str(config_path)],
        listeners=[listener],
    )

    try:
        stdout, message = process.communicate(timeout=10.0)
        assert process.returncode == 2
        assert not stdout
        assert "--self-bind" in message
        assert "LISTEN_PID matches" in message
        assert "systemd-passed listener" in message
        assert listener.getsockname() == str(tmp_path / "observe.sock")
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
        listener.close()


def test_run_direct_closes_multiple_listeners_and_oserror_listener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import topos.daemon.run as daemon_run
    import topos.service.cli as service_cli

    config_path = _write_config(tmp_path / "toposd.toml")
    first, second = socket.socketpair()
    monkeypatch.setattr(service_cli, "adopt_listeners", lambda _expected: {"observe": [first, second]})
    assert service_cli.main(["run", "--config", str(config_path)]) == 2
    assert first.fileno() == second.fileno() == -1
    assert "more than one fd named observe passed" in capsys.readouterr().err

    listener = _listening_socket(tmp_path / "error.sock")
    monkeypatch.setattr(service_cli, "adopt_listeners", lambda _expected: {"observe": [listener]})

    def fail_run_daemon(*_args, **_kwargs):
        raise OSError("listener startup failed")

    monkeypatch.setattr(daemon_run, "run_daemon", fail_run_daemon)
    assert service_cli.main(["run", "--config", str(config_path)]) == 2
    assert listener.fileno() == -1
    assert "toposd run: listener startup failed" in capsys.readouterr().err


def test_activated_run_serves_and_leaves_socket_node_on_sigterm(tmp_path: Path) -> None:
    path = tmp_path / "observe.sock"
    configured_path = tmp_path / "configured.sock"
    listener = _listening_socket(path)
    config_path = _write_config(tmp_path / "toposd.toml", socket_path=configured_path)
    process = _spawn_toposd(["run", "--config", config_path], listeners=[listener])
    try:
        response, startup = _wait_for_hello(process, path)
        assert response["id"] == "service-test"
        assert response["ok"] is True
        stdout, _stderr = _stop_process(process)
        assert "serving read-only topos frames on" in startup
        assert str(path) in startup
        assert str(configured_path) not in startup
        assert "(activated)" in startup
        assert path.exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
        listener.close()


def test_self_bind_run_serves_and_unlinks_its_socket_on_sigterm(tmp_path: Path) -> None:
    configured_path = tmp_path / "configured.sock"
    socket_path = tmp_path / "self-bind.sock"
    config_path = _write_config(tmp_path / "toposd.toml", socket_path=configured_path)
    process = _spawn_toposd(["run", "--self-bind", "--socket", socket_path, "--config", config_path])
    try:
        response, startup = _wait_for_hello(process, socket_path)
        assert response["id"] == "service-test"
        assert response["ok"] is True
        stdout, _stderr = _stop_process(process)
        assert "serving read-only topos frames on" in startup
        assert str(socket_path) in startup
        assert str(configured_path) not in startup
        assert "(self-bind)" in startup
        assert not socket_path.exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_topos_daemon_serve_is_an_argparse_error(capsys: pytest.CaptureFixture[str]) -> None:
    from topos.cli import _main_daemon

    with pytest.raises(SystemExit) as exc:
        _main_daemon(["serve"])
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


def test_source_contains_no_retired_runtime_paths() -> None:
    retired_paths = ("/run/topos/topos.sock", "/run/topos/bpf", "/run/topos/squeeze")
    sources = [
        path for path in TOPOS_SRC.rglob("*")
        if path.is_file() and path.suffix in {".py", ".toml", ".service", ".socket", ".sysusers"}
    ]
    for path in sources:
        contents = path.read_text(encoding="utf-8")
        assert not any(retired in contents for retired in retired_paths), path

