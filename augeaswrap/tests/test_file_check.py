from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from augeaswrap import ApplyFailed, Change, PlanRefused, Request
from augeaswrap.check import Check, PathMode
from augeaswrap.engine import apply, plan
from augeaswrap.file import File
from augeaswrap import check as check_module
from augeaswrap import file as file_module


def fake_root(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    root = tmp_path / "root"
    root.mkdir()
    return root


def test_file_present_absent_create_only_and_metadata(tmp_path):
    root = fake_root(tmp_path)
    target = root / "etc/app.conf"
    target.parent.mkdir()
    target.write_text("operator value", encoding="utf-8")
    target.chmod(0o600)
    before = target.stat().st_mtime_ns

    create_only = File("/etc/app.conf", "present", "tool value", "0644", create_only=True)
    planned = plan(Request("create only", (create_only,)), root=root)
    assert not planned.has_changes
    assert "preserved-existing-file" in planned.changes[0].details
    assert apply(planned).txn is None
    assert target.read_text(encoding="utf-8") == "operator value"
    assert target.stat().st_mtime_ns == before
    with pytest.raises(ValueError, match="create_only cannot be used with absent"):
        File("/etc/app.conf", "absent", create_only=True)

    replaced = File(
        "/etc/app.conf",
        "present",
        "new value",
        "0640",
        owner=os.geteuid(),
        group=os.getegid(),
    )
    result = apply(plan(Request("replace", (replaced,)), root=root))
    assert result.txn
    assert target.read_text(encoding="utf-8") == "new value"
    assert target.stat().st_mode & 0o7777 == 0o640
    assert target.stat().st_uid == os.geteuid()
    assert target.stat().st_gid == os.getegid()

    deleted = File("/etc/app.conf", "absent")
    assert plan(Request("delete", (deleted,)), root=root).has_changes
    apply(plan(Request("delete", (deleted,)), root=root))
    assert not target.exists()
    absent_again = plan(Request("delete", (deleted,)), root=root)
    assert not absent_again.has_changes


def test_file_path_allowlist_normalization_and_constructor_boundaries():
    for path in ("relative", "/", "/opt/private/file", "/etc/../root/file", "/etc//duplicate", "/etc/file\x00"):
        with pytest.raises(ValueError):
            File(path, "present", "x", "0644")
    for mode in (True, -1, 0o10000, "999", "64", None):
        with pytest.raises(ValueError):
            File("/etc/app.conf", "present", "x", mode)
    assert File("/etc/zero.conf", "present", "x", 0).mode == 0
    assert File("/etc/full-mode.conf", "present", "x", 0o7777).mode == 0o7777
    with pytest.raises(ValueError, match="state"):
        File("/etc/app.conf", "unknown")
    with pytest.raises(ValueError, match="text is required"):
        File("/etc/app.conf", "present", mode="0644")
    with pytest.raises(ValueError, match="absent file must not specify"):
        File("/etc/app.conf", "absent", text="x")
    with pytest.raises(ValueError, match="mode"):
        File("/etc/app.conf", "present", "x")
    with pytest.raises(ValueError, match="create_only cannot be used with absent"):
        File("/etc/app.conf", "absent", create_only=True)
    with pytest.raises(ValueError, match="validate cannot be used with absent"):
        File("/etc/app.conf", "absent", validate="json")
    with pytest.raises(ValueError, match="create_only must be a boolean"):
        File("/etc/app.conf", "present", "x", "0644", create_only=1)
    with pytest.raises(ValueError, match="owner must be a name or numeric id"):
        File("/etc/app.conf", "present", "x", "0644", owner=True)
    with pytest.raises(ValueError, match="group id must be non-negative"):
        File("/etc/app.conf", "present", "x", "0644", group=-1)


def test_file_refuses_symlinks_in_target_and_parent_components(tmp_path):
    root = fake_root(tmp_path)
    real_dir = root / "real"
    real_dir.mkdir()
    (real_dir / "target.conf").write_text("outside", encoding="utf-8")
    (root / "etc").mkdir()
    (root / "etc/link").symlink_to(real_dir, target_is_directory=True)
    request = Request("symlink", (File("/etc/link/target.conf", "present", "bad", "0644"),))
    with pytest.raises(PlanRefused, match="symlink"):
        plan(request, root=root)

    target = root / "etc/target.conf"
    target.symlink_to(real_dir / "target.conf")
    with pytest.raises(PlanRefused, match="symlink"):
        plan(Request("symlink target", (File("/etc/target.conf", "absent"),)), root=root)


def test_atomic_commit_failure_leaves_the_original_file_intact(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    target = root / "etc/app.conf"
    target.parent.mkdir()
    target.write_bytes(b"original\n")
    staged_plan = plan(Request("atomic", (File("/etc/app.conf", "present", "replacement\n", "0600"),)), root=root)

    original_replace = file_module._fs._replace

    def fail_replace(temporary, destination):
        if Path(destination) == target:
            raise OSError("rename interrupted")
        return original_replace(temporary, destination)

    monkeypatch.setattr(file_module._fs, "_replace", fail_replace)
    with pytest.raises(ApplyFailed, match="rename interrupted"):
        apply(staged_plan)
    assert target.read_bytes() == b"original\n"
    assert not list(target.parent.glob(".augeaswrap-file-*"))


def test_json_and_toml_validators_run_on_staged_content(tmp_path):
    root = fake_root(tmp_path)
    valid_json = File("/etc/data.json", "present", '{"a": 1}\n', "0644", validate="json")
    assert plan(Request("json", (valid_json,)), root=root).has_changes
    duplicate = File("/etc/duplicate.json", "present", '{"a": 1, "a": 2}', "0644", validate="json")
    with pytest.raises(PlanRefused, match="duplicate JSON key"):
        plan(Request("duplicate", (duplicate,)), root=root)
    valid_toml = File("/etc/data.toml", "present", "answer = 42\n", "0644", validate="toml")
    assert plan(Request("toml", (valid_toml,)), root=root).has_changes
    invalid_toml = File("/etc/invalid.toml", "present", "answer = [\n", "0644", validate="toml")
    with pytest.raises(PlanRefused, match="validation failed"):
        plan(Request("invalid toml", (invalid_toml,)), root=root)


def test_validator_spec_boundaries_and_custom_argv_placeholder(tmp_path, monkeypatch):
    for spec in ("findmnt", "", ["python3"], ["cmd"], ["cmd", ""], ["cmd", "x%s%s"], ["unknown"]):
        with pytest.raises(ValueError, match="validate"):
            File("/etc/test.conf", "present", "x", "0644", validate=spec)
    with pytest.raises(ValueError, match="at most one"):
        File("/etc/test.conf", "present", "x", "0644", validate=["cmd", "check", "%s", "%s"])
    with pytest.raises(ValueError, match="validate must"):
        File("/etc/test.conf", "present", "x", "0644", validate=["unknown", "validator"])

    root = fake_root(tmp_path)
    observed = []

    staged = []

    def success(argv, **kwargs):
        observed.append((argv, kwargs))
        staged.append(Path(argv[-1].split("=", 1)[-1]).is_file())
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(file_module.subprocess, "run", success)
    command = File("/etc/test.conf", "present", "ok", "0644", validate=["cmd", "custom-validator", "--file=%s"])
    assert command.validate == ("cmd", "custom-validator", "--file=%s")
    plan(Request("custom validator", (command,)), root=root)
    argv, kwargs = observed[0]
    assert argv[0] == "custom-validator"
    assert argv[1].startswith("--file=")
    assert kwargs["timeout"] == 30

    no_placeholder = File("/etc/no-placeholder.conf", "present", "ok", "0644", validate=["cmd", "custom-validator"])
    plan(Request("custom validator", (no_placeholder,)), root=root)
    # The staged file exists while the validator runs.
    assert staged == [True, True]


def test_real_custom_validator_failure_reports_status_and_decoded_stderr(tmp_path):
    root = fake_root(tmp_path)
    code = "import sys; sys.stdin.read(); sys.stderr.write('invalid config\\n'); raise SystemExit(3)"
    resource = File(
        "/etc/custom.conf",
        "present",
        "invalid",
        "0644",
        validate=["cmd", sys.executable, "-c", code, "%s"],
    )

    with pytest.raises(PlanRefused) as failure:
        plan(Request("real custom validator", (resource,)), root=root)

    assert failure.value.problems[0].message.endswith("validator exited 3: invalid config")


def test_validator_command_timeout_nonzero_and_missing_command_refuse(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    command = File("/etc/test.conf", "present", "ok", "0644", validate=["cmd", "validator", "%s"])
    monkeypatch.setattr(file_module.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("missing")))
    with pytest.raises(PlanRefused, match="validator failed"):
        plan(Request("missing validator", (command,)), root=root)

    monkeypatch.setattr(file_module.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=3, stdout="", stderr="invalid config\n"))
    with pytest.raises(PlanRefused, match="validator exited 3: invalid config"):
        plan(Request("bad validator", (command,)), root=root)

    timeout = subprocess.TimeoutExpired("validator", 30)
    monkeypatch.setattr(file_module.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(timeout))
    with pytest.raises(PlanRefused, match="validator failed"):
        plan(Request("slow validator", (command,)), root=root)


def test_external_builtin_validators_use_staged_file_and_report_diagnostics(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    calls = []

    staged = []

    def success(argv, **kwargs):
        calls.append((tuple(argv), kwargs))
        staged.append(Path(argv[-1]).is_file())
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(file_module.subprocess, "run", success)
    for index, validator in enumerate(("udev", "sudoers", "sshd")):
        resource = File(f"/etc/rule-{index}.conf", "present", "valid", "0644", validate=validator)
        plan(Request(f"{validator} validator", (resource,)), root=root)
    assert [call[0][0] for call in calls] == ["udevadm", "visudo", "sshd"]
    # The staged file exists while the validator runs and is removed afterwards.
    assert staged == [True, True, True]
    assert not any(Path(call[0][-1]).exists() for call in calls)

    monkeypatch.setattr(file_module.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="bad output", stderr=""))
    with pytest.raises(PlanRefused, match="exited 1: bad output"):
        plan(Request("bad external validator", (File("/etc/sshd.conf", "present", "x", "0644", validate="sshd"),)), root=root)
    monkeypatch.setattr(file_module.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""))
    with pytest.raises(PlanRefused, match="no diagnostic"):
        plan(Request("bad external validator", (File("/etc/sshd.conf", "present", "x", "0644", validate="sshd"),)), root=root)


