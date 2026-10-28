"""Exercise every public workflow CLI branch with guarded fake sessions."""
from __future__ import annotations

import base64
import json
import struct

import pytest

from tnt.cli import build_cli
from tnt.session import Session
from tnt.adapters.v27 import V27
from tnt.transport import GuardedRpc
from tnt.profile import load_profiles


class Host:
    def __init__(self, results=None):
        self.results = dict(results or {})
        self.calls = []

    def call(self, method, *params, job=False):
        self.calls.append((method, params, job))
        value = self.results.get(method, [])
        if isinstance(value, Exception):
            raise value
        return value

    def close(self):
        pass


def opener_for(config, host):
    profiles, default = load_profiles(config)
    profile = profiles[default or next(iter(profiles))]

    def opener(_profile, *, allow_write=False, **_):
        return Session(profile, GuardedRpc(host, read_only=not allow_write), V27("TrueNAS-27.0.0-RC.1"))
    return opener


def invoke(config, host, capsys, args):
    readonly = len(args) > 1 and args[1] in {"list", "show", "console"}
    flags = ["--json"] if readonly else ["--dry-run", "--json"]
    code = build_cli(opener=opener_for(config, host)).run(argv=[*args, "--config", str(config), *flags])
    output = capsys.readouterr()
    assert code == 0, output.err
    return json.loads(output.out)


def dataset_host():
    return Host({
        "pool.dataset.details": [{"id": "tank/home", "children": [], "mounted": False,
                                  "containers": [], "vms": []}],
        "pool.dataset.get_instance": {"id": "tank/home", "type": "FILESYSTEM"},
        "pool.dataset.query": [],
        "pool.snapshot.query": [{"id": "tank/home@nightly"}],
        "pool.snapshot.get_instance": {"id": "tank/home@nightly", "dataset": "tank/home"},
        "container.query": [], "vm.query": [],
    })


def user_host():
    return Host({"user.query": [{"id": 8, "username": "alice", "full_name": "Alice",
                                 "builtin": False, "local": True, "sshpubkey": ""}],
                 "auth.me": {"pw_name": "operator"}})


def test_dataset_snapshot_read_commands_and_mutation_matrix(config, capsys):
    host = dataset_host()
    assert invoke(config, host, capsys, ["dataset", "show", "tank/home"]) == {"id": "tank/home", "type": "FILESYSTEM"}
    assert invoke(config, host, capsys, ["snapshot", "list", "tank/home"]) == [{"id": "tank/home@nightly"}]
    assert invoke(config, host, capsys, ["snapshot", "list"]) == [{"id": "tank/home@nightly"}]

    expected = {
        ("dataset", "create", "tank/new", "--type", "zvol", "--volsize", "1073741824", "--create-ancestors"):
            "pool.dataset.create",
        ("dataset", "update", "tank/home", "--readonly", "ON"):
            "pool.dataset.update",
        ("dataset", "delete", "tank/home", "--recursive"):
            "pool.dataset.delete",
        ("snapshot", "create", "tank/home", "nightly", "--recursive"):
            "pool.snapshot.create",
        ("snapshot", "clone", "tank/home@nightly", "tank/copy"):
            "pool.snapshot.clone",
        ("snapshot", "hold", "tank/home@nightly"):
            "pool.snapshot.hold",
        ("snapshot", "release", "tank/home@nightly"):
            "pool.snapshot.release",
        ("snapshot", "rollback", "tank/home@nightly"):
            "pool.snapshot.rollback",
        ("snapshot", "delete", "tank/home@nightly", "--recursive"):
            "pool.snapshot.delete",
    }
    for command, method in expected.items():
        payload = invoke(config, host, capsys, list(command))
        assert method in payload["plan"]


def test_user_read_update_delete_password_and_ssh_key_commands(config, capsys, tmp_path):
    host = user_host()
    assert invoke(config, host, capsys, ["user", "show", "alice"]) == {
        "id": 8, "username": "alice", "full_name": "Alice", "builtin": False,
        "local": True, "sshpubkey": "***",
    }

    password = tmp_path / "password"
    password.write_text("fake-password\n")
    password.chmod(0o600)
    key_type = b"ssh-ed25519"
    key = "ssh-ed25519 " + base64.b64encode(struct.pack(">I", len(key_type)) + key_type + bytes(32)).decode() + " test"
    key_file = tmp_path / "key.pub"
    key_file.write_text(key + "\n")

    commands = [
        (["user", "create", "bob", "Bob", "--group-create", "--groups", "10, 11", "--shell", "/bin/sh",
          "--home", "/mnt/tank/bob", "--home-create", "--password-disabled"], "user.create"),
        (["user", "update", "alice", "--full-name", "Alice Example", "--shell", "/bin/sh", "--home", "/mnt/tank/alice"], "user.update"),
        (["user", "access", "alice", "--groups", "10,11", "--locked"], "user.update"),
        (["user", "delete", "alice"], "user.delete"),
        (["user", "password", "alice", "--password-file", str(password)], "user.set_password"),
        (["user", "ssh-key", "set", "alice", "--file", str(key_file), "--add"], "user.update"),
        (["user", "ssh-key", "set", "alice", "--key", key], "user.update"),
        (["user", "ssh-key", "remove", "alice"], "user.update"),
    ]
    for command, method in commands:
        payload = invoke(config, host, capsys, command)
        assert method in payload["plan"]
        assert "fake-password" not in json.dumps(payload)
        assert key not in json.dumps(payload)


