"""Behaviour tests for the reviewed scp-api catalog cases.

Each reviewed case in ``cli-review-scp-api.toml`` names an invocation and the
outcome that was judged acceptable.  The tests replay that exact invocation
through the real ``main()`` against a routed fake client (``RoutedClient``:
an unplanned GET fails the test, every call is recorded) with HOME and the
working directory under ``tmp_path``, and compare what really happens with
what the catalog claims: exit status, output, and the exact API calls.

Three kinds of test link to catalog rows:

* ``test_replayed_case_matches_catalog`` -- every non-control case; the
  expected API calls per invocation are pinned in ``CALLS``.
* ``test_invalid_argument_is_refused`` -- the argument-shape cases also prove
  that a bad value is refused before any API call.
* one parametrised test per library control (``--json``, ``--debug-raw``,
  ``--yes``) across routes, each proving the contrast with the same argv
  without the control.

The parameters are taken from the catalog's own ``test_ids`` so a node id can
never drift from its row; each parameter carries its row's ``cli_case`` marker.
"""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest

from case_harness import MUTATING, SCP_ROUTES, TASK_UUID, run_scp_api

NETCUP_DIR = Path(__file__).resolve().parent.parent
ROWS = {
    row["id"]: row
    for row in tomllib.loads(
        (NETCUP_DIR / "cli-review-scp-api.toml").read_text(encoding="utf-8")
    ).get("cases", [])
}
MAC = "aa:bb:cc:dd:ee:ff"
FW = f"servers/42/interfaces/{MAC}/firewall"
USERINFO = ("get_user_info", "userinfo", None)
POLICIES = "users/1/firewall-policies"


def _nodes(function: str) -> list:
    """(row id, node id inside the brackets) for every catalog link to ``function``."""
    found = []
    prefix = f"tests/test_cli_cases_scp_api.py::{function}["
    for row_id, row in ROWS.items():
        for test_id in row["test_ids"]:
            if test_id.startswith(prefix):
                found.append((row_id, test_id[len(prefix) : -1]))
    return found


def _params(function: str):
    return [
        pytest.param(row_id, node, marks=pytest.mark.cli_case(row_id), id=node)
        for row_id, node in _nodes(function)
    ]


