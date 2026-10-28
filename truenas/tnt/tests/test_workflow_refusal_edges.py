"""Input and response-shape boundaries for the shared CLI/TUI workflow layer."""
from __future__ import annotations

import base64
import struct

import pytest

from tnt.actions import Action, Kind, RpcCall
from tnt.errors import Refused
from tnt.profile import Secret
from tnt.workflows import Workflows, _attachment_names, _flatten, _running_names, _safe_value, _rows, validate_plan_preconditions, validate_ssh_public_keys


class Rpc:
    def __init__(self, **results):
        self.results = results
        self.calls = []
        self.event = None
        self.unsubscribed = []

    def call(self, method, *params, job=False):
        self.calls.append((method, params, job))
        value = self.results.get(method, [])
        if isinstance(value, Exception):
            raise value
        return value

    def subscribe_event(self, name, callback):
        assert name == "container.metrics"
        if self.event:
            self.event(callback)
        return "event-1"

    def unsubscribe_event(self, subscription):
        self.unsubscribed.append(subscription)


USER = {"id": 8, "username": "alice", "full_name": "Alice", "local": True, "builtin": False,
        "sshpubkey": "old-key"}
VM = {"id": 12, "name": "guest", "status": {"state": "STOPPED"}}
CONTAINER = {"id": 7, "name": "web", "status": {"state": "STOPPED"}}
DATASET = {"id": "tank/home", "mounted": False, "children": [], "containers": [], "vms": []}


def rpc_user(user=USER, login="operator"):
    return Rpc(**{"user.query": [user], "auth.me": {"pw_name": login}})


def test_user_input_and_editability_refusals_are_specific():
    workflows = Workflows(rpc_user())
    with pytest.raises(Refused, match="username is required"):
        workflows.user_create_action({"full_name": "Alice", "group": 8, "password_disabled": True})
    with pytest.raises(Refused, match="full name is required"):
        workflows.user_create_action({"username": "alice", "group": 8, "password_disabled": True})
    with pytest.raises(Refused, match="primary group"):
        workflows.user_create_action({"username": "alice", "full_name": "Alice", "group": "8", "password_disabled": True})
    with pytest.raises(Refused, match="primary group"):
        workflows.user_create_action({"username": "alice", "full_name": "Alice", "group": True, "password_disabled": True})
    with pytest.raises(Refused, match="protected Secret"):
        workflows.user_create_action({"username": "alice", "full_name": "Alice", "group": 8, "password": "raw"})
    with pytest.raises(Refused, match="protected password"):
        workflows.user_create_action({"username": "alice", "full_name": "Alice", "group": 8})
    assert workflows.user_create_action({"username": "new", "full_name": "New", "group_create": True,
                                          "password_disabled": True}).calls[0].params[0]["group_create"] is True

    with pytest.raises(Refused, match="at least one user property"):
        workflows.user_update_action("alice", {})
    for field in ("password", "sshpubkey"):
        with pytest.raises(Refused, match="dedicated"):
            workflows.user_update_action("alice", {field: "unsafe"})
    with pytest.raises(Refused, match="allow-list"):
        workflows.user_update_action("alice", {"username": "renamed"})
    for payload in ({"random_password": True}, {"password_disabled": True}, {"locked": True},
                    {"ssh_password_enabled": False}):
        with pytest.raises(Refused, match="allow-list"):
            workflows.user_update_action("alice", payload)
    with pytest.raises(Refused, match="non-empty string"):
        workflows.user_update_action("alice", {"full_name": ""})
    with pytest.raises(Refused, match="directory-service"):
        Workflows(rpc_user(USER | {"local": False})).user_update_action("alice", {"shell": "/bin/sh"})
    with pytest.raises(Refused, match="pass --builtin"):
        Workflows(rpc_user(USER | {"builtin": True})).user_update_action("alice", {"shell": "/bin/sh"})
    with pytest.raises(Refused, match="login user could not be determined"):
        Workflows(Rpc(**{"user.query": [USER], "auth.me": {}})).user_update_action("alice", {"shell": "/bin/sh"})
    with pytest.raises(Refused, match="builtin and root/admin"):
        Workflows(rpc_user(USER | {"username": "root"})).user_delete_action("root")