def test_user_access_cli_requires_current_user_and_typed_confirmation(config, capsys):
    user = {"id": 7, "username": "operator", "builtin": False, "local": True}
    host = Host({"user.query": [user], "auth.me": {"pw_name": "operator"}, "user.update": user})
    cli = build_cli(opener=opener_for(config, host))
    code = cli.run(argv=["user", "access", "operator", "--locked", "--config", str(config),
                         "--allow-write", "--confirm", "operator", "--json"])
    output = capsys.readouterr()
    assert code != 0 and "extra confirmation" in output.err + output.out
    assert not any(call[0] == "user.update" for call in host.calls)

    host.calls.clear()
    code = build_cli(opener=opener_for(config, host)).run(
        argv=["user", "access", "operator", "--locked", "--confirm-self", "--config", str(config),
              "--allow-write", "--confirm", "operator", "--json"])
    output = capsys.readouterr()
    assert code == 0, output.err
    assert any(call[0] == "user.update" and call[1][1] == {"locked": True} for call in host.calls)
    assert "[destructive]" in output.out and "operator" in output.out


@pytest.mark.parametrize(("option", "field", "value"), [
    ("--password-disabled", "password_disabled", True),
    ("--password-enabled", "password_disabled", False),
    ("--locked", "locked", True),
    ("--unlocked", "locked", False),
    ("--ssh-password-enabled", "ssh_password_enabled", True),
    ("--ssh-password-disabled", "ssh_password_enabled", False),
])
def test_user_access_cli_maps_auth_toggles_to_explicit_v27_values(config, capsys, option, field, value):
    host = user_host()
    code = build_cli(opener=opener_for(config, host)).run(
        argv=["user", "access", "alice", option, "--config", str(config), "--allow-write",
              "--confirm", "alice", "--json"])
    output = capsys.readouterr()
    assert code == 0, output.err
    call = next(call for call in host.calls if call[0] == "user.update")
    assert call[1][1] == {field: value}


def test_user_access_cli_refuses_conflicting_toggle_pairs(config, capsys):
    host = user_host()
    code = build_cli(opener=opener_for(config, host)).run(
        argv=["user", "access", "alice", "--locked", "--unlocked", "--config", str(config), "--dry-run"])
    output = capsys.readouterr()
    assert code != 0 and "choose only one of the paired locked options" in output.err + output.out
    assert not any(call[0] == "user.update" for call in host.calls)


def test_container_read_create_update_and_all_lifecycle_verbs(config, capsys, tmp_path):
    spec = tmp_path / "container.json"
    spec.write_text(json.dumps({"image": {"name": "alpine", "version": "3.20"}, "environment": {"A": "b"}}))
    host = Host({"container.query": [{"id": 4, "name": "web", "status": {"state": "STOPPED"},
                                      "autostart": False, "cpuset": None}]})
    assert invoke(config, host, capsys, ["container", "list"]) == [
        {"id": 4, "name": "web", "status": {"state": "STOPPED"}, "autostart": False,
         "cpuset": None, "metrics": None, "metrics_status": "no-running-containers",
         "v27_unavailable_fields": ["image", "cpu_count", "memory_limit"]}
    ]
    assert "web" in json.dumps(invoke(config, host, capsys, ["container", "show", "web"]))

    commands = [
        (["container", "create", "new", "--file", str(spec), "--image", "nginx", "--image-version", "1.2",
          "--pool", "tank", "--cpuset", "0-1", "--autostart"], "container.create"),
        (["container", "create", "from-file", "--file", str(spec)], "container.create"),
        (["container", "create", "plain", "--image", "busybox"], "container.create"),
        (["container", "create", "plain", "--image", "busybox", "--no-autostart"], "container.create"),
        (["container", "update", "web", "--cpuset", "1-2", "--autostart"], "container.update"),
        (["container", "update", "web", "--no-autostart"], "container.update"),
        (["container", "start", "web"], "container.start"),
        (["container", "stop", "web"], "container.stop"),
        (["container", "restart", "web"], "container.stop"),
        (["container", "delete", "web"], "container.delete"),
    ]
    for command, method in commands:
        assert method in invoke(config, host, capsys, command)["plan"]


