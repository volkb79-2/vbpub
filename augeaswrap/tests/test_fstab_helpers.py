"""Golden contracts and refusal cases for pure fstab intent helpers."""

from __future__ import annotations

import pytest

from augeaswrap.formats.fstab import mount_item, mount_ops, swap_item, swap_op


def test_mount_item_and_ops_have_fstab_lens_field_order_and_repeated_options():
    item = mount_item("PARTUUID=abcd", "/srv/data", "ext4", ["noatime", "defaults"], "0", "2")
    assert list(item.items()) == [
        ("spec", "PARTUUID=abcd"),
        ("file", "/srv/data"),
        ("vfstype", "ext4"),
        ("opt", ["noatime", "defaults"]),
        ("dump", "0"),
        ("passno", "2"),
    ]
    assert mount_ops([item]) == (
        {
            "records": "*",
            "key": "file",
            "exclusive": False,
            "items": [item],
        },
    )


def test_swap_item_and_op_emit_explicit_pri_field_and_exclusive_selector():
    item = swap_item("PARTUUID=swap-1", 10, discard=True)
    assert list(item.items()) == [
        ("spec", "PARTUUID=swap-1"),
        ("file", "none"),
        ("vfstype", "swap"),
        ("opt", ["sw", "pri=10", "discard"]),
        ("dump", "0"),
        ("passno", "0"),
    ]
    assert swap_op([item]) == {
        "records": "*[vfstype='swap']",
        "key": "spec",
        "exclusive": True,
        "items": [item],
    }
    assert swap_op([])["items"] == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source", "UUID=one two"),
        ("mountpoint", "/srv/with\nnewline"),
        ("filesystem", "ext4#comment"),
        ("option", "rw\tnoatime"),
        ("dump", "0 1"),
        ("passno", "2#bad"),
    ],
)
def test_mount_item_refuses_whitespace_newline_or_comment_marker(field, value):
    values = {
        "source": "UUID=good",
        "mountpoint": "/srv/data",
        "filesystem": "ext4",
        "options": ["defaults"],
        "dump": "0",
        "passno": "2",
    }
    if field == "option":
        values["options"] = [value]
    else:
        values[field] = value
    with pytest.raises(ValueError, match="whitespace|non-empty|decimal"):
        mount_item(**values)


def test_mount_helpers_refuse_empty_device_invalid_mountpoint_and_duplicate_mountpoint():
    with pytest.raises(ValueError, match="device"):
        mount_item("", "/srv/data", "ext4", ["defaults"], "0", "2")
    with pytest.raises(ValueError, match="absolute path"):
        mount_item("UUID=x", "srv/data", "ext4", ["defaults"], "0", "2")
    first = mount_item("UUID=a", "/srv/data", "ext4", ["defaults"], "0", "2")
    second = mount_item("UUID=b", "/srv/data", "xfs", ["defaults"], "0", "2")
    with pytest.raises(ValueError, match="duplicate fstab mountpoint"):
        mount_ops([first, second])


def test_mount_helpers_refuse_an_empty_option_list():
    with pytest.raises(ValueError, match="non-empty sequence"):
        mount_item("UUID=x", "/srv/data", "ext4", [], "0", "2")


def test_mount_helpers_refuse_invalid_types_fields_and_numeric_ranges():
    with pytest.raises(ValueError, match="non-empty string"):
        mount_item(1, "/srv/data", "ext4", ["defaults"], "0", "2")
    with pytest.raises(ValueError, match="non-empty sequence"):
        mount_item("UUID=x", "/srv/data", "ext4", "defaults", "0", "2")
    with pytest.raises(ValueError, match="decimal integer"):
        mount_item("UUID=x", "/srv/data", "ext4", ["defaults"], "-1", "2")
    with pytest.raises(ValueError, match="at most 1"):
        mount_item("UUID=x", "/srv/data", "ext4", ["defaults"], "2", "2")
    assert mount_ops([]) == ()
    with pytest.raises(ValueError, match="must be a sequence"):
        mount_ops("not a sequence")
    with pytest.raises(ValueError, match="mappings from mount_item"):
        mount_ops([None])
    with pytest.raises(ValueError, match="no mountpoint"):
        mount_ops([{"spec": "UUID=x"}])


def test_swap_helper_refuses_empty_device_duplicate_device_and_invalid_priority():
    with pytest.raises(ValueError, match="device"):
        swap_item("", 1)
    with pytest.raises(ValueError, match="priority"):
        swap_item("UUID=swap", True)
    with pytest.raises(ValueError, match="priority"):
        swap_item("UUID=swap", 32768)
    item = swap_item("UUID=swap", 1)
    with pytest.raises(ValueError, match="duplicate fstab swap device"):
        swap_op([item, item])
    with pytest.raises(ValueError, match="device"):
        swap_op([{"spec": "", "file": "none", "vfstype": "swap"}])
    with pytest.raises(ValueError, match="discard"):
        swap_item("UUID=swap", 1, discard=1)


def test_swap_helpers_refuse_invalid_sequence_items_and_priority_types():
    with pytest.raises(ValueError, match="integer from"):
        swap_item("UUID=swap", "1")
    with pytest.raises(ValueError, match="must be a sequence"):
        swap_op("not a sequence")
    with pytest.raises(ValueError, match="must come from swap_item"):
        swap_op([None])
    with pytest.raises(ValueError, match="must come from swap_item"):
        swap_op([{"spec": "UUID=x", "file": "none", "vfstype": "ext4"}])