# invocation -> the exact API calls (method, endpoint without /api/v1/, detail)
CALLS = {
    "attach-iso 42 --iso-id 1234 --yes": [("post", "servers/42/iso", {"isoId": 1234})],
    "attach-iso 42 --iso-id 1234 --user-iso-name a.iso": [],
    "attach-iso 42 --user-iso-name custom.iso --yes": [
        ("post", "servers/42/iso", {"userIsoName": "custom.iso"})
    ],
    "attach-iso 42 --iso-id 1234": [],
    "attach-iso 42 --iso-id 1234 --change-boot-device-to-cdrom --yes": [
        ("get", "servers/42", None),
        ("post", "servers/42/iso", {"isoId": 1234, "changeBootDeviceToCdrom": True}),
    ],
    "boot-order 42": [("get", "servers/42", None)],
    "boot-order 42 --debug-raw": [("get", "servers/42", None)],
    "boot-order 42 --json": [("get", "servers/42", None)],
    "boot-order 42 set HDD,CDROM,NETWORK --yes": [
        ("get", "servers/42", None),
        ("patch", "servers/42", ({"bootorder": ["HDD", "CDROM", "NETWORK"]}, None)),
    ],
    "snapshots 42 create --disk-name vda --yes": [
        ("post", "servers/42/snapshots", {"name": "vbpub-<utc>", "diskName": "vda"})
    ],
    "snapshots 42 create --online --yes": [
        ("post", "servers/42/snapshots", {"name": "vbpub-<utc>", "onlineSnapshot": True})
    ],
    "snapshots 42 create --description pre-upgrade --yes": [
        ("get", "servers/42/disks", None),
        (
            "post",
            "servers/42/snapshots",
            {"name": "vbpub-<utc>", "description": "pre-upgrade", "diskName": "vda"},
        ),
    ],
    "disks 42 supported-drivers": [("get", "servers/42/disks/supported-drivers", None)],
    "disks 42": [("get", "servers/42/disks", None)],
    "disks": [("get", "servers", None), ("get", "servers/42/disks", None)],
    'firewall-policies create --policy-json {"name": "ssh-in", "rules": []} --yes': [
        USERINFO,
        ("post", POLICIES, {"name": "ssh-in", "rules": []}),
    ],
    "firewall-policies put 12 --policy-file policy.json --yes": [
        USERINFO,
        (
            "put",
            f"{POLICIES}/12",
            {"name": "ssh-in", "description": "allow ssh", "rules": []},
        ),
    ],
    "firewall-policies create --policy-json {} --policy-file policy.json": [],
    "firewall-policies": [USERINFO, ("get", POLICIES, None)],
    "firewall-policies --limit 5": [USERINFO, ("get", POLICIES, {"limit": 5})],
    "firewall-policies --offset 2": [USERINFO, ("get", POLICIES, {"offset": 2})],
    "firewall-policies --filter ssh": [USERINFO, ("get", POLICIES, {"q": "ssh"})],
    "firewall-policies --query ssh": [USERINFO, ("get", POLICIES, {"q": "ssh"})],
    f"firewall 42 {MAC} get": [("get", FW, None)],
    f"firewall 42 {MAC} set --active --yes": [
        ("put", FW, {"copiedPolicies": [], "userPolicies": [], "active": True})
    ],
    f"firewall 42 {MAC} get --consistency-check": [("get", FW, {"consistencyCheck": True})],
    "firewall 42 get": [("get", "servers/42", None), ("get", FW, None)],
    "firewall 42": [("get", "servers/42", None), ("get", FW, None)],
    "firewall 42 set --active --inactive --yes": [],
    "firewall 42 set --active --yes": [
        ("get", "servers/42", None),
        ("put", FW, {"copiedPolicies": [], "userPolicies": [], "active": True}),
    ],
    "firewall 42 set --inactive --yes": [
        ("get", "servers/42", None),
        ("put", FW, {"copiedPolicies": [], "userPolicies": [], "active": False}),
    ],
    "firewall 42 set --copied-policy-id 3 --active --yes": [
        ("get", "servers/42", None),
        ("put", FW, {"copiedPolicies": [{"id": 3}], "userPolicies": [], "active": True}),
    ],
    "firewall 42 set --user-policy-id 4 --active --yes": [
        ("get", "servers/42", None),
        ("put", FW, {"copiedPolicies": [], "userPolicies": [{"id": 4}], "active": True}),
    ],
    "guest-agent-status 42": [("get", "servers/42/guest-agent/status", None)],
    "imageflavours 42": [("get", "servers/42/imageflavours", None)],
    "imageflavours": [("get", "servers", None), ("get", "servers/42/imageflavours", None)],
    "imageflavours 42 --filter debian": [("get", "servers/42/imageflavours", None)],
    "iso-attached 42 detach --yes": [
        ("get", "tasks", {"serverId": 42, "state": "PENDING"}),
        ("get", "tasks", {"serverId": 42, "state": "RUNNING"}),
        ("delete", "servers/42/iso", None),
    ],
    "iso-attached 42": [("get", "servers/42/iso", None)],
    "iso-attached": [("get", "servers", None), ("get", "servers/42/iso", None)],
    "iso-bootable 42": [("get", "servers/42/isoimages", None)],
    "iso-bootable": [("get", "servers", None), ("get", "servers/42/isoimages", None)],
    "iso-bootable 42 --filter rescue": [("get", "servers/42/isoimages", None)],
    "login": [("get", "servers", None), ("get", "servers/42", None)],
    "metrics 42 network": [("get", "servers/42/metrics/network", None)],
    "metrics 42 disk": [("get", "servers/42/metrics/disk", None)],
    "metrics 42 cpu": [("get", "servers/42/metrics/cpu", None)],
    "metrics 42 network-packet": [("get", "servers/42/metrics/network/packet", None)],
    "metrics 42 disk --hours 24": [("get", "servers/42/metrics/disk", {"hours": 24})],
    "power reset 42 --yes": [("patch", "servers/42", ({"state": "ON"}, {"stateOption": "RESET"}))],
    "power on 42 --yes": [("patch", "servers/42", ({"state": "ON"}, None))],
    "power off 42 --yes": [("patch", "servers/42", ({"state": "OFF"}, {"stateOption": "POWEROFF"}))],
    "power cycle 42 --yes": [
        ("patch", "servers/42", ({"state": "ON"}, {"stateOption": "POWERCYCLE"}))
    ],
    "power on 42": [],
    "rescuesystem 42 deactivate --yes": [("delete", "servers/42/rescuesystem", None)],
    "rescuesystem 42": [("get", "servers/42/rescuesystem", None)],
    "rescuesystem": [("get", "servers", None), ("get", "servers/42/rescuesystem", None)],
    "server-details 42": [("get", "servers/42", None)],
    "servers": [("get", "servers", None)],
    "snapshots 42 dryrun": [
        ("get", "servers/42/disks", None),
        ("post", "servers/42/snapshots:dryrun", {"diskName": "vda"}),
    ],
    "snapshots 42 create --yes": [
        ("get", "servers/42/disks", None),
        ("post", "servers/42/snapshots", {"name": "vbpub-<utc>", "diskName": "vda"}),
    ],
    "snapshots": [("get", "servers", None), ("get", "servers/42/snapshots", None)],
    "snapshots 42 create --name before-upgrade --yes": [
        ("get", "servers/42/disks", None),
        ("post", "servers/42/snapshots", {"name": "before-upgrade", "diskName": "vda"}),
    ],
    "status 42": [("get", "servers/42", None)],
    "status": [("get", "servers", None), ("get", "servers/42", None)],
    "status 42 --ssh-timeout 3": [("get", "servers/42", None)],
    f"tasks {TASK_UUID} cancel --yes": [("put", f"tasks/{TASK_UUID}:cancel", None)],
    f"tasks {TASK_UUID}": [("get", f"tasks/{TASK_UUID}", None)],
    "tasks": [("get", "tasks", None)],
    "tasks --state WAITING_FOR_CANCEL": [("get", "tasks", {"state": "WAITING_FOR_CANCEL"})],
    "tasks --state RUNNING": [("get", "tasks", {"state": "RUNNING"})],
    "tasks --state FINISHED": [("get", "tasks", {"state": "FINISHED"})],
    "tasks --state CANCELED": [("get", "tasks", {"state": "CANCELED"})],
    "tasks --state PENDING": [("get", "tasks", {"state": "PENDING"})],
    "tasks --state ERROR": [("get", "tasks", {"state": "ERROR"})],
    "tasks --limit 5": [("get", "tasks", {"limit": 5})],
    "tasks --offset 1": [("get", "tasks", {"offset": 1})],
    "tasks --filter install": [("get", "tasks", {"q": "install"})],
    "tasks --query install": [("get", "tasks", {"q": "install"})],
    "tasks --server-id 42": [("get", "tasks", {"serverId": 42})],
    "user-iso upload custom.iso --yes": [
        USERINFO,
        ("post", "users/1/isos/custom.iso?multipart=false", None),
        ("upload_file", "https://upload.invalid/object", ("custom.iso", 0, None)),
    ],
    "user-iso": [USERINFO, ("get", "users/1/isos", None)],
    "user-iso upload custom.iso --name renamed.iso --yes": [
        USERINFO,
        ("post", "users/1/isos/renamed.iso?multipart=false", None),
        ("upload_file", "https://upload.invalid/object", ("custom.iso", 0, None)),
    ],
    "user-iso upload custom.iso --multipart --yes": [
        USERINFO,
        ("post", "users/1/isos/custom.iso?multipart=true", None),
        ("get", "users/1/isos/custom.iso/up-1/parts/1", None),
        ("upload_file", "https://upload.invalid/part-1", ("custom.iso", 0, 3)),
        ("put", "users/1/isos/custom.iso/up-1", [{"ETag": '"e-1"', "partNumber": 1}]),
    ],
    "user-iso upload custom.iso --multipart --part-size-mib 5 --yes": [
        USERINFO,
        ("post", "users/1/isos/custom.iso?multipart=true", None),
        ("get", "users/1/isos/custom.iso/up-1/parts/1", None),
        ("upload_file", "https://upload.invalid/part-1", ("custom.iso", 0, 3)),
        ("put", "users/1/isos/custom.iso/up-1", [{"ETag": '"e-1"', "partNumber": 1}]),
    ],
}

