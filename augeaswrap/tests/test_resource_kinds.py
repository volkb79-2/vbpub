"""Fake-root contract tests for sysusers, tmpfiles and endpoint resources."""

from __future__ import annotations

import base64
import hashlib
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from augeaswrap import (
    ApplyFailed,
    Endpoint,
    GroupRemoval,
    GroupSpec,
    PlanRefused,
    Problem,
    Request,
    StalePlan,
    Sysusers,
    Tmpfiles,
    Unit,
    apply,
    endpoints,
    journal,
    load_request,
    plan,
    revert,
)
from augeaswrap import _resource_files as file_ops
from augeaswrap import endpoint as endpoint_module
from augeaswrap import (
    shadow,
    sysctl as sysctl_module,
    sysusers as sysusers_module,
    tmpfiles as tmpfiles_module,
    unit as unit_module,
)


def fake_root(tmp_path: Path, *, environment: bytes = b"") -> Path:
    root = tmp_path / "root"
    (root / "etc/profile.d").mkdir(parents=True)
    (root / "etc/environment").write_bytes(environment)
    return root


def put(root: Path, path: str, data: bytes) -> Path:
    target = root / path.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def assert_runner_timeout(timeout):
    assert timeout == 30.0


def canned_runner(monkeypatch, *, returncode: int = 0, stdout: str = "", stderr: str = ""):
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def run(argv, *, timeout):
        assert_runner_timeout(timeout)
        kwargs = {"timeout": timeout}
        calls.append((tuple(argv), kwargs))
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(shadow, "run_command", run)
    return calls


@pytest.fixture(autouse=True)
def no_host_systemd_commands(monkeypatch):
    monkeypatch.setattr(
        shadow,
        "run_command",
        lambda argv, *, timeout: (
            SimpleNamespace(returncode=0, stdout="", stderr="")
            if tuple(argv) and timeout == 30.0
            else pytest.fail("unexpected runner arguments")
        ),
    )


def req(*resources) -> Request:
    return Request("test resource kinds 1.0", tuple(resources))


