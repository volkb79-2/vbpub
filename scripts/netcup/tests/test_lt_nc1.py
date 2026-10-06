"""LT-NC1 regression tests: bugs and UX findings from the 2026-10-06 live
Phase 0/1 run against r1002/v1001.  Every fixture below is the response shape
actually observed live (evidence P0/ and P1/), not an invented one.  All local;
no network."""
from __future__ import annotations

import io
import json
import types
from pathlib import Path

import netcup_scp_client
import pytest
from cli_extended import CliFailure

from case_harness import S42, SCP_ROUTES, TASK_UUID, run_scp_api

NETCUP_DIR = Path(__file__).resolve().parent.parent
SPEC = json.loads((NETCUP_DIR / "netcup-scp-openapi.json").read_text(encoding="utf-8"))
SCHEMAS = SPEC["components"]["schemas"]

# P0.09c: one disk.  P0.04j: the live bootorder.
LIVE_DISKS = [{"allocationInMiB": 3126, "capacityInMiB": 524288, "name": "vda", "path": None, "storageDriver": "VIRTIO"}]
LIVE_ORDER = ["HDD", "CDROM", "NETWORK"]
DETAILS = {"id": 42, "name": "v42", "serverLiveInfo": {"bootorder": LIVE_ORDER, "state": "RUNNING"}}


def _ns(**kw):
    return types.SimpleNamespace(json=False, **kw)


def _run(mod, argv, tmp_path, monkeypatch, capsys, **routes):
    table = dict(SCP_ROUTES)
    table.update(routes)
    return run_scp_api(mod, argv, tmp_path=tmp_path, monkeypatch=monkeypatch, capsys=capsys, routes=table)


def _pal(mod):
    return mod._Palette(enabled=False)


# --- item 1: user id as a digit string (P0.15 firewall-policies / user-iso) ----

@pytest.mark.parametrize("value,expected", [("152828", 152828), (152828, 152828), ("7", 7)])
def test_scp_user_id_accepts_int_and_digit_string(explore_mod, fake_client, value, expected):
    client = fake_client(user_info={"id": value, "username": "221368"})
    assert explore_mod._scp_user_id(client) == expected


@pytest.mark.parametrize(
    "value",
    ["0", "-1", "12a", "", " 12", "1.5", "²", "٣", True, False, 0, -3, 1.5, None, [], {"a": 1},
     "9" * 5000, "1" * 20]  # over 19 digits: ResponseShapeError, never a raw ValueError
)
def test_scp_user_id_rejects_everything_else(explore_mod, fake_client, value):
    client = fake_client(user_info={"id": value})
    with pytest.raises(explore_mod.ResponseShapeError):
        explore_mod._scp_user_id(client)


def test_firewall_policies_works_with_live_string_userinfo(explore_mod, tmp_path, monkeypatch, capsys):
    class StringIdClient:
        calls = []

        def get_user_info(self):
            return {"id": "152828", "username": "221368"}

        def get(self, endpoint, params=None):
            self.calls.append(endpoint)
            return [{"id": 9, "name": "ssh-in"}]

    client = StringIdClient()
    explore_mod.cmd_firewall_policies(client, _ns(action=None, query=None, limit=None, offset=None, policy_id=None), _pal(explore_mod))
    assert client.calls == ["/api/v1/users/152828/firewall-policies"]


# --- item 2: dryrun accepts a list / object / no body (P1.1) -------------------

@pytest.mark.parametrize("answer", [[], None, {}])
def test_dryrun_empty_answer_means_snapshot_possible(explore_mod, fake_client, capsys, answer):
    client = fake_client(get_responses=[LIVE_DISKS], post_responses=[answer], allow=("get", "post"))
    explore_mod.cmd_snapshots(client, _ns(server_id=42, action="dryrun"), _pal(explore_mod))
    assert "snapshot possible" in capsys.readouterr().out


def test_dryrun_lists_blocking_reasons(explore_mod, fake_client, capsys):
    reasons = ["server is busy", {"message": "disk is locked"}]
    client = fake_client(get_responses=[LIVE_DISKS], post_responses=[reasons], allow=("get", "post"))
    explore_mod.cmd_snapshots(client, _ns(server_id=42, action="dryrun"), _pal(explore_mod))
    out = capsys.readouterr().out
    assert "not possible" in out and "server is busy" in out and "disk is locked" in out
    assert "snapshot possible" not in out


def test_dryrun_object_answer_is_rendered(explore_mod, fake_client, capsys):
    client = fake_client(get_responses=[LIVE_DISKS], post_responses=[{"possible": False}], allow=("get", "post"))
    explore_mod.cmd_snapshots(client, _ns(server_id=42, action="dryrun"), _pal(explore_mod))
    assert "possible: no" in capsys.readouterr().out


