import json

from tnt.adapters.v27 import V27
from tnt.cli import build_cli
from tnt.profile import Profile
from tnt.session import Session
from tnt.transport import GuardedRpc


class Host:
    def __init__(self, results=None):
        self.results = dict(results or {})
        self.calls = []

    def call(self, method, *params, job=False):
        self.calls.append((method, params, job))
        value = self.results.get(method, {"method": method})
        if isinstance(value, Exception):
            raise value
        return value

    def close(self):
        pass


def make_opener(host):
    def opener(profile, *, allow_write=False, **_):
        return Session(profile, GuardedRpc(host, read_only=not allow_write), V27("TrueNAS-27.0.0-RC.1"))
    return opener


def test_dataset_list_json_calls_shared_details_query(capsys, config):
    host = Host({"pool.dataset.details": [{"id": "tank/home", "used": {"value": "1 GiB"}}]})
    code = build_cli(opener=make_opener(host)).run(argv=["dataset", "list", "--config", str(config), "--json"])
    out = capsys.readouterr()

    assert code == 0
    assert json.loads(out.out) == [{"id": "tank/home", "used": {"value": "1 GiB"}}]
    assert host.calls == [("pool.dataset.details", (), False)]


def test_dataset_create_dry_run_renders_api_call_without_applying(capsys, config):
    host = Host({"pool.dataset.query": []})
    code = build_cli(opener=make_opener(host)).run(argv=["dataset", "create", "tank/home", "--config", str(config),
                                                           "--dry-run", "--compression", "LZ4", "--json"])
    out = capsys.readouterr()
    payload = json.loads(out.out)

    assert code == 0 and payload["applied"] is False
    assert 'pool.dataset.create({"compression": "LZ4", "name": "tank/home", "type": "FILESYSTEM"})' in payload["plan"]
    assert "pool.dataset.create" not in [call[0] for call in host.calls]


def test_dataset_create_apply_uses_plan_gate_and_exact_calls(capsys, config):
    host = Host({"pool.dataset.query": [], "pool.query": [], "boot.get_state": {}, "disk.query": [],
                 "pool.dataset.create": {"id": "tank/home", "type": "FILESYSTEM"}})
    code = build_cli(opener=make_opener(host)).run(argv=["dataset", "create", "tank/home", "--config", str(config),
                                                           "--allow-write", "--comments", "work data", "--json"])
    out = capsys.readouterr()
    payload = json.loads(out.out)

    assert code == 0 and payload["result"] == [{"id": "tank/home", "type": "FILESYSTEM"}]
    assert [call[0] for call in host.calls].count("pool.dataset.create") == 1
    assert host.calls[-1] == ("pool.dataset.create", ({"name": "tank/home", "type": "FILESYSTEM", "comments": "work data"},), False)


def test_user_listing_masks_ssh_key_material_in_json(capsys, config):
    public_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA= laptop"
    host = Host({"user.query": [{"id": 1001, "username": "alice", "builtin": False,
                                 "sshpubkey": public_key, "unixhash": "secret-hash"}]})
    code = build_cli(opener=make_opener(host)).run(argv=["user", "list", "--config", str(config), "--json"])
    out = capsys.readouterr()
    result = json.loads(out.out)

    assert code == 0
    assert result[0]["sshpubkey"] == "***" and result[0]["unixhash"] == "***"
    assert public_key not in out.out


def test_container_create_dry_run_shows_v27_image_selector(capsys, config):
    host = Host({"container.query": []})
    code = build_cli(opener=make_opener(host)).run(argv=["container", "create", "web", "--image", "nginx",
                                                           "--config", str(config), "--dry-run", "--json"])
    out = capsys.readouterr()
    payload = json.loads(out.out)

    assert code == 0 and payload["applied"] is False
    assert 'container.create({"image": {"name": "nginx", "version": "latest"}, "name": "web"})' in payload["plan"]
    assert "container.create" not in [call[0] for call in host.calls]


def test_vm_device_add_dry_run_shows_exact_v27_device_payload(capsys, config):
    vm = {"id": 21, "name": "guest", "status": {"state": "STOPPED"}}
    host = Host({"vm.query": [vm], "vm.get_instance": vm})
    code = build_cli(opener=make_opener(host)).run(argv=["vm", "device", "add", "guest", "--type", "disk",
                                                           "--zvol", "tank/guest-disk", "--size", "10737418240",
                                                           "--config", str(config), "--dry-run", "--json"])
    out = capsys.readouterr()
    assert code == 0, out.err
    payload = json.loads(out.out)

    assert code == 0 and payload["applied"] is False
    assert 'vm.device.create({"attributes": {"create_zvol": true, "dtype": "DISK", "zvol_name": "tank/guest-disk", "zvol_volsize": 10737418240}, "vm": 21})' in payload["plan"]


