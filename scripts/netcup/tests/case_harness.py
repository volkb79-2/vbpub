"""In-process runners for the catalog-driven CLI case tests.

Every reviewed catalog case names a real invocation.  The case tests replay
that invocation through the real ``main()`` of the script with a fake Netcup
client, ``HOME`` and the working directory under ``tmp_path``, and the real
``.env`` loader switched off, so no test can reach the provider API or a real
credential.  The runners return what a case can be judged on: the exit
status, both output streams, the client calls, and (install-host) the final
parsed argument namespace.
"""
from __future__ import annotations

import json
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SERVER_RECORD = {
    "id": 42,
    "name": "v42",
    "hostname": "target.example",
    "serverLiveInfo": {"disks": [{"dev": "vda", "capacityInMiB": 524288}]},
}
CONFIG_PAYLOAD = {
    "serverId": 42,
    "hostname": "target.example",
    "imageFlavourId": 128,
    "diskName": "vda",
    "sshKeyIds": [1],
    "customScript": "echo hi",
}


@dataclass
class CaseRun:
    status: Any
    out: str
    err: str
    calls: list = field(default_factory=list)
    args: Any = None
    followers: list = field(default_factory=list)

    @property
    def methods(self) -> list[str]:
        return [call[0] for call in self.calls]


def _isolate(monkeypatch, tmp_path: Path) -> None:
    """HOME and cwd under tmp_path; no inherited Netcup configuration."""
    import os

    # The scripts write NETCUP_SCP_API_* variables into os.environ while they
    # run; give each replay a private copy so nothing leaks to other tests.
    private_env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("NETCUP_SCP_API_")
    }
    monkeypatch.setattr(os, "environ", private_env)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)


def _status(callable_) -> Any:
    try:
        return callable_()
    except SystemExit as exc:
        return exc.code