def test_dryrun_json_emits_the_raw_response(explore_mod, tmp_path, monkeypatch, capsys):
    run = _run(explore_mod, ["snapshots", "42", "dryrun", "--json"], tmp_path, monkeypatch, capsys)
    # The routed client answers every POST with {}; the raw answer is passed through untouched.
    assert run.status == 0
    assert json.loads(run.out) == {}


# --- item 3: snapshot create payload (P1.2: 422 "Disk name cannot be blank") ----

def _check_against_schema(schema_name, payload):
    schema = SCHEMAS[schema_name]
    assert set(schema.get("required", [])) <= set(payload), "required field missing"
    assert set(payload) <= set(schema["properties"]), "field not in the schema"
    for key, value in payload.items():
        wanted = schema["properties"][key]["type"]
        assert isinstance(value, {"string": str, "boolean": bool}[wanted])
        if "maxLength" in schema["properties"][key]:
            assert len(value) <= schema["properties"][key]["maxLength"]


def test_create_defaults_diskname_to_the_only_disk_and_matches_schema(explore_mod, fake_client):
    client = fake_client(get_responses=[LIVE_DISKS], allow=("get", "post"))
    explore_mod.cmd_snapshots(client, _ns(server_id=42, action="create", name="lt-pre", yes=True), _pal(explore_mod))
    _, endpoint, payload = client.calls[1]
    assert endpoint == "/api/v1/servers/42/snapshots"
    assert payload == {"name": "lt-pre", "diskName": "vda"}
    _check_against_schema("ServerSnapshotCreate", payload)


def test_create_with_every_option_matches_schema(explore_mod, fake_client):
    client = fake_client(allow=("get", "post"))
    args = _ns(server_id=42, action="create", name="n", yes=True, disk_name="vdb", online=False, description="why")
    explore_mod.cmd_snapshots(client, args, _pal(explore_mod))
    (_, _, payload), = client.calls  # explicit disk: no disks GET
    assert payload == {"name": "n", "description": "why", "diskName": "vdb"}
    _check_against_schema("ServerSnapshotCreate", payload)
    # --online and --disk-name are mutually exclusive (see test_b2_*); online alone:
    client = fake_client(allow=("post",))
    args = _ns(server_id=42, action="create", name="n", yes=True, online=True, description="why")
    explore_mod.cmd_snapshots(client, args, _pal(explore_mod))
    assert client.calls[0][2] == {"name": "n", "description": "why", "onlineSnapshot": True}
    _check_against_schema("ServerSnapshotCreate", client.calls[0][2])


def test_online_snapshot_needs_no_disk_lookup(explore_mod, fake_client):
    client = fake_client(allow=("post",))
    explore_mod.cmd_snapshots(client, _ns(server_id=42, action="create", name="n", yes=True, online=True), _pal(explore_mod))
    assert client.calls[0][2] == {"name": "n", "onlineSnapshot": True}


def test_dryrun_payload_matches_check_schema(explore_mod, fake_client):
    client = fake_client(get_responses=[LIVE_DISKS], post_responses=[[]], allow=("get", "post"))
    explore_mod.cmd_snapshots(client, _ns(server_id=42, action="dryrun", disk_name=None), _pal(explore_mod))
    _check_against_schema("ServerSnapshotCreateCheck", client.calls[1][2])


@pytest.mark.parametrize("disks", [[], [{"name": "vda"}, {"name": "vdb"}]])
def test_disk_name_is_required_unless_exactly_one_disk(explore_mod, fake_client, disks):
    client = fake_client(get_responses=[disks], allow=("get",))
    with pytest.raises(CliFailure, match="--disk-name"):
        explore_mod.cmd_snapshots(client, _ns(server_id=42, action="create", name="n", yes=True), _pal(explore_mod))
    assert [c[0] for c in client.calls] == ["get"]  # nothing was POSTed


def test_snapshot_options_exist_on_the_real_parser(explore_mod):
    args = explore_mod.parse_args(["snapshots", "42", "create", "--disk-name", "vda", "--online", "--description", "x"])
    assert (args.disk_name, args.online, args.description) == ("vda", True, "x")


# --- item 4: error output ----------------------------------------------------------

def test_verb_usage_error_is_message_plus_one_hint_not_full_help(explore_mod, tmp_path, monkeypatch, capsys):
    run = _run(explore_mod, ["firewall", "42", "set", "--yes"], tmp_path, monkeypatch, capsys)
    assert run.status == 2
    assert "firewall set requires either --active or --inactive" in run.err
    assert "Hint: run ./scp-api.py help firewall" in run.err
    assert "positional arguments" not in run.err and "usage:" not in run.err
    assert run.calls == []


