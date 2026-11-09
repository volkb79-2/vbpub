"""Focused negative tests for strict edit parsing and registry hooks."""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from augeaswrap import Change, Edit, PlanRefused
from augeaswrap import edit as edit_module


def make_edit(**changes):
    values = {
        "file": "/etc/fstab",
        "lens": "Fstab",
        "state": "present",
        "ops": (),
        "create": False,
        "on_change": "none",
    }
    values.update(changes)
    return Edit(**values)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"lens": "bad/lens"}, "lens module"),
        ({"state": "new"}, "state must"),
        ({"create": 1}, "create must be a boolean"),
        ({"on_change": "restart"}, "on_change must"),
        ({"ops": "not-an-array"}, "ops must be an array"),
        ({"accept_duplicates": "*"}, "accept_duplicates must be an array"),
        ({"accept_duplicates": ["*", "*"]}, "repeated selector"),
        ({"accept_duplicates": ["/etc/fstab"]}, "relative to the edited file"),
        ({"state": "absent", "validate": "findmnt"}, "absent edit"),
        ({"validate": ["cmd", "", "%s"]}, "non-empty argv"),
        ({"validate": "unknown-validator"}, "edit validate must be"),
    ],
)
def test_edit_constructor_refuses_invalid_policy_values(changes, message):
    with pytest.raises(ValueError, match=message):
        make_edit(**changes)


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        ({1: "set"}, "keys must be strings"),
        ({}, "exactly one of"),
        ({"set": "entry/value", "rm": "entry/value"}, "exactly one of"),
        ({"set": "/entry/value", "value": "x"}, "relative to the edited file"),
        ({"set": "entry/value", "value": ""}, "non-empty single-line"),
        ({"records": "*", "key": "bad/key", "items": [], "exclusive": False}, "simple Augeas label"),
        ({"records": "*", "key": "file", "items": [], "exclusive": 1}, "exclusive must be a boolean"),
        ({"records": "*", "key": "file", "items": "bad", "exclusive": False}, "items must be an array"),
        ({"records": "*", "key": "file", "items": [{"file": []}], "exclusive": False}, "only opt may"),
        ({"records": "*", "key": "file", "items": [{}], "exclusive": False}, "at least one field"),
        ({"records": "*", "key": "file", "items": [{"bad/name": "x"}], "exclusive": False}, "simple Augeas labels"),
        ({"records": "*", "key": "file", "items": [{"opt": "defaults"}], "exclusive": False}, "opt must be a non-empty string list"),
        ({"records": "*", "key": "file", "items": [{"opt": []}], "exclusive": False}, "opt must be a non-empty string list"),
        ({"records": "*", "key": "file", "items": [{"file": 1}], "exclusive": False}, "only opt may"),
        ({"records": "*", "key": "spec", "items": [{"file": "/mnt"}], "exclusive": False}, "must contain scalar key field"),
        (
            {"records": "*", "key": "file", "items": [{"file": "/mnt"}, {"file": "/mnt"}], "exclusive": False},
            "duplicate file values",
        ),
    ],
)
def test_edit_operation_parser_refuses_malformed_operations(operation, message):
    with pytest.raises(ValueError, match=message):
        make_edit(ops=[operation])


def test_edit_private_tree_and_match_guards_refuse_corrupt_observations():
    resource = make_edit(ops=[{"set": "entry/value", "value": "x"}])
    with pytest.raises(ValueError, match="invalid Augeas tree"):
        edit_module._operation_commands(resource, {"tree": [], "matches": {}})
    with pytest.raises(ValueError, match="invalid match paths"):
        edit_module._operation_commands(resource, {"tree": {}, "matches": {"0": [1]}})
    with pytest.raises(ValueError, match="invalid match data"):
        resource._guard_duplicates({"tree": [], "matches": {}})
    with pytest.raises(ValueError, match="must contain an image"):
        resource.diff(SimpleNamespace(root=Path("/")), {resource.addresses()[0]: {}})


def test_private_edit_helpers_refuse_unreachable_invalid_states():
    resource = make_edit(ops=[{"set": "entry/value", "value": "x"}])
    with pytest.raises(ValueError, match="unsupported operation kind"):
        object.__setattr__(resource, "ops", (edit_module._Operation("unknown"),))
        edit_module._operation_commands(resource, {"tree": {}, "matches": {"0": []}})

    resource = make_edit(ops=[{"set": "entry/value", "value": "x"}])
    with pytest.raises(ValueError, match="invalid Augeas tree data"):
        edit_module._observe_operation(resource, b"source", 0, tree=[])


