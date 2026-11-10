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
    def runner(argv, *, timeout):
        assert tuple(argv)[0] == "systemd-sysusers"
        assert timeout == 30.0
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(shadow, "run_command", runner)


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


def test_sysusers_gid_validation_keeps_zero_and_excludes_reserved_boundaries():
    assert sysusers_module._valid_gid(0)
    assert sysusers_module._valid_gid(1)
    assert not sysusers_module._valid_gid(65535)
    assert not sysusers_module._valid_gid(sysusers_module._MAX_GID)


def test_accept_shadow_requires_real_pairs_and_validates_each_field():
    class PairLike:
        def __iter__(self):
            return iter(("/etc/sysusers.d/base.conf", "svc"))

        def __len__(self):
            return 2

    with pytest.raises(ValueError, match="entries must be"):
        sysusers_module._accept_value([PairLike()])
    for entry in (
        ("relative.conf", "svc"),
        ("/etc/sysusers.d/../base.conf", "svc"),
        ("/etc/sysusers.d/base.conf", ""),
    ):
        with pytest.raises(ValueError, match="exact absolute file"):
            sysusers_module._accept_value([entry])


@pytest.mark.parametrize(
    "path,encoded",
    [
        ("/etc/group", "Y Q=="),
        ("/etc/passwd", "Y Q=="),
    ],
)
def test_passwd_group_parser_requires_canonical_base64(monkeypatch, path, encoded):
    def observe(_root, observed_path):
        return {"exists": observed_path == path, "data_b64": encoded if observed_path == path else ""}

    monkeypatch.setattr(file_ops, "observe_file", observe)
    with pytest.raises(ValueError):
        sysusers_module._passwd_groups(Path("/unused"))


@pytest.mark.parametrize(
    "data",
    [b"root:x:0\n", b":x:123:\n", b":x:123\n", b"root:x:nope:\n", b"root:x:65535:\n"],
)
def test_group_database_rejects_each_malformed_row_shape(tmp_path, data):
    root = fake_root(tmp_path)
    put(root, "/etc/group", data)
    with pytest.raises(PlanRefused, match="invalid /etc/group line"):
        sysusers_module._passwd_groups(root)


def test_sysusers_group_parser_accepts_posix_quotes_and_minimal_group_rows():
    assert sysusers_module._group_lines(b'g "service account"\n') == (
        ("service account", "g service account"),
    )
    with pytest.raises(ValueError, match="invalid sysusers group definition"):
        sysusers_module._group_lines(b"g\n")


def test_sysusers_constructor_rejects_each_mutable_collection_and_pair_shape():
    empty = ("60-x.conf", "present", (), (), (), "fallback")
    for index in (2, 3, 4):
        args = list(empty)
        args[index] = []
        with pytest.raises(ValueError, match="must be tuples"):
            Sysusers(*args)
    absent = ("60-x.conf", "absent", (), (), (), "fallback")
    for index in (2, 3, 4):
        args = list(absent)
        args[index] = ((GroupSpec("svc"),) if index == 2 else (("alice", "staff"),))
        with pytest.raises(ValueError, match="absent sysusers must not"):
            Sysusers(*args)
    for pair in (("alice",), ["alice", "staff"], (1, "staff"), ("alice", 1), ("bad name", "staff"), ("alice", "bad group")):
        with pytest.raises(ValueError, match="name pairs"):
            Sysusers("60-x.conf", "present", (), (pair,), (), "fallback")


def test_observation_keeps_an_explicit_gid_before_the_group_exists(tmp_path):
    root = fake_root(tmp_path)
    resource = Sysusers("60-x.conf", "present", (GroupSpec("svc", 430),), (), (), "fallback")

    observed = resource.observe(SimpleNamespace(root=root))[resource.addresses()[0]]

    assert observed["gids"] == {"svc": 430}


def test_fragment_change_runs_sysusers_even_when_the_group_already_exists(tmp_path):
    root = fake_root(tmp_path)
    put(root, "/etc/group", b"svc:x:430:\n")
    resource = Sysusers("60-x.conf", "present", (GroupSpec("svc", 430),), (), (), "fallback")
    ctx = SimpleNamespace(root=root, planned_gids={}, planned_group_names=set())

    change = resource.diff(ctx, resource.observe(ctx))[0]

    assert change.after["file_changed"] is True
    assert change.after["run_sysusers"] is True


def test_missing_group_runs_sysusers_when_fragment_already_matches(tmp_path):
    root = fake_root(tmp_path)
    put(root, "/etc/sysusers.d/60-x.conf", b"g svc 430 -\n")
    resource = Sysusers("60-x.conf", "present", (GroupSpec("svc", 430),), (), (), "fallback")
    ctx = SimpleNamespace(root=root, planned_gids={}, planned_group_names=set())

    change = resource.diff(ctx, resource.observe(ctx))[0]

    assert change.after["file_changed"] is False
    assert change.after["run_sysusers"] is True