def test_error_hint_does_not_override_an_explicit_hint(explore_mod, tmp_path, monkeypatch, capsys):
    run = _run(explore_mod, ["tasks", "--state", "RUNNING", TASK_UUID], tmp_path, monkeypatch, capsys)
    assert run.status == 2
    assert "task filters are only valid when listing tasks" in run.err
    assert "Hint: run ./scp-api.py help tasks" in run.err
    assert "positional arguments" not in run.err


# --- item 5: UX --------------------------------------------------------------------

FLAVOURS = [
    {"id": 128, "alias": "debian13", "image": {"name": "Debian 13.7.0 UEFI"}},
    {"id": 131, "alias": "freebsd", "image": {"name": "FreeBSD 14"}},
    {"id": 137, "alias": "ubuntu", "image": {"name": "Ubuntu 24.04"}},
]


def test_filter_does_not_match_the_id_column(explore_mod, tmp_path, monkeypatch, capsys):
    run = _run(explore_mod, ["imageflavours", "42", "--filter", "13", "--json"], tmp_path, monkeypatch, capsys,
               **{S42 + "/imageflavours": FLAVOURS})
    kept = [row["id"] for row in json.loads(run.out)]
    assert kept == [128]  # "Debian 13.7.0" and alias debian13; ids 131/137 no longer match


def test_filter_still_matches_alias_and_iso_description(explore_mod, tmp_path, monkeypatch, capsys):
    run = _run(explore_mod, ["imageflavours", "42", "--filter", "FREEBSD", "--json"], tmp_path, monkeypatch, capsys,
               **{S42 + "/imageflavours": FLAVOURS})
    assert [row["id"] for row in json.loads(run.out)] == [131]
    run = _run(explore_mod, ["iso-bootable", "42", "--filter", "netinst", "--json"], tmp_path, monkeypatch, capsys)
    assert [row["id"] for row in json.loads(run.out)] == [99]
    run = _run(explore_mod, ["iso-bootable", "42", "--filter", "99", "--json"], tmp_path, monkeypatch, capsys)
    assert json.loads(run.out) == []  # an id is not text


def test_tasks_uuid_is_validated_locally_before_any_call(explore_mod, tmp_path, monkeypatch, capsys):
    run = _run(explore_mod, ["tasks", "not-a-uuid"], tmp_path, monkeypatch, capsys)
    assert run.status == 2
    assert "not a UUID" in run.err
    assert run.calls == []
    ok = _run(explore_mod, ["tasks", TASK_UUID], tmp_path, monkeypatch, capsys)
    assert ok.status == 0 and [c[1] for c in ok.calls] == [f"/api/v1/tasks/{TASK_UUID}"]


def test_tasks_bare_cancel_keeps_its_own_message(explore_mod, tmp_path, monkeypatch, capsys):
    run = _run(explore_mod, ["tasks", "cancel"], tmp_path, monkeypatch, capsys)
    assert run.status == 2 and "cancel requires a task UUID" in run.err and run.calls == []


def test_detach_json_emits_json(explore_mod, tmp_path, monkeypatch, capsys):
    run = _run(explore_mod, ["iso-attached", "42", "detach", "--yes", "--json"], tmp_path, monkeypatch, capsys)
    assert run.status == 0
    assert json.loads(run.out) == {"detached": True, "serverId": 42}
    plain = _run(explore_mod, ["iso-attached", "42", "detach", "--yes"], tmp_path, monkeypatch, capsys)
    assert "detached" in plain.out and not plain.out.lstrip().startswith("{")


def test_consistency_check_shows_na_when_nothing_assigned(explore_mod, fake_client, capsys):
    # P0.15: active no, no policies, consistent null.
    live = {"active": False, "copiedPolicies": [], "userPolicies": [], "consistent": None}
    client = fake_client(get_responses=[dict(live)])
    args = _ns(server_id=1, mac="aa:bb:cc:dd:ee:ff", action="get", consistency_check=True,
               copied_policy_ids=[], user_policy_ids=[], active=None)
    explore_mod.cmd_firewall(client, args, _pal(explore_mod))
    assert "consistent: n/a" in capsys.readouterr().out
    # The JSON form keeps the API's null.
    client = fake_client(get_responses=[dict(live)])
    args.json = True
    explore_mod.cmd_firewall(client, args, _pal(explore_mod))
    assert json.loads(capsys.readouterr().out)["consistent"] is None


