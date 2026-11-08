"""Pure and fake-root coverage for cgprofiled's standalone installer."""

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
AUGEASWRAP_BYTES = b"augeaswrap wheel fixture"
CGPROFILE_SHA = hashlib.sha256(CGPROFILE_BYTES).hexdigest()
CLI_EXTENDED_SHA = hashlib.sha256(CLI_EXTENDED_BYTES).hexdigest()
AUGEASWRAP_SHA = hashlib.sha256(AUGEASWRAP_BYTES).hexdigest()


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


class CancelledPrompts(Prompts):
    def text(self, message, **kwargs):
        raise installer.PromptCancelled()

    def select(self, message, choices, **kwargs):
        raise installer.PromptCancelled()

    def confirm(self, message, **kwargs):
        raise installer.PromptCancelled()


def _runtime(*, dry_run=False, json_mode=False, interactive=False, yes=False, prompts=None, confirmed=True):
    asked = []

    def confirm(message):
        asked.append(message)
        return confirmed

    return SimpleNamespace(
        dry_run=dry_run,
        json_mode=json_mode,
        yes=yes,
        output=SimpleNamespace(is_interactive=interactive),
        prompts=prompts or Prompts(),
        confirm=confirm,
        asked=asked,
    )


def _args(*, version="2.2.0", enable=True, **overrides):
    values = {
        "config": None,
        "wheel": f"{version}@sha256:{CGPROFILE_SHA}",
        "cli_extended_wheel": f"0.5.3@sha256:{CLI_EXTENDED_SHA}",
        "augeaswrap_wheel": None,
        "wheel_source": None,
        "control_group": "cgprofile-control",
        "observer_group": "cgprofile-observer",
        "add_user": None,
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
    data = (AUGEASWRAP_BYTES if "augeaswrap-" in url else
            CLI_EXTENDED_BYTES if "cli_extended-" in url else CGPROFILE_BYTES)
    Path(dest).write_bytes(data)


def _fake_run(commands, group_gids=None, active=None, fail=None):
    group_gids = group_gids if group_gids is not None else {}
    active = active if active is not None else {}
    fail = fail if fail is not None else {}

    def run(argv, **_kwargs):
        argv = list(argv)
        commands.append(argv)
        for prefix, code in fail.items():
            if argv[: len(prefix)] == list(prefix):
                return subprocess.CompletedProcess(argv, code, "", f"{prefix[0]} failed")
        if len(argv) >= 3 and argv[1:3] == ["-m", "venv"]:
            bindir = Path(argv[-1]) / "bin"
            bindir.mkdir(parents=True, exist_ok=True)
            (bindir / "cgprofiled").write_text("", encoding="utf-8")
            (bindir / "cgprofilectl").write_text("", encoding="utf-8")
        if argv and argv[0] == "systemd-sysusers":
            group_gids.update({"cgprofile-control": 7101, "cgprofile-observer": 7102})
        if argv and argv[0] == "groupdel":
            group_gids.pop(argv[1], None)
        if argv and argv[0] == "systemctl" and len(argv) >= 4 and argv[1] == "is-active":
            return subprocess.CompletedProcess(argv, 0 if active.get(argv[-1], False) else 3, "", "")
        if argv and argv[0].endswith("/cgprofiled") and "--version" in argv:
            # .../opt/cgprofile/<version>/venv/bin/cgprofiled
            return subprocess.CompletedProcess(argv, 0, f"cgprofiled {Path(argv[0]).parents[2].name}\n", "")
        if argv and "unit" in argv and "print" in argv:
            unit = argv[argv.index("--unit") + 1]
            config = argv[argv.index("--config") + 1]
            values, _digest = daemon_config.read_config_file(config, explicit=True)
            rendered = unitfiles.render(config_values=values)
            return subprocess.CompletedProcess(argv, 0, rendered[unit], "")
        if argv and argv[0].endswith("/cgprofilectl") and "sessions" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps({"sessions": []}), "")
        if argv and "status" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps({
                "daemon": {"version": "2.2.0"},
                "config": {"last_reload_error": None},
            }), "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    return run


@pytest.fixture(autouse=True)
def _fake_runtime_owner(monkeypatch):
    # The fake root is created by the test user, not root.
    monkeypatch.setattr(installer, "RUNTIME_TREE_OWNER", (os.getuid(), os.getgid()))


@pytest.fixture(autouse=True)
def _fake_group_database(monkeypatch):
    # Installer config paths must never consult the cockpit's real group table.
    groups = {}

    def getgrnam(name):
        if name not in groups:
            raise KeyError(name)
        return SimpleNamespace(gr_name=name, gr_gid=groups[name], gr_mem=[])

    monkeypatch.setattr(installer.grp, "getgrnam", getgrnam)


class Host:
    """A fake root with injectable groups, users, systemctl state and command log."""

    def __init__(self, tmp_path, monkeypatch, *, active=None, fail=None, groups=None, running_prefix=None):
        self.paths = _paths(tmp_path)
        self.commands = []
        self.group_gids = dict(groups or {})
        self.active = active if active is not None else {}
        monkeypatch.setattr(installer, "_group_gid", lambda name: self.group_gids.get(name))

        def getgrnam(name):
            gid = self.group_gids.get(name)
            if gid is None:
                raise KeyError(name)
            return SimpleNamespace(gr_name=name, gr_gid=gid, gr_mem=[])

        monkeypatch.setattr(daemon_config.grp, "getgrnam", getgrnam)
        monkeypatch.setattr(installer.pwd, "getpwnam", lambda name: SimpleNamespace(pw_name=name))
        extra = {} if running_prefix is None else {"running_prefix": running_prefix}
        self.subject = installer.Installer(
            self.paths, run=_fake_run(self.commands, self.group_gids, self.active, fail),
            fetch=_fake_fetch, geteuid=lambda: 0, python="python3", **extra,
        )
        monkeypatch.setattr(installer.Installer, "_wait_ready", lambda self, _venv, _version: None)

    def install(self, args=None, runtime=None):
        return self.subject.install(args or _args(), runtime or _runtime(yes=True))

    def seeded_install(self):
        """Run a real install on the fake root so uninstall tests start from a valid tree."""
        self.install()
        self.commands.clear()

    def runtime_tree(self):
        """The directories systemd creates for the units, with the layout's modes."""
        for directory, mode in ((self.paths.runtime_root, 0o755), (self.paths.control_dir, 0o755),
                                (self.paths.observer_dir, 0o755), (self.paths.state_dir, 0o700)):
            os.makedirs(directory, exist_ok=True)
            os.chmod(directory, mode)

    def has(self, *prefix):
        return any(command[: len(prefix)] == list(prefix) for command in self.commands)


@pytest.fixture()
def host(tmp_path, monkeypatch):
    return Host(tmp_path, monkeypatch)


def test_pin_duration_and_version_helpers_are_strict():
    assert installer.parse_pin(f"2.2.0@sha256:{CGPROFILE_SHA}", "cgprofile") == ("2.2.0", CGPROFILE_SHA)
    assert installer.version_key("2.2.0") > installer.version_key("2.2.0rc1")
    assert installer.has_socket_units("2.2.0")
    assert not installer.has_socket_units("2.1.9")
    assert installer.parse_duration("10m") == 600
    assert installer.parse_duration("3h") == 10800
    with pytest.raises(installer.InstallError, match="64 lowercase hex"):
        installer.parse_pin("2.2.0@sha256:ABC", "cgprofile")
    with pytest.raises(installer.InstallError, match="64 lowercase hex"):
        installer.parse_pin(None, "cgprofile")
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
    dropin = paths.at("/etc/systemd/system/cgprofiled.service.d/50-mdt.conf")
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
    unit = Path(paths.unit_dir) / "cgprofiled.service"
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text("old\n", encoding="utf-8")

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
    assert "cgprofiled install refused" in message


def test_units_without_an_install_record_or_pending_are_not_ours(tmp_path):
    paths = _paths(tmp_path)
    for name in ("cgprofiled.service", "cgprofile.slice", "cgprofile-control.socket", "cgprofile-observer.socket"):
        unit = Path(paths.unit_dir) / name
        unit.parent.mkdir(parents=True, exist_ok=True)
        unit.write_text("old\n", encoding="utf-8")
    problems = installer.old_installation_problems(paths)
    assert sum("without install record" in item for item in problems) == 4
    Path(paths.install_record).parent.mkdir(parents=True)
    Path(paths.install_record).write_text("created_groups = []\n", encoding="utf-8")
    assert installer.old_installation_problems(paths) == []


def test_the_2x_layout_is_refused_with_removal_steps_and_never_migrated(tmp_path):
    paths = _paths(tmp_path)
    assert not any(item.startswith("2.x layout:") for item in installer.old_installation_problems(paths))
    Path(paths.unit_dir).mkdir(parents=True)
    (Path(paths.unit_dir) / "cgpd.service").write_text("old\n", encoding="utf-8")
    (Path(paths.unit_dir) / "cgpd.service.d").mkdir()
    Path(paths.config).parent.mkdir(parents=True)
    (Path(paths.config).parent / "cgpd.toml").write_text("interval = 2\n", encoding="utf-8")
    client = Path(paths.at("/usr/local/bin/cgpctl"))
    client.parent.mkdir(parents=True)
    client.symlink_to("/opt/cgprofile/current/venv/bin/cgpctl")  # a dangling link still counts

    problems = installer.old_installation_problems(paths)
    message = installer.manual_removal_message(problems)

    found = [item for item in problems if item.startswith("2.x layout:")]
    assert sorted(item.rsplit("/", 1)[-1] for item in found) == ["cgpctl", "cgpd.service", "cgpd.service.d", "cgpd.toml"]
    assert "cgpd.service" in message and "/usr/local/bin/cgpctl" in message and "/etc/cgprofile/cgpd.toml" in message
    assert "nothing is migrated" in message and "cgprofiled.toml" in message


@pytest.mark.parametrize("old", ["cgpd.service", "cgpd.service.d", "cgpd.toml", "cgpctl"])
def test_install_refuses_each_2x_path_before_changing_anything(host, tmp_path, old):
    target = {
        "cgpd.service": host.paths.at("/etc/systemd/system/cgpd.service"),
        "cgpd.service.d": host.paths.at("/etc/systemd/system/cgpd.service.d"),
        "cgpd.toml": host.paths.at("/etc/cgprofile/cgpd.toml"),
        "cgpctl": host.paths.at("/usr/local/bin/cgpctl"),
    }[old]
    Path(target).parent.mkdir(parents=True, exist_ok=True)
    if old == "cgpd.service.d":
        Path(target).mkdir()
    else:
        Path(target).write_text("x\n", encoding="utf-8")
    before = _snapshot(tmp_path)
    with pytest.raises(installer.InstallError, match="cgprofiled install refused") as caught:
        host.install()
    assert f"2.x layout: {target}" in str(caught.value)
    assert _snapshot(tmp_path) == before and not host.commands


def test_a_seeded_old_config_gets_the_removal_list_not_a_bare_invalid_config_error(host, tmp_path):
    """C3: `_config_groups` used to reject the seeded `ctl_socket` config before the P-11 list was built."""
    Path(host.paths.config).parent.mkdir(parents=True)
    Path(host.paths.config).write_text('ctl_socket = "/run/cgprofile/ctl.sock"\n', encoding="utf-8")
    before = _snapshot(tmp_path)
    with pytest.raises(installer.InstallError) as caught:
        host.install()
    text = str(caught.value)
    assert "cgprofiled install refused" in text and "old config keys: ctl_socket" in text
    assert "Manual steps:" in text and "is invalid" not in text
    assert _snapshot(tmp_path) == before and not host.commands


def test_a_differently_suffixed_mdt_manifest_is_still_the_old_installer(tmp_path):
    version = Path(_paths(tmp_path).opt) / "2.1.0"
    version.mkdir(parents=True)
    (version / "MANIFEST").write_text("installer=mdt host-setup (MDT-099)\n", encoding="utf-8")
    assert any("mdt host-setup MANIFEST" in item for item in installer.old_installation_problems(_paths(tmp_path)))


def test_version_root_findings_get_their_own_message_not_the_old_host_steps(tmp_path):
    paths = _paths(tmp_path)
    opt = Path(paths.opt)
    (opt / "2.2.0").mkdir(parents=True)  # no MANIFEST
    (opt / "2.1.0").mkdir()
    (opt / "2.1.0" / "MANIFEST").write_text("installer=something else\n", encoding="utf-8")
    (opt / "2.0.0").mkdir()
    (opt / "2.0.0" / "MANIFEST").mkdir()  # unreadable as a file
    (opt / ".stage.abc").mkdir()
    (opt / ".cgprofiled-render.abc").mkdir()
    (opt / "PENDING").write_text("2.2.0\n", encoding="utf-8")
    (opt / "linked").symlink_to("2.2.0")
    (opt / "1.9.0").mkdir()
    (opt / "1.9.0" / "MANIFEST").write_text("installer=cgprofiled install 1.9.0\n", encoding="utf-8")

    problems = installer.old_installation_problems(paths)
    message = installer.manual_removal_message(problems)

    assert any(item.startswith("unmarked version tree:") for item in problems)
    assert any(item.startswith("unknown version tree MANIFEST:") for item in problems)
    assert any(item.startswith("cannot inspect version MANIFEST") for item in problems)
    assert sum(item.startswith("leftover version staging directory:") for item in problems) == 2
    assert any(item.startswith("version tree is a symlink:") for item in problems)
    assert any(item.startswith("leftover install marker:") for item in problems)
    assert not any("1.9.0" in item for item in problems)
    assert "Under /opt/cgprofile" in message
    assert "cgprofiled uninstall --yes" in message
    assert "Manual steps:" not in message
    assert "remove the old installation" not in message
    assert installer.manual_removal_message([]).count("Manual steps:") == 1


def test_a_version_root_that_is_not_a_directory_is_reported(tmp_path):
    paths = _paths(tmp_path)
    Path(paths.opt).parent.mkdir(parents=True)
    Path(paths.opt).write_text("file\n", encoding="utf-8")
    assert any(item.startswith("version root is not a real directory:")
               for item in installer.old_installation_problems(paths))


