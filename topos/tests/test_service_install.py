"""Fake-root behavior for toposd install and uninstall."""

from __future__ import annotations

import argparse
import builtins
import hashlib
import os
import re
import socket
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from topos.service import install
from topos.service.install import InstallError, InstallPaths, Installer


@pytest.fixture(autouse=True)
def _fake_root_owns_runtime_tree(monkeypatch):
    """Directories a test creates under tmp_path count as the expected root-owned tree."""
    monkeypatch.setattr(install, "RUNTIME_TREE_OWNER", (os.getuid(), os.getgid()))


def _owned_host(paths: InstallPaths) -> None:
    """A minimal finished toposd install: the record is the ownership marker."""
    Path(paths.install_record).parent.mkdir(parents=True, exist_ok=True)
    Path(paths.install_record).write_text('created_groups = ["topos-observe"]\n', encoding="utf-8")


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
        "add_user": None,
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
        "cli_extended_wheel": _pin("0.5.3", clix),
        "daemon_lock": _pin(version, lock),
        "wheel_source": "https://release.invalid/download",
        "observe_group": "topos-observe",
        "add_user": None,
        "enable": enable,
        "no_enable": not enable,
        "allow_downgrade": False,
    }
    values.update(overrides)
    return _args(**values), {"topos-" + version + "-py3-none-any.whl": wheel,
                             "cli_extended-0.5.3-py3-none-any.whl": clix,
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
    assert paths.endpoint_fragment == str(tmp_path / "etc/host-endpoints.d/topos.env")
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
    Path(paths.at("/run/topos")).chmod(0o750)
    Path(paths.at("/run/topos/topos.sock")).write_text("old socket placeholder", encoding="utf-8")
    Path(paths.at("/run/topos/bpf")).mkdir()
    Path(paths.at("/run/topos/squeeze")).mkdir()
    Path(paths.current_new).parent.mkdir(parents=True)
    Path(paths.current_new).write_text("leftover", encoding="utf-8")
    old_manifest = Path(paths.opt) / "0.6.0" / "MANIFEST"
    old_manifest.parent.mkdir(parents=True)
    old_manifest.write_text("installer=mdt host-setup\n", encoding="utf-8")
    old_unit = Path(paths.unit_dir) / "topos.service"
    old_unit.parent.mkdir(parents=True, exist_ok=True)
    old_unit.write_text("old unit\n", encoding="utf-8")
    before = {path: path.read_bytes() for path in (tmpfile, unrelated, dropin, old_manifest, old_unit)}

    problems = install.old_installation_problems(paths)
    message = install.manual_removal_message(problems)

    assert any(str(tmpfile) in item for item in problems)
    assert any(str(unrelated) in item for item in problems)
    assert any(str(dropin) in item for item in problems)
    assert any("0750; expected root:root 0755" in item for item in problems)
    assert any("old-layout path" in item for item in problems)
    assert any("leftover install marker" in item for item in problems)
    assert any("unknown version tree MANIFEST" in item for item in problems)
    assert any("hand-installed old unit" in item for item in problems)
    assert "systemctl stop topos.service" in message
    assert "rm -rf /run/topos" in message
    assert "recreate containers that bind-mount /run/topos" in message
    # current.new is under /opt/topos, so the message also carries the uninstall hint.
    assert "toposd uninstall --yes" in message
    assert {path: path.read_bytes() for path in before} == before

    # Units next to PENDING belong to an unfinished toposd install, not an old one.
    Path(paths.pending).write_text("0.7.3\n", encoding="utf-8")
    problems = install.old_installation_problems(paths)
    assert not any("hand-installed old unit" in item for item in problems)
    assert any(item == f"leftover install marker: {paths.pending}" for item in problems)


def test_removal_message_for_an_unfinished_install_omits_the_old_host_steps() -> None:
    message = install.manual_removal_message(["leftover install marker: /opt/topos/PENDING"])
    assert "toposd uninstall --yes" in message
    assert "rm -rf /run/topos" not in message
    assert message.endswith("Then rerun toposd install.")
    # Without findings the old-host steps are the safe default.
    assert "rm -rf /run/topos" in install.manual_removal_message()


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


def test_nonexistent_mapped_group_is_refused_and_an_existing_one_is_allowed(tmp_path: Path, monkeypatch) -> None:
    args, _wheels = _install_args(observe_group="missing-group")
    _groups(monkeypatch)
    with pytest.raises(InstallError, match="mapped observe group 'missing-group' does not exist"):
        install._gather_inputs(args, _runtime(dry_run=True), InstallPaths(tmp_path))

    _groups(monkeypatch, {"ops": 991})
    args, _wheels = _install_args(observe_group="ops")
    values = install._gather_inputs(args, _runtime(dry_run=True), InstallPaths(tmp_path))
    assert values["observe_group"] == "ops"


def test_the_docker_group_is_never_an_observe_group(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    _groups(monkeypatch, {"docker": 994})
    commands = []

    # By flag, even though the group exists.
    args, _wheels = _install_args(observe_group="docker", enable=False)
    with pytest.raises(InstallError, match="observe group must not be docker.*topos-observe.*--add-user observe=USER"):
        install._gather_inputs(args, _runtime(dry_run=True), paths)

    # From the install record.
    Path(paths.install_record).parent.mkdir(parents=True)
    Path(paths.install_record).write_text('observe_group = "docker"\n', encoding="utf-8")
    args, _wheels = _install_args(observe_group=None, enable=False)
    with pytest.raises(InstallError, match="observe group must not be docker"):
        Installer(paths, run=_fake_run(commands), geteuid=lambda: 0).install(args, _runtime(yes=True))
    Path(paths.install_record).unlink()

    # From an existing config file: the config itself is refused, naming the key.
    Path(paths.config).parent.mkdir(parents=True, exist_ok=True)
    Path(paths.config).write_text('[sockets.observe]\ngroup = "docker"\n', encoding="utf-8")
    with pytest.raises(InstallError, match="sockets.observe.group must not be docker"):
        Installer(paths, run=_fake_run(commands), geteuid=lambda: 0).install(args, _runtime(yes=True))

    # Nothing was written or run.
    assert commands == [] and not Path(paths.pending).exists() and not os.path.lexists(paths.opt)


def test_install_rechecks_a_mapped_group_before_writing_anything(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    args, _wheels = _install_args(observe_group="ops", enable=False)
    calls = []

    def gid(group: str) -> int | None:
        if group == "docker":
            return None
        calls.append(None)
        # The existence and GID checks in _gather_inputs see the group; the recheck does not.
        return 991 if len(calls) <= 2 else None

    monkeypatch.setattr(install, "_group_gid", gid)
    subject = Installer(paths, run=_fake_run([]), geteuid=lambda: 0)

    with pytest.raises(InstallError, match="mapped observe group 'ops' does not exist"):
        subject.install(args, _runtime(yes=True))

    assert len(calls) == 3
    assert not Path(paths.config).exists() and not Path(paths.opt).exists()


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
        "observe_group": "ops",
        "observe_users": ["alice"],
        "enable_now": False,
        "created_groups": [],
    }
    record.write_text(install._render_record(fields), encoding="utf-8")
    _groups(monkeypatch, {"ops": 991})
    monkeypatch.setattr(install.pwd, "getpwnam", lambda name: SimpleNamespace(pw_name=name))
    values = install._gather_inputs(_args(), _runtime(dry_run=True), paths)
    assert values["version"] == "0.7.3"
    assert values["observe_group"] == "ops"
    assert values["observe_users"] == ["alice"]
    assert values["enable_now"] is False


def test_tty_prompts_resolve_group_users_and_enable(tmp_path: Path, monkeypatch) -> None:
    class Prompts:
        def __init__(self, pins):
            self.calls = []
            self.pins = list(pins)
            self.defaults = []

        def select(self, message, choices, **kwargs):
            self.calls.append(("select", message))
            self.defaults.append(kwargs.get("default"))
            return choices[1]

        def text(self, message, **_kwargs):
            self.calls.append(("text", message))
            if "Existing group" in message:
                return "ops"
            if message.endswith(" pin"):
                return self.pins.pop(0)
            return "alice"

        def confirm(self, message, **_kwargs):
            self.calls.append(("confirm", message))
            return True

    args, _wheels = _install_args()
    prompts = Prompts((args.wheel, args.cli_extended_wheel, args.daemon_lock))
    args.wheel = args.cli_extended_wheel = args.daemon_lock = args.add_user = None
    args.observe_group = None
    args.enable = args.no_enable = False
    _groups(monkeypatch, {"ops": 991})
    monkeypatch.setattr(install.pwd, "getpwnam", lambda name: SimpleNamespace(pw_name=name))
    values = install._gather_inputs(
        args,
        _runtime(interactive=True, prompts=prompts),
        InstallPaths(tmp_path),
    )
    assert values["observe_group"] == "ops"
    assert values["observe_users"] == ["alice"]
    assert values["enable_now"] is True
    assert prompts.defaults == ["create topos-observe"]
    assert [name for name, _message in prompts.calls] == ["text", "text", "text", "select", "text", "text", "confirm"]


@pytest.mark.parametrize(
    ("gids", "reason"),
    (({"ops": 0}, "GID 0"), ({"ops": 994, "docker": 994}, "the docker group's GID")),
)
def test_a_group_with_gid_zero_or_the_docker_gid_is_refused_on_every_path(
    tmp_path: Path, monkeypatch, gids, reason
) -> None:
    paths = InstallPaths(tmp_path)
    _groups(monkeypatch, gids)
    commands = []
    message = f"observe group 'ops' has {reason}"

    # Flag.
    args, _wheels = _install_args(observe_group="ops", enable=False)
    with pytest.raises(InstallError, match=message) as caught:
        install._gather_inputs(args, _runtime(dry_run=True), paths)
    assert caught.value.exit_code == 2

    # Install record.
    Path(paths.install_record).parent.mkdir(parents=True)
    Path(paths.install_record).write_text('observe_group = "ops"\n', encoding="utf-8")
    args, _wheels = _install_args(observe_group=None, enable=False)
    with pytest.raises(InstallError, match=message):
        Installer(paths, run=_fake_run(commands), geteuid=lambda: 0).install(args, _runtime(yes=True))
    Path(paths.install_record).unlink()

    # Existing config file.
    Path(paths.config).parent.mkdir(parents=True, exist_ok=True)
    Path(paths.config).write_text(install._render_config("ops"), encoding="utf-8")
    with pytest.raises(InstallError, match=message):
        Installer(paths, run=_fake_run(commands), geteuid=lambda: 0).install(args, _runtime(yes=True))

    # Nothing was written or run.
    assert commands == [] and not Path(paths.pending).exists() and not os.path.lexists(paths.opt)


def test_install_rechecks_the_group_gid_before_writing_anything(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    args, _wheels = _install_args(observe_group="ops", enable=False)
    calls = []

    def gid(group: str) -> int | None:
        if group == "docker":
            return None
        calls.append(None)
        # _gather_inputs sees a harmless GID; by the recheck the group has become GID 0.
        return 991 if len(calls) <= 2 else 0

    monkeypatch.setattr(install, "_group_gid", gid)

    with pytest.raises(InstallError, match="observe group 'ops' has GID 0"):
        Installer(paths, run=_fake_run([]), geteuid=lambda: 0).install(args, _runtime(yes=True))

    assert not Path(paths.config).exists() and not Path(paths.opt).exists()


def test_a_deleted_recorded_user_is_forgotten_not_fatal_but_a_flag_user_must_exist(
    tmp_path: Path, monkeypatch
) -> None:
    paths = InstallPaths(tmp_path)
    args, wheels = _install_args(add_user=["observe=alice"])
    groups = _groups(monkeypatch)
    monkeypatch.setattr(install.pwd, "getpwnam", lambda name: (_ for _ in ()).throw(KeyError(name))
                        if name == "ghost" else SimpleNamespace(pw_name=name))
    monkeypatch.setattr(Installer, "_wait_ready", lambda *_args, **_kwargs: None)
    Path(paths.install_record).parent.mkdir(parents=True)
    Path(paths.install_record).write_text('observe_users = ["ghost", "bob"]\n', encoding="utf-8")
    commands = []
    subject = Installer(paths, run=_fake_run(commands, groups), fetch=_fake_fetch(wheels), geteuid=lambda: 0)

    body = subject.install(args, _runtime(yes=True))

    assert body["users_forgotten"] == ["ghost"] and body["users_added"] == ["bob", "alice"]
    assert "dropped recorded users that no longer exist: ghost" in install._install_summary(body)
    assert 'observe_users = ["bob", "alice"]' in Path(paths.install_record).read_text(encoding="utf-8")
    assert not any(c[:3] == ["gpasswd", "-a", "ghost"] for c in commands)

    bad, _wheels = _install_args(add_user=["observe=ghost"])
    with pytest.raises(InstallError, match="user 'ghost' does not exist"):
        subject.install(bad, _runtime(yes=True))

    # Only a deleted user is forgiven: a damaged name in the record is still an error, and a
    # user listed twice is reported once.
    Path(paths.install_record).write_text('observe_users = ["ghost", "ghost"]\n', encoding="utf-8")
    plain, _wheels = _install_args(add_user=None)
    assert subject.install(plain, _runtime(dry_run=True))["users_forgotten"] == ["ghost"]
    Path(paths.install_record).write_text('observe_users = ["Bad User"]\n', encoding="utf-8")
    with pytest.raises(InstallError, match="not a usable Unix user name"):
        subject.install(plain, _runtime(dry_run=True))


def test_a_group_with_the_docker_gid_typed_at_the_prompt_is_refused(tmp_path: Path, monkeypatch) -> None:
    class Prompts:
        def select(self, _message, choices, **_kwargs):
            return choices[1]

        def text(self, message, **_kwargs):
            return "ops" if "Existing group" in message else "unused"

    args, _wheels = _install_args(observe_group=None, enable=False)
    _groups(monkeypatch, {"ops": 994, "docker": 994})

    with pytest.raises(InstallError, match="observe group 'ops' has the docker group's GID"):
        install._gather_inputs(args, _runtime(interactive=True, prompts=Prompts()), InstallPaths(tmp_path))


def test_add_user_flags_are_repeatable_additive_and_kept_in_the_record(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    args, wheels = _install_args(add_user=["observe=alice", "observe=bob"])
    groups = _groups(monkeypatch)
    monkeypatch.setattr(install.pwd, "getpwnam", lambda name: SimpleNamespace(pw_name=name))
    monkeypatch.setattr(Installer, "_wait_ready", lambda *_args, **_kwargs: None)
    commands = []
    subject = Installer(paths, run=_fake_run(commands, groups), fetch=_fake_fetch(wheels), geteuid=lambda: 0)

    dry = subject.install(args, _runtime(dry_run=True))
    assert dry["users_to_add"] == ["alice", "bob"]
    assert "would add to topos-observe: alice, bob" in install._install_summary(dry)

    body = subject.install(args, _runtime(yes=True))
    assert body["users_added"] == ["alice", "bob"]
    assert [c for c in commands if c[0] == "gpasswd"] == [
        ["gpasswd", "-a", "alice", "topos-observe"], ["gpasswd", "-a", "bob", "topos-observe"],
    ]
    assert 'observe_users = ["alice", "bob"]' in Path(paths.install_record).read_text(encoding="utf-8")

    # A rerun without the flag keeps the recorded users; one more flag adds to them.
    args, _wheels = _install_args(add_user=["observe=carol"])
    commands.clear()
    body = subject.install(args, _runtime(yes=True))
    assert body["users_added"] == ["alice", "bob", "carol"]
    assert 'observe_users = ["alice", "bob", "carol"]' in Path(paths.install_record).read_text(encoding="utf-8")
    args, _wheels = _install_args(add_user=None)
    assert subject.install(args, _runtime(yes=True))["users_added"] == ["alice", "bob", "carol"]
    assert not any(c[0] == "usermod" for c in commands)


def test_typing_docker_at_the_group_prompt_is_refused(tmp_path: Path, monkeypatch) -> None:
    class Prompts:
        def select(self, _message, choices, **_kwargs):
            return choices[1]

        def text(self, message, **_kwargs):
            return "docker" if "Existing group" in message else "unused"

    args, _wheels = _install_args(observe_group=None, enable=False)
    _groups(monkeypatch, {"docker": 994})

    with pytest.raises(InstallError, match="observe group must not be docker"):
        install._gather_inputs(args, _runtime(interactive=True, prompts=Prompts()), InstallPaths(tmp_path))


def test_dry_run_works_nonroot_and_writes_nothing(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    args, _wheels = _install_args()
    _groups(monkeypatch)
    subject = Installer(paths, geteuid=lambda: 1000, run=lambda *_args, **_kwargs: pytest.fail("dry run ran a command"))
    body = subject.install(args, _runtime(dry_run=True))
    assert body["dry_run"] is True
    assert body["version"] == "0.7.3"
    assert body["endpoint_fragment"]["path"] == paths.endpoint_fragment
    assert body["endpoint_fragment"]["owner"] == "topos"
    assert set(body["endpoint_fragment"]["entries"]) == {
        "TOPOS_OBSERVE_SOCKET", "TOPOS_OBSERVE_SOCKET_DIR", "TOPOS_OBSERVE_GID",
    }
    assert "would publish host endpoint fragment" in install._install_summary(body)
    assert not Path(paths.opt).exists()
    assert not Path(paths.config).exists()


def test_endpoint_plan_collision_refuses_before_changes(tmp_path: Path, monkeypatch) -> None:
    from augeaswrap import endpoints

    paths = InstallPaths(tmp_path)
    endpoints.publish(
        "another-owner",
        {"TOPOS_OBSERVE_SOCKET": "/tmp/other.sock"},
        writer="test publish",
        root=tmp_path,
    )
    args, wheels = _install_args()
    _groups(monkeypatch)
    commands = []
    subject = Installer(paths, run=_fake_run(commands), fetch=_fake_fetch(wheels), geteuid=lambda: 0)

    with pytest.raises(InstallError, match="augeaswrap.endpoints.plan refused.*declared by both owner"):
        subject.install(args, _runtime(yes=True))

    assert commands == []
    assert not Path(paths.opt).exists()
    assert not Path(paths.install_record).exists()


def test_endpoint_entries_require_a_resolved_group_gid(monkeypatch) -> None:
    monkeypatch.setattr(install, "_group_gid", lambda _name: None)

    with pytest.raises(InstallError, match="has no GID after systemd-sysusers"):
        install._endpoint_entries(install.DEFAULTS, resolve_gid=True)


def test_endpoint_api_reports_missing_augeaswrap_and_preserves_nested_import_errors(monkeypatch) -> None:
    subject = Installer()

    def missing_package(_name):
        raise ModuleNotFoundError("No module named 'augeaswrap'", name="augeaswrap")

    monkeypatch.setattr(install.importlib, "import_module", missing_package)
    with pytest.raises(InstallError, match="augeaswrap.endpoints is unavailable"):
        subject._endpoints_api()

    def missing_nested_dependency(_name):
        raise ModuleNotFoundError("No module named 'nested_dependency'", name="nested_dependency")

    monkeypatch.setattr(install.importlib, "import_module", missing_nested_dependency)
    with pytest.raises(ModuleNotFoundError, match="nested_dependency"):
        subject._endpoints_api()


@pytest.mark.parametrize(
    ("error_type", "message"),
    (
        (InstallError, "endpoint plan refused"),
        (RuntimeError, "augeaswrap.endpoints.plan failed: endpoint plan refused"),
    ),
)
def test_endpoint_preflight_preserves_known_errors_and_wraps_unexpected_ones(
    tmp_path: Path, error_type, message
) -> None:
    subject = Installer(InstallPaths(tmp_path))

    def fail_plan(*_args, **_kwargs):
        raise error_type("endpoint plan refused")

    subject._endpoints_api = lambda: SimpleNamespace(plan=fail_plan)
    with pytest.raises(InstallError, match=message):
        subject._preflight_endpoint_record(install.DEFAULTS)


def test_install_publishes_endpoint_after_the_final_record_write(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    args, wheels = _install_args()
    groups = _groups(monkeypatch)
    commands = []
    subject = Installer(paths, run=_fake_run(commands, groups), fetch=_fake_fetch(wheels), geteuid=lambda: 0)
    monkeypatch.setattr(Installer, "_wait_ready", lambda *_args, **_kwargs: None)
    original_write = install._atomic_write
    record_writes = []
    calls = []

    def atomic_write(path, data, mode=0o644):
        if path == paths.install_record:
            record_writes.append(data)
        return original_write(path, data, mode)

    class EndpointApi:
        def plan(self, owner, entries, *, root):
            assert owner == "topos" and root == paths.root
            return []

        def publish(self, owner, entries, *, writer, root):
            calls.append((owner, entries, writer, root))
            assert len(record_writes) == 2
            assert Path(paths.install_record).is_file()
            assert Path(paths.pending).is_file()

    monkeypatch.setattr(install, "_atomic_write", atomic_write)
    subject._endpoints_api = lambda: EndpointApi()
    body = subject.install(args, _runtime(yes=True))

    assert len(calls) == 1
    owner, entries, writer, root = calls[0]
    assert owner == "topos"
    assert entries == {
        "TOPOS_OBSERVE_SOCKET": "/run/topos/observe/topos.sock",
        "TOPOS_OBSERVE_SOCKET_DIR": "/run/topos/observe",
        "TOPOS_OBSERVE_GID": "9101",
    }
    assert writer == "toposd install 0.7.3" and root == paths.root
    assert body["endpoint_fragment"]["path"] == paths.endpoint_fragment


@pytest.mark.parametrize(
    ("error_type", "message"),
    (
        (InstallError, "publish refused"),
        (RuntimeError, "cannot publish topos host endpoint record: publish refused"),
    ),
)
def test_install_preserves_publish_errors_and_wraps_unexpected_ones(
    tmp_path: Path, monkeypatch, error_type, message
) -> None:
    paths = InstallPaths(tmp_path)
    args, wheels = _install_args()
    groups = _groups(monkeypatch)
    subject = Installer(paths, run=_fake_run([], groups), fetch=_fake_fetch(wheels), geteuid=lambda: 0)
    monkeypatch.setattr(Installer, "_wait_ready", lambda *_args, **_kwargs: None)

    class EndpointApi:
        def plan(self, *_args, **_kwargs):
            return []

        def publish(self, *_args, **_kwargs):
            raise error_type("publish refused")

    subject._endpoints_api = lambda: EndpointApi()
    with pytest.raises(InstallError, match=message):
        subject.install(args, _runtime(yes=True))


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


def test_interrupted_first_install_records_created_group_for_uninstall(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    args, wheels = _install_args()
    groups = _groups(monkeypatch)
    commands = []
    base_run = _fake_run(commands, groups)

    def run(argv, **kwargs):
        if argv and argv[0] == "groupdel":
            commands.append(list(argv))
            groups.pop(argv[1], None)
            return subprocess.CompletedProcess(argv, 0, "", "")
        return base_run(argv, **kwargs)

    def fail_ready(*_args, **_kwargs):
        raise InstallError("ready timeout")

    monkeypatch.setattr(Installer, "_wait_ready", fail_ready)
    subject = Installer(paths, run=run, fetch=_fake_fetch(wheels), geteuid=lambda: 0)
    with pytest.raises(InstallError, match="ready timeout"):
        subject.install(args, _runtime(yes=True))
    assert groups == {"topos-observe": 9101}
    assert 'created_groups = ["topos-observe"]' in Path(paths.install_record).read_text(encoding="utf-8")
    assert Path(paths.pending).is_file()

    body = subject.uninstall(_args(remove_groups=True), _runtime(yes=True))

    assert body["groups_removed"] == ["topos-observe"]
    assert groups == {} and ["groupdel", "topos-observe"] in commands


def test_interrupted_first_install_can_be_reapplied_from_its_early_record(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    args, wheels = _install_args()
    groups = _groups(monkeypatch)
    commands = []
    subject = Installer(paths, run=_fake_run(commands, groups), fetch=_fake_fetch(wheels), geteuid=lambda: 0)

    monkeypatch.setattr(Installer, "_wait_ready", lambda *_args, **_kwargs: (_ for _ in ()).throw(InstallError("ready timeout")))
    with pytest.raises(InstallError, match="ready timeout"):
        subject.install(args, _runtime(yes=True))
    assert Path(paths.install_record).is_file() and Path(paths.pending).is_file()

    monkeypatch.setattr(Installer, "_wait_ready", lambda *_args, **_kwargs: None)
    body = subject.install(args, _runtime(yes=True))

    assert body["ok"] is True and body["enabled"] is True
    assert not Path(paths.pending).exists()
    assert 'created_groups = ["topos-observe"]' in Path(paths.install_record).read_text(encoding="utf-8")
    assert ["systemctl", "daemon-reload"] in commands


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
    # Nothing was applied, so the failure leaves no trace for the next run to refuse.
    assert sorted(os.listdir(paths.opt)) == []


def test_failed_venv_build_removes_its_version_tree_and_pending(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    args, wheels = _install_args()
    _groups(monkeypatch)
    commands = []
    fake = _fake_run(commands)

    def run(argv, **kwargs):
        if "--require-hashes" in argv:
            return subprocess.CompletedProcess(argv, 1, "", "no matching distribution")
        return fake(argv, **kwargs)

    subject = Installer(paths, run=run, fetch=_fake_fetch(wheels), geteuid=lambda: 0, python="python-test")
    with pytest.raises(InstallError, match="no matching distribution"):
        subject.install(args, _runtime(yes=True))
    assert sorted(os.listdir(paths.opt)) == []
    # The venv was created at its final path, never in a stage that is moved.
    assert [command for command in commands if command[1:3] == ["-m", "venv"]] == [
        ["python-test", "-m", "venv", str(Path(paths.opt, "0.7.3", "venv"))]
    ]


def test_unwritable_version_root_is_reported_and_leaves_no_stage(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    args, wheels = _install_args()
    _groups(monkeypatch)
    subject = Installer(paths, run=_fake_run([]), fetch=_fake_fetch(wheels), geteuid=lambda: 0)
    original = install.os.makedirs

    def makedirs(path, *a, **k):
        if str(path) == str(Path(paths.opt, "0.7.3")):
            raise PermissionError(13, "Permission denied")
        return original(path, *a, **k)

    monkeypatch.setattr(install.os, "makedirs", makedirs)
    with pytest.raises(InstallError, match="cannot prepare version tree"):
        subject.install(args, _runtime(yes=True))
    assert sorted(os.listdir(paths.opt)) == []


def test_manifest_write_failure_removes_the_version_tree(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    args, wheels = _install_args()
    _groups(monkeypatch)
    subject = Installer(paths, run=_fake_run([]), fetch=_fake_fetch(wheels), geteuid=lambda: 0)
    original = install._atomic_write

    def atomic_write(path, data, mode=0o644):
        if path.endswith("MANIFEST"):
            raise OSError(28, "No space left on device")
        return original(path, data, mode)

    monkeypatch.setattr(install, "_atomic_write", atomic_write)
    with pytest.raises(InstallError, match="cannot build immutable version tree .*No space left"):
        subject.install(args, _runtime(yes=True))
    assert sorted(os.listdir(paths.opt)) == []


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
    _owned_host(paths)
    values = {"version": "0.7.9", "wheel_sha": "a", "cli_extended_sha": "b", "lock_sha": "c", "allow_downgrade": False}
    subject = Installer(paths, geteuid=lambda: 0)
    with pytest.raises(InstallError, match="refusing downgrade from 0.8.0 to 0.7.9.*--allow-downgrade"):
        subject._preflight(values, dry_run=True)
    values["allow_downgrade"] = True
    assert subject._preflight(values, dry_run=True) == (True, True)


def test_versions_without_toposd_are_refused(tmp_path: Path, monkeypatch) -> None:
    _groups(monkeypatch)
    args, _wheels = _install_args(version="0.6.9")
    with pytest.raises(InstallError, match="supports topos 0.7.0 and later; 0.6.9 has no toposd"):
        install._gather_inputs(args, _runtime(dry_run=True), InstallPaths(tmp_path))


def test_install_refuses_a_foreign_entry_in_usr_local_bin(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    _groups(monkeypatch)
    Path(paths.client_link).parent.mkdir(parents=True)
    Path(paths.client_link).write_text("#!/bin/sh\n", encoding="utf-8")
    args, _wheels = _install_args()
    with pytest.raises(InstallError, match="usr/local/bin/topos exists and was not written by toposd install"):
        Installer(paths, geteuid=lambda: 0).install(args, _runtime(dry_run=True))
    # A link into /opt/topos is ours and passes.
    Path(paths.client_link).unlink()
    Path(paths.client_link).symlink_to(paths.at("/opt/topos/current/venv/bin/topos"))
    assert Installer(paths, geteuid=lambda: 0).install(args, _runtime(dry_run=True))["dry_run"] is True


def test_existing_config_keeps_non_group_values_and_allows_group_edit(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    _groups(monkeypatch, {"ops": 991})
    Path(paths.config).parent.mkdir(parents=True)
    edited = install._render_config("ops").replace('"0660"', '"0600"')
    Path(paths.config).write_text(edited, encoding="utf-8")
    subject = Installer(paths, geteuid=lambda: 0)

    text, config, changed = subject._config("ops")
    assert (text, changed) == (edited, False) and config.sockets.observe.mode == "0600"
    text, config, changed = subject._config("topos-observe")
    assert changed and config.sockets.observe.group == "topos-observe"
    assert 'mode = "0600"' in text
    Path(paths.config).write_text("invalid = [\n", encoding="utf-8")
    with pytest.raises(InstallError, match="toposd.toml is invalid.*fix or remove it"):
        subject._config("ops")
    Path(paths.config).write_bytes(b"\xff\xfe")
    with pytest.raises(InstallError, match="cannot read .*toposd.toml"):
        subject._config("ops")
    Path(paths.config).unlink()
    text, _config, changed = subject._config("ops")
    assert (text, changed) == (install._render_config("ops"), True)


def test_install_applies_and_reports_a_flag_group_change_in_dry_run(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    _groups(monkeypatch, {"ops": 991, "redis": 992})
    Path(paths.config).parent.mkdir(parents=True)
    original = install._render_config("ops").replace('group = "ops"', 'group = "ops" # operator note')
    Path(paths.config).write_text(original, encoding="utf-8")
    args, _wheels = _install_args(observe_group="redis", enable=False)

    body = Installer(paths, geteuid=lambda: 0).install(args, _runtime(dry_run=True))

    assert body["config_changes"] == ["sockets.observe.group"]
    assert body["observe_group"] == "redis"
    assert install.parse_config(install._edit_config_group(original, "redis", paths.config)).sockets.observe.group == "redis"
    assert "group = \"redis\" # operator note" in install._edit_config_group(original, "redis", paths.config)
    assert "set sockets.observe.group = redis in " + paths.config in install._install_summary(body)


def test_existing_config_group_wins_without_flag_and_flag_equal_to_file_is_unchanged(
    tmp_path: Path, monkeypatch
) -> None:
    paths = InstallPaths(tmp_path)
    _groups(monkeypatch, {"ops": 991})
    Path(paths.config).parent.mkdir(parents=True)
    original = install._render_config("ops")
    Path(paths.config).write_text(original, encoding="utf-8")
    Path(paths.install_record).write_text('observe_group = "topos-observe"\n', encoding="utf-8")
    args, _wheels = _install_args(observe_group=None, enable=False)

    body = Installer(paths, geteuid=lambda: 0).install(args, _runtime(dry_run=True))
    assert body["observe_group"] == "ops" and body["config_changes"] == []
    assert Path(paths.config).read_text(encoding="utf-8") == original

    args.observe_group = "ops"
    body = Installer(paths, geteuid=lambda: 0).install(args, _runtime(dry_run=True))
    assert body["config_changes"] == []
    assert Path(paths.config).read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    ("contents", "expected_line"),
    (
        ('state_dir = "/run/topos/state"\n\n[sockets.observe]\n# preserve this comment\npath = "/run/topos/observe/topos.sock"\n',
         'group = "ops"\n'),
        ('state_dir = "/run/topos/state"\nsockets.observe.path = "/run/topos/observe/topos.sock"\n',
         'sockets.observe.group = "ops"\n'),
        ('state_dir = "/run/topos/state"\n[sockets]\nobserve.path = "/run/topos/observe/topos.sock"\n',
         'observe.group = "ops"\n'),
        ('state_dir = "/run/topos/state"\n',
         '[sockets.observe]\ngroup = "ops"\n'),
    ),
)
def test_config_group_editor_adds_missing_group_without_losing_comments_or_dotted_form(
    tmp_path: Path, contents: str, expected_line: str
) -> None:
    paths = InstallPaths(tmp_path)
    Path(paths.config).parent.mkdir(parents=True)
    Path(paths.config).write_text(contents, encoding="utf-8")

    edited, config, changed = Installer(paths)._config("ops")

    assert changed and expected_line in edited
    if "preserve this comment" in contents:
        assert "preserve this comment" in edited
    assert config.sockets.observe.group == "ops"


def test_config_group_editor_refuses_unsafe_inline_table_and_invalid_edited_text(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    Path(paths.config).parent.mkdir(parents=True)
    Path(paths.config).write_text('sockets = { observe = { group = "ops" } }\n', encoding="utf-8")
    with pytest.raises(InstallError, match=r"cannot safely edit sockets.observe.group.*edit the group under \[sockets.observe\]"):
        Installer(paths)._config("redis")

    original = install.parse_config

    def refuse_edited(text: str, *, source: str = "<config>"):
        if 'group = "redis"' in text:
            raise install.ConfigError("invalid edited result")
        return original(text, source=source)

    monkeypatch.setattr(install, "parse_config", refuse_edited)
    Path(paths.config).write_text(install._render_config("ops"), encoding="utf-8")
    with pytest.raises(InstallError, match="editing sockets.observe.group would make .* invalid: invalid edited result"):
        Installer(paths)._config("redis")


def test_config_group_editor_handles_parent_table_and_missing_final_newline(tmp_path: Path) -> None:
    paths = InstallPaths(tmp_path)
    parent_table = install._edit_config_group("[sockets]\n", "ops", paths.config)
    assert parent_table == '[sockets]\n[sockets.observe]\ngroup = "ops"\n'
    assert install.parse_config(parent_table).sockets.observe.group == "ops"

    no_final_newline = '[sockets.observe]\npath = "/run/topos/observe/topos.sock"'
    edited = install._edit_config_group(no_final_newline, "ops", paths.config)
    assert edited == no_final_newline + '\ngroup = "ops"\n'
    assert install.parse_config(edited).sockets.observe.group == "ops"


@pytest.mark.parametrize(
    "text",
    (
        '[sockets.observe]\npath = "/run/topos/observe/topos.sock"\n[sockets.extra]\n',
        '[sockets]\nobserve.path = "/run/topos/observe/topos.sock"\n[sockets.extra]\n',
    ),
)
def test_config_group_editor_refuses_unsupported_nested_socket_tables(tmp_path: Path, text: str) -> None:
    with pytest.raises(InstallError, match="cannot safely edit sockets.observe.group"):
        install._edit_config_group(text, "ops", str(tmp_path / "toposd.toml"))


def test_config_group_editor_refuses_nested_inline_observe_table(tmp_path: Path) -> None:
    with pytest.raises(InstallError, match="cannot safely edit sockets.observe.group"):
        install._edit_config_group(
            '[sockets]\nobserve = { path = "/run/topos/observe/topos.sock" }\n',
            "ops",
            str(tmp_path / "toposd.toml"),
        )


def test_existing_config_group_reports_read_and_parse_failures(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    Path(paths.config).parent.mkdir(parents=True)
    Path(paths.config).write_text("not = [valid\n", encoding="utf-8")
    with pytest.raises(InstallError, match="is invalid: .*fix or remove it"):
        install._existing_config_group(paths)

    original = Path.read_text

    def denied(path: Path, *args, **kwargs):
        if os.fspath(path) == paths.config:
            raise PermissionError("denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(InstallError, match="cannot read .*toposd.toml: denied"):
        install._existing_config_group(paths)


def test_a_config_without_a_group_key_names_no_group(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    _groups(monkeypatch, {"ops": 993})
    Path(paths.config).parent.mkdir(parents=True)
    Path(paths.config).write_text('state_dir = "/run/topos/state"\n', encoding="utf-8")
    args, _wheels = _install_args(observe_group=None, enable=False)

    assert install._existing_config_group(paths) is None
    # A pipe run with no flag, no record and a file that names no group is an error,
    # not the default group.
    with pytest.raises(InstallError, match="missing required --observe-group"):
        Installer(paths, geteuid=lambda: 0).install(args, _runtime(dry_run=True))

    # The record's group is then applied to the file and reported.
    Path(paths.install_record).write_text('observe_group = "ops"\n', encoding="utf-8")
    body = Installer(paths, geteuid=lambda: 0).install(args, _runtime(dry_run=True))
    assert body["observe_group"] == "ops" and body["config_changes"] == ["sockets.observe.group"]

    # A record that names the default group changes nothing in the file.
    Path(paths.install_record).write_text('observe_group = "topos-observe"\n', encoding="utf-8")
    body = Installer(paths, geteuid=lambda: 0).install(args, _runtime(dry_run=True))
    assert body["observe_group"] == "topos-observe" and body["config_changes"] == []

    # Dotted and table forms both count as naming a group.
    for text in ('sockets.observe.group = "ops"\n', '[sockets.observe]\ngroup = "ops"\n'):
        Path(paths.config).write_text(text, encoding="utf-8")
        assert install._existing_config_group(paths) == "ops"


def test_config_group_editor_returns_text_unchanged_when_the_group_already_matches(tmp_path: Path) -> None:
    text = install._render_config("ops")

    assert install._edit_config_group(text, "ops", str(tmp_path / "toposd.toml")) == text


def test_a_fresh_install_creates_its_config_and_reports_no_edit(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    _groups(monkeypatch)
    args, _wheels = _install_args(observe_group="topos-observe", enable=False)

    body = Installer(paths, geteuid=lambda: 0).install(args, _runtime(dry_run=True))

    assert body["config_changes"] == []
    assert "set sockets.observe.group" not in install._install_summary(body)


def test_a_group_edit_keeps_the_mode_of_the_operators_config(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    args, wheels = _install_args(observe_group="redis")
    groups = _groups(monkeypatch, {"redis": 992})
    Path(paths.config).parent.mkdir(parents=True)
    Path(paths.config).write_text(install._render_config("topos-observe"), encoding="utf-8")
    os.chmod(paths.config, 0o640)
    subject = Installer(paths, run=_fake_run([], groups), fetch=_fake_fetch(wheels), geteuid=lambda: 0)
    monkeypatch.setattr(Installer, "_wait_ready", lambda *_args, **_kwargs: None)

    body = subject.install(args, _runtime(yes=True))

    assert body["config_changes"] == ["sockets.observe.group"]
    assert 'group = "redis"' in Path(paths.config).read_text(encoding="utf-8")
    assert stat.S_IMODE(os.stat(paths.config).st_mode) == 0o640


def test_install_replaces_a_dangling_config_symlink_and_a_stat_failure_is_a_clean_refusal(
    tmp_path: Path, monkeypatch
) -> None:
    paths = InstallPaths(tmp_path)
    args, wheels = _install_args()
    groups = _groups(monkeypatch)
    Path(paths.config).parent.mkdir(parents=True)
    Path(paths.config).symlink_to(tmp_path / "missing.toml")
    subject = Installer(paths, run=_fake_run([], groups), fetch=_fake_fetch(wheels), geteuid=lambda: 0)
    monkeypatch.setattr(Installer, "_wait_ready", lambda *_args, **_kwargs: None)

    body = subject.install(args, _runtime(yes=True))

    assert body["config_changes"] == [] and not Path(paths.config).is_symlink()
    assert 'group = "topos-observe"' in Path(paths.config).read_text(encoding="utf-8")

    original = install.os.stat

    def denied(path, *a, **kw):
        if os.fspath(path) == paths.config:
            raise PermissionError("denied")
        return original(path, *a, **kw)

    monkeypatch.setattr(install.os, "stat", denied)
    with pytest.raises(InstallError, match="cannot inspect .*toposd.toml: denied"):
        subject.install(args, _runtime(yes=True))
    assert not Path(paths.pending).exists()


def test_uninstall_reports_nothing_installed_for_a_directory_at_the_sysusers_path_of_an_unowned_host(
    tmp_path: Path,
) -> None:
    paths = InstallPaths(tmp_path)
    Path(paths.sysusers).mkdir(parents=True)

    body = Installer(paths, run=_fake_run([]), geteuid=lambda: 0).uninstall(_args(), _runtime(yes=True))

    assert body["message"] == "nothing installed by toposd install"


@pytest.mark.parametrize("which", ("sysusers", "dropin"))
def test_uninstall_refuses_a_directory_at_every_unlinked_path_of_an_owned_host(
    tmp_path: Path, which: str
) -> None:
    paths = InstallPaths(tmp_path)
    _owned_host(paths)
    target = Path(paths.sysusers) if which == "sysusers" else Path(paths.service_dropins, "50-mdt-socket-group.conf")
    target.mkdir(parents=True)
    commands = []

    with pytest.raises(InstallError, match=f"unit path is a directory: {re.escape(str(target))}"):
        Installer(paths, run=_fake_run(commands), geteuid=lambda: 0).uninstall(_args(), _runtime(yes=True))

    assert commands == [] and target.is_dir()


def test_config_group_edit_is_idempotent_and_duplicate_target_refuses(tmp_path: Path) -> None:
    paths = InstallPaths(tmp_path)
    Path(paths.config).parent.mkdir(parents=True)
    Path(paths.config).write_text(install._render_config("ops"), encoding="utf-8")
    subject = Installer(paths)
    edited, _config, changed = subject._config("redis")
    assert changed
    Path(paths.config).write_text(edited, encoding="utf-8")
    same, _config, changed = subject._config("redis")
    assert (same, changed) == (edited, False)

    with pytest.raises(InstallError, match="cannot safely edit sockets.observe.group"):
        install._edit_config_group(
            '[sockets.observe]\ngroup = "ops"\ngroup = "redis"\n', "redis", paths.config
        )


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
    from augeaswrap import endpoints

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
    endpoints.publish(
        "topos",
        {
            "TOPOS_OBSERVE_SOCKET": "/run/topos/observe/topos.sock",
            "TOPOS_OBSERVE_SOCKET_DIR": "/run/topos/observe",
            "TOPOS_OBSERVE_GID": "9101",
        },
        writer="toposd install 0.7.3",
        root=tmp_path,
    )
    Path(paths.opt, "0.7.3", "venv", "bin").mkdir(parents=True)
    Path(paths.opt, "current").symlink_to("0.7.3")
    Path(paths.client_link).parent.mkdir(parents=True)
    Path(paths.client_link).symlink_to(paths.at("/opt/topos/current/venv/bin/topos"))
    Path(paths.daemon_link).symlink_to("/opt/foreign/toposd")
    Path(paths.service_dropins).mkdir(parents=True)
    Path(paths.service_dropins, "50-mdt-socket-group.conf").write_text("old\n", encoding="utf-8")
    Path(paths.state_dir).mkdir(parents=True, mode=0o700)
    Path(paths.state_dir).chmod(0o700)
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
    assert not Path(paths.install_record).exists()
    assert not Path(paths.endpoint_fragment).exists()
    assert not Path(paths.config).parent.exists()
    assert not Path(paths.opt).exists()
    assert not Path(paths.sysusers).exists()
    assert not Path(paths.socket_node).exists()
    assert not Path(paths.client_link).is_symlink()
    assert Path(paths.daemon_link).is_symlink()
    assert body["kept"] == [paths.daemon_link]
    assert paths.daemon_link not in body["removed"] and paths.client_link in body["removed"]
    assert ["systemctl", "daemon-reload"] in commands
    # The socket is stopped first, so a connection cannot re-activate the service.
    assert ["systemctl", "disable", "--now", "topos-observe.socket", "topos.service"] in commands
    assert not Path(paths.runtime_root).exists()
    assert not Path(paths.service_dropins).exists()
    assert "kept (not written by toposd install): " + paths.daemon_link in install._uninstall_summary(body)


def test_uninstall_withdraws_endpoint_before_stopping_or_removing(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    _owned_host(paths)
    units = Path(paths.unit_dir)
    units.mkdir(parents=True)
    for name in ("topos-observe.socket", "topos.service"):
        (units / name).write_text("owned\n", encoding="utf-8")
    commands = []
    subject = Installer(paths, run=_fake_run(commands), geteuid=lambda: 0)
    observed = []

    class EndpointApi:
        def withdraw(self, owner, *, root):
            observed.append((owner, root, list(commands), Path(paths.install_record).is_file()))

    subject._endpoints_api = lambda: EndpointApi()
    subject.uninstall(_args(), _runtime(yes=True))

    assert observed == [("topos", paths.root, [], True)]
    assert commands[0][:3] == ["systemctl", "disable", "--now"]


@pytest.mark.parametrize(
    ("error_type", "message"),
    (
        (InstallError, "withdraw refused"),
        (RuntimeError, "cannot withdraw topos host endpoint record: withdraw refused"),
    ),
)
def test_uninstall_preserves_withdraw_errors_and_wraps_unexpected_ones(
    tmp_path: Path, error_type, message
) -> None:
    paths = InstallPaths(tmp_path)
    _owned_host(paths)
    subject = Installer(paths, run=_fake_run([]), geteuid=lambda: 0)

    class EndpointApi:
        def withdraw(self, *_args, **_kwargs):
            raise error_type("withdraw refused")

    subject._endpoints_api = lambda: EndpointApi()
    with pytest.raises(InstallError, match=message):
        subject.uninstall(_args(), _runtime(yes=True))


def test_uninstall_removes_only_recorded_groups_when_requested(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    _owned_host(paths)
    _groups(monkeypatch, {"topos-observe": 9101})
    commands = []
    subject = Installer(paths, run=_fake_run(commands), geteuid=lambda: 0)
    body = subject.uninstall(_args(remove_groups=True), _runtime(yes=True))
    assert body["groups_removed"] == ["topos-observe"]
    assert ["groupdel", "topos-observe"] in commands
    assert "removed group: topos-observe" in install._uninstall_summary(body)


def test_uninstall_keeps_other_files_in_etc_topos(tmp_path: Path) -> None:
    paths = InstallPaths(tmp_path)
    _owned_host(paths)
    Path(paths.config).parent.joinpath("notes.txt").write_text("operator\n", encoding="utf-8")
    body = Installer(paths, run=_fake_run([]), geteuid=lambda: 0).uninstall(_args(), _runtime(yes=True))
    assert body["kept"] == [str(Path(paths.config).parent)]
    assert Path(paths.config).parent.joinpath("notes.txt").is_file()
    assert not Path(paths.install_record).exists()


def test_uninstall_dry_run_is_nonroot_lists_targets_and_never_runs_commands(tmp_path: Path) -> None:
    paths = InstallPaths(tmp_path)
    subject = Installer(paths, geteuid=lambda: 1000,
                        run=lambda *_args, **_kwargs: pytest.fail("dry run ran systemctl"))
    body = subject.uninstall(_args(), _runtime(dry_run=True))
    assert body["dry_run"] is True and body["removed"] == []
    assert install._uninstall_summary(body) == "nothing installed by toposd install"
    assert list(tmp_path.iterdir()) == []

    _owned_host(paths)
    body = subject.uninstall(_args(remove_groups=True), _runtime(dry_run=True))
    assert body["removed"] == [paths.install_record]
    assert body["groups_removed"] == ["topos-observe"]
    summary = install._uninstall_summary(body)
    assert f"would remove: {paths.install_record}" in summary
    assert "would remove group: topos-observe" in summary
    assert Path(paths.install_record).is_file()
    assert install._uninstall_summary({"removed": [], "groups_removed": []}) == "nothing to remove"


def test_uninstall_needs_root_and_confirmation(tmp_path: Path) -> None:
    paths = InstallPaths(tmp_path)
    _owned_host(paths)
    with pytest.raises(InstallError, match="needs root"):
        Installer(paths, geteuid=lambda: 1000).uninstall(_args(), _runtime(yes=True))
    with pytest.raises(InstallError, match="not confirmed"):
        Installer(paths, geteuid=lambda: 0).uninstall(_args(), _runtime(yes=False))
    assert Path(paths.install_record).is_file()


def test_uninstall_refuses_a_host_toposd_install_does_not_own(tmp_path: Path) -> None:
    paths = InstallPaths(tmp_path)
    old_unit = Path(paths.unit_dir) / "topos.service"
    old_unit.parent.mkdir(parents=True)
    old_unit.write_text("hand-installed\n", encoding="utf-8")
    with pytest.raises(InstallError, match="(?s)toposd uninstall refused.*hand-installed old unit"):
        Installer(paths, run=_fake_run([]), geteuid=lambda: 0).uninstall(_args(), _runtime(yes=True))
    assert old_unit.is_file()


def test_uninstall_clears_an_unfinished_install_marked_by_pending(tmp_path: Path) -> None:
    paths = InstallPaths(tmp_path)
    Path(paths.opt, "0.7.3").mkdir(parents=True)  # unmarked: the build was interrupted
    Path(paths.pending).write_text("0.7.3\n", encoding="utf-8")
    Path(paths.opt, ".stage.abc").mkdir()
    unit = Path(paths.unit_dir) / "topos.service"
    unit.parent.mkdir(parents=True)
    unit.write_text("written before the failure\n", encoding="utf-8")
    body = Installer(paths, run=_fake_run([]), geteuid=lambda: 0).uninstall(_args(), _runtime(yes=True))
    assert str(unit) in body["removed"] and not unit.exists()
    assert not Path(paths.opt).exists()


@pytest.mark.parametrize(
    ("relative", "message"),
    (
        ("run/topos/bpf", "old-layout path remains"),
        ("run/topos/other", "unknown path under the runtime tree"),
        ("run/topos/observe/extra.sock", "unknown path under the runtime tree"),
        ("opt/topos/notes", "unknown path under the version root"),
    ),
)
def test_uninstall_refuses_unknown_paths_before_any_change(tmp_path: Path, relative: str, message: str) -> None:
    paths = InstallPaths(tmp_path)
    _owned_host(paths)
    Path(paths.observe_dir).mkdir(parents=True)
    Path(paths.opt).mkdir(parents=True)
    target = tmp_path / relative
    if target.name in {"bpf", "other"}:
        target.mkdir()
    else:
        target.write_text("x\n", encoding="utf-8")
    commands = []
    with pytest.raises(InstallError, match=message):
        Installer(paths, run=_fake_run(commands), geteuid=lambda: 0).uninstall(_args(), _runtime(yes=True))
    assert commands == [] and target.exists() and Path(paths.install_record).is_file()


def test_uninstall_refuses_regular_file_at_socket_node(tmp_path: Path) -> None:
    paths = InstallPaths(tmp_path)
    _owned_host(paths)
    Path(paths.socket_node).parent.mkdir(parents=True)
    Path(paths.socket_node).write_text("not a socket", encoding="utf-8")
    with pytest.raises(InstallError, match="topos.sock is not a socket"):
        Installer(paths, geteuid=lambda: 0).uninstall(_args(), _runtime(yes=True))
    assert Path(paths.socket_node).is_file()


def test_uninstall_reports_an_unremovable_runtime_root(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    _owned_host(paths)
    Path(paths.runtime_root).mkdir(parents=True)
    original = install.os.rmdir

    def rmdir(path, *a, **k):
        if str(path) == paths.runtime_root:
            raise OSError(16, "Device or resource busy")
        return original(path, *a, **k)

    monkeypatch.setattr(install.os, "rmdir", rmdir)
    with pytest.raises(InstallError, match="cannot remove runtime directory .*busy"):
        Installer(paths, run=_fake_run([]), geteuid=lambda: 0).uninstall(_args(), _runtime(yes=True))
    # The record survives, so the uninstall can be rerun.
    assert Path(paths.install_record).is_file()


def test_uninstall_reports_an_unremovable_version_root(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    _owned_host(paths)
    version = Path(paths.opt, "0.7.3")
    version.mkdir(parents=True)
    Path(paths.pending).write_text("0.7.4\n", encoding="utf-8")
    original = install.shutil.rmtree

    def rmtree(path, *a, **k):
        if str(path) == str(version):
            raise OSError(30, "Read-only file system", str(version))
        return original(path, *a, **k)

    monkeypatch.setattr(install.shutil, "rmtree", rmtree)
    with pytest.raises(InstallError, match="cannot remove .*opt/topos/0.7.3.*Read-only"):
        Installer(paths, run=_fake_run([]), geteuid=lambda: 0).uninstall(_args(), _runtime(yes=True))
    # Both ownership markers survive, so the uninstall can be rerun.
    assert Path(paths.pending).is_file() and Path(paths.install_record).is_file()


def test_running_version_dir_comes_from_real_prefix_and_requires_a_real_tree(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    version = Path(paths.opt, "0.7.3")
    (version / "venv").mkdir(parents=True)
    Path(paths.current).symlink_to("0.7.3")

    through_current = Installer(paths, running_prefix=f"{paths.current}/venv")
    assert through_current._running_version_dir() == str(version)
    assert Installer(paths, running_prefix=str(version))._running_version_dir() == str(version)
    assert Installer(paths, running_prefix=paths.opt)._running_version_dir() is None
    assert Installer(paths, running_prefix=str(Path(paths.opt, "0.7.4", "venv")))._running_version_dir() is None

    outside = tmp_path / "outside"
    outside.mkdir()
    Path(paths.opt, "0.7.5").symlink_to(outside, target_is_directory=True)
    assert Installer(paths, running_prefix=str(Path(paths.opt, "0.7.5")))._running_version_dir() is None

    monkeypatch.setattr(install.os.path, "commonpath", lambda _paths: (_ for _ in ()).throw(ValueError("drive")))
    assert through_current._running_version_dir() is None


def test_prune_versions_skips_the_tree_the_installer_runs_from(tmp_path: Path) -> None:
    paths = InstallPaths(tmp_path)
    Path(paths.opt).mkdir(parents=True)
    for version in ("0.5.0", "0.6.0", "0.7.0", "0.8.0"):
        Path(paths.opt, version).mkdir()
    running = str(Path(paths.opt, "0.6.0"))

    removed = Installer(paths)._prune_versions("0.8.0", running)

    assert removed == ["0.5.0"]
    assert Path(running).is_dir()


def test_uninstall_defers_running_tree_and_current_until_finish(tmp_path: Path) -> None:
    paths = InstallPaths(tmp_path)
    _owned_host(paths)
    version = Path(paths.opt, "0.7.3")
    version.mkdir(parents=True)
    (version / "MANIFEST").write_text("installer=toposd install\n", encoding="utf-8")
    Path(paths.opt, "0.7.2").mkdir()
    Path(paths.opt, "0.7.2", "MANIFEST").write_text("installer=toposd install\n", encoding="utf-8")
    Path(paths.current).symlink_to("0.7.3")
    Path(paths.pending).write_text("0.7.3\n", encoding="utf-8")
    subject = Installer(paths, run=_fake_run([]), geteuid=lambda: 0,
                        running_prefix=f"{paths.current}/venv")

    body = subject.uninstall(_args(), _runtime(yes=True))

    assert body["deferred"] == str(version)
    assert Path(paths.current).is_symlink() and version.is_dir()
    assert not Path(paths.opt, "0.7.2").exists()
    assert Path(paths.pending).is_file() and Path(paths.install_record).is_file()

    subject.finish_uninstall(body)

    assert "deferred" not in body
    assert not Path(paths.current).exists() and not version.exists()
    assert not Path(paths.pending).exists() and not Path(paths.opt).exists()
    assert not Path(paths.install_record).exists()


def test_uninstall_reports_config_directory_inspection_failure_before_marker_removal(
    tmp_path: Path, monkeypatch
) -> None:
    paths = InstallPaths(tmp_path)
    _owned_host(paths)
    original = install.os.listdir

    def listdir(path):
        if os.fspath(path) == os.path.dirname(paths.config):
            raise PermissionError("denied")
        return original(path)

    monkeypatch.setattr(install.os, "listdir", listdir)
    with pytest.raises(InstallError, match="cannot inspect .*etc/topos.*denied"):
        Installer(paths, run=_fake_run([]), geteuid=lambda: 0).uninstall(_args(), _runtime(yes=True))
    assert Path(paths.install_record).is_file()


def test_version_root_and_marker_helpers_accept_missing_paths_and_remove_symlink_children(tmp_path: Path) -> None:
    paths = InstallPaths(tmp_path)
    subject = Installer(paths)
    subject._remove_version_root(keep=None)
    subject._remove_markers({"kept": []})

    Path(paths.opt).mkdir(parents=True)
    target = tmp_path / "target"
    target.mkdir()
    link = Path(paths.opt, "current.new")
    link.symlink_to(target, target_is_directory=True)
    subject._remove_version_root(keep=None)
    assert not link.exists() and not link.is_symlink()


def test_version_root_removal_reports_unlink_error(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    Path(paths.opt).mkdir(parents=True)
    leftover = Path(paths.opt, "current.new")
    leftover.write_text("partial", encoding="utf-8")
    original = install.os.unlink

    def denied(path, *args, **kwargs):
        if os.fspath(path) == str(leftover):
            raise PermissionError(13, "denied", str(leftover))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(install.os, "unlink", denied)
    with pytest.raises(InstallError, match="cannot remove .*current.new: .*denied"):
        Installer(paths)._remove_version_root(keep=None)


def test_marker_removal_reports_version_root_error_and_keeps_nonempty_config_dir(tmp_path: Path) -> None:
    paths = InstallPaths(tmp_path)
    subject = Installer(paths)
    Path(paths.opt).mkdir(parents=True)
    Path(paths.current).symlink_to("0.7.3")
    with pytest.raises(InstallError, match="cannot remove .*opt/topos: "):
        subject._remove_markers({"kept": []})

    Path(paths.current).unlink()
    Path(paths.config).parent.mkdir(parents=True, exist_ok=True)
    foreign = Path(paths.config).parent / "operator.conf"
    foreign.write_text("keep", encoding="utf-8")
    body = {"kept": []}
    subject._remove_markers(body)
    assert body["kept"] == [os.path.dirname(paths.config)]
    assert foreign.is_file()


def test_finish_uninstall_is_rerunnable_after_running_tree_was_already_removed(tmp_path: Path) -> None:
    paths = InstallPaths(tmp_path)
    _owned_host(paths)
    Path(paths.opt).mkdir(parents=True)
    Path(paths.current).symlink_to("0.7.3")
    Path(paths.pending).write_text("0.7.3\n", encoding="utf-8")
    body = {"kept": [], "deferred": str(Path(paths.opt, "0.7.3"))}

    Installer(paths).finish_uninstall(body)

    assert not Path(paths.current).exists() and not Path(paths.pending).exists()
    assert not Path(paths.opt).exists() and not Path(paths.install_record).exists()

    Installer(paths).finish_uninstall({"kept": [], "deferred": str(Path(paths.opt, "0.7.3"))})
    assert not Path(paths.current).exists() and not Path(paths.opt).exists()


def test_a_finish_failure_after_a_group_failure_keeps_the_groupdel_advice(monkeypatch) -> None:
    from cli_extended import CliFailure

    class Subject:
        def uninstall(self, _args, _runtime):
            return {"ok": False, "removed": [], "kept": [], "deferred": "/version",
                    "group_failures": ["topos-observe: busy"], "groups_kept": ["topos-observe"]}

        def finish_uninstall(self, _body):
            raise InstallError("cannot remove /version: busy")

    monkeypatch.setattr(install, "_handler_paths", lambda _args, _runtime: InstallPaths("/fake"))
    monkeypatch.setattr(install, "Installer", lambda _paths: Subject())
    runtime = _runtime(yes=True)
    runtime.output.primary = lambda _body: None

    with pytest.raises(CliFailure, match=r"finish by hand: remove /fake/opt/topos, then /fake/etc/topos/install.toml; "
                                          r"run groupdel for topos-observe once free"):
        install._uninstall_handler(argparse.Namespace(root="/", remove_groups=True), runtime)


def test_finish_uninstall_reports_running_tree_and_current_removal_errors(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    version = Path(paths.opt, "0.7.3")
    version.mkdir(parents=True)
    Path(paths.current).symlink_to("0.7.3")
    subject = Installer(paths)
    original_rmtree = install.shutil.rmtree

    def denied_tree(path, *args, **kwargs):
        if os.fspath(path) == str(version):
            raise PermissionError("tree busy")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(install.shutil, "rmtree", denied_tree)
    with pytest.raises(InstallError, match="cannot remove .*0.7.3: tree busy"):
        subject.finish_uninstall({"deferred": str(version), "kept": []})
    assert version.is_dir() and Path(paths.current).is_symlink()

    monkeypatch.setattr(install.shutil, "rmtree", original_rmtree)
    original_unlink = install.os.unlink

    def denied_current(path, *args, **kwargs):
        if os.fspath(path) == paths.current:
            raise PermissionError("current busy")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(install.os, "unlink", denied_current)
    with pytest.raises(InstallError, match="cannot remove .*current: current busy"):
        subject.finish_uninstall({"deferred": str(version), "kept": []})
    assert not version.exists() and Path(paths.current).is_symlink()


def test_uninstall_handler_prints_before_tree_removal_and_imports_nothing_after(
    tmp_path: Path, monkeypatch
) -> None:
    paths = InstallPaths(tmp_path)
    _owned_host(paths)
    version = Path(paths.opt, "0.7.3")
    version.mkdir(parents=True)
    (version / "MANIFEST").write_text("installer=toposd install\n", encoding="utf-8")
    Path(paths.current).symlink_to("0.7.3")
    monkeypatch.setattr(install, "_handler_paths", lambda _args, _runtime: paths)
    base_installer = Installer
    monkeypatch.setattr(
        install,
        "Installer",
        lambda _paths: base_installer(paths, run=_fake_run([]), geteuid=lambda: 0,
                                     running_prefix=f"{paths.current}/venv"),
    )

    state = {"removed": False, "events": [], "imports": []}
    original_import = builtins.__import__
    original_rmtree = install.shutil.rmtree

    def tracked_import(name, *args, **kwargs):
        if state["removed"]:
            state["imports"].append(name)
        return original_import(name, *args, **kwargs)

    def tracked_rmtree(path, *args, **kwargs):
        result = original_rmtree(path, *args, **kwargs)
        if str(path) == str(version):
            state["removed"] = True
        return result

    monkeypatch.setattr(builtins, "__import__", tracked_import)
    monkeypatch.setattr(install.shutil, "rmtree", tracked_rmtree)
    runtime = _runtime(yes=True, json_mode=True)
    runtime.output.primary = lambda value: state["events"].append(("printed", value))
    try:
        result = install._uninstall_handler(argparse.Namespace(root="/", remove_groups=False), runtime)
    finally:
        builtins.__import__ = original_import
    assert result == 0
    assert state["events"] and state["events"][0][0] == "printed"
    assert "deferred" not in state["events"][0][1]
    assert state["removed"] and state["imports"] == []


def test_uninstall_handler_maps_finish_failure_to_rerun_hint(monkeypatch) -> None:
    from cli_extended import CliFailure

    class Subject:
        def uninstall(self, _args, _runtime):
            return {"ok": True, "removed": [], "kept": [], "deferred": "/version"}

        def finish_uninstall(self, _body):
            raise InstallError("cannot remove /version: busy")

    monkeypatch.setattr(install, "_handler_paths", lambda _args, _runtime: InstallPaths("/fake"))
    monkeypatch.setattr(install, "Installer", lambda _paths: Subject())
    runtime = _runtime(yes=True)
    printed = []
    runtime.output.primary = printed.append

    with pytest.raises(CliFailure, match=r"cannot remove /version: busy; finish by hand: remove /fake/opt/topos, then /fake/etc/topos/install.toml") as caught:
        install._uninstall_handler(argparse.Namespace(root="/", remove_groups=False), runtime)
    assert printed
    assert "rerun" not in str(caught.value)  # the toposd that ran is gone with its tree


def test_pending_only_host_keeps_its_marker_until_the_end(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    Path(paths.opt).mkdir(parents=True)
    Path(paths.pending).write_text("0.7.3\n", encoding="utf-8")
    Path(paths.runtime_root).mkdir(parents=True)
    original = install.os.rmdir

    def rmdir(path, *a, **k):
        if str(path) == paths.runtime_root:
            raise OSError(16, "Device or resource busy")
        return original(path, *a, **k)

    monkeypatch.setattr(install.os, "rmdir", rmdir)
    with pytest.raises(InstallError, match="cannot remove runtime directory"):
        Installer(paths, run=_fake_run([]), geteuid=lambda: 0).uninstall(_args(), _runtime(yes=True))
    assert Path(paths.pending).is_file()
    monkeypatch.setattr(install.os, "rmdir", original)
    Installer(paths, run=_fake_run([]), geteuid=lambda: 0).uninstall(_args(), _runtime(yes=True))
    assert not Path(paths.opt).exists() and not Path(paths.runtime_root).exists()


def test_remove_groups_rerun_skips_groups_already_gone(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    _record(paths, 'created_groups = ["topos-observe"]\n')
    Path(paths.config).write_text("state_dir = '/run/topos/state'\n", encoding="utf-8")
    _groups(monkeypatch, {})
    commands = []
    body = Installer(paths, run=_fake_run(commands), geteuid=lambda: 0).uninstall(
        _args(remove_groups=True), _runtime(yes=True)
    )
    assert body["groups_removed"] == []
    assert ["groupdel", "topos-observe"] not in commands


def test_groupdel_failure_is_reported_after_cleanup_and_names_the_manual_steps(
    tmp_path: Path, monkeypatch
) -> None:
    from cli_extended import CliFailure

    paths = InstallPaths(tmp_path)
    _record(paths, 'created_groups = ["topos-observe"]\n')
    Path(paths.config).write_text("state_dir = '/run/topos/state'\n", encoding="utf-8")
    groups = _groups(monkeypatch, {"topos-observe": 9101})
    commands = []
    base_run = _fake_run(commands)

    def run(argv, **kwargs):
        if argv and argv[0] == "groupdel":
            commands.append(list(argv))
            return subprocess.CompletedProcess(argv, 1, "", "group is a primary group")
        return base_run(argv, **kwargs)

    base_installer = Installer
    monkeypatch.setattr(install, "_handler_paths", lambda _args, _runtime: paths)
    monkeypatch.setattr(
        install, "Installer", lambda _paths: base_installer(paths, run=run, geteuid=lambda: 0)
    )
    runtime = _runtime(yes=True, json_mode=True)
    printed = []
    runtime.output.primary = printed.append

    with pytest.raises(CliFailure, match="could not remove groups topos-observe; run groupdel for them") as caught:
        install._uninstall_handler(argparse.Namespace(root="/", remove_groups=True), runtime)

    assert paths.install_record in str(caught.value) and "rerun" not in str(caught.value)
    assert caught.value.exit_code == 2
    result = printed[0]
    assert result["ok"] is False
    assert result["groups_kept"] == ["topos-observe"]
    assert result["group_failures"] and "primary group" in result["group_failures"][0]
    assert paths.install_record not in result["removed"]
    assert paths.install_record in [str(path) for path in Path(paths.config).parent.iterdir()]
    assert paths.config in result["removed"] and not Path(paths.config).exists()
    assert ["systemctl", "daemon-reload"] in commands
    assert "kept group: topos-observe" in install._uninstall_summary(result)
    assert "group removal failed:" in install._uninstall_summary(result)

    retry = base_installer(paths, run=_fake_run([]), geteuid=lambda: 0)
    retry.uninstall(_args(remove_groups=False), _runtime(yes=True))
    assert not Path(paths.install_record).exists()
    assert groups == {"topos-observe": 9101}


def test_created_group_record_refuses_any_group_topos_did_not_create(tmp_path: Path) -> None:
    paths = InstallPaths(tmp_path)
    _record(paths, 'created_groups = ["ops"]\n')
    commands = []

    with pytest.raises(InstallError, match=r"install record .*etc/topos/install.toml contains unsupported created group 'ops'"):
        Installer(paths, run=_fake_run(commands), geteuid=lambda: 0).uninstall(
            _args(remove_groups=True), _runtime(yes=True)
        )

    assert commands == [] and Path(paths.install_record).is_file()


def test_uninstall_refuses_a_directory_at_an_owned_unit_before_commands(tmp_path: Path) -> None:
    paths = InstallPaths(tmp_path)
    _owned_host(paths)
    unit = Path(paths.unit_dir, "topos.service")
    unit.mkdir(parents=True)
    commands = []

    with pytest.raises(InstallError, match="unit path is a directory: .*topos.service"):
        Installer(paths, run=_fake_run(commands), geteuid=lambda: 0).uninstall(_args(), _runtime(yes=True))

    assert commands == [] and unit.is_dir() and Path(paths.install_record).is_file()


def test_uninstall_refuses_when_an_owned_unit_cannot_be_inspected(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    _owned_host(paths)
    unit = Path(paths.unit_dir, "topos.service")
    unit.parent.mkdir(parents=True)
    unit.write_text("owned\n", encoding="utf-8")
    original = install.os.lstat

    def denied(path, *args, **kwargs):
        if os.fspath(path) == str(unit):
            raise PermissionError("denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(install.os, "lstat", denied)
    commands = []
    with pytest.raises(InstallError, match="cannot inspect unit path .*topos.service: denied"):
        Installer(paths, run=_fake_run(commands), geteuid=lambda: 0).uninstall(_args(), _runtime(yes=True))
    assert commands == [] and unit.is_file()


def test_uninstall_reports_unit_unlink_oserror_with_its_path(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    _owned_host(paths)
    unit = Path(paths.unit_dir, "topos.service")
    unit.parent.mkdir(parents=True)
    unit.write_text("owned\n", encoding="utf-8")
    original = install.os.unlink

    def denied(path, *args, **kwargs):
        if os.fspath(path) == str(unit):
            raise PermissionError("denied")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(install.os, "unlink", denied)
    with pytest.raises(InstallError, match="cannot remove .*topos.service.*denied"):
        Installer(paths, run=_fake_run([]), geteuid=lambda: 0).uninstall(_args(), _runtime(yes=True))
    assert unit.is_file()


def test_a_failed_upgrade_is_reapplied_by_rerunning_install(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    args, wheels = _install_args(enable=False)
    groups = _groups(monkeypatch)
    subject = Installer(paths, run=_fake_run([], groups), fetch=_fake_fetch(wheels), geteuid=lambda: 0)
    subject.install(args, _runtime(yes=True))
    # An upgrade died after the PENDING write and the current.new link.
    Path(paths.pending).write_text("0.7.3\n", encoding="utf-8")
    Path(paths.current_new).symlink_to("0.7.3")
    # A doctor-style scan still reports the unfinished apply.
    assert any("leftover install marker" in item for item in install.old_installation_problems(paths))

    # A retry whose pin is wrong fails before applying and keeps the earlier marker.
    retry_args, retry_wheels = _install_args(version="0.7.4", enable=False)
    retry_args.wheel = "0.7.4@sha256:" + "f" * 64
    retry = Installer(paths, run=_fake_run([], groups), fetch=_fake_fetch(retry_wheels), geteuid=lambda: 0)
    with pytest.raises(InstallError, match="refusing before unpack"):
        retry.install(retry_args, _runtime(yes=True))
    assert Path(paths.pending).is_file()

    # The failed attempt already wrote the units and current, so on disk nothing
    # differs; the re-apply still reloads and restarts the running service.
    commands = []
    active = {"topos.service": True, "topos-observe.socket": True}
    monkeypatch.setattr(Installer, "_wait_ready", lambda *_args, **_kwargs: None)
    Installer(paths, run=_fake_run(commands, groups, active), fetch=_fake_fetch(wheels),
              geteuid=lambda: 0).install(args, _runtime(yes=True))
    assert ["systemctl", "daemon-reload"] in commands
    assert ["systemctl", "restart", "topos-observe.socket"] in commands
    assert not Path(paths.pending).exists() and not Path(paths.current_new).exists()
    assert os.readlink(paths.current) == "0.7.3"


def test_a_kept_config_must_keep_the_fixed_layout(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    Path(paths.config).parent.mkdir(parents=True)
    subject = Installer(paths, geteuid=lambda: 0)
    for key_line, key in (
        ('state_dir = "/run/topos/elsewhere"\n', "state_dir"),
        ('[sockets.observe]\npath = "/run/topos/observe/x.sock"\n', "sockets.observe.path"),
    ):
        Path(paths.config).write_text(key_line, encoding="utf-8")
        with pytest.raises(InstallError, match=f"sets {key} = .*fixed path"):
            subject._config("topos-observe")


def test_release_candidates_of_the_socket_release_are_supported() -> None:
    assert install._is_socket_unit_version("0.7.0rc1")
    assert not install._is_socket_unit_version("0.6.9")


def test_root_option_is_only_valid_for_dry_runs(tmp_path: Path) -> None:
    from cli_extended import CliFailure

    args = argparse.Namespace(root=str(tmp_path))
    with pytest.raises(CliFailure, match="--root is only valid with --dry-run"):
        install._handler_paths(args, _runtime(dry_run=False))
    assert install._handler_paths(args, _runtime(dry_run=True)).root == str(tmp_path)
    assert install._handler_paths(argparse.Namespace(root="/"), _runtime(dry_run=False)).root == "/"


# --- input resolution -------------------------------------------------------


def _record(paths: InstallPaths, text: str) -> None:
    Path(paths.install_record).parent.mkdir(parents=True, exist_ok=True)
    Path(paths.install_record).write_text(text, encoding="utf-8")


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"wheel": "not-a-pin"}, "--wheel must be"),
        ({"add_user": ["observe=Bad User"]}, "not a usable Unix user name"),
        ({"add_user": ["observe=ghost"]}, "user 'ghost' does not exist"),
        ({"add_user": ["alice"]}, "--add-user takes observe=USER"),
        ({"add_user": ["control=alice"]}, "--add-user takes observe=USER"),
        ({"add_user": ["observe="]}, "--add-user takes observe=USER"),
        ({"observe_group": "Bad Group"}, "not a usable observe group name"),
        ({"enable": True, "no_enable": True}, "choose only one of --enable and --no-enable"),
    ),
)
def test_gather_inputs_refuses_bad_values(tmp_path: Path, monkeypatch, overrides, message) -> None:
    _groups(monkeypatch)
    monkeypatch.setattr(install.pwd, "getpwnam", lambda name: (_ for _ in ()).throw(KeyError(name)))
    args, _wheels = _install_args(**overrides)
    with pytest.raises(InstallError, match=message):
        install._gather_inputs(args, _runtime(dry_run=True), InstallPaths(tmp_path))


def test_gather_inputs_requires_matching_lock_version_and_accepts_no_enable(tmp_path: Path, monkeypatch) -> None:
    _groups(monkeypatch)
    args, _wheels = _install_args(enable=False)
    assert install._gather_inputs(args, _runtime(dry_run=True), InstallPaths(tmp_path))["enable_now"] is False
    args.daemon_lock = _pin("0.7.4", b"lock")
    with pytest.raises(InstallError, match="--daemon-lock version 0.7.4 must equal topos wheel version 0.7.3"):
        install._gather_inputs(args, _runtime(dry_run=True), InstallPaths(tmp_path))


def test_dry_run_defaults_enable_false_but_a_noninteractive_install_still_requires_a_choice(
    tmp_path: Path, monkeypatch
) -> None:
    _groups(monkeypatch)
    args, _wheels = _install_args(enable=False)
    args.no_enable = False

    values = install._gather_inputs(args, _runtime(dry_run=True), InstallPaths(tmp_path))
    assert values["enable_now"] is False
    with pytest.raises(InstallError, match="missing required --enable or --no-enable"):
        install._gather_inputs(args, _runtime(), InstallPaths(tmp_path))


@pytest.mark.parametrize(
    ("record", "message"),
    (
        ("wheel = [\n", "cannot read prior install record"),
        ('created_groups = ["Bad Group"]\n', "invalid created_groups list"),
        ("observe_group = 5\n", "saved observe_group value is invalid"),
        ('observe_group = "topos-observe"\nobserve_users = [1]\n', "saved observe_users value is invalid"),
    ),
)
def test_a_damaged_install_record_is_refused(tmp_path: Path, monkeypatch, record, message) -> None:
    paths = InstallPaths(tmp_path)
    _record(paths, record)
    _groups(monkeypatch)
    args, _wheels = _install_args(observe_group=None, add_user=None)
    with pytest.raises(InstallError, match=message):
        install._gather_inputs(args, _runtime(dry_run=True), paths)


def test_render_record_refuses_unsupported_values() -> None:
    with pytest.raises(InstallError, match="unsupported install record value for count"):
        install._render_record({"count": 3})


class _Prompts:
    """Scripted TTY answers; a value of PromptCancelled raises it."""

    def __init__(self, select=None, text=None, confirm=True):
        self.select_answer, self.text_answers, self.confirm_answer = select, dict(text or {}), confirm

    @staticmethod
    def _give(value):
        if value is install.PromptCancelled:
            raise install.PromptCancelled()
        return value

    def select(self, _message, choices, **_kwargs):
        return self._give(choices[0] if self.select_answer is None else self.select_answer)

    def text(self, message, **_kwargs):
        for key, value in self.text_answers.items():
            if key in message:
                return self._give(value)
        return ""

    def confirm(self, _message, **_kwargs):
        return self._give(self.confirm_answer)


def _interactive_args(**overrides):
    args, _wheels = _install_args(observe_group=None, add_user=None, **overrides)
    args.enable = args.no_enable = False
    return args


def test_tty_group_prompt_creates_the_default_or_refuses_an_empty_mapping(tmp_path: Path, monkeypatch) -> None:
    _groups(monkeypatch)
    paths = InstallPaths(tmp_path)
    values = install._gather_inputs(_interactive_args(), _runtime(interactive=True, prompts=_Prompts()), paths)
    assert values["observe_group"] == "topos-observe"
    prompts = _Prompts(select="map an existing group", text={"Existing group": ""})
    with pytest.raises(InstallError, match="missing required --observe-group"):
        install._gather_inputs(_interactive_args(), _runtime(interactive=True, prompts=prompts), paths)


@pytest.mark.parametrize(
    "runtime",
    (
        _runtime(),
        _runtime(json_mode=True),
        _runtime(dry_run=True),
    ),
)
def test_noninteractive_install_requires_an_observe_group(tmp_path: Path, monkeypatch, runtime) -> None:
    _groups(monkeypatch)
    args, _wheels = _install_args(observe_group=None)

    with pytest.raises(InstallError, match="missing required --observe-group; provide it on a non-interactive run"):
        install._gather_inputs(args, runtime, InstallPaths(tmp_path))


@pytest.mark.parametrize(
    ("prompts", "clear_wheel"),
    (
        (_Prompts(select=install.PromptCancelled), False),
        (_Prompts(text={"Users": install.PromptCancelled}), False),
        (_Prompts(confirm=install.PromptCancelled), False),
        (_Prompts(text={"topos release pin": install.PromptCancelled}), True),
    ),
)
def test_cancelled_prompts_exit_130(tmp_path: Path, monkeypatch, prompts, clear_wheel) -> None:
    _groups(monkeypatch)
    args = _interactive_args()
    if clear_wheel:
        args.wheel = None
    with pytest.raises(InstallError, match="installation prompts cancelled") as raised:
        install._gather_inputs(args, _runtime(interactive=True, prompts=prompts), InstallPaths(tmp_path))
    assert raised.value.exit_code == 130


# --- old-installation scan edges ---------------------------------------------


def test_scan_skips_non_conf_entries_and_reads_unrecognised_drop_ins(tmp_path: Path) -> None:
    paths = InstallPaths(tmp_path)
    Path(paths.tmpfiles_dir).mkdir(parents=True)
    Path(paths.tmpfiles_dir, "README").write_text("/run/topos\n", encoding="utf-8")
    dropins = Path(paths.service_dropins)
    dropins.mkdir(parents=True)
    (dropins / "notes.txt").write_text("Group=docker\n", encoding="utf-8")
    (dropins / "10-limits.conf").write_text("[Service]\nMemoryMax=1G\n", encoding="utf-8")
    group = dropins / "20-group.conf"
    group.write_text("[Service]\nGroup=docker\n", encoding="utf-8")
    unreadable = dropins / "30-binary.conf"
    unreadable.write_bytes(b"\xff\xfe")
    found = install.find_foreign_rules(paths)
    assert str(group) in found
    assert any(item.startswith(f"{unreadable} (cannot inspect") for item in found)
    assert not any("notes.txt" in item or "10-limits" in item or "README" in item for item in found)


def test_scan_reports_unlistable_directories_unreadable_nodes_and_manifests(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    Path(paths.tmpfiles_dir).mkdir(parents=True)
    Path(paths.runtime_root).mkdir(parents=True)
    manifest = Path(paths.opt, "0.7.3", "MANIFEST")
    manifest.parent.mkdir(parents=True)
    manifest.write_bytes(b"\xff\xfe")
    original_listdir, original_lstat = install.os.listdir, install.os.lstat

    def listdir(path):
        if str(path) == paths.tmpfiles_dir:
            raise PermissionError(13, "Permission denied")
        return original_listdir(path)

    def lstat(path):
        if str(path) == paths.runtime_root:
            raise PermissionError(13, "Permission denied")
        return original_lstat(path)

    monkeypatch.setattr(install.os, "listdir", listdir)
    monkeypatch.setattr(install.os, "lstat", lstat)
    problems = install.old_installation_problems(paths)
    assert any(f"cannot inspect {paths.tmpfiles_dir}: Permission denied" in item for item in problems)
    assert f"cannot inspect {paths.runtime_root}: Permission denied" in problems
    assert any(item.startswith(f"cannot inspect version MANIFEST {manifest}") for item in problems)


def test_scan_reports_a_version_root_that_is_not_a_directory(tmp_path: Path) -> None:
    paths = InstallPaths(tmp_path)
    Path(paths.opt).parent.mkdir(parents=True)
    Path(paths.opt).write_text("not a directory\n", encoding="utf-8")
    assert f"version root is not a real directory: {paths.opt}" in install.old_installation_problems(paths)


# --- asset fetch -------------------------------------------------------------


def test_directory_wheel_source_copies_assets_or_names_the_missing_one(tmp_path: Path) -> None:
    source = tmp_path / "wheels"
    source.mkdir()
    (source / "topos-0.7.3-py3-none-any.whl").write_bytes(b"wheel")
    dest = tmp_path / "copy.whl"
    install._fetch_asset(str(source), "topos", "topos-0.7.3-py3-none-any.whl", str(dest), None)
    assert dest.read_bytes() == b"wheel"
    with pytest.raises(InstallError, match="--wheel-source directory must contain topos-0.7.3-daemon.lock"):
        install._fetch_asset(str(source), "topos", "topos-0.7.3-daemon.lock", str(dest), None)


def test_fetch_refuses_plain_http_and_unexpected_names_and_reports_failures(tmp_path: Path, monkeypatch) -> None:
    dest = str(tmp_path / "asset")
    with pytest.raises(InstallError, match="must be an HTTPS release base or a directory"):
        install._fetch_asset("http://insecure.invalid", "topos", "topos-0.7.3-py3-none-any.whl", dest, None)
    with pytest.raises(InstallError, match="unexpected wheel filename: other.whl"):
        install._fetch_asset("https://r.invalid", "topos", "other.whl", dest, None)
    with pytest.raises(InstallError, match="download failed for https://r.invalid/cli-extended-v0.5.3/.*: OSError: offline"):
        install._fetch_asset("https://r.invalid", "cli-extended", "cli_extended-0.5.3-py3-none-any.whl", dest,
                             lambda _url, _dest: (_ for _ in ()).throw(OSError("offline")))

    import io

    class _Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    urls = []
    monkeypatch.setattr(install.urllib.request, "urlopen",
                        lambda url, timeout: urls.append((url, timeout)) or _Response(b"lock bytes"))
    install._fetch_asset("https://r.invalid/", "topos", "topos-0.7.3-daemon.lock", dest, None)
    assert Path(dest).read_bytes() == b"lock bytes"
    assert urls == [("https://r.invalid/topos-v0.7.3/topos-0.7.3-daemon.lock", 60)]
    monkeypatch.setattr(install.urllib.request, "urlopen",
                        lambda url, timeout: (_ for _ in ()).throw(TimeoutError("slow")))
    with pytest.raises(InstallError, match="download failed .*TimeoutError: slow"):
        install._fetch_asset("https://r.invalid", "topos", "topos-0.7.3-daemon.lock", dest, None)


def test_install_from_a_directory_source_resolves_dependencies_offline(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path / "root")
    Path(paths.root).mkdir(parents=True)
    source = tmp_path / "wheels"
    source.mkdir()
    args, wheels = _install_args(wheel_source=str(source), enable=False)
    for name, data in wheels.items():
        (source / name).write_bytes(data)
    groups = _groups(monkeypatch)
    commands = []
    Installer(paths, run=_fake_run(commands, groups), geteuid=lambda: 0).install(args, _runtime(yes=True))
    pip = [command for command in commands if "--require-hashes" in command]
    assert pip and pip[0][-3:] == ["--no-index", "--find-links", str(source)]


def test_an_invalid_daemon_lock_is_refused_before_the_venv(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    args, wheels = _install_args()
    wheels["topos-0.7.3-daemon.lock"] = b"textual>=8\n"
    args.daemon_lock = _pin("0.7.3", wheels["topos-0.7.3-daemon.lock"])
    _groups(monkeypatch)
    commands = []
    with pytest.raises(InstallError, match="daemon lock topos-0.7.3-daemon.lock is invalid"):
        Installer(paths, run=_fake_run(commands), fetch=_fake_fetch(wheels), geteuid=lambda: 0).install(
            args, _runtime(yes=True)
        )
    assert commands == [] and sorted(os.listdir(paths.opt)) == []


# --- hello probe and readiness ------------------------------------------------


def _hello_server(path: Path, reply):
    import socketserver
    import threading

    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            import json

            request = json.loads(self.rfile.readline())
            self.wfile.write(reply(request))

    server = socketserver.UnixStreamServer(str(path), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.mark.parametrize(
    ("reply", "error"),
    (
        (lambda request: b"", "no bounded hello response"),
        (lambda request: b'{"id": "other", "ok": true, "result": {}}\n', "no successful hello envelope"),
        (lambda request: ('{"id": "%s", "ok": true, "result": []}\n' % request["id"]).encode(), "has no result"),
    ),
)
def test_hello_probe_refuses_bad_replies(tmp_path: Path, reply, error) -> None:
    path = tmp_path / "hello.sock"
    server = _hello_server(path, reply)
    try:
        with pytest.raises(OSError, match=error):
            install._hello(str(path), timeout=5.0)
    finally:
        server.shutdown()
        server.server_close()


def test_hello_probe_returns_the_result(tmp_path: Path) -> None:
    path = tmp_path / "hello.sock"
    server = _hello_server(
        path,
        lambda request: ('{"id": "%s", "ok": true, "result": {"identity": {"version": "0.7.3"}}}\n'
                         % request["id"]).encode(),
    )
    try:
        assert install._hello(str(path), timeout=5.0) == {"identity": {"version": "0.7.3"}}
    finally:
        server.shutdown()
        server.server_close()


def test_ready_probe_retries_after_errors_and_times_out_after_the_last_attempt(tmp_path: Path, monkeypatch) -> None:
    replies = [OSError("refused"), {"identity": {"version": "0.7.3"}}]

    def hello(_path, *, timeout):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(install, "_hello", hello)
    sleeps = []
    subject = Installer(InstallPaths(tmp_path), monotonic=lambda: 0.0, sleep=sleeps.append)
    subject._wait_ready("/run/topos/observe/topos.sock", "0.7.3")
    assert sleeps == [1.0] and replies == []

    # The deadline passes during the attempt itself.
    clock = iter((0.0, 0.0, 5.0))
    monkeypatch.setattr(install, "_hello", lambda _path, *, timeout: {"identity": {}})
    subject = Installer(InstallPaths(tmp_path), monotonic=lambda: next(clock), sleep=lambda _s: None)
    with pytest.raises(InstallError, match="did not report topos version 0.7.3 within 1s"):
        subject._wait_ready("/run/topos/observe/topos.sock", "0.7.3", timeout=1.0)


# --- preflight and immutable version trees ------------------------------------


def _values(**overrides):
    args, wheels = _install_args()
    values = {
        "version": "0.7.3", "wheel": args.wheel, "cli_extended_wheel": args.cli_extended_wheel,
        "daemon_lock": args.daemon_lock, "allow_downgrade": False,
        "wheel_sha": args.wheel.split(":")[1], "cli_extended_sha": args.cli_extended_wheel.split(":")[1],
        "lock_sha": args.daemon_lock.split(":")[1],
    }
    values.update(overrides)
    return values


def _manifest_text(values) -> str:
    return "\n".join((
        f"version={values['version']}", f"wheel_pin={values['wheel']}",
        f"cli_extended_wheel_pin={values['cli_extended_wheel']}", f"daemon_lock_pin={values['daemon_lock']}",
        f"wheel_sha256={values['wheel_sha']}", f"cli_extended_sha256={values['cli_extended_sha']}",
        f"daemon_lock_sha256={values['lock_sha']}", "installer=toposd install", "",
    ))


def test_preflight_needs_root_outside_dry_runs(tmp_path: Path) -> None:
    with pytest.raises(InstallError, match="toposd install needs root"):
        Installer(InstallPaths(tmp_path), geteuid=lambda: 1000)._preflight(_values(), dry_run=False)


def test_preflight_accepts_a_matching_immutable_tree_and_refuses_a_differing_one(tmp_path: Path) -> None:
    paths = InstallPaths(tmp_path)
    values = _values()
    tree = Path(paths.opt, "0.7.3")
    tree.mkdir(parents=True)
    (tree / "MANIFEST").write_text(_manifest_text(values), encoding="utf-8")
    subject = Installer(paths, geteuid=lambda: 0)
    assert subject._preflight(values, dry_run=False) == (False, True)
    (tree / "MANIFEST").write_text(_manifest_text(_values(lock_sha="0" * 64)), encoding="utf-8")
    with pytest.raises(InstallError, match="is immutable and its manifest differs"):
        subject._preflight(values, dry_run=False)


def test_preflight_refuses_an_unreadable_manifest(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    tree = Path(paths.opt, "0.7.3")
    tree.mkdir(parents=True)
    (tree / "MANIFEST").write_text(_manifest_text(_values()), encoding="utf-8")
    monkeypatch.setattr(install, "old_installation_problems", lambda _paths: [])
    original = Path.read_text

    def read_text(path, *a, **k):
        if path == tree / "MANIFEST":
            raise PermissionError(13, "denied")
        return original(path, *a, **k)

    monkeypatch.setattr(Path, "read_text", read_text)
    with pytest.raises(InstallError, match="cannot read immutable version MANIFEST"):
        Installer(paths, geteuid=lambda: 0)._preflight(_values(), dry_run=False)


def test_preflight_refuses_an_unmarked_requested_version(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    Path(paths.opt, "0.7.3").mkdir(parents=True)
    monkeypatch.setattr(install, "old_installation_problems", lambda _paths: [])
    with pytest.raises(InstallError, match="unmarked version directory .*0.7.3 exists; remove it manually"):
        Installer(paths, geteuid=lambda: 0)._preflight(_values(), dry_run=False)


@pytest.mark.parametrize(
    ("target", "make_dir", "message"),
    (
        (None, True, "exists but is not a symlink"),
        ("../elsewhere", False, "must point to a version directory inside /opt/topos"),
        ("0.7.1", False, "points to an incomplete version tree"),
    ),
)
def test_current_pointer_must_name_a_finished_version(tmp_path: Path, target, make_dir, message) -> None:
    paths = InstallPaths(tmp_path)
    Path(paths.opt).mkdir(parents=True)
    if make_dir:
        Path(paths.current).mkdir()
    else:
        Path(paths.current).symlink_to(target)
    with pytest.raises(InstallError, match=message):
        Installer(paths)._current_version()


def test_an_existing_version_tree_is_reused_only_with_its_daemon(tmp_path: Path) -> None:
    paths = InstallPaths(tmp_path)
    values = _values()
    tree = Path(paths.opt, "0.7.3")
    tree.mkdir(parents=True)
    (tree / "MANIFEST").write_text(_manifest_text(values), encoding="utf-8")
    subject = Installer(paths, run=lambda *_a, **_k: pytest.fail("reuse ran a command"))
    with pytest.raises(InstallError, match="bin/toposd is missing from an immutable version tree"):
        subject._ensure_version(values)
    (tree / "venv" / "bin").mkdir(parents=True)
    (tree / "venv" / "bin" / "toposd").write_text("", encoding="utf-8")
    assert subject._ensure_version(values) == str(tree / "venv")


def test_install_link_check_treats_an_unreadable_link_as_foreign(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    link = Path(paths.client_link)
    link.parent.mkdir(parents=True)
    link.symlink_to(paths.at("/opt/topos/current/venv/bin/topos"))
    monkeypatch.setattr(install.os, "readlink", lambda _path: (_ for _ in ()).throw(OSError(5, "I/O error")))
    assert install._is_install_link(str(link), paths) is False


def test_prune_without_a_version_root_removes_nothing(tmp_path: Path) -> None:
    assert Installer(InstallPaths(tmp_path))._prune_versions("0.7.3") == []


# --- apply ------------------------------------------------------------------


def test_install_needs_confirmation(tmp_path: Path, monkeypatch) -> None:
    _groups(monkeypatch)
    args, _wheels = _install_args()
    with pytest.raises(InstallError, match="installation was not confirmed"):
        Installer(InstallPaths(tmp_path), geteuid=lambda: 0).install(args, _runtime(yes=False))
    assert not Path(InstallPaths(tmp_path).opt).exists()


def test_install_refuses_when_sysusers_does_not_create_the_group(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    args, wheels = _install_args(enable=False)
    _groups(monkeypatch)
    with pytest.raises(InstallError, match="systemd-sysusers did not create configured group 'topos-observe'"):
        Installer(paths, run=_fake_run([], groups={}), fetch=_fake_fetch(wheels), geteuid=lambda: 0).install(
            args, _runtime(yes=True)
        )
    # The failure came after the version tree, so PENDING marks the unfinished install.
    assert Path(paths.pending).is_file()


def test_install_adds_observe_users_and_restarts_only_what_changed(tmp_path: Path, monkeypatch) -> None:
    paths = InstallPaths(tmp_path)
    args, wheels = _install_args(add_user=["observe=alice"])
    groups = _groups(monkeypatch)
    monkeypatch.setattr(install.pwd, "getpwnam", lambda name: SimpleNamespace(pw_name=name))
    monkeypatch.setattr(Installer, "_wait_ready", lambda *_args, **_kwargs: None)
    commands = []
    subject = Installer(paths, run=_fake_run(commands, groups), fetch=_fake_fetch(wheels), geteuid=lambda: 0)
    body = subject.install(args, _runtime(yes=True))
    assert ["gpasswd", "-a", "alice", "topos-observe"] in commands
    assert not any(command[0] == "usermod" for command in commands)
    assert body["users_added"] == ["alice"]
    assert "added to topos-observe: alice (a new login session or a restarted service" in install._install_summary(body)

    active = {"topos.service": True, "topos-observe.socket": True}
    socket_unit = Path(paths.unit_dir, "topos-observe.socket")
    service_unit = Path(paths.unit_dir, "topos.service")

    # Drift in the socket unit restarts the socket; the service follows via Requires=.
    socket_unit.write_text("drift\n", encoding="utf-8")
    commands.clear()
    Installer(paths, run=_fake_run(commands, groups, active), fetch=_fake_fetch(wheels),
              geteuid=lambda: 0).install(args, _runtime(yes=True))
    assert ["systemctl", "restart", "topos-observe.socket"] in commands
    assert ["systemctl", "restart", "topos.service"] not in commands

    # Drift only in the service unit restarts the service alone.
    service_unit.write_text("drift\n", encoding="utf-8")
    commands.clear()
    Installer(paths, run=_fake_run(commands, groups, active), fetch=_fake_fetch(wheels),
              geteuid=lambda: 0).install(args, _runtime(yes=True))
    assert ["systemctl", "restart", "topos.service"] in commands
    assert ["systemctl", "restart", "topos-observe.socket"] not in commands

    # No drift: nothing restarts.
    commands.clear()
    Installer(paths, run=_fake_run(commands, groups, active), fetch=_fake_fetch(wheels),
              geteuid=lambda: 0).install(args, _runtime(yes=True))
    assert not any(command[1:2] == ["restart"] for command in commands)


# --- CLI handlers -------------------------------------------------------------


def _handler_runtime(*, dry_run=True, json_mode=False):
    printed = []
    runtime = _runtime(dry_run=dry_run, json_mode=json_mode)
    runtime.output.primary = printed.append
    return runtime, printed


def test_install_and_uninstall_handlers_print_summaries_and_map_refusals(tmp_path: Path, monkeypatch) -> None:
    from cli_extended import CliFailure

    _groups(monkeypatch)
    args, _wheels = _install_args()
    args.root = str(tmp_path)
    runtime, printed = _handler_runtime()
    assert install._install_handler(args, runtime) == 0
    assert printed == [
        f"dry run: topos 0.7.3 would be installed; no changes made; would publish host endpoint fragment "
        f"{tmp_path / 'etc/host-endpoints.d/topos.env'}"
    ]
    runtime, printed = _handler_runtime(json_mode=True)
    assert install._uninstall_handler(argparse.Namespace(root=str(tmp_path), remove_groups=False), runtime) == 0
    assert printed[0]["message"] == "nothing installed by toposd install"

    args.wheel = "bad"
    with pytest.raises(CliFailure, match="--wheel must be"):
        install._install_handler(args, _handler_runtime()[0])
    _record(InstallPaths(tmp_path), "created_groups = 5\n")
    with pytest.raises(CliFailure, match="invalid created_groups"):
        install._uninstall_handler(argparse.Namespace(root=str(tmp_path), remove_groups=False), _handler_runtime()[0])


def test_install_summary_lists_drift_and_the_enable_choice() -> None:
    body = {"version": "0.7.3", "resources_changed": ["topos.service"], "enabled": True}
    assert install._install_summary(body) == "topos 0.7.3 installed; topos.service; enabled=True"
    body["resources_changed"] = []
    assert "no resource drift" in install._install_summary(body)
