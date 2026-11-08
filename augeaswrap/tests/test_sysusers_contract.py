"""Sysusers parser, guard, verifier and group-removal contract tests."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from augeaswrap import GroupRemoval, GroupSpec, PlanRefused, Sysusers, load_request
from augeaswrap import _resource_files as file_ops
from augeaswrap import shadow, sysusers as sysusers_module


@pytest.fixture(autouse=True)
def no_host_validator(monkeypatch):
    monkeypatch.setattr(shadow, "run_command", lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""))


def fake_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    root.mkdir(parents=True)
    return root


def put(root: Path, path: str, data: bytes) -> Path:
    target = root / path.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def test_group_spec_and_acceptance_reject_invalid_shapes():
    for args, message in (
        (("bad name",), "group name is invalid"),
        (("good", True), "gid must"),
        (("good", -1), "gid must"),
        (("good", None, ["other"]), "list of group names"),
        (("good", None, ("bad name",)), "list of group names"),
        (("good", None, (), (True,)), "valid non-reserved 32-bit GIDs"),
    ):
        with pytest.raises(ValueError, match=message):
            GroupSpec(*args)
    for value, message in (
        (None, "must be an array"),
        (["bad"], "entries must be"),
        ([("relative", "group")], "exact absolute file"),
        ([("/etc/x", "")], "exact absolute file"),
        ([("/etc/x", "key"), ("/etc/x", "key")], "duplicate accept_shadow"),
    ):
        with pytest.raises(ValueError, match=message):
            sysusers_module._accept_value(value)


def test_membership_and_group_table_parsers_refuse_invalid_shapes():
    for value, message in (
        (None, "must be an array"),
        (["bad"], "entries must be tables"),
        ([{"user": "bad name", "group": "staff"}], "names are invalid"),
        ([{"user": "alice", "group": "staff"}, {"user": "alice", "group": "staff"}], "duplicate members_add"),
    ):
        with pytest.raises(ValueError, match=message):
            sysusers_module._entries(value, "members_add")
    for value, message in (
        (None, "must be an array"),
        (["bad"], "entries must be tables"),
        ([{"name": "svc", "not_gid": "0"}], "must be an array"),
        ([{"name": "svc"}, {"name": "svc"}], "duplicate group name"),
    ):
        with pytest.raises(ValueError, match=message):
            sysusers_module._groups_value(value)
    with pytest.raises(ValueError, match="invalid sysusers group definition"):
        sysusers_module._group_lines(b"g\n")
    assert sysusers_module._group_lines(b"# comment\n\nr service 100-200\n") == ()


def test_sysusers_constructor_rejects_invalid_state_and_memberships():
    valid_group = (GroupSpec("svc", 430),)
    for args, message in (
        (("bad", "present", valid_group, (), (), "fallback"), "single .conf"),
        (("60-x.conf", "unknown", (), (), (), "fallback"), "state must"),
        (("60-x.conf", "present", [], (), (), "fallback"), "must be tuples"),
        (("60-x.conf", "absent", valid_group, (), (), "fallback"), "absent sysusers must not"),
        (("60-x.conf", "present", (object(),), (), (), "fallback"), "GroupSpec values"),
        (("60-x.conf", "present", (GroupSpec("svc"), GroupSpec("svc")), (), (), "fallback"), "duplicate group name"),
        (("60-x.conf", "present", (), (("bad name", "staff"),), (), "fallback"), "name pairs"),
        (("60-x.conf", "present", (), (("alice", "staff"), ("alice", "staff")), (), "fallback"), "duplicate user/group pair"),
        (("60-x.conf", "present", (), (("alice", "staff"),), (("alice", "staff"),), "fallback"), "both added and absent"),
        (("60-x.conf", "present", (), (), (), "unknown"), "intent must"),
    ):
        with pytest.raises(ValueError, match=message):
            Sysusers(*args)


def test_passwd_group_and_sysusers_request_parsers_cover_empty_and_bad_rows(tmp_path):
    root = fake_root(tmp_path)
    assert sysusers_module._passwd_groups(root) == ({}, set(), {})
    put(root, "/etc/group", b"\n")
    put(root, "/etc/passwd", b"\n")
    assert sysusers_module._passwd_groups(root) == ({}, set(), {})
    put(root, "/etc/group", b"invalid\n")
    with pytest.raises(PlanRefused, match="invalid /etc/group line"):
        sysusers_module._passwd_groups(root)
    put(root, "/etc/group", b"")
    put(root, "/etc/passwd", b"invalid\n")
    with pytest.raises(PlanRefused, match="invalid /etc/passwd line"):
        sysusers_module._passwd_groups(root)

    with pytest.raises(ValueError, match="missing sysusers field"):
        sysusers_module._parse({"file": "60-x.conf", "state": "present", "intent": "fallback"})
    with pytest.raises(ValueError, match="both added and absent"):
        sysusers_module._parse({
            "file": "60-x.conf", "state": "present", "intent": "fallback", "groups": [],
            "members_add": [{"user": "alice", "group": "staff"}],
            "members_absent": [{"user": "alice", "group": "staff"}],
        })
    absent = sysusers_module._parse({"file": "60-x.conf", "state": "absent", "intent": "fallback"})
    assert absent.state == "absent"
    with pytest.raises(ValueError, match="missing group field"):
        sysusers_module._parse_removal({"name": "staff"})
    assert sysusers_module._parse_removal({"name": "staff", "state": "absent"}) == GroupRemoval("staff")
    with pytest.raises(ValueError, match="resource is not Sysusers"):
        sysusers_module._values(object())
    with pytest.raises(ValueError, match="resource is not GroupRemoval"):
        sysusers_module._group_values(object())


def test_diff_supports_context_without_optional_maps_and_rejects_bad_observations(tmp_path):
    root = fake_root(tmp_path)
    resource = Sysusers("60-x.conf", "present", (GroupSpec("svc", 430),), (), (), "fallback")
    ctx = SimpleNamespace(root=root, planned_gids=None, planned_group_names=None)
    observed = resource.observe(ctx)
    change = resource.diff(ctx, observed)[0]
    assert change.after["run_sysusers"] is True
    with pytest.raises(ValueError, match="observation is invalid"):
        resource.diff(ctx, {resource.addresses()[0]: None})

    current_root = fake_root(tmp_path / "current")
    put(current_root, "/etc/group", b"svc:x:430:\n")
    put(current_root, "/etc/sysusers.d/60-x.conf", b"g svc 430 -\n")
    current = Sysusers("60-x.conf", "present", (GroupSpec("svc", 430),), (), (), "fallback")
    current_ctx = SimpleNamespace(root=current_root, planned_gids={}, planned_group_names=None)
    current_change = current.diff(current_ctx, current.observe(current_ctx))[0]
    assert current_change.action == "noop" and current_ctx.planned_gids == {"svc": 430}

    auto = Sysusers("70-auto.conf", "present", (GroupSpec("auto"),), (), (), "fallback")
    auto_ctx = SimpleNamespace(root=root, planned_gids={}, planned_group_names=set())
    auto.diff(auto_ctx, auto.observe(auto_ctx))
    assert auto_ctx.planned_gids == {}  # the allocated GID is not guessed


def test_absent_sysusers_reports_effect_only_when_a_fragment_exists(tmp_path):
    root = fake_root(tmp_path)
    resource = Sysusers("60-old.conf", "absent", (), (), (), "fallback")
    ctx = SimpleNamespace(root=root, planned_gids={}, planned_group_names=set())
    assert resource.diff(ctx, resource.observe(ctx))[0].details == ()
    put(root, "/etc/sysusers.d/60-old.conf", b"g old - -\n")
    change = resource.diff(ctx, resource.observe(ctx))[0]
    assert "created accounts and groups in place" in change.details[0]
    with pytest.raises(TypeError, match="requires a staged file"):
        resource.commit(ctx, object())


def test_gid_and_membership_helpers_reject_invalid_observations_and_unknown_groups():
    with pytest.raises(ValueError, match="resolved group map is invalid"):
        sysusers_module._check_gid_constraints((), None, {})
    with pytest.raises(ValueError, match="membership observation is invalid"):
        sysusers_module._membership_actions(Sysusers("60-x.conf", "present", (), (), (), "fallback"), {})
    with pytest.raises(PlanRefused, match="referenced group missing"):
        sysusers_module._check_gid_constraints((GroupSpec("svc", 430, distinct_from=("missing",)),), {}, {})
    add = Sysusers("60-x.conf", "present", (), (("alice", "staff"),), (), "fallback")
    remove = Sysusers("60-x.conf", "present", (), (), (("alice", "staff"),), "fallback")
    current = {"groups": {"staff": {"members": []}}, "users": ["alice"]}
    assert sysusers_module._membership_actions(add, current) == (("add", "alice", "staff"),)
    current["groups"]["staff"]["members"] = ["alice"]
    assert sysusers_module._membership_actions(add, current) == ()
    assert sysusers_module._membership_actions(remove, current) == (("remove", "alice", "staff"),)
    empty = {"groups": {"staff": {"members": []}}, "users": ["alice"]}
    assert sysusers_module._membership_actions(remove, empty) == ()
    with pytest.raises(PlanRefused, match="GID for svc must be resolved"):
        sysusers_module._check_gid_constraints((GroupSpec("svc", not_gid=(0,)),), {}, {})
    sysusers_module._check_gid_constraints((GroupSpec("svc", distinct_from=("staff",)),), {"svc": 430, "staff": 300}, {})


def test_staged_validation_refuses_bad_layer_snapshots_and_missing_file_is_noop(tmp_path):
    staged_path = tmp_path / "stage" / "60-test.conf"
    staged_path.parent.mkdir()
    staged_path.write_bytes(b"g svc 430 -\n")
    staged = file_ops.StagedFile(str(staged_path), {"exists": True})
    ctx = SimpleNamespace(root=tmp_path)
    with pytest.raises(PlanRefused, match="no file map"):
        sysusers_module._validate_staged(ctx, "/etc/sysusers.d/60-test.conf", staged, None)
    with pytest.raises(PlanRefused, match="invalid path"):
        sysusers_module._validate_staged(ctx, "/etc/sysusers.d/60-test.conf", staged, {"files": {"/etc/passwd": "YQ=="}})
    with pytest.raises(PlanRefused, match="invalid bytes"):
        sysusers_module._validate_staged(ctx, "/etc/sysusers.d/60-test.conf", staged, {"files": {"/usr/lib/sysusers.d/x.conf": 1}})
    sysusers_module._validate_staged(ctx, "/etc/sysusers.d/60-test.conf", file_ops.StagedFile(str(staged_path), {"exists": False}), {"files": {}})


def test_group_removal_validation_hooks_and_live_verifier(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="group name is invalid"):
        GroupRemoval("bad name")
    root = fake_root(tmp_path)
    removal = GroupRemoval("staff")
    before = {removal.addresses()[0]: {"exists": False, "primary_users": []}}
    with pytest.raises(ValueError, match="group observation is invalid"):
        removal.diff(None, {removal.addresses()[0]: None})
    change = removal.diff(SimpleNamespace(planned_group_names=None), before)[0]
    assert change.action == "noop" and removal.post_actions([change]) == []
    with pytest.raises(TypeError, match="staged absence image"):
        removal.commit(None, None)
    assert removal.verify(SimpleNamespace(root=root)) == []
    monkeypatch.setattr(sysusers_module, "_passwd_groups", lambda _root: ({"staff": (430, ())}, set(), {}))
    assert removal.verify(SimpleNamespace(root=Path("/")))[0].code == "group.verify.failed"
    monkeypatch.setattr(sysusers_module, "_passwd_groups", lambda _root: ({}, set(), {}))
    assert removal.verify(SimpleNamespace(root=Path("/"))) == []


def test_live_sysusers_verify_checks_group_gid_and_memberships(monkeypatch):
    monkeypatch.setattr(file_ops, "verify_file", lambda *_args: [])
    resource = Sysusers(
        "60-live.conf", "present", (GroupSpec("svc", 430),),
        (("alice", "svc"), ("alice", "missing")), (("alice", "staff"), ("alice", "empty")), "fallback",
    )
    monkeypatch.setattr(
        sysusers_module, "_passwd_groups",
        lambda _root: ({"svc": (431, ("bob",)), "staff": (300, ("alice",))}, {"alice"}, {}),
    )
    found = resource.verify(SimpleNamespace(root=Path("/"), expected={}))
    assert [item.code for item in found] == [
        "sysusers.gid.verify", "sysusers.member.verify", "sysusers.member.verify", "sysusers.member.verify",
    ]

    missing = Sysusers("60-missing.conf", "present", (), (("nobody", "missing"),), (), "fallback")
    monkeypatch.setattr(sysusers_module, "_passwd_groups", lambda _root: ({}, set(), {}))
    assert [item.code for item in missing.verify(SimpleNamespace(root=Path("/"), expected={}))] == [
        "sysusers.member.verify",
    ]

    verified = Sysusers("60-ok.conf", "present", (GroupSpec("svc"),), (("alice", "svc"),), (("bob", "staff"),), "fallback")
    monkeypatch.setattr(
        sysusers_module, "_passwd_groups",
        lambda _root: ({"svc": (430, ("alice",)), "staff": (300, ())}, {"alice"}, {}),
    )
    assert verified.verify(SimpleNamespace(root=Path("/"), expected={})) == []