def test_consistency_check_keeps_a_real_answer(explore_mod, fake_client, capsys):
    client = fake_client(get_responses=[{"active": True, "consistent": True}])
    args = _ns(server_id=1, mac="aa:bb:cc:dd:ee:ff", action="get", consistency_check=True,
               copied_policy_ids=[], user_policy_ids=[], active=None)
    explore_mod.cmd_firewall(client, args, _pal(explore_mod))
    assert "consistent: yes" in capsys.readouterr().out


# P0.13: {timestamp: {series: number}}
LIVE_NETWORK = {
    "2026-10-05T05:10:00Z": {"2a:4b:3b:6d:61:8a IN": 240.67, "2a:4b:3b:6d:61:8a OUT": 73.08},
    "2026-10-05T05:20:00Z": {"2a:4b:3b:6d:61:8a IN": 246.98, "2a:4b:3b:6d:61:8a OUT": 73.17},
}


def test_metrics_plain_is_a_compact_table_with_units(explore_mod, tmp_path, monkeypatch, capsys):
    run = _run(explore_mod, ["metrics", "42", "network", "--hours", "24"], tmp_path, monkeypatch, capsys,
               **{S42 + "/metrics/network": LIVE_NETWORK})
    assert run.status == 0
    lines = run.out.splitlines()
    assert lines[0] == "network metrics, server 42: 2 samples, 2026-10-05T05:10:00Z .. 2026-10-05T05:20:00Z"
    header = lines[1].split()
    assert header == ["series", "unit", "min", "avg", "max", "last"]
    in_row = next(line for line in lines if line.startswith("2a:4b:3b:6d:61:8a IN")).split()
    assert in_row[2:] == ["B/s*", "240.67", "243.82", "246.98", "246.98"]
    assert "{" not in run.out  # no JSON dump


def test_metrics_json_stays_the_raw_map(explore_mod, tmp_path, monkeypatch, capsys):
    run = _run(explore_mod, ["metrics", "42", "network", "--json"], tmp_path, monkeypatch, capsys,
               **{S42 + "/metrics/network": LIVE_NETWORK})
    assert json.loads(run.out) == LIVE_NETWORK


def test_metrics_unexpected_shape_falls_back_to_json(explore_mod, capsys):
    explore_mod._print_metrics({"metrics": [1]}, "cpu", 42, _pal(explore_mod))
    assert json.loads(capsys.readouterr().out) == {"metrics": [1]}


def test_tasks_limit_zero_makes_no_request(explore_mod, tmp_path, monkeypatch, capsys):
    run = _run(explore_mod, ["tasks", "--limit", "0"], tmp_path, monkeypatch, capsys)
    assert run.status == 0 and run.calls == []
    assert "limit 0: nothing requested" in run.out
    js = _run(explore_mod, ["tasks", "--limit", "0", "--json"], tmp_path, monkeypatch, capsys)
    assert json.loads(js.out) == [] and js.calls == []
    one = _run(explore_mod, ["tasks", "--limit", "1"], tmp_path, monkeypatch, capsys)
    assert [c[1] for c in one.calls] == ["/api/v1/tasks"]


@pytest.mark.parametrize("state", ["PENDING", "RUNNING", "WAITING_FOR_CANCEL"])
def test_detach_is_refused_while_a_task_is_active(explore_mod, fake_client, state):
    # F10: the live provider answered HTTP 500 to a detach during a running attach.
    row = {"uuid": TASK_UUID, "name": "ServerAttachIsoTask", "state": state}
    order = ["PENDING", "RUNNING", "WAITING_FOR_CANCEL"]
    client = fake_client(get_responses=[[row] if state == s else [] for s in order],
                         allow=("get",))  # a DELETE would raise AssertionError
    with pytest.raises(CliFailure, match=f"is still {state}") as caught:
        explore_mod.cmd_attached_iso(client, _ns(server_id=42, action="detach", yes=True), _pal(explore_mod))
    assert TASK_UUID in str(caught.value) and "monitor-task.py watch" in caught.value.hint
    assert all(c[0] == "get" for c in client.calls)
    assert client.calls[0][2] == {"serverId": 42, "state": "PENDING"}


def test_detach_ignores_finished_rows_the_api_may_return(explore_mod, fake_client):
    done = {"uuid": TASK_UUID, "name": "ServerAttachIsoTask", "state": "FINISHED"}
    client = fake_client(get_responses=[[done], [done], [done]], allow=("get", "delete"))
    explore_mod.cmd_attached_iso(client, _ns(server_id=42, action="detach", yes=True), _pal(explore_mod))
    assert client.calls[-1][0] == "delete"
    assert [c[2]["state"] for c in client.calls if c[0] == "get"] == ["PENDING", "RUNNING", "WAITING_FOR_CANCEL"]


