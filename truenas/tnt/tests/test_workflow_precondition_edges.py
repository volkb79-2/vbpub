"""Adversarial input shapes for the plan-time uniqueness and drift preconditions."""
from __future__ import annotations

import base64
import struct
from dataclasses import replace

import pytest

from tnt.actions import Action, Kind, RpcCall
from tnt.errors import Refused
from tnt.profile import Secret
from tnt.workflows import (Workflows, _attachment_names, _flatten, _host_port, _rows, _running_names,
                           validate_plan_preconditions, validate_ssh_public_keys)


def test_workflow_response_helpers_accept_only_expected_collection_shapes():
    assert list(_flatten({"id": "tank", "children": [{"id": "tank/home"}]})) == [
        {"id": "tank", "children": [{"id": "tank/home"}]}, {"id": "tank/home"}]
    assert list(_flatten(({"name": "one"}, "bad"))) == [{"name": "one"}]
    assert list(_flatten("bad")) == []
    assert _running_names(["bad", {"name": "run", "status": {"state": "running"}},
                           {"name": "suspend", "status": "SUSPENDED"}, {"name": 1, "status": "STARTING"}]) == {
                               "run", "suspend"}
    assert _attachment_names([{"name": "web"}, {"name": 2}, "bad"]) == {"web"}
    assert _attachment_names({"name": "web"}) == set()
    with pytest.raises(Refused, match="invalid row collection"):
        _rows(({"name": "not-list"},), "fake.query")


def test_ssh_key_validation_rejects_every_malformed_encoding_boundary():
    with pytest.raises(Refused, match="at least one"):
        validate_ssh_public_keys(" \n")
    with pytest.raises(Refused, match="supported OpenSSH"):
        validate_ssh_public_keys("ssh-ed25519")
    with pytest.raises(Refused, match="base64"):
        validate_ssh_public_keys("ssh-ed25519 !")
    with pytest.raises(Refused, match="too short"):
        validate_ssh_public_keys("ssh-ed25519 AAAA")
    malformed = base64.b64encode(struct.pack(">I", 100) + b"x").decode()
    with pytest.raises(Refused, match="malformed"):
        validate_ssh_public_keys(f"ssh-ed25519 {malformed}")
    other_type = base64.b64encode(struct.pack(">I", len(b"ssh-rsa")) + b"ssh-rsa" + bytes(8)).decode()
    with pytest.raises(Refused, match="does not match"):
        validate_ssh_public_keys(f"ssh-ed25519 {other_type}")
    valid = base64.b64encode(struct.pack(">I", len(b"ssh-ed25519")) + b"ssh-ed25519" + bytes(32)).decode()
    assert validate_ssh_public_keys(f"\nssh-ed25519 {valid} test\n") == [f"ssh-ed25519 {valid} test"]


def test_vm_console_endpoint_formatting_handles_ipv6_auto_ports_and_invalid_ports():
    assert _host_port(None, 5900) is None
    assert _host_port("", 5900) is None
    assert _host_port("2001:db8::1", None) == "[2001:db8::1]:auto-assigned"
    assert _host_port("[2001:db8::1]", 5900) == "[2001:db8::1]:5900"
    assert _host_port("localhost", True) is None
    assert _host_port("localhost", "5900") is None


def test_preconditions_fail_closed_for_bad_uniqueness_probes_and_existing_names():
    create = RpcCall("container.create", ({"name": "web"},))
    expected_probe = RpcCall("container.query", ([['name', '=', 'web']], {}))
    with pytest.raises(Refused, match="matching container.query"):
        validate_plan_preconditions(Action("T", Kind.MUTATE, "create", (create,), ()), [])
    with pytest.raises(Refused, match="live uniqueness probe"):
        validate_plan_preconditions(Action("T", Kind.MUTATE, "create", (create,), (expected_probe,)), [None])
    with pytest.raises(Refused, match="already exists"):
        validate_plan_preconditions(Action("T", Kind.MUTATE, "create", (create,), (expected_probe,)),
                                    [[{"id": 1, "name": "web"}]])