def test_incremental_record_helpers_refuse_invalid_snapshots_and_indices():
    resource = make_edit(
        ops=[{"records": "*", "key": "file", "items": [{"file": "/mnt"}], "exclusive": True}]
    )
    operation = resource.ops[0]
    with pytest.raises(ValueError, match="file image"):
        edit_module._apply_operations(resource, {})
    with pytest.raises(ValueError, match="invalid Augeas tree"):
        edit_module._apply_operations(resource, {"image": {"exists": False}, "tree": []})
    with pytest.raises(ValueError, match="invalid operation index"):
        edit_module._operation_commands(resource, {"tree": {}, "matches": {}}, (1,))
    with pytest.raises(ValueError, match="reconciled against staged observations"):
        edit_module._operation_commands(resource, {"tree": {}, "matches": {"0": []}})
    with pytest.raises(ValueError, match="invalid operation index"):
        resource._guard_duplicates({"tree": {}, "matches": {}}, operation_indices=(1,))
    with pytest.raises(ValueError, match="invalid Augeas tree data"):
        edit_module._record_item_commands(resource, operation, operation.items[0], {"tree": [], "matches": {}}, 0)
    with pytest.raises(ValueError, match="invalid match paths"):
        edit_module._record_item_commands(
            resource,
            operation,
            operation.items[0],
            {"tree": {}, "matches": {"0": [1]}},
            0,
        )
    with pytest.raises(ValueError, match="invalid Augeas tree data"):
        edit_module._record_exclusive_removal_commands(resource, operation, {"tree": [], "matches": {}}, 0)
    with pytest.raises(ValueError, match="invalid match paths"):
        edit_module._record_exclusive_removal_commands(
            resource,
            operation,
            {"tree": {}, "matches": {"0": [1]}},
            0,
        )
    assert edit_module._record_item_commands(
        resource,
        operation,
        operation.items[0],
        {"tree": {}, "matches": {"0": ["/other/fstab/01[1]"]}},
        0,
    ) == [
        'set "/files/etc/fstab/01[1]/file" "/mnt"'
    ]
    assert edit_module._record_exclusive_removal_commands(
        resource,
        operation,
        {"tree": {}, "matches": {"0": ["/other/fstab/01[1]"]}},
        0,
    ) == []
    assert edit_module._run_edit_commands(resource, b"same", []) == b"same"


def test_records_guards_reject_unsupported_tree_labels_and_bad_sequence_fields():
    assert edit_module._record_values(
        {"01[1]": [("not/a/field", "value")]},
        "/files/etc/fstab/01[1]",
        "/files/etc/fstab",
    ) == {}
    assert edit_module._sequence_index("02[1]") is None
    assert edit_module._new_node_path("/files/etc/fstab", "other", {"01": []}) == "/files/etc/fstab/01[2]"

    resource = make_edit(
        ops=[{"records": "*", "key": "file", "items": [{"file": "/mnt"}], "exclusive": False}]
    )
    with pytest.raises(ValueError, match="unsupported node label"):
        edit_module._record_item_commands(
            resource,
            resource.ops[0],
            resource.ops[0].items[0],
            {
                "tree": {"odd": []},
                "matches": {"0": ["/files/etc/fstab/odd"]},
            },
            0,
        )


def test_image_helpers_validate_bytes_metadata_and_snapshot_shapes():
    with pytest.raises(ValueError, match="no file bytes"):
        edit_module._decode_image({"exists": True})
    assert edit_module._decode_image({"exists": False}) == b""
    with pytest.raises(ValueError, match="metadata is invalid"):
        edit_module._metadata({"exists": True, "mode": True, "uid": 0, "gid": 0})

    resource = make_edit()
    change = Change("edit", resource.addresses()[0], "change", {}, {}, resource)
    with pytest.raises(ValueError, match="no file image"):
        edit_module._snapshot(change, None)
    with pytest.raises(ValueError, match="no file image"):
        edit_module._snapshot(change, {"image": None})
    image = {
        "exists": True,
        "sha256": "digest",
        "mode": 0o640,
        "uid": 4,
        "gid": 5,
        "data_b64": base64.b64encode(b"before").decode("ascii"),
    }
    snapshot = edit_module._snapshot(change, {"image": image})
    assert snapshot["data_b64"] == image["data_b64"]
    assert edit_module._snapshot(change, {"image": {"exists": False}})["exists"] is False


