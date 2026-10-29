import pytest

from tnt.actions import Action, Kind, Plan, RpcCall, apply_plan, make_plan
from tnt.errors import Refused
from tnt.policy import SafetyState
from tnt.transport import GuardedRpc
from tnt.workflows import Workflows


class Rpc:
    def __init__(self, results):
        self.results = results
        self.calls = []

    def call(self, method, *params, job=False):
        self.calls.append((method, params, job))
        value = self.results.get(method, [])
        if isinstance(value, Exception):
            raise value
        return value

    def subscribe_event(self, name, callback):
        assert name == "container.metrics"
        callback("ADDED", fields={"5": {"cpu": {"cpu_user_percentage": 1.0, "cpu_system_percentage": 2.0}}})
        return "test-subscription"

    def unsubscribe_event(self, subscription_id):
        assert subscription_id == "test-subscription"


DATASET = {"id": "tank/home", "name": "home", "pool": "tank", "type": "FILESYSTEM", "children": [],
           "mountpoint": "/mnt/tank/home", "mounted": False, "used": {"value": "1 GiB"}, "available": {"value": "9 GiB"},
           "quota": {"value": "2 GiB"}, "refquota": {"value": "INHERIT"}, "compression": {"value": "LZ4"},
           "readonly": {"value": "OFF"}, "comments": {"value": "home"}, "vms": [], "containers": []}


def test_dataset_tree_and_show_share_the_verified_v27_queries():
    rpc = Rpc({"pool.dataset.details": [DATASET], "pool.dataset.get_instance": DATASET})
    workflows = Workflows(rpc)

    assert workflows.datasets() == [DATASET]
    assert workflows.dataset("tank/home") == DATASET
    assert rpc.calls == [
        ("pool.dataset.details", (), False),
        ("pool.dataset.get_instance", ("tank/home",), False),
    ]


def test_dataset_create_and_update_actions_use_exact_calls_and_drift_probes():
    rpc = Rpc({"pool.dataset.query": [], "pool.dataset.get_instance": DATASET})
    workflows = Workflows(rpc)

    create = workflows.dataset_create_action({"name": "tank/new", "type": "FILESYSTEM", "compression": "LZ4"})
    update = workflows.dataset_update_action("tank/home", {"readonly": "ON"})

    assert create.kind is Kind.MUTATE
    assert create.calls[0].method == "pool.dataset.create"
    assert create.calls[0].params == ({"name": "tank/new", "type": "FILESYSTEM", "compression": "LZ4"},)
    assert create.probe == (create.probe[0],) and create.probe[0].method == "pool.dataset.query"
    assert update.kind is Kind.MUTATE
    assert update.calls[0].method == "pool.dataset.update"
    assert update.calls[0].params == ("tank/home", {"readonly": "ON"})
    assert update.probe[0].method == "pool.dataset.get_instance"


@pytest.mark.parametrize(("details", "containers", "vms"), [
    ([DATASET | {"children": [{"id": "tank/home/child"}]}], [], []),
    ([DATASET | {"mounted": True}], [], []),
    ([DATASET | {"vms": [{"name": "guest", "path": "/dev/zvol/tank/home"}]}], [],
     [{"name": "guest", "status": {"state": "RUNNING"}}]),
    ([DATASET | {"containers": [{"name": "web", "path": "/mnt/tank/home"}]}],
     [{"name": "web", "status": {"state": "RUNNING"}}], []),
])
def test_dataset_delete_with_children_mount_or_running_workload_requires_recursive(details, containers, vms):
    rpc = Rpc({"pool.dataset.details": details, "container.query": containers, "vm.query": vms})

    with pytest.raises(Refused, match="--recursive"):
        Workflows(rpc).dataset_delete_action("tank/home", recursive=False)


def test_dataset_delete_requires_recursive_for_mount_but_allows_explicit_confirmed_plan():
    rpc = Rpc({"pool.dataset.details": [DATASET | {"mounted": True}], "container.query": [], "vm.query": []})
    action = Workflows(rpc).dataset_delete_action("tank/home", recursive=True)

    assert action.kind is Kind.DESTRUCTIVE
    assert action.confirm_target == "tank/home"
    assert action.calls[0].method == "pool.dataset.delete"
    assert action.calls[0].params == ("tank/home", {"recursive": True})
    assert {call.method for call in action.probe} == {"pool.dataset.details", "container.query", "vm.query"}


