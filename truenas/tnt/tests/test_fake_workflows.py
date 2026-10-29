"""End-to-end workflow calls through the pinned JSON-RPC client and the in-memory v27 fake."""
import base64
import struct
from types import SimpleNamespace

import pytest

from tnt import actions as actions_module
from tnt.actions import apply_plan, make_plan
from tnt.errors import Refused
from tnt.profile import Secret
from tnt.session import open_session
from tnt.testing import FakeServer


def apply(session, action, *, confirm=None):
    plan = make_plan(action, session.rpc)
    return apply_plan(plan, session.rpc, allow_write=True, typed_confirmation=confirm)


def test_dataset_and_snapshot_workflows_use_real_client_and_v27_reply_shapes():
    with FakeServer() as server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)
        workflows = session.workflows
        try:
            assert workflows.datasets()[0]["id"] == "tank"
            apply(session, workflows.dataset_create_action({"name": "tank/home", "type": "FILESYSTEM", "compression": "LZ4"}))
            assert workflows.dataset("tank/home")["compression"] == {"value": "LZ4"}
            apply(session, workflows.dataset_update_action("tank/home", {"readonly": "ON"}))
            assert workflows.dataset("tank/home")["readonly"] == {"value": "ON"}

            apply(session, workflows.snapshot_create_action({"dataset": "tank/home", "name": "daily"}))
            assert workflows.snapshots("tank/home")[0]["id"] == "tank/home@daily"
            apply(session, workflows.snapshot_hold_action("tank/home@daily"))
            assert workflows.snapshot("tank/home@daily")["held"] is True
            apply(session, workflows.snapshot_release_action("tank/home@daily"))
            apply(session, workflows.snapshot_rollback_action("tank/home@daily"), confirm="tank/home@daily")
            apply(session, workflows.snapshot_clone_action("tank/home@daily", "tank/clone"))
            assert workflows.dataset("tank/clone")["clone_of"] == "tank/home@daily"
            apply(session, workflows.snapshot_delete_action("tank/home@daily"), confirm="tank/home@daily")
            apply(session, workflows.dataset_delete_action("tank/clone"), confirm="tank/clone")
            apply(session, workflows.dataset_delete_action("tank/home"), confirm="tank/home")
        finally:
            session.close()


def test_user_workflows_use_real_client_and_keep_keys_and_passwords_out_of_public_results():
    with FakeServer() as server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)
        workflows = session.workflows
        key_blob = struct.pack(">I", len(b"ssh-ed25519")) + b"ssh-ed25519" + bytes(32)
        public_key = "ssh-ed25519 " + base64.b64encode(key_blob).decode() + " tnt-test"
        password = Secret("fake-workflow-password")
        try:
            assert any(user["username"] == "root" and user["builtin"] for user in workflows.users())
            apply(session, workflows.user_create_action({"username": "alice", "full_name": "Alice Example", "group": 1,
                                                         "password": password}))
            apply(session, workflows.user_update_action("alice", {"full_name": "Alice Updated"}))
            apply(session, workflows.user_password_action("alice", Secret("fake-next-password")), confirm="alice")
            apply(session, workflows.user_ssh_key_action("alice", [public_key]))
            access = workflows.user_access_action("alice", {"locked": True, "ssh_password_enabled": False})
            apply(session, access, confirm="alice")
            assert workflows.user("alice")["locked"] is True
            assert workflows.user("alice")["ssh_password_enabled"] is False

            with pytest.raises(Refused, match="extra confirmation"):
                workflows.user_access_action(server.username, {"locked": True})
            self_access = workflows.user_access_action(server.username, {"locked": True}, confirm_self=True)
            apply(session, self_access, confirm=server.username)
            unlock = workflows.user_access_action(server.username, {"locked": False}, confirm_self=True)
            apply(session, unlock, confirm=server.username)
            public_user = workflows.user("alice")
            assert public_user["sshpubkey"] == "***"
            assert "fake-workflow-password" not in repr(public_user)
            assert server.user_passwords["alice"] == "fake-next-password"
            apply(session, workflows.user_delete_action("alice"), confirm="alice")
        finally:
            session.close()


