"""Fake-root behavior for toposd install and uninstall."""

from __future__ import annotations

import argparse
import hashlib
import os
import socket
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from topos.service import install
from topos.service.install import InstallError, InstallPaths, Installer


def _runtime(*, dry_run=False, yes=True, interactive=False, json_mode=False, prompts=None):
    return SimpleNamespace(
        dry_run=dry_run,
        yes=yes,
        json_mode=json_mode,
        output=SimpleNamespace(is_interactive=interactive),
        prompts=prompts,
        confirm=lambda _message: yes,
    )


def _args(**overrides):
    values = {
        "wheel": None,
        "cli_extended_wheel": None,
        "daemon_lock": None,
        "wheel_source": None,
        "observe_group": None,
        "observe_users": None,
        "enable": False,
        "no_enable": False,
        "allow_downgrade": False,
        "remove_groups": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _pin(version: str, data: bytes) -> str:
    return f"{version}@sha256:{hashlib.sha256(data).hexdigest()}"


def _install_args(*, version="0.7.3", enable=True, **overrides):
    wheel = b"topos wheel bytes"
    clix = b"cli-extended wheel bytes"
    lock = ("textual==8.2.8 --hash=sha256:" + "a" * 64 + "\n").encode()
    values = {
        "wheel": _pin(version, wheel),
        "cli_extended_wheel": _pin("0.5.2", clix),
        "daemon_lock": _pin(version, lock),
        "wheel_source": "https://release.invalid/download",
        "observe_group": None,
        "observe_users": "",
        "enable": enable,
        "no_enable": not enable,
        "allow_downgrade": False,
    }
    values.update(overrides)
    return _args(**values), {"topos-" + version + "-py3-none-any.whl": wheel,
                             "cli_extended-0.5.2-py3-none-any.whl": clix,
                             "topos-" + version + "-daemon.lock": lock}


def _fake_run(commands, groups=None, active=None):
    groups = groups if groups is not None else {}
    active = active if active is not None else {}

    def run(argv, **_kwargs):
        argv = list(argv)
        commands.append(argv)
        if len(argv) >= 3 and argv[1:3] == ["-m", "venv"]:
            bindir = Path(argv[-1]) / "bin"
            bindir.mkdir(parents=True, exist_ok=True)
            (bindir / "python").write_text("", encoding="utf-8")
            (bindir / "toposd").write_text("", encoding="utf-8")
        if argv and argv[0] == "systemd-sysusers":
            groups["topos-observe"] = 9101
        if argv and argv[0] == "systemctl" and len(argv) >= 4 and argv[1] == "is-active":
            return subprocess.CompletedProcess(argv, 0 if active.get(argv[-1], False) else 3, "", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    return run


def _fake_fetch(wheels):
    def fetch(_url, destination):
        Path(destination).write_bytes(wheels[Path(destination).name])
    return fetch


def _groups(monkeypatch, initial=None):
    groups = dict(initial or {})

    def getgrnam(name):
        if name not in groups:
            raise KeyError(name)
        return SimpleNamespace(gr_name=name, gr_gid=groups[name])

    monkeypatch.setattr(install.grp, "getgrnam", getgrnam)
    return groups


def test_install_paths_prefix_every_host_path_and_reject_relative_paths(tmp_path: Path) -> None:
    paths = InstallPaths(tmp_path)
    assert paths.config == str(tmp_path / "etc/topos/toposd.toml")
    assert paths.install_record == str(tmp_path / "etc/topos/install.toml")
    assert paths.current == str(tmp_path / "opt/topos/current")
    assert paths.socket_node == str(tmp_path / "run/topos/observe/topos.sock")
    with pytest.raises(ValueError, match="expected an absolute service path"):
        paths.at("relative")


def test_version_ordering_places_final_after_prerelease() -> None:
    assert install.version_key("0.7.0") > install.version_key("0.7.0rc1")
    with pytest.raises(InstallError, match="unsupported topos version"):
        install.version_key("latest")


def test_old_installation_scan_collects_p11_problems_without_changing_files(tmp_path: Path) -> None:
    paths = InstallPaths(tmp_path)
    tmpfile = Path(paths.tmpfiles_dir) / "mdt-topos.conf"
    tmpfile.parent.mkdir(parents=True)
    tmpfile.write_text("d /run/topos 0750 root docker -\n", encoding="utf-8")
    unrelated = tmpfile.parent / "custom.conf"
    unrelated.write_text("d /run/topos/observe 0755 root root -\n", encoding="utf-8")
    dropin = Path(paths.service_dropins) / "50-mdt-socket-group.conf"
    dropin.parent.mkdir(parents=True)
    dropin.write_text("[Service]\nGroup=docker\n", encoding="utf-8")
    for absolute in ("/run/topos", "/run/topos/observe", "/run/topos/state"):
        Path(paths.at(absolute)).mkdir(parents=True)
    Path(paths.at("/run/topos/topos.sock")).write_text("old socket placeholder", encoding="utf-8")
    Path(paths.at("/run/topos/bpf")).mkdir()
    Path(paths.at("/run/topos/squeeze")).mkdir()
    Path(paths.pending).parent.mkdir(parents=True)
    Path(paths.pending).write_text("0.7.3\n", encoding="utf-8")
    Path(paths.current_new).write_text("leftover", encoding="utf-8")
    old_manifest = Path(paths.opt) / "0.6.0" / "MANIFEST"
    old_manifest.parent.mkdir(parents=True)
    old_manifest.write_text("installer=mdt host-setup\n", encoding="utf-8")
    old_unit = Path(paths.unit_dir) / "topos.service"
    old_unit.parent.mkdir(parents=True)
    old_unit.write_text("old unit\n", encoding="utf-8")
    before = {path: path.read_bytes() for path in (tmpfile, unrelated, dropin, old_manifest, old_unit)}

    problems = install.old_installation_problems(paths)
    message = install.manual_removal_message(problems)

    assert any(str(tmpfile) in item for item in problems)
    assert any(str(unrelated) in item for item in problems)
    assert any(str(dropin) in item for item in problems)
    assert any("root:root 0755" in item for item in problems)
    assert any("old-layout path" in item for item in problems)
    assert any("leftover install marker" in item for item in problems)
    assert any("unknown version tree MANIFEST" in item for item in problems)
    assert any("leftover install marker" in item for item in problems)
    assert any("hand-installed old unit" in item for item in problems)
    assert "systemctl stop topos.service" in message
    assert "rm -rf /run/topos" in message
    assert "recreate containers that bind-mount /run/topos" in message
    assert {path: path.read_bytes() for path in before} == before


def test_old_installation_scan_flags_read_errors_and_directory_owner_modes(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    conf = Path(paths.tmpfiles_dir) / "custom.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text("unrelated\n", encoding="utf-8")
    original_lstat = install.os.lstat

    def fake_lstat(path):
        if str(path) == paths.runtime_root:
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o750, st_uid=0, st_gid=999)
        return original_lstat(path)

    monkeypatch.setattr(install.os, "lstat", fake_lstat)
    assert any("root:root 0755" in item for item in install.old_installation_problems(paths))

    original_read = Path.read_text

    def unreadable(path, *args, **kwargs):
        if path == conf:
            raise PermissionError("denied")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)
    assert any("cannot inspect" in item for item in install.find_foreign_rules(paths))


def test_old_installation_scan_handles_unmarked_temporary_and_unreadable_version_trees(
    tmp_path: Path, monkeypatch
) -> None:
    paths = InstallPaths(tmp_path)
    opt = Path(paths.opt)
    (opt / ".stage.interrupted").mkdir(parents=True)
    (opt / "0.7.4").mkdir()
    (opt / "0.7.5").mkdir()
    (opt / "0.7.5" / "MANIFEST").write_text("installer=toposd install\n", encoding="utf-8")
    (opt / "current").symlink_to("0.7.5")
    (opt / "not-a-directory").write_text("ignored\n", encoding="utf-8")
    (opt / "0.7.6").symlink_to("0.7.5")

    problems = install.old_installation_problems(paths)
    assert any("leftover version staging directory" in item for item in problems)
    assert any("unmarked version tree" in item for item in problems)
    assert any("version tree is a symlink" in item for item in problems)
    assert not any(str(paths.current) in item for item in problems)

    original_listdir = install.os.listdir

    def denied(path):
        if str(path) == paths.opt:
            raise PermissionError("denied")
        return original_listdir(path)

    monkeypatch.setattr(install.os, "listdir", denied)
    assert any("cannot inspect version root" in item for item in install.old_installation_problems(paths))


def test_missing_non_default_inputs_name_the_required_flag(tmp_path: Path) -> None:
    with pytest.raises(InstallError, match="missing required --wheel"):
        install._gather_inputs(_args(), _runtime(dry_run=True), InstallPaths(tmp_path))


def test_invalid_wheel_source_is_refused_during_dry_run(tmp_path: Path, monkeypatch) -> None:
    args, _wheels = _install_args(wheel_source="file:///not-a-directory")
    _groups(monkeypatch)
    with pytest.raises(InstallError, match="--wheel-source must be an HTTPS release base"):
        install._gather_inputs(args, _runtime(dry_run=True), InstallPaths(tmp_path))


def test_nonexistent_mapped_group_is_refused_and_docker_is_allowed(tmp_path: Path, monkeypatch) -> None:
    args, _wheels = _install_args(observe_group="missing-group")
    _groups(monkeypatch)
    with pytest.raises(InstallError, match="mapped observe group 'missing-group' does not exist"):
        install._gather_inputs(args, _runtime(dry_run=True), InstallPaths(tmp_path))

    _groups(monkeypatch, {"docker": 991})
    args, _wheels = _install_args(observe_group="docker")
    values = install._gather_inputs(args, _runtime(dry_run=True), InstallPaths(tmp_path))
    assert values["observe_group"] == "docker"


def test_saved_record_resolves_group_users_and_pins(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    record = Path(paths.install_record)
    record.parent.mkdir(parents=True)
    args, _wheels = _install_args()
    fields = {
        "wheel": args.wheel,
        "cli_extended_wheel": args.cli_extended_wheel,
        "daemon_lock": args.daemon_lock,
        "wheel_source": args.wheel_source,
        "observe_group": "docker",
        "observe_users": ["alice"],
        "enable_now": False,
        "created_groups": [],
    }
    record.write_text(install._render_record(fields), encoding="utf-8")
    _groups(monkeypatch, {"docker": 991})
    monkeypatch.setattr(install.pwd, "getpwnam", lambda name: SimpleNamespace(pw_name=name))
    values = install._gather_inputs(_args(), _runtime(dry_run=True), paths)
    assert values["version"] == "0.7.3"
    assert values["observe_group"] == "docker"
    assert values["observe_users"] == ["alice"]
    assert values["enable_now"] is False


def test_tty_prompts_resolve_group_users_and_enable(tmp_path: Path, monkeypatch) -> None:
    class Prompts:
        def __init__(self, pins):
            self.calls = []
            self.pins = list(pins)

        def select(self, message, choices, **_kwargs):
            self.calls.append(("select", message))
            return choices[1]

        def text(self, message, **_kwargs):
            self.calls.append(("text", message))
            if "Existing group" in message:
                return "docker"
            if "release pin" in message:
                return self.pins.pop(0)
            return "alice"

        def confirm(self, message, **_kwargs):
            self.calls.append(("confirm", message))
            return True

    args, _wheels = _install_args()
    prompts = Prompts((args.wheel, args.cli_extended_wheel, args.daemon_lock))
    args.wheel = args.cli_extended_wheel = args.daemon_lock = None
    _groups(monkeypatch, {"docker": 991})
    monkeypatch.setattr(install.pwd, "getpwnam", lambda name: SimpleNamespace(pw_name=name))
    values = install._gather_inputs(
        args,
        _runtime(interactive=True, prompts=prompts),
        InstallPaths(tmp_path),
    )
    assert values["observe_group"] == "docker"
    assert values["observe_users"] == ["alice"]
    assert values["enable_now"] is True
    assert [name for name, _message in prompts.calls] == ["text", "text", "text", "select", "text", "text", "confirm"]


def test_dry_run_works_nonroot_and_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    args, _wheels = _install_args()
    _groups(monkeypatch)
    subject = Installer(paths, geteuid=lambda: 1000, run=lambda *_args, **_kwargs: pytest.fail("dry run ran a command"))
    body = subject.install(args, _runtime(dry_run=True))
    assert body["dry_run"] is True
    assert body["version"] == "0.7.3"
    assert not Path(paths.opt).exists()
    assert not Path(paths.config).exists()


def test_install_verifies_pins_builds_venv_writes_files_and_records_created_group(
    tmp_path: Path, monkeypatch
) -> None:
    paths = InstallPaths(tmp_path)
    args, wheels = _install_args()
    groups = _groups(monkeypatch)
    commands = []
    subject = Installer(paths, run=_fake_run(commands, groups), fetch=_fake_fetch(wheels), geteuid=lambda: 0, python="python-test")
    monkeypatch.setattr(Installer, "_wait_ready", lambda *_args, **_kwargs: None)

    body = subject.install(args, _runtime(yes=True))

    assert body["ok"] is True and body["enabled"] is True
    assert body["resources_changed"] == ["topos-observe.socket", "topos.service", "topos.slice", "topos.sysusers"]
    assert os.readlink(paths.current) == "0.7.3"
    assert "group = \"topos-observe\"" in Path(paths.config).read_text(encoding="utf-8")
    manifest = Path(paths.opt, "0.7.3", "MANIFEST").read_text(encoding="utf-8")
    assert "installer=toposd install" in manifest
    assert f"wheel_pin={args.wheel}" in manifest
    assert f"cli_extended_wheel_pin={args.cli_extended_wheel}" in manifest
    assert f"daemon_lock_pin={args.daemon_lock}" in manifest
    assert f"daemon_lock_sha256={hashlib.sha256(wheels['topos-0.7.3-daemon.lock']).hexdigest()}" in manifest
    record = Path(paths.install_record).read_text(encoding="utf-8")
    assert 'created_groups = ["topos-observe"]' in record
    assert Path(paths.client_link).is_symlink() and Path(paths.daemon_link).is_symlink()
    assert not Path(paths.pending).exists() and not Path(paths.current_new).exists()
    assert any(command[:3] == ["systemctl", "start", "topos-observe.socket"] for command in commands)
    assert any(command[:3] == ["systemctl", "start", "topos.service"] for command in commands)


def test_install_hash_mismatch_refuses_before_pip_unpack(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    args, wheels = _install_args()
    _groups(monkeypatch)
    commands = []
    subject = Installer(paths, run=_fake_run(commands), fetch=_fake_fetch(wheels), geteuid=lambda: 0)
    args.wheel = "0.7.3@sha256:" + "f" * 64
    with pytest.raises(InstallError, match="refusing before unpack"):
        subject.install(args, _runtime(yes=True))
    assert not any("pip" in " ".join(command) for command in commands)
    assert Path(paths.pending).is_file()


def test_install_refuses_old_layout_before_any_mutation(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    _groups(monkeypatch)
    Path(paths.at("/etc/tmpfiles.d/mdt-topos.conf")).parent.mkdir(parents=True)
    Path(paths.at("/etc/tmpfiles.d/mdt-topos.conf")).write_text("old\n", encoding="utf-8")
    args, _wheels = _install_args()
    with pytest.raises(InstallError, match="Manual removal"):
        Installer(paths, geteuid=lambda: 0).install(args, _runtime(yes=True))
    assert not Path(paths.opt).exists()


def test_preflight_refuses_downgrade_unless_allowed(tmp_path: Path) -> None:
    paths = InstallPaths(tmp_path)
    old = Path(paths.opt, "0.8.0")
    old.mkdir(parents=True)
    (old / "MANIFEST").write_text("installer=toposd install\n", encoding="utf-8")
    Path(paths.current).symlink_to("0.8.0")
    socket_unit = Path(paths.unit_dir) / "topos-observe.socket"
    socket_unit.parent.mkdir(parents=True)
    socket_unit.write_text("socket\n", encoding="utf-8")
    Path(paths.install_record).parent.mkdir(parents=True)
    Path(paths.install_record).write_text("created_groups = []\n", encoding="utf-8")
    values = {"version": "0.6.9", "wheel_sha": "a", "cli_extended_sha": "b", "lock_sha": "c", "allow_downgrade": False}
    subject = Installer(paths, geteuid=lambda: 0)
    with pytest.raises(InstallError, match="--allow-downgrade"):
        subject._preflight(values, dry_run=True)
    values["allow_downgrade"] = True
    assert subject._preflight(values, dry_run=True) == (True, True)


def test_downgrade_removes_socket_unit_and_node_only_after_confirmation(tmp_path: Path) -> None:
    paths = InstallPaths(tmp_path)
    socket_unit = Path(paths.unit_dir) / "topos-observe.socket"
    socket_unit.parent.mkdir(parents=True)
    socket_unit.write_text("socket\n", encoding="utf-8")
    Path(paths.socket_node).parent.mkdir(parents=True)
    node = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    node.bind(paths.socket_node)
    node.close()
    commands = []
    subject = Installer(paths, run=_fake_run(commands), geteuid=lambda: 0)
    subject._apply_downgrade(
        {"allow_downgrade": True, "version": "0.6.9"},
        subject._resource_paths(),
    )
    assert not socket_unit.exists()
    assert not Path(paths.socket_node).exists()
    assert ["systemctl", "disable", "--now", "topos-observe.socket"] in commands


def test_downgrade_refuses_to_remove_a_non_socket_node(tmp_path: Path) -> None:
    paths = InstallPaths(tmp_path)
    Path(paths.socket_node).parent.mkdir(parents=True)
    Path(paths.socket_node).write_text("do not delete", encoding="utf-8")
    subject = Installer(paths, geteuid=lambda: 0)
    with pytest.raises(InstallError, match="refusing to remove non-socket"):
        subject._apply_downgrade({"allow_downgrade": True, "version": "0.6.9"}, subject._resource_paths())


def test_ready_probe_accepts_expected_version_and_deadline_is_clock_bounded(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(install, "_hello", lambda _path, *, timeout: {"identity": {"version": "0.7.3"}})
    subject = Installer(InstallPaths(tmp_path), monotonic=lambda: 0.0, sleep=lambda _seconds: None)
    subject._wait_ready("/run/topos/observe/topos.sock", "0.7.3")

    now = [0.0]
    monkeypatch.setattr(install, "_hello", lambda _path, *, timeout: {"identity": {"version": "0.7.2"}})
    subject.monotonic = lambda: now[0]
    subject.sleep = lambda amount: now.__setitem__(0, now[0] + amount)
    with pytest.raises(InstallError, match="did not report topos version 0.7.3"):
        subject._wait_ready("/run/topos/observe/topos.sock", "0.7.3", timeout=1.0)


def test_prune_keeps_newest_two_and_requested_current(tmp_path: Path) -> None:
    paths = InstallPaths(tmp_path)
    opt = Path(paths.opt)
    opt.mkdir(parents=True)
    for version in ("0.5.0", "0.6.0", "0.7.0", "0.8.0"):
        (opt / version).mkdir()
    assert set(Installer(paths)._prune_versions("0.5.0")) == {"0.6.0"}


def test_command_errors_are_reported_and_check_false_returns_result(tmp_path: Path) -> None:
    subject = Installer(InstallPaths(tmp_path), run=lambda argv, **_kwargs: subprocess.CompletedProcess(argv, 5, "", "failed"))
    with pytest.raises(InstallError, match=r"command failed \(systemctl status\), exit 5: failed"):
        subject._command(["systemctl", "status"])
    assert subject._command(["systemctl", "status"], check=False).returncode == 5

    def fail_to_start(*_args, **_kwargs):
        raise OSError("missing command")

    with pytest.raises(InstallError, match="command failed to run"):
        Installer(InstallPaths(tmp_path), run=fail_to_start)._command(["systemctl", "status"])


def test_uninstall_removes_owned_files_and_preserves_foreign_links_and_groups_by_default(tmp_path: Path) -> None:
    paths = InstallPaths(tmp_path)
    unit_dir = Path(paths.unit_dir)
    unit_dir.mkdir(parents=True)
    for name in ("topos.service", "topos-observe.socket", "topos.slice"):
        (unit_dir / name).write_text("owned\n", encoding="utf-8")
    Path(paths.sysusers).parent.mkdir(parents=True)
    Path(paths.sysusers).write_text("g topos-observe -\n", encoding="utf-8")
    Path(paths.config).parent.mkdir(parents=True)
    Path(paths.config).write_text("state_dir = '/run/topos/state'\n", encoding="utf-8")
    Path(paths.install_record).write_text('created_groups = ["topos-observe"]\n', encoding="utf-8")
    Path(paths.opt, "0.7.3", "venv", "bin").mkdir(parents=True)
    Path(paths.opt, "current").symlink_to("0.7.3")
    Path(paths.client_link).parent.mkdir(parents=True)
    Path(paths.client_link).symlink_to(paths.at("/opt/topos/current/venv/bin/topos"))
    Path(paths.daemon_link).symlink_to("/opt/foreign/toposd")
    Path(paths.service_dropins).mkdir(parents=True)
    Path(paths.service_dropins, "50-mdt-socket-group.conf").write_text("old\n", encoding="utf-8")
    Path(paths.state_dir).mkdir(parents=True)
    Path(paths.state_dir, "state").write_text("volatile\n", encoding="utf-8")
    Path(paths.observe_dir).mkdir(parents=True)
    node = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    node.bind(paths.socket_node)
    node.close()
    commands = []
    subject = Installer(paths, run=_fake_run(commands), geteuid=lambda: 0)

    body = subject.uninstall(_args(), _runtime(yes=True))

    assert body["groups_removed"] == []
    assert not Path(paths.config).exists()
    assert not Path(paths.opt).exists()
    assert not Path(paths.sysusers).exists()
    assert not Path(paths.socket_node).exists()
    assert Path(paths.daemon_link).is_symlink()
    assert ["systemctl", "daemon-reload"] in commands
    assert any(command[:3] == ["systemctl", "disable", "--now"] for command in commands)
    assert not Path(paths.runtime_root).exists()


def test_uninstall_removes_only_recorded_groups_when_requested(tmp_path: Path) -> None:
    paths = InstallPaths(tmp_path)
    Path(paths.install_record).parent.mkdir(parents=True)
    Path(paths.install_record).write_text('created_groups = ["topos-observe"]\n', encoding="utf-8")
    commands = []
    subject = Installer(paths, run=_fake_run(commands), geteuid=lambda: 0)
    body = subject.uninstall(_args(remove_groups=True), _runtime(yes=True))
    assert body["groups_removed"] == ["topos-observe"]
    assert ["groupdel", "topos-observe"] in commands


def test_uninstall_dry_run_is_nonroot_and_never_runs_commands(tmp_path: Path) -> None:
    subject = Installer(InstallPaths(tmp_path), geteuid=lambda: 1000,
                       run=lambda *_args, **_kwargs: pytest.fail("dry run ran systemctl"))
    body = subject.uninstall(_args(), _runtime(dry_run=True))
    assert body["dry_run"] is True
    assert not tmp_path.exists()


def test_uninstall_refuses_regular_file_at_socket_node(tmp_path: Path) -> None:
    paths = InstallPaths(tmp_path)
    Path(paths.socket_node).parent.mkdir(parents=True)
    Path(paths.socket_node).write_text("not a socket", encoding="utf-8")
    with pytest.raises(InstallError, match="refusing to remove non-socket"):
        Installer(paths, geteuid=lambda: 0).uninstall(_args(), _runtime(yes=True))