def run_install_host(mod, argv, *, tmp_path, monkeypatch, capsys, fake_client, scenario):
    """Replay one install-host invocation.

    scenario: "none" (no network expected), "file" (a valid target-host.jsonc
    in the working directory), "gather" (a wizard gather against one server).
    """
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("NETCUP_SCP_API_REFRESH_TOKEN", "fake-refresh-token")
    monkeypatch.setattr(mod, "load_env_file", lambda: None)
    monkeypatch.setattr(mod, "get_access_token", lambda _refresh: "fake-access-token")
    live = "--dry-run" not in argv
    allow = ("get", "get_user_info", "post") if live else ("get", "get_user_info")
    if scenario == "file":
        (tmp_path / "target-host.jsonc").write_text(json.dumps(CONFIG_PAYLOAD))
        client = fake_client(get_responses=[dict(SERVER_RECORD)] * 3, allow=allow)
    elif scenario == "gather":
        monkeypatch.setenv("NETCUP_SCP_API_SERVER_NAME", "v42")
        flavours = [{"id": 2, "image": {"name": "Debian 13.2 UEFI amd64"}}]
        client = fake_client(
            get_responses=[[{"id": 42, "name": "v42"}], dict(SERVER_RECORD), flavours, [], []],
            allow=allow,
        )
    elif scenario == "gather-id":
        flavours = [{"id": 2, "image": {"name": "Debian 13.2 UEFI amd64"}}]
        client = fake_client(
            get_responses=[dict(SERVER_RECORD), flavours, [], []], allow=allow
        )
    else:
        client = fake_client(allow=())
    monkeypatch.setattr(mod, "NetcupSCPClient", lambda *a, **k: client)

    # An explicit identity must name an existing file; the key itself is never
    # derived by a real ssh-keygen here.
    for flag, content in (
        ("--ssh-identity-file", "not a real private key\n"),
        ("--custom-script-file", "echo from-custom-script-file\n"),
    ):
        if flag in argv:
            (tmp_path / argv[list(argv).index(flag) + 1]).write_text(content)
    monkeypatch.setattr(
        mod,
        "_read_public_key_for_identity",
        lambda _identity: "ssh-ed25519 AAAAfakekey vbpub-test",
    )

    followers: list = []

    class StubFollower:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            followers.append(self)

        def start(self):
            return None

        def stop(self):
            return None

    def interrupt(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(mod, "_SSHCustomScriptFollower", StubFollower)
    monkeypatch.setattr(mod.time, "sleep", interrupt)

    captured: dict[str, Any] = {}
    original_body = mod._run_install_workflow_body

    def spy(args, runtime):
        captured["args"] = args
        return original_body(args, runtime)

    monkeypatch.setattr(mod, "_run_install_workflow_body", spy)
    status = _status(lambda: mod.main(list(argv)))
    out = capsys.readouterr()
    return CaseRun(status, out.out, out.err, client.calls, captured.get("args"), followers)


MAC = "aa:bb:cc:dd:ee:ff"
TASK_UUID = "3a27fe8e-e747-4f3b-80b0-f930c0d0db3f"
S42 = "/api/v1/servers/42"
POLICY_JSON = '{"name": "ssh-in", "description": "allow ssh", "rules": []}'

SCP_ROUTES = {
    "/api/v1/servers": [
        {"id": 42, "name": "v42", "hostname": "h42.example", "nickname": "n42", "disabled": False}
    ],
    S42: {
        "id": 42,
        "name": "v42",
        "hostname": "h42.example",
        "architecture": "AMD64",
        "serverLiveInfo": {
            "state": "RUNNING",
            "cpuCount": 2,
            "currentServerMemoryInMiB": 2048,
            "disks": [{"dev": "vda", "capacityInMiB": 10240}],
            "interfaces": [{"mac": MAC}],
        },
    },
    S42 + "/imageflavours": [{"id": 2, "alias": "debian", "image": {"name": "Debian 13"}}],
    S42 + "/isoimages": [
        {"id": 1234, "name": "rescue", "description": "recovery ISO", "architecture": "AMD64"}
    ],
    S42 + "/iso": {"isoAttached": False},
    S42 + "/disks": [{"name": "vda", "capacityInMiB": 10240, "storageDriver": "VIRTIO"}],
    S42 + "/disks/supported-drivers": ["VIRTIO", "SATA"],
    S42 + "/rescuesystem": {"active": False},
    S42 + "/snapshots": [{"uuid": "snap-1", "name": "before", "state": "READY"}],
    S42 + "/metrics/cpu": {"metrics": [{"cpu": 1}]},
    S42 + "/metrics/disk": {"metrics": [{"disk": 1}]},
    S42 + "/metrics/network": {"metrics": [{"network": 1}]},
    S42 + "/metrics/network/packet": {"metrics": [{"packet": 1}]},
    S42 + "/guest-agent/status": {"available": True},
    "/api/v1/tasks": [{"uuid": TASK_UUID, "name": "installImage", "state": "FINISHED"}],
    f"/api/v1/tasks/{TASK_UUID}": {"uuid": TASK_UUID, "state": "FINISHED"},
    "/api/v1/users/1/isos": [{"key": "custom.iso", "sizeInB": 3}],
    "/api/v1/users/1/isos/custom.iso/up-1/parts/1": {"url": "https://upload.invalid/part-1"},
    "/api/v1/users/1/firewall-policies": [{"id": 9, "name": "ssh-in", "rules": []}],
    f"{S42}/interfaces/{MAC}/firewall": {"active": True},
}


class RoutedClient:
    """Answers each endpoint from a fixed table and records every call.

    Calls are (method, endpoint, params-or-body).  An endpoint outside the
    table is a test failure, so a case can never reach an unplanned request.
    """

    def __init__(self, routes=None):
        self.routes = dict(SCP_ROUTES if routes is None else routes)
        self.calls: list = []

    def _answer(self, method, endpoint, detail):
        self.calls.append((method, endpoint, detail))
        if method == "get":
            if endpoint not in self.routes:
                raise AssertionError(f"unplanned GET {endpoint}")
            import copy

            return copy.deepcopy(self.routes[endpoint])
        return {}

    def get(self, endpoint, params=None):
        return self._answer("get", endpoint, params)

    def post(self, endpoint, data=None):
        if "?multipart=true" in endpoint:
            self.calls.append(("post", endpoint, data))
            return {"uploadId": "up-1"}
        if "?multipart=false" in endpoint:
            self.calls.append(("post", endpoint, data))
            return {"presignedUrl": "https://upload.invalid/object"}
        return self._answer("post", endpoint, data)

    def put(self, endpoint, data=None, params=None):
        return self._answer("put", endpoint, data)

    def patch(self, endpoint, data=None, params=None):
        return self._answer("patch", endpoint, (data, params))

    def delete(self, endpoint, params=None):
        return self._answer("delete", endpoint, params)

    def upload_file(self, url, path, offset=0, size=None):
        self.calls.append(("upload_file", url, (str(path), offset, size)))
        return {"etag": '"e-1"'}

    def get_user_info(self):
        self.calls.append(("get_user_info", "userinfo", None))
        return {"id": 1}


MUTATING = {"post", "put", "patch", "delete", "upload_file"}


def run_scp_api(mod, argv, *, tmp_path, monkeypatch, capsys, routes=None):
    """Replay one scp-api invocation against a RoutedClient."""
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("NETCUP_SCP_API_REFRESH_TOKEN", "fake-refresh-token")
    monkeypatch.setattr(mod, "load_env_file", lambda: None)
    (tmp_path / "custom.iso").write_bytes(b"iso")
    (tmp_path / "policy.json").write_text(POLICY_JSON)
    client = RoutedClient(routes)
    logins: list = []

    def fake_login(_env_path, output=None):
        logins.append(_env_path)
        return 0

    monkeypatch.setattr(mod, "build_client", lambda: client)
    monkeypatch.setattr(mod, "run_device_code_login", fake_login)
    # status would otherwise probe SSH and resolve reverse DNS.
    monkeypatch.setattr(mod, "_prepare_ssh_probe_context", lambda: {})
    monkeypatch.setattr(mod, "_ssh_connection_summary", lambda *a, **k: "no keys tried")
    monkeypatch.setattr(mod, "_reverse_dns_summary", lambda _inventory: "-")
    status = _status(lambda: mod.main(list(argv)))
    out = capsys.readouterr()
    return CaseRun(status, out.out, out.err, client.calls, None, logins)