def test_container_and_vm_workflows_execute_v27_jobs_and_device_calls():
    with FakeServer() as server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)
        workflows = session.workflows
        try:
            apply(session, workflows.container_create_action({"name": "web", "image": {"name": "nginx", "version": "latest"},
                                                              "pool": "tank", "autostart": True}))
            container = workflows.container("web")
            assert container["id"] == 1 and "image" not in container
            apply(session, workflows.container_update_action("web", {"autostart": False, "cpuset": "0-1"}))
            apply(session, workflows.container_lifecycle_action("web", "start"))
            assert workflows.container("web")["status"]["state"] == "RUNNING"
            listed = workflows.containers()[0]
            assert listed["metrics"]["cpu"]["cpu_user_percentage"] == 1.25
            assert listed["metrics"]["mem_usage_limit"]["mem_usage_limit_used_mib"] == 28.4
            apply(session, workflows.container_lifecycle_action("web", "restart"), confirm="web")
            apply(session, workflows.container_lifecycle_action("web", "stop"), confirm="web")
            apply(session, workflows.container_delete_action("web"), confirm="web")

            apply(session, workflows.vm_create_action({"name": "guest", "memory": 2048, "vcpus": 2}))
            apply(session, workflows.vm_device_create_action("guest", {"dtype": "DISK", "create_zvol": True,
                                                                       "zvol_name": "tank/guest-disk", "zvol_volsize": 10 * 1024 ** 3}))
            apply(session, workflows.vm_device_create_action("guest", {
                "dtype": "DISPLAY", "type": "VNC", "bind": "127.0.0.1", "port": 5901,
                "web_port": 6901, "web": True, "password": None,
            }))
            vm = workflows.vm("guest")
            assert vm["id"] == 1 and vm["devices"][0]["attributes"]["dtype"] == "DISK"
            console = workflows.vm_console("guest")
            assert console["console"] == "1_guest"
            assert console["displays"][0]["endpoint"] == "127.0.0.1:5901"
            assert console["displays"][0]["web_endpoint"] == "127.0.0.1:6901"
            assert console["web_display"]["uri"].startswith("http://127.0.0.1/")
            console_action = workflows.vm_console_action("guest")
            assert console_action.kind.value == "read"
            assert [call.method for call in console_action.calls] == [
                "vm.get_console", "vm.get_display_devices", "vm.get_display_web_uri",
            ]
            reviewed_console = apply(session, console_action)
            assert reviewed_console[0] == "1_guest"
            assert reviewed_console[1][0]["attributes"]["password_configured"] is False
            assert reviewed_console[2]["uri"].startswith("http://127.0.0.1/")
            apply(session, workflows.vm_update_action("guest", {"memory": 4096}))
            apply(session, workflows.vm_lifecycle_action("guest", "start"))
            apply(session, workflows.vm_lifecycle_action("guest", "restart"))
            apply(session, workflows.vm_lifecycle_action("guest", "stop"), confirm="guest")
            apply(session, workflows.vm_lifecycle_action("guest", "poweroff"), confirm="guest")
            apply(session, workflows.vm_delete_action("guest"), confirm="guest")
        finally:
            session.close()


def test_container_restart_waits_until_the_stop_job_reaches_stopped_state():
    with FakeServer() as server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)
        workflows = session.workflows
        try:
            apply(session, workflows.container_create_action({
                "name": "restart-lag", "image": {"name": "nginx", "version": "latest"},
                "pool": "tank", "autostart": False,
            }))
            apply(session, workflows.container_lifecycle_action("restart-lag", "start"))

            container_id = next(iter(server.containers))
            state = {"pending_queries": 0, "start_before_stopped": False}
            original_dispatch = server._dispatch

            def delayed_stop_dispatch(method, params):
                if method == "container.query" and params and params[0] == [["id", "=", container_id]]:
                    raise AssertionError("container.query does not accept id filters on v27")
                if method == "container.get_instance" and state["pending_queries"]:
                    state["pending_queries"] -= 1
                    if state["pending_queries"] == 0:
                        server.containers[container_id]["status"]["state"] = "STOPPED"
                if method == "container.start" and state["pending_queries"]:
                    state["start_before_stopped"] = True
                result = original_dispatch(method, params)
                if method == "container.stop":
                    server.containers[container_id]["status"]["state"] = "RUNNING"
                    state["pending_queries"] = 2
                return result

            server._dispatch = delayed_stop_dispatch
            apply(session, workflows.container_lifecycle_action("restart-lag", "restart"), confirm="restart-lag")
            assert state["pending_queries"] == 0
            assert not state["start_before_stopped"]
        finally:
            session.close()


@pytest.mark.parametrize("row", [
    None,
    {},
    {"status": "RUNNING"},
    {"status": {"state": None}},
    {"status": {"state": "RUNNING"}},
], ids=["non-mapping-row", "missing-status", "non-mapping-status", "non-string-state", "still-running"])
def test_container_stop_requires_a_verified_stopped_state(row, monkeypatch):
    monkeypatch.setattr(actions_module, "time", SimpleNamespace(monotonic=lambda: 0.0, sleep=lambda _delay: None))
    monkeypatch.setattr(actions_module, "_CONTAINER_STOP_TIMEOUT", 0.0)

    class Rpc:
        def call(self, method, *params):
            assert method == "container.get_instance"
            assert params == (7,)
            return row

    with pytest.raises(Refused, match="did not report STOPPED"):
        actions_module._wait_for_container_stopped(Rpc(), 7)


def test_v27_rejects_vm_names_with_non_alphanumeric_characters_before_write():
    with FakeServer() as server:
        session = open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)
        try:
            assert session.rpc.call("vm.supports_virtualization") is True
            with pytest.raises(Refused, match="ASCII letters and digits"):
                session.workflows.vm_create_action({"name": "tnt-live-invalid", "memory": 256})
            assert not any(request.get("method") == "vm.create" for request in server.requests)
        finally:
            session.close()