class _TasksErrorClient:
    """GET /tasks fails; any DELETE is recorded (it must not happen unless overridden)."""

    def __init__(self):
        self.calls = []

    def get(self, endpoint, params=None):
        self.calls.append(("get", endpoint))
        raise netcup_scp_client.HTTPStatusError(503, "service unavailable", "")

    def delete(self, endpoint, params=None):
        self.calls.append(("delete", endpoint))
        return {}


def test_detach_is_fail_closed_when_the_task_list_errors(explore_mod):
    client = _TasksErrorClient()
    with pytest.raises(CliFailure, match="503"):
        explore_mod.cmd_attached_iso(client, _ns(server_id=42, action="detach", yes=True), _pal(explore_mod))
    assert [c[0] for c in client.calls] == ["get"]  # no DELETE


def test_detach_ignore_active_tasks_overrides_but_still_confirms(explore_mod, monkeypatch, capsys):
    client = _TasksErrorClient()
    args = _ns(server_id=42, action="detach", yes=True, ignore_active_tasks=True)
    explore_mod.cmd_attached_iso(client, args, _pal(explore_mod))
    assert client.calls == [("delete", "/api/v1/servers/42/iso")]  # no GET /tasks at all
    assert "--ignore-active-tasks" in capsys.readouterr().err
    # Declining the confirmation still stops the DELETE.
    client = _TasksErrorClient()
    monkeypatch.setattr("builtins.input", lambda *_a: "n")
    args = _ns(server_id=42, action="detach", yes=False, ignore_active_tasks=True)
    explore_mod.cmd_attached_iso(client, args, _pal(explore_mod))
    assert client.calls == []


def test_ignore_active_tasks_is_a_real_option_and_warns(explore_mod, tmp_path, monkeypatch, capsys):
    run = _run(explore_mod, ["iso-attached", "42", "detach", "--yes", "--ignore-active-tasks"], tmp_path, monkeypatch, capsys)
    assert run.status == 0
    assert "--ignore-active-tasks" in run.err
    assert [c[0] for c in run.calls] == ["delete"]
    refused = _run(explore_mod, ["iso-attached", "42", "detach", "--ignore-active-tasks"], tmp_path, monkeypatch, capsys)
    assert refused.status == 2 and [c for c in refused.calls if c[0] == "delete"] == []


# --- item 5, progress clamp (F5), monitor-task ---------------------------------------

def test_monitor_progress_is_clamped_monotonic(monitor_task_mod, monkeypatch):
    import sys
    sys.path.insert(0, str(Path(__file__).parent))
    from test_monitor_task import _invoke_app, _stub_api

    mod = monitor_task_mod
    # F5: "RUNNING (100%)" then 91%, 94% on a real ISO attach.
    sequence = [100, 91, 94, 100]
    responses = [
        {"state": "RUNNING", "name": "ServerAttachIsoTask", "taskProgress": {"progressInPercent": p}}
        for p in sequence[:-1]
    ] + [{"state": "FINISHED", "name": "ServerAttachIsoTask", "taskProgress": {"progressInPercent": 100}}]
    _stub_api(monkeypatch, mod, responses)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    result = _invoke_app(mod.build_cli(), ["watch", TASK_UUID, "--poll", "1"])
    shown = [int(line.split("(")[1].split("%")[0]) for line in result.stderr.splitlines() if "Task state:" in line]
    assert shown == [100, 100]  # one RUNNING line (clamped lines are not repeated) and the FINISHED line
    assert all(b >= a for a, b in zip(shown, shown[1:]))
    assert "91" not in result.stderr and "94" not in result.stderr


def test_monitor_progress_clamp_notes_raw_value_in_debug(monitor_task_mod, monkeypatch):
    from test_monitor_task import _invoke_app, _stub_api

    mod = monitor_task_mod
    responses = [
        {"state": "RUNNING", "taskProgress": {"progressInPercent": 100}},
        {"state": "RUNNING", "taskProgress": {"progressInPercent": 91}},
        {"state": "FINISHED"},
    ]
    _stub_api(monkeypatch, mod, responses)
    monkeypatch.setattr(mod.time, "sleep", lambda _s: None)
    result = _invoke_app(mod.build_cli(), ["watch", TASK_UUID, "--poll", "1", "--debug"])
    assert "progress raw 91.00% is below the 100% already shown" in result.stderr
    assert "Task state: RUNNING (91%)" not in result.stderr


def test_monitor_progress_increasing_values_are_untouched(monitor_task_mod):
    shown, highest = monitor_task_mod._monotonic_percent(40.0, 25.0, types.SimpleNamespace())
    assert (shown, highest) == (40.0, 40.0)
    assert monitor_task_mod._monotonic_percent(None, 25.0, types.SimpleNamespace()) == (None, 25.0)