def test_user_access_changes_are_separate_typed_and_current_user_guarded():
    workflows = Workflows(rpc_user())
    for values in ({"password_disabled": True}, {"locked": True}, {"ssh_password_enabled": False}):
        action = workflows.user_access_action("alice", values)
        assert action.kind is Kind.DESTRUCTIVE and action.confirm_target == "alice"
        assert action.calls[0].method == "user.update"
        assert action.calls[0].params[1] == values
        assert [call.method for call in action.probe] == ["user.query", "auth.me"]

    with pytest.raises(Refused, match="allow-list"):
        workflows.user_access_action("alice", {"random_password": True})
    with pytest.raises(Refused, match="allow-list"):
        workflows.user_access_action("alice", {"username": "renamed"})
    with pytest.raises(Refused, match="boolean"):
        workflows.user_access_action("alice", {"locked": "true"})
    with pytest.raises(Refused, match="at least one account-access"):
        workflows.user_access_action("alice", {})
    with pytest.raises(Refused, match="primary group ID"):
        workflows.user_access_action("alice", {"group": True})
    with pytest.raises(Refused, match="supplementary group IDs"):
        workflows.user_access_action("alice", {"groups": [10, True]})
    with pytest.raises(Refused, match="confirm_self must be true or false"):
        workflows.user_access_action("alice", {"locked": True}, confirm_self=1)
    with pytest.raises(Refused, match="extra confirmation"):
        Workflows(rpc_user(USER, login="alice")).user_access_action("alice", {"locked": True})
    self_action = Workflows(rpc_user(USER, login="alice")).user_access_action(
        "alice", {"locked": False}, confirm_self=True)
    assert self_action.kind is Kind.DESTRUCTIVE and self_action.confirm_target == "alice"
    self_key = Workflows(rpc_user(USER, login="alice")).user_ssh_key_action("alice", [], remove=True, confirm_self=True)
    assert self_key.kind is Kind.DESTRUCTIVE and self_key.confirm_target == "alice"
    assert [call.method for call in self_key.probe] == ["user.query", "auth.me"]
    with pytest.raises(Refused, match="built in"):
        Workflows(rpc_user(USER | {"builtin": True})).user_access_action("alice", {"locked": True})
    with pytest.raises(Refused, match="immutable"):
        Workflows(rpc_user(USER | {"immutable": True})).user_access_action("alice", {"locked": True})


def test_user_password_and_key_modes_cover_self_add_replace_and_remove():
    key_type = b"ssh-ed25519"
    key = "ssh-ed25519 " + base64.b64encode(struct.pack(">I", len(key_type)) + key_type + bytes(32)).decode() + " new"
    workflows = Workflows(rpc_user())
    with pytest.raises(Refused, match="protected Secret"):
        workflows.user_password_action("alice", "raw")
    added = workflows.user_ssh_key_action("alice", [key], replace=False)
    assert "old-key" in added.calls[0].params[1]["sshpubkey"].reveal()
    assert key in added.calls[0].params[1]["sshpubkey"].reveal()
    replaced = workflows.user_ssh_key_action("alice", [key], replace=True)
    assert replaced.calls[0].params[1]["sshpubkey"].reveal() == key
    removed = workflows.user_ssh_key_action("alice", [], remove=True)
    assert removed.calls[0].params[1]["sshpubkey"].reveal() == ""
    with pytest.raises(Refused, match="current login SSH key"):
        Workflows(rpc_user(USER, login="alice")).user_ssh_key_action("alice", [key])
    with pytest.raises(Refused, match="current login password"):
        Workflows(rpc_user(USER, login="alice")).user_password_action("alice", Secret("new-pass"))
    self_password = Workflows(rpc_user(USER, login="alice")).user_password_action(
        "alice", Secret("new-pass"), confirm_self=True)
    assert self_password.kind is Kind.DESTRUCTIVE


