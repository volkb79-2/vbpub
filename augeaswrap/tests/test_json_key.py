"""JSON key resource contracts, staged validation, and exact revert behavior."""

from __future__ import annotations

import json
import stat
import subprocess
import tomllib
from pathlib import Path

import pytest

from augeaswrap import JsonKey, PlanRefused, Request, apply, load_request, plan, revert
from augeaswrap import json_key as json_key_module
from augeaswrap.kinds import registered_kinds


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


def test_unicode_integer_and_float_fidelity_and_strict_toml_round_trip(tmp_path):
    value = {"unicode": "λ 雪", "integer": 7, "float": 2.0, "array": [1, 1.25]}
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
    parsed = json.loads((root / "etc/docker/daemon.json").read_text(encoding="utf-8"))
    assert parsed["daemon"]["options"] == value
    assert type(parsed["daemon"]["options"]["integer"]) is int
    assert type(parsed["daemon"]["options"]["float"]) is float


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


@pytest.mark.parametrize(
    ("source", "key", "message"),
    [
        ('{"x": 1, "x": 2}', ["x"], "duplicate JSON key 'x'"),
        ('{"parent": {"x": 1, "x": 2}}', ["parent", "x"], "duplicate JSON key 'x'"),
        ('{"x": NaN}', ["x"], "invalid JSON constant NaN"),
        ("[]", ["x"], "JSON root"),
        ("null", ["x"], "JSON root"),
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
        del timeout
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
