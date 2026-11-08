"""Endpoint adapter validation and registry-hook contract tests."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from cli_extended.parser import CliFailure

from augeaswrap import Change, Endpoint, PostAction, Problem, endpoints
from augeaswrap import endpoint as endpoint_module


def fake_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    (root / "etc/profile.d").mkdir(parents=True)
    (root / "etc/environment").write_bytes(b"")
    return root


def put(root: Path, path: str, data: bytes) -> Path:
    target = root / path.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def test_observe_rejects_non_directory_and_records_other_fragments(monkeypatch, tmp_path):
    root = fake_root(tmp_path / "bad")
    put(root, "/etc/host-endpoints.d", b"file")
    monkeypatch.setattr(endpoint_module.file_ops, "observe_file", lambda *_args: {"exists": False})
    with pytest.raises(ValueError, match="is not a directory"):
        endpoint_module._observe(root, "mine")

    root = fake_root(tmp_path / "other")
    endpoints.publish("other", {"OTHER_SOCKET": "/run/other.sock"}, writer="test 1", root=root)
    observed = endpoint_module._observe(root, "mine")
    assert "/etc/host-endpoints.d/other.env" in observed["files"]
    assert observed["fragments"]["other"]["entries"] == {"OTHER_SOCKET": "/run/other.sock"}


def test_byte_and_fragment_decoders_refuse_malformed_snapshots():
    assert endpoint_module._bytes(None) == b""
    assert endpoint_module._bytes({"exists": False}) == b""
    assert endpoint_module._bytes({"exists": True, "data_b64": base64.b64encode(b"x").decode()}) == b"x"
    with pytest.raises(ValueError, match="no file bytes"):
        endpoint_module._bytes({"exists": True})
    with pytest.raises(Exception):
        endpoint_module._bytes({"exists": True, "data_b64": "!"})

    with pytest.raises(ValueError, match="snapshot is invalid"):
        endpoint_module._fragments(None)
    with pytest.raises(ValueError, match="invalid entry"):
        endpoint_module._fragments({"bad": None})
    with pytest.raises(ValueError, match="invalid values"):
        endpoint_module._fragments({"other": {"writer": 1, "entries": {}}})
    fragment = endpoints.EndpointFragment("other", "test 1", {"OTHER_SOCKET": "/run/other.sock"})
    assert endpoint_module._fragments({"other": {"writer": "test 1", "entries": dict(fragment.entries)}})["other"] == fragment


def test_gid_resolution_rejects_bad_group_files_and_requires_known_ids():
    before = {"files": {"/etc/group": {"exists": True, "data_b64": base64.b64encode(b"bad\n").decode()}}}
    with pytest.raises(Exception, match="invalid /etc/group line"):
        endpoint_module._resolve_entries(SimpleNamespace(planned_gids={}), before, (("APP_GID", "@gid:users"),))
    duplicate = {"files": {"/etc/group": {"exists": True, "data_b64": base64.b64encode(b"users:x:1000:\nusers:x:1001:\n").decode()}}}
    with pytest.raises(Exception, match="duplicate group"):
        endpoint_module._resolve_entries(SimpleNamespace(planned_gids={}), duplicate, (("APP_GID", "@gid:users"),))
    planned = endpoint_module._resolve_entries(
        SimpleNamespace(planned_gids={"svc": 430}), {"files": {}}, (("APP_GID", "@gid:svc"), ("APP_SOCKET", "/run/app.sock"))
    )
    assert planned == {"APP_GID": "430", "APP_SOCKET": "/run/app.sock"}
    with_blank_line = {"files": {"/etc/group": {"exists": True, "data_b64": base64.b64encode(b"\nusers:x:1000:\n").decode()}}}
    assert endpoint_module._resolve_entries(SimpleNamespace(planned_gids={}), with_blank_line, (("APP_GID", "@gid:users"),)) == {"APP_GID": "1000"}
    with pytest.raises(Exception, match="cannot resolve"):
        endpoint_module._resolve_entries(SimpleNamespace(planned_gids={}), {"files": {}}, (("APP_GID", "@gid:missing"),))


@pytest.mark.parametrize(
    "args, message",
    [
        (("bad name", "present", (("A", "b"),)), "name is invalid"),
        (("valid", "unknown", (("A", "b"),)), "state must be"),
        (("valid", "present", ()), "requires entries"),
        (("valid", "present", (("A", 3),)), "entries must be"),
        (("valid", "present", (("A", "1"), ("A", "2"))), "duplicate key"),
        (("valid", "absent", (("A", "1"),)), "must not have entries"),
    ],
)
def test_endpoint_constructor_refuses_invalid_values(args, message):
    with pytest.raises(ValueError, match=message):
        Endpoint(*args)


def test_fragment_map_same_files_and_endpoint_absent_without_fragment(tmp_path):
    assert endpoint_module._same_files({}, {}) is False
    assert endpoint_module._same_files({"files": {"/etc/x": {"exists": False}}}, {"/etc/x": None}) is True
    assert endpoint_module._same_files({"files": {"/etc/x": None}}, {"/etc/x": "YQ=="}) is False
    root = fake_root(tmp_path)
    resource = Endpoint("missing", "absent")
    change = resource.diff(SimpleNamespace(root=root), {resource.addresses()[0]: endpoint_module._observe(root, "missing")})[0]
    assert change.action == "noop" and change.after["desired_files"] == {}
    with pytest.raises(ValueError, match="observation is invalid"):
        resource.diff(None, {resource.addresses()[0]: None})


def test_endpoint_adapter_commit_and_verify_refusal_paths(tmp_path):
    root = fake_root(tmp_path)
    resource = Endpoint("mine", "present", (("APP_SOCKET", "/run/app.sock"),))
    with pytest.raises(TypeError, match="staged endpoint"):
        resource.commit(SimpleNamespace(root=root), object())
    assert resource.verify(SimpleNamespace(root=root, expected={}))[0].code == "endpoint.verify.expected"
    expected = endpoint_module.EndpointStage("mine", "present", {"APP_SOCKET": "/run/app.sock"}, {"/etc/host-endpoints.d/mine.env": "Zm9v"})
    failures = resource.verify(SimpleNamespace(root=root, expected={resource.addresses()[0]: expected}))
    assert failures[0].code == "endpoint.verify.failed"
    absent = endpoint_module.Endpoint("missing", "absent")
    no_files = endpoint_module.EndpointStage("missing", "absent", {}, {})
    assert absent.verify(SimpleNamespace(root=root, expected={absent.addresses()[0]: no_files})) == []

    restore = endpoint_module.EndpointRestore("mine", b"previous fragment", ())
    with pytest.raises(TypeError, match="staged fragment"):
        restore.commit(SimpleNamespace(root=root), object())
    assert restore.verify(SimpleNamespace(root=root))[0].code == "endpoint.revert.verify"
    with pytest.raises(ValueError, match="observation is invalid"):
        restore.diff(None, {restore.addresses()[0]: None})
    absent_restore = endpoint_module.EndpointRestore("mine", None, ())
    assert absent_restore.diff(None, {absent_restore.addresses()[0]: {"fragment": {"exists": False}}})[0].action == "noop"


def test_live_publish_defers_endpoint_write_to_an_argv_post_action(monkeypatch):
    resource = Endpoint("mine", "present", (("APP_SOCKET", "/run/app.sock"),))
    staged = endpoint_module.EndpointStage("mine", "present", {"APP_SOCKET": "/run/app.sock"}, {})
    monkeypatch.setattr(endpoint_module, "_postimage", lambda _root, _name: {"before": True})
    monkeypatch.setattr(endpoint_module.endpoints, "publish", lambda *_args, **_kwargs: pytest.fail("publish ran during commit"))

    assert resource.commit(SimpleNamespace(root=Path("/")), staged) == {"before": True}
    change = Change(
        "endpoint", "endpoint:mine", "change", {},
        {"state": "present", "entries": {"APP_SOCKET": "/run/app.sock"}, "writer": "service install 1"},
        resource,
    )
    action = resource.post_actions([change])[0]
    assert action.argv[1:10] == (
        "-m", "augeaswrap", "endpoints", "publish", "--owner", "mine",
        "--writer", "service install 1", "--entry",
    )
    assert action.argv[-1] == "APP_SOCKET=/run/app.sock"
    assert Endpoint("mine", "absent").post_actions([
        Change("endpoint", "endpoint:mine", "change", {}, {"state": "absent"}, resource)
    ]) == []
    assert resource.post_actions([
        Change("endpoint", "endpoint:mine", "noop", {}, {"state": "present"}, resource)
    ]) == []
    with pytest.raises(ValueError, match="missing its writer or entries"):
        resource.post_actions([
            Change("endpoint", "endpoint:mine", "change", {}, {"state": "present"}, resource)
        ])


def test_restore_fragment_refuses_endpoint_symlink_problems(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    monkeypatch.setattr(endpoint_module.endpoints, "_target_symlink_problems", lambda _root: [Problem("endpoint.path.symlink", "blocked")])
    with pytest.raises(CliFailure, match="blocked"):
        endpoint_module._restore_fragment(root, "mine", None)


def test_decode_fragment_and_snapshot_validation_paths():
    fragment = endpoints.EndpointFragment("mine", "test 1", {"APP_SOCKET": "/run/app.sock"})
    encoded = endpoints._fragment_bytes(fragment)
    assert endpoint_module._decode_fragment("mine", encoded) == fragment
    assert endpoint_module._decode_fragment("mine", b"\xff") is None
    assert endpoint_module._decode_fragment("mine", b"bad\nheader\n") is None
    assert endpoint_module._decode_fragment("mine", b"# host-endpoints v1\n# owner=mine\n# writer=test\ninvalid\n") is None
    assert endpoint_module._decode_fragment("mine", b"# host-endpoints v1\n# owner=mine\n# writer=test\nPATH=/run/x\n") is None
    assert endpoint_module._decode_fragment(
        "mine", b"# host-endpoints v1\n# owner=mine\n# writer=test\nAPP_SOCKET=/run/a.sock\nAPP_SOCKET=/run/b.sock\n"
    ) is None

    with pytest.raises(ValueError, match="observation is invalid"):
        endpoint_module._snapshot(SimpleNamespace(resource=SimpleNamespace(name="mine")), None)
    with pytest.raises(ValueError, match="has no name"):
        endpoint_module._snapshot(SimpleNamespace(resource=SimpleNamespace(name=None)), {})
    with pytest.raises(ValueError, match="no fragment image"):
        endpoint_module._snapshot(SimpleNamespace(resource=SimpleNamespace(name="mine")), {"files": {}})


def test_restore_hook_validates_every_preimage_field():
    with pytest.raises(ValueError, match="does not match its address"):
        endpoint_module._restore("endpoint:mine", {"name": "other", "exists": False}, ())
    with pytest.raises(ValueError, match="name is invalid"):
        endpoint_module._restore("endpoint:bad name", {"name": "bad name", "exists": False}, ())
    with pytest.raises(ValueError, match="exists flag"):
        endpoint_module._restore("endpoint:mine", {"name": "mine", "exists": 1}, ())
    with pytest.raises(ValueError, match="must not contain bytes"):
        endpoint_module._restore("endpoint:mine", {"name": "mine", "exists": False, "data_b64": "YQ=="}, ())
    with pytest.raises(ValueError, match="no fragment bytes"):
        endpoint_module._restore("endpoint:mine", {"name": "mine", "exists": True}, ())
    with pytest.raises(ValueError, match="digest differs"):
        endpoint_module._restore("endpoint:mine", {"name": "mine", "exists": True, "data_b64": "YQ==", "sha256": "bad"}, ())

    record = {"name": "mine", "exists": True, "data_b64": base64.b64encode(b"fragment").decode(), "sha256": hashlib.sha256(b"fragment").hexdigest()}
    assert endpoint_module._restore("endpoint:mine", record, ()).data == b"fragment"

    actions = (
        PostAction("endpoint publish mine", ("publish",), 5.0),
        PostAction("other action", ("other",), 5.0),
    )
    assert endpoint_module._restore("endpoint:mine", record, actions).actions == (actions[1],)
    absent = endpoint_module._restore("endpoint:mine", {"name": "mine", "exists": False}, actions)
    assert absent.actions == (actions[1],)


def test_endpoint_loader_serializer_and_bad_group_base64_paths():
    with pytest.raises(ValueError, match="missing endpoint field"):
        endpoint_module._parse({})
    with pytest.raises(ValueError, match="array of tables"):
        endpoint_module._parse({"name": "mine", "state": "absent", "entries": "bad"})
    with pytest.raises(ValueError, match="must be strings"):
        endpoint_module._parse({"name": "mine", "state": "present", "entries": [{"key": 3, "value": "x"}]})
    parsed = endpoint_module._parse({"name": "mine", "state": "present", "entries": [{"key": "APP_SOCKET", "value": "/run/app.sock"}]})
    assert endpoint_module._values(parsed)["entries"] == [{"key": "APP_SOCKET", "value": "/run/app.sock"}]
    with pytest.raises(ValueError, match="resource is not Endpoint"):
        endpoint_module._values(object())
    with pytest.raises(Exception):
        endpoint_module._resolve_entries(SimpleNamespace(planned_gids={}), {"files": {"/etc/group": {"exists": True, "data_b64": "!"}}}, ())