def test_unreadable_version_root_and_rules_are_reported_not_raised(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    Path(paths.opt).mkdir(parents=True)
    real_listdir = os.listdir

    def listdir(path):
        if str(path) == paths.opt:
            raise PermissionError(13, "Permission denied")
        return real_listdir(path)

    monkeypatch.setattr(installer.os, "listdir", listdir)
    assert any(item.startswith("cannot inspect version root") for item in installer.old_installation_problems(paths))
    with pytest.raises(installer.InstallError, match="cannot inspect"):
        installer._directory_names(paths.opt)


def test_foreign_rules_read_failures_are_findings(tmp_path):
    paths = _paths(tmp_path)
    rule = Path(paths.at("/etc/tmpfiles.d/other.conf"))
    rule.parent.mkdir(parents=True)
    rule.write_bytes(b"\xff\xfe")
    dropin = Path(paths.at("/etc/systemd/system/cgprofiled.service.d/10-x.conf"))
    dropin.parent.mkdir(parents=True)
    dropin.write_bytes(b"\xff\xfe")
    found = installer.find_foreign_rules(paths)
    assert any("other.conf" in item and "cannot inspect" in item for item in found)
    assert any("10-x.conf" in item and "cannot inspect" in item for item in found)
    rule.write_text("d /run/cgprofile 0755 root root -\n", encoding="utf-8")
    dropin.write_text("[Service]\nEnvironment=X=1\n", encoding="utf-8")
    found = installer.find_foreign_rules(paths)
    assert str(rule) in found and str(dropin) not in found


def test_unreadable_rule_directory_is_a_finding(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    directory = Path(paths.at("/etc/tmpfiles.d"))
    directory.mkdir(parents=True)
    real_listdir = os.listdir

    def listdir(path):
        if str(path) == str(directory):
            raise PermissionError(13, "Permission denied")
        return real_listdir(path)

    monkeypatch.setattr(installer.os, "listdir", listdir)
    assert any("cannot inspect" in item for item in installer.find_foreign_rules(paths))


def test_run_tree_problems_cover_wrong_owner_type_and_unreadable(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    Path(paths.control_dir).mkdir(parents=True)
    Path(paths.control_dir).chmod(0o755)
    Path(paths.runtime_root).chmod(0o755)
    Path(paths.observer_dir).write_text("not a directory\n", encoding="utf-8")
    monkeypatch.setattr(installer, "RUNTIME_TREE_OWNER", (0, 0))
    problems = installer._run_tree_problems(paths)
    assert any(paths.control_dir in item for item in problems)
    assert any(paths.observer_dir in item for item in problems)
    monkeypatch.setattr(installer, "RUNTIME_TREE_OWNER", (os.getuid(), os.getgid()))
    Path(paths.state_dir).mkdir()
    Path(paths.state_dir).chmod(0o700)
    assert [item for item in installer._run_tree_problems(paths) if paths.observer_dir not in item] == []
    real_lstat = os.lstat

    def lstat(path, *args, **kwargs):
        if str(path) == paths.state_dir:
            raise PermissionError(13, "Permission denied")
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(installer.os, "lstat", lstat)
    assert any("cannot inspect" in item and paths.state_dir in item
               for item in installer._run_tree_problems(paths))


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


def test_noninteractive_missing_group_and_enable_name_their_flags(tmp_path):
    with pytest.raises(installer.InstallError, match="missing required --control-group"):
        installer._gather_inputs(_args(control_group=None), _runtime(), _paths(tmp_path))
    with pytest.raises(installer.InstallError, match="--enable and --no-enable"):
        installer._gather_inputs(_args(enable=True, no_enable=True), _runtime(), _paths(tmp_path))
    with pytest.raises(installer.InstallError, match="--enable or --no-enable"):
        installer._gather_inputs(_args(enable=False, no_enable=False), _runtime(), _paths(tmp_path))


def test_tty_wizard_resolves_missing_pins_groups_and_enable(tmp_path, monkeypatch):
    monkeypatch.setattr(installer.pwd, "getpwnam", lambda _name: SimpleNamespace(pw_name="alice"))
    monkeypatch.setattr(installer, "_group_gid", lambda _name: None)
    prompts = Prompts(text_values=[f"2.2.0@sha256:{CGPROFILE_SHA}",
                                  f"0.5.3@sha256:{CLI_EXTENDED_SHA}", "alice", ""],
                      confirm_value=True)
    args = _args(wheel=None, cli_extended_wheel=None, control_group=None, observer_group=None,
                 add_user=None, enable=False, no_enable=False)

    values = installer._gather_inputs(args, _runtime(interactive=True, prompts=prompts), _paths(tmp_path))

    assert values["version"] == "2.2.0" and values["cli_extended_version"] == "0.5.3"
    assert values["control_group"] == "cgprofile-control"
    assert values["observer_group"] == "cgprofile-observer"
    assert values["control_users"] == ["alice"] and values["observer_users"] == []
    assert values["enable_now"] is True
    assert [call[0] for call in prompts.calls] == ["text", "text", "select", "select", "text", "text", "confirm"]


def test_3x_requires_a_pinned_augeaswrap_wheel_and_dependency_floors(tmp_path):
    with pytest.raises(installer.InstallError, match="missing required --augeaswrap-wheel"):
        installer._gather_inputs(_args(version="3.0.0"), _runtime(), _paths(tmp_path))
    with pytest.raises(installer.InstallError, match="cli-extended 0.5.2 is too old"):
        installer._gather_inputs(_args(cli_extended_wheel=f"0.5.2@sha256:{CLI_EXTENDED_SHA}"),
                                  _runtime(), _paths(tmp_path))
    with pytest.raises(installer.InstallError, match="augeaswrap 0.0.9 is too old"):
        installer._gather_inputs(_args(version="3.0.0", augeaswrap_wheel=f"0.0.9@sha256:{AUGEASWRAP_SHA}"),
                                  _runtime(), _paths(tmp_path))


def test_tty_wizard_can_map_an_existing_group(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "_group_gid", lambda name: {"ops": 4200}.get(name))
    prompts = Prompts(text_values=["ops"], select_value="map an existing group")
    runtime = _runtime(interactive=True, prompts=prompts)
    assert installer._prompt_group(runtime, "control", "cgprofile-control", None, None) == "ops"
    assert installer._prompt_group(runtime, "control", "cgprofile-control", None, "explicit") == "explicit"
    assert installer._prompt_group(runtime, "control", "cgprofile-control", "saved", None) == "saved"


def test_tty_prompts_cancelled_exit_130(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "_group_gid", lambda _name: None)
    runtime = _runtime(interactive=True, prompts=CancelledPrompts())
    with pytest.raises(installer.InstallError) as caught:
        installer._prompt_missing(runtime, None, "--wheel", "pin")
    assert caught.value.exit_code == 130
    with pytest.raises(installer.InstallError) as caught:
        installer._prompt_group(runtime, "control", "cgprofile-control", None, None)
    assert caught.value.exit_code == 130
    args = _args(enable=False, no_enable=False)
    with pytest.raises(installer.InstallError) as caught:
        installer._gather_inputs(args, runtime, _paths(tmp_path))
    assert caught.value.exit_code == 130


def test_tty_enable_prompt_cancellation_exits_130(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "_group_gid", lambda _name: None)
    paths = _paths(tmp_path)
    Path(paths.install_record).parent.mkdir(parents=True, exist_ok=True)
    Path(paths.install_record).write_text(
        'control_users = []\nobserver_users = []\n', encoding="utf-8")
    args = _args(enable=False, no_enable=False)
    runtime = _runtime(interactive=True, prompts=CancelledPrompts())
    with pytest.raises(installer.InstallError) as caught:
        installer._gather_inputs(args, runtime, paths)
    assert caught.value.exit_code == 130 and "prompts cancelled" in str(caught.value)


def test_a_dry_run_needs_no_enable_decision_but_a_real_pipe_run_does(tmp_path):
    args = _args(enable=False, no_enable=False)
    assert installer._gather_inputs(args, _runtime(dry_run=True), _paths(tmp_path))["enable_now"] is False
    with pytest.raises(installer.InstallError, match="--enable or --no-enable"):
        installer._gather_inputs(args, _runtime(json_mode=True), _paths(tmp_path))


def test_missing_user_and_bad_user_names_are_refused(monkeypatch):
    def getpwnam(name):
        raise KeyError(name)

    monkeypatch.setattr(installer.pwd, "getpwnam", getpwnam)
    with pytest.raises(installer.InstallError, match="not a usable Unix user name"):
        installer._validate_users(["-evil"])
    with pytest.raises(installer.InstallError, match="does not exist"):
        installer._validate_users(["ghost"])


@pytest.mark.parametrize("spec,match", [
    ("", "LEVEL=USER"),
    ("admin=alice", "unknown --add-user access level"),
    ("control=", "non-empty user"),
    ("observer=alice=extra", "not a usable Unix user name"),
])
def test_add_user_flag_requires_one_level_and_existing_user(monkeypatch, spec, match):
    monkeypatch.setattr(installer.pwd, "getpwnam", lambda name: SimpleNamespace(pw_name=name))
    with pytest.raises(installer.InstallError, match=match):
        installer._parse_add_users([spec])


def test_add_user_flag_is_repeatable_and_keeps_level_order(monkeypatch):
    monkeypatch.setattr(installer.pwd, "getpwnam", lambda name: SimpleNamespace(pw_name=name))
    assert installer._parse_add_users(["observer=bob", "control=alice", "control=alice"]) == {
        "control": ["alice"], "observer": ["bob"],
    }


def test_uid_list_parsing_rejects_garbage():
    assert installer._parse_uid_list(" ") == []
    assert installer._parse_uid_list("0, 1000,0") == [0, 1000]
    with pytest.raises(installer.InstallError, match="comma-separated list"):
        installer._parse_uid_list("a")
    with pytest.raises(installer.InstallError, match="non-negative"):
        installer._parse_uid_list("-1")


def test_pre_socket_release_needs_allow_downgrade_and_an_installation(tmp_path, host):
    with pytest.raises(installer.InstallError, match="supports cgprofile 2.2.0 and later"):
        installer._gather_inputs(_args(version="2.1.0"), _runtime(), host.paths)
    with pytest.raises(installer.InstallError, match="does not provide the native host service"):
        installer._gather_inputs(_args(version="1.9.0", allow_downgrade=True), _runtime(), host.paths)
    values = installer._gather_inputs(_args(version="2.1.0", allow_downgrade=True), _runtime(), host.paths)
    # a fresh host has nothing to roll back
    with pytest.raises(installer.InstallError, match="no installation is present to roll back"):
        host.subject._preflight(values, host.paths.config, dry_run=True)


def test_mapped_group_must_exist_and_all_levels_reject_docker_root_and_duplicate_gids(monkeypatch):
    gids = {"docker": 100, "same": 200, "control": 200, "observer": 200, "root": 0,
            "docker-alias": 100}
    monkeypatch.setattr(installer, "_group_gid", lambda name: gids.get(name))
    with pytest.raises(installer.InstallError, match="mapped control group.*does not exist"):
        installer._validate_group_mapping("missing", "cgprofile-observer")
    with pytest.raises(installer.InstallError, match="mapped observer group.*does not exist"):
        installer._validate_group_mapping("cgprofile-control", "gone")
    with pytest.raises(installer.InstallError, match="the control group 'docker' is the docker group"):
        installer._validate_group_mapping("docker", "cgprofile-observer")
    with pytest.raises(installer.InstallError, match="GID 0"):
        installer._validate_group_mapping("root", "cgprofile-observer")
    with pytest.raises(installer.InstallError, match="GID 0"):
        installer._validate_group_mapping("cgprofile-control", "root")
    with pytest.raises(installer.InstallError, match="docker group's GID"):
        installer._validate_group_mapping("docker-alias", "cgprofile-observer")
    with pytest.raises(installer.InstallError, match="docker group's GID"):
        installer._validate_group_mapping("cgprofile-control", "docker-alias")
    with pytest.raises(installer.InstallError, match="control and observer groups both name 'same'"):
        installer._validate_group_mapping("same", "same")
    with pytest.raises(installer.InstallError, match="same GID"):
        installer._validate_group_mapping("control", "observer")


def test_every_saved_group_input_is_validated_even_when_a_flag_overrides_it(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "_group_gid", lambda name: {"cgprofile-control": 7100,
                                                                 "cgprofile-observer": 7101,
                                                                 "safe": 7102}.get(name))
    paths = _paths(tmp_path)
    Path(paths.install_record).parent.mkdir(parents=True)
    Path(paths.install_record).write_text('control_group = "docker"\n', encoding="utf-8")
    with pytest.raises(installer.InstallError, match="the control group 'docker' is the docker group"):
        installer._gather_inputs(_args(control_group="safe"), _runtime(), paths)


def test_existing_config_group_is_not_overridden_when_unsafe(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "_group_gid", lambda name: {"safe": 7100,
                                                                 "cgprofile-observer": 7101}.get(name))
    paths = _paths(tmp_path)
    Path(paths.config).parent.mkdir(parents=True)
    Path(paths.config).write_text(
        '[sockets.control]\npath = "/run/cgprofile/control/cgprofile.sock"\n'
        'group = "docker"\nmode = "0660"\n', encoding="utf-8")
    with pytest.raises(installer.InstallError, match="docker"):
        installer._gather_inputs(_args(control_group="safe"), _runtime(), paths)


def test_tty_group_prompt_is_subject_to_the_same_group_policy(monkeypatch):
    runtime = _runtime(interactive=True, prompts=Prompts(text_values=["docker"],
                                                        select_value="map an existing group"))
    with pytest.raises(installer.InstallError, match="the control group 'docker' is the docker group"):
        installer._prompt_group(runtime, "control", "cgprofile-control", None, None)


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
    seen = []
    installer._fetch_wheel("https://example.invalid/base", "cgprofile", filename, str(target),
                           lambda url, dest: seen.append(url))
    assert seen == ["https://example.invalid/base/cgprofile-v2.2.0/" + filename]


def test_preflight_refuses_downgrade_until_the_operator_allows_it(tmp_path):
    paths = _paths(tmp_path)
    opt = Path(paths.opt)
    (opt / "2.2.0").mkdir(parents=True)
    (opt / "2.2.0" / "MANIFEST").write_text("installer=cgprofiled install 2.2.0\n", encoding="utf-8")
    (opt / "current").symlink_to("2.2.0")
    Path(paths.install_record).parent.mkdir(parents=True)
    Path(paths.install_record).write_text("created_groups = []\n", encoding="utf-8")
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


def test_preflight_downgrade_without_socket_units_names_the_plain_refusal(tmp_path):
    paths = _paths(tmp_path)
    opt = Path(paths.opt)
    (opt / "2.3.0").mkdir(parents=True)
    (opt / "2.3.0" / "MANIFEST").write_text("installer=cgprofiled install 2.3.0\n", encoding="utf-8")
    (opt / "current").symlink_to("2.3.0")
    Path(paths.install_record).parent.mkdir(parents=True)
    Path(paths.install_record).write_text("created_groups = []\n", encoding="utf-8")
    values = {"version": "2.2.0", "wheel_sha": CGPROFILE_SHA, "cli_extended_sha": CLI_EXTENDED_SHA,
              "allow_downgrade": False}
    with pytest.raises(installer.InstallError, match="refusing downgrade from 2.3.0 to 2.2.0; rerun"):
        _installer(tmp_path)._preflight(values, paths.config, dry_run=True)


def test_preflight_manifest_digest_mismatch_and_unreadable_manifest(tmp_path):
    paths = _paths(tmp_path)
    version = Path(paths.opt) / "2.2.0"
    version.mkdir(parents=True)
    manifest = version / "MANIFEST"
    manifest.write_text("installer=cgprofiled install 2.2.0\ncgprofile_sha256=ffff\n", encoding="utf-8")
    values = {"version": "2.2.0", "wheel_sha": CGPROFILE_SHA, "cli_extended_sha": CLI_EXTENDED_SHA,
              "allow_downgrade": False}
    with pytest.raises(installer.InstallError, match="immutable and its wheel digest differs"):
        _installer(tmp_path)._preflight(values, paths.config, dry_run=True)
    manifest.write_bytes(b"\xff\xfe")
    with pytest.raises(installer.InstallError, match="cannot read immutable version MANIFEST|cannot inspect"):
        _installer(tmp_path)._preflight(values, paths.config, dry_run=True)


def test_current_pointer_must_be_a_complete_relative_version(tmp_path):
    paths = _paths(tmp_path)
    opt = Path(paths.opt)
    opt.mkdir(parents=True)
    subject = _installer(tmp_path)
    assert subject._current_version() is None
    (opt / "current").write_text("not a link\n", encoding="utf-8")
    with pytest.raises(installer.InstallError, match="not a symlink"):
        subject._current_version()
    (opt / "current").unlink()
    (opt / "current").symlink_to("/opt/cgprofile/2.2.0")
    with pytest.raises(installer.InstallError, match="must point to a version directory"):
        subject._current_version()
    (opt / "current").unlink()
    (opt / "current").symlink_to("2.2.0")
    with pytest.raises(installer.InstallError, match="incomplete version tree"):
        subject._current_version()
    (opt / "2.2.0").mkdir()
    (opt / "2.2.0" / "MANIFEST").write_text("installer=cgprofiled install 2.2.0\n", encoding="utf-8")
    assert subject._current_version() == "2.2.0"


def test_dry_run_preflight_does_not_need_root(tmp_path):
    values = installer._gather_inputs(_args(), _runtime(dry_run=True), _paths(tmp_path))
    subject = installer.Installer(_paths(tmp_path), geteuid=lambda: 1000)
    assert subject._preflight(values, subject.paths.config, dry_run=True) == (False, True)
    with pytest.raises(installer.InstallError, match="needs root"):
        subject._preflight(values, subject.paths.config, dry_run=False)


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


def test_downgrade_removes_socket_units_nodes_and_directories(tmp_path):
    paths = _paths(tmp_path)
    resources = _installer(tmp_path)._resource_paths()
    for name in ("cgprofile-control.socket", "cgprofile-observer.socket", "cgprofile.sysusers"):
        Path(resources[name]).parent.mkdir(parents=True, exist_ok=True)
        Path(resources[name]).write_text("owned\n", encoding="utf-8")
    commands = []
    import socket as socket_module

    for level_dir, sock in ((paths.control_dir, paths.at(daemon_config.DEFAULT_CONTROL_SOCKET)),
                            (paths.observer_dir, paths.at(daemon_config.DEFAULT_OBSERVER_SOCKET))):
        Path(level_dir).mkdir(parents=True)
        listener = socket_module.socket(socket_module.AF_UNIX)
        listener.bind(sock)
        listener.close()
    values = {"version": "2.1.0", "allow_downgrade": True,
              "config_values": {"gates_slice": "dev-gates.slice"}}
    subject = installer.Installer(paths, run=_fake_run(commands), geteuid=lambda: 0)
    subject._apply_downgrade(values, resources)
    assert not any(Path(resources[name]).exists() for name in
                   ("cgprofile-control.socket", "cgprofile-observer.socket", "cgprofile.sysusers"))
    assert not Path(paths.control_dir).exists() and not Path(paths.observer_dir).exists()
    # a non-socket node is never deleted
    Path(paths.control_dir).mkdir(parents=True)
    Path(paths.at(daemon_config.DEFAULT_CONTROL_SOCKET)).write_text("regular\n", encoding="utf-8")
    with pytest.raises(installer.InstallError, match="non-socket node"):
        subject._apply_downgrade(values, resources)
    # not a downgrade: nothing happens
    subject._apply_downgrade({"version": "2.2.0", "allow_downgrade": True}, resources)
    subject._apply_downgrade({"version": "2.1.0", "allow_downgrade": False}, resources)


def test_downgrade_reports_an_uninspectable_socket_node(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    resources = _installer(tmp_path)._resource_paths()
    real_lstat = os.lstat

    def lstat(path, *args, **kwargs):
        if str(path) == paths.at(daemon_config.DEFAULT_CONTROL_SOCKET):
            raise PermissionError(13, "Permission denied")
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(installer.os, "lstat", lstat)
    values = {"version": "2.1.0", "allow_downgrade": True, "config_values": {}}
    with pytest.raises(installer.InstallError, match="cannot inspect socket node"):
        _installer(tmp_path)._apply_downgrade(values, resources)


def test_drain_deadline_writes_pending_and_force_records_live_sessions(tmp_path):
    paths = _paths(tmp_path)
    clock = iter((0.0, 10.0))
    subject = _installer(tmp_path, monotonic=lambda: next(clock), sleep=lambda _seconds: None)
    subject._live_sessions = lambda _current: [{"session": "s-live"}]
    values = {"version": "2.2.0", "drain_seconds": 10, "drain_timeout": "10s", "force_restart": False}
    with pytest.raises(installer.DrainTimeout, match="rerun cgprofiled install when the sessions have ended") as caught:
        subject._drain_or_refuse("2.1.0", values)
    assert caught.value.exit_code == 3
    assert "remove" not in str(caught.value)
    assert Path(paths.opt, "PENDING").read_text() == "2.2.0\n"

    forced = {**values, "force_restart": True}
    subject._drain_or_refuse("2.1.0", forced)
    assert forced["forced_sessions"] == ["s-live"]


def test_drain_waits_then_proceeds_when_the_sessions_end(tmp_path):
    answers = iter(([{"session": "a"}], []))
    slept = []
    clock = iter((0.0, 1.0, 2.0, 3.0, 4.0))
    subject = _installer(tmp_path, monotonic=lambda: next(clock), sleep=slept.append)
    subject._live_sessions = lambda _current: next(answers)
    subject._drain_or_refuse("2.1.0", {"version": "2.2.0", "drain_seconds": 600, "force_restart": False})
    assert slept


def test_live_session_listing_failures_refuse_the_restart(tmp_path):
    subject = _installer(tmp_path)
    subject._command = lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, "", "down")
    with pytest.raises(installer.InstallError, match="cannot list live"):
        subject._live_sessions("2.1.0")
    for stdout, match in (("not json", "invalid JSON"), (json.dumps({"x": 1}), "no session list")):
        subject._command = lambda argv, _stdout=stdout, **kwargs: subprocess.CompletedProcess(argv, 0, _stdout, "")
        with pytest.raises(installer.InstallError, match=match):
            subject._live_sessions("2.1.0")
    subject._command = lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, json.dumps(
        {"sessions": [{"session": "a", "state": "live"}, {"session": "b", "state": "done"}, "x"]}), "")
    assert subject._live_sessions("2.1.0") == [{"session": "a", "state": "live"}]


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
    subject._command = lambda _argv, **_kwargs: subprocess.CompletedProcess([], 0, "not json", "")
    now[0] = 0.0
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
    # once 2.3.0 is current, 2.0.0 is neither current nor among the newest two
    assert _installer(tmp_path)._prune_versions("2.3.0") == ["2.0.0"]
    assert _installer(tmp_path)._prune_versions("2.3.0") == []
    shutil_missing = _installer(tmp_path.parent / "absent-root")._prune_versions("2.3.0")
    assert shutil_missing == []


def test_install_dry_run_reports_candidate_and_add_user_memberships(tmp_path, monkeypatch):
    subject = _installer(tmp_path)
    gids = {"cgprofile-control": 7100, "cgprofile-observer": 7101}
    monkeypatch.setattr(installer, "_group_gid", lambda name: gids.get(name))
    monkeypatch.setattr(installer.pwd, "getpwnam", lambda name: SimpleNamespace(pw_name=name))

    body = subject.install(_args(add_user=["control=alice", "observer=bob"]), _runtime(dry_run=True))

    assert body["dry_run"] is True and body["version"] == "2.2.0"
    assert body["endpoint_fragment"]["path"] == subject.paths.endpoint_fragment
    assert body["endpoint_fragment"]["owner"] == "cgprofile"
    assert set(body["endpoint_fragment"]["entries"]) == {
        "CGPROFILE_CONTROL_SOCKET", "CGPROFILE_CONTROL_SOCKET_DIR", "CGPROFILE_CONTROL_GID",
        "CGPROFILE_OBSERVER_SOCKET", "CGPROFILE_OBSERVER_SOCKET_DIR", "CGPROFILE_OBSERVER_GID",
    }
    assert "would publish host endpoint fragment" in installer._install_summary(body)
    assert body["socket_mode"] == "activated"
    assert body["users_to_add"] == {
        "control": {"group": "cgprofile-control", "users": ["alice"]},
        "observer": {"group": "cgprofile-observer", "users": ["bob"]},
    }
    assert "would add user alice to cgprofile-control" in installer._install_summary(body)
    assert not Path(_paths(tmp_path).opt).exists()


def test_install_drops_and_reports_recorded_users_whose_accounts_are_gone(host, monkeypatch):
    Path(host.paths.install_record).parent.mkdir(parents=True, exist_ok=True)
    Path(host.paths.install_record).write_text(
        'control_users = ["retired-user"]\nobserver_users = []\n', encoding="utf-8")

    def getpwnam(name):
        if name == "retired-user":
            raise KeyError(name)
        return SimpleNamespace(pw_name=name)

    monkeypatch.setattr(installer.pwd, "getpwnam", getpwnam)

    body = host.install()

    assert body["users_forgotten"] == ["retired-user"]
    assert "dropped recorded users that no longer exist: retired-user" in installer._install_summary(body)
    assert not any(command[:2] == ["gpasswd", "-a"] and "retired-user" in command
                   for command in host.commands)
    record = Path(host.paths.install_record).read_text(encoding="utf-8")
    assert "control_users = []" in record
    assert "retired-user" not in record


def test_install_dry_run_reports_recorded_users_whose_accounts_are_gone(host, monkeypatch):
    Path(host.paths.install_record).parent.mkdir(parents=True, exist_ok=True)
    Path(host.paths.install_record).write_text(
        'observer_users = ["retired-observer"]\n', encoding="utf-8")

    def getpwnam(name):
        if name == "retired-observer":
            raise KeyError(name)
        return SimpleNamespace(pw_name=name)

    monkeypatch.setattr(installer.pwd, "getpwnam", getpwnam)

    body = host.install(runtime=_runtime(dry_run=True))

    assert body["users_forgotten"] == ["retired-observer"]
    assert "dropped recorded users that no longer exist: retired-observer" in installer._install_summary(body)
    assert host.commands == []
    assert not Path(host.paths.opt).exists()


def test_install_still_refuses_a_missing_user_named_with_add_user(host, monkeypatch):
    def getpwnam(name):
        if name == "missing-user":
            raise KeyError(name)
        return SimpleNamespace(pw_name=name)

    monkeypatch.setattr(installer.pwd, "getpwnam", getpwnam)

    with pytest.raises(installer.InstallError, match="user 'missing-user' does not exist") as caught:
        host.install(_args(add_user=["control=missing-user"]))

    assert caught.value.exit_code == 2
    assert host.commands == []
    assert not Path(host.paths.pending).exists()
    assert not Path(host.paths.config).exists()
    assert not Path(host.paths.install_record).exists()
    assert not Path(host.paths.opt).exists()


def test_tty_wizard_refuses_a_prompted_nonexistent_user(tmp_path, monkeypatch):
    def getpwnam(name):
        if name == "missing-user":
            raise KeyError(name)
        return SimpleNamespace(pw_name=name)

    monkeypatch.setattr(installer.pwd, "getpwnam", getpwnam)
    monkeypatch.setattr(installer, "_group_gid", lambda _name: None)
    prompts = Prompts(text_values=["missing-user"])

    with pytest.raises(installer.InstallError, match="user 'missing-user' does not exist"):
        installer._gather_inputs(
            _args(add_user=None), _runtime(interactive=True, prompts=prompts), _paths(tmp_path))

    assert prompts.calls == [(
        "text", "Users to add to the control group (comma-separated)",
        {"default": "", "required": False},
    )]


@pytest.mark.parametrize(
    "groups,overrides,needles",
    [
        ({"docker": 9001}, {"control_group": "docker"},
         ("control group 'docker'", "docker group", "cgprofile-control")),
        ({"docker": 9001}, {"observer_group": "docker"},
         ("observer group 'docker'", "docker group", "cgprofile-observer")),
        ({"docker": 9001, "docker-alias": 9001}, {"control_group": "docker-alias"},
         ("control group 'docker-alias'", "docker group's GID 9001", "cgprofile-control")),
        ({"docker": 9001, "docker-alias": 9001}, {"observer_group": "docker-alias"},
         ("observer group 'docker-alias'", "docker group's GID 9001", "cgprofile-observer")),
        ({"root-alias": 0}, {"control_group": "root-alias"},
         ("control group 'root-alias'", "GID 0", "cgprofile-control")),
        ({"root-alias": 0}, {"observer_group": "root-alias"},
         ("observer group 'root-alias'", "GID 0", "cgprofile-observer")),
        ({"shared": 9002}, {"control_group": "shared", "observer_group": "shared"},
         ("control and observer groups both name 'shared'",)),
        ({"control-alias": 9003, "observer-alias": 9003},
         {"control_group": "control-alias", "observer_group": "observer-alias"},
         ("control group 'control-alias' and observer group 'observer-alias'", "same GID 9003")),
    ],
)
def test_install_refuses_unsafe_group_flags_before_any_write(tmp_path, monkeypatch, groups, overrides, needles):
    host = Host(tmp_path, monkeypatch, groups=groups)

    with pytest.raises(installer.InstallError) as caught:
        host.install(_args(**overrides))

    assert caught.value.exit_code == 2
    message = str(caught.value)
    for needle in needles:
        assert needle in message
    assert host.commands == []
    assert not Path(host.paths.pending).exists()
    assert not Path(host.paths.config).exists()
    assert not Path(host.paths.install_record).exists()
    assert not Path(host.paths.opt).exists()
    assert not Path(host.paths.unit_dir).exists()


@pytest.mark.parametrize("level,group", [("control", "docker"), ("observer", "docker")])
def test_explicit_group_flag_resolution_refuses_docker_by_name(monkeypatch, level, group):
    monkeypatch.setattr(installer, "_group_gid", lambda name: {"docker": 9001}.get(name))

    with pytest.raises(installer.InstallError, match=f"{level} group 'docker'.*dedicated group"):
        installer._prompt_group(_runtime(), level, f"cgprofile-{level}", None, group)


def test_install_applies_config_units_groups_links_and_start(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    Path(paths.install_record).parent.mkdir(parents=True, exist_ok=True)
    Path(paths.install_record).write_text('created_groups = ["cgprofile-control"]\n', encoding="utf-8")
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
    assert os.readlink(paths.client_link) == paths.client_target
    # both clients are linked: the socket client and the end-user client (E.2)
    assert os.readlink(paths.user_link) == paths.user_target
    assert paths.user_link.endswith("/usr/local/bin/cgprofile") and paths.user_target.endswith("/venv/bin/cgprofile")
    record = Path(paths.install_record).read_text(encoding="utf-8")
    assert "created_groups = [\"cgprofile-control\", \"cgprofile-observer\"]" in record
    assert ["systemctl", "start", "cgprofile-control.socket", "cgprofile-observer.socket"] in commands
    assert ["systemctl", "start", "cgprofiled.service"] in commands
    assert group_gids == {"cgprofile-control": 7101, "cgprofile-observer": 7102}
    assert not Path(paths.pending).exists()
    fragment = Path(paths.endpoint_fragment).read_text(encoding="ascii")
    assert "# owner=cgprofile\n" in fragment and "# writer=cgprofiled install 2.2.0\n" in fragment
    assert "CGPROFILE_CONTROL_GID=7101\n" in fragment
    assert "CGPROFILE_OBSERVER_GID=7102\n" in fragment
    assert Path(paths.at("/etc/host-endpoints.env")).is_file()


def test_publish_precedes_the_install_record(host, monkeypatch):
    original = installer._atomic_write
    observed = []

    def write(path, data, mode):
        if path == host.paths.install_record:
            observed.append(os.path.isfile(host.paths.endpoint_fragment))
        return original(path, data, mode)

    monkeypatch.setattr(installer, "_atomic_write", write)
    host.install()
    assert observed == [True]


def test_3x_install_fetches_and_installs_the_pinned_augeaswrap_wheel(host):
    pin = f"0.1.0@sha256:{AUGEASWRAP_SHA}"
    host.install(_args(version="3.0.0", augeaswrap_wheel=pin))

    pip = next(command for command in host.commands if command[1:4] == ["-m", "pip", "install"])
    assert [Path(item).name for item in pip if item.endswith(".whl")] == [
        "cgprofile-3.0.0-py3-none-any.whl",
        "cli_extended-0.5.3-py3-none-any.whl",
        "augeaswrap-0.1.0-py3-none-any.whl",
    ]
    record = Path(host.paths.install_record).read_text(encoding="utf-8")
    assert f'augeaswrap_wheel = "{pin}"' in record


def test_endpoint_helper_missing_from_bootstrap_runtime_fails_closed(monkeypatch, tmp_path):
    subject = _installer(tmp_path)

    def missing(_name):
        raise ModuleNotFoundError("No module named 'augeaswrap'", name="augeaswrap")

    monkeypatch.setattr(installer.importlib, "import_module", missing)
    with pytest.raises(installer.InstallError, match="augeaswrap.endpoints is unavailable"):
        subject._endpoints_api()


def test_endpoint_helper_propagates_missing_transitive_dependencies(monkeypatch, tmp_path):
    subject = _installer(tmp_path)

    def missing(_name):
        raise ModuleNotFoundError("No module named 'cli_extended.identity'", name="cli_extended")

    monkeypatch.setattr(installer.importlib, "import_module", missing)
    with pytest.raises(ModuleNotFoundError, match="cli_extended.identity"):
        subject._endpoints_api()


def test_endpoint_entries_refuse_a_group_without_a_gid(monkeypatch):
    monkeypatch.setattr(installer.daemon_config, "validate_socket_groups", lambda _sockets: None)
    monkeypatch.setattr(installer, "_group_gid", lambda _name: None)

    with pytest.raises(installer.InstallError, match="configured control group 'cgprofile-control' has no GID"):
        installer._endpoint_entries({}, resolve_gids=True)


def test_endpoint_preflight_refuses_an_existing_key_collision(host, tmp_path):
    from augeaswrap import endpoints

    endpoints.publish("another-owner", {"CGPROFILE_CONTROL_SOCKET": "/tmp/other.sock"},
                      writer="test publish", root=tmp_path)
    values = installer._gather_inputs(_args(), _runtime(dry_run=True), host.paths)
    with pytest.raises(installer.InstallError, match="augeaswrap.endpoints.plan refused.*collision"):
        host.subject._preflight(values, host.paths.config, dry_run=True)


def test_install_needs_confirmation_unless_yes(host):
    runtime = _runtime(confirmed=False)
    with pytest.raises(installer.InstallError, match="was not confirmed"):
        host.install(runtime=runtime)
    assert runtime.asked and not os.path.lexists(host.paths.opt)
    assert not host.commands


def test_install_restarts_inactive_socket_units_after_drain(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    opt = Path(paths.opt)
    opt.mkdir(parents=True)
    (opt / "2.1.0").mkdir()
    (opt / "2.1.0" / "MANIFEST").write_text("installer=cgprofiled install 2.1.0\n", encoding="utf-8")
    (opt / "current").symlink_to("2.1.0")
    Path(paths.install_record).parent.mkdir(parents=True)
    Path(paths.install_record).write_text("created_groups = []\n", encoding="utf-8")
    commands = []
    group_gids = {"cgprofile-control": 7101, "cgprofile-observer": 7102}
    monkeypatch.setattr(installer, "_group_gid", lambda name: group_gids.get(name))
    active = {"cgprofiled.service": True}
    subject = installer.Installer(paths, run=_fake_run(commands, group_gids, active),
                                  fetch=_fake_fetch, geteuid=lambda: 0, python="python3")
    drained = []
    monkeypatch.setattr(installer.Installer, "_drain_or_refuse",
                        lambda self, _current, _values, *_rest: drained.append(_current))
    monkeypatch.setattr(installer.Installer, "_wait_ready", lambda self, _venv, _version: None)

    body = subject.install(_args(), _runtime(yes=True))

    assert body["ok"] is True and drained == ["2.1.0"]
    assert ["systemctl", "restart", "cgprofile-control.socket"] in commands
    assert ["systemctl", "restart", "cgprofile-observer.socket"] in commands


def test_active_service_without_a_current_pointer_is_a_refusal_not_a_traceback(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch, active={"cgprofiled.service": True})
    with pytest.raises(installer.InstallError, match="current is missing"):
        host.install()
    # nothing was applied and PENDING did not survive the refusal
    assert not Path(host.paths.pending).exists()
    assert not Path(host.paths.config).exists()


# --- H2: a saved install record with lists reruns cleanly ---------------------------------


def test_saved_record_lists_are_used_as_lists(host):
    Path(host.paths.install_record).parent.mkdir(parents=True)
    Path(host.paths.install_record).write_text(
        'control_users = ["root"]\nobserver_users = ["daemon", "nobody"]\n'
        'observe_slices = ["a.slice"]\nallow_uids = [0, 1000]\n', encoding="utf-8")
    args = _args(add_user=None)
    values = installer._gather_inputs(args, _runtime(), host.paths)
    assert values["control_users"] == ["root"]
    assert values["observer_users"] == ["daemon", "nobody"]
    assert values["observe_slices"] == ["a.slice"]
    assert values["allow_uids"] == [0, 1000]


def test_install_twice_with_users_keeps_working_without_flags(host, monkeypatch):
    monkeypatch.setattr(installer.pwd, "getpwnam", lambda name: SimpleNamespace(pw_name=name))
    first = host.install(_args(add_user=["control=root", "observer=daemon"], observe_slices="a.slice",
                               allow_uids="1000"))
    assert first["ok"]
    record = Path(host.paths.install_record).read_text(encoding="utf-8")
    assert 'control_users = ["root"]' in record and 'observer_users = ["daemon"]' in record
    host.commands.clear()
    again = host.install(_args(wheel=None, cli_extended_wheel=None, control_group=None, observer_group=None,
                               add_user=None, enable=False, no_enable=False))
    assert again["ok"] and again["config_changes"] == []
    assert ["gpasswd", "-a", "root", "cgprofile-control"] in host.commands
    assert Path(host.paths.install_record).read_text(encoding="utf-8") == record


def test_add_user_appends_to_the_saved_record_without_forgetting_prior_members(host, monkeypatch):
    monkeypatch.setattr(installer.pwd, "getpwnam", lambda name: SimpleNamespace(pw_name=name))
    Path(host.paths.install_record).parent.mkdir(parents=True)
    Path(host.paths.install_record).write_text('control_users = ["alice"]\n', encoding="utf-8")
    values = installer._gather_inputs(_args(add_user=["control=bob"]), _runtime(), host.paths)
    assert values["control_users"] == ["alice", "bob"]


def test_invalid_saved_record_values_are_refused_by_name(host):
    for body, match in (
        ('control_users = [1]\n', "saved control_users value is invalid"),
        ('observer_users = 3\n', "saved observer_users value is invalid"),
        ('observe_slices = [true]\n', "saved observe_slices value is invalid"),
        ('allow_uids = ["x"]\n', "saved allow_uids value is invalid"),
    ):
        Path(host.paths.install_record).parent.mkdir(parents=True, exist_ok=True)
        Path(host.paths.install_record).write_text(body, encoding="utf-8")
        with pytest.raises(installer.InstallError, match=match):
            installer._gather_inputs(_args(add_user=None), _runtime(), host.paths)
    assert installer._record_words("a, b", "x") == ["a", "b"] and installer._record_words(None, "x") == []
    assert installer._record_uids(None) == []


def test_unreadable_record_is_refused(host):
    Path(host.paths.install_record).parent.mkdir(parents=True)
    Path(host.paths.install_record).write_text("not = [valid\n", encoding="utf-8")
    with pytest.raises(installer.InstallError, match="cannot read prior install record"):
        installer._read_record(host.paths.install_record)
    Path(host.paths.install_record).write_text('created_groups = [1]\n', encoding="utf-8")
    with pytest.raises(installer.InstallError, match="invalid created_groups"):
        installer._record_created_groups(installer._read_record(host.paths.install_record))


# --- M1: a foreign /usr/local/bin/cgprofilectl entry is never replaced --------------------------


def test_foreign_client_link_is_refused_before_any_change(host):
    link = Path(host.paths.client_link)
    link.parent.mkdir(parents=True)
    link.write_text("#!/bin/sh\n# pip console script\n", encoding="utf-8")
    with pytest.raises(installer.InstallError, match="was not written by cgprofiled install"):
        host.install()
    assert link.read_text(encoding="utf-8").startswith("#!/bin/sh")
    assert not os.path.lexists(host.paths.opt) and not host.commands
    link.unlink()
    link.symlink_to("/usr/bin/true")
    with pytest.raises(installer.InstallError, match="was not written by cgprofiled install"):
        host.install()
    assert os.readlink(link) == "/usr/bin/true"


def test_a_foreign_end_user_client_entry_is_refused_too(host):
    link = Path(host.paths.user_link)
    link.parent.mkdir(parents=True)
    link.write_text("#!/bin/sh\n# somebody else's cgprofile\n", encoding="utf-8")
    with pytest.raises(installer.InstallError, match=f"{link} exists and was not written by cgprofiled install"):
        host.install()
    assert link.read_text(encoding="utf-8").startswith("#!/bin/sh")
    assert not os.path.lexists(host.paths.opt) and not host.commands


def test_our_own_client_link_is_replaced_on_upgrade(host):
    host.install()
    assert os.readlink(host.paths.client_link) == host.paths.client_target
    again = host.install()
    assert again["ok"]


# --- M7: an existing cgprofiled.toml is the operator's -------------------------------------------


def _write_config(host, text):
    Path(host.paths.config).parent.mkdir(parents=True, exist_ok=True)
    Path(host.paths.config).write_text(text, encoding="utf-8")


def test_existing_config_keeps_operator_keys_and_comments(host):
    text = ("# operator comment\nsessions_dir = \"/srv/sessions\"\nkeep_days = 3\n\n"
            "[sockets.control]\npath = \"/run/cgprofile/control/cgprofile.sock\"\n"
            "group = \"cgprofile-control\"\nmode = \"0660\"\n\n"
            "[sockets.observer]\npath = \"/run/cgprofile/observer/cgprofile.sock\"\n"
            "group = \"cgprofile-observer\"\nmode = \"0660\"\n")
    _write_config(host, text)
    body = host.install()
    assert body["config_changes"] == []
    assert Path(host.paths.config).read_text(encoding="utf-8") == text


def test_existing_config_merges_only_the_changed_group_and_flag_keys(host):
    _write_config(host, "keep_days = 3\n")
    host.group_gids["ops"] = 4200
    body = host.install(_args(control_group="ops", gates_slice="my.slice", observe_slices="a.slice",
                              allow_uids="1000"))
    assert body["config_changes"] == ["sockets.control.group", "allow_uids", "gates_slice", "observe_slices"]
    merged = daemon_config.parse_table(Path(host.paths.config).read_text(encoding="utf-8"))
    assert merged["keep_days"] == 3
    assert merged["sockets.control"]["group"] == "ops"
    assert merged["gates_slice"] == "my.slice" and merged["allow_uids"] == [1000]
    record = Path(host.paths.install_record).read_text(encoding="utf-8")
    assert 'gates_slice = "my.slice"' in record and "allow_uids = [1000]" in record


def test_existing_config_with_other_socket_path_or_invalid_text_is_refused(host):
    _write_config(host, '[sockets.control]\npath = "/run/elsewhere/control.sock"\n'
                        'group = "cgprofile-control"\nmode = "0660"\n')
    with pytest.raises(installer.InstallError, match="uses the fixed path"):
        host.install()
    _write_config(host, "bogus = 1\n")
    with pytest.raises(installer.InstallError, match="is invalid: .*fix or remove it"):
        host.install()
    assert not Path(host.paths.pending).exists()


def test_target_config_reports_unreadable_and_invalid_existing_files(host):
    values = installer._gather_inputs(_args(), _runtime(), host.paths)
    Path(host.paths.config).parent.mkdir(parents=True, exist_ok=True)
    Path(host.paths.config).write_bytes(b"\xff")
    with pytest.raises(installer.InstallError, match="cannot read .*cgprofiled.toml"):
        host.subject._target_config(host.paths.config, values)

    Path(host.paths.config).write_text("unknown = 1\n", encoding="utf-8")
    with pytest.raises(installer.InstallError, match="is invalid: .*fix or remove it"):
        host.subject._target_config(host.paths.config, values)


def test_unreadable_existing_config_is_refused(host):
    _write_config(host, "")
    Path(host.paths.config).write_bytes(b"\xff\xfe")
    with pytest.raises(installer.InstallError, match="cannot inspect existing config"):
        host.install()
    with pytest.raises(installer.InstallError, match="cannot inspect existing config .*cgprofiled.toml"):
        installer._gather_inputs(_args(), _runtime(), host.paths)


def test_rollback_render_drops_the_socket_tables(host):
    host.install()
    values = installer._gather_inputs(_args(version="2.1.0", allow_downgrade=True), _runtime(), host.paths)
    text, changes = host.subject._target_config(host.paths.config, values)
    assert "sockets" not in text and changes == ["sockets.* (removed for the older release)"]
    # a rollback onto a missing file renders without the tables from the start
    absent_text, absent_changes = host.subject._target_config(host.paths.config + ".none", values)
    assert "sockets" not in absent_text and absent_changes == []


def test_rendered_config_that_fails_validation_is_refused(host, monkeypatch):
    values = installer._gather_inputs(_args(), _runtime(), host.paths)

    def refuse(_text):
        raise daemon_config.ConfigError("boom")

    monkeypatch.setattr(daemon_config, "parse_table", refuse)
    with pytest.raises(installer.InstallError, match="rendered config refused: boom"):
        host.subject._target_config(host.paths.config + ".none", values)


def test_non_default_config_flag_is_not_supported(host):
    with pytest.raises(installer.InstallError, match="manage .* only; --config /tmp/x.toml is not supported"):
        host.install(_args(config="/tmp/x.toml"))
    with pytest.raises(installer.InstallError, match="--config /tmp/x.toml is not supported"):
        host.subject.uninstall(_args(config="/tmp/x.toml"), _runtime(yes=True))
    host.install(_args(config=host.paths.config))


# --- PENDING lifecycle ----------------------------------------------------------------------


def test_pending_is_written_first_and_removed_last(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    seen = {}
    original = installer.Installer._ensure_version

    def spy(self, values):
        seen["pending_during_build"] = Path(self.paths.pending).read_text(encoding="utf-8")
        return original(self, values)

    monkeypatch.setattr(installer.Installer, "_ensure_version", spy)
    host.install()
    assert seen["pending_during_build"] == "2.2.0\n"
    assert not Path(host.paths.pending).exists()
    assert Path(host.paths.install_record).is_file()


def test_failed_fetch_or_pin_check_drops_pending_and_leaves_no_version_tree(host):
    bad = _args(wheel=f"2.2.0@sha256:{'0' * 64}")
    with pytest.raises(installer.InstallError, match="does not match the pin"):
        host.install(bad)
    assert not Path(host.paths.pending).exists()
    assert not os.path.lexists(Path(host.paths.opt) / "2.2.0")
    assert not Path(host.paths.install_record).exists()
    bad = _args(cli_extended_wheel=f"0.5.3@sha256:{'0' * 64}")
    with pytest.raises(installer.InstallError, match="does not match the pin"):
        host.install(bad)
    bad = _args(version="3.0.0", augeaswrap_wheel=f"0.1.0@sha256:{'0' * 64}")
    with pytest.raises(installer.InstallError, match="sha256 of augeaswrap-0.1.0.*does not match the pin"):
        host.install(bad)
    assert not Path(host.paths.pending).exists()
    assert not os.path.lexists(Path(host.paths.opt) / "3.0.0")
    assert not Path(host.paths.install_record).exists()
    # the failure left nothing that blocks the next run
    assert host.install()["ok"]


def test_config_check_failure_drops_pending_before_any_host_change(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    original = host.subject.run

    def run(argv, **kwargs):
        if list(argv)[-2:] == ["config", "check"]:
            return subprocess.CompletedProcess(argv, 2, "", "bad config")
        return original(argv, **kwargs)

    host.subject.run = run
    with pytest.raises(installer.InstallError, match="config check refused"):
        host.install()
    assert not Path(host.paths.pending).exists()
    assert not Path(host.paths.config).exists() and not Path(host.paths.unit_dir).exists()


def test_unit_print_failure_is_a_refusal(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    original = host.subject.run

    def run(argv, **kwargs):
        if "unit" in argv and "print" in argv:
            return subprocess.CompletedProcess(argv, 2, "", "no template")
        return original(argv, **kwargs)

    host.subject.run = run
    with pytest.raises(installer.InstallError, match="unit print refused"):
        host.install()


def test_version_build_failures_are_bounded_and_clean_up(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch, fail={("python3", "-m", "venv"): 1})
    with pytest.raises(installer.InstallError, match="command failed") as caught:
        host.install()
    assert "apt install python3-venv" in str(caught.value) and "-m venv" in str(caught.value)
    assert not os.path.lexists(Path(host.paths.opt) / "2.2.0") and not Path(host.paths.pending).exists()

    original = _fake_run(host.commands, host.group_gids, host.active)

    def wrong_version(argv, **kwargs):
        if list(argv)[-1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, "cgprofiled 9.9.9\n", "")
        return original(argv, **kwargs)

    host.subject.run = wrong_version
    with pytest.raises(installer.InstallError, match="expected 2.2.0"):
        host.install()
    assert not os.path.lexists(Path(host.paths.opt) / "2.2.0")


def test_unmarked_version_directory_is_refused_not_built_over(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    (Path(host.paths.opt) / "2.2.0").mkdir(parents=True)
    with pytest.raises(installer.InstallError, match="unmarked version tree"):
        host.install()
    # the defensive guard in the builder itself
    values = installer._gather_inputs(_args(), _runtime(), host.paths)
    with pytest.raises(installer.InstallError, match="unmarked version directory"):
        host.subject._ensure_version(values)


def test_immutable_version_without_its_venv_is_refused(host):
    host.install()
    cgprofiled = Path(host.paths.opt) / "2.2.0" / "venv" / "bin" / "cgprofiled"
    cgprofiled.unlink()
    with pytest.raises(installer.InstallError, match="/cgprofiled is missing"):
        host.install()


def test_drain_timeout_keeps_pending_and_a_rerun_reapplies(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    host.install()
    host.active["cgprofiled.service"] = True
    ticks = iter(range(0, 10**6, 700))
    host.subject.monotonic = lambda: float(next(ticks))
    host.subject.sleep = lambda _seconds: None
    host.subject._live_sessions = lambda _current: [{"session": "s1"}]
    newer = _args(version="2.3.0", drain_timeout="1s")
    # version 2.3.0 has its own pins; reuse the fixture bytes
    with pytest.raises(installer.DrainTimeout):
        host.install(newer)
    assert Path(host.paths.pending).read_text(encoding="utf-8") == "2.3.0\n"
    assert os.readlink(Path(host.paths.opt) / "current") == "2.2.0"
    # the record exists, so the rerun is a re-apply, and the drain is asked for again
    host.subject._live_sessions = lambda _current: []
    host.commands.clear()
    body = host.install(newer)
    assert body["ok"]
    assert not Path(host.paths.pending).exists()
    assert os.readlink(Path(host.paths.opt) / "current") == "2.3.0"
    assert host.has("systemctl", "daemon-reload")


def test_failed_apply_leaves_pending_and_a_rerun_reapplies(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    host.install()
    original = host.subject.run
    broken = {"on": True}

    def run(argv, **kwargs):
        if broken["on"] and list(argv)[:2] == ["systemctl", "daemon-reload"]:
            return subprocess.CompletedProcess(argv, 1, "", "reload failed")
        return original(argv, **kwargs)

    host.subject.run = run
    with pytest.raises(installer.InstallError, match="daemon-reload"):
        host.install(_args(version="2.3.0"))
    assert Path(host.paths.pending).is_file()
    broken["on"] = False
    host.commands.clear()
    body = host.install(_args(version="2.3.0"))
    assert body["ok"] and not Path(host.paths.pending).exists()
    assert host.has("systemctl", "daemon-reload")


def test_pending_records_new_groups_before_systemd_sysusers_is_invoked(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    original = host.subject.run
    sysusers_calls = 0

    def run(argv, **kwargs):
        nonlocal sysusers_calls
        if list(argv)[0] == "systemd-sysusers":
            pending = Path(host.paths.pending)
            assert pending.is_file()
            assert "created_groups=cgprofile-control,cgprofile-observer\n" in pending.read_text(
                encoding="utf-8"
            )
            sysusers_calls += 1
        return original(argv, **kwargs)

    host.subject.run = run
    host.install()

    assert sysusers_calls == 1


def test_a_first_install_that_failed_after_pending_needs_uninstall_not_a_blind_rerun(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch, fail={("systemd-sysusers",): 1})
    with pytest.raises(installer.InstallError, match="systemd-sysusers"):
        host.install()
    assert Path(host.paths.pending).is_file() and not Path(host.paths.install_record).exists()
    with pytest.raises(installer.InstallError, match="leftover install marker") as caught:
        host.install()
    assert "cgprofiled uninstall --yes" in str(caught.value)
    # ... and uninstall accepts the PENDING-marked host
    body = host.subject.uninstall(_args(), _runtime(yes=True))
    assert body["ok"] and not os.path.lexists(host.paths.opt)


def test_missing_sysusers_group_after_creation_is_an_error(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    original = host.subject.run

    def run(argv, **kwargs):
        if list(argv)[0] == "systemd-sysusers":
            return subprocess.CompletedProcess(argv, 0, "", "")
        return original(argv, **kwargs)

    host.subject.run = run
    with pytest.raises(installer.InstallError, match="did not create configured group"):
        host.install()


# --- install rollbacks, drift and restarts --------------------------------------------------


def test_downgrade_install_removes_socket_units_and_renders_without_sockets(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    host.install()
    monkeypatch.setattr(installer, "has_socket_units", lambda version: version != "2.1.0"
                        and installer.version_key(version) >= installer.version_key("2.2.0"))
    original = host.subject.run

    def run(argv, **kwargs):
        if list(argv)[-1:] == ["--version"]:
            return subprocess.CompletedProcess(argv, 0, "cgprofiled 2.1.0\n", "")
        if "unit" in argv and "print" in argv:
            unit = argv[argv.index("--unit") + 1]
            return subprocess.CompletedProcess(argv, 0, f"# {unit}\n", "")
        return original(argv, **kwargs)

    host.subject.run = run
    body = host.install(_args(version="2.1.0", allow_downgrade=True))
    assert body["downgrade"] is True
    assert not Path(host.subject._resource_paths()["cgprofile-control.socket"]).exists()
    assert "sockets" not in Path(host.paths.config).read_text(encoding="utf-8")


def test_active_service_is_reloaded_for_config_drift_and_restarted_for_unit_drift(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    host.install()
    host.active.update({"cgprofiled.service": True, "cgprofile-control.socket": True,
                        "cgprofile-observer.socket": True})
    host.commands.clear()
    # same version, only a reloadable config key changes
    body = host.install(_args(allow_uids="1000"))
    assert body["config_drift"] is True
    assert ["systemctl", "reload", "cgprofiled.service"] in host.commands
    assert not host.has("systemctl", "restart")
    # a unit text drift restarts the service
    unit = Path(host.subject._resource_paths()["cgprofiled.service"])
    unit.write_text("drifted\n", encoding="utf-8")
    host.commands.clear()
    host.install(_args(allow_uids="1000"))
    assert ["systemctl", "restart", "cgprofiled.service"] in host.commands


def test_forced_restart_records_the_live_sessions(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    host.install()
    host.active["cgprofiled.service"] = True
    host.subject._live_sessions = lambda _current: [{"session": "s-live"}]
    body = host.install(_args(version="2.3.0", force_restart=True))
    assert body["forced_sessions"] == ["s-live"]


def test_enable_decision_comes_from_the_record_and_no_enable_skips_start(host):
    body = host.install(_args(enable=False))
    assert body["enabled"] is False
    assert not host.has("systemctl", "start") and not host.has("systemctl", "enable")
    values = installer._gather_inputs(_args(enable=False, no_enable=False), _runtime(), host.paths)
    assert values["enable_now"] is False


def test_fixed_config_change_detection(host):
    host.install()
    path = host.paths.config
    unchanged = Path(path).read_text(encoding="utf-8")
    assert host.subject._fixed_config_changed(path, {}, unchanged) is False
    changed = unchanged.replace('group = "cgprofile-observer"', 'group = "other"')
    assert host.subject._fixed_config_changed(path, {}, changed) is True
    assert host.subject._fixed_config_changed(path, {}, "bogus = 1\n") is True


# --- uninstall: ownership, order, re-runnability --------------------------------------------


def _snapshot(root: Path):
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


def test_uninstall_on_an_empty_host_reports_nothing_installed(host):
    body = host.subject.uninstall(_args(), _runtime(yes=True))
    assert body["message"] == "nothing installed by cgprofiled install" and body["removed"] == []
    assert not host.commands
    assert installer._uninstall_summary(body) == "nothing installed by cgprofiled install"


def test_uninstall_refuses_an_old_host_setup_install_before_any_change(host, tmp_path):
    paths = host.paths
    tmpfiles = Path(paths.at("/etc/tmpfiles.d/mdt-cgprofile.conf"))
    tmpfiles.parent.mkdir(parents=True)
    tmpfiles.write_text("d /run/cgprofile 0770 root docker -\n", encoding="utf-8")
    version = Path(paths.opt) / "2.1.0"
    version.mkdir(parents=True)
    (version / "MANIFEST").write_text("installer=mdt host-setup (MDT-055)\n", encoding="utf-8")
    (Path(paths.opt) / "current").symlink_to("2.1.0")
    unit = Path(paths.unit_dir) / "cgprofiled.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("old\n", encoding="utf-8")
    Path(paths.config).parent.mkdir(parents=True)
    Path(paths.config).write_text("", encoding="utf-8")
    Path(paths.state_data).mkdir(parents=True)
    before = _snapshot(tmp_path)
    with pytest.raises(installer.InstallError, match="cgprofiled uninstall refused") as caught:
        host.subject.uninstall(_args(purge_data=True), _runtime(yes=True))
    assert "mdt-cgprofile.conf" in str(caught.value)
    assert _snapshot(tmp_path) == before and not host.commands


def test_uninstall_refuses_an_unowned_old_layout_path_even_with_a_valid_config(host, tmp_path):
    Path(host.paths.config).parent.mkdir(parents=True)
    Path(host.paths.config).write_text("", encoding="utf-8")
    old_socket = Path(host.paths.at("/run/cgprofile/ctl.sock"))
    old_socket.parent.mkdir(parents=True)
    old_socket.write_text("", encoding="utf-8")
    before = _snapshot(tmp_path)
    with pytest.raises(installer.InstallError, match="old-layout socket path remains"):
        host.subject.uninstall(_args(), _runtime(yes=True))
    assert _snapshot(tmp_path) == before


def test_uninstall_refuses_unknown_content_even_on_an_owned_host(host):
    host.seeded_install()
    host.runtime_tree()
    cases = (
        (Path(host.paths.runtime_root) / "stray", "unknown path under the runtime tree"),
        (Path(host.paths.control_dir) / "other.sock", "unknown path under the runtime tree"),
        (Path(host.paths.opt) / "foreign-dir", "unknown path under the version root"),
    )
    for path, match in cases:
        path.mkdir() if path.name.endswith("dir") else path.write_text("x\n", encoding="utf-8")
        with pytest.raises(installer.InstallError, match=match):
            host.subject.uninstall(_args(), _runtime(yes=True))
        assert not host.commands
        if path.is_dir():
            path.rmdir()
        else:
            path.unlink()
    node = Path(host.paths.at(daemon_config.DEFAULT_CONTROL_SOCKET))
    node.write_text("regular\n", encoding="utf-8")
    with pytest.raises(installer.InstallError, match="is not a socket"):
        host.subject.uninstall(_args(), _runtime(yes=True))


def test_uninstall_needs_root_and_confirmation(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    host.seeded_install()
    nonroot = installer.Installer(host.paths, run=host.subject.run, geteuid=lambda: 1000)
    with pytest.raises(installer.InstallError, match="needs root"):
        nonroot.uninstall(_args(), _runtime(yes=True))
    # a dry run needs no root and changes nothing
    body = nonroot.uninstall(_args(remove_groups=True), _runtime(dry_run=True))
    assert body["dry_run"] is True and host.paths.opt in body["removed"]
    assert Path(host.paths.opt).exists()
    runtime = _runtime(confirmed=False)
    with pytest.raises(installer.InstallError, match="was not confirmed"):
        host.subject.uninstall(_args(), runtime)
    assert runtime.asked and Path(host.paths.opt).exists() and not host.commands


def test_uninstall_removes_everything_it_wrote_and_stops_the_slice(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    Path(host.paths.state_data).mkdir(parents=True)
    (Path(host.paths.state_data) / "keep").write_text("data\n", encoding="utf-8")
    host.seeded_install()
    for directory in (host.paths.control_dir, host.paths.observer_dir, host.paths.state_dir):
        Path(directory).mkdir(parents=True, exist_ok=True)
    Path(host.paths.control_dir, "cgprofile.sock").write_text("", encoding="utf-8")
    # replace the placeholder file by a real socket node
    import socket as socket_module

    for sock in (host.paths.at(daemon_config.DEFAULT_CONTROL_SOCKET),
                 host.paths.at(daemon_config.DEFAULT_OBSERVER_SOCKET)):
        if os.path.lexists(sock):
            os.unlink(sock)
        listener = socket_module.socket(socket_module.AF_UNIX)
        listener.bind(sock)
        listener.close()
    (Path(host.paths.state_dir) / "damon.lock").write_text("", encoding="utf-8")
    for path in (host.paths.runtime_root, host.paths.control_dir, host.paths.observer_dir):
        os.chmod(path, 0o755)
    os.chmod(host.paths.state_dir, 0o700)
    first_systemctl_saw_fragment = []
    original_run = host.subject.run

    def inspect_first_systemctl(argv, **kwargs):
        if argv and argv[0] == "systemctl" and not first_systemctl_saw_fragment:
            first_systemctl_saw_fragment.append(os.path.lexists(host.paths.endpoint_fragment))
        return original_run(argv, **kwargs)

    host.subject.run = inspect_first_systemctl
    body = host.subject.uninstall(_args(), _runtime(yes=True))
    assert body["ok"] and body["data_purged"] is False and body["groups_removed"] == []
    assert first_systemctl_saw_fragment == [False]
    for path in (host.paths.opt, host.paths.config, host.paths.install_record, host.paths.runtime_root,
                 host.paths.unit_dir + "/cgprofiled.service", host.paths.unit_dir + "/cgprofile.slice",
                 host.paths.unit_dir + "/cgprofile-control.socket", host.paths.client_link,
                 host.paths.user_link, host.paths.sysusers_dir + "/cgprofile.conf"):
        assert not os.path.lexists(path), path
    assert not os.path.lexists(os.path.dirname(host.paths.config))
    assert not os.path.lexists(host.paths.endpoint_fragment)
    assert "CGPROFILE_CONTROL_SOCKET" not in Path(host.paths.at("/etc/host-endpoints.env")).read_text()
    assert Path(host.paths.state_data, "keep").is_file()
    assert host.has("systemctl", "disable", "--now", "cgprofile-control.socket", "cgprofile-observer.socket",
                    "cgprofiled.service")
    assert ["systemctl", "stop", "cgprofile.slice"] in host.commands
    assert host.commands.index(["systemctl", "stop", "cgprofile.slice"]) > next(
        index for index, command in enumerate(host.commands) if command[:3] == ["systemctl", "disable", "--now"])
    assert not host.has("groupdel")
    summary = installer._uninstall_summary(body)
    assert "removed: " + host.paths.opt in summary


@pytest.mark.parametrize(
    "failure,expected",
    [
        (installer.InstallError("withdraw refused"), "withdraw refused"),
        (ValueError("withdraw failed"), "cannot withdraw cgprofile host endpoint record: withdraw failed"),
    ],
)
def test_uninstall_reports_endpoint_withdraw_failures_before_stopping(tmp_path, monkeypatch, failure, expected):
    host = Host(tmp_path, monkeypatch)
    host.seeded_install()

    def withdraw(_owner, *, root):
        assert root == host.paths.root
        raise failure

    host.subject._endpoints_api = lambda: SimpleNamespace(withdraw=withdraw)
    with pytest.raises(installer.InstallError, match=expected):
        host.subject.uninstall(_args(), _runtime(yes=True))
    assert not host.commands


def test_uninstall_reloads_systemd_right_after_the_unit_files_and_before_groups_and_markers(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    host.seeded_install()
    host.group_gids.update({"cgprofile-control": 7101, "cgprofile-observer": 7102})
    log = []
    original = host.subject.run
    paths = host.paths

    def run(argv, **kwargs):
        log.append((list(argv), {
            "record": os.path.lexists(paths.install_record),
            "pending_or_opt": os.path.lexists(paths.opt),
            "service_unit": os.path.lexists(paths.unit_dir + "/cgprofiled.service"),
        }))
        return original(argv, **kwargs)

    host.subject.run = run
    host.subject.uninstall(_args(remove_groups=True), _runtime(yes=True))
    reload_at = next(i for i, (argv, _s) in enumerate(log) if argv[:2] == ["systemctl", "daemon-reload"])
    reset_at = next(i for i, (argv, _s) in enumerate(log) if argv[:2] == ["systemctl", "reset-failed"])
    groupdel_at = next(i for i, (argv, _s) in enumerate(log) if argv[0] == "groupdel")
    assert reload_at < reset_at < groupdel_at
    assert log[reload_at][1]["service_unit"] is False
    # at groupdel time the ownership markers (and so the group list) still exist
    assert log[groupdel_at][1]["record"] and log[groupdel_at][1]["pending_or_opt"]
    assert not os.path.lexists(paths.install_record) and not os.path.lexists(paths.opt)


def test_interrupted_uninstall_can_be_rerun_and_still_removes_the_groups(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    host.install()
    host.commands.clear()
    original = host.subject.run
    host.group_gids.update({"cgprofile-control": 7101, "cgprofile-observer": 7102})
    state = {"fail": True}

    def run(argv, **kwargs):
        if state["fail"] and list(argv)[:2] == ["groupdel", "cgprofile-observer"]:
            return subprocess.CompletedProcess(argv, 8, "", "group is a primary group")
        return original(argv, **kwargs)

    host.subject.run = run
    with pytest.raises(installer.InstallError, match="could not remove group.*cgprofile-observer"):
        host.subject.uninstall(_args(remove_groups=True), _runtime(yes=True))
    # the first group is gone, the record and the opt tree remain for the rerun
    assert "cgprofile-control" not in host.group_gids and "cgprofile-observer" in host.group_gids
    assert Path(host.paths.install_record).is_file() and os.path.lexists(host.paths.opt)
    state["fail"] = False
    host.commands.clear()
    body = host.subject.uninstall(_args(remove_groups=True), _runtime(yes=True))
    # the group that no longer resolves is skipped, the other one is removed
    assert body["groups_removed"] == ["cgprofile-observer"]
    assert ["groupdel", "cgprofile-control"] not in host.commands
    assert not os.path.lexists(host.paths.install_record) and not os.path.lexists(host.paths.opt)


def test_uninstall_purges_data_only_when_asked_and_reports_foreign_client_link_kept(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    host.seeded_install()
    Path(host.paths.state_data).mkdir(parents=True)
    (Path(host.paths.state_data) / "sessions").write_text("x\n", encoding="utf-8")
    link = Path(host.paths.client_link)
    link.unlink()
    link.write_text("#!/bin/sh\n", encoding="utf-8")
    user_link = Path(host.paths.user_link)
    user_link.unlink()
    user_link.write_text("#!/bin/sh\n", encoding="utf-8")
    body = host.subject.uninstall(_args(purge_data=True), _runtime(yes=True))
    assert body["data_purged"] is True and not Path(host.paths.state_data).exists()
    assert body["kept"] == [host.paths.client_link, host.paths.user_link]
    assert link.is_file() and user_link.is_file()
    assert "kept (not written by cgprofiled install)" in installer._uninstall_summary(body)


def test_uninstall_stops_when_systemd_disable_fails_and_keeps_every_file(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch, fail={("systemctl", "disable"): 1})
    host.seeded_install()
    with pytest.raises(installer.InstallError, match="command failed.*disable"):
        host.subject.uninstall(_args(), _runtime(yes=True))
    assert Path(host.paths.unit_dir, "cgprofiled.service").is_file()
    assert Path(host.paths.install_record).is_file()


def test_uninstall_removes_a_leftover_mdt_dropin_and_keeps_a_foreign_one_refused(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    host.seeded_install()
    dropin = Path(host.paths.service_dropins) / "50-mdt-socket-group.conf"
    dropin.parent.mkdir(parents=True)
    dropin.write_text("[Service]\nGroup=docker\n", encoding="utf-8")
    dry = host.subject.uninstall(_args(), _runtime(dry_run=True))
    assert str(dropin) in dry["removed"]
    foreign = Path(host.paths.service_dropins) / "10-site.conf"
    foreign.write_text("[Service]\nGroup=staff\n", encoding="utf-8")
    with pytest.raises(installer.InstallError, match="10-site.conf"):
        host.subject.uninstall(_args(), _runtime(yes=True))
    assert dropin.is_file() and not host.commands
    foreign.unlink()
    host.subject.uninstall(_args(), _runtime(yes=True))
    assert not dropin.exists() and not Path(host.paths.service_dropins).exists()


def test_uninstall_reports_a_runtime_directory_it_cannot_remove(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    host.seeded_install()
    Path(host.paths.state_dir).mkdir(parents=True)
    os.chmod(host.paths.state_dir, 0o700)
    os.chmod(host.paths.runtime_root, 0o755)
    real_rmdir = os.rmdir

    def rmdir(path, *args, **kwargs):
        if str(path) == host.paths.runtime_root:
            raise OSError(16, "Device or resource busy", str(path))
        return real_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(installer.os, "rmdir", rmdir)
    with pytest.raises(installer.InstallError, match="cannot remove runtime directory"):
        host.subject.uninstall(_args(), _runtime(yes=True))
    assert Path(host.paths.install_record).is_file()


def test_uninstall_reports_a_data_purge_and_version_root_it_cannot_remove(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    host.seeded_install()
    Path(host.paths.state_data).mkdir(parents=True)
    real_rmtree = installer.shutil.rmtree

    def failing(path, *args, **kwargs):
        if str(path) == host.paths.state_data:
            raise OSError(13, "Permission denied", str(path))
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(installer.shutil, "rmtree", failing)
    with pytest.raises(installer.InstallError, match="cannot purge"):
        host.subject.uninstall(_args(purge_data=True), _runtime(yes=True))
    assert Path(host.paths.install_record).is_file()

    def failing_opt(path, *args, **kwargs):
        if str(path).startswith(host.paths.opt):
            raise OSError(13, "Permission denied", str(path))
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(installer.shutil, "rmtree", failing_opt)
    with pytest.raises(installer.InstallError, match="cannot remove"):
        host.subject.uninstall(_args(), _runtime(yes=True))
    assert Path(host.paths.install_record).is_file()


def test_marker_removal_failures_are_reported(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    host.seeded_install()
    real_rmdir = os.rmdir

    def rmdir(path, *args, **kwargs):
        if str(path) == host.paths.opt:
            raise OSError(39, "Directory not empty", str(path))
        return real_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(installer.os, "rmdir", rmdir)
    with pytest.raises(installer.InstallError, match="cannot remove"):
        host.subject.uninstall(_args(), _runtime(yes=True))
    assert Path(host.paths.install_record).is_file()


def test_etc_directory_with_foreign_files_is_kept_and_reported(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    host.seeded_install()
    Path(host.paths.config).parent.joinpath("other.conf").write_text("mine\n", encoding="utf-8")
    body = host.subject.uninstall(_args(), _runtime(yes=True))
    assert os.path.dirname(host.paths.install_record) in body["kept"]
    assert not os.path.lexists(host.paths.install_record)


# --- M6: the tree the process runs from is removed last, after the result is printed ---------


def test_uninstall_defers_the_running_version_tree_and_the_markers(tmp_path, monkeypatch):
    first = Host(tmp_path, monkeypatch)
    first.seeded_install()
    running = Path(first.paths.opt) / "2.2.0" / "venv"
    host_subject = installer.Installer(
        first.paths, run=first.subject.run, geteuid=lambda: 0, running_prefix=str(running))
    body = host_subject.uninstall(_args(), _runtime(yes=True))
    assert body["deferred"] == os.path.realpath(Path(first.paths.opt) / "2.2.0")
    assert (Path(first.paths.opt) / "2.2.0" / "venv").is_dir()
    assert os.path.islink(Path(first.paths.opt) / "current")  # kept until finish_uninstall
    # the ownership markers stay until the deferred tree is gone, so a kill here is rerunnable
    assert Path(first.paths.install_record).is_file()
    host_subject.finish_uninstall(body)
    assert not os.path.lexists(first.paths.opt) and not os.path.lexists(first.paths.install_record)
    assert "deferred" not in body
    host_subject.finish_uninstall(body)  # nothing deferred: a no-op


def test_interrupted_deferred_uninstall_is_rerunnable_from_a_copy(tmp_path, monkeypatch):
    first = Host(tmp_path, monkeypatch)
    first.seeded_install()
    running = Path(first.paths.opt) / "2.2.0" / "venv"
    host_subject = installer.Installer(
        first.paths, run=first.subject.run, geteuid=lambda: 0, running_prefix=str(running))
    host_subject.uninstall(_args(), _runtime(yes=True))
    # the process died before finish_uninstall: install.toml makes the host owned, so a rerun cleans up
    body = first.subject.uninstall(_args(), _runtime(yes=True))
    assert body["ok"] and not os.path.lexists(first.paths.opt) and not os.path.lexists(first.paths.install_record)


def test_running_version_dir_is_none_outside_the_opt_tree(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    assert host.subject._running_version_dir() is None


def test_finish_uninstall_reports_a_tree_it_cannot_remove(tmp_path, monkeypatch):
    first = Host(tmp_path, monkeypatch)
    first.seeded_install()
    running = Path(first.paths.opt) / "2.2.0" / "venv"
    subject = installer.Installer(first.paths, run=first.subject.run, geteuid=lambda: 0,
                                  running_prefix=str(running))
    body = subject.uninstall(_args(), _runtime(yes=True))
    real_rmtree = installer.shutil.rmtree

    def failing(path, *args, **kwargs):
        raise OSError(13, "Permission denied", str(path))

    monkeypatch.setattr(installer.shutil, "rmtree", failing)
    with pytest.raises(installer.InstallError, match="cannot remove"):
        subject.finish_uninstall(body)
    monkeypatch.setattr(installer.shutil, "rmtree", real_rmtree)


# --- handlers -------------------------------------------------------------------------------


class _Output:
    is_interactive = False

    def __init__(self):
        self.printed = []

    def primary(self, value):
        self.printed.append(value)


def _handler_runtime(output, **overrides):
    runtime = _runtime(**overrides)
    runtime.output = output
    return runtime


def test_handlers_print_before_the_deferred_removal_and_map_failures(tmp_path, monkeypatch):
    first = Host(tmp_path, monkeypatch)
    first.seeded_install()
    running = Path(first.paths.opt) / "2.2.0" / "venv"
    order = []
    subject = installer.Installer(first.paths, run=first.subject.run, geteuid=lambda: 0,
                                  running_prefix=str(running))
    real_finish = subject.finish_uninstall
    subject.finish_uninstall = lambda body: (order.append("finish"), real_finish(body))
    monkeypatch.setattr(installer, "Installer", lambda: subject)

    class Out(_Output):
        def primary(self, value):
            order.append("print")
            super().primary(value)

    output = Out()
    assert installer._uninstall_handler(_args(), _handler_runtime(output, yes=True)) == 0
    assert order == ["print", "finish"] and "deferred" not in str(output.printed[0])
    assert not os.path.lexists(first.paths.opt)

    json_out = Out()
    assert installer._uninstall_handler(_args(), _handler_runtime(json_out, yes=True, json_mode=True)) == 0
    assert json_out.printed[0]["message"] == "nothing installed by cgprofiled install"


def test_handlers_turn_install_errors_into_cli_failures(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    real_installer = installer.Installer
    monkeypatch.setattr(installer, "Installer", lambda: host.subject)
    out = _Output()
    with pytest.raises(installer.CliFailure) as caught:
        installer._install_handler(_args(wheel=None), _handler_runtime(out))
    assert caught.value.exit_code == 2
    assert installer._install_handler(_args(), _handler_runtime(out, yes=True)) == 0
    assert "installed" in out.printed[-1]
    dry = _Output()
    assert installer._install_handler(_args(), _handler_runtime(dry, dry_run=True)) == 0
    assert dry.printed[0].startswith("dry run:")
    as_json = _Output()
    assert installer._install_handler(_args(), _handler_runtime(as_json, yes=True, json_mode=True)) == 0
    assert as_json.printed[0]["ok"] is True

    first = _Output()
    with pytest.raises(installer.CliFailure):
        installer._uninstall_handler(_args(config="/nope"), _handler_runtime(first, yes=True))

    # A deferred uninstall removes the interpreter that ran cgprofiled; the final
    # failure must give manual cleanup steps rather than invoking that binary again.
    running = Path(host.paths.opt) / "2.2.0" / "venv"
    subject = real_installer(host.paths, run=host.subject.run, geteuid=lambda: 0,
                             running_prefix=str(running))
    monkeypatch.setattr(installer, "Installer", lambda: subject)
    monkeypatch.setattr(subject, "finish_uninstall",
                        lambda body: (_ for _ in ()).throw(installer.InstallError("cannot remove /opt/x")))
    with pytest.raises(installer.CliFailure, match="manual cleanup") as caught:
        installer._uninstall_handler(_args(remove_groups=True), _handler_runtime(_Output(), yes=True))
    assert "groupdel" in str(caught.value) and host.paths.install_record in str(caught.value)
    assert "rerun cgprofiled uninstall" not in str(caught.value)

    nondeferred_paths = installer.InstallPaths(str(tmp_path / "nondeferred"))

    def fail_after_non_deferred_cleanup(_body):
        raise installer.InstallError("cannot finish ownership-marker cleanup")

    nondeferred = SimpleNamespace(
        paths=nondeferred_paths,
        uninstall=lambda *_args: {"ok": True, "dry_run": False, "removed": [], "manual_groups": []},
        finish_uninstall=fail_after_non_deferred_cleanup,
    )
    monkeypatch.setattr(installer, "Installer", lambda: nondeferred)
    with pytest.raises(installer.CliFailure, match="manual cleanup") as caught:
        installer._uninstall_handler(_args(), _handler_runtime(_Output(), yes=True))
    assert nondeferred_paths.install_record in str(caught.value)
    assert "remaining version tree" not in str(caught.value) and "groupdel" not in str(caught.value)


def test_uninstall_never_runs_user_membership_removal_commands(host):
    host.seeded_install()
    host.commands.clear()
    host.subject.uninstall(_args(), _runtime(yes=True))
    assert not any(command[:2] == ["gpasswd", "-d"] for command in host.commands)
    assert not any(command and command[0] == "usermod" for command in host.commands)


def test_summaries_describe_dry_runs_and_real_runs():
    assert "would be installed" in installer._install_summary({"dry_run": True, "version": "2.2.0"})
    assert "would add user alice to cgprofile-control" in installer._install_summary({
        "dry_run": True, "version": "2.2.0",
        "users_to_add": {"control": {"group": "cgprofile-control", "users": ["alice"]}},
    })
    assert "no resource drift" in installer._install_summary(
        {"dry_run": False, "version": "2.2.0", "resources_changed": [], "enabled": True})
    assert installer._uninstall_summary({"dry_run": True, "removed": ["/a"], "groups_removed": ["g"]}) \
        == "would remove: /a\nwould remove group: g"
    assert installer._uninstall_summary({"dry_run": False}) == "nothing to remove"


# --- round 2 ------------------------------------------------------------------------------


def test_force_restart_proceeds_with_a_warning_when_the_session_list_is_unavailable(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    host.install()
    host.active["cgprofiled.service"] = True

    def wedged(_current):
        raise installer.InstallError("cannot list live cgprofile sessions; refusing to restart")

    host.subject._live_sessions = wedged
    with pytest.raises(installer.InstallError, match="cannot list live cgprofile sessions"):
        host.install(_args(version="2.3.0"))
    assert not Path(host.paths.pending).exists() or Path(host.paths.install_record).exists()
    body = host.install(_args(version="2.3.0", force_restart=True))
    assert body["ok"] and body["forced_sessions"] == ["unknown (the session list was unavailable)"]
    assert "cannot list live cgprofile sessions" in body["warnings"][0]
    assert "WARNING" in installer._install_summary(body)
    assert os.readlink(host.paths.current) == "2.3.0"


def test_force_restart_without_a_current_pointer_also_proceeds(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch, active={"cgprofiled.service": True})
    body = host.install(_args(force_restart=True))
    assert body["forced_sessions"] == ["unknown (the session list was unavailable)"]
    assert "current is missing" in body["warnings"][0]
    with pytest.raises(installer.InstallError, match="current is missing"):
        Host(tmp_path / "other", monkeypatch, active={"cgprofiled.service": True}).install()


def test_groups_created_by_an_interrupted_first_install_are_recorded_and_removable(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)

    def timeout(self, _venv, _version):
        raise installer.InstallError("daemon did not report version 2.2.0")

    monkeypatch.setattr(installer.Installer, "_wait_ready", timeout)
    with pytest.raises(installer.InstallError, match="did not report"):
        host.install()
    assert not Path(host.paths.install_record).exists()
    pending = Path(host.paths.pending).read_text(encoding="utf-8")
    assert pending.splitlines() == ["2.2.0", "created_groups=cgprofile-control,cgprofile-observer"]
    assert set(host.group_gids) == {"cgprofile-control", "cgprofile-observer"}
    body = host.subject.uninstall(_args(remove_groups=True), _runtime(yes=True))
    assert body["groups_removed"] == ["cgprofile-control", "cgprofile-observer"]
    assert host.group_gids == {} and not os.path.lexists(host.paths.opt)


def test_pending_group_lines_survive_rewrites_and_feed_the_record_on_a_reapply(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    host.install()
    Path(host.paths.pending).write_text(
        "2.2.0\ncreated_groups=cgprofile-observer,evil-group\nunrelated=1\n", encoding="utf-8")
    assert installer._pending_created_groups(host.paths) == ["cgprofile-observer"]
    installer._write_pending(host.paths, "2.3.0", ["cgprofile-control"])
    assert Path(host.paths.pending).read_text(encoding="utf-8").splitlines() == [
        "2.3.0", "created_groups=cgprofile-observer,cgprofile-control"]
    Path(host.paths.pending).write_text("2.2.0\ncreated_groups=cgprofile-observer\n", encoding="utf-8")
    Path(host.paths.install_record).write_text("created_groups = []\n", encoding="utf-8")
    body = host.install()
    assert body["ok"] and not Path(host.paths.pending).exists()
    assert 'created_groups = ["cgprofile-observer"]' in Path(host.paths.install_record).read_text(encoding="utf-8")
    Path(host.paths.pending).write_bytes(b"\xff\xfe")
    assert installer._pending_created_groups(host.paths) == []
    Path(host.paths.pending).unlink()
    assert installer._pending_created_groups(host.paths) == []


def test_created_groups_in_a_record_are_limited_to_the_two_default_groups(host):
    Path(host.paths.install_record).parent.mkdir(parents=True)
    Path(host.paths.install_record).write_text('created_groups = ["root"]\n', encoding="utf-8")
    with pytest.raises(installer.InstallError, match="invalid created_groups"):
        host.subject.uninstall(_args(remove_groups=True), _runtime(yes=True))
    assert not host.has("groupdel")


_SOCKETS = ('[sockets.control]\npath = "/run/cgprofile/control/cgprofile.sock"\ngroup = "{control}"\nmode = "0660"\n\n'
            '[sockets.observer]\npath = "/run/cgprofile/observer/cgprofile.sock"\ngroup = "{observer}"\nmode = "0660"\n')


def test_a_hand_edited_socket_group_wins_over_the_record_when_no_flag_is_given(host):
    host.install()
    host.group_gids.update({"ops": 4200, "metrics": 4201})
    edited = _SOCKETS.format(control="ops", observer="metrics")
    Path(host.paths.config).write_text("# mine\n" + edited, encoding="utf-8")
    again = host.install(_args(control_group=None, observer_group=None))
    assert again["config_changes"] == []
    assert Path(host.paths.config).read_text(encoding="utf-8").startswith("# mine\n")
    record = Path(host.paths.install_record).read_text(encoding="utf-8")
    assert 'control_group = "ops"' in record and 'observer_group = "metrics"' in record
    # a group passed as a flag is applied to the file
    flagged = host.install(_args(control_group="cgprofile-control", observer_group=None))
    assert flagged["config_changes"] == ["sockets.control.group"]
    parsed = daemon_config.parse_table(Path(host.paths.config).read_text(encoding="utf-8"))
    assert parsed["sockets.control"]["group"] == "cgprofile-control"
    assert parsed["sockets.observer"]["group"] == "metrics"


def test_prune_never_deletes_the_tree_the_installer_runs_from(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    opt = Path(host.paths.opt)
    for version in ("2.0.0", "2.1.0", "2.2.0", "2.3.0"):
        (opt / version / "venv").mkdir(parents=True)
    running = installer.Installer(host.paths, geteuid=lambda: 0, running_prefix=str(opt / "2.1.0" / "venv"))
    assert running._prune_versions("2.3.0") == ["2.0.0"]
    assert (opt / "2.1.0").is_dir() and not (opt / "2.0.0").exists()


def test_a_unit_path_that_is_a_directory_is_a_bounded_uninstall_failure(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    host.seeded_install()
    unit = Path(host.paths.unit_dir) / "cgprofile.slice"
    unit.unlink()
    unit.mkdir()
    with pytest.raises(installer.InstallError, match="cannot remove .*cgprofile.slice"):
        host.subject.uninstall(_args(), _runtime(yes=True))
    assert Path(host.paths.install_record).is_file()


def test_a_failing_groupdel_does_not_stop_the_rest_of_the_uninstall(tmp_path, monkeypatch):
    running = Path(tmp_path) / "opt" / "cgprofile" / "2.2.0" / "venv"
    host = Host(tmp_path, monkeypatch, running_prefix=str(running))
    host.install()
    original = host.subject.run
    host.group_gids.update({"cgprofile-control": 7101, "cgprofile-observer": 7102})

    def run(argv, **kwargs):
        if list(argv)[:2] == ["groupdel", "cgprofile-control"]:
            return subprocess.CompletedProcess(argv, 8, "", "is a primary group")
        return original(argv, **kwargs)

    host.subject.run = run
    Path(host.paths.state_data).mkdir(parents=True)
    with pytest.raises(installer.InstallError, match="could not remove group.*cgprofile-control") as caught:
        host.subject.uninstall(_args(remove_groups=True, purge_data=True), _runtime(yes=True))
    assert "ownership markers are kept" in str(caught.value)
    assert "groupdel cgprofile-control" in str(caught.value)
    assert host.paths.opt in str(caught.value) and host.paths.current in str(caught.value)
    assert host.paths.install_record in str(caught.value) and host.paths.pending in str(caught.value)
    assert "rerun cgprofiled uninstall" not in str(caught.value)
    # the other group, data purge and config were still handled; current is preserved for manual cleanup
    assert "cgprofile-observer" not in host.group_gids and "cgprofile-control" in host.group_gids
    assert not Path(host.paths.state_data).exists() and not Path(host.paths.config).exists()
    assert (Path(host.paths.opt) / "2.2.0").exists() and Path(host.paths.current).is_symlink()
    assert Path(host.paths.install_record).is_file()


def test_the_current_link_stays_until_the_deferred_step(tmp_path, monkeypatch):
    first = Host(tmp_path, monkeypatch)
    first.seeded_install()
    running = Path(first.paths.opt) / "current" / "venv"
    subject = installer.Installer(first.paths, run=first.subject.run, geteuid=lambda: 0,
                                  running_prefix=str(running))
    body = subject.uninstall(_args(), _runtime(yes=True))
    assert os.path.islink(first.paths.current) and (Path(first.paths.opt) / "2.2.0" / "venv").is_dir()
    subject.finish_uninstall(body)
    assert not os.path.lexists(first.paths.opt)


def test_pip_installs_exactly_the_two_pinned_wheels_without_resolving(host):
    host.install()
    pip = next(c for c in host.commands if c[1:4] == ["-m", "pip", "install"])
    assert "--no-deps" in pip and "--no-index" in pip
    assert [Path(item).name for item in pip if item.endswith(".whl")] == [
        "cgprofile-2.2.0-py3-none-any.whl", "cli_extended-0.5.3-py3-none-any.whl"]


def test_registered_verbs_expose_the_documented_options():
    collected = []

    class Registry:
        def register(self, spec):
            collected.extend(spec.options)

    installer.register_install(Registry())
    names = {flag for option in collected for flag in option.flags}
    assert {"--wheel", "--cli-extended-wheel", "--control-group", "--allow-downgrade", "--remove-groups",
            "--purge-data", "--augeaswrap-wheel"} <= names


def test_the_client_link_target_is_prefixed_by_the_root(tmp_path):
    paths = _paths(tmp_path)
    assert paths.client_target == str(tmp_path / "opt/cgprofile/current/venv/bin/cgprofilectl")
    assert paths.user_target == str(tmp_path / "opt/cgprofile/current/venv/bin/cgprofile")
    assert paths.client_links == ((paths.client_link, paths.client_target), (paths.user_link, paths.user_target))
    assert paths.control_dir == str(tmp_path / "run/cgprofile/control")
    custom_paths = installer.InstallPaths(
        str(tmp_path), "/run/custom/control.sock", "/run/custom/observer.sock", "/run/custom/state.lock",
    )
    assert custom_paths.control_dir == str(tmp_path / "run/custom")
    assert custom_paths.observer_dir == str(tmp_path / "run/custom")
    assert custom_paths.runtime_root == str(tmp_path / "run/custom")
    assert custom_paths.state_dir == str(tmp_path / "run/custom")
    with pytest.raises(ValueError, match="absolute service path"):
        paths.at("relative/path")
    assert installer.InstallPaths().at("/etc/x") == "/etc/x"


# --- remaining branches ----------------------------------------------------------------------


def test_foreign_rule_scan_skips_other_files_and_the_version_scan_skips_plain_files(tmp_path):
    paths = _paths(tmp_path)
    tmpfiles = Path(paths.at("/etc/tmpfiles.d"))
    tmpfiles.mkdir(parents=True)
    (tmpfiles / "README").write_text("not a rule\n", encoding="utf-8")
    Path(paths.opt).mkdir(parents=True)
    (Path(paths.opt) / "notes.txt").write_text("not a version\n", encoding="utf-8")
    assert installer.old_installation_problems(paths) == []


def test_http_fetch_streams_the_asset_and_reports_failures(tmp_path, monkeypatch):
    class Response:
        def __init__(self):
            self.stream = __import__("io").BytesIO(b"asset bytes")

        def __enter__(self):
            return self.stream

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(installer.urllib.request, "urlopen", lambda url, timeout: Response())
    target = tmp_path / "asset.whl"
    installer._fetch_wheel("https://example.invalid/base", "cgprofile", "cgprofile-2.2.0-py3-none-any.whl",
                           str(target))
    assert target.read_bytes() == b"asset bytes"

    def refuse(url, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr(installer.urllib.request, "urlopen", refuse)
    with pytest.raises(installer.InstallError, match="download failed for .*OSError: connection refused"):
        installer._fetch_wheel("https://example.invalid/base", "cgprofile",
                               "cgprofile-2.2.0-py3-none-any.whl", str(target))


def test_duplicate_users_are_listed_once(monkeypatch):
    monkeypatch.setattr(installer.pwd, "getpwnam", lambda name: SimpleNamespace(pw_name=name))
    assert installer._validate_users(["alice", "bob", "alice"]) == ["alice", "bob"]


def test_duplicate_missing_users_are_forgotten_once(monkeypatch):
    def missing_user(name):
        raise KeyError(name)

    monkeypatch.setattr(installer.pwd, "getpwnam", missing_user)
    forgotten = []
    assert installer._validate_users(["retired-user", "retired-user"], forgotten) == []
    assert forgotten == ["retired-user"]


def test_a_command_that_cannot_run_is_a_bounded_failure(tmp_path):
    def broken(argv, **_kwargs):
        raise subprocess.TimeoutExpired(argv, 1)

    subject = installer.Installer(_paths(tmp_path), run=broken, geteuid=lambda: 0)
    with pytest.raises(installer.InstallError, match="command failed to run"):
        subject._command(["systemctl", "daemon-reload"])
    ok = installer.Installer(_paths(tmp_path), run=lambda argv, **kw: subprocess.CompletedProcess(argv, 1, "", ""),
                             geteuid=lambda: 0)
    with pytest.raises(installer.InstallError, match=r"exit 1$"):
        ok._command(["x"])


def test_a_manifest_that_turns_unreadable_after_the_scan_is_refused(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    version = Path(paths.opt) / "2.2.0"
    version.mkdir(parents=True)
    (version / "MANIFEST").write_bytes(b"\xff\xfe")
    monkeypatch.setattr(installer, "old_installation_problems", lambda *_args: [])
    values = {"version": "2.2.0", "wheel_sha": CGPROFILE_SHA, "cli_extended_sha": CLI_EXTENDED_SHA,
              "allow_downgrade": False}
    with pytest.raises(installer.InstallError, match="cannot read immutable version MANIFEST"):
        _installer(tmp_path)._preflight(values, paths.config, dry_run=True)


def test_rollback_onto_a_config_without_socket_tables_reports_no_table_change(host):
    _write_config(host, "keep_days = 3\n")
    values = installer._gather_inputs(_args(version="2.1.0", allow_downgrade=True), _runtime(), host.paths)
    text, changes = host.subject._target_config(host.paths.config, values)
    assert changes == [] and "keep_days = 3" in text and "sockets" not in text


def test_wait_ready_retries_a_failing_status_probe(tmp_path):
    answers = iter((subprocess.CompletedProcess([], 1, "", "down"), subprocess.CompletedProcess(
        [], 0, json.dumps({"daemon": {"version": "2.2.0"}, "config": {"last_reload_error": None}}), "")))
    subject = _installer(tmp_path, sleep=lambda _seconds: None)
    subject._command = lambda _argv, **_kwargs: next(answers)
    subject._wait_ready("/venv", "2.2.0", timeout=30)


def test_reapply_removes_a_leftover_current_new_marker(host):
    host.install()
    Path(host.paths.current_new).symlink_to("2.1.0")
    body = host.install()
    assert body["ok"] and not os.path.lexists(host.paths.current_new)
    assert os.readlink(host.paths.current) == "2.2.0"


def test_nothing_to_restart_or_reload_when_an_active_install_is_unchanged(host):
    host.install()
    host.active.update({"cgprofiled.service": True, "cgprofile-control.socket": True,
                        "cgprofile-observer.socket": True})
    host.commands.clear()
    body = host.install()
    assert body["resources_changed"] == [] and body["config_drift"] is False
    assert not host.has("systemctl", "restart") and not host.has("systemctl", "reload")


def test_uninstall_purge_of_an_absent_data_directory_is_fine(host):
    host.seeded_install()
    assert host.subject.uninstall(_args(purge_data=True), _runtime(yes=True))["data_purged"] is True


def test_uninstall_of_a_pending_only_host_without_etc_or_opt_leftovers(tmp_path, monkeypatch):
    host = Host(tmp_path, monkeypatch)
    Path(host.paths.opt).mkdir(parents=True)
    Path(host.paths.pending).write_text("2.2.0\n", encoding="utf-8")
    body = host.subject.uninstall(_args(), _runtime(yes=True))
    assert body["ok"] and not os.path.lexists(host.paths.opt)
    # an install record whose /opt tree was removed by hand: only the record remains to clean
    Path(host.paths.install_record).parent.mkdir(parents=True)
    Path(host.paths.install_record).write_text("created_groups = []\n", encoding="utf-8")
    body = host.subject.uninstall(_args(), _runtime(yes=True))
    assert body["ok"] and not os.path.lexists(host.paths.install_record)
    assert installer._uninstall_summary({"dry_run": False, "removed": [], "groups_removed": [], "kept": []}) \
        == "nothing to remove"