# case node -> (invalid argv, distinguishing stderr text)
INT = "must be an integer"
CHOICE = "invalid choice"
INVALID = {
    "attach-iso-server-id": ("attach-iso not-an-id --iso-id 1234", INT),
    "boot-order-action": ("boot-order 42 bogus", CHOICE),
    "boot-order-order": ("boot-order 42 set HDD,FLOPPY --yes", "unknown boot device"),
    "boot-order-server-id": ("boot-order abc", INT),
    "disks-action": ("disks 42 bogus", CHOICE),
    "disks-server-id": ("disks abc", INT),
    "firewall-policies-action": ("firewall-policies bogus", CHOICE),
    "firewall-policies-policy-id": ("firewall-policies put abc --policy-file policy.json --yes", INT),
    "firewall-action": ("firewall 42 get bogus", CHOICE),
    "firewall-mac": ("firewall 42 not-a-mac", "must be a MAC address"),
    "firewall-server-id": ("firewall abc", INT),
    "guest-agent-status-server-id": ("guest-agent-status abc", INT),
    "imageflavours-server-id": ("imageflavours abc", INT),
    "iso-attached-action": ("iso-attached 42 bogus", CHOICE),
    "iso-attached-server-id": ("iso-attached abc", INT),
    "iso-bootable-server-id": ("iso-bootable abc", INT),
    "metrics-metric": ("metrics 42 bogus", CHOICE),
    "metrics-server-id": ("metrics abc cpu", INT),
    "metrics-hours": ("metrics 42 cpu --hours 0", "must be between 1 and 1440 hours"),
    "power-action": ("power bogus 42", CHOICE),
    "power-server-id": ("power on abc", INT),
    "rescuesystem-action": ("rescuesystem 42 bogus", CHOICE),
    "rescuesystem-server-id": ("rescuesystem abc", INT),
    "server-details-server-id": ("server-details abc", INT),
    "snapshots-action": ("snapshots 42 bogus", CHOICE),
    "snapshots-server-id": ("snapshots abc", INT),
    "status-server-id": ("status abc", INT),
    "tasks-action": (f"tasks {TASK_UUID} bogus", CHOICE),
    "tasks-state": ("tasks --state BOGUS", CHOICE),
    "user-iso-action": ("user-iso bogus", CHOICE),
    "user-iso-file": ("user-iso upload missing.iso --yes", "is not a regular file"),
}