def test_file_resolve_ids_and_snapshot_helpers(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    numeric = File("/etc/ids.conf", "present", "ids", "0644", owner=0, group=0)
    planned = plan(Request("ids", (numeric,)), root=root)
    assert planned.has_changes

    monkeypatch.setattr(file_module.pwd, "getpwnam", lambda name: (_ for _ in ()).throw(KeyError(name)))
    with pytest.raises(PlanRefused, match="unknown user"):
        plan(Request("unknown user", (File("/etc/user.conf", "present", "x", "0644", owner="no-such-user"),)), root=root)
    monkeypatch.setattr(file_module.grp, "getgrnam", lambda name: (_ for _ in ()).throw(KeyError(name)))
    with pytest.raises(PlanRefused, match="unknown group"):
        plan(Request("unknown group", (File("/etc/group.conf", "present", "x", "0644", group="no-such-group"),)), root=root)

    assert file_module._public_image({"exists": False}) == {"exists": False}
    assert file_module._decode_value({"data_b64": "eA=="}) == b"x"
    with pytest.raises(ValueError, match="no content"):
        file_module._decode_value({})
    with pytest.raises(ValueError):
        file_module._decode_value({"data_b64": "not-base64"})
    with pytest.raises(ValueError):
        file_module._decode_value({"data_b64": "Y Q=="})


def test_check_exec_path_mode_and_absent_guards_are_read_only(tmp_path):
    root = fake_root(tmp_path)
    command = Check("present", exec=("/bin/true",), expect_exit=0, timeout=1)
    planned = plan(Request("read-only exec", (command,)), root=root)
    assert not planned.has_changes
    assert apply(planned).txn is None
    assert not (root / "var/lib/augeaswrap/txn").exists()

    target = root / "etc/owned.conf"
    target.parent.mkdir()
    target.write_text("mode", encoding="utf-8")
    target.chmod(0o640)
    path_check = Check(
        "present",
        path_mode=PathMode("/etc/owned.conf", os.geteuid(), os.getegid(), "0640", "must-match"),
    )
    assert not plan(Request("path mode", (path_check,)), root=root).has_changes
    missing_check = Check(
        "present",
        path_mode=PathMode("/etc/missing.conf", 0, 0, "0644", "absent"),
    )
    assert not plan(Request("absent path", (missing_check,)), root=root).has_changes

    disabled = Check("absent", exec=("/definitely/not/run",), expect_exit=0, timeout=1)
    assert not plan(Request("disabled check", (disabled,)), root=root).has_changes
    skipped = disabled.observe(SimpleNamespace(root=root))[disabled.addresses()[0]]
    assert skipped["skipped"] is True


def test_check_path_mode_resolves_named_owner_and_group_from_their_databases(monkeypatch):
    def lookup_user(name):
        if name != "alice":
            raise KeyError(name)
        return SimpleNamespace(pw_uid=41)

    def lookup_group(name):
        if name != "staff":
            raise KeyError(name)
        return SimpleNamespace(gr_gid=42)

    monkeypatch.setattr(check_module.pwd, "getpwnam", lookup_user)
    monkeypatch.setattr(check_module.grp, "getgrnam", lookup_group)

    expected = PathMode("/etc/owned.conf", "alice", "staff", "0640", "must-match")

    resolved = (
        check_module._resolve_id(expected.owner, group=False),
        check_module._resolve_id(expected.group, group=True),
    )
    assert resolved == (41, 42)


@pytest.mark.parametrize("field", ["owner", "group", "mode"])
def test_path_mode_refuses_each_independent_metadata_mismatch(tmp_path, field):
    root = fake_root(tmp_path)
    target = root / "etc/metadata.conf"
    target.parent.mkdir()
    target.write_text("managed", encoding="utf-8")
    target.chmod(0o640)
    owner, group, mode = os.geteuid(), os.getegid(), "0640"
    if field == "owner":
        owner += 1
    elif field == "group":
        group += 1
    else:
        mode = "0600"
    check = Check("present", path_mode=PathMode("/etc/metadata.conf", owner, group, mode, "must-match"))

    with pytest.raises(PlanRefused, match="differs"):
        plan(Request("metadata mismatch", (check,)), root=root)


def test_failed_exec_uses_decoded_stderr_when_stdout_is_empty(tmp_path):
    root = fake_root(tmp_path)
    command = Check(
        "present",
        exec=(sys.executable, "-c", "import sys; sys.stderr.write('stderr detail\\n'); raise SystemExit(1)"),
        expect_exit=0,
        timeout=2,
    )

    with pytest.raises(PlanRefused) as failure:
        plan(Request("stderr diagnostic", (command,)), root=root)

    assert failure.value.problems[0].message.endswith(f"{sys.executable}: stderr detail")


def test_check_diff_exposes_observation_fields_but_not_fingerprint():
    check = Check("present", exec=("/bin/true",), expect_exit=0, timeout=1)
    address = check.addresses()[0]

    changes = check.diff(None, {address: {"argv": ["/bin/true"], "returncode": 0, "fingerprint": "private"}})

    assert len(changes) == 1
    assert changes[0].before == {"argv": ["/bin/true"], "returncode": 0}
    assert changes[0].after == {"argv": ["/bin/true"], "returncode": 0}


def test_check_failures_timeout_path_mode_mismatch_and_symlink_are_structured(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    failed_command = Check("present", exec=("/bin/false",), expect_exit=0, timeout=1)
    with pytest.raises(PlanRefused, match="exited 1"):
        plan(Request("failed exec", (failed_command,)), root=root)

    no_diagnostic = Check("present", exec=("/bin/true",), expect_exit=1, timeout=1)
    with pytest.raises(PlanRefused, match="no diagnostic"):
        plan(Request("exit mismatch", (no_diagnostic,)), root=root)

    timed = Check("present", exec=("slow",), expect_exit=0, timeout=0.1)
    monkeypatch.setattr(check_module.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired("slow", 0.1)))
    with pytest.raises(PlanRefused, match="timed out"):
        plan(Request("timeout", (timed,)), root=root)

    info = Check("present", exec=("missing",), expect_exit=0, timeout=1)
    monkeypatch.setattr(check_module.subprocess, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("not found")))
    with pytest.raises(PlanRefused, match="cannot run check command"):
        plan(Request("missing command", (info,)), root=root)

    target = root / "etc/file"
    target.parent.mkdir()
    target.write_text("x", encoding="utf-8")
    target.chmod(0o600)
    mismatch = Check("present", path_mode=PathMode("/etc/file", 0, 0, "0644", "must-match"))
    with pytest.raises(PlanRefused, match="differs"):
        plan(Request("metadata mismatch", (mismatch,)), root=root)
    existing_absent = Check("present", path_mode=PathMode("/etc/file", 0, 0, "0644", "absent"))
    with pytest.raises(PlanRefused, match="expected /etc/file to be absent"):
        plan(Request("unexpected path", (existing_absent,)), root=root)

    real = root / "etc/real"
    real.write_text("x", encoding="utf-8")
    (root / "etc/link").symlink_to(real)
    symlink_check = Check("present", path_mode=PathMode("/etc/link", 0, 0, "0644", "must-match"))
    with pytest.raises(PlanRefused, match="symlink"):
        plan(Request("symlink path", (symlink_check,)), root=root)