def test_sysusers_present_dry_run_membership_and_revert_journals_irreversible_effects(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    put(root, "/etc/passwd", b"alice:x:1000:1000::/home/alice:/bin/sh\n")
    put(root, "/etc/group", b"users:x:1000:alice\n")
    calls = canned_runner(monkeypatch)
    resource = Sysusers(
        "60-example.conf",
        "present",
        (GroupSpec("svc", 430), GroupSpec("extra", 431)),
        (("alice", "svc"),),
        (("alice", "users"),),
        "authoritative",
    )
    planned = plan(req(resource), root=root)
    assert calls and all(call[0][:3] == ("systemd-sysusers", "--root", call[0][2]) for call in calls)
    assert b"g extra 431 -\ng svc 430 -\n" == base64.b64decode(planned.changes[0].after["image"]["data_b64"])
    assert [item.argv for item in resource.post_actions([planned.changes[0]])] == [
        ("systemd-sysusers", "/etc/sysusers.d/60-example.conf"),
        ("gpasswd", "-a", "alice", "--", "svc"),
        ("gpasswd", "-d", "alice", "--", "users"),
    ]
    result = apply(planned)
    record = journal(result.txn, root=root)
    assert record["irreversible_effects"] == [
        {"address": "sysusers:/etc/sysusers.d/60-example.conf", "effect": "gpasswd add alice svc"},
        {"address": "sysusers:/etc/sysusers.d/60-example.conf", "effect": "gpasswd remove alice users"},
        {
            "address": "sysusers:/etc/sysusers.d/60-example.conf",
            "effect": "systemd-sysusers may create groups; revert restores only the fragment",
        },
    ]
    assert (root / "etc/sysusers.d/60-example.conf").exists()
    # The root is fake, so no systemd or gpasswd command ran.
    assert record["actions"][0]["status"] == "skipped(root)"
    reverted = revert(result.txn, root=root)
    assert reverted.txn
    assert not (root / "etc/sysusers.d/60-example.conf").exists()
    restored = journal(reverted.txn, root=root)
    assert restored["actions"] == []  # account membership and absent-file sysusers actions are not replayed


def test_sysusers_absent_deletes_file_and_revert_restores_exact_bytes(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    target = put(root, "/etc/sysusers.d/50-old.conf", b"# retained bytes\ng old - -\n")
    canned_runner(monkeypatch)
    result = apply(plan(req(Sysusers("50-old.conf", "absent", (), (), (), "fallback")), root=root))
    assert not target.exists()
    assert "created accounts and groups in place" in journal(result.txn, root=root)["changes"][0]["details"][0]
    revert(result.txn, root=root)
    assert target.read_bytes() == b"# retained bytes\ng old - -\n"


def test_sysusers_shadow_intent_acceptance_equal_values_and_first_definition(tmp_path):
    root = fake_root(tmp_path)
    put(root, "/run/sysusers.d/20-vendor.conf", b"g svc 430 -\n")
    resource = Sysusers("60-site.conf", "present", (GroupSpec("svc", 431),), (), (), "authoritative")
    with pytest.raises(PlanRefused) as failure:
        plan(req(resource), root=root)
    assert failure.value.problems[0].code == "shadow.earlier"

    accepted = Sysusers(
        "60-site.conf", "present", (GroupSpec("svc", 431),), (), (), "authoritative",
        (("/run/sysusers.d/20-vendor.conf", "svc"),),
    )
    assert any("note shadow.earlier" in item for item in plan(req(accepted), root=root).changes[0].details)

    equal_root = fake_root(tmp_path / "equal")
    put(equal_root, "/usr/lib/sysusers.d/10-vendor.conf", b"g svc 430 -\n")
    equal = Sysusers("60-site.conf", "present", (GroupSpec("svc", 430),), (), (), "fallback")
    assert any("equal value" in item for item in plan(req(equal), root=equal_root).changes[0].details)

    masked_root = fake_root(tmp_path / "masked")
    put(masked_root, "/etc/sysusers.d/60-site.conf", b"g svc 431 -\n")
    put(masked_root, "/usr/lib/sysusers.d/60-site.conf", b"g svc 430 -\n")
    fallback = Sysusers("60-site.conf", "present", (GroupSpec("svc", 431),), (), (), "fallback")
    with pytest.raises(PlanRefused) as masked:
        plan(req(fallback), root=masked_root)
    assert masked.value.problems[0].code == "shadow.mask"


def test_sysusers_gid_constraints_mismatch_and_unresolved_references(tmp_path):
    root = fake_root(tmp_path)
    put(root, "/etc/group", b"base:x:100:alice\nother:x:200:\n")
    resource = Sysusers(
        "60-groups.conf", "present",
        (GroupSpec("svc", 300, not_gid_of=("base",), not_gid=(101,), distinct_from=("other",)),),
        (), (), "authoritative",
    )
    assert plan(req(resource), root=root).has_changes
    for spec, code in (
        (GroupSpec("svc", 100, not_gid_of=("base",)), "sysusers.gid.constraint"),
        (GroupSpec("svc", 200, distinct_from=("other",)), "sysusers.gid.constraint"),
        (GroupSpec("svc", 101, not_gid=(101,)), "sysusers.gid.constraint"),
        (GroupSpec("svc", 300, distinct_from=("missing",)), "sysusers.gid.unresolved"),
    ):
        with pytest.raises(PlanRefused) as failure:
            plan(req(Sysusers("60-groups.conf", "present", (spec,), (), (), "authoritative")), root=root)
        assert failure.value.problems[0].code == code

    put(root, "/etc/group", b"svc:x:350:\nbase:x:100:alice\nother:x:200:\n")
    with pytest.raises(PlanRefused, match="has GID 350"):
        plan(req(Sysusers("60-groups.conf", "present", (GroupSpec("svc", 300),), (), (), "authoritative")), root=root)
    put(root, "/etc/group", b"dup:x:100:\ndup:x:101:\n")
    with pytest.raises(PlanRefused, match="duplicate group"):
        plan(req(Sysusers("60-groups.conf", "present", (GroupSpec("svc", 300),), (), (), "authoritative")), root=root)
    put(root, "/etc/group", b"one:x:100:\ntwo:x:100:\n")
    with pytest.raises(PlanRefused, match="GID 100 is shared"):
        plan(req(Sysusers("60-groups.conf", "present", (GroupSpec("svc", 300),), (), (), "authoritative")), root=root)
    put(root, "/etc/group", b"")
    put(root, "/etc/passwd", b"bad:x:1:nope::/:/bin/sh\n")
    with pytest.raises(PlanRefused, match="invalid /etc/passwd line"):
        plan(req(Sysusers("60-groups.conf", "present", (GroupSpec("svc", 300),), (), (), "authoritative")), root=root)


def test_sysusers_two_new_dynamic_groups_plan_without_gid_collisions(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    canned_runner(monkeypatch)
    resource = Sysusers(
        "60-dynamic.conf", "present", (GroupSpec("first"), GroupSpec("second")), (), (), "fallback"
    )

    planned = plan(req(resource), root=root)

    assert planned.has_changes
    assert base64.b64decode(planned.changes[0].after["image"]["data_b64"]) == (
        b"g first - -\ng second - -\n"
    )


@pytest.mark.parametrize("name", ["a.b", "foo$", "1abc", "-a", "-svc", "--"])
def test_sysusers_names_match_systemd_name_rules(name):
    with pytest.raises(ValueError, match="group name is invalid"):
        GroupSpec(name)
    with pytest.raises(ValueError, match="group name is invalid"):
        GroupRemoval(name)


def test_sysusers_name_length_and_allowed_character_boundaries():
    for name in ("a" * 31, "a-", "_ok"):
        assert GroupSpec(name).name == name
        assert GroupRemoval(name).name == name

    for constructor in (GroupSpec, GroupRemoval):
        with pytest.raises(ValueError, match="group name is invalid"):
            constructor("a" * 32)


def test_sysusers_membership_names_use_account_name_characters():
    resource = Sysusers(
        "60-members.conf", "present", (), (("alice.smith", "users"),), (("buildbot$", "users"),), "fallback"
    )
    assert resource.members_add == (("alice.smith", "users"),)
    assert resource.members_absent == (("buildbot$", "users"),)

    request = load_request({
        "augeaswrap": 1,
        "writer": "membership names 1.0",
        "sysusers": [{
            "file": "60-members.conf", "state": "present", "intent": "fallback",
            "groups": [],
            "members_add": [{"user": "alice.smith", "group": "users"}],
            "members_absent": [{"user": "buildbot$", "group": "users"}],
        }],
    })
    assert load_request(tomllib.loads(request.to_toml())).to_toml() == request.to_toml()


@pytest.mark.parametrize(
    ("user", "group"),
    [
        ("a" * 256, "users"),
        ("alice$build", "users"),
        ("1alice", "users"),
        ("alice", "staff.team"),
        ("alice", "staff$"),
    ],
)
def test_sysusers_membership_pairs_reject_invalid_account_and_group_names(user, group):
    with pytest.raises(ValueError, match="name pairs"):
        Sysusers("60-members.conf", "present", (), ((user, group),), (), "fallback")

    table = {
        "file": "60-members.conf",
        "state": "present",
        "intent": "fallback",
        "groups": [],
        "members_add": [{"user": user, "group": group}],
        "members_absent": [],
    }
    with pytest.raises(ValueError, match="names are invalid"):
        load_request({"augeaswrap": 1, "writer": "invalid member names", "sysusers": [table]})


@pytest.mark.parametrize("gid", [-1, 65535, 4294967295, 4294967296, True])
def test_sysusers_fixed_gids_must_be_valid_non_reserved_32_bit_values(gid):
    with pytest.raises(ValueError, match="valid non-reserved 32-bit GID"):
        GroupSpec("svc", gid)
    with pytest.raises(ValueError, match="valid non-reserved 32-bit GIDs"):
        GroupSpec("svc", not_gid=(gid,))
    assert GroupSpec("svc", 4294967294).gid == 4294967294


@pytest.mark.parametrize(
    "path,contents,code",
    [
        ("/etc/group", b"svc:x:65535:\n", "sysusers.group.invalid"),
        ("/etc/group", b"svc:x:4294967296:\n", "sysusers.group.invalid"),
        ("/etc/passwd", b"alice:x:1000:4294967295::/home/alice:/bin/sh\n", "sysusers.passwd.invalid"),
    ],
)
def test_sysusers_refuses_invalid_existing_group_ids(path, contents, code, tmp_path):
    root = fake_root(tmp_path)
    put(root, path, contents)
    resource = Sysusers("60-absent.conf", "absent", (), (), (), "fallback")
    with pytest.raises(PlanRefused) as failure:
        plan(req(resource), root=root)
    assert failure.value.problems[0].code == code


def test_sysusers_refuses_fixed_gid_held_by_another_group_before_endpoint_planning(tmp_path):
    root = fake_root(tmp_path)
    put(root, "/etc/group", b"docker:x:999:\n")
    request = req(
        Sysusers("60-groups.conf", "present", (GroupSpec("newg", 999),), (), (), "authoritative"),
        Endpoint("example", "present", (("EXAMPLE_GID", "@gid:newg"),)),
    )
    with pytest.raises(PlanRefused) as failure:
        plan(request, root=root)
    assert failure.value.problems[0].code == "sysusers.gid.in-use"
    assert "already held by group docker" in failure.value.problems[0].message


def test_sysusers_refuses_fixed_gid_collision_with_a_prior_requested_group(tmp_path):
    root = fake_root(tmp_path)
    request = req(
        Sysusers("60-first.conf", "present", (GroupSpec("first", 999),), (), (), "authoritative"),
        Sysusers("70-second.conf", "present", (GroupSpec("second", 999),), (), (), "authoritative"),
    )
    with pytest.raises(PlanRefused) as failure:
        plan(request, root=root)
    assert failure.value.problems[0].code == "sysusers.gid.in-use"
    assert "already assigned to group first" in failure.value.problems[0].message


@pytest.mark.parametrize(
    "group_data",
    [b"svc:x:999:\nother:x:999:\n", b"svc:x:65535:\n"],
)
def test_endpoint_gid_reference_refuses_ambiguous_or_reserved_existing_gids(group_data, tmp_path):
    root = fake_root(tmp_path)
    put(root, "/etc/group", group_data)
    endpoint = Endpoint("example", "present", (("EXAMPLE_GID", "@gid:svc"),))
    with pytest.raises(PlanRefused) as failure:
        plan(req(endpoint), root=root)
    assert failure.value.problems[0].code == "endpoint.gid.group-file"


def test_endpoint_gid_requires_our_fixed_declaration_to_be_the_effective_group(tmp_path):
    root = fake_root(tmp_path)
    put(root, "/usr/lib/sysusers.d/10-vendor.conf", b"g svc 400 -\n")
    request = req(
        Sysusers("60-site.conf", "present", (GroupSpec("svc", 430),), (), (), "fallback"),
        Endpoint("example", "present", (("EXAMPLE_GID", "@gid:svc"),)),
    )
    with pytest.raises(PlanRefused) as failure:
        plan(request, root=root)
    assert failure.value.problems[0].code == "endpoint.gid.unresolved"


def test_sysusers_dry_run_diagnostics_refuse_even_with_zero_exit(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    resource = Sysusers("60-valid.conf", "present", (GroupSpec("svc", 430),), (), (), "authoritative")
    for output in (
        {"stderr": "'svc' is not a valid user or group name."},
        {"stderr": "Suggested group ID 430 for svc already used."},
        {"stdout": "Suggested group ID 430 for svc already used."},
    ):
        canned_runner(monkeypatch, **output)
        with pytest.raises(PlanRefused) as failure:
            plan(req(resource), root=root)
        assert failure.value.problems[0].code == "sysusers.validate.failed"
        assert "reported a diagnostic" in failure.value.problems[0].message

    for output in (
        {"stderr": "Failed to parse GID in an unrelated layer"},
        {"stderr": "syntax error in an unrelated layer"},
        {"stderr": "already used by an unrelated operation"},
        {"stderr": "unknown command type in an unrelated layer"},
    ):
        canned_runner(monkeypatch, **output)
        assert plan(req(resource), root=root).changes[0].action == "change"


def test_sysusers_conflict_with_earlier_configuration_refuses_zero_exit(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    put(root, "/usr/lib/sysusers.d/10-vendor.conf", b"g svc 400 -\n")
    resource = Sysusers("60-site.conf", "present", (GroupSpec("svc", 430),), (), (), "fallback")
    canned_runner(monkeypatch, stdout="Conflict with earlier configuration: group svc has GID 400")

    with pytest.raises(PlanRefused, match="reported a diagnostic") as failure:
        plan(req(resource), root=root)
    assert failure.value.problems[0].code == "sysusers.validate.failed"


def test_sysusers_membership_guards_and_strict_request_round_trip(tmp_path):
    root = fake_root(tmp_path)
    put(root, "/etc/passwd", b"alice:x:1000:1000::/home/alice:/bin/sh\n")
    put(root, "/etc/group", b"users:x:1000:\n")
    with pytest.raises(PlanRefused, match="unknown user"):
        plan(req(Sysusers("60-members.conf", "present", (), (("missing", "users"),), (), "fallback")), root=root)
    with pytest.raises(PlanRefused, match="missing group"):
        plan(req(Sysusers("60-members.conf", "present", (), (("alice", "missing"),), (), "fallback")), root=root)
    mapping = {
        "augeaswrap": 1,
        "writer": "consumer 1.0",
        "sysusers": [{
            "file": "60-members.conf", "state": "present", "intent": "authoritative",
            "groups": [{"name": "svc", "gid": 430, "not_gid": [99], "distinct_from": ["users"]}],
            "members_add": [{"user": "alice", "group": "svc"}],
            "members_absent": [{"user": "alice", "group": "users"}],
            "accept_shadow": [{"file": "/usr/lib/sysusers.d/90-vendor.conf", "key": "svc"}],
        }],
    }
    loaded = load_request(mapping)
    assert load_request(tomllib.loads(loaded.to_toml())).to_toml() == loaded.to_toml()
    with pytest.raises(ValueError, match="missing sysusers field.*intent"):
        load_request({"augeaswrap": 1, "writer": "x", "sysusers": [{"file": "x.conf", "state": "present"}]})
    with pytest.raises(ValueError, match="unknown sysusers key"):
        load_request({"augeaswrap": 1, "writer": "x", "sysusers": [{"file": "x.conf", "state": "absent", "intent": "fallback", "bad": 1}]})
    with pytest.raises(ValueError, match="cannot be both added and absent"):
        load_request({"augeaswrap": 1, "writer": "x", "sysusers": [{
            "file": "x.conf", "state": "present", "intent": "fallback", "groups": [],
            "members_add": [{"user": "alice", "group": "users"}],
            "members_absent": [{"user": "alice", "group": "users"}],
        }]})
    with pytest.raises(ValueError, match="duplicate user/group pair"):
        Sysusers("60-dup.conf", "present", (), (("alice", "users"), ("alice", "users")), (), "fallback")
    with pytest.raises(ValueError, match="duplicate group name"):
        Sysusers("60-dup.conf", "present", (GroupSpec("svc"), GroupSpec("svc")), (), (), "fallback")

    group_request = load_request({
        "augeaswrap": 1, "writer": "group 1", "group": [{"name": "old", "state": "absent"}],
    })
    assert load_request(tomllib.loads(group_request.to_toml())).to_toml() == group_request.to_toml()
    with pytest.raises(ValueError, match="unknown group key"):
        load_request({"augeaswrap": 1, "writer": "group 1", "group": [{"name": "old", "state": "absent", "extra": True}]})


@pytest.mark.parametrize("name", ["-f", "--"])
def test_sysusers_rejects_leading_dash_names_in_python_and_toml_inputs(name):
    with pytest.raises(ValueError, match="group name is invalid"):
        GroupSpec(name)
    with pytest.raises(ValueError, match="group name is invalid"):
        GroupRemoval(name)

    for field, user, group in (
        ("members_add", name, "users"),
        ("members_add", "alice", name),
        ("members_absent", name, "users"),
        ("members_absent", "alice", name),
    ):
        with pytest.raises(ValueError, match="name pairs"):
            Sysusers("60-members.conf", "present", (), ((user, group),) if field == "members_add" else (), ((user, group),) if field == "members_absent" else (), "fallback")
        table = {
            "file": "60-members.conf",
            "state": "present",
            "intent": "fallback",
            "groups": [],
            "members_add": [],
            "members_absent": [],
        }
        table[field] = [{"user": user, "group": group}]
        with pytest.raises(ValueError, match="names are invalid"):
            load_request({"augeaswrap": 1, "writer": "test 1.0", "sysusers": [table]})

    with pytest.raises(ValueError, match="group name is invalid"):
        load_request({"augeaswrap": 1, "writer": "test 1.0", "group": [{"name": name, "state": "absent"}]})


def test_membership_only_sysusers_does_not_write_an_empty_fragment_or_run_the_native_tool(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    put(root, "/etc/passwd", b"alice:x:1000:1000::/home/alice:/bin/sh\n")
    put(root, "/etc/group", b"users:x:1000:\n")
    existing = put(root, "/etc/sysusers.d/60-members.conf", b"g stale 1200 -\n")
    before = existing.read_bytes()
    before_stat = existing.stat()
    calls = canned_runner(monkeypatch)
    resource = Sysusers("60-members.conf", "present", (), (("alice", "users"),), (), "fallback")
    planned = plan(req(resource), root=root)
    assert planned.changes[0].after["file_changed"] is False
    assert planned.changes[0].after["run_sysusers"] is False
    assert [action.argv for action in resource.post_actions([planned.changes[0]])] == [
        ("gpasswd", "-a", "alice", "--", "users")
    ]
    assert calls == []
    result = apply(planned)
    assert existing.read_bytes() == before
    assert (existing.stat().st_ino, existing.stat().st_mtime_ns) == (before_stat.st_ino, before_stat.st_mtime_ns)
    assert journal(result.txn, root=root)["actions"][0]["status"] == "skipped(root)"


def test_sysusers_runner_failure_and_group_file_parsing_errors(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    canned_runner(monkeypatch, returncode=1, stderr="invalid row")
    with pytest.raises(PlanRefused, match="systemd-sysusers --root"):
        plan(req(Sysusers("60-bad.conf", "present", (GroupSpec("svc", 430),), (), (), "fallback")), root=root)
    def unavailable(argv, *, timeout):
        assert tuple(argv)[0] == "systemd-sysusers"
        assert_runner_timeout(timeout)
        raise OSError("runner unavailable")

    monkeypatch.setattr(shadow, "run_command", unavailable)
    with pytest.raises(PlanRefused, match="cannot run systemd-sysusers dry-run"):
        plan(req(Sysusers("60-error.conf", "present", (GroupSpec("svc", 430),), (), (), "fallback")), root=root)
    put(root, "/etc/group", b"invalid\n")
    with pytest.raises(PlanRefused, match="invalid /etc/group line"):
        plan(req(Sysusers("60-error.conf", "absent", (), (), (), "fallback")), root=root)


def test_sysusers_group_line_parser_and_verify_error_paths(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="invalid sysusers definition"):
        sysusers_module._group_lines(b"g svc 'unterminated\n")
    with pytest.raises(ValueError, match="invalid sysusers group definition"):
        sysusers_module._group_lines(b"g\n")
    with pytest.raises(ValueError, match="invalid sysusers membership definition"):
        sysusers_module._group_lines(b"m alice\n")
    with pytest.raises(ValueError, match="unknown sysusers directive"):
        sysusers_module._group_lines(b"x name value\n")
    assert sysusers_module._group_lines(b"r 1000\n") == ()
    def empty_sysusers_fields(value, *, comments, posix):
        assert value == "g svc"
        assert comments is True
        assert posix is True
        return []

    monkeypatch.setattr(sysusers_module.shlex, "split", empty_sysusers_fields)
    assert sysusers_module._group_lines(b"g svc\n") == ()

    root = fake_root(tmp_path)
    resource = Sysusers("60-verify.conf", "present", (), (), (), "authoritative")
    monkeypatch.setattr(file_ops, "verify_file", lambda *_args: [])
    for failure, code in (
        (ValueError("bad layers"), "sysusers.shadow.verify"),
        (PlanRefused([Problem("shadow.later", "stale shadow")]), "shadow.later"),
    ):
        monkeypatch.setattr(sysusers_module, "observe_layers", lambda *_args, error=failure: (_ for _ in ()).throw(error))
        problems = resource.verify(SimpleNamespace(root=root, expected={}))
        assert problems[0].code == code


def test_file_post_and_pre_image_projections_reject_invalid_inputs(tmp_path):
    root = fake_root(tmp_path)
    with pytest.raises(ValueError, match="no file path"):
        file_ops.post_image(SimpleNamespace(path=None), SimpleNamespace(root=root), "file:/etc/a")
    with pytest.raises(ValueError, match="no prior observation"):
        file_ops.pre_image(SimpleNamespace(before=None))
    with pytest.raises(ValueError, match="no target image"):
        file_ops.pre_image(SimpleNamespace(before={"target": None}))
    assert file_ops.pre_image(SimpleNamespace(before={"target": {"exists": False}})) == {"exists": False}


def test_sysusers_failed_commit_uses_restore_shaped_failure_images(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    resource = Sysusers("60-fail.conf", "present", (GroupSpec("svc", 430),), (), (), "authoritative")
    planned = plan(req(resource), root=root)

    def fail_commit(*_args):
        raise OSError("commit interrupted")

    monkeypatch.setattr(file_ops, "commit_file", fail_commit)
    with pytest.raises(ApplyFailed) as failure:
        apply(planned)
    record = journal(failure.value.txn, root=root)

    assert record["status"] == "failed"
    assert record["post_images"] == {}
    assert record["unknown_post_images"] == []


def test_sysusers_dry_run_contains_observed_layer_files(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    vendor = put(root, "/usr/lib/sysusers.d/10-vendor.conf", b"g vendor 700 -\n")
    calls = []

    def run(argv, *, timeout):
        assert_runner_timeout(timeout)
        kwargs = {"timeout": timeout}
        calls.append((tuple(argv), kwargs))
        stage_root = Path(argv[2])
        assert (stage_root / "usr/lib/sysusers.d/10-vendor.conf").read_bytes() == vendor.read_bytes()
        assert (stage_root / "etc/sysusers.d/60-app.conf").read_text(encoding="utf-8") == "g app 430 -\n"
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(shadow, "run_command", run)
    planned = plan(req(Sysusers("60-app.conf", "present", (GroupSpec("app", 430),), (), (), "fallback")), root=root)
    apply(planned)
    assert calls and calls[0][0][0:2] == ("systemd-sysusers", "--root")


def test_group_removal_primary_guard_irreversible_effect_and_verify(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    put(root, "/etc/group", b"staff:x:900:alice\n")
    put(root, "/etc/passwd", b"alice:x:1000:900::/home/alice:/bin/sh\n")
    with pytest.raises(PlanRefused) as failure:
        plan(req(GroupRemoval("staff")), root=root)
    assert failure.value.problems[0].code == "group.primary"

    # This non-primary group may be the invoking process's own supplementary group.
    put(root, "/etc/passwd", b"alice:x:1000:1000::/home/alice:/bin/sh\n")
    planned = plan(req(GroupRemoval("staff")), root=root)
    assert not planned.as_dict()["changes"][0]["revertible"]
    assert planned.changes[0].details == ("irreversible: groupdel staff",)
    result = apply(planned)
    record = journal(result.txn, root=root)
    assert record["irreversible_effects"] == [{"address": "group:staff", "effect": "groupdel staff"}]
    assert record["actions"][0]["argv"] == ["groupdel", "--", "staff"]
    assert record["actions"][0]["status"] == "skipped(root)"
    with pytest.raises(PlanRefused, match="no revert support"):
        revert(result.txn, root=root)

    missing = plan(req(GroupRemoval("missing")), root=root)
    assert not missing.has_changes
    monkeypatch.setattr(sysusers_module, "_passwd_groups", lambda _root: ({"staff": (900, ())}, set(), {}))
    live = GroupRemoval("staff").verify(SimpleNamespace(root=Path("/")))
    assert live[0].code == "group.verify.failed"
    with pytest.raises(ValueError, match="only supports explicit"):
        GroupRemoval("staff", "present")


def test_group_removal_refuses_gid_zero_even_without_a_passwd_file(tmp_path):
    root = fake_root(tmp_path)
    put(root, "/etc/group", b"root:x:0:\n")
    with pytest.raises(PlanRefused) as failure:
        plan(req(GroupRemoval("root")), root=root)
    assert failure.value.problems[0].code == "group.gid.protected"


@pytest.mark.parametrize(
    "resources",
    [
        (GroupRemoval("svc"), Sysusers("60-app.conf", "present", (GroupSpec("svc", 430),), (), (), "fallback")),
        (Sysusers("60-app.conf", "present", (GroupSpec("svc", 430),), (), (), "fallback"), GroupRemoval("svc")),
    ],
)
def test_group_removal_and_definition_conflict_in_either_request_order(resources, tmp_path):
    root = fake_root(tmp_path)
    with pytest.raises(PlanRefused, match="both removed and defined"):
        plan(req(*resources), root=root)


@pytest.mark.parametrize("membership", ["add", "absent"])
@pytest.mark.parametrize("group_first", [False, True])
def test_group_removal_refuses_same_request_membership_changes(membership, group_first, tmp_path):
    root = fake_root(tmp_path)
    put(root, "/etc/passwd", b"alice:x:1000:1000::/home/alice:/bin/sh\n")
    put(root, "/etc/group", b"staff:x:900:alice\n")
    resource = Sysusers(
        "60-members.conf",
        "present",
        (),
        (("alice", "staff"),) if membership == "add" else (),
        (("alice", "staff"),) if membership == "absent" else (),
        "authoritative",
    )
    removal = GroupRemoval("staff")
    resources = (removal, resource) if group_first else (resource, removal)
    with pytest.raises(PlanRefused) as failure:
        plan(req(*resources), root=root)
    assert failure.value.problems[0].code == "sysusers.member.group.removed"


def test_failed_endpoint_revert_records_the_restore_observation_for_retry(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    resource = Endpoint("example", "present", (("EXAMPLE_SOCKET", "/run/example.sock"),))
    applied = apply(plan(req(resource), root=root))
    fragment = root / "etc/host-endpoints.d/example.env"
    previous = fragment.read_bytes()
    restore_fragment = endpoint_module._restore_fragment

    def restore_then_fail(selected_root, name, data):
        restore_fragment(selected_root, name, data)
        raise OSError("injected failure after endpoint restore write")

    monkeypatch.setattr(endpoint_module, "_restore_fragment", restore_then_fail)
    with pytest.raises(ApplyFailed) as failure:
        revert(applied.txn, root=root)

    failed_journal = journal(failure.value.txn, root=root)
    assert failed_journal["unknown_post_images"] == []
    assert "endpoint:example" in failed_journal["post_images"]

    monkeypatch.setattr(endpoint_module, "_restore_fragment", restore_fragment)
    retry = revert(failure.value.txn, root=root)
    assert retry.txn
    assert fragment.read_bytes() == previous


def test_tmpfiles_present_absent_idempotence_validation_and_revert(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    calls = canned_runner(monkeypatch)
    text = "# managed data\nd /var/lib/example 0750 svc svc -\nf /var/lib/example/state - svc svc - value\n"
    resource = Tmpfiles("60-example.conf", "present", text, "authoritative")
    planned = plan(req(resource), root=root)
    assert calls and calls[0][0][:4] == ("systemd-tmpfiles", "--root", calls[0][0][2], "--dry-run")
    assert any("irreversible: systemd-tmpfiles --create" in detail for detail in planned.changes[0].details)
    action = planned.as_dict()["post_actions"][0]
    assert action["argv"] == ["systemd-tmpfiles", "--create", "/etc/tmpfiles.d/60-example.conf"]
    result = apply(planned)
    target = root / "etc/tmpfiles.d/60-example.conf"
    assert target.read_text(encoding="utf-8") == text
    unchanged = plan(req(resource), root=root)
    assert not unchanged.has_changes
    assert not any("irreversible:" in detail for detail in unchanged.changes[0].details)
    revert(result.txn, root=root)
    assert not target.exists()

    existing = put(root, "/etc/tmpfiles.d/40-old.conf", b"d /var/cache/old 0755 - - -\n")
    removed = Tmpfiles("40-old.conf", "absent", None, "fallback")
    result = apply(plan(req(removed), root=root))
    assert not existing.exists()
    assert "paths already created in place" in journal(result.txn, root=root)["changes"][0]["details"][0]
    reverted = revert(result.txn, root=root)
    assert journal(reverted.txn, root=root)["actions"] == []
    assert existing.read_bytes() == b"d /var/cache/old 0755 - - -\n"

    original = put(root, "/etc/sysusers.d/60-existing.conf", b"g app 430 -\n")
    canned_runner(monkeypatch)
    changed = Sysusers("60-existing.conf", "present", (GroupSpec("app", 431),), (), (), "authoritative")
    applied = apply(plan(req(changed), root=root))
    original_actions = journal(applied.txn, root=root)["actions"]
    assert original_actions[0]["argv"] == ["systemd-sysusers", "/etc/sysusers.d/60-existing.conf"]
    restored = revert(applied.txn, root=root)
    assert [item["argv"] for item in journal(restored.txn, root=root)["actions"]] == [
        ["systemd-sysusers", "/etc/sysusers.d/60-existing.conf"]
    ]
    assert original.read_bytes() == b"g app 430 -\n"


@pytest.mark.parametrize("text", [
    "? /var/lib/x 0755 - - -\n",
    "d relative 0755 - - -\n",
    "d /var/../tmp 0755 - - -\n",
    "d /var//lib/x 0755 - - -\n",
    "d /var/lib/x 0999 - - -\n",
    "d /var/lib/x 0755 - - -\nd /var/lib/x 0755 - - -\n",
])
def test_tmpfiles_rejects_invalid_line_syntax(text):
    with pytest.raises(ValueError):
        Tmpfiles("60-bad.conf", "present", text, "authoritative")


@pytest.mark.parametrize("text", ["d /var/lib/x 0755 - - -\r\n", "# invalid\x00comment\n"])
def test_tmpfiles_rejects_control_characters(text):
    with pytest.raises(ValueError, match="NUL or carriage returns"):
        Tmpfiles("60-bad.conf", "present", text, "authoritative")


def test_tmpfiles_accepts_systemd_types_modifiers_and_quoted_paths():
    lines = (
        b'h /var/lib/x - - - - -\n'
        b'H /var/lib/y - - - - -\n'
        b'e /var/lib/z - - - - -\n'
        b'd= /var/lib/a 0755 - - -\n'
        b'f~ /var/lib/b 0644 - - - YWJj\n'
        b'f^ /var/lib/c 0644 - - - example-credential\n'
        b'f+ "/var/lib/with space" 0644 - - - contents with spaces\n'
        b'd /var/lib/escaped\\sspace 0755 - - -\n'
    )
    parsed = tmpfiles_module._parse_lines(lines)
    assert tuple(path for path, _line in parsed) == (
        "/var/lib/x", "/var/lib/y", "/var/lib/z", "/var/lib/a", "/var/lib/b", "/var/lib/c",
        "/var/lib/with space", "/var/lib/escaped space",
    )
    assert "contents with spaces" in parsed[6][1]
    assert tmpfiles_module._parse_lines(b"d /var/lib/hidden ~0750 - - -\n")[0][0] == "/var/lib/hidden"
    assert tmpfiles_module._parse_lines(b"d relative invalid - - -\n", strict=False) == (
        ("relative", "d relative invalid - - -"),
    )
    with pytest.raises(ValueError, match="invalid quoting"):
        tmpfiles_module._parse_lines(b'd "/var/lib/unclosed 0755 - - -\n')


def test_tmpfiles_fields_keep_fixed_columns_before_arguments():
    assert tmpfiles_module._fields("d /var/lib/x 0755 - - - argument with spaces") == (
        ["d", "/var/lib/x", "0755", "-", "-", "-"],
        "argument with spaces",
    )


def test_tmpfiles_parser_accepts_exactly_two_type_path_fields():
    assert tmpfiles_module._parse_lines(b"x -\n") == (("-", "x -"),)
    with pytest.raises(ValueError, match="at least a type and path"):
        tmpfiles_module._parse_lines(b"x\n")


def test_tmpfiles_parser_validates_optional_mode_with_exactly_three_fields():
    assert tmpfiles_module._parse_lines(b"d /var/lib/x 0755\n")[0][1] == "d /var/lib/x 0755"
    with pytest.raises(ValueError, match="mode must be octal"):
        tmpfiles_module._parse_lines(b"d /var/lib/x bad\n")


def test_tmpfiles_shadow_parsing_keeps_duplicate_path_operations():
    repeated = b"d /var/lib/x 0755 - - -\nd /var/lib/x 0700 - - -\n"
    assert tuple(line for _path, line in tmpfiles_module._shadow_lines(repeated)) == (
        "d /var/lib/x 0755 - - -",
        "d /var/lib/x 0700 - - -",
    )


def test_tmpfiles_accept_shadow_rejects_bad_pair_path_and_key_forms():
    for value, message in (
        ([("/etc//tmpfiles.conf", "KEY")], "exact absolute file"),
        ([("/etc/tmpfiles.conf", None)], "exact absolute file"),
        ([("/etc/tmpfiles.conf", "bad\x00key")], "exact absolute file"),
        ([("/etc/tmpfiles.conf", "")], "exact absolute file"),
        ([("/etc/tmpfiles.conf",)], "entries must be"),
        ([("/etc/tmpfiles.conf", "KEY", "extra")], "entries must be"),
    ):
        with pytest.raises(ValueError, match=message):
            tmpfiles_module._accept_value(value)


def test_tmpfiles_staged_dry_run_has_selected_account_files_and_planned_groups(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    put(root, "/etc/group", b"users:x:1000:\nlegacy:x:1001:\n")
    put(root, "/etc/passwd", b"alice:x:1000:1000::/home/alice:/bin/sh\n")
    stage = tmp_path / "stage" / "60-example.conf"
    stage.parent.mkdir()
    stage.write_text("d /var/lib/example 0750 alice svc -\n", encoding="utf-8")
    seen = []

    def run(argv, *, timeout):
        assert_runner_timeout(timeout)
        stage_root = Path(argv[2])
        seen.append((stage_root / "etc/group").read_text(encoding="utf-8"))
        assert (stage_root / "etc/passwd").read_bytes() == b"alice:x:1000:1000::/home/alice:/bin/sh\n"
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(shadow, "run_command", run)
    before = {
        "group": file_ops.observe_file(root, "/etc/group"),
        "passwd": file_ops.observe_file(root, "/etc/passwd"),
    }
    staged = file_ops.StagedFile(str(stage), {"exists": True})
    tmpfiles_module._validate_staged(
        SimpleNamespace(), "/etc/tmpfiles.d/60-example.conf", staged, before, {"svc": 430}, ["legacy"]
    )
    assert seen == ["users:x:1000:\nsvc:x:430:\n"]

    seen.clear()
    tmpfiles_module._validate_staged(
        SimpleNamespace(), "/etc/tmpfiles.d/60-example.conf", staged, before, {"svc": 0}, []
    )
    assert seen == ["users:x:1000:\nlegacy:x:1001:\nsvc:x:0:\n"]

    for invalid_gids in ({1: 430}, {"svc": "430"}, {"svc": False}, {"svc": -1}):
        with pytest.raises(PlanRefused, match="planned group map is invalid"):
            tmpfiles_module._validate_staged(
                SimpleNamespace(), "/etc/tmpfiles.d/60-example.conf", staged, before, invalid_gids, []
            )

    bad_group = {"exists": True, "data_b64": "dXNl cnM6eDoxMDAwOg=="}
    with pytest.raises(ValueError):
        tmpfiles_module._validate_staged(
            SimpleNamespace(), "/etc/tmpfiles.d/60-example.conf", staged,
            {**before, "group": bad_group}, None, [],
        )

    with pytest.raises(PlanRefused, match="planned group map is invalid"):
        tmpfiles_module._validate_staged(
            SimpleNamespace(), "/etc/tmpfiles.d/60-example.conf", staged, before, {"svc": True}, []
        )
    with pytest.raises(PlanRefused, match="planned group removal map is invalid"):
        tmpfiles_module._validate_staged(
            SimpleNamespace(), "/etc/tmpfiles.d/60-example.conf", staged, before, {}, [True]
        )


def test_tmpfiles_shadow_first_path_exact_accept_equal_and_file_mask(tmp_path):
    root = fake_root(tmp_path)
    put(root, "/usr/lib/tmpfiles.d/20-vendor.conf", b"d /var/lib/example 0755 - - -\n")
    resource = Tmpfiles("60-site.conf", "present", "d /var/lib/example 0700 - - -\n", "authoritative")
    with pytest.raises(PlanRefused) as failure:
        plan(req(resource), root=root)
    assert failure.value.problems[0].code == "shadow.earlier"
    accepted = Tmpfiles(
        "60-site.conf", "present", "d /var/lib/example 0700 - - -\n", "authoritative",
        (("/usr/lib/tmpfiles.d/20-vendor.conf", "/var/lib/example"),),
    )
    assert any("note shadow.earlier" in item for item in plan(req(accepted), root=root).changes[0].details)

    equal_root = fake_root(tmp_path / "equal")
    put(equal_root, "/usr/lib/tmpfiles.d/20-vendor.conf", b"d /var/lib/example 0700 - - -\n")
    equal = Tmpfiles("60-site.conf", "present", "d /var/lib/example 0700 - - -\n", "fallback")
    assert any("equal value" in item for item in plan(req(equal), root=equal_root).changes[0].details)

    masked_root = fake_root(tmp_path / "masked")
    put(masked_root, "/etc/tmpfiles.d/60-site.conf", b"d /var/lib/example 0700 - - -\n")
    put(masked_root, "/usr/lib/tmpfiles.d/60-site.conf", b"d /var/lib/vendor 0755 - - -\n")
    with pytest.raises(PlanRefused) as masked:
        plan(req(Tmpfiles("60-site.conf", "present", "d /var/lib/example 0700 - - -\n", "fallback")), root=masked_root)
    assert masked.value.problems[0].code == "shadow.mask"

    glob_root = fake_root(tmp_path / "glob")
    put(glob_root, "/usr/local/lib/tmpfiles.d/90-vendor.conf", b"h /var/lib/example/child - - - - -\n")
    glob = Tmpfiles("60-site.conf", "present", "e /var/lib/example/* - - - - -\n", "fallback")
    with pytest.raises(PlanRefused) as glob_failure:
        plan(req(glob), root=glob_root)
    assert glob_failure.value.problems[0].code.startswith("shadow.")


@pytest.mark.parametrize(
    ("resource", "vendor_path", "vendor_text", "expected_code"),
    [
        (Sysusers("60-site.conf", "present", (GroupSpec("svc", 430),), (), (), "authoritative"),
         "/usr/lib/sysusers.d/10-vendor.conf", b"g svc 431 -\n", "shadow.earlier"),
        (Sysusers("60-site.conf", "present", (GroupSpec("svc", 430),), (), (), "authoritative"),
         "/usr/lib/sysusers.d/10-vendor.conf", b'u svc 431 "service account" /nonexistent /usr/sbin/nologin\n', "shadow.earlier"),
        (Sysusers("60-site.conf", "present", (GroupSpec("svc", 430),), (), (), "authoritative"),
         "/usr/lib/sysusers.d/10-vendor.conf", b"m alice svc\n", "shadow.earlier"),
        (Tmpfiles("60-site.conf", "present", "d /var/lib/example 0750 - - -\n", "authoritative"),
         "/usr/lib/tmpfiles.d/10-vendor.conf", b"d /var/lib/example 0700 - - -\n", "shadow.earlier"),
    ],
)
def test_sysusers_and_tmpfiles_verify_recheck_current_shadow_sources(
    resource, vendor_path, vendor_text, expected_code, tmp_path
):
    root = fake_root(tmp_path)
    target = put(root, resource.path, (
        b"g svc 430 -\n" if isinstance(resource, Sysusers) else b"d /var/lib/example 0750 - - -\n"
    ))
    expected = file_ops.StagedFile(str(target), file_ops.observe_file(root, resource.path))
    put(root, vendor_path, vendor_text)
    context = SimpleNamespace(root=root, expected={resource.addresses()[0]: expected})

    assert expected_code in {problem.code for problem in resource.verify(context)}


def test_sysusers_shadow_parser_tracks_implicit_groups_from_users_and_memberships():
    assert sysusers_module._group_lines(
        b'u daemon - "system daemon" /nonexistent /usr/sbin/nologin\n'
        b'm alice operators\n'
        b'r - 400-499\n'
    ) == (
        ("daemon", 'u daemon - system daemon /nonexistent /usr/sbin/nologin'),
        ("operators", "m alice operators"),
    )


def test_sysusers_does_not_treat_an_implicit_user_id_as_a_planned_group_gid(tmp_path):
    root = fake_root(tmp_path)
    put(root, "/etc/group", b"users:x:1000:\n")
    put(root, "/usr/lib/sysusers.d/10-vendor.conf", b'u svc 431 "service account" /nonexistent /usr/sbin/nologin\n')
    resource = Sysusers("60-site.conf", "present", (GroupSpec("svc", 430),), (), (), "fallback")
    context = SimpleNamespace(
        root=root,
        writer="test",
        stage_dir=tmp_path,
        planned_gids={},
        planned_group_names=set(),
        planned_group_absent=set(),
    )
    observed = resource.observe(context)

    resource.diff(context, observed)

    assert context.planned_gids == {}


def test_tmpfiles_accounts_are_part_of_stale_plan_preconditions(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    group = put(root, "/etc/group", b"users:x:1000:\n")
    resource = Tmpfiles("60-site.conf", "present", "d /var/lib/example 0750 - - -\n", "fallback")
    planned = plan(req(resource), root=root)
    group.write_text("users:x:1001:\n", encoding="utf-8")
    with pytest.raises(StalePlan, match="changed after plan"):
        apply(planned)


def test_tmpfiles_runner_failure_exception_and_toml_round_trip(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    resource = Tmpfiles(
        "60-example.conf", "present", "d /var/lib/example 0750 svc svc -\n", "fallback",
        (("/usr/lib/tmpfiles.d/20-vendor.conf", "/var/lib/example"),),
    )
    mapping = {"augeaswrap": 1, "writer": "consumer 1", "tmpfiles": [{
        "file": resource.file, "state": resource.state, "text": resource.text,
        "intent": resource.intent,
        "accept_shadow": [{"file": "/usr/lib/tmpfiles.d/20-vendor.conf", "key": "/var/lib/example"}],
    }]}
    loaded = load_request(mapping)
    assert load_request(tomllib.loads(loaded.to_toml())).to_toml() == loaded.to_toml()
    canned_runner(monkeypatch, returncode=1, stderr="invalid line")
    with pytest.raises(PlanRefused, match="systemd-tmpfiles --root"):
        plan(req(resource), root=root)
    def unavailable(argv, *, timeout):
        assert tuple(argv)[0] == "systemd-tmpfiles"
        assert_runner_timeout(timeout)
        raise OSError("runner unavailable")

    monkeypatch.setattr(shadow, "run_command", unavailable)
    with pytest.raises(PlanRefused, match="cannot run systemd-tmpfiles dry-run"):
        plan(req(resource), root=root)
    with pytest.raises(ValueError, match="missing tmpfiles field.*intent"):
        load_request({"augeaswrap": 1, "writer": "x", "tmpfiles": [{"file": "x.conf", "state": "absent"}]})


def test_endpoint_sysusers_gid_resolution_request_order_and_frozen_files(tmp_path, monkeypatch):
    root = fake_root(tmp_path, environment=b"KEEP=1\n")
    canned_runner(monkeypatch)
    account = Sysusers("60-app.conf", "present", (GroupSpec("app", 430),), (), (), "authoritative")
    endpoint = Endpoint("example", "present", (("APP_GID", "@gid:app"), ("APP_SOCKET", "/run/app.sock")))
    planned = plan(req(account, endpoint), root=root)
    assert planned.changes[1].after["entries"]["APP_GID"] == "430"
    assert planned.as_dict()["post_actions"][0]["argv"] == [
        "systemd-sysusers", "/etc/sysusers.d/60-app.conf"
    ]
    assert planned.as_dict()["post_actions"][1]["argv"][1:5] == [
        "-m", "augeaswrap", "endpoints", "publish"
    ]
    applied = apply(planned)
    fragment = root / "etc/host-endpoints.d/example.env"
    assert fragment.read_bytes() == (
        b"# host-endpoints v1\n# owner=example\n# writer=test resource kinds 1.0\n"
        b"APP_GID=430\nAPP_SOCKET=/run/app.sock\n"
    )
    assert b"# BEGIN host-endpoints (generated by cli_extended.hostcfg.endpoints; do not edit)\n" in (root / "etc/environment").read_bytes()
    revert(applied.txn, root=root)
    assert not fragment.exists()

    reverse = fake_root(tmp_path / "reverse")
    with pytest.raises(PlanRefused) as failure:
        plan(req(endpoint, account), root=reverse)
    assert failure.value.problems[0].code == "endpoint.gid.unresolved"


def test_endpoint_withdraw_precedes_later_resource_commit(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    endpoints.publish("example", {"APP_SOCKET": "/run/app.sock"}, writer="seed 1", root=root)
    unit_path = put(root, "/etc/systemd/system/app.service", b"[Unit]\nDescription=app\n")
    order: list[str] = []

    original_endpoint_commit = Endpoint.commit

    def endpoint_commit(resource, ctx, staged):
        if resource.name == "example" and staged.state == "absent":
            order.append("withdraw-commit")
        return original_endpoint_commit(resource, ctx, staged)

    original_unlink = unit_module._fs.unlink_and_sync

    def unit_unlink(selected_root, target):
        if target == unit_path:
            order.append("unit-remove")
        return original_unlink(selected_root, target)

    monkeypatch.setattr(Endpoint, "commit", endpoint_commit)
    monkeypatch.setattr(unit_module._fs, "unlink_and_sync", unit_unlink)
    removal = Unit(name="app.service", state="absent")

    apply(plan(req(Endpoint("example", "absent"), removal), root=root))
    assert order == ["withdraw-commit", "unit-remove"]
    with pytest.raises(PlanRefused, match="endpoint withdrawal must precede unit removal"):
        plan(req(removal, Endpoint("example", "absent")), root=root)


def test_endpoint_plan_errors_exact_fragment_revert_and_loader_round_trip(tmp_path):
    root = fake_root(tmp_path, environment=b"APP_SOCKET=/external.sock\n")
    endpoint = Endpoint("example", "present", (("APP_SOCKET", "/run/app.sock"),))
    with pytest.raises(PlanRefused) as failure:
        plan(req(endpoint), root=root)
    assert failure.value.problems[0].code.startswith("endpoint.environment")

    clean_root = fake_root(tmp_path / "clean")
    seed = endpoints.render("example", {"APP_SOCKET": "/run/old.sock"}, writer="old 1", current_environment=b"", existing_fragments={}, root=clean_root)
    path = clean_root / "etc/host-endpoints.d/example.env"
    path.parent.mkdir(parents=True, exist_ok=True)
    old_bytes = b"# host-endpoints v1\n# owner=example\n# writer=exact old\nAPP_SOCKET=/run/old.sock\n"
    path.write_bytes(old_bytes)
    result = apply(plan(req(Endpoint("example", "present", (("APP_SOCKET", "/run/new.sock"),))), root=clean_root))
    revert(result.txn, root=clean_root)
    assert path.read_bytes() == old_bytes

    loaded = load_request({"augeaswrap": 1, "writer": "endpoint 1", "endpoint": [{
        "name": "example", "state": "present", "entries": [{"key": "APP_SOCKET", "value": "/run/app.sock"}],
    }]})
    assert load_request(tomllib.loads(loaded.to_toml())).to_toml() == loaded.to_toml()


def test_request_loader_preserves_cross_kind_order_and_endpoint_checks_existing_gid(tmp_path):
    root = fake_root(tmp_path)
    put(root, "/etc/group", b"users:x:1000:\n")
    mapping = {
        "augeaswrap": 1,
        "writer": "ordering 1",
        "endpoint": [{"name": "example", "state": "absent"}],
        "sysusers": [{"file": "60-app.conf", "state": "absent", "intent": "fallback"}],
    }
    assert [item.kind for item in load_request(mapping).resources] == ["endpoint", "sysusers"]
    assert plan(req(Endpoint("example", "present", (("APP_GID", "@gid:users"),))), root=root).changes[0].after["entries"]["APP_GID"] == "1000"
    for resources in (
        (GroupRemoval("users"), Endpoint("example", "present", (("APP_GID", "@gid:users"),))),
        (Endpoint("example", "present", (("APP_GID", "@gid:users"),)), GroupRemoval("users")),
    ):
        with pytest.raises(PlanRefused) as removed_group:
            plan(req(*resources), root=root)
        assert removed_group.value.problems[0].code == "endpoint.gid.removed"
    with pytest.raises(PlanRefused) as failure:
        plan(req(Endpoint("bad", "present", (("BAD_VALUE", "@gid:"),))), root=root)
    assert failure.value.problems[0].code == "endpoint.gid.invalid"

    vendor_root = fake_root(tmp_path / "vendor")
    put(vendor_root, "/usr/lib/sysusers.d/20-vendor.conf", b"g app 431 -\n")
    fallback = Sysusers("60-site.conf", "present", (GroupSpec("app", 430),), (), (), "fallback")
    with pytest.raises(PlanRefused) as unverified:
        plan(req(fallback, Endpoint("example", "present", (("APP_GID", "@gid:app"),))), root=vendor_root)
    assert unverified.value.problems[0].code == "endpoint.gid.unresolved"

    duplicate = req(
        Sysusers("60-first.conf", "present", (GroupSpec("duplicate", 430),), (), (), "fallback"),
        Sysusers("70-second.conf", "present", (GroupSpec("duplicate", 430),), (), (), "fallback"),
    )
    with pytest.raises(PlanRefused, match="declared by multiple sysusers resources"):
        plan(duplicate, root=fake_root(tmp_path / "duplicate"))


def test_staged_validators_and_live_verifiers_refuse_canned_failures(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    resource = Tmpfiles("60-verify.conf", "present", "d /var/lib/example 0750 - - -\n", "fallback")
    planned = plan(req(resource), root=root)
    monkeypatch.setattr(tmpfiles_module.file_ops, "verify_file", lambda *_args: [])
    monkeypatch.setattr(tmpfiles_module, "observe_layers", lambda *_args: {"files": {}, "links": {}, "directories": {}})
    ctx = SimpleNamespace(root=Path("/"), expected={resource.addresses()[0]: object()})
    assert resource.verify(ctx) == []

    sysusers_resource = Sysusers("60-verify.conf", "present", (GroupSpec("svc", 430),), (), (), "fallback")
    monkeypatch.setattr(sysusers_module.file_ops, "verify_file", lambda *_args: [])
    monkeypatch.setattr(sysusers_module, "observe_layers", lambda *_args: {"files": {}, "links": {}, "directories": {}})
    monkeypatch.setattr(sysusers_module, "_passwd_groups", lambda _root: ({}, set(), {}))
    verify_ctx = SimpleNamespace(root=Path("/"), expected={sysusers_resource.addresses()[0]: object()})
    assert sysusers_resource.verify(verify_ctx)[0].code == "sysusers.group.verify"
    verify_ctx.expected[sysusers_resource.addresses()[0]] = SimpleNamespace()
    assert {item.code for item in sysusers_resource.verify(verify_ctx)} == {"sysusers.group.verify"}


def test_file_restore_hook_rejects_untrusted_and_inconsistent_preimages():
    with pytest.raises(ValueError, match="path is invalid"):
        file_ops.restore_file("sysctl", "sysctl:/../../etc/passwd", {"path": "/../../etc/passwd", "exists": False}, ())
    with pytest.raises(ValueError, match="does not match its address"):
        file_ops.restore_file("sysctl", "sysctl:/etc/other.conf", {"path": "/etc/sysctl.d/x.conf", "exists": False}, ())
    with pytest.raises(ValueError, match="exists flag"):
        file_ops.restore_file("sysctl", "sysctl:/etc/sysctl.d/x.conf", {"path": "/etc/sysctl.d/x.conf", "exists": 1}, ())
    base = {"path": "/etc/sysctl.d/x.conf", "exists": True, "mode": 0o644, "uid": 0, "gid": 0, "data_b64": "YQ==", "sha256": "bad"}
    with pytest.raises(ValueError, match="digest differs"):
        file_ops.restore_file("sysctl", "sysctl:/etc/sysctl.d/x.conf", base, ())
    base["sha256"] = hashlib.sha256(b"a").hexdigest()
    base["mode"] = 0o10000
    with pytest.raises(ValueError, match="mode is invalid"):
        file_ops.restore_file("sysctl", "sysctl:/etc/sysctl.d/x.conf", base, ())
    with pytest.raises(ValueError, match="outside /etc/sysctl.d"):
        sysctl_module._restore("sysctl:/etc/passwd", {"path": "/etc/passwd"}, ())
    with pytest.raises(ValueError, match="outside /etc/sysusers.d"):
        sysusers_module._restore("sysusers:/etc/passwd", {"path": "/etc/passwd"}, ())
    with pytest.raises(ValueError, match="outside /etc/tmpfiles.d"):
        tmpfiles_module._restore("tmpfiles:/etc/passwd", {"path": "/etc/passwd"}, ())
    with pytest.raises(ValueError, match="outside /etc/tmpfiles.d"):
        tmpfiles_module._restore(
            "tmpfiles:/etc/tmpfiles.d/bad name.conf",
            {"path": "/etc/tmpfiles.d/bad name.conf"},
            (),
        )


def test_file_helper_snapshots_verify_and_restore_contract(tmp_path):
    root = fake_root(tmp_path)
    path = "/etc/sysctl.d/60-test.conf"
    address = f"sysctl:{path}"
    with pytest.raises(ValueError, match="observation must be a mapping"):
        file_ops.snapshot(SimpleNamespace(resource=SimpleNamespace(path=path)), None)
    with pytest.raises(ValueError, match="no target image"):
        file_ops.snapshot(SimpleNamespace(resource=SimpleNamespace(path=path)), {"target": None})
    with pytest.raises(ValueError, match="no file path"):
        file_ops.snapshot(SimpleNamespace(resource=SimpleNamespace(path=None)), {"exists": False})
    assert file_ops.snapshot(SimpleNamespace(resource=SimpleNamespace(path=path)), {"exists": False}) == {
        "path": path, "exists": False, "sha256": None, "mode": None, "uid": None, "gid": None,
    }
    target = put(root, path, b"wanted\n")
    image = file_ops.observe_file(root, path)
    snapshot = file_ops.snapshot(SimpleNamespace(resource=SimpleNamespace(path=path)), {"target": image})
    assert snapshot["data_b64"] == image["data_b64"]
    staged = file_ops.StagedFile(path, image)
    ctx = SimpleNamespace(root=root, expected={address: staged})
    assert file_ops.verify_file(ctx, address, path, object(), "sysctl")[0].code == "sysctl.verify.expected"
    assert file_ops.verify_file(ctx, address, path, staged, "sysctl") == []
    mismatched = file_ops.StagedFile(path, file_ops.expected_image(b"different\n", image))
    assert file_ops.verify_file(ctx, address, path, mismatched, "sysctl")[0].code == "sysctl.verify.failed"

    restore = file_ops.restore_file("sysctl", address, {"path": path, "exists": False}, ())
    with pytest.raises(ValueError, match="restore observation must be a mapping"):
        restore.diff(None, {address: None})
    with pytest.raises(TypeError, match="requires a staged file image"):
        restore.commit(None, object())
    assert restore.verify(SimpleNamespace(root=root, expected={}))[0].code == "sysctl.verify.expected"

    bad_metadata = {"path": path, "exists": True, "mode": 0o644, "uid": True, "gid": 0, "data_b64": "YQ==", "sha256": hashlib.sha256(b"a").hexdigest()}
    with pytest.raises(ValueError, match="mode and ids"):
        file_ops.restore_file("sysctl", address, bad_metadata, ())
    missing_bytes = {"path": path, "exists": True, "mode": 0o644, "uid": 0, "gid": 0, "sha256": hashlib.sha256(b"a").hexdigest()}
    with pytest.raises(ValueError, match="has no file bytes"):
        file_ops.restore_file("sysctl", address, missing_bytes, ())


def test_sysctl_revert_restores_the_recorded_preimage_mode(tmp_path):
    root = fake_root(tmp_path)
    target = put(root, "/etc/sysctl.d/60-mode.conf", b"kernel.test = 0\n")
    target.chmod(0o640)
    resource = sysctl_module.Sysctl(
        "60-mode.conf", "present", (sysctl_module.SysctlEntry("kernel.test", "1"),), False, "authoritative"
    )
    applied = apply(plan(req(resource), root=root))
    assert target.stat().st_mode & 0o777 == 0o644
    revert(applied.txn, root=root)
    assert target.stat().st_mode & 0o777 == 0o640


@pytest.mark.parametrize("kind", ["sysctl", "sysusers", "tmpfiles"])
def test_resource_file_kinds_refuse_a_symlinked_target(kind, tmp_path):
    root = fake_root(tmp_path)
    put(root, "/etc/group", b"")
    put(root, "/etc/passwd", b"")
    if kind == "sysctl":
        path = "/etc/sysctl.d/60-link.conf"
        resource = sysctl_module.Sysctl(
            "60-link.conf", "present", (sysctl_module.SysctlEntry("kernel.test", "1"),), False, "fallback"
        )
    elif kind == "sysusers":
        path = "/etc/sysusers.d/60-link.conf"
        resource = Sysusers("60-link.conf", "present", (GroupSpec("svc", 430),), (), (), "fallback")
    else:
        path = "/etc/tmpfiles.d/60-link.conf"
        resource = Tmpfiles("60-link.conf", "present", "d /var/lib/example 0750 - - -\n", "fallback")
    target = put(root, path, b"old\n")
    target.unlink()
    target.symlink_to("/dev/null")
    with pytest.raises(PlanRefused) as failure:
        plan(req(resource), root=root)
    assert "symlink" in str(failure.value).lower()


def test_resource_file_commit_passes_recorded_owner_to_atomic_write(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    path = "/etc/sysctl.d/60-owner.conf"
    image = file_ops.expected_image(b"kernel.test = 1\n", {"exists": False})
    staged = file_ops.StagedFile(path, image)
    seen = {}
    expected_owner = (image["uid"], image["gid"])

    def capture_owner(_root, target, data, mode, **kwargs):
        assert kwargs == {"owner": expected_owner}
        seen["owner"] = kwargs["owner"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(mode)

    monkeypatch.setattr(file_ops._fs, "atomic_write", capture_owner)
    file_ops.commit_file(SimpleNamespace(root=root), path, staged)
    assert seen["owner"] == expected_owner


def test_tmpfiles_internal_validation_and_noop_paths():
    assert tmpfiles_module._parse_lines(b"x - - - - -\n") == (("-", "x - - - - -"),)
    assert tmpfiles_module._shadow_lines(b"x - - - - -\nd /var/lib/x 0755 - - -\n") == (
        ("/var/lib/x", "d /var/lib/x 0755 - - -"),
    )
    for value, message in (
        (None, "must be an array"),
        (["bad"], "entries must be"),
        ([("relative", "/var/lib/x")], "exact absolute file"),
        ([("/etc/x", "")], "exact absolute file"),
        ([("/etc/x", "key"), ("/etc/x", "key")], "duplicate accept_shadow"),
    ):
        with pytest.raises(ValueError, match=message):
            tmpfiles_module._accept_value(value)

    for args, message in (
        (("bad", "present", "d /var/lib/x 0755 - - -\n", "fallback"), "single .conf"),
        (("60-x.conf", "unknown", "d /var/lib/x 0755 - - -\n", "fallback"), "state must"),
        (("60-x.conf", "present", "d /var/lib/x 0755 - - -\n", "unknown"), "intent must"),
        (("60-x.conf", "present", "", "fallback"), "non-empty text"),
        (("60-x.conf", "absent", "text", "fallback"), "must not specify text"),
    ):
        with pytest.raises(ValueError, match=message):
            Tmpfiles(*args)

    absent = tmpfiles_module._parse({"file": "60-x.conf", "state": "absent", "intent": "fallback"})
    assert absent.state == "absent"
    with pytest.raises(ValueError, match="missing tmpfiles field"):
        tmpfiles_module._parse({"file": "60-x.conf", "state": "present", "intent": "fallback"})
    resource = Tmpfiles("60-x.conf", "present", "d /var/lib/x 0755 - - -\n", "fallback")
    with pytest.raises(ValueError, match="observation is invalid"):
        resource.diff(None, {resource.addresses()[0]: None})
    with pytest.raises(TypeError, match="requires a staged file"):
        resource.commit(None, object())
    assert Tmpfiles("60-x.conf", "absent", None, "fallback").post_actions([]) == []
    missing_absent = Tmpfiles("60-x.conf", "absent", None, "fallback")
    absent_address = missing_absent.addresses()[0]
    absent_change = missing_absent.diff(
        SimpleNamespace(), {absent_address: {"target": {"exists": False}}}
    )[0]
    assert absent_change.action == "noop"
    assert absent_change.details == ()
    tmpfiles_module._validate_staged(
        SimpleNamespace(), resource.path, file_ops.StagedFile(resource.path, {"exists": False}), {}, {}, []
    )


def test_tmpfiles_c_escapes_fields_staging_and_verify_error_paths(monkeypatch, tmp_path):
    assert tmpfiles_module._fields("") == ([], "")
    assert tmpfiles_module._c_unescape(r"\n", 0) == ("\n", 2)
    assert tmpfiles_module._c_unescape(r"\x41", 0) == ("A", 4)
    assert tmpfiles_module._c_unescape(r"\101", 0) == ("A", 4)
    with pytest.raises(ValueError, match="trailing backslash"):
        tmpfiles_module._c_unescape("\\", 0)
    with pytest.raises(ValueError, match="invalid hexadecimal"):
        tmpfiles_module._c_unescape(r"\xG", 0)
    with pytest.raises(ValueError, match="unknown C escape"):
        tmpfiles_module._c_unescape(r"\q", 0)
    with pytest.raises(ValueError, match="quote must enclose"):
        tmpfiles_module._fields("field'joined")
    with pytest.raises(ValueError, match="quoted field must be followed"):
        tmpfiles_module._fields('"field"joined')
    with pytest.raises(ValueError, match="at least a type and path"):
        tmpfiles_module._parse_lines(b"x\n")

    root = fake_root(tmp_path)
    staged_path = tmp_path / "stage" / "60-example.conf"
    staged_path.parent.mkdir()
    staged_path.write_text("d /var/lib/example 0750 svc svc -\n", encoding="utf-8")
    staged = file_ops.StagedFile(str(staged_path), {"exists": True})
    with pytest.raises(PlanRefused, match="missing /etc/group snapshot"):
        tmpfiles_module._validate_staged(SimpleNamespace(), "/etc/tmpfiles.d/60-example.conf", staged, {}, {}, [])
    before = {"group": {"exists": False}, "passwd": {"exists": False}}
    with pytest.raises(PlanRefused, match="planned group map is invalid"):
        tmpfiles_module._validate_staged(SimpleNamespace(), "/etc/tmpfiles.d/60-example.conf", staged, before, None, [])

    old_target = put(root, "/etc/tmpfiles.d/60-example.conf", b"d /var/lib/example 0700 - - -\n")
    put(root, "/etc/group", b"svc:x:430:\n")
    before = {
        "group": file_ops.observe_file(root, "/etc/group"),
        "passwd": file_ops.observe_file(root, "/etc/passwd"),
    }
    seen = []

    def record_staged_operand(argv, *, timeout):
        assert_runner_timeout(timeout)
        seen.append(tuple(argv))
        operand = Path(argv[-1])
        assert operand.is_relative_to(Path(argv[2]))
        assert operand.read_bytes() == staged_path.read_bytes()
        assert operand.read_bytes() != old_target.read_bytes()
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(shadow, "run_command", record_staged_operand)
    tmpfiles_module._validate_staged(SimpleNamespace(), "/etc/tmpfiles.d/60-example.conf", staged, before, {"svc": 430}, [])
    expected_conf = Path(staged_path).parent / "tmpfiles-root/etc/tmpfiles.d/60-example.conf"
    assert seen and seen[0][-1] == str(expected_conf)
    assert (Path(staged_path).parent / "tmpfiles-root/etc/group").read_text(encoding="utf-8") == "svc:x:430:\n"

    resource = Tmpfiles("60-verify.conf", "present", "d /var/lib/example 0750 - - -\n", "authoritative")
    monkeypatch.setattr(file_ops, "verify_file", lambda *_args: [])
    for failure, code in (
        (ValueError("bad layers"), "tmpfiles.shadow.verify"),
        (PlanRefused([Problem("shadow.earlier", "stale shadow")]), "shadow.earlier"),
    ):
        monkeypatch.setattr(tmpfiles_module, "observe_layers", lambda *_args, error=failure: (_ for _ in ()).throw(error))
        problems = resource.verify(SimpleNamespace(root=root, expected={}))
        assert problems[0].code == code