def test_vm_read_create_update_lifecycle_console_and_device_verbs(config, capsys, tmp_path):
    vm = {"id": 21, "name": "guest", "memory": 2048, "vcpus": 2, "status": {"state": "STOPPED"},
          "devices": [], "display_available": False}
    host = Host({"vm.query": [vm], "vm.get_console": "21_guest", "vm.get_display_devices": [],
                 "vm.get_display_web_uri": {"error": None, "uri": None}, "pool.query": [],
                 "boot.get_state": {}, "disk.query": []})
    assert invoke(config, host, capsys, ["vm", "list"]) == [vm]
    assert invoke(config, host, capsys, ["vm", "show", "guest"]) == vm
    console = invoke(config, host, capsys, ["vm", "console", "guest"])
    assert console["console"] == "21_guest"
    assert "vm.get_display_web_uri" in " ".join(call[0] for call in host.calls)

    commands = [
        (["vm", "create", "new", "--memory", "2048", "--vcpus", "2", "--disk-zvol", "tank/new", "--disk-size",
          "10737418240", "--iso", "/mnt/tank/install.iso", "--nic-attach", "br0", "--autostart"], "vm.create"),
        (["vm", "create", "new-defaults", "--memory", "2048", "--disk-zvol", "tank/default", "--disk-size",
          "1073741824", "--no-autostart"], "vm.create"),
        (["vm", "update", "guest", "--memory", "4096", "--vcpus", "4", "--autostart"], "vm.update"),
        (["vm", "start", "guest"], "vm.start"), (["vm", "stop", "guest"], "vm.stop"),
        (["vm", "restart", "guest"], "vm.restart"), (["vm", "poweroff", "guest"], "vm.poweroff"),
        (["vm", "suspend", "guest"], "vm.suspend"), (["vm", "resume", "guest"], "vm.resume"),
        (["vm", "delete", "guest", "--delete-zvols"], "vm.delete"),
        (["vm", "device", "add", "guest", "--type", "iso", "--path", "/mnt/tank/install.iso"], "vm.device.create"),
        (["vm", "device", "add", "guest", "--type", "nic", "--attach", "br0"], "vm.device.create"),
        (["vm", "device", "add", "guest", "--type", "nic"], "vm.device.create"),
    ]
    for command, method in commands:
        payload = invoke(config, host, capsys, command)
        assert method in payload["plan"]

    spec = tmp_path / "device.json"
    spec.write_text(json.dumps({"dtype": "DISPLAY", "attributes": {"bind": "127.0.0.1"}}))
    assert "vm.device.create" in invoke(config, host, capsys,
                                         ["vm", "device", "add", "guest", "--file", str(spec)])["plan"]


@pytest.mark.parametrize(("command", "message"), [
    (["dataset", "create", "tank/zvol", "--type", "zvol"], "--volsize BYTES is required"),
    (["dataset", "create", "tank/fs", "--volsize", "10"], "--volsize is only valid"),
    (["dataset", "update", "tank/home"], "at least one property option"),
    (["user", "update", "alice"], "at least one user property option"),
    (["container", "create", "web"], "image.name and image.version"),
    (["vm", "create", "guest", "--memory", "2048"], "VM creation requires --disk-zvol"),
    (["vm", "update", "guest"], "at least one VM property option"),
    (["vm", "device", "add", "guest", "--type", "disk"], "--type disk requires"),
    (["vm", "device", "add", "guest", "--type", "iso"], "--type iso requires"),
])
def test_workflow_cli_refuses_incomplete_minimal_specs(config, capsys, command, message):
    host = Host({"pool.dataset.details": [{"id": "tank/home", "children": [], "mounted": False,
                                           "containers": [], "vms": []}],
                 "pool.dataset.query": [], "pool.dataset.get_instance": {"id": "tank/home"},
                 "user.query": [{"id": 8, "username": "alice", "builtin": False, "local": True}],
                 "auth.me": {"pw_name": "operator"},
                 "container.query": [{"id": 4, "name": "web", "status": {"state": "STOPPED"}}],
                 "vm.query": [{"id": 21, "name": "guest", "status": {"state": "STOPPED"}}]})
    code = build_cli(opener=opener_for(config, host)).run(argv=[*command, "--config", str(config), "--dry-run"])
    output = capsys.readouterr()
    assert code != 0
    assert message in output.err or message in output.out