# --- item 6: boot order (spec: PATCH /servers/{id} ServerBootorderPatch) -------------

def test_boot_order_read_prints_the_live_order(explore_mod, tmp_path, monkeypatch, capsys):
    run = _run(explore_mod, ["boot-order", "42"], tmp_path, monkeypatch, capsys, **{S42: DETAILS})
    assert run.status == 0 and run.out == "HDD,CDROM,NETWORK\n"
    assert [c[0] for c in run.calls] == ["get"]
    js = _run(explore_mod, ["boot-order", "42", "--json"], tmp_path, monkeypatch, capsys, **{S42: DETAILS})
    assert json.loads(js.out) == {"serverId": 42, "bootorder": LIVE_ORDER}


def test_boot_order_set_patches_with_a_schema_valid_body(explore_mod, tmp_path, monkeypatch, capsys):
    run = _run(explore_mod, ["boot-order", "42", "set", "hdd, cdrom,network", "--yes"], tmp_path, monkeypatch, capsys,
               **{S42: DETAILS})
    assert run.status == 0
    patch = [c for c in run.calls if c[0] == "patch"]
    assert patch == [("patch", S42, ({"bootorder": ["HDD", "CDROM", "NETWORK"]}, None))]
    body = patch[0][2][0]
    schema = SCHEMAS["ServerBootorderPatch"]
    assert set(schema["required"]) <= set(body) and len(body["bootorder"]) >= schema["properties"]["bootorder"]["minItems"]
    assert set(body["bootorder"]) <= set(SCHEMAS["Bootorder"]["enum"])
    assert "boot order set to HDD,CDROM,NETWORK" in run.out


@pytest.mark.parametrize("order", ["", "HDD,,CDROM", "HDD,FLOPPY", "HDD,HDD", "cdrom,hdd,cdrom"])
def test_boot_order_set_rejects_bad_orders_before_any_call(explore_mod, tmp_path, monkeypatch, capsys, order):
    run = _run(explore_mod, ["boot-order", "42", "set", order, "--yes"], tmp_path, monkeypatch, capsys, **{S42: DETAILS})
    assert run.status == 2 and run.calls == []


def test_boot_order_set_requires_an_order(explore_mod, tmp_path, monkeypatch, capsys):
    run = _run(explore_mod, ["boot-order", "42", "set", "--yes"], tmp_path, monkeypatch, capsys)
    assert run.status == 2 and "requires an ORDER" in run.err and run.calls == []


def test_boot_order_set_needs_consent(explore_mod, tmp_path, monkeypatch, capsys):
    run = _run(explore_mod, ["boot-order", "42", "set", "HDD"], tmp_path, monkeypatch, capsys, **{S42: DETAILS})
    assert run.status == 2 and "confirmation is required" in run.err
    assert [c for c in run.calls if c[0] == "patch"] == []


def test_boot_order_set_honours_the_protected_server_guard(explore_mod, fake_client, monkeypatch):
    monkeypatch.setenv("NETCUP_SCP_API_PROTECTED_SERVERS", "v2202503209318326780")
    client = fake_client(get_responses=[{"id": 1, "name": "v2202503209318326780"}], allow=("get", "patch"))
    with pytest.raises(explore_mod.netcup_scp_client.ProtectedServerError):
        explore_mod.cmd_boot_order(client, _ns(server_id=1, action="set", order="HDD", yes=True), _pal(explore_mod))
    assert [c[0] for c in client.calls] == ["get"]  # refused before the PATCH


def test_boot_order_is_registered_as_mutating(explore_mod):
    assert "boot-order" in explore_mod.build_cli().command_parsers


def test_attach_with_cdrom_boot_prints_the_previous_order_and_restore_command(explore_mod, tmp_path, monkeypatch, capsys):
    # F9: HDD,CDROM,NETWORK became HDD,NETWORK,CDROM after attach + detach.
    run = _run(explore_mod, ["attach-iso", "42", "--iso-id", "84", "--change-boot-device-to-cdrom", "--yes"],
               tmp_path, monkeypatch, capsys, **{S42: DETAILS})
    assert run.status == 0
    assert "previous boot order: HDD,CDROM,NETWORK" in run.err
    assert "./scp-api.py boot-order 42 set HDD,CDROM,NETWORK" in run.err
    assert [c[0] for c in run.calls].index("get") < [c[0] for c in run.calls].index("post")


def test_attach_without_the_boot_flag_reads_no_boot_order(explore_mod, tmp_path, monkeypatch, capsys):
    run = _run(explore_mod, ["attach-iso", "42", "--iso-id", "84", "--yes"], tmp_path, monkeypatch, capsys)
    assert [c[0] for c in run.calls] == ["post"]
    assert "boot order" not in run.err


