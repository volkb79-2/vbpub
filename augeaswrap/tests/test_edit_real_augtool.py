"""Real-tool Fstab edit regressions; the registered tester lane requires augeas-tools."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from augeaswrap import Edit, PlanRefused, Request, apply, augtool, plan
from augeaswrap.formats.fstab import mount_item

FIXTURE = (
    b"# /etc/fstab: static file system information.\n"
    b"#\n"
    b"# <file system> <mount point> <type> <options> <dump> <pass>\n"
    b"UUID=aaaaaaaa-0000-0000-0000-000000000001 /     ext4  errors=remount-ro,noatime 0 1\n"
    b"\n"
    b"# swap lives here\n"
    b"/dev/null  none  swap  sw  0  0\n"
    b"LABEL=data /srv  xfs   defaults,nofail,x-systemd.device-timeout=5  0  2\n"
)


@pytest.fixture(autouse=True)
def require_real_augeas_tools():
    missing = [name for name in ("augtool", "augparse") if shutil.which(name) is None]
    if missing:
        pytest.fail(
            "real Augeas tests require augtool and augparse from augeas-tools; "
            f"missing: {', '.join(missing)}",
            pytrace=False,
        )


def fixture_root(tmp_path: Path, source: bytes = FIXTURE) -> tuple[Path, Path]:
    root = tmp_path / "root"
    target = root / "etc/fstab"
    target.parent.mkdir(parents=True)
    target.write_bytes(source)
    return root, target


def record_plan(root: Path, item: dict[str, object]):
    resource = Edit(
        file="/etc/fstab",
        lens="Fstab",
        state="present",
        ops=[{"records": "*", "key": "file", "exclusive": False, "items": [item]}],
        create=False,
        on_change="none",
    )
    return plan(Request("real augtool Fstab regression", (resource,)), root=root)


def test_real_augtool_appends_a_new_fstab_record_in_a_private_root(tmp_path):
    root, target = fixture_root(tmp_path)
    item = mount_item("UUID=bbbbbbbb-0000-0000-0000-000000000002", "/data", "ext4", ["defaults"], "0", "2")

    planned = record_plan(root, item)

    assert planned.has_changes
    assert any(line.startswith("+UUID=bbbbbbbb-0000-0000-0000-000000000002\t/data\text4\tdefaults\t0 2") for line in planned.changes[0].details)
    assert target.read_bytes() == FIXTURE


def test_real_augtool_edits_one_record_and_preserves_comments_and_other_lines(tmp_path):
    root, target = fixture_root(tmp_path)
    old_line = b"LABEL=data /srv  xfs   defaults,nofail,x-systemd.device-timeout=5  0  2\n"
    new_line = b"LABEL=data\t/srv\txfs\tnoatime\t0 2\n"
    item = mount_item("LABEL=data", "/srv", "xfs", ["noatime"], "0", "2")

    planned = record_plan(root, item)
    assert planned.has_changes
    apply(planned)

    assert target.read_bytes() == FIXTURE.replace(old_line, new_line, 1)


def test_real_augtool_unchanged_tree_has_no_augnew_and_equal_equals_options_are_noop(tmp_path):
    root, target = fixture_root(tmp_path)
    item = mount_item(
        "UUID=aaaaaaaa-0000-0000-0000-000000000001",
        "/",
        "ext4",
        ["errors=remount-ro", "noatime"],
        "0",
        "1",
    )

    planned = record_plan(root, item)
    unchanged = augtool.run(
        path="/etc/fstab",
        lens="Fstab",
        source=target.read_bytes(),
        script=augtool.edit_script([]),
        save_new=True,
    )

    assert not planned.has_changes
    assert unchanged.saved is None
    assert target.read_bytes() == FIXTURE


def test_real_augtool_keeps_order_and_repeated_options(tmp_path):
    root, _target = fixture_root(tmp_path)
    item = mount_item(
        "UUID=cccccccc-0000-0000-0000-000000000003",
        "/ordered",
        "ext4",
        ["noatime", "nofail", "noatime"],
        "0",
        "2",
    )

    planned = record_plan(root, item)

    assert planned.has_changes
    assert any("\tnoatime,nofail,noatime\t0 2" in line for line in planned.changes[0].details)


def test_real_augtool_encodes_options_with_equals_as_name_and_value(tmp_path):
    root, _target = fixture_root(tmp_path)
    item = mount_item(
        "UUID=dddddddd-0000-0000-0000-000000000004",
        "/options",
        "ext4",
        ["errors=remount-ro", "x-systemd.device-timeout=5"],
        "0",
        "2",
    )

    planned = record_plan(root, item)

    assert planned.has_changes
    assert any("\terrors=remount-ro,x-systemd.device-timeout=5\t0 2" in line for line in planned.changes[0].details)


def test_real_augtool_save_failure_includes_the_post_save_lens_diagnostic():
    with pytest.raises(PlanRefused) as failure:
        augtool.run(
            path="/etc/fstab",
            lens="Fstab",
            source=FIXTURE,
            script=augtool.edit_script(
                [
                    'ins 01 after "/files/etc/fstab/3"',
                    'set "/files/etc/fstab/01[1]/spec" "UUID=eeee"',
                    'set "/files/etc/fstab/01[1]/file" "/bad"',
                    'set "/files/etc/fstab/01[1]/vfstype" "ext4"',
                    'set "/files/etc/fstab/01[1]/opt[1]" "errors=remount-ro"',
                    'set "/files/etc/fstab/01[1]/dump" "0"',
                    'set "/files/etc/fstab/01[1]/passno" "2"',
                ]
            ),
            save_new=True,
        )

    assert failure.value.problems[0].code == "edit.augtool.failed"
    assert "/augeas/files/etc/fstab/error/message" in failure.value.problems[0].message
    assert "Failed to match tree under /files/etc/fstab/01" in failure.value.problems[0].message