def test_check_construction_and_verification_boundaries(tmp_path, monkeypatch):
    for kwargs in (
        {"state": "invalid", "exec": ("/bin/true",), "expect_exit": 0, "timeout": 1},
        {"state": "present"},
        {"state": "present", "exec": ("/bin/true",), "path_mode": PathMode("/etc/a", 0, 0, "0644", "must-match"), "expect_exit": 0, "timeout": 1},
        {"state": "present", "exec": (), "expect_exit": 0, "timeout": 1},
        {"state": "present", "exec": ("",), "expect_exit": 0, "timeout": 1},
        {"state": "present", "exec": ("/bin/true",), "timeout": 1},
        {"state": "present", "exec": ("/bin/true",), "expect_exit": True, "timeout": 1},
        {"state": "present", "exec": ("/bin/true",), "expect_exit": 0, "timeout": float("inf")},
        {"state": "present", "exec": ("/bin/true",), "expect_exit": 0, "timeout": 0},
        {"state": "present", "exec": ("/bin/true",), "expect_exit": 0, "timeout": True},
        {"state": "present", "path_mode": {"path": "/etc/a", "owner": 0}},
        {"state": "present", "path_mode": {"path": "/etc/a", "owner": 0, "group": 0, "mode": "0644", "if_exists": "must-match", "extra": 1}},
        {"state": "present", "path_mode": "bad"},
        {"state": "present", "path_mode": PathMode("/etc/a", 0, 0, "0644", "must-match"), "expect_exit": 0},
    ):
        with pytest.raises(ValueError):
            Check(**kwargs)

    for values in (
        ("relative", 0, 0, "0644", "must-match"),
        ("/", 0, 0, "0644", "must-match"),
        ("/etc/../bad", 0, 0, "0644", "must-match"),
        ("/etc/a", -1, 0, "0644", "must-match"),
        ("/etc/a", 0, 0, "0644", "maybe"),
        ("/etc/a", "missing-user-augeaswrap", 0, "0644", "must-match"),
    ):
        with pytest.raises(ValueError):
            PathMode(*values)

    root = fake_root(tmp_path)
    good = Check("present", exec=("/bin/true",), expect_exit=0, timeout=1)
    assert good.verify(SimpleNamespace(root=root)) == []
    bad = Check("present", exec=("/bin/false",), expect_exit=0, timeout=1)
    assert bad.verify(SimpleNamespace(root=root))[0].code == "check.exec.exit"
    skipped = Check("absent", exec=("/bin/false",), expect_exit=0, timeout=1)
    assert skipped.stage(None, None) is None
    assert skipped.commit(None, None) is None
    assert skipped.post_actions([]) == []