def _normalised(calls):
    """Calls without the /api/v1/ prefix; the snapshot default name's clock is masked."""
    result = []
    for method, endpoint, detail in calls:
        endpoint = endpoint.replace("/api/v1/", "", 1)
        if (
            method == "post"
            and endpoint == "servers/42/snapshots"
            and re.fullmatch(r"vbpub-\d{8}T\d{6}Z", detail["name"])
        ):
            detail = {**detail, "name": "vbpub-<utc>"}
        result.append((method, endpoint, detail))
    return result


def _run(argv, tmp_path, monkeypatch, capsys, mod, sub="run", **extra):
    workdir = tmp_path / sub
    workdir.mkdir()
    return run_scp_api(
        mod, list(argv), tmp_path=workdir, monkeypatch=monkeypatch, capsys=capsys, **extra
    )


def _argv(row):
    return list(row["invocation"])


def _mutations(run):
    return [method for method in run.methods if method in MUTATING]


@pytest.mark.parametrize(("case_id", "node"), _params("test_replayed_case_matches_catalog"))
def test_replayed_case_matches_catalog(case_id, node, explore_mod, tmp_path, monkeypatch, capsys):
    row = ROWS[case_id]
    key = " ".join(row["invocation"])
    run = _run(row["invocation"], tmp_path, monkeypatch, capsys, explore_mod)
    assert run.status == row["expected_exit_status"]
    assert row["expected_stdout_contains"] in run.out
    assert row["expected_stderr_contains"] in run.err
    assert _normalised(run.calls) == CALLS[key]
    # A mutating call is only ever made by a run that succeeded.
    if run.status != 0:
        assert _mutations(run) == []
        assert run.out == ""
    if row["expected_exit_status"] == 2:
        assert run.calls == []