def test_membership_observation_rejects_invalid_groups_and_users_independently():
    resource = Sysusers("60-x.conf", "present", (), (), (), "fallback")
    for observed in ({"groups": [], "users": []}, {"groups": {}, "users": None}):
        with pytest.raises(ValueError, match="membership observation is invalid"):
            sysusers_module._membership_actions(resource, observed)


def test_missing_group_removal_keeps_a_false_exists_after_image(tmp_path):
    root = fake_root(tmp_path)
    resource = GroupRemoval("missing")
    ctx = SimpleNamespace(root=root, planned_group_names=set())

    change = resource.diff(ctx, resource.observe(ctx))[0]

    assert change.action == "noop"
    assert change.after == {"exists": False, "gid": None, "members": [], "primary_users": []}


def test_membership_only_request_serializes_an_empty_fragment():
    assert sysusers_module._render(()) == b""


def test_staged_layer_validation_skips_target_and_absent_layer_bytes(tmp_path):
    root = fake_root(tmp_path / "root")
    staged_path = tmp_path / "stage" / "60-test.conf"
    staged_path.parent.mkdir(parents=True)
    staged_path.write_bytes(b"g svc 430 -\n")
    staged = file_ops.StagedFile(str(staged_path), {"exists": True})

    sysusers_module._validate_staged(
        SimpleNamespace(root=root),
        "/etc/sysusers.d/60-test.conf",
        staged,
        {"files": {"/etc/sysusers.d/60-test.conf": "not-base64", "/usr/lib/sysusers.d/old.conf": None}},
    )

    assert (staged_path.parent / "sysusers-root/etc/sysusers.d/60-test.conf").read_bytes() == b"g svc 430 -\n"


def test_staged_layer_validation_builds_nested_files_and_rejects_noncanonical_base64(tmp_path):
    root = fake_root(tmp_path / "root")
    put(root, "/etc/group", b"root:x:0:\n")
    put(root, "/etc/passwd", b"root:x:0:0:root:/root:/bin/sh\n")
    staged_path = tmp_path / "stage" / "60-test.conf"
    staged_path.parent.mkdir(parents=True)
    staged_path.write_bytes(b"g svc 430 -\n")
    staged = file_ops.StagedFile(str(staged_path), {"exists": True})
    layers = {"files": {"/usr/lib/sysusers.d/nested/other.conf": "YQ=="}}

    sysusers_module._validate_staged(SimpleNamespace(root=root), "/etc/sysusers.d/60-test.conf", staged, layers)

    stage_root = staged_path.parent / "sysusers-root"
    assert (stage_root / "usr/lib/sysusers.d/nested/other.conf").read_bytes() == b"a"
    assert (stage_root / "etc/group").read_bytes() == b"root:x:0:\n"
    assert (stage_root / "etc/passwd").read_bytes() == b"root:x:0:0:root:/root:/bin/sh\n"
    with pytest.raises(ValueError):
        sysusers_module._validate_staged(
            SimpleNamespace(root=root),
            "/etc/sysusers.d/60-test.conf",
            staged,
            {"files": {"/usr/lib/sysusers.d/other.conf": "Y Q=="}},
        )


def test_staged_layer_validation_writes_when_a_shallow_parent_already_exists(tmp_path):
    root = fake_root(tmp_path / "root")
    staged_path = tmp_path / "stage" / "60-test.conf"
    staged_path.parent.mkdir(parents=True)
    staged_path.write_bytes(b"g svc 430 -\n")
    staged = file_ops.StagedFile(str(staged_path), {"exists": True})
    stage_root = staged_path.parent / "sysusers-root"
    (stage_root / "usr/lib/sysusers.d").mkdir(parents=True)

    sysusers_module._validate_staged(
        SimpleNamespace(root=root),
        "/etc/sysusers.d/60-test.conf",
        staged,
        {"files": {"/usr/lib/sysusers.d/other.conf": "YQ=="}},
    )

    assert (stage_root / "usr/lib/sysusers.d/other.conf").read_bytes() == b"a"


def test_group_removal_serializer_rejects_other_resource_types():
    with pytest.raises(ValueError, match="resource is not GroupRemoval"):
        sysusers_module._group_values(object())


@pytest.mark.parametrize("path", ["60-x.conf", "/etc/sysusers.d/bad name.conf"])
def test_sysusers_restore_rejects_relative_and_invalid_basenames(path):
    with pytest.raises(ValueError, match="outside /etc/sysusers.d"):
        sysusers_module._restore("sysusers:/etc/sysusers.d/60-x.conf", {"path": path}, ())