def test_path_mode_identity_boundaries_and_mapping_form(tmp_path):
    for owner in (1.5, ""):
        with pytest.raises(ValueError, match="owner and group"):
            PathMode("/etc/a", owner, 0, "0644", "must-match")
    mapped = Check(
        "present",
        path_mode={"path": "/etc/a", "owner": 0, "group": 0, "mode": "0644", "if_exists": "must-match"},
    )
    assert mapped.path_mode == PathMode("/etc/a", 0, 0, "0644", "must-match")
    with pytest.raises(ValueError, match="must be a mapping"):
        mapped.diff(None, {mapped.addresses()[0]: "text"})

    root = fake_root(tmp_path)
    with pytest.raises(PlanRefused, match="to exist"):
        plan(Request("w", (mapped,)), root=root)
    absent_ok = Check("present", path_mode=PathMode("/etc/a", 0, 0, "0644", "absent"))
    assert not plan(Request("w", (absent_ok,)), root=root).has_changes


def test_path_mode_reports_inspection_errors(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    original = check_module._fs.safe_components

    def deny(root_arg, path):
        if path.name == "denied":
            raise PermissionError("denied")
        return original(root_arg, path)

    monkeypatch.setattr(check_module._fs, "safe_components", deny)
    check = Check("present", path_mode=PathMode("/etc/denied", 0, 0, "0644", "must-match"))
    with pytest.raises(PlanRefused, match="cannot inspect /etc/denied"):
        plan(Request("w", (check,)), root=root)


def test_file_resource_rejects_misuse_directly(tmp_path):
    with pytest.raises(ValueError, match="non-empty name"):
        File("/etc/a.conf", "present", "x", "0644", owner="")
    resource = File("/etc/a.conf", "present", "x", "0644")
    address = resource.addresses()[0]
    with pytest.raises(ValueError, match="must be a mapping"):
        resource.diff(None, {address: "text"})
    with pytest.raises(TypeError, match="requires its staged image"):
        resource.commit(SimpleNamespace(root=fake_root(tmp_path)), object())
    assert resource.verify(SimpleNamespace(root=tmp_path, expected={}))[0].code == "file.verify.expected"


def test_file_verify_reports_a_post_image_that_differs_from_the_staged_image(tmp_path):
    root = fake_root(tmp_path)
    resource = File("/etc/a.conf", "present", "x", "0644")
    planned = plan(Request("w", (resource,)), root=root)
    change = planned.changes[0]
    staging = tmp_path / "stage"
    staging.mkdir()
    context = SimpleNamespace(root=root, stage_dir=staging, expected={})
    context.expected[change.address] = resource.stage(context, change)
    # Nothing was committed, so the file is still absent and differs from the staged image.
    assert resource.verify(context)[0].code == "file.verify.failed"
    resource.commit(context, context.expected[change.address])
    assert resource.verify(context) == []


def test_stage_removes_its_private_copy_when_writing_fails(tmp_path, monkeypatch):
    root = fake_root(tmp_path)
    stage_dir = tmp_path / "external-stage" / "nested"
    resource = File("/etc/a.conf", "present", "x", "0644")
    address = resource.addresses()[0]
    staged_path = stage_dir / hashlib.sha256(address.encode("utf-8")).hexdigest() / "a.conf"
    descriptors = []

    def broken(descriptor, _mode):
        descriptors.append(descriptor)
        Path(os.readlink(f"/proc/self/fd/{descriptor}")).unlink()
        raise OSError("fchmod failed")

    monkeypatch.setattr(file_module.os, "fchmod", broken)
    change = Change("file", address, "change", {}, {"exists": True, "mode": 0o644, "uid": 0, "gid": 0}, resource)
    context = SimpleNamespace(root=root, stage_dir=stage_dir)
    with pytest.raises(OSError, match="fchmod failed"):
        resource.stage(context, change)

    assert not staged_path.exists()
    assert len(descriptors) == 1
    with pytest.raises(OSError):
        os.fstat(descriptors[0])


@pytest.mark.parametrize("existing_address_dir", [False, True])
def test_file_external_stage_creates_or_reuses_address_directory(tmp_path, existing_address_dir):
    root = fake_root(tmp_path / "root-tree")
    stage_dir = tmp_path / "external-stage" / "deep" / "nested"
    resource = File("/etc/a.conf", "present", "staged bytes", "0640")
    address = resource.addresses()[0]
    address_dir = stage_dir / hashlib.sha256(address.encode("utf-8")).hexdigest()
    if existing_address_dir:
        address_dir.mkdir(parents=True)
    change = Change("file", address, "change", {}, {"exists": True, "mode": 0o640, "uid": 0, "gid": 0}, resource)

    staged = resource.stage(SimpleNamespace(root=root, stage_dir=stage_dir), change)

    assert Path(staged.path).read_bytes() == b"staged bytes"
    assert Path(staged.path).stat().st_mode & 0o7777 == 0o640


@pytest.mark.parametrize("existing_address_dir", [False, True])
def test_file_restore_external_stage_creates_or_reuses_address_directory(tmp_path, existing_address_dir):
    root = fake_root(tmp_path / "root-tree")
    stage_dir = tmp_path / "external-stage" / "deep" / "nested"
    raw = b"restored bytes"
    record = {
        "path": "/etc/a.conf",
        "exists": True,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "data_b64": base64.b64encode(raw).decode("ascii"),
        "mode": 0o600,
        "uid": 0,
        "gid": 0,
    }
    restored = file_module._file_restore("file:/etc/a.conf", record, ())
    address = restored.addresses()[0]
    address_dir = stage_dir / hashlib.sha256(address.encode("utf-8")).hexdigest()
    if existing_address_dir:
        address_dir.mkdir(parents=True)

    staged = restored.stage(SimpleNamespace(root=root, stage_dir=stage_dir), object())

    assert staged.data == raw
    assert (address_dir / "a.conf").read_bytes() == raw


@pytest.mark.parametrize("missing_field", ["data", "mode", "uid", "gid"])
def test_file_commit_refuses_each_incomplete_staged_field(tmp_path, missing_field):
    root = fake_root(tmp_path)
    target = root / "etc/a.conf"
    target.parent.mkdir()
    target.write_bytes(b"original")
    values = {"data": b"new", "mode": 0o600, "uid": 0, "gid": 0}
    values[missing_field] = None
    staged = file_module._StagedFile("/tmp/staged", True, values["data"], values["mode"], values["uid"], values["gid"], {"exists": True})

    with pytest.raises(AssertionError):
        File("/etc/a.conf", "present", "new", "0600").commit(SimpleNamespace(root=root), staged)

    assert target.read_bytes() == b"original"


@pytest.mark.parametrize("missing_field", ["data", "mode", "uid", "gid"])
def test_file_restore_commit_refuses_each_incomplete_staged_field(tmp_path, missing_field):
    root = fake_root(tmp_path)
    target = root / "etc/a.conf"
    target.parent.mkdir()
    target.write_bytes(b"original")
    raw = b"restored"
    record = {
        "path": "/etc/a.conf",
        "exists": True,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "data_b64": base64.b64encode(raw).decode("ascii"),
        "mode": 0o600,
        "uid": 0,
        "gid": 0,
    }
    restore = file_module._file_restore("file:/etc/a.conf", record, ())
    values = {"data": raw, "mode": 0o600, "uid": 0, "gid": 0}
    values[missing_field] = None
    staged = file_module._RestoredImage(True, values["data"], values["mode"], values["uid"], values["gid"], record)

    with pytest.raises(AssertionError):
        restore.commit(SimpleNamespace(root=root), staged)

    assert target.read_bytes() == b"original"


def test_file_restore_metadata_accepts_zero_and_maximum_mode():
    raw = b"pre-image"
    record = {
        "path": "/etc/a.conf",
        "exists": True,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "data_b64": base64.b64encode(raw).decode("ascii"),
        "mode": 0,
        "uid": 0,
        "gid": 0,
    }

    assert file_module._file_restore("file:/etc/a.conf", record, ()).image["mode"] == 0
    record["mode"] = 0o7777
    assert file_module._file_restore("file:/etc/a.conf", record, ()).image["mode"] == 0o7777


def test_file_diff_state_details_and_plan_render_preserve_crlf():
    present = File("/etc/a.conf", "present", "new\r\n", "0644")
    address = present.addresses()[0]
    missing = present.diff(None, {address: {"exists": False}})[0]
    assert missing.action == "change"
    assert missing.details == ()

    absent = File("/etc/a.conf", "absent")
    deleted = absent.diff(None, {address: {"exists": True, "sha256": "old", "mode": 0o644, "uid": 0, "gid": 0}})[0]
    assert deleted.action == "change"
    assert deleted.details == ("state=absent",)

    old = base64.b64encode(b"old\r\n").decode("ascii")
    rendered = file_module._render_file(
        Change("file", address, "change", {}, {}, present),
        SimpleNamespace(observed={address: {"exists": True, "data_b64": old}}),
    )
    assert "-old\r" in rendered
    assert "+new\r" in rendered