def test_user_create_masks_password_in_plan_and_api_result(capsys, config, tmp_path):
    password = tmp_path / "new-password"
    password.write_text("fake-create-password\n")
    password.chmod(0o600)
    host = Host({"user.query": [], "user.create": {"username": "alice", "password": "fake-create-password"}})
    code = build_cli(opener=make_opener(host)).run(argv=["user", "create", "alice", "Alice Example", "--group", "10",
                                                           "--password-file", str(password), "--config", str(config),
                                                           "--allow-write", "--json"])
    out = capsys.readouterr()
    payload = json.loads(out.out)

    assert code == 0
    assert "fake-create-password" not in out.out
    assert "***" in payload["plan"]
    assert payload["result"] == [{"username": "alice", "password": "***"}]


def test_password_set_current_login_requires_extra_flag_and_typed_confirmation(capsys, config, tmp_path):
    password = tmp_path / "password"
    password.write_text("fake-change-password\n")
    password.chmod(0o600)
    user = {"id": 7, "username": "operator", "builtin": False}
    host = Host({"user.query": [user], "auth.me": {"pw_name": "operator"}, "user.get_instance": user})
    code = build_cli(opener=make_opener(host)).run(argv=["user", "password", "operator", "--password-file", str(password),
                                                           "--config", str(config), "--allow-write", "--confirm", "operator",
                                                           "--confirm-self", "--json"])
    out = capsys.readouterr()

    assert code == 0
    assert "fake-change-password" not in out.out
    call = next(call for call in host.calls if call[0] == "user.set_password")
    assert call[1][0]["new_password"] == "fake-change-password"


def test_password_set_for_another_user_is_a_planned_mutation_without_self_confirmation(capsys, config, tmp_path):
    password = tmp_path / "password"
    password.write_text("fake-other-password\n")
    password.chmod(0o600)
    host = Host({"user.query": [{"id": 8, "username": "alice", "builtin": False}],
                 "auth.me": {"pw_name": "operator"}, "user.set_password": {"result": True}})
    code = build_cli(opener=make_opener(host)).run(argv=["user", "password", "alice", "--password-file", str(password),
                                                           "--config", str(config), "--allow-write", "--json"])
    out = capsys.readouterr()
    payload = json.loads(out.out)

    assert code == 0
    assert "fake-other-password" not in out.out
    assert "user.set_password" in payload["plan"]
    assert next(call for call in host.calls if call[0] == "user.set_password")[1][0]["new_password"] == "fake-other-password"


def test_vm_create_applies_a_second_exact_device_plan_after_vm_create(capsys, config):
    class VMHost(Host):
        def __init__(self):
            super().__init__()
            self.vm = None
            self.devices = []

        def call(self, method, *params, job=False):
            self.calls.append((method, params, job))
            if method == "vm.query":
                filters = params[0] if params else []
                return [] if self.vm is None or filters and filters[0][2] != self.vm["name"] else [self.vm]
            if method == "vm.create":
                data = params[0]
                self.vm = {"id": 21, "name": data["name"], "memory": data["memory"], "vcpus": data.get("vcpus", 1),
                           "status": {"state": "STOPPED"}, "devices": [], "display_available": False}
                return self.vm
            if method == "vm.get_instance":
                return self.vm
            if method == "vm.device.create":
                device = {"id": len(self.devices) + 1, **params[0]}
                self.devices.append(device)
                self.vm["devices"].append(device)
                return device
            return super().call(method, *params, job=job)

    host = VMHost()
    code = build_cli(opener=make_opener(host)).run(argv=["vm", "create", "guest", "--memory", "2048", "--vcpus", "2",
                                                           "--disk-zvol", "tank/guests/guest-disk", "--disk-size", "10737418240",
                                                           "--config", str(config), "--allow-write", "--json"])
    out = capsys.readouterr()
    payload = json.loads(out.out)

    assert code == 0
    assert [call[0] for call in host.calls].count("vm.create") == 1
    assert host.calls[-1][0] == "vm.device.create"
    assert host.calls[-1][1][0]["vm"] == 21
    assert "vm.device.create" in out.err and '"vm": 21' in out.err
    assert payload["device_plans"][0]["result"][0]["attributes"]["zvol_volsize"] == 10737418240
