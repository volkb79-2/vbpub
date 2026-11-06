"""Pure and fake-root coverage for cgpd's standalone installer."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from cgprofile import daemon_config, unitfiles
from cgprofile.cli import installer


CGPROFILE_BYTES = b"cgprofile wheel fixture"
CLI_EXTENDED_BYTES = b"cli-extended wheel fixture"
CGPROFILE_SHA = hashlib.sha256(CGPROFILE_BYTES).hexdigest()
CLI_EXTENDED_SHA = hashlib.sha256(CLI_EXTENDED_BYTES).hexdigest()


class Prompts:
    def __init__(self, *, text_values=(), select_value=None, confirm_value=True):
        self.text_values = list(text_values)
        self.select_value = select_value
        self.confirm_value = confirm_value
        self.calls = []

    def text(self, message, **kwargs):
        self.calls.append(("text", message, kwargs))
        return self.text_values.pop(0) if self.text_values else ""

    def select(self, message, choices, **kwargs):
        self.calls.append(("select", message, choices, kwargs))
        return self.select_value or choices[0]

    def confirm(self, message, **kwargs):
        self.calls.append(("confirm", message, kwargs))
        return self.confirm_value


def _runtime(*, dry_run=False, json_mode=False, interactive=False, yes=False, prompts=None):
    return SimpleNamespace(
        dry_run=dry_run,
        json_mode=json_mode,
        yes=yes,
        output=SimpleNamespace(is_interactive=interactive),
        prompts=prompts or Prompts(),
    )


def _args(*, version="2.2.0", enable=True, **overrides):
    values = {
        "config": None,
        "wheel": f"{version}@sha256:{CGPROFILE_SHA}",
        "cli_extended_wheel": f"0.5.1@sha256:{CLI_EXTENDED_SHA}",
        "wheel_source": None,
        "control_group": "cgprofile-control",
        "observer_group": "cgprofile-observer",
        "control_users": "",
        "observer_users": "",
        "gates_slice": None,
        "observe_slices": None,
        "allow_uids": None,
        "drain_timeout": None,
        "force_restart": False,
        "allow_downgrade": False,
        "enable": enable,
        "no_enable": not enable,
        "remove_groups": False,
        "purge_data": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _paths(tmp_path):
    return installer.InstallPaths(str(tmp_path))


def _installer(tmp_path, **kwargs):
    return installer.Installer(_paths(tmp_path), geteuid=lambda: 0, **kwargs)


def _fake_fetch(url, dest):
    Path(dest).write_bytes(CLI_EXTENDED_BYTES if "cli_extended-" in url else CGPROFILE_BYTES)


def _fake_run(commands, group_gids=None, active=None):
    group_gids = group_gids if group_gids is not None else {}
    active = active if active is not None else {}

    def run(argv, **_kwargs):
        argv = list(argv)
        commands.append(argv)
        if len(argv) >= 3 and argv[1:3] == ["-m", "venv"]:
            bindir = Path(argv[-1]) / "bin"
            bindir.mkdir(parents=True, exist_ok=True)
            (bindir / "cgpd").write_text("", encoding="utf-8")
            (bindir / "cgpctl").write_text("", encoding="utf-8")
        if argv and argv[0] == "systemd-sysusers":
            group_gids.update({"cgprofile-control": 7101, "cgprofile-observer": 7102})
        if argv and argv[0] == "systemctl" and len(argv) >= 4 and argv[1] == "is-active":
            return subprocess.CompletedProcess(argv, 0 if active.get(argv[-1], False) else 3, "", "")
        if argv and argv[0].endswith("/cgpd") and "--version" in argv:
            return subprocess.CompletedProcess(argv, 0, "cgpd 2.2.0\n", "")
        if argv and "unit" in argv and "print" in argv:
            unit = argv[argv.index("--unit") + 1]
            config = argv[argv.index("--config") + 1]
            values, _digest = daemon_config.read_config_file(config, explicit=True)
            rendered = unitfiles.render(config_values=values)
            return subprocess.CompletedProcess(argv, 0, rendered[unit], "")
        if argv and "status" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps({
                "daemon": {"version": "2.2.0"},
                "config": {"last_reload_error": None},
            }), "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    return run


def test_pin_duration_and_version_helpers_are_strict():
    assert installer.parse_pin(f"2.2.0@sha256:{CGPROFILE_SHA}", "cgprofile") == ("2.2.0", CGPROFILE_SHA)
    assert installer.version_key("2.2.0") > installer.version_key("2.2.0rc1")
    assert installer.has_socket_units("2.2.0")
    assert not installer.has_socket_units("2.1.9")
    assert installer.parse_duration("10m") == 600
    assert installer.parse_duration("3h") == 10800
    with pytest.raises(installer.InstallError, match="64 lowercase hex"):
        installer.parse_pin("2.2.0@sha256:ABC", "cgprofile")
    with pytest.raises(installer.InstallError, match="unsupported cgprofile version"):
        installer.version_key("latest")
    with pytest.raises(installer.InstallError, match="positive duration"):
        installer.parse_duration("0m")


def test_old_installation_scan_reports_each_p11_condition(tmp_path):
    paths = _paths(tmp_path)
    Path(paths.config).parent.mkdir(parents=True)
    Path(paths.config).write_text('ctl_socket = "/run/cgprofile/ctl.sock"\n', encoding="utf-8")
    tmpfiles = paths.at("/etc/tmpfiles.d/mdt-cgprofile.conf")
    Path(tmpfiles).parent.mkdir(parents=True)
    Path(tmpfiles).write_text("d /run/cgprofile 0770 root docker -\n", encoding="utf-8")
    dropin = paths.at("/etc/systemd/system/cgpd.service.d/50-mdt.conf")
    Path(dropin).parent.mkdir(parents=True)
    Path(dropin).write_text("[Service]\nGroup=docker\n", encoding="utf-8")
    old_sysusers = paths.at("/etc/sysusers.d/mdt-cgprofile.conf")
    Path(old_sysusers).parent.mkdir(parents=True)
    Path(old_sysusers).write_text("g cgprofile-observer - - -\n", encoding="utf-8")
    runtime = Path(paths.runtime_root)
    runtime.mkdir(parents=True)
    runtime.chmod(0o770)
    opt = Path(paths.opt)
    old_version = opt / "2.1.0"
    old_version.mkdir(parents=True)
    (old_version / "MANIFEST").write_text("installer=mdt host-setup (MDT-055)\n", encoding="utf-8")
    (opt / "PENDING").write_text("2.2.0\n", encoding="utf-8")
    (opt / "current.new").write_text("leftover\n", encoding="utf-8")

    problems = installer.old_installation_problems(paths)
    message = installer.manual_removal_message(problems)

    assert any("old config keys: ctl_socket" in problem for problem in problems)
    assert any("foreign rule or drop-in" in problem and "mdt-cgprofile.conf" in problem for problem in problems)
    assert any("foreign rule or drop-in" in problem and "50-mdt.conf" in problem for problem in problems)
    assert any("mdt-cgprofile.conf" in problem for problem in problems)
    assert any("root:root 0755" in problem for problem in problems)
    assert any("mdt host-setup MANIFEST" in problem for problem in problems)
    assert any("leftover install marker" in problem for problem in problems)
    assert "Manual steps:" in message and "keep /var/lib/cgprofile" in message


def test_invalid_existing_config_is_not_silently_overwritten(tmp_path):
    paths = _paths(tmp_path)
    Path(paths.config).parent.mkdir(parents=True)
    Path(paths.config).write_text("not valid toml = [\n", encoding="utf-8")

    with pytest.raises(installer.InstallError, match="cannot inspect existing config"):
        installer.old_installation_problems(paths)


def test_noninteractive_missing_pin_names_its_flag(tmp_path):
    args = _args(wheel=None)
    with pytest.raises(installer.InstallError, match="missing required --wheel"):
        installer._gather_inputs(args, _runtime(), _paths(tmp_path))


def test_tty_wizard_resolves_missing_pins_groups_and_enable(tmp_path, monkeypatch):
    monkeypatch.setattr(installer.pwd, "getpwnam", lambda _name: SimpleNamespace(pw_name="alice"))
    monkeypatch.setattr(installer, "_group_gid", lambda _name: None)
    prompts = Prompts(text_values=[f"2.2.0@sha256:{CGPROFILE_SHA}",
                                  f"0.5.1@sha256:{CLI_EXTENDED_SHA}", "alice", ""],
                      confirm_value=True)
    args = _args(wheel=None, cli_extended_wheel=None, control_group=None, observer_group=None,
                 control_users=None, observer_users=None, enable=False, no_enable=False)

    values = installer._gather_inputs(args, _runtime(interactive=True, prompts=prompts), _paths(tmp_path))

    assert values["version"] == "2.2.0" and values["cli_extended_version"] == "0.5.1"
    assert values["control_group"] == "cgprofile-control"
    assert values["observer_group"] == "cgprofile-observer"
    assert values["control_users"] == ["alice"] and values["observer_users"] == []
    assert values["enable_now"] is True
    assert [call[0] for call in prompts.calls] == ["text", "text", "select", "select", "text", "text", "confirm"]


def test_noninteractive_dry_run_requires_an_enable_decision(tmp_path):
    args = _args(enable=False, no_enable=False)
    with pytest.raises(installer.InstallError, match="--enable or --no-enable"):
        installer._gather_inputs(args, _runtime(dry_run=True), _paths(tmp_path))


def test_mapped_group_must_exist_and_observer_must_not_be_docker(monkeypatch):
    gids = {"docker": 100, "same": 200, "control": 200, "observer": 200}
    monkeypatch.setattr(installer, "_group_gid", lambda name: gids.get(name))
    with pytest.raises(installer.InstallError, match="mapped control group.*does not exist"):
        installer._validate_group_mapping("missing", "cgprofile-observer")
    with pytest.raises(installer.InstallError, match="must not be docker"):
        installer._validate_group_mapping("cgprofile-control", "docker")
    with pytest.raises(installer.InstallError, match="must differ"):
        installer._validate_group_mapping("same", "same")
    with pytest.raises(installer.InstallError, match="same GID"):
        installer._validate_group_mapping("control", "observer")


def test_wheel_fetch_copies_local_sources_and_rejects_missing_or_insecure_sources(tmp_path):
    source = tmp_path / "wheels"
    source.mkdir()
    filename = "cgprofile-2.2.0-py3-none-any.whl"
    (source / filename).write_bytes(CGPROFILE_BYTES)
    target = tmp_path / "out.whl"
    installer._fetch_wheel(str(source), "cgprofile", filename, str(target))
    assert target.read_bytes() == CGPROFILE_BYTES
    with pytest.raises(installer.InstallError, match="must contain"):
        installer._fetch_wheel(str(source), "cgprofile", "absent.whl", str(target))
    with pytest.raises(installer.InstallError, match="HTTPS"):
        installer._fetch_wheel("http://example.invalid", "cgprofile", filename, str(target))
    with pytest.raises(installer.InstallError, match="unexpected wheel filename"):
        installer.filename_version("bad.whl")


def test_preflight_refuses_downgrade_until_the_operator_allows_it(tmp_path):
    paths = _paths(tmp_path)
    opt = Path(paths.opt)
    opt.mkdir(parents=True)
    (opt / "current").symlink_to("2.2.0")
    socket_path = Path(paths.unit_dir) / "cgprofile-control.socket"
    socket_path.parent.mkdir(parents=True)
    socket_path.write_text("socket unit\n", encoding="utf-8")
    values = {"version": "2.1.0", "wheel_sha": CGPROFILE_SHA, "cli_extended_sha": CLI_EXTENDED_SHA,
              "allow_downgrade": False}
    subject = _installer(tmp_path)

    with pytest.raises(installer.InstallError, match="--allow-downgrade"):
        subject._preflight(values, paths.config, dry_run=True)

    values["allow_downgrade"] = True
    assert subject._preflight(values, paths.config, dry_run=True) == (True, True)


def test_downgrade_disable_failure_preserves_socket_unit_files(tmp_path):
    paths = _paths(tmp_path)
    resources = {
        "cgprofile-control.socket": str(Path(paths.unit_dir) / "cgprofile-control.socket"),
        "cgprofile-observer.socket": str(Path(paths.unit_dir) / "cgprofile-observer.socket"),
        "cgprofile.sysusers": str(Path(paths.sysusers_dir) / "cgprofile.conf"),
    }
    for path in resources.values():
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text("owned\n", encoding="utf-8")
    commands = []

    def failed(argv, **_kwargs):
        commands.append(list(argv))
        return subprocess.CompletedProcess(argv, 1, "", "unit still active")

    values = {"version": "2.1.0", "allow_downgrade": True}
    with pytest.raises(installer.InstallError, match="command failed.*disable"):
        installer.Installer(paths, run=failed, geteuid=lambda: 0)._apply_downgrade(values, resources)

    assert all(Path(path).is_file() for path in resources.values())
    assert commands[0][1:3] == ["disable", "--now"]


def test_drain_deadline_writes_pending_and_force_records_live_sessions(tmp_path):
    paths = _paths(tmp_path)
    clock = iter((0.0, 10.0))
    subject = _installer(tmp_path, monotonic=lambda: next(clock), sleep=lambda _seconds: None)
    subject._live_sessions = lambda _current: [{"session": "s-live"}]
    values = {"version": "2.2.0", "drain_seconds": 10, "drain_timeout": "10s", "force_restart": False}
    with pytest.raises(installer.InstallError, match="live sessions remain") as caught:
        subject._drain_or_refuse("2.1.0", values)
    assert caught.value.exit_code == 3
    assert Path(paths.opt, "PENDING").read_text() == "2.2.0\n"

    forced = {**values, "force_restart": True}
    subject._drain_or_refuse("2.1.0", forced)
    assert forced["forced_sessions"] == ["s-live"]


def test_wait_ready_accepts_expected_version_and_refuses_deadline(tmp_path):
    subject = _installer(tmp_path)
    subject._command = lambda _argv, **_kwargs: subprocess.CompletedProcess(
        [], 0, json.dumps({"daemon": {"version": "2.2.0"}, "config": {"last_reload_error": None}}), "")
    subject._wait_ready("/venv", "2.2.0", timeout=1)

    now = [0.0]
    subject._command = lambda _argv, **_kwargs: subprocess.CompletedProcess(
        [], 0, json.dumps({"daemon": {"version": "2.1.0"}, "config": {"last_reload_error": None}}), "")
    subject.monotonic = lambda: now[0]
    subject.sleep = lambda amount: now.__setitem__(0, now[0] + amount)
    with pytest.raises(installer.InstallError, match="did not report version"):
        subject._wait_ready("/venv", "2.2.0", timeout=1)


def test_prune_keeps_the_newest_two_and_the_current_version(tmp_path):
    paths = _paths(tmp_path)
    opt = Path(paths.opt)
    opt.mkdir(parents=True)
    for version in ("2.0.0", "2.1.0", "2.2.0", "2.3.0"):
        (opt / version).mkdir()
    removed = _installer(tmp_path)._prune_versions("2.0.0")
    assert set(removed) == {"2.1.0"}
    assert {path.name for path in opt.iterdir() if path.is_dir()} == {"2.0.0", "2.2.0", "2.3.0"}
    assert _installer(tmp_path)._prune_versions("2.3.0") == []


def test_install_dry_run_only_reports_the_candidate(tmp_path, monkeypatch):
    subject = _installer(tmp_path)
    gids = {"cgprofile-control": 7100, "cgprofile-observer": 7101}
    monkeypatch.setattr(installer, "_group_gid", lambda name: gids.get(name))

    body = subject.install(_args(), _runtime(dry_run=True))

    assert body["dry_run"] is True and body["version"] == "2.2.0"
    assert body["socket_mode"] == "activated"
    assert not Path(_paths(tmp_path).opt).exists()


def test_install_applies_config_units_groups_links_and_start(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    Path(paths.install_record).parent.mkdir(parents=True, exist_ok=True)
    Path(paths.install_record).write_text('created_groups = ["prior-created-group"]\n', encoding="utf-8")
    commands = []
    group_gids = {}
    monkeypatch.setattr(installer, "_group_gid", lambda name: group_gids.get(name))
    subject = installer.Installer(
        paths, run=_fake_run(commands, group_gids), fetch=_fake_fetch,
        geteuid=lambda: 0, python="python3",
    )
    monkeypatch.setattr(installer.Installer, "_wait_ready", lambda self, _venv, _version: None)

    body = subject.install(_args(), _runtime(yes=True))

    assert body["ok"] is True and body["enabled"] is True
    assert body["resources_changed"] == sorted(unitfiles.TEMPLATES)
    assert os.readlink(Path(paths.opt) / "current") == "2.2.0"
    config = Path(paths.config).read_text(encoding="utf-8")
    assert "[sockets.control]" in config and "[sockets.observer]" in config
    assert Path(paths.client_link).is_symlink()
    record = Path(paths.install_record).read_text(encoding="utf-8")
    assert "created_groups = [\"prior-created-group\", \"cgprofile-control\", \"cgprofile-observer\"]" in record
    assert ["systemctl", "start", "cgprofile-control.socket", "cgprofile-observer.socket"] in commands
    assert ["systemctl", "start", "cgpd.service"] in commands
    assert group_gids == {"cgprofile-control": 7101, "cgprofile-observer": 7102}


def test_install_restarts_inactive_socket_units_after_drain(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    opt = Path(paths.opt)
    opt.mkdir(parents=True)
    (opt / "2.1.0").mkdir()
    (opt / "current").symlink_to("2.1.0")
    commands = []
    group_gids = {"cgprofile-control": 7101, "cgprofile-observer": 7102}
    monkeypatch.setattr(installer, "_group_gid", lambda name: group_gids.get(name))
    active = {"cgpd.service": True}
    subject = installer.Installer(paths, run=_fake_run(commands, group_gids, active),
                                  fetch=_fake_fetch, geteuid=lambda: 0, python="python3")
    drained = []
    monkeypatch.setattr(installer.Installer, "_drain_or_refuse",
                        lambda self, _current, _values: drained.append(True))
    monkeypatch.setattr(installer.Installer, "_wait_ready", lambda self, _venv, _version: None)

    body = subject.install(_args(), _runtime(yes=True))

    assert body["ok"] is True and drained == [True]
    assert ["systemctl", "restart", "cgprofile-control.socket"] in commands
    assert ["systemctl", "restart", "cgprofile-observer.socket"] in commands


def test_uninstall_stops_existing_units_and_keeps_groups_and_data_by_default(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    for path in (
        Path(paths.unit_dir) / "cgpd.service",
        Path(paths.unit_dir) / "cgprofile-control.socket",
        Path(paths.unit_dir) / "cgprofile-observer.socket",
        Path(paths.unit_dir) / "cgprofile.slice",
        Path(paths.sysusers_dir) / "cgprofile.conf",
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("owned\n", encoding="utf-8")
    Path(paths.config).parent.mkdir(parents=True)
    Path(paths.config).write_text("", encoding="utf-8")
    Path(paths.install_record).parent.mkdir(parents=True, exist_ok=True)
    Path(paths.install_record).write_text('created_groups = ["cgprofile-control"]\n', encoding="utf-8")
    Path(paths.opt).mkdir(parents=True)
    Path(paths.state_data).mkdir(parents=True)
    (Path(paths.state_data) / "keep").write_text("data\n", encoding="utf-8")
    for directory in ("control", "observer", "state"):
        Path(paths.runtime_root, directory).mkdir(parents=True, exist_ok=True)
    Path(paths.client_link).parent.mkdir(parents=True)
    Path(paths.client_link).symlink_to("/opt/cgprofile/current/venv/bin/cgpctl")
    commands = []
    subject = installer.Installer(paths, run=_fake_run(commands), geteuid=lambda: 0)

    body = subject.uninstall(_args())

    assert body["data_purged"] is False and body["groups_removed"] == []
    assert not Path(paths.config).exists() and not Path(paths.opt).exists()
    assert not Path(paths.unit_dir, "cgpd.service").exists()
    assert not Path(paths.sysusers_dir, "cgprofile.conf").exists()
    assert Path(paths.state_data, "keep").is_file()
    assert any(command[:3] == ["systemctl", "disable", "--now"] for command in commands)
    assert ["systemctl", "daemon-reload"] in commands


def test_uninstall_refuses_to_remove_units_when_systemd_disable_fails(tmp_path):
    paths = _paths(tmp_path)
    unit = Path(paths.unit_dir) / "cgpd.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("service\n", encoding="utf-8")

    def failed(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, 1, "", "stop failed")

    with pytest.raises(installer.InstallError, match="command failed.*disable"):
        installer.Installer(paths, run=failed, geteuid=lambda: 0).uninstall(_args())
    assert unit.is_file()
