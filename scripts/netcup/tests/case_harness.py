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


def run_script_in_process(mod, argv, *, tmp_path, monkeypatch, capsys, client=None, patches=()):
    """Replay one invocation of scp-api.py or monitor-task.py.

    ``patches`` is a sequence of (attribute, value) pairs applied to ``mod``.
    """
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "load_env_file", lambda: None, raising=False)
    for name, value in patches:
        monkeypatch.setattr(mod, name, value)
    status = _status(lambda: mod.main(list(argv)))
    out = capsys.readouterr()
    return CaseRun(status, out.out, out.err, list(getattr(client, "calls", [])))


def namespace(**values) -> types.SimpleNamespace:
    return types.SimpleNamespace(**values)
