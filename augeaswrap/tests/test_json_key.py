"""JSON key resource contracts, staged validation, and exact revert behavior."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import stat
import subprocess
import tomllib
from dataclasses import FrozenInstanceError, replace
from types import SimpleNamespace
from pathlib import Path

import pytest

from augeaswrap import JsonKey, PlanRefused, Request, apply, load_request, plan, revert
from augeaswrap import json_key as json_key_module
from augeaswrap.kinds import kind_spec, registered_kinds
from augeaswrap.json_key import _RestoredJsonKey, _StagedJsonKey, _render_json_key


def fake_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    root.mkdir()
    return root


def request(resource: JsonKey) -> Request:
    return Request("json_key test", (resource,))


def key_resource(
    *,
    state: str = "present",
    value: object = "journald",
    on_change: dict[str, str] | None = None,
    **kwargs,
) -> JsonKey:
    return JsonKey(
        "/etc/docker/daemon.json",
        ["log-driver"],
        state,
        value if state == "present" else None,
        on_change=on_change or {"unit": "docker.service", "action": "none"},
        **kwargs,
    )


def completed(argv, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(list(argv), returncode, stdout, stderr)


def restore_record(*, file="/etc/docker/daemon.json", mode=0o640, uid=0, gid=0, data=b"{}"):
    return {
        "file": file,
        "exists": True,
        "sha256": hashlib.sha256(data).hexdigest(),
        "data_b64": base64.b64encode(data).decode("ascii"),
        "mode": mode,
        "uid": uid,
        "gid": gid,
    }


def restore_resource(record):
    hooks = kind_spec("json_key").revert
    assert hooks is not None
    return hooks.restore(f"json_key:{record['file']}", record, ())


def test_present_canonicalizes_whole_file_keeps_order_and_preserves_metadata(tmp_path):
    root = fake_root(tmp_path)
    target = root / "etc/docker/daemon.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b'{ "z": 1, "nested": {"first": true} }\n')
    target.chmod(0o640)
    before_stat = target.stat()
    planned = plan(request(key_resource()), root=root)

    assert planned.has_changes
    assert '"z": 1' in planned.render()
    assert '"nested": {' in planned.render()
    assert '"log-driver": "journald"' in planned.render()
    result = apply(planned)

    assert result.txn
    assert result.actions == ()
    assert result.pending == ()
    assert target.read_bytes() == (
        b'{\n'
        b'  "z": 1,\n'
        b'  "nested": {\n'
        b'    "first": true\n'
        b'  },\n'
        b'  "log-driver": "journald"\n'
        b'}\n'
    )
    assert stat.S_IMODE(target.stat().st_mode) == 0o640
    assert (target.stat().st_uid, target.stat().st_gid) == (before_stat.st_uid, before_stat.st_gid)


def test_absent_key_is_reported_and_deletion_keeps_empty_parent(tmp_path):
    root = fake_root(tmp_path)
    target = root / "etc/docker/daemon.json"
    target.parent.mkdir(parents=True)
    target.write_text('{\n  "outer": {\n    "keep": 1\n  }\n}\n', encoding="utf-8")
    resource = JsonKey(
        "/etc/docker/daemon.json",
        ["outer", "missing"],
        "absent",
        on_change={"unit": "docker.service", "action": "none"},
    )
    absent_plan = plan(request(resource), root=root)
    assert not absent_plan.has_changes
    assert "key already absent" in absent_plan.render()

    missing_parent = JsonKey(
        "/etc/docker/daemon.json",
        ["missing", "leaf"],
        "absent",
        on_change={"unit": "docker.service", "action": "none"},
    )
    missing_parent_plan = plan(request(missing_parent), root=root)
    assert not missing_parent_plan.has_changes
    assert "key already absent" in missing_parent_plan.render()
    assert "missing" not in json.loads(target.read_text(encoding="utf-8"))

    resource = JsonKey(
        "/etc/docker/daemon.json",
        ["outer", "keep"],
        "absent",
        on_change={"unit": "docker.service", "action": "none"},
    )
    result = apply(plan(request(resource), root=root))
    assert result.txn
    assert target.read_bytes() == b'{\n  "outer": {}\n}\n'


def test_absent_key_noop_preserves_noncanonical_bytes_and_creates_no_transaction(tmp_path):
    root = fake_root(tmp_path)
    target = root / "etc/docker/daemon.json"
    target.parent.mkdir(parents=True)
    original = b'{ "keep" : 1 }\n'
    target.write_bytes(original)
    resource = JsonKey(
        "/etc/docker/daemon.json",
        ["missing"],
        "absent",
        on_change={"unit": "docker.service", "action": "none"},
    )

    planned = plan(request(resource), root=root)
    assert not planned.has_changes
    assert "key already absent" in planned.render()

    result = apply(planned)
    assert result.txn is None
    assert target.read_bytes() == original


def test_absent_key_in_missing_file_keeps_the_after_image_absent(tmp_path):
    root = fake_root(tmp_path)
    resource = JsonKey(
        "/etc/docker/daemon.json",
        ["log-driver"],
        "absent",
        on_change={"unit": "docker.service", "action": "none"},
    )

    planned = plan(request(resource), root=root)

    assert not planned.has_changes
    assert planned.changes[0].after == {"exists": False}
    assert "key already absent" in planned.changes[0].details
    assert not (root / "etc/docker/daemon.json").exists()

    data, image, details = json_key_module._desired(root, resource, observed={"exists": False})
    assert data is None
    assert image == {"exists": False, "fingerprint": "absent"}
    assert details == ("key already absent",)
    assert not (root / "etc/docker/daemon.json").exists()


def test_json_canonical_output_preserves_unicode_characters():
    encoded = json_key_module._canonical({"description": "雪"})

    assert '"description": "雪"' in encoded.decode("utf-8")


def test_json_key_records_are_immutable():
    resource = key_resource()
    with pytest.raises(FrozenInstanceError):
        resource.file = "/etc/other.json"


def test_idempotent_plan_creates_no_second_transaction(tmp_path):
    root = fake_root(tmp_path)
    resource = key_resource()
    first = apply(plan(request(resource), root=root))
    target = root / "etc/docker/daemon.json"
    canonical = target.read_bytes()

    second_plan = plan(request(resource), root=root)
    second = apply(second_plan)

    assert first.txn
    assert not second_plan.has_changes
    assert second.txn is None
    assert target.read_bytes() == canonical
    assert _render_json_key(second_plan.changes[0], second_plan) == []


def test_revert_restores_exact_changed_bytes_and_mode(tmp_path):
    root = fake_root(tmp_path)
    target = root / "etc/docker/daemon.json"
    target.parent.mkdir(parents=True)
    original = b'{ "existing": 1 }\n'
    target.write_bytes(original)
    target.chmod(0o600)
    result = apply(plan(request(key_resource()), root=root))

    restored = revert(result.txn, root=root)

    assert restored.txn
    assert target.read_bytes() == original
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_revert_of_new_file_removes_it(tmp_path):
    root = fake_root(tmp_path)
    target = root / "etc/docker/daemon.json"
    result = apply(plan(request(key_resource()), root=root))
    assert target.exists()

    restored = revert(result.txn, root=root)

    assert restored.txn
    assert not target.exists()


def test_revert_refuses_a_malformed_json_key_preimage(tmp_path):
    root = fake_root(tmp_path)
    target = root / "etc/docker/daemon.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b'{"existing": true}\n')
    result = apply(plan(request(key_resource()), root=root))
    pre_dir = root / "var/lib/augeaswrap/txn" / result.txn / "pre"
    metadata_path = next(pre_dir.glob("*.json"))
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["mode"] = "unsafe"
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")

    with pytest.raises(PlanRefused, match="invalid metadata in pre-image"):
        revert(result.txn, root=root)
    assert target.read_bytes() != b'{"existing": true}\n'


def test_revert_refuses_preimage_bytes_with_a_mismatched_digest(tmp_path):
    root = fake_root(tmp_path)
    target = root / "etc/docker/daemon.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b'{"existing": true}\n')
    result = apply(plan(request(key_resource()), root=root))
    pre_dir = root / "var/lib/augeaswrap/txn" / result.txn / "pre"
    data_path = next(pre_dir.glob("*.data"))
    data_path.write_bytes(b"different bytes")

    with pytest.raises(PlanRefused, match="digest differs"):
        revert(result.txn, root=root)
    assert target.read_bytes() != b'{"existing": true}\n'


def test_json_key_revert_hooks_validate_their_inputs(tmp_path):
    root = fake_root(tmp_path)
    target = root / "etc/docker/daemon.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b'{"existing": true}\n')
    resource = key_resource()
    planned = plan(request(resource), root=root)
    change = planned.changes[0]
    hooks = kind_spec("json_key").revert
    assert hooks is not None

    with pytest.raises(ValueError, match="observation must be a mapping"):
        hooks.snapshot(change, None)
    with pytest.raises(ValueError, match="pre-image address does not match"):
        hooks.restore("file:/etc/docker/daemon.json", {}, ())
    with pytest.raises(ValueError, match="pre-image file does not match"):
        hooks.restore(change.address, {"file": "/etc/other.json", "exists": False}, ())
    with pytest.raises(ValueError, match="exists flag must be a boolean"):
        hooks.restore(change.address, {"file": resource.file, "exists": "yes"}, ())

    record = dict(hooks.snapshot(change, planned.observed[change.address]))
    record["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="bytes do not match their digest"):
        hooks.restore(change.address, record, ())

    restored = _RestoredJsonKey(resource.file, {"exists": False, "fingerprint": "absent"})
    with pytest.raises(ValueError, match="restore observation must be a mapping"):
        restored.diff(None, {change.address: object()})
    with pytest.raises(TypeError, match="restore commit requires its staged image"):
        restored.commit(SimpleNamespace(root=root), object())
    with pytest.raises(ValueError, match="present json_key restore image is incomplete"):
        restored.commit(
            SimpleNamespace(root=root),
            _StagedJsonKey("", None, None, None, None, {}, exists=True),
        )


@pytest.mark.parametrize("missing", ["data", "mode", "uid", "gid"])
def test_json_key_commit_rejects_each_incomplete_staged_field(tmp_path, missing):
    root = fake_root(tmp_path)
    values = {"data": b"{}", "mode": 0o600, "uid": 0, "gid": 0}
    values[missing] = None
    staged = _StagedJsonKey("", values["data"], values["mode"], values["uid"], values["gid"], {})

    with pytest.raises(ValueError, match="present json_key image is incomplete"):
        key_resource().commit(SimpleNamespace(root=root), staged)

    assert not (root / "etc/docker/daemon.json").exists()


@pytest.mark.parametrize("missing", ["data", "mode", "uid", "gid"])
def test_json_key_restore_commit_rejects_each_incomplete_staged_field(tmp_path, missing):
    root = fake_root(tmp_path)
    values = {"data": b"{}", "mode": 0o600, "uid": 0, "gid": 0}
    values[missing] = None
    staged = _StagedJsonKey("", values["data"], values["mode"], values["uid"], values["gid"], {})
    restored = _RestoredJsonKey("/etc/docker/daemon.json", {"exists": True})

    with pytest.raises(ValueError, match="present json_key restore image is incomplete"):
        restored.commit(SimpleNamespace(root=root), staged)

    assert not (root / "etc/docker/daemon.json").exists()


def test_json_restore_accepts_zero_metadata_and_the_maximum_mode():
    zero = restore_resource(restore_record(mode=0, uid=0, gid=0))
    maximum = restore_resource(restore_record(mode=0o7777, uid=0, gid=0))

    assert zero.image["mode"] == 0 and zero.image["uid"] == zero.image["gid"] == 0
    assert maximum.image["mode"] == 0o7777


@pytest.mark.parametrize(
    "metadata",
    [
        {"mode": True, "uid": 0, "gid": 0},
        {"mode": 0o10000, "uid": 0, "gid": 0},
        {"mode": 0o640, "uid": True, "gid": 0},
        {"mode": 0o640, "uid": 0, "gid": True},
    ],
)
def test_json_restore_rejects_each_invalid_metadata_field(metadata):
    record = restore_record(**metadata)

    with pytest.raises(ValueError, match="invalid metadata in pre-image"):
        restore_resource(record)


def test_unicode_integer_and_float_fidelity_and_strict_toml_round_trip(tmp_path):
    value = {
        "unicode": "λ 雪",
        "integer_max": 2**63 - 1,
        "integer_min": -(2**63),
        "float": 2.0,
        "array": [1, 1.25],
    }
    resource = JsonKey(
        "/etc/docker/daemon.json",
        ["daemon", "options"],
        "present",
        value,
        on_change={"unit": "docker.service", "action": "none"},
    )
    original = request(resource)
    loaded = load_request(tomllib.loads(original.to_toml()))
    assert loaded == original

    root = fake_root(tmp_path)
    apply(plan(original, root=root))
    target = root / "etc/docker/daemon.json"
    output = target.read_bytes()
    assert '"unicode": "λ 雪"'.encode("utf-8") in output
    parsed = json.loads(output)
    assert parsed["daemon"]["options"] == value
    assert type(parsed["daemon"]["options"]["integer_max"]) is int
    assert type(parsed["daemon"]["options"]["integer_min"]) is int
    assert type(parsed["daemon"]["options"]["float"]) is float


def test_existing_document_integer_outside_request_domain_passes_through(tmp_path):
    root = fake_root(tmp_path)
    target = root / "etc/docker/daemon.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b'{"existing":9223372036854775808}\n')

    apply(plan(request(key_resource(value="journald")), root=root))

    parsed = json.loads(target.read_bytes())
    assert parsed["existing"] == 2**63
    assert type(parsed["existing"]) is int
    assert parsed["log-driver"] == "journald"


def test_json_key_is_registered_and_request_loader_rejects_unknown_fields():
    assert "json_key" in registered_kinds()
    valid = {
        "file": "/etc/docker/daemon.json",
        "key": ["x"],
        "state": "present",
        "value": 1,
        "on_change": {"unit": "docker.service", "action": "none"},
    }
    with pytest.raises(ValueError, match="unknown json_key key"):
        load_request({"augeaswrap": 1, "writer": "strict", "json_key": [{**valid, "typo": True}]})
    with pytest.raises(ValueError, match="missing json_key field.*value"):
        load_request(
            {
                "augeaswrap": 1,
                "writer": "strict",
                "json_key": [{key: value for key, value in valid.items() if key != "value"}],
            }
        )
    with pytest.raises(ValueError, match="on_change must be a table"):
        load_request(
            {
                "augeaswrap": 1,
                "writer": "strict",
                "json_key": [{**valid, "on_change": ["docker.service", "none"]}],
            }
        )
    assert 'validate = "dockerd"' in Request("round-trip", (key_resource(validate="dockerd"),)).to_toml()
    with pytest.raises(ValueError, match="has no TOML form"):
        kind_spec("json_key").to_toml(object())
    absent = load_request(
        {
            "augeaswrap": 1,
            "writer": "strict",
            "json_key": [
                {
                    "file": "/etc/docker/daemon.json",
                    "key": ["x"],
                    "state": "absent",
                    "on_change": {"unit": "docker.service", "action": "none"},
                }
            ],
        }
    )
    assert absent.resources[0].state == "absent"


def test_json_type_reports_every_non_object_shape():
    assert json_key_module._json_type(True) == "boolean"
    assert json_key_module._json_type("text") == "string"
    assert json_key_module._json_type({"key": 1}) == "object"


@pytest.mark.parametrize(
    ("source", "key", "message"),
    [
        ('{"x": 1, "x": 2}', ["x"], "duplicate JSON key 'x'"),
        ('{"parent": {"x": 1, "x": 2}}', ["parent", "x"], "duplicate JSON key 'x'"),
        ('{"x": NaN}', ["x"], "invalid JSON constant NaN"),
        ("[]", ["x"], "JSON root"),
        ("null", ["x"], "JSON root"),
        ("true", ["x"], "JSON root"),
        ('"string root"', ["x"], "JSON root"),
        ('{"x": 1}', ["x", "y"], "non-object intermediate"),
    ],
)
def test_invalid_documents_and_parent_shapes_are_refused(tmp_path, source, key, message):
    root = fake_root(tmp_path)
    target = root / "etc/docker/daemon.json"
    target.parent.mkdir(parents=True)
    target.write_text(source, encoding="utf-8")
    resource = JsonKey(
        "/etc/docker/daemon.json",
        key,
        "present",
        "value",
        on_change={"unit": "docker.service", "action": "none"},
    )

    with pytest.raises(PlanRefused, match=message):
        plan(request(resource), root=root)


def test_invalid_json_and_path_shape_values_are_refused(tmp_path):
    root = fake_root(tmp_path)
    target = root / "etc/docker/daemon.json"
    target.parent.mkdir(parents=True)
    target.write_text("not JSON", encoding="utf-8")
    with pytest.raises(PlanRefused, match="cannot parse JSON object"):
        plan(request(key_resource()), root=root)

    for kwargs, message in [
        ({"value": float("nan")}, "finite JSON number"),
        ({"value": [None]}, "cannot be null"),
        ({"value": None}, "required when state is present"),
        ({"value": {1: "bad"}}, "keys must be strings"),
        ({"value": 1 << 64}, "signed 64-bit range"),
        ({"value": object()}, "TOML-compatible JSON value"),
        ({"key": []}, "non-empty list of string segments"),
        ({"key": [1]}, "non-empty list of string segments"),
    ]:
        values = {"value": "valid", "key": ["x"]}
        values.update(kwargs)
        with pytest.raises(ValueError, match=message):
            JsonKey(
                "/etc/docker/daemon.json",
                values["key"],
                "present",
                values["value"],
                on_change={"unit": "docker.service", "action": "none"},
            )


@pytest.mark.parametrize(
    ("state", "value", "message"),
    [
        ("unknown", 1, "state must be 'present' or 'absent'"),
        ("present", None, "value is required when state is present"),
        ("absent", 1, "value cannot be used when state is absent"),
    ],
)
def test_state_and_value_presence_are_closed(state, value, message):
    with pytest.raises(ValueError, match=message):
        JsonKey(
            "/etc/docker/daemon.json",
            ["x"],
            state,
            value,
            on_change={"unit": "docker.service", "action": "none"},
        )


def test_planning_wraps_mutated_nested_value_errors_as_refusals(tmp_path):
    root = fake_root(tmp_path)
    resource = key_resource(value={"nested": {"valid": True}})
    resource.value["nested"]["valid"] = None

    with pytest.raises(PlanRefused) as refused:
        plan(request(resource), root=root)

    assert refused.value.problems[0].code == "json_key.value.invalid"
    assert "cannot be null" in refused.value.problems[0].message


def test_diff_and_stage_refusal_guards(tmp_path):
    root = fake_root(tmp_path)
    resource = key_resource()
    address = resource.addresses()[0]
    with pytest.raises(ValueError, match="observation must be a mapping"):
        resource.diff(SimpleNamespace(root=root), {address: object()})

    absent = JsonKey(
        "/etc/docker/daemon.json",
        ["log-driver"],
        "absent",
        on_change={"unit": "docker.service", "action": "none"},
    )
    absent_plan = plan(request(absent), root=root)
    with pytest.raises(ValueError, match="cannot stage an absent json_key file"):
        absent.stage(
            SimpleNamespace(root=root, stage_dir=tmp_path / "stage"),
            absent_plan.changes[0],
        )

    target = root / "etc/docker/daemon.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"log-driver":"old"}\n', encoding="utf-8")
    changed = plan(request(resource), root=root)
    change = changed.changes[0]
    wrong_after = replace(change, after={**change.after, "sha256": "0" * 64})
    with pytest.raises(ValueError, match="source changed while staging"):
        resource.stage(
            SimpleNamespace(root=root, stage_dir=tmp_path / "stage"),
            wrong_after,
        )


def test_external_stage_removes_partial_file_when_staging_fails(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    resource = key_resource()
    planned = plan(request(resource), root=root)
    stage_dir = tmp_path / "outside-stage"
    original_fchmod = os.fchmod

    def fail_staged_descriptor(descriptor, mode):
        staged_path = Path(os.readlink(f"/proc/self/fd/{descriptor}"))
        if staged_path.name == "daemon.json" and stage_dir in staged_path.parents:
            raise OSError("injected stage write failure")
        return original_fchmod(descriptor, mode)

    monkeypatch.setattr(json_key_module.os, "fchmod", fail_staged_descriptor)
    with pytest.raises(OSError, match="injected stage write failure"):
        resource.stage(
            SimpleNamespace(root=root, stage_dir=stage_dir),
            planned.changes[0],
        )
    assert not list(stage_dir.rglob("daemon.json"))


def test_external_stage_cleans_partial_file_when_stream_write_fails(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    resource = key_resource()
    planned = plan(request(resource), root=root)
    stage_dir = tmp_path / "outside-stage"

    class BrokenStream:
        def __init__(self, descriptor):
            self.descriptor = descriptor

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            os.close(self.descriptor)

        def write(self, _data):
            raise OSError("injected stream write failure")

        def flush(self):
            return None

    real_fdopen = os.fdopen

    def broken_fdopen(descriptor, mode, *args, **kwargs):
        if mode != "wb":
            return real_fdopen(descriptor, mode, *args, **kwargs)
        try:
            opened = os.path.realpath(os.readlink(f"/proc/self/fd/{descriptor}"))
        except OSError:
            return real_fdopen(descriptor, mode, *args, **kwargs)
        if not opened.endswith("/daemon.json"):
            return real_fdopen(descriptor, mode, *args, **kwargs)
        return BrokenStream(descriptor)

    monkeypatch.setattr(json_key_module.os, "fdopen", broken_fdopen)
    with pytest.raises(OSError, match="injected stream write failure"):
        resource.stage(
            SimpleNamespace(root=root, stage_dir=stage_dir),
            planned.changes[0],
        )
    assert not list(stage_dir.rglob("daemon.json"))


def test_external_stage_closes_descriptor_zero_after_fchmod_failure(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    resource = key_resource()
    planned = plan(request(resource), root=root)
    closed = []

    def fail_fchmod(_descriptor, _mode):
        raise OSError("injected fchmod failure")

    real_open = os.open

    def open_staged(path, flags, mode=0o777, *args, **kwargs):
        if Path(path).name != "daemon.json":
            return real_open(path, flags, mode, *args, **kwargs)
        assert flags == os.O_WRONLY | os.O_CREAT | os.O_EXCL
        assert mode == 0o644
        return 0

    monkeypatch.setattr(json_key_module.os, "open", open_staged)
    real_close = os.close
    monkeypatch.setattr(
        json_key_module.os,
        "close",
        lambda fd: closed.append(fd) if fd == 0 else real_close(fd),
    )
    monkeypatch.setattr(json_key_module.os, "fchmod", fail_fchmod)

    with pytest.raises(OSError, match="injected fchmod failure"):
        resource.stage(SimpleNamespace(root=root, stage_dir=tmp_path / "outside-stage"), planned.changes[0])

    assert closed == [0]


def test_external_stage_cleanup_tolerates_partial_file_already_removed(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    resource = key_resource()
    planned = plan(request(resource), root=root)
    stage_dir = tmp_path / "outside-stage"

    def remove_then_fail(_descriptor, _mode):
        staged_path = next(stage_dir.rglob("daemon.json"))
        staged_path.unlink()
        raise OSError("injected fchmod failure")

    monkeypatch.setattr(json_key_module.os, "fchmod", remove_then_fail)

    with pytest.raises(OSError, match="injected fchmod failure"):
        resource.stage(SimpleNamespace(root=root, stage_dir=stage_dir), planned.changes[0])

    assert not list(stage_dir.rglob("daemon.json"))


def test_stage_refuses_symlinked_root_parent_without_creating_outside_directory(tmp_path):
    root = fake_root(tmp_path)
    resource = key_resource()
    planned = plan(request(resource), root=root)
    outside = tmp_path / "outside-stage-target"
    outside.mkdir()
    stage_parent = root / "var/lib/augeaswrap/stage"
    stage_parent.parent.mkdir(parents=True)
    stage_parent.symlink_to(outside, target_is_directory=True)
    stage_dir = stage_parent
    escaped_directory = outside / hashlib.sha256(planned.changes[0].address.encode("utf-8")).hexdigest()

    with pytest.raises(PlanRefused, match="symlink"):
        resource.stage(SimpleNamespace(root=root, stage_dir=stage_dir), planned.changes[0])

    assert not escaped_directory.exists()


def test_restore_stage_creates_missing_stage_parent_directories(tmp_path):
    root = fake_root(tmp_path)
    record = restore_record()
    restored = restore_resource(record)
    stage_dir = tmp_path / "missing" / "nested" / "stage"

    staged = restored.stage(SimpleNamespace(root=root, stage_dir=stage_dir), None)

    assert Path(staged.path).read_bytes() == b"{}"


def test_restore_stage_can_reuse_its_existing_address_directory(tmp_path):
    root = fake_root(tmp_path)
    restored = restore_resource(restore_record())
    context = SimpleNamespace(root=root, stage_dir=tmp_path / "stage")

    first = restored.stage(context, None)
    second = restored.stage(context, None)

    assert Path(first.path) == Path(second.path)
    assert Path(second.path).read_bytes() == b"{}"


@pytest.mark.parametrize(
    "metadata",
    [
        {"mode": None, "uid": 0, "gid": 0},
        {"mode": 0o640, "uid": None, "gid": 0},
        {"mode": 0o640, "uid": 0, "gid": None},
    ],
)
def test_json_restore_refuses_incomplete_metadata_even_if_other_values_are_present(tmp_path, metadata):
    root = fake_root(tmp_path)
    record = restore_record(**metadata)

    with pytest.raises(ValueError, match="invalid metadata in pre-image"):
        restore_resource(record)


def test_commit_post_action_and_verify_guards(tmp_path):
    root = fake_root(tmp_path)
    resource = key_resource()
    ctx = SimpleNamespace(root=root, expected={})
    with pytest.raises(TypeError, match="commit requires its staged image"):
        resource.commit(ctx, object())
    incomplete = _StagedJsonKey("", None, None, None, None, {})
    with pytest.raises(ValueError, match="present json_key image is incomplete"):
        resource.commit(ctx, incomplete)

    assert resource.post_actions([]) == []
    assert resource.verify(ctx)[0].code == "json_key.verify.expected"

    apply(plan(request(resource), root=root))
    wrong_expected = _StagedJsonKey("", b"{}\n", 0o644, os.geteuid(), os.getegid(), {"exists": False}, exists=True)
    failed = resource.verify(SimpleNamespace(root=root, expected={resource.addresses()[0]: wrong_expected}))
    assert failed[0].code == "json_key.verify.failed"

    target = root / "etc/docker/daemon.json"
    restored = _RestoredJsonKey(resource.file, {"exists": False, "fingerprint": "absent"})
    assert restored.verify(SimpleNamespace(root=root))[0].code == "revert.verify.failed"


def test_allowlist_and_symlink_path_guards_refuse(tmp_path):
    with pytest.raises(ValueError, match="allowed configuration directory"):
        JsonKey("/var/lib/daemon.json", ["x"], "present", 1, on_change={"unit": "docker.service", "action": "none"})

    root = fake_root(tmp_path)
    real = tmp_path / "real-etc"
    real.mkdir()
    (root / "etc").symlink_to(real, target_is_directory=True)
    with pytest.raises(PlanRefused, match="refusing symlinked target"):
        plan(request(key_resource()), root=root)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"validate": "json"}, "validate must be 'dockerd'"),
        ({"validate": ["dockerd"]}, "validate must be 'dockerd'"),
        ({"on_change": None}, "on_change is required"),
        ({"on_change": {"unit": "docker.service", "action": "none", "extra": True}}, "exactly unit and action"),
        ({"on_change": {"unit": "docker.service", "action": "reload"}}, "action must be"),
        ({"on_change": {"unit": "bad/name.service", "action": "restart"}}, "path separator"),
    ],
)
def test_validator_and_on_change_vocabulary_are_closed(kwargs, message):
    values = {"validate": None, "on_change": {"unit": "docker.service", "action": "none"}}
    values.update(kwargs)
    with pytest.raises(ValueError, match=message):
        JsonKey("/etc/docker/daemon.json", ["x"], "present", 1, **values)


def test_dockerd_validator_receives_only_the_canonical_staged_copy(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    calls = []

    def runner(argv, *, timeout):
        calls.append((tuple(argv), timeout, Path(argv[-1]).read_bytes()))
        return completed(argv)

    monkeypatch.setattr(json_key_module.shadow, "run_command", runner)
    resource = key_resource(validate="dockerd", value={"debug": False})
    plan(request(resource), root=root)

    assert len(calls) == 1
    argv, timeout, staged_bytes = calls[0]
    assert argv[:3] == ("dockerd", "--validate", "--config-file")
    assert Path(argv[3]).name == "daemon.json"
    assert Path(argv[3]) != root / "etc/docker/daemon.json"
    assert timeout == 30.0
    assert staged_bytes == b'{\n  "log-driver": {\n    "debug": false\n  }\n}\n'


@pytest.mark.parametrize("failure", ["exit", "missing"])
def test_dockerd_validator_refuses_failure_and_missing_binary(tmp_path, monkeypatch, failure):
    root = fake_root(tmp_path)

    def runner(argv, *, timeout):
        assert tuple(argv)[0] == "dockerd"
        assert timeout == 30.0
        if failure == "missing":
            raise FileNotFoundError("dockerd")
        return completed(argv, 1, stderr="invalid config")

    monkeypatch.setattr(json_key_module.shadow, "run_command", runner)
    with pytest.raises(PlanRefused) as refused:
        plan(request(key_resource(validate="dockerd")), root=root)
    assert refused.value.problems[0].code == "json_key.validate.failed"
    assert not (root / "etc/docker/daemon.json").exists()


def test_pending_on_change_records_a_restart_without_running_it(tmp_path):
    root = fake_root(tmp_path)
    resource = key_resource(on_change={"unit": "docker.service", "action": "pending"})
    result = apply(plan(request(resource), root=root))

    assert result.txn
    assert result.pending == ("json_key restart docker.service",)
    from augeaswrap.engine import journal

    recorded = journal(result.txn, root=root)["actions"][0]
    assert recorded["argv"] == ["systemctl", "restart", "--", "docker.service"]
    assert recorded["pending"] is True
    assert recorded["status"] == "pending"


def test_missing_file_absent_request_is_noop_with_report(tmp_path):
    root = fake_root(tmp_path)
    resource = JsonKey(
        "/etc/docker/daemon.json",
        ["log-driver"],
        "absent",
        on_change={"unit": "docker.service", "action": "none"},
    )
    planned = plan(request(resource), root=root)
    assert not planned.has_changes
    assert "key already absent" in planned.render()