def _row_ending(suffix):
    return next(i for i in ROWS if i.endswith(suffix))


# route, catalog row, row that must stay, text that proves the other row is gone
FILTER_CASES = [
    pytest.param(
        "imageflavours", "Debian 13", "Ubuntu",
        marks=pytest.mark.cli_case(_row_ending("imageflavours/--filter/--filter")),
        id="imageflavours",
    ),
    pytest.param(
        "iso-bootable", "recovery ISO", "netinst",
        marks=pytest.mark.cli_case(_row_ending("iso-bootable/--filter/--filter")),
        id="iso-bootable",
    ),
]


@pytest.mark.parametrize(("route", "kept", "dropped"), FILTER_CASES)
def test_client_side_filter_keeps_only_matching_rows(
    route, kept, dropped, explore_mod, tmp_path, monkeypatch, capsys
):
    row = ROWS[_row_ending(f"{route}/--filter/--filter")]
    value = row["invocation"][row["invocation"].index("--filter") + 1]
    unfiltered = _run(["%s" % route, "42"], tmp_path, monkeypatch, capsys, explore_mod, "all")
    assert kept in unfiltered.out and dropped in unfiltered.out
    # The reviewed value, and the same value in another case, keep one row only.
    for variant, name in ((value, "as-reviewed"), (value.upper(), "upper"), (value.capitalize(), "cap")):
        run = _run([route, "42", "--filter", variant], tmp_path, monkeypatch, capsys, explore_mod, name)
        assert run.status == 0
        assert kept in run.out
        assert dropped not in run.out
        # Filtering is local: the same single GET as the unfiltered listing.
        assert run.calls == unfiltered.calls
    # A value that matches nothing leaves no data rows.
    empty = _run([route, "42", "--filter", "no-such-text"], tmp_path, monkeypatch, capsys, explore_mod, "none")
    assert kept not in empty.out and dropped not in empty.out


PART_SIZE_CASE = next(i for i in ROWS if i.endswith("--part-size-mib/--part-size-mib"))


@pytest.mark.cli_case(PART_SIZE_CASE)
def test_part_size_changes_how_a_large_iso_is_split(explore_mod, tmp_path, monkeypatch, capsys):
    """--part-size-mib is observable only with a file larger than one part."""
    mib = 1024 * 1024
    routes = dict(SCP_ROUTES)
    for number in (1, 2, 3):
        routes[f"/api/v1/users/1/isos/custom.iso/up-1/parts/{number}"] = {
            "url": f"https://upload.invalid/part-{number}"
        }
    base = ["user-iso", "upload", "custom.iso", "--multipart", "--yes"]
    sized = _run(base + ["--part-size-mib", "5"], tmp_path, monkeypatch, capsys, explore_mod,
                 "sized", iso_size=11 * mib, routes=routes)
    default = _run(base, tmp_path, monkeypatch, capsys, explore_mod, "default",
                   iso_size=65 * mib, routes=routes)
    assert sized.status == 0
    assert [c[2] for c in sized.calls if c[0] == "upload_file"] == [
        ("custom.iso", 0, 5 * mib),
        ("custom.iso", 5 * mib, 5 * mib),
        ("custom.iso", 10 * mib, 1 * mib),
    ]
    assert default.status == 0
    # The default part size is 64 MiB: a 65 MiB file is split 64 + 1.
    assert [c[2] for c in default.calls if c[0] == "upload_file"] == [
        ("custom.iso", 0, 64 * mib),
        ("custom.iso", 64 * mib, 1 * mib),
    ]