# --- review fix round 1 -------------------------------------------------------------

ATTACH = ["attach-iso", "42", "--iso-id", "84", "--change-boot-device-to-cdrom", "--yes"]
RESTORE = "./scp-api.py boot-order 42 set HDD,CDROM,NETWORK"


def test_b1_restore_hint_survives_quiet(explore_mod, tmp_path, monkeypatch, capsys):
    run = _run(explore_mod, ATTACH + ["--quiet"], tmp_path, monkeypatch, capsys, **{S42: DETAILS})
    assert run.status == 0
    assert RESTORE in run.err


def test_b1_restore_command_is_in_the_json_result(explore_mod, tmp_path, monkeypatch, capsys):
    run = _run(explore_mod, ATTACH + ["--json"], tmp_path, monkeypatch, capsys, **{S42: DETAILS})
    assert run.status == 0
    assert json.loads(run.out)["restore_command"] == RESTORE
    quiet = _run(explore_mod, ATTACH + ["--json", "--quiet"], tmp_path, monkeypatch, capsys, **{S42: DETAILS})
    assert json.loads(quiet.out)["restore_command"] == RESTORE and RESTORE in quiet.err


def test_b1_unexpected_device_names_build_no_restore_command(explore_mod, tmp_path, monkeypatch, capsys):
    odd = {"id": 42, "name": "v42", "serverLiveInfo": {"bootorder": ["HDD", "$(touch x)"]}}
    run = _run(explore_mod, ATTACH + ["--json"], tmp_path, monkeypatch, capsys, **{S42: odd})
    assert run.status == 0
    assert "cannot build a restore command: unexpected boot device names: '$(touch x)'" in run.err
    assert "boot-order 42 set" not in run.err
    assert "restore_command" not in json.loads(run.out)


@pytest.mark.parametrize("verb", ["create", "dryrun"])
def test_b2_online_with_disk_name_is_rejected_locally(explore_mod, tmp_path, monkeypatch, capsys, verb):
    run = _run(explore_mod, ["snapshots", "42", verb, "--online", "--disk-name", "vda", "--yes"], tmp_path, monkeypatch, capsys)
    assert run.status == 2
    assert "--online cannot be combined with --disk-name" in run.err
    assert run.calls == []


def test_b2_online_help_mentions_uefi(explore_mod):
    help_text = explore_mod.build_cli().command_parsers["snapshots"].format_help()
    assert "online.uefi" in help_text


def test_b4_dryrun_400_renders_the_blocking_reasons(explore_mod, tmp_path, monkeypatch, capsys):
    body = json.dumps([{"code": "server.snapshot.create.error.iso", "message": "an ISO is attached"},
                       {"code": "server.snapshot.create.error.online.uefi", "message": "UEFI host"}])

    from case_harness import RoutedClient

    class Client(RoutedClient):
        def post(self, endpoint, data=None):
            self.calls.append(("post", endpoint, data))
            raise netcup_scp_client.HTTPStatusError(400, "Bad Request", body)

    client = Client()
    args = _ns(server_id=42, action="dryrun", disk_name="vda", online=False)
    with pytest.raises(CliFailure) as caught:
        explore_mod.cmd_snapshots(client, args, _pal(explore_mod))
    assert caught.value.exit_code == 1
    assert "snapshot not possible; blocking reasons: an ISO is attached; UEFI host" in str(caught.value)
    assert "HTTP 400" not in str(caught.value)
    # --json emits the raw reasons.
    args.json = True
    with pytest.raises(CliFailure):
        explore_mod.cmd_snapshots(client, args, _pal(explore_mod))
    assert json.loads(capsys.readouterr().out) == json.loads(body)


def test_b4_dryrun_other_http_errors_stay_errors(explore_mod, fake_client):
    class Client(fake_client):
        def post(self, endpoint, data):
            raise netcup_scp_client.HTTPStatusError(400, "Bad Request", "not json")

    client = Client(allow=("get", "post"))
    with pytest.raises(CliFailure, match="HTTP 400") as caught:
        explore_mod.cmd_snapshots(client, _ns(server_id=42, action="dryrun", disk_name="vda"), _pal(explore_mod))
    assert "blocking reasons" not in str(caught.value)


