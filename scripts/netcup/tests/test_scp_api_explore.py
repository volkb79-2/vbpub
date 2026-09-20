"""Tests for scp-api.py: presentation helpers, the confirm-gate on
mutating actions, and dispatch against a FakeClient - all local, no live
netcup calls."""
from __future__ import annotations

import json
import types
from pathlib import Path

import pytest


# --- presentation helpers ---------------------------------------------------

def test_stringify_none_bool_dict(explore_mod):
    assert explore_mod._stringify(None) == ""
    assert explore_mod._stringify(True) == "yes"
    assert explore_mod._stringify(False) == "no"
    assert explore_mod._stringify(123) == "123"
    assert json.loads(explore_mod._stringify({"a": 1})) == {"a": 1}


def test_stringify_strips_control_chars_and_newlines(explore_mod):
    # A hostname/name field containing a raw ANSI escape or embedded
    # newline must never reach the terminal / break table alignment
    # (review finding, 2026-09-09).
    assert explore_mod._stringify("evil\x1b[2J\x1b[31mPWNED") == "evil[2J[31mPWNED"
    assert explore_mod._stringify("normal\nhost\ttabbed") == "normalhosttabbed"
    assert explore_mod._stringify("plain text") == "plain text"


def test_color_enabled_respects_no_color_env(explore_mod, monkeypatch):
    monkeypatch.setattr(explore_mod.sys.stdout, "isatty", lambda: True)
    monkeypatch.setenv("NO_COLOR", "1")
    assert explore_mod._color_enabled(no_color_flag=False) is False