def test_dataset_delete_always_refuses_boot_pool():
    rpc = Rpc({"pool.dataset.details": [{"id": "boot-pool/data"}], "container.query": [], "vm.query": []})

    with pytest.raises(Refused, match="boot-pool"):
        Workflows(rpc).dataset_delete_action("boot-pool/data", recursive=True)


def test_dataset_delete_rechecks_running_usage_when_applying_the_plan():
    class ChangesAtApply:
        def __init__(self):
            self.details_calls = 0

        def call(self, method, *params, job=False):
            if method == "pool.dataset.details":
                self.details_calls += 1
                return [DATASET | {"mounted": self.details_calls >= 3}]
            if method in {"container.query", "vm.query", "pool.query", "disk.query"}:
                return []
            if method == "boot.get_state":
                return {}
            return None

        def close(self):
            pass

    rpc = GuardedRpc(ChangesAtApply(), read_only=False)
    action = Workflows(rpc).dataset_delete_action("tank/home")
    plan = make_plan(action, rpc)

    with pytest.raises(Refused, match="is mounted"):
        apply_plan(plan, rpc, allow_write=True, typed_confirmation="tank/home")


def test_dataset_delete_action_cannot_skip_usage_probes():
    action = Action("TNT-12", Kind.DESTRUCTIVE, "delete", (RpcCall("pool.dataset.delete", ("tank/home", {})),),
                    (RpcCall("pool.dataset.get_instance", ("tank/home",)),), "tank/home")
    rpc = GuardedRpc(Rpc({}), read_only=False)

    with pytest.raises(Refused, match="usage probes"):
        make_plan(action, rpc)


def test_snapshot_queries_and_mutations_use_v27_method_shapes():
    rpc = Rpc({"pool.snapshot.query": [{"id": "tank/home@nightly"}], "pool.snapshot.get_instance": {"id": "tank/home@nightly"},
               "pool.dataset.query": []})
    workflows = Workflows(rpc)

    assert workflows.snapshots("tank/home") == [{"id": "tank/home@nightly"}]
    create = workflows.snapshot_create_action({"dataset": "tank/home", "name": "nightly"})
    clone = workflows.snapshot_clone_action("tank/home@nightly", "tank/copy")
    hold = workflows.snapshot_hold_action("tank/home@nightly")
    release = workflows.snapshot_release_action("tank/home@nightly")
    rollback = workflows.snapshot_rollback_action("tank/home@nightly")
    delete = workflows.snapshot_delete_action("tank/home@nightly")

    assert create.calls[0].method == "pool.snapshot.create"
    assert create.probe[0].method == "pool.dataset.get_instance"
    assert clone.calls[0].params == ({"snapshot": "tank/home@nightly", "dataset_dst": "tank/copy"},)
    assert hold.calls[0].params == ("tank/home@nightly", {})
    assert release.calls[0].params == ("tank/home@nightly", {})
    assert rollback.kind is Kind.DESTRUCTIVE and rollback.confirm_target == "tank/home@nightly"
    assert delete.kind is Kind.DESTRUCTIVE and delete.confirm_target == "tank/home@nightly"


def test_user_workflows_mask_key_material_and_refuse_builtin_or_current_user_deletion():
    from tnt.profile import Secret

    user = {"id": 1001, "username": "alice", "full_name": "Alice", "builtin": False,
            "sshpubkey": "ssh-ed25519 AAAATEST fake-comment", "groups": [10]}
    rpc = Rpc({"user.query": [user], "auth.me": {"pw_name": "operator"}})
    workflows = Workflows(rpc)

    assert workflows.users() == [{**user, "sshpubkey": "***"}]
    update = workflows.user_update_action("alice", {"full_name": "Alice Example"})
    create = workflows.user_create_action({"username": "bob", "full_name": "Bob", "group": 10,
                                           "password": Secret("fake-secret")})
    delete = workflows.user_delete_action("alice")

    assert update.calls[0].method == "user.update" and update.calls[0].params[0] == 1001
    assert create.calls[0].method == "user.create"
    assert "fake-secret" not in create.calls[0].render()
    assert delete.kind is Kind.DESTRUCTIVE and delete.confirm_target == "alice"

    with pytest.raises(Refused, match="builtin"):
        Workflows(Rpc({"user.query": [user | {"builtin": True}], "auth.me": {"pw_name": "operator"}})).user_update_action(
            "alice", {"full_name": "changed"})
    with pytest.raises(Refused, match="currently logged in"):
        Workflows(Rpc({"user.query": [user | {"username": "operator"}], "auth.me": {"pw_name": "operator"}})).user_delete_action(
            "operator")
    with pytest.raises(Refused, match="protected password"):
        workflows.user_create_action({"username": "nopass", "full_name": "No Password", "group": 10})


