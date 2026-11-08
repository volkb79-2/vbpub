"""Fake-root contract tests for sysusers, tmpfiles and endpoint resources."""

from __future__ import annotations

import base64
import hashlib
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from augeaswrap import (
    Endpoint,
    GroupRemoval,
    GroupSpec,
    PlanRefused,
    Request,
    StalePlan,
    Sysusers,
    Tmpfiles,
    apply,
    endpoints,
    journal,
    load_request,
    plan,
    revert,
)
from augeaswrap import _resource_files as file_ops
from augeaswrap import shadow, sysctl as sysctl_module, sysusers as sysusers_module, tmpfiles as tmpfiles_module


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


def canned_runner(monkeypatch, *, returncode: int = 0, stdout: str = "", stderr: str = ""):
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def run(argv, **kwargs):
        calls.append((tuple(argv), kwargs))
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr(shadow, "run_command", run)
    return calls


@pytest.fixture(autouse=True)
def no_host_systemd_commands(monkeypatch):
    monkeypatch.setattr(
        shadow,
        "run_command",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
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
        ("gpasswd", "-a", "alice", "svc"),
        ("gpasswd", "-d", "alice", "users"),
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


def test_sysusers_runner_failure_and_group_file_parsing_errors(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    canned_runner(monkeypatch, returncode=1, stderr="invalid row")
    with pytest.raises(PlanRefused, match="systemd-sysusers --root"):
        plan(req(Sysusers("60-bad.conf", "present", (GroupSpec("svc", 430),), (), (), "fallback")), root=root)
    monkeypatch.setattr(shadow, "run_command", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("runner unavailable")))
    with pytest.raises(PlanRefused, match="cannot run systemd-sysusers dry-run"):
        plan(req(Sysusers("60-error.conf", "present", (GroupSpec("svc", 430),), (), (), "fallback")), root=root)
    put(root, "/etc/group", b"invalid\n")
    with pytest.raises(PlanRefused, match="invalid /etc/group line"):
        plan(req(Sysusers("60-error.conf", "absent", (), (), (), "fallback")), root=root)


def test_sysusers_dry_run_contains_observed_layer_files(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    vendor = put(root, "/usr/lib/sysusers.d/10-vendor.conf", b"g vendor 700 -\n")
    calls = []

    def run(argv, **kwargs):
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
    assert record["actions"][0]["argv"] == ["groupdel", "staff"]
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


def test_tmpfiles_present_absent_idempotence_validation_and_revert(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    calls = canned_runner(monkeypatch)
    text = "# managed data\nd /var/lib/example 0750 svc svc -\nf /var/lib/example/state - svc svc - value\n"
    resource = Tmpfiles("60-example.conf", "present", text, "authoritative")
    planned = plan(req(resource), root=root)
    assert calls and calls[0][0][:4] == ("systemd-tmpfiles", "--root", calls[0][0][2], "--dry-run")
    action = planned.as_dict()["post_actions"][0]
    assert action["argv"] == ["systemd-tmpfiles", "--create", "/etc/tmpfiles.d/60-example.conf"]
    result = apply(planned)
    target = root / "etc/tmpfiles.d/60-example.conf"
    assert target.read_text(encoding="utf-8") == text
    assert not plan(req(resource), root=root).has_changes
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
    "d /var/lib/x 0755 - - - extra extra\n",
    "d /var/lib/x 0755 - - -\nd /var/lib/x 0755 - - -\n",
])
def test_tmpfiles_rejects_invalid_line_syntax(text):
    with pytest.raises(ValueError):
        Tmpfiles("60-bad.conf", "present", text, "authoritative")


@pytest.mark.parametrize("text", ["d /var/lib/x 0755 - - -\r\n", "# invalid\x00comment\n"])
def test_tmpfiles_rejects_control_characters(text):
    with pytest.raises(ValueError, match="NUL or carriage returns"):
        Tmpfiles("60-bad.conf", "present", text, "authoritative")


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
    monkeypatch.setattr(shadow, "run_command", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("runner unavailable")))
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
    order: list[str] = []
    original = endpoints.withdraw

    def withdraw(owner, *, root):
        order.append("withdraw")
        return original(owner, root=root)

    monkeypatch.setattr(endpoints, "withdraw", withdraw)

    class LaterResource:
        kind = "unit"

        def addresses(self):
            return ("unit:app.service",)

        def observe(self, ctx):
            return {"unit:app.service": {"present": False}}

        def diff(self, ctx, observed):
            from augeaswrap import Change
            return [Change(self.kind, "unit:app.service", "change", observed["unit:app.service"], {"present": True}, self)]

        def stage(self, ctx, change):
            return change.after

        def commit(self, ctx, staged):
            order.append("unit-remove")
            return staged

        def post_actions(self, changes):
            return []

        def verify(self, ctx):
            return []

    apply(plan(req(Endpoint("example", "absent"), LaterResource()), root=root))
    assert order == ["withdraw", "unit-remove"]


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
    with pytest.raises(PlanRefused) as failure:
        plan(req(Endpoint("bad", "present", (("BAD_VALUE", "@gid:"),))), root=root)
    assert failure.value.problems[0].code == "endpoint.gid.invalid"

    vendor_root = fake_root(tmp_path / "vendor")
    put(vendor_root, "/usr/lib/sysusers.d/20-vendor.conf", b"g app 431 -\n")
    fallback = Sysusers("60-site.conf", "present", (GroupSpec("app", 430),), (), (), "fallback")
    resolved = plan(req(fallback, Endpoint("example", "present", (("APP_GID", "@gid:app"),))), root=vendor_root)
    assert resolved.changes[1].after["entries"]["APP_GID"] == "431"

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
    ctx = SimpleNamespace(root=Path("/"), expected={resource.addresses()[0]: object()})
    assert resource.verify(ctx) == []

    sysusers_resource = Sysusers("60-verify.conf", "present", (GroupSpec("svc", 430),), (), (), "fallback")
    monkeypatch.setattr(sysusers_module.file_ops, "verify_file", lambda *_args: [])
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


def test_tmpfiles_internal_validation_and_noop_paths():
    assert tmpfiles_module._parse_lines(b"x - - - - -\n") == (("-", "x - - - - -"),)
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
    tmpfiles_module._validate_staged(SimpleNamespace(), resource.path, file_ops.StagedFile(resource.path, {"exists": False}))