def test_color_enabled_respects_flag(explore_mod, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(explore_mod.sys.stdout, "isatty", lambda: True)
    assert explore_mod._color_enabled(no_color_flag=True) is False


def test_color_enabled_requires_tty(explore_mod, monkeypatch):
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr(explore_mod.sys.stdout, "isatty", lambda: False)
    assert explore_mod._color_enabled(no_color_flag=False) is False
    monkeypatch.setattr(explore_mod.sys.stdout, "isatty", lambda: True)
    assert explore_mod._color_enabled(no_color_flag=False) is True


def test_palette_noop_when_disabled(explore_mod):
    pal = explore_mod._Palette(enabled=False)
    assert pal.bold("x") == "x"
    assert pal.red("x") == "x"


def test_palette_wraps_when_enabled(explore_mod):
    pal = explore_mod._Palette(enabled=True)
    assert pal.bold("x") == "\033[1mx\033[0m"


def test_print_table_empty_message(explore_mod, capsys):
    pal = explore_mod._Palette(enabled=False)
    explore_mod.print_table([], ["id", "name"], pal, "nothing here")
    assert "nothing here" in capsys.readouterr().out


def test_print_table_renders_rows(explore_mod, capsys):
    pal = explore_mod._Palette(enabled=False)
    rows = [{"id": 1, "name": "a"}, {"id": 22, "name": "bb"}]
    explore_mod.print_table(rows, ["id", "name"], pal, "empty")
    out = capsys.readouterr().out
    assert "id" in out and "name" in out
    assert "1" in out and "a" in out
    assert "22" in out and "bb" in out


def test_print_kv_nests_dicts(explore_mod, capsys):
    pal = explore_mod._Palette(enabled=False)
    explore_mod.print_kv({"active": False, "template": {"name": "X"}}, pal)
    out = capsys.readouterr().out
    assert "active" in out and "no" in out
    assert "template" in out and "name" in out and "X" in out


def test_emit_json_mode(explore_mod, capsys):
    explore_mod.emit({"a": 1}, as_json=True)
    out = capsys.readouterr().out
    assert json.loads(out) == {"a": 1}


# --- confirm() ---------------------------------------------------------------

def test_confirm_yes_flag_skips_prompt(explore_mod, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(AssertionError("should not prompt")))
    pal = explore_mod._Palette(enabled=False)
    assert explore_mod.confirm("do it?", yes=True, pal=pal) is True


@pytest.mark.parametrize("reply,expected", [("y", True), ("yes", True), ("n", False), ("", False), ("nah", False)])
def test_confirm_prompts_and_parses_reply(explore_mod, monkeypatch, reply, expected):
    monkeypatch.setattr("builtins.input", lambda *a: reply)
    pal = explore_mod._Palette(enabled=False)
    assert explore_mod.confirm("do it?", yes=False, pal=pal) is expected


# --- build_client() ------------------------------------------------------------

def test_build_client_missing_refresh_token_exits(explore_mod, monkeypatch):
    monkeypatch.delenv("NETCUP_SCP_API_REFRESH_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        explore_mod.build_client()


def test_build_client_happy_path(explore_mod, monkeypatch):
    monkeypatch.setenv("NETCUP_SCP_API_REFRESH_TOKEN", "rt")
    monkeypatch.setattr(explore_mod, "get_access_token", lambda rt: "tok")
    client = explore_mod.build_client()
    assert isinstance(client, explore_mod.NetcupSCPClient)
    assert client.access_token == "tok"
    assert client.refresh_token == "rt"


# --- subcommands against a FakeClient ------------------------------------------

def _ns(**kw):
    return types.SimpleNamespace(json=False, **kw)


def test_cmd_servers_list(explore_mod, fake_client, capsys):
    client = fake_client(get_responses=[[{"id": 1, "hostname": "h", "nickname": "n", "name": "x", "disabled": False}]])
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_servers(client, _ns(), pal)
    assert client.calls == [("get", "/api/v1/servers", None)]
    assert "h" in capsys.readouterr().out


def test_cmd_servers_rejects_non_array_api_answer(explore_mod, fake_client):
    client = fake_client(get_responses=[{"id": 1}])
    pal = explore_mod._Palette(enabled=False)
    with pytest.raises(explore_mod.ResponseShapeError, match="expected a JSON array"):
        explore_mod.cmd_servers(client, _ns(), pal)


def test_cmd_server_details(explore_mod, fake_client, capsys):
    client = fake_client(get_responses=[{"id": 1, "hostname": "h"}])
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_server_details(client, _ns(server_id=1), pal)
    assert client.calls == [("get", "/api/v1/servers/1", None)]
    assert "hostname" in capsys.readouterr().out


def test_cmd_imageflavours_flattens_nested_image_name(explore_mod, fake_client, capsys):
    client = fake_client(get_responses=[[{"id": 5, "alias": "Minimal", "image": {"name": "Debian 13"}}]])
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_imageflavours(client, _ns(server_id=1), pal)
    out = capsys.readouterr().out
    assert "Debian 13" in out
    assert "Minimal" in out


def test_cmd_imageflavours_without_id_enumerates_and_filters_all_servers(explore_mod, fake_client, capsys):
    client = fake_client(get_responses=[
        [
            {"id": 1, "name": "debian-vm", "hostname": "debian.example"},
            {"id": 2, "name": "windows-vm", "hostname": "windows.example"},
        ],
        [{"id": 5, "alias": "Debian UEFI", "image": {"name": "Debian 13"}}],
        [{"id": 9, "alias": "Windows", "image": {"name": "Windows 2022"}}],
    ])
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_imageflavours(client, _ns(server_id=None, filter="debian"), pal)
    out = capsys.readouterr().out
    assert client.calls == [
        ("get", "/api/v1/servers", None),
        ("get", "/api/v1/servers/1/imageflavours", None),
        ("get", "/api/v1/servers/2/imageflavours", None),
    ]
    assert "debian-vm" in out
    assert "Debian 13" in out
    assert "Windows 2022" not in out


def test_cmd_iso_bootable_without_id_enumerates_all_servers(explore_mod, fake_client, capsys):
    client = fake_client(get_responses=[
        [{"id": 1, "name": "first"}, {"id": 2, "name": "second"}],
        [{"id": 10, "name": "debian-installer", "description": "Debian", "architecture": "AMD64"}],
        [{"id": 11, "name": "rescue", "description": "Recovery", "architecture": "AMD64"}],
    ])
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_iso_bootable(client, _ns(server_id=None, filter=None), pal)
    out = capsys.readouterr().out
    assert "first" in out and "second" in out
    assert "debian-installer" in out and "rescue" in out
    assert "serverId" in out


def test_cmd_attached_iso_detach_declined_never_calls_delete(explore_mod, fake_client, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    client = fake_client(allow=())  # any get/post/patch/put/delete raises
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_attached_iso(client, _ns(server_id=1, action="detach", yes=False), pal)
    assert client.calls == []


def test_cmd_attached_iso_detach_with_yes_calls_delete(explore_mod, fake_client):
    client = fake_client(allow=("delete",))
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_attached_iso(client, _ns(server_id=1, action="detach", yes=True), pal)
    assert client.calls == [("delete", "/api/v1/servers/1/iso", None)]


def test_cmd_attach_iso_uses_bootable_iso_id_and_confirmation(explore_mod, fake_client, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    declined = fake_client(allow=())
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_attach_iso(
        declined,
        _ns(server_id=1, iso_id=10, user_iso_name=None, change_boot_device_to_cdrom=True, yes=False),
        pal,
    )
    assert declined.calls == []

    client = fake_client(allow=("post",))
    explore_mod.cmd_attach_iso(
        client,
        _ns(server_id=1, iso_id=10, user_iso_name=None, change_boot_device_to_cdrom=True, yes=True),
        pal,
    )
    assert client.calls == [
        ("post", "/api/v1/servers/1/iso", {"isoId": 10, "changeBootDeviceToCdrom": True})
    ]


def test_cmd_attach_iso_can_use_uploaded_iso_name(explore_mod, fake_client):
    client = fake_client(allow=("post",))
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_attach_iso(
        client,
        _ns(server_id=1, iso_id=None, user_iso_name="my-recovery.iso", change_boot_device_to_cdrom=False, yes=True),
        pal,
    )
    assert client.calls == [("post", "/api/v1/servers/1/iso", {"userIsoName": "my-recovery.iso"})]


def test_cmd_rescuesystem_deactivate_gated_by_confirm(explore_mod, fake_client, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    client = fake_client(allow=())
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_rescuesystem(client, _ns(server_id=1, action="deactivate", yes=False), pal)
    assert client.calls == []


def test_cmd_tasks_cancel_with_yes(explore_mod, fake_client):
    client = fake_client(allow=("put",))
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_tasks(client, _ns(uuid="abc", action="cancel", yes=True), pal)
    assert client.calls == [("put", "/api/v1/tasks/abc:cancel", None, None)]


def test_cmd_tasks_passes_api_filters(explore_mod, fake_client):
    client = fake_client(get_responses=[[{"uuid": "abc", "state": "RUNNING"}]])
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_tasks(
        client,
        _ns(
            uuid=None,
            action=None,
            query="install",
            server_filter_id=42,
            state="RUNNING",
            limit=10,
            offset=20,
        ),
        pal,
    )
    assert client.calls == [
        (
            "get",
            "/api/v1/tasks",
            {"q": "install", "serverId": 42, "state": "RUNNING", "limit": 10, "offset": 20},
        )
    ]


def test_cmd_tasks_rejects_filters_with_task_uuid(explore_mod, fake_client):
    client = fake_client(allow=())
    pal = explore_mod._Palette(enabled=False)
    with pytest.raises(SystemExit):
        explore_mod.cmd_tasks(
            client,
            _ns(uuid="abc", action=None, query="install", server_filter_id=None, state=None, limit=None, offset=None),
            pal,
        )
    assert client.calls == []


@pytest.mark.parametrize(
    ("metric", "endpoint"),
    [
        ("cpu", "/api/v1/servers/1/metrics/cpu"),
        ("disk", "/api/v1/servers/1/metrics/disk"),
        ("network", "/api/v1/servers/1/metrics/network"),
        ("network-packet", "/api/v1/servers/1/metrics/network/packet"),
    ],
)
def test_cmd_metrics_selects_endpoint_and_hours(explore_mod, fake_client, metric, endpoint):
    client = fake_client(get_responses=[{"2026-09-20T00:00:00Z": {"value": 1}}])
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_metrics(client, _ns(server_id=1, metric=metric, hours=24), pal)
    assert client.calls == [("get", endpoint, {"hours": 24})]


def test_cmd_guest_agent_status_reads_status(explore_mod, fake_client, capsys):
    client = fake_client(get_responses=[{"guestAgentAvailable": True}])
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_guest_agent_status(client, _ns(server_id=1), pal)
    assert client.calls == [("get", "/api/v1/servers/1/guest-agent/status", None)]
    assert "guestAgentAvailable" in capsys.readouterr().out


def test_cmd_firewall_policies_lists_user_policies(explore_mod, fake_client, capsys):
    client = fake_client(
        get_responses=[[{"id": 12, "name": "web", "description": "HTTP", "rules": [{"action": "ACCEPT"}]}]],
        user_info={"id": 99},
    )
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_firewall_policies(client, _ns(query="web", limit=10, offset=0), pal)
    assert client.calls == [
        ("get_user_info",),
        ("get", "/api/v1/users/99/firewall-policies", {"q": "web", "limit": 10, "offset": 0}),
    ]
    assert "web" in capsys.readouterr().out


def test_cmd_firewall_policies_rejects_partial_userinfo(explore_mod, fake_client):
    client = fake_client(user_info={"username": "operator"})
    pal = explore_mod._Palette(enabled=False)
    with pytest.raises(explore_mod.ResponseShapeError, match="positive integer user id"):
        explore_mod.cmd_firewall_policies(client, _ns(query=None, limit=None, offset=None), pal)


def test_cmd_firewall_policy_create_validates_before_post(explore_mod, fake_client):
    client = fake_client(allow=())
    pal = explore_mod._Palette(enabled=False)
    with pytest.raises(ValueError, match="unknown field"):
        explore_mod.cmd_firewall_policies(
            client,
            _ns(
                action="create",
                policy_id=None,
                policy_json='{"name":"ssh","unexpected":true}',
                policy_file=None,
                query=None,
                limit=None,
                offset=None,
                yes=True,
            ),
            pal,
        )
    assert client.calls == []


def test_cmd_firewall_policy_put_posts_validated_payload(explore_mod, fake_client):
    client = fake_client(allow=("get_user_info", "put"), user_info={"id": 99})
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_firewall_policies(
        client,
        _ns(
            action="put",
            policy_id=12,
            policy_json=json.dumps({
                "name": "ssh",
                "rules": [{
                    "direction": "INGRESS",
                    "protocol": "TCP",
                    "action": "DROP",
                    "destinationPorts": "22",
                }],
            }),
            policy_file=None,
            query=None,
            limit=None,
            offset=None,
            yes=True,
        ),
        pal,
    )
    assert client.calls == [
        ("get_user_info",),
        ("put", "/api/v1/users/99/firewall-policies/12", {
            "name": "ssh",
            "rules": [{
                "direction": "INGRESS",
                "protocol": "TCP",
                "action": "DROP",
                "destinationPorts": "22",
            }],
        },
        None),
    ]


def test_firewall_policy_examples_pass_local_validation(explore_mod):
    examples = Path(__file__).resolve().parent.parent / "firewall-policy-examples"
    for path in sorted(examples.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        assert explore_mod._validate_firewall_policy(document)["name"]


@pytest.mark.parametrize(
    "document",
    [
        {"name": "bad", "rules": [{"direction": "INGRESS", "protocol": "TCP"}]},
        {"name": "bad", "rules": [{"direction": "INGRESS", "protocol": "TCP", "action": "DROP", "destinationPorts": "65536"}]},
        {"name": "bad", "rules": [{"direction": "INGRESS", "protocol": "TCP", "action": "DROP", "sources": ["not-an-ip"]}]},
    ],
)
def test_firewall_policy_validation_rejects_incomplete_values(explore_mod, document):
    with pytest.raises(ValueError, match="firewall policy"):
        explore_mod._validate_firewall_policy(document)


def test_cmd_user_isos_lists_account_objects(explore_mod, fake_client, capsys):
    client = fake_client(
        get_responses=[[{"key": "recovery.iso", "sizeInB": 123, "lastModified": "now"}]],
        user_info={"id": 99},
    )
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_user_isos(client, _ns(action=None, file=None, name=None, multipart=False), pal)
    assert client.calls == [
        ("get_user_info",),
        ("get", "/api/v1/users/99/isos", None),
    ]
    assert "recovery.iso" in capsys.readouterr().out


def test_cmd_user_isos_uploads_single_part_and_does_not_expose_url(explore_mod, fake_client, tmp_path, capsys):
    iso = tmp_path / "recovery.iso"
    iso.write_bytes(b"iso-bytes")
    client = fake_client(
        allow=("get_user_info", "post", "upload_file"),
        post_responses=[{"presignedUrl": "https://objects.invalid/upload?signature=secret"}],
        user_info={"id": 99},
    )
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_user_isos(
        client,
        _ns(
            action="upload", file=str(iso), name=None, multipart=False,
            part_size_mib=64, yes=True,
        ),
        pal,
    )
    assert client.calls[:2] == [
        ("get_user_info",),
        ("post", "/api/v1/users/99/isos/recovery.iso?multipart=false", None),
    ]
    assert client.calls[2][0] == "upload_file"
    out = capsys.readouterr().out
    assert "recovery.iso" in out
    assert "presigned" not in out.lower()


def test_cmd_user_isos_uploads_multipart_and_completes_parts(explore_mod, fake_client, tmp_path):
    iso = tmp_path / "large.iso"
    iso.write_bytes(b"x" * (5 * 1024 * 1024 + 10))
    client = fake_client(
        get_responses=[
            {"url": "https://objects.invalid/part-1"},
            {"url": "https://objects.invalid/part-2"},
        ],
        post_responses=[{"uploadId": "upload/id"}],
        allow=("get", "get_user_info", "post", "upload_file", "put"),
        user_info={"id": 99},
    )
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_user_isos(
        client,
        _ns(
            action="upload", file=str(iso), name="large.iso", multipart=True,
            part_size_mib=5, yes=True,
        ),
        pal,
    )
    assert client.calls[0:2] == [
        ("get_user_info",),
        ("post", "/api/v1/users/99/isos/large.iso?multipart=true", None),
    ]
    assert client.calls[2][0:2] == (
        "get", "/api/v1/users/99/isos/large.iso/upload%2Fid/parts/1"
    )
    assert client.calls[4][0:2] == (
        "get", "/api/v1/users/99/isos/large.iso/upload%2Fid/parts/2"
    )
    assert client.calls[-1][0] == "put"
    assert client.calls[-1][1] == "/api/v1/users/99/isos/large.iso/upload%2Fid"
    assert client.calls[-1][2] == [
        {"ETag": '"fake-etag"', "partNumber": 1},
        {"ETag": '"fake-etag"', "partNumber": 2},
    ]


def test_cmd_disks_supported_drivers_rejects_malformed_answer(explore_mod, fake_client):
    client = fake_client(get_responses=[{"driver": "VIRTIO"}])
    pal = explore_mod._Palette(enabled=False)
    with pytest.raises(explore_mod.ResponseShapeError, match="expected a JSON array"):
        explore_mod.cmd_disks(client, _ns(server_id=1, action="supported-drivers"), pal)


def test_cmd_firewall_get_can_request_consistency_check(explore_mod, fake_client):
    client = fake_client(get_responses=[{"active": True}])
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_firewall(
        client,
        _ns(server_id=1, mac="aa:bb:cc:dd:ee:ff", action="get", consistency_check=True),
        pal,
    )
    assert client.calls == [
        (
            "get",
            "/api/v1/servers/1/interfaces/aa:bb:cc:dd:ee:ff/firewall",
            {"consistencyCheck": True},
        )
    ]


def test_cmd_firewall_omitted_mac_resolves_single_interface(explore_mod, fake_client):
    client = fake_client(get_responses=[
        {"serverLiveInfo": {"interfaces": [{"mac": "aa:bb:cc:dd:ee:ff"}]}},
        {"active": True},
    ])
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_firewall(
        client,
        _ns(server_id=1, mac=None, action="get", consistency_check=False),
        pal,
    )
    assert client.calls == [
        ("get", "/api/v1/servers/1", None),
        ("get", "/api/v1/servers/1/interfaces/aa:bb:cc:dd:ee:ff/firewall", None),
    ]


def test_cmd_firewall_omitted_mac_rejects_multiple_interfaces(explore_mod, fake_client, capsys):
    client = fake_client(get_responses=[{
        "serverLiveInfo": {"interfaces": [
            {"mac": "aa:bb:cc:dd:ee:ff"},
            {"mac": "11:22:33:44:55:66"},
        ]}
    }])
    pal = explore_mod._Palette(enabled=False)
    with pytest.raises(SystemExit) as exc:
        explore_mod.cmd_firewall(
            client,
            _ns(server_id=1, mac=None, action="get", consistency_check=False),
            pal,
        )
    assert exc.value.code == 2
    assert "multiple interfaces" in capsys.readouterr().err


def test_cmd_firewall_omitted_mac_rejects_partial_server_answer(explore_mod, fake_client):
    client = fake_client(get_responses=[{"id": 1, "hostname": "vm"}])
    pal = explore_mod._Palette(enabled=False)
    with pytest.raises(explore_mod.ResponseShapeError, match="did not contain serverLiveInfo.interfaces"):
        explore_mod.cmd_firewall(
            client,
            _ns(server_id=1, mac=None, action="get", consistency_check=False),
            pal,
        )


def test_cmd_firewall_set_replaces_assignments_and_is_confirmed(explore_mod, fake_client, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    declined = fake_client(allow=())
    pal = explore_mod._Palette(enabled=False)
    args = _ns(
        server_id=1,
        mac="aa:bb:cc:dd:ee:ff",
        action="set",
        consistency_check=False,
        copied_policy_ids=[3, 4],
        user_policy_ids=[8],
        active=False,
        yes=False,
    )
    explore_mod.cmd_firewall(declined, args, pal)
    assert declined.calls == []

    client = fake_client(allow=("put",))
    args.yes = True
    explore_mod.cmd_firewall(client, args, pal)
    assert client.calls == [
        (
            "put",
            "/api/v1/servers/1/interfaces/aa:bb:cc:dd:ee:ff/firewall",
            {
                "copiedPolicies": [{"id": 3}, {"id": 4}],
                "userPolicies": [{"id": 8}],
                "active": False,
            },
            None,
        )
    ]


def test_cmd_firewall_set_requires_explicit_active_state(explore_mod, fake_client):
    client = fake_client(allow=())
    pal = explore_mod._Palette(enabled=False)
    with pytest.raises(SystemExit):
        explore_mod.cmd_firewall(
            client,
            _ns(
                server_id=1,
                mac="aa:bb:cc:dd:ee:ff",
                action="set",
                consistency_check=False,
                copied_policy_ids=[],
                user_policy_ids=[],
                active=None,
                yes=True,
            ),
            pal,
        )
    assert client.calls == []


def test_cmd_snapshots_dryrun_uses_post(explore_mod, fake_client):
    client = fake_client(allow=("post",))
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_snapshots(client, _ns(server_id=1, action="dryrun", name=None, yes=False), pal)
    assert client.calls == [("post", "/api/v1/servers/1/snapshots:dryrun", {})]


def test_cmd_snapshots_create_declined_never_posts(explore_mod, fake_client, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    client = fake_client(allow=())
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_snapshots(client, _ns(server_id=1, action="create", name=None, yes=False), pal)
    assert client.calls == []


def test_cmd_snapshots_create_without_name_gets_a_default(explore_mod, fake_client):
    # ServerSnapshotCreate requires "name" server-side -- omitting --name
    # used to send {} and always fail AFTER the confirm prompt (review
    # finding, 2026-09-09).
    client = fake_client(allow=("post",))
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_snapshots(client, _ns(server_id=1, action="create", name=None, yes=True), pal)
    assert len(client.calls) == 1
    _, endpoint, payload = client.calls[0]
    assert endpoint == "/api/v1/servers/1/snapshots"
    assert payload.get("name")


def test_cmd_snapshots_create_with_explicit_name(explore_mod, fake_client):
    client = fake_client(allow=("post",))
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_snapshots(client, _ns(server_id=1, action="create", name="pre-upgrade", yes=True), pal)
    assert client.calls == [("post", "/api/v1/servers/1/snapshots", {"name": "pre-upgrade"})]


def test_cmd_tasks_cancel_without_uuid_errors_instead_of_silently_listing(explore_mod, fake_client):
    client = fake_client(allow=())
    pal = explore_mod._Palette(enabled=False)
    with pytest.raises(SystemExit):
        explore_mod.cmd_tasks(client, _ns(uuid=None, action="cancel", yes=True), pal)
    assert client.calls == []


@pytest.mark.parametrize(
    ("command", "payload", "params"),
    [
        ("on", {"state": "ON"}, None),
        ("off", {"state": "OFF"}, {"stateOption": "POWEROFF"}),
        ("cycle", {"state": "ON"}, {"stateOption": "POWERCYCLE"}),
        ("reset", {"state": "ON"}, {"stateOption": "RESET"}),
    ],
)
def test_cmd_power_actions_are_confirmed_and_use_server_patch(
    explore_mod, fake_client, monkeypatch, command, payload, params
):
    monkeypatch.setattr("builtins.input", lambda *a: "n")
    declined = fake_client(allow=())
    pal = explore_mod._Palette(enabled=False)
    explore_mod.cmd_power(
        declined,
        _ns(action=command, server_id=42, yes=False),
        pal,
    )
    assert declined.calls == []

    client = fake_client(allow=("patch",))
    explore_mod.cmd_power(client, _ns(action=command, server_id=42, yes=True), pal)
    assert client.calls == [("patch", "/api/v1/servers/42", payload, params)]


# --- main()/--help must not require a working settings file -------------------

def test_help_short_circuits_before_configure(explore_mod, monkeypatch, capsys):
    monkeypatch.setattr(explore_mod.sys, "argv", ["scp-api.py", "--help"])
    monkeypatch.setattr(
        explore_mod, "_configure",
        lambda: (_ for _ in ()).throw(AssertionError("_configure() must not run for --help")),
    )
    with pytest.raises(SystemExit) as exc:
        explore_mod.main()
    assert exc.value.code == 0
    help_out = capsys.readouterr().out
    assert "--help" in help_out
    assert "[-h]" not in help_out


def test_main_rejects_invalid_policy_before_configure(explore_mod, monkeypatch, capsys):
    monkeypatch.setattr(
        explore_mod.sys,
        "argv",
        ["scp-api.py", "firewall-policies", "create", "--policy-json", "[]"],
    )
    monkeypatch.setattr(
        explore_mod, "_configure",
        lambda: (_ for _ in ()).throw(AssertionError("settings must not load for invalid local input")),
    )
    assert explore_mod.main() == 2
    assert "must be a JSON object" in capsys.readouterr().err


def test_no_argument_prints_top_level_usage_without_required_command_error(explore_mod, monkeypatch, capsys):
    monkeypatch.setattr(explore_mod.sys, "argv", ["scp-api.py"])
    with pytest.raises(SystemExit) as exc:
        explore_mod.parse_args()
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "usage:" in err
    assert "required: command" not in err
    assert "imageflavours" in err


# --- CLI wiring ----------------------------------------------------------------

def test_parse_args_servers_no_id(explore_mod, monkeypatch):
    monkeypatch.setattr(explore_mod.sys, "argv", ["scp-api.py", "servers"])
    args = explore_mod.parse_args()
    assert args.command == "servers"
    assert not hasattr(args, "server_id")


def test_parse_args_iso_attached_detach_yes(explore_mod, monkeypatch):
    monkeypatch.setattr(explore_mod.sys, "argv", ["scp-api.py", "iso-attached", "42", "detach", "--yes"])
    args = explore_mod.parse_args()
    assert args.command == "iso-attached"
    assert args.server_id == 42
    assert args.action == "detach"
    assert args.yes is True


def test_parse_args_attach_iso(explore_mod, monkeypatch):
    monkeypatch.setattr(
        explore_mod.sys,
        "argv",
        ["scp-api.py", "attach-iso", "42", "--iso-id", "7", "--change-boot-device-to-cdrom", "--yes"],
    )
    args = explore_mod.parse_args()
    assert args.command == "attach-iso"
    assert args.server_id == 42
    assert args.iso_id == 7
    assert args.user_iso_name is None
    assert args.change_boot_device_to_cdrom is True
    assert args.yes is True


def test_parse_args_task_filters(explore_mod, monkeypatch):
    monkeypatch.setattr(
        explore_mod.sys,
        "argv",
        [
            "scp-api.py",
            "tasks",
            "--filter",
            "install",
            "--server-id",
            "42",
            "--state",
            "RUNNING",
            "--limit",
            "10",
            "--offset",
            "2",
        ],
    )
    args = explore_mod.parse_args()
    assert args.query == "install"
    assert args.server_filter_id == 42
    assert args.state == "RUNNING"
    assert args.limit == 10
    assert args.offset == 2


def test_parse_args_metrics_and_firewall(explore_mod, monkeypatch):
    monkeypatch.setattr(
        explore_mod.sys,
        "argv",
        ["scp-api.py", "metrics", "42", "network-packet", "--hours", "24"],
    )
    args = explore_mod.parse_args()
    assert args.command == "metrics"
    assert args.metric == "network-packet"
    assert args.hours == 24

    monkeypatch.setattr(
        explore_mod.sys,
        "argv",
        [
            "scp-api.py",
            "firewall",
            "42",
            "aa:bb:cc:dd:ee:ff",
            "set",
            "--user-policy-id",
            "8",
            "--inactive",
        ],
    )
    args = explore_mod.parse_args()
    assert args.command == "firewall"
    assert args.action == "set"
    assert args.user_policy_ids == [8]
    assert args.active is False

    monkeypatch.setattr(
        explore_mod.sys,
        "argv",
        ["scp-api.py", "firewall", "42", "get"],
    )
    args = explore_mod.parse_args()
    assert args.command == "firewall"
    assert args.mac is None
    assert args.action == "get"


def test_parse_args_power_groups_action_under_power(explore_mod, monkeypatch):
    monkeypatch.setattr(explore_mod.sys, "argv", ["scp-api.py", "power", "cycle", "42"])
    args = explore_mod.parse_args()
    assert args.command == "power"
    assert args.action == "cycle"
    assert args.server_id == 42


def test_parse_args_user_iso_upload(explore_mod, monkeypatch):
    monkeypatch.setattr(
        explore_mod.sys,
        "argv",
        ["scp-api.py", "user-isos", "upload", "custom.iso", "--multipart", "--part-size-mib", "8"],
    )
    args = explore_mod.parse_args()
    assert args.command == "user-isos"
    assert args.action == "upload"
    assert args.file == "custom.iso"
    assert args.multipart is True
    assert args.part_size_mib == 8


def test_parse_args_firewall_policy_put(explore_mod, monkeypatch):
    monkeypatch.setattr(
        explore_mod.sys,
        "argv",
        ["scp-api.py", "firewall-policies", "put", "12", "--policy-file", "policy.json"],
    )
    args = explore_mod.parse_args()
    assert args.command == "firewall-policies"
    assert args.action == "put"
    assert args.policy_id == 12
    assert args.policy_file == "policy.json"


def test_parse_args_accepts_filter_and_help_after_command(explore_mod, monkeypatch):
    monkeypatch.setattr(
        explore_mod.sys,
        "argv",
        ["scp-api.py", "iso-bootable", "--filter", "debian", "--json"],
    )
    args = explore_mod.parse_args()
    assert args.server_id is None
    assert args.filter == "debian"
    assert args.json is True


def test_subcommand_help_separates_actions_from_options(explore_mod, monkeypatch, capsys):
    monkeypatch.setattr(explore_mod.sys, "argv", ["scp-api.py", "snapshots", "--help"])
    with pytest.raises(SystemExit) as exc:
        explore_mod.parse_args()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "actions:" in out
    assert "{create,dryrun}" in out
    assert "--create" not in out
    assert "--yes" in out