def test_registry_restore_hooks_validate_records_and_project_shapes(tmp_path):
    resource = make_edit()
    change = Change("edit", resource.addresses()[0], "change", {}, {}, resource)
    with pytest.raises(ValueError, match="no path"):
        edit_module._restore(resource.addresses()[0], {}, ())
    with pytest.raises(ValueError, match="does not match its address"):
        edit_module._restore("edit:/etc/other", {"path": "/etc/fstab"}, ())
    restored = edit_module._restore(resource.addresses()[0], {"path": "/etc/fstab", "exists": False}, ())
    with pytest.raises(ValueError, match="observation is invalid"):
        restored.diff(None, {restored.addresses()[0]: None})

    root = tmp_path / "root"
    (root / "etc").mkdir(parents=True)
    (root / "etc/fstab").write_bytes(b"fstab\n")
    ctx = SimpleNamespace(root=root)
    assert edit_module._post_image(resource, ctx, resource.addresses()[0])["image"]["exists"] is True
    with pytest.raises(ValueError, match="no file path"):
        edit_module._post_image(object(), ctx, "edit:/etc/fstab")
    with pytest.raises(ValueError, match="no prior image"):
        edit_module._pre_image(Change("edit", resource.addresses()[0], "change", None, {}, resource))
    with pytest.raises(ValueError, match="not Edit"):
        edit_module._values(object())
    assert edit_module._values(make_edit(accept_duplicates=["entry/value"]))["accept_duplicates"] == ["entry/value"]
    assert edit_module._render(change, object()) == []


def test_restore_parser_checks_ops_and_duplicate_selector_types():
    base = {
        "file": "/etc/fstab",
        "lens": "Fstab",
        "state": "present",
        "ops": [],
        "create": False,
        "on_change": "none",
    }
    with pytest.raises(ValueError, match="ops must be an array"):
        edit_module._parse({**base, "ops": ()})
    with pytest.raises(ValueError, match="accept_duplicates must be an array"):
        edit_module._parse({**base, "accept_duplicates": "*"})
    assert edit_module._parse(base) == make_edit()


def test_stage_stale_commit_and_verify_refusals(monkeypatch, tmp_path):
    resource = make_edit(ops=[{"set": "entry/value", "value": "x"}])
    before = {"exists": True, "sha256": "before", "mode": 0o644, "uid": 1, "gid": 1}
    after = {"exists": True, "sha256": "after", "mode": 0o644, "uid": 1, "gid": 1, "data_b64": "YQ=="}
    change = Change("edit", resource.addresses()[0], "change", before, after, resource)
    monkeypatch.setattr(Edit, "observe", lambda self, _ctx: {self.addresses()[0]: {"image": after}})
    with pytest.raises(PlanRefused, match="changed while staging"):
        resource.stage(SimpleNamespace(root=tmp_path, stage_dir=tmp_path / "stage"), change)

    with pytest.raises(TypeError, match="requires its staged Augeas image"):
        resource.commit(SimpleNamespace(root=tmp_path), object())
    assert resource.verify(SimpleNamespace(expected={}))

    root = tmp_path / "root"
    (root / "etc").mkdir(parents=True)
    (root / "etc/fstab").write_bytes(b"current\n")
    expected = edit_module._StagedEdit(
        "/etc/fstab", True, b"expected\n", 0o644, 0, 0, None,
        {"exists": True, "sha256": "other", "mode": 0o644, "uid": 0, "gid": 0},
    )
    assert resource.verify(SimpleNamespace(root=root, expected={resource.addresses()[0]: expected}))

    (root / "etc/fstab").unlink()
    (root / "etc/fstab").symlink_to(tmp_path / "outside")
    assert resource.verify(SimpleNamespace(root=root, expected={resource.addresses()[0]: expected}))


def test_absent_commit_deletes_target_and_returns_an_absent_image(tmp_path):
    root = tmp_path / "root"
    (root / "etc").mkdir(parents=True)
    target = root / "etc/fstab"
    target.write_bytes(b"old\n")
    resource = make_edit(state="absent")
    staged = edit_module._StagedEdit("/etc/fstab", False, None, None, None, None, None, {"exists": False})

    result = resource.commit(SimpleNamespace(root=root), staged)

    assert result == {"exists": False}
    assert not target.exists()
    assert resource.commit(SimpleNamespace(root=root), staged) == {"exists": False}


def test_commit_rechecks_symlink_components_before_write(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "etc").symlink_to(outside, target_is_directory=True)
    resource = make_edit()
    staged = edit_module._StagedEdit(
        "/etc/fstab", True, b"new\n", 0o644, 0, 0, None,
        {"exists": True, "sha256": "new", "mode": 0o644, "uid": 0, "gid": 0},
    )

    with pytest.raises(PlanRefused) as failure:
        resource.commit(SimpleNamespace(root=root), staged)

    assert failure.value.problems[0].code == "edit.path.unsafe"