@pytest.mark.parametrize("probe,results,message", [
    ((), [], "one named-object probe"),
    ((RpcCall("vm.query", ([['name', '=', 'web']], {})),), [[{"id": 1, "name": "web"}]], "probe does not match"),
    ((RpcCall("container.query", ([], {})),), [[{"id": 1, "name": "web"}]], "one exact equality filter"),
    ((RpcCall("container.query", ([['id', '=', "web"]], {})),), [[{"id": 1, "name": "web"}]], "identify the exact target"),
    ((RpcCall("container.query", ([['name', '=', "web"]], {})),), [None], "did not return a row list"),
    ((RpcCall("container.query", ([['name', '=', "web"]], {})),), [[{"name": "web"}]], "exactly one current target"),
])
def test_target_probe_shape_and_identity_are_checked(probe, results, message):
    action = Action("T", Kind.MUTATE, "update", (RpcCall("container.update", (1, {"autostart": True})),), probe)
    with pytest.raises(Refused, match=message):
        validate_plan_preconditions(action, results)


def test_target_probe_rejects_mixed_families_wrong_ids_and_vm_device_ids():
    probe = RpcCall("container.query", ([['name', '=', "web"]], {}))
    mixed = Action("T", Kind.MUTATE, "mixed", (RpcCall("container.update", (1, {"autostart": True})),
                                                  RpcCall("vm.start", (1, {}))), (probe,))
    with pytest.raises(Refused, match="different object families"):
        validate_plan_preconditions(mixed, [[{"id": 1, "name": "web"}]])
    wrong_id = Action("T", Kind.MUTATE, "update", (RpcCall("container.update", (2, {"autostart": True})),), (probe,))
    with pytest.raises(Refused, match="ID does not match"):
        validate_plan_preconditions(wrong_id, [[{"id": 1, "name": "web"}]])

    vm_probe = RpcCall("vm.query", ([['name', '=', "guest"]], {}))
    bad_device = Action("T", Kind.MUTATE, "device", (RpcCall("vm.device.create", ({"vm": 2, "attributes": {"dtype": "NIC"}},)),),
                        (vm_probe,))
    with pytest.raises(Refused, match="device target does not match"):
        validate_plan_preconditions(bad_device, [[{"id": 1, "name": "guest"}]])


def test_password_preconditions_bind_username_and_current_login_classification():
    probe = RpcCall("user.query", ([['username', '=', "alice"]], {}))
    auth_probe = RpcCall("auth.me")
    bad_payload = Action("T", Kind.MUTATE, "password", (RpcCall("user.set_password", ("not-a-mapping",)),),
                         (probe, auth_probe))
    with pytest.raises(Refused, match="payload does not identify"):
        validate_plan_preconditions(bad_payload, [[{"id": 2, "username": "alice"}], {"pw_name": "operator"}])
    mismatch = Action("T", Kind.MUTATE, "password", (RpcCall("user.set_password", ({"username": "bob", "new_password": Secret("x")},)),),
                      (probe, auth_probe))
    with pytest.raises(Refused, match="password target does not match"):
        validate_plan_preconditions(mismatch, [[{"id": 2, "username": "alice"}], {"pw_name": "operator"}])


def test_share_create_preconditions_bind_canonical_path_and_unique_name():
    path = "/mnt/tank/share"
    call = RpcCall("sharing.smb.create", ({"name": "team", "path": path},))
    probes = (RpcCall("filesystem.stat", (path,)), RpcCall("sharing.smb.query", ([['name', '=', "team"]], {})))
    action = Action("T", Kind.MUTATE, "create SMB", (call,), probes)
    validate_plan_preconditions(action, [{"realpath": path}, []])

    with pytest.raises(Refused, match="remote path and uniqueness probes"):
        validate_plan_preconditions(replace(action, probe=probes[1:]), [[], []])
    with pytest.raises(Refused, match="missing, unresolved, or resolves under boot-pool"):
        validate_plan_preconditions(action, [None, []])
    with pytest.raises(Refused, match="boot-pool"):
        validate_plan_preconditions(action, [{"realpath": "/mnt/boot-pool/share"}, []])
    with pytest.raises(Refused, match="canonical path explicitly"):
        validate_plan_preconditions(action, [{"realpath": "/mnt/tank/other"}, []])
    with pytest.raises(Refused, match="live uniqueness probe"):
        validate_plan_preconditions(action, [{"realpath": path}, None])
    with pytest.raises(Refused, match="already exists"):
        validate_plan_preconditions(action, [{"realpath": path}, [{"name": "team"}]])