def test_container_response_states_and_action_input_refusals():
    no_rows = Workflows(Rpc(**{"container.query": []}))
    assert no_rows.containers() == []
    stopped = Workflows(Rpc(**{"container.query": [CONTAINER]})).containers()[0]
    assert stopped["metrics_status"] == "no-running-containers"
    unknown = Workflows(Rpc(**{"container.query": [{"id": 8, "name": "mystery", "status": {}}]})).containers()[0]
    assert unknown["metrics_status"] == "state-unknown"

    rpc = Rpc(**{"container.query": [CONTAINER]})
    workflows = Workflows(rpc)
    with pytest.raises(Refused, match="container name is required"):
        workflows.container_create_action({"image": {"name": "nginx", "version": "latest"}})
    with pytest.raises(Refused, match="image.name and image.version"):
        workflows.container_create_action({"name": "web", "image": {"name": "nginx"}})
    with pytest.raises(Refused, match="boot-pool"):
        workflows.container_create_action({"name": "web", "pool": "boot-pool", "image": {"name": "x", "version": "1"}})
    with pytest.raises(Refused, match="at least one"):
        workflows.container_update_action("web", {})
    with pytest.raises(Refused, match="memory limits are unavailable"):
        workflows.container_update_action("web", {"memory": 2})
    with pytest.raises(Refused, match="unsupported container operation"):
        workflows.container_lifecycle_action("web", "pause")
    start = workflows.container_lifecycle_action("web", "start")
    assert start.kind is Kind.MUTATE and start.calls[0].params == (7,)


def test_metrics_ignores_malformed_events_and_unsubscribes_after_callback_failure():
    rpc = Rpc()
    rpc.event = lambda callback: (callback("REMOVED", fields={"7": {"cpu": {}}}),
                                  callback("ADDED", fields=[]))
    assert Workflows(rpc).container_metrics(timeout=0.001) == ({}, "timeout")
    assert rpc.unsubscribed == ["event-1"]

    failed = Rpc(**{"container.query": [CONTAINER | {"status": {"state": "RUNNING"}}]})
    def boom(name, callback):
        raise RuntimeError("event unavailable")
    failed.subscribe_event = boom
    row = Workflows(failed).containers()[0]
    assert row["metrics_status"] == "error" and row["metrics_error"] == "event unavailable"


@pytest.mark.parametrize("attributes", [
    {"dtype": "NOPE"},
    {"dtype": "DISK", "create_zvol": True, "zvol_name": "", "zvol_volsize": 5},
    {"dtype": "DISK", "create_zvol": True, "zvol_name": "tank/x", "zvol_volsize": True},
    {"dtype": "CDROM", "path": "/iso/file.iso"},
])
def test_vm_device_schema_boundaries(attributes):
    with pytest.raises(Refused):
        Workflows.validate_vm_device_attributes(attributes)


def test_vm_action_boundaries_and_console_shapes():
    vm_rpc = Rpc(**{"vm.query": [VM]})
    workflows = Workflows(vm_rpc)
    with pytest.raises(Refused, match="VM name is required"):
        workflows.vm_create_action({"memory": 2048})
    with pytest.raises(Refused, match="excludes device lists"):
        workflows.vm_create_action({"name": "guest", "memory": 2048, "devices": []})
    for memory in (True, 19, "2048"):
        with pytest.raises(Refused, match="at least 20"):
            workflows.vm_create_action({"name": "guest", "memory": memory})
    with pytest.raises(Refused, match="at least one VM property"):
        workflows.vm_update_action("guest", {})
    with pytest.raises(Refused, match="boot-pool"):
        workflows.vm_create_action({"name": "guest", "memory": 2048, "disk": "/mnt/boot-pool/guest"})
    with pytest.raises(Refused, match="unsupported VM operation"):
        workflows.vm_lifecycle_action("guest", "pause")
    for operation in ("poweroff", "restart", "suspend", "resume"):
        assert workflows.vm_lifecycle_action("guest", operation).calls[0].method == f"vm.{operation}"
    assert workflows.vm_delete_action("guest", delete_zvols=True).calls[0].params[1]["zvols"] is True

    console_rpc = Rpc(**{
        "vm.query": [VM], "vm.get_console": "12_guest", "vm.get_display_devices": [],
        "vm.get_display_web_uri": {"error": "no display", "uri": None},
    })
    assert Workflows(console_rpc).vm_console("guest")["web_display"]["uri"] is None
    invalid = Rpc(**{"vm.query": [VM], "vm.get_console": {"bad": True}, "vm.get_display_devices": [],
                     "vm.get_display_web_uri": {"error": None, "uri": None}})
    with pytest.raises(Refused, match="response shape"):
        Workflows(invalid).vm_console("guest")
    bad_device = Rpc(**{"vm.query": [VM], "vm.get_console": "12_guest", "vm.get_display_devices": [{"id": 1}],
                        "vm.get_display_web_uri": {"error": None, "uri": None}})
    with pytest.raises(Refused, match="omitted its attributes"):
        Workflows(bad_device).vm_console("guest")