def test_current_user_credential_change_needs_extra_confirmation():
    import base64
    import struct

    from tnt.profile import Secret

    key_blob = struct.pack(">I", len(b"ssh-ed25519")) + b"ssh-ed25519" + bytes(32)
    public_key = "ssh-ed25519 " + base64.b64encode(key_blob).decode() + " test"
    user = {"id": 7, "username": "operator", "full_name": "Operator", "builtin": False}
    rpc = Rpc({"user.query": [user], "auth.me": {"pw_name": "operator"}})

    with pytest.raises(Refused, match="extra confirmation"):
        Workflows(rpc).user_ssh_key_action("operator", [public_key])
    action = Workflows(rpc).user_ssh_key_action("operator", [public_key], confirm_self=True)
    assert action.calls[0].method == "user.update"
    assert action.calls[0].params[1]["sshpubkey"].reveal() == public_key

    with pytest.raises(Refused, match="extra confirmation"):
        Workflows(rpc).user_password_action("operator", Secret("fake-password"))
    self_password = Workflows(rpc).user_password_action("operator", Secret("fake-password"), confirm_self=True)
    assert self_password.kind is Kind.DESTRUCTIVE and self_password.confirm_target == "operator"
    other_password = Workflows(Rpc({"user.query": [{"id": 8, "username": "alice", "builtin": False}],
                                    "auth.me": {"pw_name": "operator"}})).user_password_action(
        "alice", Secret("fake-password"))
    assert other_password.kind is Kind.MUTATE and other_password.confirm_target is None


def test_public_key_validation_rejects_private_and_malformed_data():
    import base64
    import struct

    from tnt.workflows import validate_ssh_public_keys

    key_blob = struct.pack(">I", len(b"ssh-ed25519")) + b"ssh-ed25519" + bytes(32)
    public_key = "ssh-ed25519 " + base64.b64encode(key_blob).decode() + " laptop"
    assert validate_ssh_public_keys(public_key) == [public_key]
    with pytest.raises(Refused, match="private"):
        validate_ssh_public_keys("-----BEGIN OPENSSH PRIVATE KEY-----")
    with pytest.raises(Refused, match="base64"):
        validate_ssh_public_keys("ssh-ed25519 not-base64")


def test_name_probe_must_match_the_object_id_targeted_by_the_write():
    from tnt.workflows import validate_plan_preconditions

    probe = RpcCall("container.query", ([["name", "=", "web"]], {}))
    action = Action("TNT-39", Kind.MUTATE, "Update web", (RpcCall("container.update", (9, {"autostart": False}),),),
                    (probe,))
    with pytest.raises(Refused, match="ID does not match"):
        validate_plan_preconditions(action, [[{"id": 5, "name": "web"}]])

    matched = Action("TNT-39", Kind.MUTATE, "Update web", (RpcCall("container.update", (5, {"autostart": False}),),),
                     (probe,))
    validate_plan_preconditions(matched, [[{"id": 5, "name": "web"}]])

    broad_probe = RpcCall("container.query", ([], {}))
    broad = Action("TNT-39", Kind.MUTATE, "Update web", (RpcCall("container.update", (5, {"autostart": False}),),),
                   (broad_probe,))
    with pytest.raises(Refused, match="equality filter"):
        validate_plan_preconditions(broad, [[{"id": 5, "name": "web"}]])