def test_config_export_preconditions_bind_host_state_and_exact_tar_call(tmp_path):
    path = str(tmp_path / "backup.tar")
    action = Workflows(object()).config_export_action(path)
    probes = [{"hostname": "test"}, "TrueNAS-27.0.0-RC.1", "READY", "boot-id"]
    validate_plan_preconditions(action, probes)
    with pytest.raises(Refused, match="exact v27 host"):
        validate_plan_preconditions(replace(action, probe=action.probe[:-1]), probes)
    with pytest.raises(Refused, match="valid name, version, and boot ID"):
        validate_plan_preconditions(action, [{}, *probes[1:]])
    with pytest.raises(Refused, match="valid name, version, and boot ID"):
        validate_plan_preconditions(action, [probes[0], probes[1], "BOOTING", probes[3]])
    with pytest.raises(Refused, match="title must bind"):
        validate_plan_preconditions(replace(action, title="Export config"), probes)
    changed_call = replace(action, calls=(replace(action.calls[0], params=("config.save", [{"secretseed": False}],
                                                                         "backup.tar", False)),))
    with pytest.raises(Refused, match="explicit config.save tar download"):
        validate_plan_preconditions(changed_call, probes)
    changed_target = replace(action, calls=(replace(action.calls[0], params=("config.save", [{"secretseed": True}],
                                                                            "elsewhere.tar", False)),))
    with pytest.raises(Refused, match="explicit config.save tar download"):
        validate_plan_preconditions(changed_target, probes)

    bad_delete = Action("T", Kind.DESTRUCTIVE, "delete", (RpcCall("pool.dataset.delete", ("tank/home",)),), ())
    with pytest.raises(Refused, match="invalid target or options"):
        validate_plan_preconditions(bad_delete, [])


def test_api_key_preconditions_bind_id_name_and_revoke_confirmation():
    id_probe = RpcCall("api_key.query", ([['id', '=', 5]], {}))
    rename_probe = RpcCall("api_key.query", ([['name', '=', "renamed"]], {}))
    rename = Action("T", Kind.MUTATE, "rename", (RpcCall("api_key.update", (5, {"name": "renamed", "reset": False})),),
                    (id_probe, rename_probe))
    validate_plan_preconditions(rename, [[{"id": 5, "name": "old"}], []])

    with pytest.raises(Refused, match="exact ID lookup"):
        validate_plan_preconditions(replace(rename, probe=(RpcCall("api_key.query", ([], {})), rename_probe)),
                                    [[{"id": 5, "name": "old"}], []])
    wrong_id = replace(rename, calls=(RpcCall("api_key.update", (6, {"name": "renamed", "reset": False})),))
    with pytest.raises(Refused, match="ID probe does not match"):
        validate_plan_preconditions(wrong_id, [[{"id": 5, "name": "old"}], []])
    with pytest.raises(Refused, match="did not resolve exactly one"):
        validate_plan_preconditions(rename, [[], []])
    malformed = replace(rename, calls=(RpcCall("api_key.update", (5, "unsafe")),))
    with pytest.raises(Refused, match="invalid update payload"):
        validate_plan_preconditions(malformed, [[{"id": 5, "name": "old"}], []])
    conflict = replace(rename, probe=(id_probe, rename_probe))
    with pytest.raises(Refused, match="already in use"):
        validate_plan_preconditions(conflict, [[{"id": 5, "name": "old"}], [{"id": 9, "name": "renamed"}]])

    revoke = Action("T", Kind.DESTRUCTIVE, "revoke", (RpcCall("api_key.delete", (5,)),), (id_probe,), "old")
    validate_plan_preconditions(revoke, [[{"id": 5, "name": "old"}]])
    with pytest.raises(Refused, match="typed confirmation"):
        validate_plan_preconditions(replace(revoke, confirm_target="stale"), [[{"id": 5, "name": "old"}]])