def test_dataset_snapshot_and_row_shape_boundaries():
    workflows = Workflows(Rpc(**{"pool.dataset.query": [], "pool.dataset.details": [], "container.query": [], "vm.query": []}))
    with pytest.raises(Refused, match="dataset name is required"):
        workflows.dataset_create_action({"type": "FILESYSTEM"})
    with pytest.raises(Refused, match="dataset name and at least one property"):
        workflows.dataset_update_action("", {})
    with pytest.raises(Refused, match="dataset name is required"):
        workflows.dataset_delete_action("")
    with pytest.raises(Refused, match="dataset name is required"):
        workflows.snapshot_create_action({"name": "nightly"})
    with pytest.raises(Refused, match="snapshot name is required"):
        workflows.snapshot_create_action({"dataset": "tank/home", "name": " "})
    with pytest.raises(Refused, match="boot-pool"):
        workflows.snapshot_clone_action("boot-pool/data@s", "tank/copy")
    with pytest.raises(Refused, match="invalid row collection"):
        Workflows(Rpc(**{"pool.snapshot.query": None})).snapshots()
    with pytest.raises(Refused, match="invalid row collection"):
        _rows([1], "fake.query")


def test_dataset_delete_probe_helpers_require_shape_and_match_only_live_attachments():
    from tnt.workflows import _check_dataset_delete_probe

    with pytest.raises(Refused, match="requires live dataset"):
        _check_dataset_delete_probe("tank/home", False, [])
    with pytest.raises(Refused, match="not present"):
        _check_dataset_delete_probe("tank/missing", False, [[], [], []])
    details = [DATASET | {"children": [{"id": "tank/home/child"}],
                          "containers": [{"name": "running"}, {"name": "stopped"}],
                          "vms": [{"name": "vm-running"}]}]
    containers = [{"name": "running", "status": {"state": "RUNNING"}},
                  {"name": "stopped", "status": {"state": "STOPPED"}}]
    vms = [{"name": "vm-running", "status": "SUSPENDED"}]
    with pytest.raises(Refused, match="child datasets.*running container.*running VM"):
        _check_dataset_delete_probe("tank/home", False, [details, containers, vms])
    _check_dataset_delete_probe("tank/home", True, [details, containers, vms])
    assert list(_flatten({"id": "tank", "children": [{"id": "tank/home"}]})) == [
        {"id": "tank", "children": [{"id": "tank/home"}]}, {"id": "tank/home"},
    ]
    assert _running_names([None, {"name": "web", "status": {"state": "starting"}}, {"name": "x", "status": "stopped"}]) == {"web"}
    assert _attachment_names("bad") == set()
    assert _safe_value({"password": "x", "nested": [{"sshpubkey": "y"}]}) == {
        "password": "***", "nested": [{"sshpubkey": "***"}],
    }
    assert _safe_value({"password_disabled": True, "ssh_password_enabled": False, "password": "secret"}) == {
        "password_disabled": True, "ssh_password_enabled": False, "password": "***",
    }


def test_target_probe_shapes_and_password_identity_are_rechecked():
    query = RpcCall("user.query", ([['username', '=', 'alice']], {}))
    auth = RpcCall("auth.me")
    call = RpcCall("user.set_password", ({"username": "alice", "new_password": Secret("next")},))
    current_write = Action("TNT-41", Kind.MUTATE, "bad classification", (call,), (query, auth))
    with pytest.raises(Refused, match="confirmation does not match"):
        validate_plan_preconditions(current_write, [[USER], {"pw_name": "alice"}])
    correct_write = Action("TNT-41", Kind.DESTRUCTIVE, "correct classification", (call,), (query, auth), "alice",
                           confirm_self=True)
    validate_plan_preconditions(correct_write, [[USER], {"pw_name": "alice"}])
    with pytest.raises(Refused, match="login user could not"):
        validate_plan_preconditions(correct_write, [[USER], {}])
    with pytest.raises(Refused, match="exact equality filter"):
        validate_plan_preconditions(Action("TNT-39", Kind.MUTATE, "bad", (RpcCall("container.start", (7,)),),
                                           (RpcCall("container.query", ([], {})),)), [[CONTAINER]])