def test_container_actions_resolve_names_to_ids_and_stop_needs_typed_confirmation():
    container = {"id": 5, "name": "web", "status": {"state": "RUNNING"}, "autostart": True, "cpuset": "0-1"}
    rpc = Rpc({"container.query": [container], "container.get_instance": container})
    workflows = Workflows(rpc)

    listed = workflows.containers()
    assert listed[0]["name"] == "web"
    assert listed[0]["metrics"]["cpu"] == {"cpu_user_percentage": 1.0, "cpu_system_percentage": 2.0}
    assert listed[0]["metrics_status"] == "available"
    assert listed[0]["v27_unavailable_fields"] == ["image", "cpu_count", "memory_limit"]
    update = workflows.container_update_action("web", {"autostart": False})
    stop = workflows.container_lifecycle_action("web", "stop")
    restart = workflows.container_lifecycle_action("web", "restart")
    create = workflows.container_create_action({"name": "api", "image": {"name": "nginx", "version": "latest"}})
    delete = workflows.container_delete_action("web")

    assert update.calls[0].params == (5, {"autostart": False})
    assert stop.calls[0].params == (5, {"force_after_timeout": True})
    assert restart.calls[0].params == (5, {"force_after_timeout": True})
    assert stop.kind is Kind.DESTRUCTIVE and stop.confirm_target == "web"
    assert [call.method for call in restart.calls] == ["container.stop", "container.start"]
    assert create.calls[0].job is True and restart.calls[0].job is True and delete.calls[0].job is True
    assert create.calls[0].method == "container.create"
    assert delete.kind is Kind.DESTRUCTIVE
    assert "container.get_instance" in Plan(restart, "fingerprint").render()
    assert "status.state is STOPPED" in Plan(stop, "fingerprint").render()


def test_container_metrics_timeout_and_unmatched_samples_are_not_reported_as_zero_or_available():
    class NoSamples(Rpc):
        def subscribe_event(self, name, callback):
            return "no-sample"

        def unsubscribe_event(self, subscription_id):
            pass

    row = {"id": 5, "name": "web", "status": {"state": "RUNNING"}}
    workflows = Workflows(NoSamples({"container.query": [row]}))
    assert workflows.container_metrics(timeout=0.001) == ({}, "timeout")

    class TimedOutWorkflows(Workflows):
        def container_metrics(self, *, timeout=2.5):
            return {}, "timeout"

    (listed,) = TimedOutWorkflows(NoSamples({"container.query": [row]})).containers()
    assert listed["metrics"] is None and listed["metrics_status"] == "timeout"

    class OtherContainerSample(Rpc):
        def subscribe_event(self, name, callback):
            callback("ADDED", fields={"999": {"cpu": {"cpu_user_percentage": 0.0}}})
            return "other-sample"

        def unsubscribe_event(self, subscription_id):
            pass

    (listed,) = Workflows(OtherContainerSample({"container.query": [row]})).containers()
    assert listed["metrics"] is None and listed["metrics_status"] == "no-sample"


def test_vm_create_and_device_actions_match_v27_schemas_and_refuse_boot_pool_paths():
    from tnt.workflows import Workflows

    rpc = Rpc({"vm.query": [{"id": 21, "name": "guest", "status": {"state": "STOPPED"}}],
               "vm.get_instance": {"id": 21, "name": "guest", "status": {"state": "STOPPED"}}})
    workflows = Workflows(rpc)

    create = workflows.vm_create_action({"name": "guest", "memory": 2048, "vcpus": 2})
    disk = workflows.vm_device_create_action("guest", {"dtype": "DISK", "create_zvol": True,
                                                        "zvol_name": "tank/guest-disk", "zvol_volsize": 10737418240})
    start = workflows.vm_lifecycle_action("guest", "start")
    stop = workflows.vm_lifecycle_action("guest", "stop")

    assert create.calls[0].method == "vm.create"
    assert disk.calls[0].params == ({"vm": 21, "attributes": {"dtype": "DISK", "create_zvol": True,
                                                                  "zvol_name": "tank/guest-disk", "zvol_volsize": 10737418240}},)
    assert start.calls[0].method == "vm.start"
    assert stop.calls[0].job is True
    assert stop.kind is Kind.DESTRUCTIVE and stop.confirm_target == "guest"
    with pytest.raises(Refused, match="boot-pool"):
        workflows.vm_device_create_action("guest", {"dtype": "DISK", "path": "/dev/zvol/boot-pool/root"})
