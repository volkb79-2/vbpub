"""TNT-56 fake validation, TNT-60 VM device list/update/delete, TNT-61 disk-image import and verified upload."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from tnt import uploads
from tnt.actions import Action, Kind, RpcCall, apply_plan, make_plan
from tnt.cli import build_cli
from tnt.errors import PlanExecutionError, Refused, TransportError
from tnt.session import open_session
from tnt.testing import CALL_ERROR, FakeServer, _RpcError

REAL_ORIGIN = uploads._origin
IMAGE = "/mnt/tank/images/debian.qcow2"
GIB = 1024 ** 3


def make_vm(server, name="guest", *, state="STOPPED", autostart=False):
    vm = server._dispatch("vm.create", [{"name": name, "memory": 2048, "autostart": autostart}])
    vm["status"] = {"state": state, "pid": None}
    return vm


def add_device(server, vm, attributes, order=1):
    return server._dispatch("vm.device.create", [{"vm": vm["id"], "order": order, "attributes": attributes}])


def open_write(server):
    return open_session(server.profile(), secret=server.secret(), allow_loopback_ws=True, allow_write=True)


def run(session, action, **kwargs):
    return apply_plan(make_plan(action, session.rpc), session.rpc, allow_write=True, **kwargs)


# -- TNT-56 -------------------------------------------------------------------------------------------------
GOOD_SCRUB = {"pool_name": "tank", "scan_type": "SCRUB", "action": "START", "threshold": 0}


@pytest.mark.parametrize("params,message", [
    ([], "exactly one data object"),
    (["tank"], "exactly one data object"),
    ([{**GOOD_SCRUB, "extra": 1}], "exactly pool_name"),
    ([{"pool_name": "tank", "scan_type": "SCRUB", "action": "START"}], "exactly pool_name"),
    ([{**GOOD_SCRUB, "pool_name": ""}], "pool_name"),
    ([{**GOOD_SCRUB, "pool_name": 3}], "pool_name"),
    ([{**GOOD_SCRUB, "scan_type": "scrub"}], "scan_type"),
    ([{**GOOD_SCRUB, "action": "STOP"}], "action"),
    ([{**GOOD_SCRUB, "threshold": "0"}], "threshold"),
    ([{**GOOD_SCRUB, "threshold": True}], "threshold"),
])
def test_fake_zpool_scrub_run_rejects_payload_drift(params, message):
    server = FakeServer()
    with pytest.raises(_RpcError, match=message) as error:
        server._dispatch("zpool.scrub.run", params)
    assert error.value.code == CALL_ERROR
    assert server.scrubs_started == []


@pytest.mark.parametrize("action,scan", [("START", "SCRUB"), ("PAUSE", "SCRUB"), ("CANCEL", "ERRORSCRUB")])
def test_fake_zpool_scrub_run_accepts_exactly_the_v27_payload(action, scan):
    server = FakeServer()
    server._dispatch("zpool.scrub.run", [{"pool_name": "tank", "scan_type": scan, "action": action, "threshold": 35}])
    assert server.scrubs_started == ["tank"]


# -- fake server consistency (TNT-60) -------------------------------------------------------------------------
def test_fake_device_table_keeps_vm_records_consistent():
    server = FakeServer()
    vm = make_vm(server)
    other = make_vm(server, "other")
    disk = add_device(server, vm, {"dtype": "DISK", "path": "/dev/zvol/tank/a", "type": "VIRTIO"})
    display = add_device(server, vm, {"dtype": "DISPLAY", "port": 5900}, order=2)
    assert [d["id"] for d in server.vms[vm["id"]]["devices"]] == [disk["id"], display["id"]]
    assert server.vms[vm["id"]]["display_available"] is True
    server._dispatch("vm.device.update", [disk["id"], {"vm": other["id"], "order": None, "attributes": {"type": "AHCI"}}])
    assert [d["id"] for d in server.vms[other["id"]]["devices"]] == [disk["id"]]
    assert server.vm_devices[disk["id"]]["attributes"] == {"dtype": "DISK", "path": "/dev/zvol/tank/a", "type": "AHCI"}
    server._dispatch("vm.device.delete", [display["id"], {"force": False}])
    assert server.vms[vm["id"]]["display_available"] is False and server.vms[vm["id"]]["devices"] == []


@pytest.mark.parametrize("payload,message", [
    ({}, "accepts only"), ({"bogus": 1}, "accepts only"), ({"order": "1"}, "order must be"),
    ({"attributes": {"dtype": "NIC"}}, "type cannot change"), ({"vm": 99}, "not found"),
])
def test_fake_device_update_validates_keys(payload, message):
    server = FakeServer()
    disk = add_device(server, make_vm(server), {"dtype": "DISK", "path": "/dev/zvol/tank/a"})
    with pytest.raises(_RpcError, match=message):
        server._dispatch("vm.device.update", [disk["id"], payload])


def test_fake_device_delete_validates_options_and_state_and_create_rejects_duplicate_zvol():
    server = FakeServer()
    vm = make_vm(server, state="RUNNING")
    disk = add_device(server, vm, {"dtype": "DISK", "create_zvol": True, "zvol_name": "tank/a", "zvol_volsize": GIB})
    assert server.vm_devices[disk["id"]]["attributes"]["path"] == "/dev/zvol/tank/a"
    with pytest.raises(_RpcError, match="already exists"):
        add_device(server, vm, {"dtype": "DISK", "create_zvol": True, "zvol_name": "tank/a", "zvol_volsize": GIB})
    with pytest.raises(_RpcError, match="options are"):
        server._dispatch("vm.device.delete", [disk["id"], {"unknown": True}])
    with pytest.raises(_RpcError, match="must be stopped"):
        server._dispatch("vm.device.delete", [disk["id"], {}])
    server._dispatch("vm.device.delete", [disk["id"], {"force": True, "zvol": True}])
    assert "tank/a" not in server.datasets


def test_fake_virtual_size_and_convert_validation():
    server = FakeServer()
    server.image_sizes[IMAGE] = 3 * GIB
    assert server._dispatch("vm.device.virtual_size", [{"path": IMAGE}]) == 3 * GIB
    for params in ([{"path": "/mnt/tank/missing.qcow2"}], [{"other": IMAGE}], []):
        with pytest.raises(_RpcError, match="No such file"):
            server._dispatch("vm.device.virtual_size", params)
    with pytest.raises(_RpcError, match="exactly source"):
        server._dispatch("vm.device.convert", [{"source": IMAGE}])
    with pytest.raises(_RpcError, match="No such file"):
        server._dispatch("vm.device.convert", [{"source": "/mnt/tank/x.qcow2", "destination": "/dev/zvol/tank/z"}])
    with pytest.raises(_RpcError, match="not an existing zvol"):
        server._dispatch("vm.device.convert", [{"source": IMAGE, "destination": "/dev/zvol/tank/z"}])


# -- TNT-60 workflows -------------------------------------------------------------------------------------------
def setup_disk_vm(server, *, state="STOPPED"):
    vm = make_vm(server, state=state)
    disk = add_device(server, vm, {"dtype": "DISK", "create_zvol": True, "zvol_name": "tank/guest0", "zvol_volsize": GIB,
                                   "type": "VIRTIO"})
    iso = add_device(server, vm, {"dtype": "CDROM", "path": "/mnt/tank/iso/a.iso"}, order=2)
    return vm, disk, iso


def test_device_list_update_and_delete_keep_data_unless_asked():
    with FakeServer() as server:
        vm, disk, iso = setup_disk_vm(server)
        session = open_write(server)
        try:
            workflows = session.workflows
            assert [d["id"] for d in workflows.vm_devices("guest")] == [disk["id"], iso["id"]]
            run(session, workflows.vm_device_update_action("guest", iso["id"], order=5,
                                                           attributes={"path": "/mnt/tank/iso/b.iso"}))
            assert server.vm_devices[iso["id"]]["order"] == 5
            assert server.vm_devices[iso["id"]]["attributes"]["path"] == "/mnt/tank/iso/b.iso"
            run(session, workflows.vm_device_update_action("guest", disk["id"], attributes={"type": "AHCI"}))
            assert server.vm_devices[disk["id"]]["attributes"]["type"] == "AHCI"

            # delete keeps the zvol by default and needs only the first confirmation
            plan = make_plan(workflows.vm_device_delete_action("guest", iso["id"]), session.rpc)
            assert "backing data is kept" in plan.action.title and plan.action.confirm_second is None
            with pytest.raises(Refused, match="typed-confirmation"):
                apply_plan(plan, session.rpc, allow_write=True)
            apply_plan(plan, session.rpc, allow_write=True, typed_confirmation=f"guest#{iso['id']}")
            assert iso["id"] not in server.vm_devices

            action = workflows.vm_device_delete_action("guest", disk["id"], delete_zvol=True)
            assert action.confirm_second == "tank/guest0" and "tank/guest0" in make_plan(action, session.rpc).render()
            plan = make_plan(action, session.rpc)
            with pytest.raises(Refused, match="typed-second-confirmation"):
                apply_plan(plan, session.rpc, allow_write=True, typed_confirmation=f"guest#{disk['id']}")
            with pytest.raises(Refused, match="typed-second-confirmation"):
                apply_plan(plan, session.rpc, allow_write=True, typed_confirmation=f"guest#{disk['id']}",
                           typed_second_confirmation="tank/other")
            assert "tank/guest0" in server.datasets
            apply_plan(plan, session.rpc, allow_write=True, typed_confirmation=f"guest#{disk['id']}",
                       typed_second_confirmation="tank/guest0")
            assert "tank/guest0" not in server.datasets and disk["id"] not in server.vm_devices
        finally:
            session.close()


def test_device_delete_without_zvol_flag_keeps_the_volume():
    with FakeServer() as server:
        vm, disk, _ = setup_disk_vm(server)
        session = open_write(server)
        try:
            run(session, session.workflows.vm_device_delete_action("guest", disk["id"]),
                typed_confirmation=f"guest#{disk['id']}")
            assert "tank/guest0" in server.datasets
        finally:
            session.close()


def test_disk_changes_need_a_stopped_vm_but_boot_order_does_not():
    with FakeServer() as server:
        vm, disk, iso = setup_disk_vm(server, state="RUNNING")
        session = open_write(server)
        try:
            workflows = session.workflows
            run(session, workflows.vm_device_update_action("guest", disk["id"], order=9))
            with pytest.raises(Refused, match="vm-not-stopped"):
                make_plan(workflows.vm_device_update_action("guest", disk["id"], attributes={"type": "AHCI"}), session.rpc)
            with pytest.raises(Refused, match="vm-not-stopped"):
                make_plan(workflows.vm_device_delete_action("guest", disk["id"]), session.rpc)
            # a CDROM change is not a disk change
            run(session, workflows.vm_device_update_action("guest", iso["id"], attributes={"path": "/mnt/tank/iso/c.iso"}))
        finally:
            session.close()


def test_the_stopped_rule_is_enforced_again_at_apply_time():
    with FakeServer() as server:
        vm, disk, _ = setup_disk_vm(server)
        session = open_write(server)
        try:
            plan = make_plan(session.workflows.vm_device_update_action("guest", disk["id"], attributes={"type": "AHCI"}),
                             session.rpc)
            server.vms[vm["id"]]["status"] = {"state": "RUNNING", "pid": 1}
            with pytest.raises(Refused, match="vm-not-stopped|state-drift"):
                apply_plan(plan, session.rpc, allow_write=True)
            assert server.vm_devices[disk["id"]]["attributes"]["type"] == "VIRTIO"
        finally:
            session.close()


def test_device_actions_refuse_foreign_missing_and_malformed_targets():
    with FakeServer() as server:
        vm, disk, _ = setup_disk_vm(server)
        other = make_vm(server, "other")
        foreign = add_device(server, other, {"dtype": "NIC"}, order=3)
        session = open_write(server)
        try:
            workflows = session.workflows
            with pytest.raises(Refused, match="does not belong"):
                workflows.vm_device_update_action("guest", foreign["id"], order=1)
            with pytest.raises(Refused, match="exactly one VM device"):
                workflows.vm_device_update_action("guest", 999, order=1)
            for bad in (0, -1, True, "1"):
                with pytest.raises(Refused, match="positive integer"):
                    workflows.vm_device_delete_action("guest", bad)
            with pytest.raises(Refused, match="only order and attributes|provide --order"):
                workflows.vm_device_update_action("guest", disk["id"])
            with pytest.raises(Refused, match="non-negative integer"):
                workflows.vm_device_update_action("guest", disk["id"], order=-1)
            with pytest.raises(Refused, match="type cannot change"):
                workflows.vm_device_update_action("guest", disk["id"], attributes={"dtype": "NIC"})
            with pytest.raises(Refused, match="not of an update|part of device creation"):
                workflows.vm_device_update_action("guest", disk["id"], attributes={"create_zvol": True})
            with pytest.raises(Refused, match="must start with /mnt/"):
                iso = next(d for d in server.vm_devices.values() if d["attributes"]["dtype"] == "CDROM")
                workflows.vm_device_update_action("guest", iso["id"], attributes={"path": "/tmp/a.iso"})
            with pytest.raises(Refused, match="boot-pool"):
                workflows.vm_device_update_action("guest", disk["id"], attributes={"path": "/dev/zvol/boot-pool/x"})
            with pytest.raises(Refused, match="either --zvol or --raw-file"):
                workflows.vm_device_delete_action("guest", disk["id"], delete_zvol=True, delete_raw_file=True)
            with pytest.raises(Refused, match="only a DISK"):
                workflows.vm_device_delete_action("guest", next(d for d in server.vm_devices.values()
                                                                if d["attributes"]["dtype"] == "CDROM")["id"], delete_zvol=True)
            with pytest.raises(Refused, match="only a RAW"):
                workflows.vm_device_delete_action("guest", disk["id"], delete_raw_file=True)
        finally:
            session.close()


def test_raw_device_delete_names_the_file_and_a_non_zvol_disk_is_refused():
    with FakeServer() as server:
        vm = make_vm(server)
        raw = add_device(server, vm, {"dtype": "RAW", "path": "/mnt/tank/raw/a.img"})
        plain = add_device(server, vm, {"dtype": "DISK", "path": "/mnt/tank/other.img"}, order=2)
        session = open_write(server)
        try:
            action = session.workflows.vm_device_delete_action("guest", raw["id"], delete_raw_file=True)
            assert action.confirm_second == "/mnt/tank/raw/a.img"
            with pytest.raises(Refused, match="not backed by a zvol"):
                session.workflows.vm_device_delete_action("guest", plain["id"], delete_zvol=True)
            boot = add_device(server, vm, {"dtype": "DISK", "path": "/dev/zvol/boot-pool/x"}, order=3)
            with pytest.raises(Refused, match="boot-pool"):
                session.workflows.vm_device_delete_action("guest", boot["id"], delete_zvol=True)
            run(session, action, typed_confirmation=f"guest#{raw['id']}", typed_second_confirmation="/mnt/tank/raw/a.img")
            assert raw["id"] not in server.vm_devices
        finally:
            session.close()


def test_device_plan_drifts_when_the_device_changes_after_planning():
    with FakeServer() as server:
        vm, disk, iso = setup_disk_vm(server)
        session = open_write(server)
        try:
            plan = make_plan(session.workflows.vm_device_update_action("guest", iso["id"], order=3), session.rpc)
            server.vm_devices[iso["id"]]["order"] = 7
            with pytest.raises(Refused, match="state-drift"):
                apply_plan(plan, session.rpc, allow_write=True)
        finally:
            session.close()


def test_policy_refuses_malformed_device_and_import_calls():
    from tnt.policy import refusal_for
    assert refusal_for("vm.device.delete", (1, {"force": False, "raw_file": False, "zvol": False})) is None
    for params in ((1,), (True, {"force": 1, "raw_file": False, "zvol": False}), (1, {"zvol": True}),
                   ("1", {"force": False, "raw_file": False, "zvol": False})):
        assert "vm.device.delete takes" in refusal_for("vm.device.delete", params)
    assert refusal_for("vm.device.update", (1, {"order": 2})) is None
    for params in ((1, {}), (True, {"order": 1}), (1,), (1, "x")):
        assert "vm.device.update takes" in refusal_for("vm.device.update", params)
    good = {"source": IMAGE, "destination": "/dev/zvol/tank/z"}
    assert refusal_for("vm.device.convert", (good,)) is None
    for data in ({"source": IMAGE}, {**good, "extra": 1}, {**good, "source": "/tmp/x"}, {**good, "destination": "/tmp/z"},
                 {**good, "source": 1}):
        assert "vm.device.convert is limited" in refusal_for("vm.device.convert", (data,))
    assert "vm.device.convert is limited" in refusal_for("vm.device.convert", ())
    assert "boot-pool" in refusal_for("vm.device.convert", ({**good, "destination": "/dev/zvol/boot-pool/z"},))
    assert refusal_for("filesystem.put", ("/mnt/tank/images/a.qcow2", {"mode": 420})) is None
    for params in (("/mnt/tank/a", {"mode": True}), ("/etc/a", {"mode": 420}), ("/mnt/tank/a", {"mode": 420, "x": 1}),
                   ("/mnt/tank/a",), ("/mnt/tank/a", "x")):
        assert "filesystem.put is limited" in refusal_for("filesystem.put", params)
    assert "boot-pool" in refusal_for("filesystem.put", ("/mnt/boot-pool/a", {"mode": 420}))


def test_device_methods_cannot_hide_inside_other_action_ids():
    with FakeServer() as server:
        vm, disk, _ = setup_disk_vm(server)
        session = open_write(server)
        try:
            sneaky = Action("TNT-40", Kind.MUTATE, "sneaky", (RpcCall("vm.device.update", (disk["id"], {"order": 4})),),
                            (RpcCall("vm.query", ([["name", "=", "guest"]], {})),))
            with pytest.raises(Refused, match="own reviewed actions"):
                make_plan(sneaky, session.rpc)
        finally:
            session.close()


def test_second_confirmation_requires_a_destructive_action_with_a_name():
    probe = (RpcCall("vm.query", ([], {})),)
    call = (RpcCall("vm.device.delete", (1, {"force": False, "raw_file": False, "zvol": True})),)
    from tnt.actions import validate_action
    for kwargs in ({"kind": Kind.MUTATE, "confirm_target": None, "confirm_second": "x"},
                   {"kind": Kind.DESTRUCTIVE, "confirm_target": "t", "confirm_second": " "}):
        with pytest.raises(Refused, match="second confirmation|destructive"):
            validate_action(Action("TNT-60", kwargs["kind"], "t", call, probe, kwargs["confirm_target"],
                                   confirm_second=kwargs["confirm_second"]))


@pytest.mark.parametrize("mutate", [
    lambda a: Action("TNT-60", Kind.MUTATE, "x", (), a.probe),  # no call: validate_action refuses
    lambda a: Action("TNT-60", Kind.MUTATE, "x", a.calls + a.calls, a.probe),
    lambda a: Action("TNT-60", Kind.MUTATE, "x", (RpcCall("vm.device.update", (a.calls[0].params[0], {"order": 1}), job=True),), a.probe),
    lambda a: Action("TNT-60", Kind.MUTATE, "x", (RpcCall("vm.device.update", (a.calls[0].params[0], "x")),), a.probe),
    lambda a: Action("TNT-60", Kind.MUTATE, "x", (RpcCall("vm.device.update", (True, {"order": 1})),), a.probe),
    lambda a: Action("TNT-60", Kind.MUTATE, "x", a.calls, a.probe[:1]),
    lambda a: Action("TNT-60", Kind.MUTATE, "x", a.calls, (RpcCall("vm.query", ([["name", "=", "guest"], ["id", "=", 1]], {})), a.probe[1])),
    lambda a: Action("TNT-60", Kind.MUTATE, "x", a.calls, (RpcCall("vm.query", ([["name", "=", ""]], {})), a.probe[1])),
    lambda a: Action("TNT-60", Kind.MUTATE, "x", a.calls, (RpcCall("vm.query", ([["name", "=", "guest"]], {"select": ["id"]})), a.probe[1])),
    lambda a: Action("TNT-60", Kind.MUTATE, "x", a.calls, (a.probe[0], RpcCall("vm.device.query", ([["id", "=", 99]], {})))),
    lambda a: Action("TNT-60", Kind.MUTATE, "x", (RpcCall("vm.start", (1, {})),), a.probe),
    lambda a: Action("TNT-60", Kind.DESTRUCTIVE, "x", a.calls, a.probe, "guest#1"),
])
def test_malformed_device_actions_are_refused(mutate):
    with FakeServer() as server:
        vm, disk, _ = setup_disk_vm(server)
        session = open_write(server)
        try:
            base = session.workflows.vm_device_update_action("guest", disk["id"], order=2)
            with pytest.raises(Refused):
                make_plan(mutate(base), session.rpc)
        finally:
            session.close()


def test_malformed_delete_actions_are_refused():
    with FakeServer() as server:
        vm, disk, _ = setup_disk_vm(server)
        session = open_write(server)
        try:
            good = session.workflows.vm_device_delete_action("guest", disk["id"], delete_zvol=True)
            variants = [
                Action("TNT-60", Kind.DESTRUCTIVE, "x", good.calls, good.probe, "wrong"),
                Action("TNT-60", Kind.DESTRUCTIVE, "x", good.calls, good.probe, good.confirm_target, confirm_second="tank/other"),
                Action("TNT-60", Kind.DESTRUCTIVE, "x", good.calls, good.probe, good.confirm_target),
                Action("TNT-60", Kind.DESTRUCTIVE, "x", (RpcCall("vm.device.delete", (disk["id"], "x")),), good.probe, good.confirm_target),
            ]
            for variant in variants:
                with pytest.raises(Refused):
                    make_plan(variant, session.rpc)
        finally:
            session.close()


# -- TNT-61 capability probe and import ----------------------------------------------------------------------------
def test_vanilla_hosts_hide_import_with_a_clear_message_and_report_no_capability_gap():
    vanilla = [m for m in FakeServer().methods if m not in {"vm.device.virtual_size", "vm.device.convert", "filesystem.put"}]
    with FakeServer(methods=vanilla) as server:
        session = open_write(server)
        try:
            assert session.missing_methods == {}
            assert "capability gaps" not in session.banner()
            with pytest.raises(Refused, match="not on vanilla v27"):
                session.workflows.vm_disk_image_size(IMAGE)
            make_vm(server)
            with pytest.raises(Refused, match="vm.device.virtual_size and vm.device.convert"):
                session.workflows.vm_disk_import_plan("guest", IMAGE, "tank/vm/new", "auto")
            with pytest.raises(Refused, match="filesystem.put"):
                session.workflows.files_put_action(_Download(), "/mnt/tank/images")
        finally:
            session.close()


class _Download:
    name = "debian.qcow2"
    size = 10
    sha256 = "ab" * 32


def test_import_methods_are_listed_when_the_host_has_them():
    with FakeServer() as server:
        session = open_write(server)
        try:
            assert session.rpc.supports("vm.device.convert") and session.rpc.supports("filesystem.put")
            server.image_sizes[IMAGE] = 3 * GIB
            assert session.workflows.vm_disk_image_size(IMAGE) == {"path": IMAGE, "virtual_size_bytes": 3 * GIB}
        finally:
            session.close()


@pytest.mark.parametrize("path", ["relative.qcow2", "/etc/a.qcow2", "/mnt/tank/../a.qcow2", "/mnt/tank/a.iso",
                                  "/mnt/tank/A.QCOW2", "/mnt//tank/a.qcow2", None])
def test_image_paths_are_validated(path):
    from tnt.workflows import validated_image_path
    with pytest.raises(Refused):
        validated_image_path(path)
    with pytest.raises(Refused, match="boot-pool"):
        validated_image_path("/mnt/boot-pool/a.qcow2")


@pytest.mark.parametrize("name", ["tank", "/tank/a", "tank//a", "tank/../a", "tank/a b", "tank/", None, 3])
def test_zvol_names_are_validated(name):
    from tnt.workflows import validated_zvol_name
    with pytest.raises(Refused):
        validated_zvol_name(name)
    with pytest.raises(Refused, match="boot-pool"):
        validated_zvol_name("boot-pool/x")


def test_import_end_to_end_through_the_cli(config, opener, capsys, server):
    vm = make_vm(server)
    server.image_sizes[IMAGE] = 3 * GIB
    cli = build_cli(opener=opener)
    base = ["vm", "disk-import", "guest", "--source", IMAGE, "--zvol", "tank/vm/root", "--config", str(config), "--json"]
    assert cli.run(argv=[*base, "--dry-run"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["applied"] is False and dry["import"]["zvol_size_bytes"] == 3 * GIB and dry["import"]["target"] == "new zvol"
    assert "vm.device.create" in dry["plan"] and server.vm_devices == {}
    assert cli.run(argv=base) == 1  # not applied without --allow-write
    capsys.readouterr()
    assert cli.run(argv=[*base, "--allow-write", "--size", str(4 * GIB), "--order", "2"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["conversion"] == "complete" and out["boot_order"] == 2
    device = server.vm_devices[out["device_id"]]
    assert device["order"] == 2 and device["attributes"]["path"] == "/dev/zvol/tank/vm/root"
    assert server.converted == [{"source": IMAGE, "zvol": "tank/vm/root"}]
    assert server.datasets["tank/vm/root"]["volsize"] == {"value": 4 * GIB}


def test_import_refusals_before_any_change(config, opener, capsys, server):
    make_vm(server, "running", state="RUNNING")
    make_vm(server, "autostarting", autostart=True)
    make_vm(server)
    server.image_sizes[IMAGE] = 3 * GIB
    server.datasets["tank/taken"] = server._make_dataset("tank/taken", type_="VOLUME")
    cli = build_cli(opener=opener)
    cases = [
        (["running", "--source", IMAGE, "--zvol", "tank/vm/a"], "vm-not-stopped"),
        (["autostarting", "--source", IMAGE, "--zvol", "tank/vm/a"], "autostart is enabled"),
        (["guest", "--source", IMAGE, "--zvol", "tank/taken"], "already exists"),
        (["guest", "--source", IMAGE, "--zvol", "tank/vm/a", "--size", str(GIB)], "smaller than the image"),
        (["guest", "--source", IMAGE, "--zvol", "tank/vm/a", "--size", "0"], "positive number of bytes"),
        (["guest", "--source", IMAGE, "--zvol", "tank/vm/a", "--size", "big"], "'auto' or a number"),
        (["guest", "--source", IMAGE, "--zvol", "boot-pool/vm/a"], "boot-pool"),
        (["guest", "--source", "/mnt/boot-pool/a.qcow2", "--zvol", "tank/vm/a"], "boot-pool"),
        (["guest", "--source", "/mnt/tank/missing.qcow2", "--zvol", "tank/vm/a"], "No such file"),
        (["guest", "--zvol", "tank/vm/a"], "--source"),
        (["guest", "--source", IMAGE, "--zvol", "tank/vm/a", "--order", "0"], "boot order"),
        (["nobody", "--source", IMAGE, "--zvol", "tank/vm/a"], "expected exactly one VM"),
    ]
    for argv, message in cases:
        assert cli.run(argv=["vm", "disk-import", *argv, "--config", str(config), "--allow-write"]) == 1, argv
        assert message in capsys.readouterr().err, argv
    assert server.vm_devices == {} and "tank/vm/a" not in server.datasets


def test_disk_image_size_cli_and_validation(config, opener, capsys, server):
    server.image_sizes[IMAGE] = 5 * GIB
    cli = build_cli(opener=opener)
    assert cli.run(argv=["vm", "disk-image-size", IMAGE, "--config", str(config), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["virtual_size_bytes"] == 5 * GIB
    server.image_sizes[IMAGE] = 0
    assert cli.run(argv=["vm", "disk-image-size", IMAGE, "--config", str(config)]) == 1
    assert "invalid size" in capsys.readouterr().err


def test_a_failed_conversion_keeps_the_device_and_zvol_and_says_not_to_start_the_vm(config, opener, capsys, server):
    make_vm(server)
    server.image_sizes[IMAGE] = 3 * GIB
    server.job_failures["vm.device.convert"] = "qemu-img: synthetic failure"
    cli = build_cli(opener=opener)
    code = cli.run(argv=["vm", "disk-import", "guest", "--source", IMAGE, "--zvol", "tank/vm/root", "--config", str(config),
                         "--allow-write"])
    err = capsys.readouterr().err
    assert code == 1 and "image conversion did not complete" in err and "Do not start VM guest" in err
    assert "tank/vm/root" in server.datasets and len(server.vm_devices) == 1 and server.converted == []
    # retry before cleanup is refused because the zvol name is now taken
    assert cli.run(argv=["vm", "disk-import", "guest", "--source", IMAGE, "--zvol", "tank/vm/root", "--config", str(config),
                         "--allow-write"]) == 1
    assert "already exists" in capsys.readouterr().err


def test_a_failed_boot_order_step_is_reported_after_a_successful_conversion(config, opener, capsys, server):
    make_vm(server)
    server.image_sizes[IMAGE] = 3 * GIB
    server.fail["vm.device.update"] = "synthetic update failure"
    code = build_cli(opener=opener).run(argv=["vm", "disk-import", "guest", "--source", IMAGE, "--zvol", "tank/vm/root",
                                              "--config", str(config), "--allow-write"])
    err = capsys.readouterr().err
    assert code == 1 and "boot-order update did not complete" in err and server.converted


def test_an_interrupted_conversion_is_reported(config, server, opener, monkeypatch, capsys):
    from tnt import cli as cli_module
    make_vm(server)
    server.image_sizes[IMAGE] = 3 * GIB

    real_apply = cli_module.apply_plan
    calls = []

    def apply(plan, rpc, **kwargs):
        calls.append(plan.action.calls[0].method)
        if plan.action.calls[0].method == "vm.device.convert":
            raise KeyboardInterrupt
        return real_apply(plan, rpc, **kwargs)

    monkeypatch.setattr(cli_module, "apply_plan", apply)
    code = build_cli(opener=opener).run(argv=["vm", "disk-import", "guest", "--source", IMAGE, "--zvol", "tank/vm/root",
                                              "--config", str(config), "--allow-write"])
    assert code == 1 and "interrupted" in capsys.readouterr().err and calls == ["vm.device.create", "vm.device.convert"]


def test_import_create_step_without_a_device_id_is_reported(config, opener, capsys, server, monkeypatch):
    make_vm(server)
    server.image_sizes[IMAGE] = 3 * GIB
    original = server._vm_device_create
    monkeypatch.setattr(server, "_vm_device_create", lambda params: {"no": "id", **{k: v for k, v in original(params).items()
                                                                                     if k != "id"}})
    code = build_cli(opener=opener).run(argv=["vm", "disk-import", "guest", "--source", IMAGE, "--zvol", "tank/vm/root",
                                              "--config", str(config), "--allow-write"])
    assert code == 1 and "no valid device ID" in capsys.readouterr().err


def _import_actions(session, server, *, size=3 * GIB):
    server.image_sizes[IMAGE] = 3 * GIB
    plan = session.workflows.vm_disk_import_plan("guest", IMAGE, "tank/vm/root", size)
    return plan


def test_malformed_import_actions_are_refused():
    with FakeServer() as server:
        make_vm(server)
        session = open_write(server)
        try:
            plan = _import_actions(session, server)
            create = plan.create
            payload = create.calls[0].params[0]
            attrs = payload["attributes"]

            def create_variant(**changes):
                new_attrs = {**attrs, **changes.pop("attributes", {})}
                new_payload = {**payload, **changes, "attributes": new_attrs}
                return Action("TNT-61", Kind.MUTATE, "x", (RpcCall("vm.device.create", (new_payload,)),), create.probe)

            for variant in (create_variant(order=1), create_variant(vm=999), create_variant(attributes={"dtype": "NIC"}),
                            create_variant(attributes={"path": "/x"}), create_variant(attributes={"create_zvol": False}),
                            create_variant(attributes={"type": "SCSI"}), create_variant(attributes={"zvol_volsize": True}),
                            create_variant(attributes={"zvol_name": "tank/other"}),
                            create_variant(attributes={"zvol_volsize": GIB}),
                            Action("TNT-61", Kind.MUTATE, "x", create.calls, create.probe[:2]),
                            Action("TNT-61", Kind.DESTRUCTIVE, "x", create.calls, create.probe, "t"),
                            Action("TNT-61", Kind.MUTATE, "x", (RpcCall("vm.device.create", ({**payload, "extra": 1},)),), create.probe)):
                with pytest.raises(Refused):
                    make_plan(variant, session.rpc)
            server.image_sizes[IMAGE] = 0
            with pytest.raises(Refused, match="invalid size"):
                make_plan(create, session.rpc)
            server.image_sizes[IMAGE] = 3 * GIB
            server.datasets["tank/vm/root"] = server._make_dataset("tank/vm/root", type_="VOLUME")
            with pytest.raises(Refused, match="already exists"):
                make_plan(create, session.rpc)
        finally:
            session.close()


def test_malformed_convert_actions_are_refused():
    with FakeServer() as server:
        vm = make_vm(server)
        session = open_write(server)
        try:
            plan = _import_actions(session, server)
            created = run(session, plan.create)[0]
            good = plan.convert_action(created["id"])
            data = good.calls[0].params[0]
            probe_vm, probe_dev, probe_ds = good.probe
            variants = [
                Action("TNT-61", Kind.MUTATE, "x", (RpcCall("vm.device.convert", (data,)),), good.probe),  # not a job
                Action("TNT-61", Kind.MUTATE, "x", (RpcCall("vm.device.convert", ({**data, "x": 1},), job=True),), good.probe),
                Action("TNT-61", Kind.MUTATE, "x", (RpcCall("vm.device.convert", ({**data, "source": "/tmp/a.qcow2"},), job=True),), good.probe),
                Action("TNT-61", Kind.MUTATE, "x", (RpcCall("vm.device.convert", ({**data, "destination": "/dev/zvol/tank/other"},), job=True),), good.probe),
                Action("TNT-61", Kind.MUTATE, "x", good.calls, good.probe[:2]),
                Action("TNT-61", Kind.MUTATE, "x", good.calls, (probe_vm, RpcCall("vm.device.query", ([["id", "=", 999]], {})), probe_ds)),
                Action("TNT-61", Kind.MUTATE, "x", good.calls, (probe_vm, RpcCall("vm.device.query", ([], {})), probe_ds)),
            ]
            for variant in variants:
                with pytest.raises(Refused):
                    make_plan(variant, session.rpc)
            # a device that is not the destination zvol disk
            server.vm_devices[created["id"]]["attributes"]["path"] = "/dev/zvol/tank/other"
            with pytest.raises(Refused, match="not the new disk"):
                make_plan(good, session.rpc)
            server.vm_devices[created["id"]]["attributes"]["path"] = "/dev/zvol/tank/vm/root"
            server.datasets["tank/vm/root"]["type"] = "FILESYSTEM"
            with pytest.raises(Refused, match="not an existing zvol"):
                make_plan(good, session.rpc)
            server.datasets["tank/vm/root"]["type"] = "VOLUME"
            server.vms[vm["id"]]["autostart"] = True
            with pytest.raises(Refused, match="autostart is enabled"):
                make_plan(good, session.rpc)
        finally:
            session.close()


def test_vm_start_between_plan_and_apply_is_refused_for_the_convert_step():
    with FakeServer() as server:
        vm = make_vm(server)
        session = open_write(server)
        try:
            plan = _import_actions(session, server)
            created = run(session, plan.create)[0]
            convert = make_plan(plan.convert_action(created["id"]), session.rpc)
            server.vms[vm["id"]]["status"] = {"state": "RUNNING", "pid": 5}
            with pytest.raises(Refused, match="vm-not-stopped|state-drift"):
                apply_plan(convert, session.rpc, allow_write=True)
            assert server.converted == []
        finally:
            session.close()


def test_convert_job_failure_surfaces_as_a_partial_plan_error():
    with FakeServer() as server:
        make_vm(server)
        server.job_failures["vm.device.convert"] = "boom"
        session = open_write(server)
        try:
            plan = _import_actions(session, server)
            created = run(session, plan.create)[0]
            with pytest.raises(PlanExecutionError, match="FAILED|failed|boom"):
                run(session, plan.convert_action(created["id"]))
        finally:
            session.close()


# -- TNT-61 verified upload from downloads/ --------------------------------------------------------------------------
def make_downloads(tmp_path, name="debian.qcow2", content=b"qcow2-bytes" * 100, sidecar="exact"):
    root = tmp_path / "downloads"
    root.mkdir(parents=True, exist_ok=True)
    (root / name).parent.mkdir(parents=True, exist_ok=True)
    (root / name).write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    if sidecar == "exact":
        (root / f"{name}.sha256").write_text(f"{digest}  {name}\n")
    elif sidecar == "bare":
        (root / f"{name}.sha256").write_text(digest + "\n")
    elif sidecar == "wrong":
        (root / f"{name}.sha256").write_text("0" * 64 + "\n")
    elif sidecar == "junk":
        (root / f"{name}.sha256").write_text("not a digest\n")
    return root, content, digest


@pytest.mark.parametrize("name", ["/etc/passwd", "../x", "a/../x", "a//b", "./a", "a/", "", "  ", "a\\b", "a\x00b",
                                  "debian.qcow2.sha256", None, 7])
def test_download_names_are_confined_to_the_downloads_folder(name):
    with pytest.raises(Refused, match="upload-source"):
        uploads.validate_download_name(name)


def test_verify_download_accepts_nested_files_and_both_sidecar_forms(tmp_path):
    for sidecar in ("exact", "bare"):
        root, content, digest = make_downloads(tmp_path / sidecar, name="sub/dir/img.qcow2", sidecar=sidecar)
        verified = uploads.verify_download("sub/dir/img.qcow2", root)
        assert verified.sha256 == digest and verified.size == len(content)
        verified.reverify()
        with verified.open() as handle:
            assert handle.read() == content


def test_verify_download_refuses_missing_wrong_or_changed_material(tmp_path):
    root, content, digest = make_downloads(tmp_path / "a", sidecar="none")
    with pytest.raises(Refused, match="upload-checksum.*required"):
        uploads.verify_download("debian.qcow2", root)
    root, _, _ = make_downloads(tmp_path / "b", sidecar="junk")
    with pytest.raises(Refused, match="64-digit"):
        uploads.verify_download("debian.qcow2", root)
    root, _, _ = make_downloads(tmp_path / "c", sidecar="wrong")
    with pytest.raises(Refused, match="does not match"):
        uploads.verify_download("debian.qcow2", root)
    root, _, _ = make_downloads(tmp_path / "d")
    verified = uploads.verify_download("debian.qcow2", root)
    (root / "debian.qcow2").write_bytes(b"tampered")
    with pytest.raises(Refused, match="changed since"):
        verified.reverify()
    with pytest.raises(Refused, match="does not exist"):
        uploads.verify_download("absent.qcow2", root)


def test_verify_download_refuses_links_empty_files_directories_and_a_missing_folder(tmp_path):
    root, content, digest = make_downloads(tmp_path / "a")
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"secret")
    (root / "link.bin").symlink_to(outside)
    (root / "link.bin.sha256").write_text(hashlib.sha256(b"secret").hexdigest() + "\n")
    with pytest.raises(Refused, match="outside downloads"):
        uploads.verify_download("link.bin", root)
    (root / "empty.bin").write_bytes(b"")
    (root / "empty.bin.sha256").write_text(hashlib.sha256(b"").hexdigest() + "\n")
    with pytest.raises(Refused, match="non-empty regular"):
        uploads.verify_download("empty.bin", root)
    (root / "sub").mkdir()
    (root / "sub.sha256").write_text(digest + "\n")
    with pytest.raises(Refused, match="non-empty regular"):
        uploads.verify_download("sub", root)
    with pytest.raises(Refused, match="downloads/ folder was not found"):
        uploads.verify_download("x", tmp_path / "nope")
    afile = tmp_path / "afile"
    afile.write_text("x")
    with pytest.raises(Refused, match="downloads/ folder was not found"):
        uploads.verify_download("x", afile)


def test_the_downloads_folder_is_derived_from_the_package_location_and_has_no_env_override(monkeypatch, tmp_path):
    expected = Path(uploads.__file__).resolve().parents[4] / "downloads"
    assert uploads.DOWNLOADS_DIR == expected
    monkeypatch.setenv("TNT_DOWNLOADS", str(tmp_path))
    monkeypatch.setenv("DOWNLOADS_DIR", str(tmp_path))
    assert uploads.DOWNLOADS_DIR == expected
    monkeypatch.setattr(uploads, "DOWNLOADS_DIR", tmp_path / "missing")
    with pytest.raises(Refused, match="downloads/ folder was not found"):
        uploads.verify_download("a.bin")
    assert "os.environ" not in Path(uploads.__file__).read_text() and "getenv" not in Path(uploads.__file__).read_text()


@pytest.mark.parametrize("directory", ["tank/images", "/etc", "/mnt/tank", "/mnt/tank/", "/mnt/tank/../x", "/mnt/tank//x",
                                       "/mnt/boot-pool/x", None, "/mnt/tank/./x"])
def test_upload_destinations_are_nas_dataset_directories(directory):
    with pytest.raises(Refused, match="upload-destination"):
        uploads.validate_destination_dir(directory)
    assert uploads.validate_destination_dir("/mnt/tank/images") == "/mnt/tank/images"


@pytest.fixture
def upload_env(server, tmp_path, monkeypatch):
    root, content, digest = make_downloads(tmp_path)
    monkeypatch.setattr(uploads, "DOWNLOADS_DIR", root)
    monkeypatch.setattr(uploads, "_origin", lambda profile: (False, "127.0.0.1", server.http_port))
    monkeypatch.setattr(uploads, "JOB_POLL_SECONDS", 0.01)
    return root, content, digest


def test_files_put_uploads_through_the_api_and_never_overwrites(config, opener, capsys, server, upload_env):
    root, content, digest = upload_env
    cli = build_cli(opener=opener)
    argv = ["files", "put", "debian.qcow2", "/mnt/tank/images", "--config", str(config), "--json"]
    assert cli.run(argv=[*argv, "--dry-run"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["applied"] is False and "filesystem.put" in dry["plan"] and digest[:12] in dry["plan"]
    assert server.upload_requests == [] and server.files == {}
    assert cli.run(argv=argv) == 1  # plan only without --allow-write
    capsys.readouterr()
    assert cli.run(argv=[*argv, "--allow-write"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["completed"] is True and result["result"][0]["sha256"] == digest
    assert server.files["/mnt/tank/images/debian.qcow2"] == content
    request = server.upload_requests[0]
    assert request["authorized"] and request["method"] == "filesystem.put" and request["mode"] == 0o644
    # a second upload to the same name is refused by the plan probe and sends nothing
    before = len(server.upload_requests)
    assert cli.run(argv=[*argv, "--allow-write"]) == 1
    assert "already exists" in capsys.readouterr().err and len(server.upload_requests) == before


def test_files_put_refusals(config, opener, capsys, server, upload_env, tmp_path):
    cli = build_cli(opener=opener)
    base = ["--config", str(config), "--allow-write"]
    for argv, message in [
        (["files", "put", "/etc/passwd", "/mnt/tank/images"], "absolute paths are refused"),
        (["files", "put", "../x", "/mnt/tank/images"], "'..'"),
        (["files", "put", "missing.bin", "/mnt/tank/images"], "does not exist"),
        (["files", "put", "debian.qcow2", "/mnt/boot-pool/images"], "boot-pool"),
        (["files", "put", "debian.qcow2", "/mnt/tank/nowhere"], "No such file"),
    ]:
        assert cli.run(argv=[*argv, *base]) == 1, argv
        assert message in capsys.readouterr().err, argv
    (upload_env[0] / "debian.qcow2").write_bytes(b"tampered")
    assert cli.run(argv=["files", "put", "debian.qcow2", "/mnt/tank/images", *base]) == 1
    assert "does not match" in capsys.readouterr().err
    assert server.upload_requests == []


def test_upload_http_failure_modes(config, opener, capsys, server, upload_env):
    cli = build_cli(opener=opener)
    argv = ["files", "put", "debian.qcow2", "/mnt/tank/images", "--config", str(config), "--allow-write"]
    server.upload_status = 500
    assert cli.run(argv=argv) == 1
    out = capsys.readouterr().out  # a failure after the call started is a reviewed partial-result report
    assert "HTTP 500" in out and '"completed": false' in out
    server.upload_status = 200
    server.upload_job_state = "FAILED"
    assert cli.run(argv=argv) == 1
    assert "ended FAILED" in capsys.readouterr().out and server.files == {}


def test_upload_sender_error_branches(server, upload_env, monkeypatch):
    from tnt.profile import Secret
    root, _, _ = upload_env
    download = uploads.verify_download("debian.qcow2")
    profile = server.profile()
    secret = server.secret()
    # bad credentials
    wrong = Secret("1-wrong-key-material")
    with pytest.raises(Refused, match="HTTP 401"):
        uploads.post_upload(profile, wrong, False, download, "/mnt/tank/images/a.bin", 0o644)
    # password authentication uses Basic and succeeds
    password_profile = server.profile(auth="password")
    job = uploads.post_upload(password_profile, server.password_secret(), False, download, "/mnt/tank/images/p.bin", 0o644)
    assert isinstance(job, int) and server.files["/mnt/tank/images/p.bin"]
    # a connection refusal is scrubbed and reported
    monkeypatch.setattr(uploads, "_origin", lambda profile: (False, "127.0.0.1", 9))
    with pytest.raises(Refused, match="upload failed"):
        uploads.post_upload(profile, secret, False, download, "/mnt/tank/images/c.bin", 0o644)


def test_upload_origin_and_response_validation(monkeypatch, server, upload_env):
    from tnt.profile import Profile
    assert REAL_ORIGIN(Profile("a", "wss://nas.example.invalid/api/current", "u")) == (True, "nas.example.invalid", 443)
    assert REAL_ORIGIN(Profile("a", "ws://127.0.0.1:81/api/current", "u")) == (False, "127.0.0.1", 81)
    with pytest.raises(Refused, match="no supported"):
        REAL_ORIGIN(Profile("a", "https://nas.example.invalid/x", "u"))
    download = uploads.verify_download("debian.qcow2")

    class Reply:
        def __init__(self, status, body):
            self.status, self._body = status, body

        def read(self, limit):
            return self._body

    for body, message in ((b"not json", "no job ID"), (b'{"job_id": true}', "invalid job ID"), (b'{"job_id": 0}', "invalid job ID"),
                          (b'{}', "no job ID")):
        class Conn:
            def __init__(self, *args, **kwargs):
                pass

            def putrequest(self, *a):
                pass

            putheader = endheaders = send = close = putrequest

            def getresponse(self, _body=body):
                return Reply(200, _body)

        monkeypatch.setattr(uploads.http.client, "HTTPConnection", Conn)
        with pytest.raises(Refused, match=message):
            uploads.post_upload(server.profile(), server.secret(), False, download, "/mnt/tank/images/z.bin", 0o644)


def test_upload_uses_verified_tls_context_for_wss(monkeypatch, server, upload_env):
    download = uploads.verify_download("debian.qcow2")
    seen = []

    class Conn:
        def __init__(self, host, port, timeout=None, context=None):
            seen.append(context)

        def putrequest(self, *a):
            raise OSError("stop here")

        def close(self):
            pass

    monkeypatch.setattr(uploads, "_origin", lambda profile: (True, "nas.example.invalid", 443))
    monkeypatch.setattr(uploads.http.client, "HTTPSConnection", Conn)
    for insecure in (False, True):
        with pytest.raises(Refused, match="upload failed"):
            uploads.post_upload(server.profile(), server.secret(), insecure, download, "/mnt/tank/images/z.bin", 0o644)
    assert seen[0].verify_mode.name == "CERT_REQUIRED" and seen[1].verify_mode.name == "CERT_NONE"


def test_wait_for_job_states_and_timeout():
    class Client:
        def __init__(self, rows):
            self.rows = list(rows)

        def call(self, method, filters):
            return self.rows.pop(0) if len(self.rows) > 1 else self.rows[0]

    ok = uploads.wait_for_job(Client([[{"state": "RUNNING"}], [{"state": "SUCCESS"}]]), 4, sleep=lambda s: None)
    assert ok["state"] == "SUCCESS"
    with pytest.raises(Refused, match="ABORTED: no reason"):
        uploads.wait_for_job(Client([[{"state": "ABORTED"}]]), 4)
    with pytest.raises(Refused, match="did not finish"):
        uploads.wait_for_job(Client([[]]), 4, timeout=0, sleep=lambda s: None)
    with pytest.raises(Refused, match="did not finish"):
        uploads.wait_for_job(Client(["junk"]), 4, timeout=0)


def test_guard_runs_an_upload_only_for_a_registered_destination(server):
    with FakeServer() as live:
        session = open_write(live)
        try:
            session.rpc.register_upload("/mnt/tank/images/a.bin", lambda client: {"ok": True})
            with pytest.raises(Refused):
                session.rpc.call("filesystem.put", "/mnt/tank/images/a.bin", {"mode": 420})  # outside an applied plan
            with pytest.raises(Refused, match="upload-source"):
                session.rpc.register_upload(3, lambda client: None)
            with pytest.raises(Refused, match="upload-source"):
                session.rpc._run_upload(("/mnt/tank/images/other.bin", {"mode": 420}))
            with pytest.raises(Refused, match="upload-source"):
                session.rpc._run_upload(())
        finally:
            session.close()


def test_guard_supports_is_open_without_a_capability_list():
    from tnt.transport import GuardedRpc

    class Nothing:
        def call(self, *args, **kwargs):
            return None

        def close(self):
            pass

    assert GuardedRpc(Nothing()).supports("vm.device.convert") is True