def test_b4_dryrun_400_end_to_end_exit_1(explore_mod, tmp_path, monkeypatch, capsys):
    from case_harness import RoutedClient

    real_post = RoutedClient.post

    def post(self, endpoint, data=None):
        if endpoint.endswith(":dryrun"):
            self.calls.append(("post", endpoint, data))
            raise netcup_scp_client.HTTPStatusError(400, "Bad Request", '["disk is locked"]')
        return real_post(self, endpoint, data)

    monkeypatch.setattr(RoutedClient, "post", post)
    run = _run(explore_mod, ["snapshots", "42", "dryrun", "--disk-name", "vda"], tmp_path, monkeypatch, capsys)
    assert run.status == 1
    assert "snapshot not possible; blocking reasons: disk is locked" in run.err


class _PatchClient:
    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def get(self, endpoint, params=None):
        self.calls.append(("get", endpoint))
        return dict(DETAILS)

    def patch(self, endpoint, data, params=None):
        self.calls.append(("patch", endpoint, data))
        return self.answer


def test_b6_boot_order_202_reports_a_submitted_task_not_set(explore_mod, capsys):
    task = {"uuid": TASK_UUID, "name": "ServerBootorderTask", "state": "PENDING"}
    client = _PatchClient(task)
    explore_mod.cmd_boot_order(client, _ns(server_id=42, action="set", order="HDD", yes=True), _pal(explore_mod))
    out = capsys.readouterr().out
    assert f"boot order change submitted (task {TASK_UUID}); watch: ./monitor-task.py watch {TASK_UUID}" in out
    assert "boot order set" not in out
    args = _ns(server_id=42, action="set", order="HDD", yes=True)
    args.json = True
    explore_mod.cmd_boot_order(_PatchClient(task), args, _pal(explore_mod))
    assert json.loads(capsys.readouterr().out) == task


@pytest.mark.parametrize("answer", [{}, None])
def test_b6_boot_order_200_204_keep_the_set_message(explore_mod, capsys, answer):
    explore_mod.cmd_boot_order(
        _PatchClient(answer), _ns(server_id=42, action="set", order="HDD,CDROM", yes=True), _pal(explore_mod)
    )
    assert "boot order set to HDD,CDROM" in capsys.readouterr().out


AGG_SERVERS = [{"id": 42, "name": "alpha-host"}, {"id": 77, "name": "beta-host"}]
AGG_FLAVOURS = [{"id": 5, "alias": "debian", "name": "Flv1", "text": "Debian text", "image": {"name": "Debian 13"}}]
AGG_ISOS = [{"id": 9, "name": "rescue.iso", "description": "tools", "architecture": "AMD64_X86_64", "text": "recovery blob"}]


def _agg(explore_mod, tmp_path, monkeypatch, capsys, verb, term):
    routes = {
        "/api/v1/servers": AGG_SERVERS,
        "/api/v1/servers/42/imageflavours": AGG_FLAVOURS, "/api/v1/servers/77/imageflavours": AGG_FLAVOURS,
        "/api/v1/servers/42/isoimages": AGG_ISOS, "/api/v1/servers/77/isoimages": AGG_ISOS,
    }
    run = _run(explore_mod, [verb, "--filter", term, "--json"], tmp_path, monkeypatch, capsys, **routes)
    assert run.status == 0
    return sorted({row["serverId"] for row in json.loads(run.out)})


@pytest.mark.parametrize("verb", ["imageflavours", "iso-bootable"])
def test_b7_aggregate_filter_matches_server_name_but_not_server_id(explore_mod, tmp_path, monkeypatch, capsys, verb):
    assert _agg(explore_mod, tmp_path, monkeypatch, capsys, verb, "beta") == [77]
    assert _agg(explore_mod, tmp_path, monkeypatch, capsys, verb, "77") == []  # serverId is an id column


def test_b7_imageflavours_filter_matches_name_and_text_fields(explore_mod, tmp_path, monkeypatch, capsys):
    assert _agg(explore_mod, tmp_path, monkeypatch, capsys, "imageflavours", "flv1") == [42, 77]
    assert _agg(explore_mod, tmp_path, monkeypatch, capsys, "imageflavours", "debian text") == [42, 77]
    assert _agg(explore_mod, tmp_path, monkeypatch, capsys, "imageflavours", "debian 13") == [42, 77]
    assert _agg(explore_mod, tmp_path, monkeypatch, capsys, "imageflavours", "5") == []  # the id


def test_b7_iso_filter_matches_text_and_architecture(explore_mod, tmp_path, monkeypatch, capsys):
    assert _agg(explore_mod, tmp_path, monkeypatch, capsys, "iso-bootable", "recovery blob") == [42, 77]
    assert _agg(explore_mod, tmp_path, monkeypatch, capsys, "iso-bootable", "amd64") == [42, 77]
    assert _agg(explore_mod, tmp_path, monkeypatch, capsys, "iso-bootable", "9") == []  # the id