@pytest.mark.parametrize(("case_id", "node"), _params("test_invalid_argument_is_refused"))
def test_invalid_argument_is_refused(case_id, node, explore_mod, tmp_path, monkeypatch, capsys):
    argv, text = INVALID[node]
    run = _run(argv.split(" "), tmp_path, monkeypatch, capsys, explore_mod)
    assert run.status == 2
    assert text in run.err
    assert run.out == ""
    # The bad value is refused before any API call is made.
    assert run.calls == []


@pytest.mark.parametrize(("case_id", "node"), _params("test_json_output_is_machine_readable"))
def test_json_output_is_machine_readable(case_id, node, explore_mod, tmp_path, monkeypatch, capsys):
    row = ROWS[case_id]
    argv = _argv(row)
    assert "--json" in argv
    run = _run(argv, tmp_path, monkeypatch, capsys, explore_mod, "json")
    assert run.status == 0
    payload = json.loads(run.out)
    assert row["expected_stdout_contains"] in run.out
    plain = _run([t for t in argv if t != "--json"], tmp_path, monkeypatch, capsys, explore_mod, "plain")
    assert plain.status == 0
    assert plain.calls == run.calls
    if node == "metrics":
        # --json is the raw timestamp -> series map; the plain form is the compact table.
        assert payload == SCP_ROUTES["/api/v1/servers/42/metrics/cpu"]
        assert "series" in plain.out and "CPU0" in plain.out and "raw" in plain.out
        assert plain.out != run.out
    else:
        assert plain.out != run.out
        with pytest.raises(json.JSONDecodeError):
            json.loads(plain.out)
    assert payload is not None


@pytest.mark.parametrize(("case_id", "node"), _params("test_debug_raw_warns_and_is_off_by_default"))
def test_debug_raw_warns_and_is_off_by_default(
    case_id, node, explore_mod, tmp_path, monkeypatch, capsys
):
    row = ROWS[case_id]
    argv = _argv(row)
    assert "--debug-raw" in argv
    raw = _run(argv, tmp_path, monkeypatch, capsys, explore_mod, "raw")
    assert raw.status == row["expected_exit_status"]
    assert "--debug-raw is active: credentials, tokens, passwords" in raw.err
    plain = _run([t for t in argv if t != "--debug-raw"], tmp_path, monkeypatch, capsys, explore_mod, "plain")
    assert plain.status == raw.status
    assert "--debug-raw is active" not in plain.err
    assert plain.calls == raw.calls
    assert plain.out == raw.out


@pytest.mark.parametrize(
    ("case_id", "node"), _params("test_yes_is_the_only_consent_in_a_non_interactive_run")
)
def test_yes_is_the_only_consent_in_a_non_interactive_run(
    case_id, node, explore_mod, tmp_path, monkeypatch, capsys
):
    row = ROWS[case_id]
    argv = _argv(row)
    assert "--yes" in argv
    accepted = _run(argv, tmp_path, monkeypatch, capsys, explore_mod, "accepted")
    assert accepted.status == 0
    assert "Confirmation accepted via --yes" in accepted.err
    assert len(_mutations(accepted)) >= 1
    # Contrast: without --yes and without a terminal the run refuses, mutating nothing.
    refused = _run([t for t in argv if t != "--yes"], tmp_path, monkeypatch, capsys, explore_mod, "refused")
    assert refused.status == 2
    assert "confirmation is required, but stdin is not interactive" in refused.err
    assert _mutations(refused) == []


def test_catalog_links_only_known_test_functions():
    # Every linked node names one of the test functions above (no stale ids).
    known = {
        "test_replayed_case_matches_catalog",
        "test_part_size_changes_how_a_large_iso_is_split",
        "test_client_side_filter_keeps_only_matching_rows",
        "test_invalid_argument_is_refused",
        "test_json_output_is_machine_readable",
        "test_debug_raw_warns_and_is_off_by_default",
        "test_yes_is_the_only_consent_in_a_non_interactive_run",
    }
    linked = {
        test_id.split("::")[1].split("[")[0]
        for row in ROWS.values()
        for test_id in row["test_ids"]
    }
    assert linked == known
    assert SCP_ROUTES  # the routed table the replays answer from is non-empty