def test_cli_file_json_validation_and_group_id_validation(config, capsys, tmp_path):
    malformed = tmp_path / "malformed.json"
    malformed.write_text("[")
    non_object = tmp_path / "array.json"
    non_object.write_text("[]")
    host = Host({"container.query": []})
    for path, message in ((malformed, "cannot read JSON object"), (non_object, "must contain a JSON object")):
        code = build_cli(opener=opener_for(config, host)).run(
            argv=["container", "create", "web", "--file", str(path), "--config", str(config), "--dry-run"])
        output = capsys.readouterr()
        assert code != 0 and message in output.err + output.out

    for groups in ("", "abc", "-1"):
        code = build_cli(opener=opener_for(config, user_host())).run(
            argv=["user", "create", "bob", "Bob", "--groups", groups, "--password-disabled",
                  "--config", str(config), "--dry-run"])
        output = capsys.readouterr()
        assert code != 0 and "invalid _group_ids value" in output.err + output.out


def test_secret_prompt_and_password_file_failures_are_hidden_and_clear(config, capsys, monkeypatch, tmp_path):
    from tnt import cli

    values = iter(("first", "different"))
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: next(values))
    code = build_cli(opener=opener_for(config, user_host())).run(
        argv=["user", "password", "alice", "--config", str(config), "--dry-run"])
    output = capsys.readouterr()
    assert code != 0 and "passwords do not match" in output.err + output.out
    assert "first" not in output.err + output.out

    empty = tmp_path / "empty-password"
    empty.write_text("\n")
    empty.chmod(0o600)
    host = user_host()
    code = build_cli(opener=opener_for(config, host)).run(
        argv=["user", "password", "alice", "--password-file", str(empty), "--config", str(config), "--dry-run"])
    output = capsys.readouterr()
    assert code != 0 and "password must not be empty" in output.err + output.out

    private = tmp_path / "not-private"
    private.write_text("fake")
    private.chmod(0o644)
    code = build_cli(opener=opener_for(config, user_host())).run(
        argv=["user", "password", "alice", "--password-file", str(private), "--config", str(config), "--dry-run"])
    output = capsys.readouterr()
    assert code != 0 and "password file must be private" in output.err + output.out


def test_cli_password_prompt_success_and_unreadable_password_file(config, capsys, monkeypatch, tmp_path):
    from tnt import cli

    values = iter(("matching-fake-password", "matching-fake-password"))
    monkeypatch.setattr(cli.getpass, "getpass", lambda _prompt: next(values))
    host = Host({"user.query": [], "user.create": {"username": "bob", "password": "matching-fake-password"}})
    code = build_cli(opener=opener_for(config, host)).run(
        argv=["user", "create", "bob", "Bob", "--group-create", "--groups", "10", "--config", str(config), "--dry-run", "--json"])
    output = capsys.readouterr()
    assert code == 0 and "matching-fake-password" not in output.out + output.err
    assert "user.query" in [call[0] for call in host.calls]
    assert "user.create" not in [call[0] for call in host.calls]

    missing = tmp_path / "invalid-password-file"
    missing.write_bytes(b"\xff")
    missing.chmod(0o600)
    code = build_cli(opener=opener_for(config, user_host())).run(
        argv=["user", "password", "alice", "--password-file", str(missing), "--config", str(config), "--dry-run"])
    output = capsys.readouterr()
    assert code != 0 and "cannot read password file" in output.err + output.out


def test_cli_missing_write_allow_and_ssh_key_argument_failures(config, capsys, tmp_path):
    dataset_host_ = Host({"pool.dataset.query": []})
    code = build_cli(opener=opener_for(config, dataset_host_)).run(
        argv=["dataset", "create", "tank/new", "--config", str(config)])
    output = capsys.readouterr()
    assert code != 0 and "pass --allow-write" in output.err + output.out

    host = user_host()
    cases = [
        (["user", "ssh-key", "set", "alice"], "provide exactly one"),
        (["user", "ssh-key", "set", "alice", "--file", str(tmp_path / "missing.pub")], "cannot read public key file"),
    ]
    for command, message in cases:
        code = build_cli(opener=opener_for(config, host)).run(
            argv=[*command, "--config", str(config), "--dry-run"])
        output = capsys.readouterr()
        assert code != 0 and message in output.err + output.out


def test_vm_create_apply_without_returned_id_is_reported_and_plan_is_not_hidden(config, capsys):
    host = Host({"vm.query": [], "vm.create": {}, "pool.query": [], "boot.get_state": {}, "disk.query": []})
    code = build_cli(opener=opener_for(config, host)).run(
        argv=["vm", "create", "guest", "--memory", "2048", "--disk-zvol", "tank/guest", "--disk-size", "1073741824",
              "--config", str(config), "--allow-write"])
    output = capsys.readouterr()
    assert code != 0 and "vm.create returned no VM ID" in output.err + output.out
    assert "vm.create" in output.err + output.out
