"""The sysctl kind's TOML, shadow, whole-file, live-check and revert behavior."""

from __future__ import annotations

import base64
import subprocess
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from augeaswrap import PlanRefused, Problem, Request, StalePlan, Sysctl, SysctlEntry, apply, journal, load_request, plan, revert
from augeaswrap import _resource_files as file_ops
from augeaswrap import shadow, sysctl
from augeaswrap.shadow import TargetClass


def fake_root(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    root.parent.mkdir(parents=True, exist_ok=True)
    root.mkdir()
    return root


def put(root: Path, path: str, data: bytes) -> Path:
    target = root / path.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def request(resource: Sysctl) -> Request:
    return Request("test sysctl 1.0", (resource,))


def canned_runner(monkeypatch, *, result=None, record=None):
    result = result or SimpleNamespace(returncode=0, stdout="", stderr="")
    calls = []

    def run(argv, *, timeout):
        assert timeout == 30.0
        calls.append((tuple(argv), {"timeout": timeout}))
        return result

    monkeypatch.setattr(shadow, "run_command", run)
    if record is not None:
        record.extend(calls)
    return calls


def test_present_is_sorted_idempotent_journaled_and_revertible(tmp_path):
    root = fake_root(tmp_path)
    resource = Sysctl(
        "60-example.conf",
        "present",
        (SysctlEntry("vm.zeta", "2"), SysctlEntry("kernel.alpha", "1", "example setting")),
        False,
        "authoritative",
    )
    planned = plan(request(resource), root=root)
    assert planned.has_changes
    assert not (root / "etc/sysctl.d/60-example.conf").exists()
    result = apply(planned)
    target = root / "etc/sysctl.d/60-example.conf"
    assert target.read_text(encoding="utf-8") == "# example setting\nkernel.alpha = 1\nvm.zeta = 2\n"
    assert not plan(request(resource), root=root).has_changes
    assert revert(result.txn, root=root).txn
    assert not target.exists()


def test_absent_deletes_only_the_file_and_revert_restores_its_bytes(tmp_path):
    root = fake_root(tmp_path)
    target = put(root, "/etc/sysctl.d/40-old.conf", b"kernel.test = 1\n")
    resource = Sysctl("40-old.conf", "absent", (), False, "authoritative")
    planned = plan(request(resource), root=root)
    assert "live kernel value unchanged" in planned.changes[0].details[0]
    result = apply(planned)
    assert not target.exists()
    revert(result.txn, root=root)
    assert target.read_bytes() == b"kernel.test = 1\n"


def test_apply_now_is_explicit_and_plans_sysctl_apply_then_readback(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    calls = canned_runner(monkeypatch)
    resource = Sysctl("70-now.conf", "present", (SysctlEntry("kernel.test", "1"),), True, "authoritative")
    planned = plan(request(resource), root=root)
    action = planned.as_dict()["post_actions"][0]
    assert action["argv"] == ["sysctl", "-p", "/etc/sysctl.d/70-now.conf"]
    assert calls == []  # fake roots never run live sysctl commands
    assert not Sysctl("70-now.conf", "present", (SysctlEntry("kernel.test", "1"),), False, "fallback").post_actions([])
    applied = apply(planned)
    assert plan(request(resource), root=root).has_changes  # explicit apply_now is an action, not a file-only noop
    assert journal(applied.txn, root=root)["irreversible_effects"] == [
        {"address": "sysctl:/etc/sysctl.d/70-now.conf", "effect": "apply_now changes live kernel values; revert restores only the fragment"}
    ]
    reverted = revert(applied.txn, root=root)
    assert journal(reverted.txn, root=root)["actions"] == []
    with pytest.raises(ValueError, match="must not have entries or apply_now"):
        Sysctl("70-now.conf", "absent", (), True, "authoritative")


def test_shadow_intent_errors_notes_and_exact_acceptance(tmp_path):
    root = fake_root(tmp_path)
    put(root, "/usr/lib/sysctl.d/90-vendor.conf", b"kernel.test = 0\n")
    resource = Sysctl("60-site.conf", "present", (SysctlEntry("kernel.test", "1"),), False, "authoritative")
    with pytest.raises(PlanRefused) as failure:
        plan(request(resource), root=root)
    assert failure.value.problems[0].code == "shadow.later"

    accepted = Sysctl(
        "60-site.conf", "present", (SysctlEntry("kernel.test", "1"),), False, "authoritative",
        (("/usr/lib/sysctl.d/90-vendor.conf", "kernel.test"),),
    )
    assert any("note shadow.later" in detail for detail in plan(request(accepted), root=root).changes[0].details)

    equal_root = fake_root(tmp_path / "equal")
    put(equal_root, "/usr/lib/sysctl.d/10-vendor.conf", b"kernel.test=1\n")
    equal = Sysctl("60-site.conf", "present", (SysctlEntry("kernel.test", "1"),), False, "authoritative")
    assert any("equal value" in detail for detail in plan(request(equal), root=equal_root).changes[0].details)

    fallback = Sysctl("90-default.conf", "present", (SysctlEntry("kernel.test", "0"),), False, "fallback")
    with pytest.raises(PlanRefused) as fallback_error:
        plan(request(fallback), root=equal_root)
    assert fallback_error.value.problems[0].code == "shadow.earlier"


def test_equal_authoritative_overlap_is_an_error_if_our_file_is_not_last(tmp_path):
    root = fake_root(tmp_path)
    put(root, "/usr/lib/sysctl.d/90-later.conf", b"kernel.test = 1\n")
    resource = Sysctl("60-site.conf", "present", (SysctlEntry("kernel.test", "1"),), False, "authoritative")
    with pytest.raises(PlanRefused) as failure:
        plan(request(resource), root=root)
    assert failure.value.problems[0].code == "shadow.later"


def test_file_mask_and_glob_key_overlap_are_checked(tmp_path):
    root = fake_root(tmp_path)
    put(root, "/usr/lib/sysctl.d/60-site.conf", b"net.ipv4.conf.eth0.rp_filter=0\n")
    resource = Sysctl(
        "60-site.conf", "present", (SysctlEntry("net.ipv4.conf.*.rp_filter", "1"),), False, "fallback"
    )
    with pytest.raises(PlanRefused) as failure:
        plan(request(resource), root=root)
    assert {problem.code for problem in failure.value.problems} == {"shadow.mask"}
    accepted = Sysctl(
        "60-site.conf", "present", (SysctlEntry("net.ipv4.conf.*.rp_filter", "1"),), False, "fallback",
        (("/usr/lib/sysctl.d/60-site.conf", "$file"),),
    )
    planned = plan(request(accepted), root=root)
    assert any("note shadow.mask" in detail for detail in planned.changes[0].details)

    glob_root = fake_root(tmp_path / "glob")
    put(glob_root, "/usr/lib/sysctl.d/90-vendor.conf", b"net.ipv4.conf.eth0.rp_filter=0\n")
    glob = Sysctl("60-site.conf", "present", (SysctlEntry("net.ipv4.conf.*.rp_filter", "1"),), False, "authoritative")
    with pytest.raises(PlanRefused) as glob_failure:
        plan(request(glob), root=glob_root)
    assert glob_failure.value.problems[0].code == "shadow.later"


def test_a_shadow_source_change_after_plan_is_stale(tmp_path):
    root = fake_root(tmp_path)
    source = put(root, "/usr/lib/sysctl.d/90-vendor.conf", b"kernel.test=0\n")
    resource = Sysctl(
        "60-site.conf", "present", (SysctlEntry("kernel.test", "1"),), False, "authoritative",
        (("/usr/lib/sysctl.d/90-vendor.conf", "kernel.test"),),
    )
    planned = plan(request(resource), root=root)
    source.write_text("kernel.test=2\n", encoding="utf-8")
    with pytest.raises(StalePlan, match="changed after plan"):
        apply(planned)


def test_verify_rechecks_layered_intent_after_the_fragment_is_committed(tmp_path):
    root = fake_root(tmp_path)
    resource = Sysctl("60-site.conf", "present", (SysctlEntry("kernel.test", "1"),), False, "authoritative")
    planned = plan(request(resource), root=root)
    staged = resource.stage(SimpleNamespace(root=root, stage_dir=tmp_path), planned.changes[0])
    context = SimpleNamespace(root=root, expected={resource.addresses()[0]: staged})
    resource.commit(context, staged)
    put(root, "/usr/lib/sysctl.d/90-vendor.conf", b"kernel.test=2\n")

    assert [problem.code for problem in resource.verify(context)] == ["shadow.later"]


def test_strict_loader_and_toml_round_trip_include_intent_and_accept_shadow(tmp_path):
    mapping = {
        "augeaswrap": 1,
        "writer": "test sysctl 1.0",
        "sysctl": [
            {
                "file": "60-site.conf",
                "state": "present",
                "apply_now": False,
                "intent": "authoritative",
                "entries": [{"key": "kernel.test", "value": "1", "comment": "line"}],
                "accept_shadow": [{"file": "/usr/lib/sysctl.d/90-vendor.conf", "key": "kernel.test"}],
            }
        ],
    }
    request_value = load_request(mapping)
    serialized = request_value.to_toml()
    reparsed = load_request(tomllib.loads(serialized))
    assert reparsed.to_toml() == serialized
    with pytest.raises(ValueError, match="missing sysctl field.*intent"):
        load_request({"augeaswrap": 1, "writer": "w", "sysctl": [{"file": "x.conf", "state": "present", "entries": [], "apply_now": False}]})
    with pytest.raises(ValueError, match="unknown sysctl key"):
        load_request({"augeaswrap": 1, "writer": "w", "sysctl": [{"file": "x.conf", "state": "absent", "apply_now": False, "intent": "fallback", "extra": 1}]})
    with pytest.raises(ValueError, match="duplicate key"):
        Sysctl("60-site.conf", "present", (SysctlEntry("kernel.x", "1"), SysctlEntry("kernel.x", "2")), False, "fallback")
    with pytest.raises(ValueError, match="normalized absolute path"):
        Sysctl("60-site.conf", "present", (SysctlEntry("kernel.x", "1"),), False, "fallback", (("/etc//sysctl.d/x.conf", "kernel.x"),))


@pytest.mark.parametrize("key,value,comment", [
    ("bad key", "1", None),
    ("kernel.x", "", None),
    ("kernel.x", "1\n2", None),
    ("kernel.x", "1#ignored", None),
    ("kernel.x", "1", "bad\ncomment"),
])
def test_sysctl_entry_rejects_invalid_single_line_values(key, value, comment):
    with pytest.raises(ValueError):
        SysctlEntry(key, value, comment)


def test_fake_root_rejects_a_leading_systemd_ignore_marker_as_a_key():
    with pytest.raises(ValueError, match="leading '-' marker is not a key"):
        SysctlEntry("-vm.swappiness", "1")
    assert sysctl._parse_assignments(b"-vm/swappiness = 1\n") == (("vm.swappiness", "1"),)
    with pytest.raises(ValueError, match="invalid sysctl assignment"):
        sysctl._parse_assignments(b"--vm.swappiness = 1\n")


def test_sysctl_key_may_not_start_with_a_dot():
    with pytest.raises(ValueError, match="must start with a letter, digit, underscore, or \\*"):
        SysctlEntry(".hidden", "1")


def test_live_proc_key_guard_is_only_applied_to_live_root():
    sysctl._check_live_keys((), (SysctlEntry("kernel.missing", "1"),), Path("/tmp/fake"))
    with pytest.raises(PlanRefused, match="does not exist under /proc/sys"):
        sysctl._check_live_keys((), (SysctlEntry("kernel.missing", "1"),), Path("/"))
    sysctl._check_live_keys(("kernel.test",), (SysctlEntry("kernel.test", "1"),), Path("/"))


def test_live_proc_key_guard_requires_a_matching_string_key():
    with pytest.raises(PlanRefused, match="does not exist under /proc/sys"):
        sysctl._check_live_keys(("kernel.other",), (SysctlEntry("kernel.missing", "1"),), Path("/"))


def test_live_key_enumeration_uses_proc_names_and_fnmatch(monkeypatch):
    def walk(path, *, followlinks):
        assert path == Path("/proc/sys")
        assert followlinks is False
        return [
            ("/proc/sys", ["kernel"], []),
            ("/proc/sys/kernel", [], ["hostname", "domainname"]),
            ("/tmp", [], ["outside"]),
        ]

    monkeypatch.setattr(
        sysctl.os,
        "walk",
        walk,
    )
    assert sysctl._live_keys((SysctlEntry("kernel.*name", "x"),)) == ("kernel.domainname", "kernel.hostname")


def test_live_key_enumeration_does_not_follow_symlinked_proc_directories(tmp_path, monkeypatch):
    proc_root = tmp_path / "proc-sys"
    kernel = proc_root / "kernel"
    kernel.mkdir(parents=True)
    (kernel / "hostname").write_text("host", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("secret", encoding="utf-8")
    (kernel / "alias").symlink_to(outside, target_is_directory=True)
    real_walk = sysctl.os.walk

    def walk(path, *, followlinks):
        assert path == Path("/proc/sys")
        assert followlinks is False
        for directory, dirs, files in real_walk(proc_root, followlinks=followlinks):
            suffix = Path(directory).relative_to(proc_root)
            virtual = Path("/proc/sys") / suffix
            yield str(virtual), dirs, files

    monkeypatch.setattr(sysctl.os, "walk", walk)

    assert sysctl._live_keys((SysctlEntry("kernel.*", "x"),)) == ("kernel.hostname",)


def test_fake_root_sysctl_observation_has_no_proc_keys(tmp_path):
    root = fake_root(tmp_path)
    resource = Sysctl("60-test.conf", "present", (SysctlEntry("kernel.hostname", "example"),), False, "fallback")

    observed = resource.observe(SimpleNamespace(root=root))[resource.addresses()[0]]

    assert observed["proc_keys"] == []


def test_sysctl_observation_never_enumerates_proc_for_fake_or_absent_roots(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    monkeypatch.setattr(sysctl, "_live_keys", lambda *_args: pytest.fail("fake root read /proc/sys"))
    present = Sysctl("60-test.conf", "present", (SysctlEntry("kernel.hostname", "example"),), False, "fallback")

    assert present.observe(SimpleNamespace(root=root))[present.addresses()[0]]["proc_keys"] == []

    monkeypatch.setattr(sysctl, "observe_layers", lambda *_args: {"files": {}, "directories": {}})
    monkeypatch.setattr(sysctl, "_native_headers", lambda *_args: ())
    monkeypatch.setattr(file_ops, "observe_file", lambda *_args: {"exists": False})
    absent = Sysctl("60-test.conf", "absent", (), False, "fallback")
    assert absent.observe(SimpleNamespace(root=Path("/")))[absent.addresses()[0]]["proc_keys"] == []


def test_absent_sysctl_does_not_report_deletion_effect_for_a_missing_fragment(tmp_path):
    root = fake_root(tmp_path)
    resource = Sysctl("60-missing.conf", "absent", (), False, "fallback")

    assert plan(request(resource), root=root).changes[0].details == ()


def test_native_order_cross_check_uses_shared_command_runner(monkeypatch):
    layers = {
        "files": {"/etc/sysctl.d/10-a.conf": base64.b64encode(b"a=1\n").decode()},
        "directories": {},
    }
    monkeypatch.setattr(sysctl, "_resolve_systemd_sysctl", lambda: "/usr/lib/systemd/systemd-sysctl")
    calls = []

    def run(argv, *, timeout):
        assert timeout == 30.0
        calls.append((tuple(argv), timeout))
        return SimpleNamespace(returncode=0, stdout="# /etc/sysctl.d/10-a.conf\na=1\n", stderr="")

    monkeypatch.setattr(shadow, "run_command", run)
    assert sysctl._native_headers(Path("/"), layers) == ("/etc/sysctl.d/10-a.conf",)

    assert calls == [(('/usr/lib/systemd/systemd-sysctl', '--cat-config'), 30.0)]

    def native_result(stdout, returncode=0, stderr=""):
        def run(argv, *, timeout):
            assert tuple(argv) == ("/usr/lib/systemd/systemd-sysctl", "--cat-config")
            assert timeout == 30.0
            return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

        return run

    monkeypatch.setattr(
        shadow,
        "run_command",
        native_result(
            "# /etc/sysctl.d/10-a.conf\na=1\n# /etc/sysctl.conf\n# Configuration file for setting system variables\na=2\n"
        ),
    )
    with_conf = {
        **layers,
        "files": {
            **layers["files"],
            "/etc/sysctl.conf": base64.b64encode(b"a=2\n").decode("ascii"),
        },
    }
    assert sysctl._native_headers(Path("/"), with_conf) == (
        "/etc/sysctl.d/10-a.conf", "/etc/sysctl.conf"
    )
    monkeypatch.setattr(
        shadow,
        "run_command",
        native_result(
            "# /etc/sysctl.d/10-a.conf\na=1\n# /etc/sysctl.d/10-a.conf - comment text\n"
        ),
    )
    assert sysctl._native_headers(Path("/"), layers) == ("/etc/sysctl.d/10-a.conf",)
    monkeypatch.setattr(shadow, "run_command", native_result("# /etc/sysctl.d/99-b.conf\n"))
    with pytest.raises(PlanRefused, match="order differs"):
        sysctl._native_headers(Path("/"), layers)
    monkeypatch.setattr(
        shadow,
        "run_command",
        native_result("# /etc/sysctl.d/10-a.conf\na=1\n# /lib/sysctl.d/99-unknown.conf\nx=1\n"),
    )
    with pytest.raises(PlanRefused, match="order differs"):
        sysctl._native_headers(Path("/"), layers)
    monkeypatch.setattr(shadow, "run_command", native_result("", returncode=1, stderr="unavailable"))
    with pytest.raises(PlanRefused, match="exited 1"):
        sysctl._native_headers(Path("/"), layers)


def test_native_sysctl_resolves_absolute_path_then_path_fallback_and_names_missing(monkeypatch):
    monkeypatch.setattr(sysctl.os.path, "isfile", lambda path: path == sysctl._SYSTEMD_SYSCTL)
    monkeypatch.setattr(sysctl.os, "access", lambda _path, _mode: True)
    monkeypatch.setattr(sysctl.shutil, "which", lambda _name: pytest.fail("PATH lookup should be second"))
    assert sysctl._resolve_systemd_sysctl() == "/usr/lib/systemd/systemd-sysctl"

    access_calls = []
    monkeypatch.setattr(sysctl.os.path, "isfile", lambda path: path == sysctl._SYSTEMD_SYSCTL)
    monkeypatch.setattr(
        sysctl.os,
        "access",
        lambda path, mode: access_calls.append((path, mode)) or False,
    )
    monkeypatch.setattr(sysctl.shutil, "which", lambda name: "/usr/sbin/systemd-sysctl" if name == "systemd-sysctl" else None)
    assert sysctl._resolve_systemd_sysctl() == "/usr/sbin/systemd-sysctl"
    assert access_calls == [(sysctl._SYSTEMD_SYSCTL, sysctl.os.X_OK)]

    monkeypatch.setattr(sysctl.os.path, "isfile", lambda _path: False)
    monkeypatch.setattr(sysctl.shutil, "which", lambda _name: None)
    with pytest.raises(PlanRefused) as missing:
        sysctl._native_headers(Path("/"), {"files": {}, "directories": {}})
    assert missing.value.problems[0].code == "sysctl.native.failed"
    assert "systemd-sysctl is unavailable" in missing.value.problems[0].message


def test_native_sysctl_runner_oserror_is_a_named_refusal(monkeypatch):
    monkeypatch.setattr(sysctl, "_resolve_systemd_sysctl", lambda: "/usr/lib/systemd/systemd-sysctl")

    def missing(argv, *, timeout):
        assert tuple(argv) == ("/usr/lib/systemd/systemd-sysctl", "--cat-config")
        assert timeout == 30.0
        raise FileNotFoundError("gone")

    monkeypatch.setattr(shadow, "run_command", missing)
    with pytest.raises(PlanRefused) as failure:
        sysctl._native_headers(Path("/"), {"files": {}, "directories": {}})
    assert failure.value.problems[0].code == "sysctl.native.failed"
    assert "cannot run systemd-sysctl --cat-config" in failure.value.problems[0].message


def test_native_sysctl_runner_timeout_is_a_named_refusal(monkeypatch):
    monkeypatch.setattr(sysctl, "_resolve_systemd_sysctl", lambda: "/usr/lib/systemd/systemd-sysctl")

    def timed_out(argv, *, timeout):
        assert tuple(argv) == ("/usr/lib/systemd/systemd-sysctl", "--cat-config")
        assert timeout == 30.0
        raise subprocess.TimeoutExpired("systemd-sysctl --cat-config", timeout)

    monkeypatch.setattr(
        shadow,
        "run_command",
        timed_out,
    )
    with pytest.raises(PlanRefused) as failure:
        sysctl._native_headers(Path("/"), {"files": {}, "directories": {}})
    assert failure.value.problems[0].code == "sysctl.native.failed"
    assert "cannot run systemd-sysctl --cat-config" in failure.value.problems[0].message


def test_readback_checks_exact_and_glob_values_and_failures(monkeypatch):
    entries = (SysctlEntry("net.ipv4.conf.*.rp_filter", "1"),)
    monkeypatch.setattr(sysctl, "_live_keys", lambda _entries: ("net.ipv4.conf.eth0.rp_filter", "net.ipv4.conf.lo.rp_filter"))

    def result(stdout, returncode=0, stderr=""):
        def run(argv, *, timeout):
            assert tuple(argv[:2]) == ("sysctl", "-n")
            assert timeout == 10.0
            return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)

        return run

    monkeypatch.setattr(shadow, "run_command", result("1\n"))
    assert sysctl._readback(entries, None) == []
    monkeypatch.setattr(shadow, "run_command", result("1\t\n"))
    assert sysctl._readback(entries, None) == []
    monkeypatch.setattr(shadow, "run_command", result("0\n"))
    assert {problem.code for problem in sysctl._readback(entries, None)} == {"sysctl.readback.mismatch"}
    monkeypatch.setattr(shadow, "run_command", result("", returncode=2, stderr="denied"))
    assert {problem.code for problem in sysctl._readback(entries, None)} == {"sysctl.readback.failed"}
    monkeypatch.setattr(sysctl, "_live_keys", lambda _entries: ())
    assert {problem.code for problem in sysctl._readback(entries, None)} == {"sysctl.readback.missing"}


def test_verify_skips_live_reads_for_fake_root_and_checks_live_branch_through_seams(monkeypatch, tmp_path):
    root = fake_root(tmp_path)
    resource = Sysctl("60-test.conf", "present", (SysctlEntry("kernel.test", "1"),), True, "authoritative")
    planned = plan(request(resource), root=root)
    staged = resource.stage(SimpleNamespace(root=root, stage_dir=tmp_path), planned.changes[0])
    ctx = SimpleNamespace(root=root, expected={resource.addresses()[0]: staged})
    resource.commit(ctx, staged)
    assert resource.verify(ctx) == []

    monkeypatch.setattr(file_ops, "verify_file", lambda *_args: [])
    monkeypatch.setattr(sysctl, "observe_layers", lambda *_args: {"files": {}, "directories": {}})
    monkeypatch.setattr(sysctl, "_native_headers", lambda *_args: ())
    monkeypatch.setattr(sysctl, "_readback", lambda *_args: [])
    live_ctx = SimpleNamespace(root=Path("/"), expected={resource.addresses()[0]: staged})
    assert resource.verify(live_ctx) == []
    monkeypatch.setattr(sysctl, "_native_headers", lambda *_args: (_ for _ in ()).throw(ValueError("bad order")))
    assert resource.verify(live_ctx)[0].code == "sysctl.verify.failed"


def test_verify_preserves_native_order_refusal_problems(monkeypatch):
    resource = Sysctl("60-test.conf", "present", (SysctlEntry("kernel.test", "1"),), False, "authoritative")
    monkeypatch.setattr(file_ops, "verify_file", lambda *_args: [])
    monkeypatch.setattr(sysctl, "observe_layers", lambda *_args: {"files": {}, "directories": {}})
    refusal = PlanRefused([Problem("sysctl.native.order", "different order")])
    monkeypatch.setattr(sysctl, "_native_headers", lambda *_args: (_ for _ in ()).throw(refusal))

    problems = resource.verify(SimpleNamespace(root=Path("/"), expected={}))

    assert [problem.code for problem in problems] == ["sysctl.native.order"]


def test_parse_assignments_accepts_systemd_comments_slash_keys_and_ignore_prefix():
    assert sysctl._parse_assignments(
        b"# note\n; another note\n\n-kernel/test = 1\n"
    ) == (("kernel.test", "1"),)
    assert sysctl._parse_assignments(b"kernel.test = 1 # text\n") == (("kernel.test", "1 # text"),)


def test_slash_keys_are_normalized_for_live_matching_and_duplicates(monkeypatch):
    assert sysctl._canonical_key("net/ipv4/conf/*/rp_filter") == "net.ipv4.conf.*.rp_filter"
    def walk(path, *, followlinks):
        assert path == Path("/proc/sys")
        assert followlinks is False
        return [("/proc/sys/net/ipv4/conf/all", [], ["rp_filter"])]

    monkeypatch.setattr(sysctl.os, "walk", walk)
    assert sysctl._live_keys((SysctlEntry("net/ipv4/conf/*/rp_filter", "1"),)) == (
        "net.ipv4.conf.all.rp_filter",
    )
    with pytest.raises(ValueError, match="duplicate key"):
        Sysctl(
            "60-site.conf",
            "present",
            (SysctlEntry("net.ipv4.conf.all.rp_filter", "1"), SysctlEntry("net/ipv4/conf/all/rp_filter", "1")),
            False,
            "authoritative",
        )


def test_parse_assignments_refuses_invalid_lines():
    with pytest.raises(ValueError, match="invalid sysctl assignment"):
        sysctl._parse_assignments(b"broken line\n")
    with pytest.raises(ValueError, match="invalid sysctl assignment"):
        sysctl._parse_assignments(b"kernel.test = \n")


@pytest.mark.parametrize(
    "value, message",
    [
        (None, "must be an array"),
        (["bad"], "entries must be"),
        ([("relative", "key")], "normalized absolute path"),
        ([("/etc/x", "")], "non-empty string"),
        ([("/etc/x", "key"), ("/etc/x", "key")], "duplicate accept_shadow"),
    ],
)
def test_accept_shadow_rejects_non_exact_values(value, message):
    with pytest.raises(ValueError, match=message):
        sysctl._accept_value(value)


def test_accept_shadow_rejects_pair_like_values_and_independent_invalid_fields():
    class PairLike:
        def __len__(self):
            return 2

        def __iter__(self):
            return iter(("/etc/x.conf", "kernel.x"))

    with pytest.raises(ValueError, match="entries must be"):
        sysctl._accept_value([PairLike()])
    for pair in (
        ("/etc/../etc/x.conf", "kernel.x"),
        ("/etc//x.conf", "kernel.x"),
        ("/etc/x.conf", None),
        ("/etc/x.conf", ""),
    ):
        with pytest.raises(ValueError, match="absolute path|non-empty string"):
            sysctl._accept_value([pair])


def test_sysctl_restore_rejects_invalid_preimage_paths():
    for filename in (None, "/tmp/60-x.conf", "/etc/sysctl.d/not-conf.txt"):
        with pytest.raises(ValueError, match="outside /etc/sysctl.d"):
            sysctl._restore("sysctl:/etc/sysctl.d/60-x.conf", {"path": filename, "exists": False}, ())


def test_sysctl_internal_guards_and_absent_parser_path(tmp_path):
    with pytest.raises(ValueError, match="single .conf"):
        Sysctl("bad", "present", (SysctlEntry("kernel.x", "1"),), False, "fallback")
    with pytest.raises(ValueError, match="state must"):
        Sysctl("60-x.conf", "unknown", (), False, "fallback")
    with pytest.raises(ValueError, match="apply_now must"):
        Sysctl("60-x.conf", "present", (SysctlEntry("kernel.x", "1"),), 1, "fallback")
    with pytest.raises(ValueError, match="entries must be a tuple"):
        Sysctl("60-x.conf", "present", [], False, "fallback")
    with pytest.raises(ValueError, match="requires at least one"):
        Sysctl("60-x.conf", "present", (), False, "fallback")
    with pytest.raises(ValueError, match="SysctlEntry values"):
        Sysctl("60-x.conf", "present", (object(),), False, "fallback")
    with pytest.raises(ValueError, match="intent must"):
        Sysctl("60-x.conf", "present", (SysctlEntry("kernel.x", "1"),), False, "unknown")
    assert sysctl._parse({"file": "60-x.conf", "state": "absent", "apply_now": False, "intent": "fallback"}).state == "absent"
    with pytest.raises(ValueError, match="array of tables"):
        sysctl._parse({"file": "60-x.conf", "state": "absent", "apply_now": False, "intent": "fallback", "entries": "bad"})
    with pytest.raises(ValueError, match="array of tables"):
        sysctl._parse({"file": "60-x.conf", "state": "absent", "apply_now": False, "intent": "fallback", "entries": ["bad"]})
    with pytest.raises(ValueError, match="missing sysctl field"):
        sysctl._parse({"file": "60-x.conf", "state": "present", "apply_now": False, "intent": "fallback"})
    with pytest.raises(ValueError, match="state must"):
        sysctl._parse({"file": "60-x.conf", "state": "bad", "apply_now": False, "intent": "fallback"})

    resource = Sysctl("60-x.conf", "present", (SysctlEntry("kernel.x", "1"),), False, "fallback")
    with pytest.raises(ValueError, match="observation is invalid"):
        resource.diff(None, {resource.addresses()[0]: None})
    with pytest.raises(ValueError, match="no layer snapshot"):
        resource.diff(SimpleNamespace(root=tmp_path), {resource.addresses()[0]: {"target": {"exists": False}}})
    with pytest.raises(TypeError, match="requires a staged file"):
        resource.commit(None, object())
    assert Sysctl("60-x.conf", "absent", (), False, "fallback").post_actions([]) == []
    with pytest.raises(ValueError, match="live proc/sys key list"):
        sysctl._check_live_keys(None, (SysctlEntry("kernel.x", "1"),), Path("/"))


def test_sysctl_live_verify_without_apply_now_uses_fake_seams(monkeypatch):
    resource = Sysctl("60-x.conf", "present", (SysctlEntry("kernel.x", "1"),), False, "fallback")
    monkeypatch.setattr(file_ops, "verify_file", lambda *_args: [])
    monkeypatch.setattr(sysctl, "observe_layers", lambda *_args: {"files": {}, "directories": {}})
    monkeypatch.setattr(sysctl, "_native_headers", lambda *_args: ())
    ctx = SimpleNamespace(root=Path("/"), expected={})
    assert resource.verify(ctx) == []
