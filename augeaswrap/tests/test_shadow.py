"""Effective order, caller intent, exact acceptance and command-seam tests."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from augeaswrap import _fs
from augeaswrap.shadow import (
    Assignment,
    TARGET_CLASSES,
    TargetClass,
    analyze,
    command_error,
    effective_files,
    observe_layers,
    run_command,
    settings,
)


def root_at(tmp_path: Path) -> Path:
    root = tmp_path / "root"
    root.mkdir()
    return root


def put(root: Path, path: str, data: bytes) -> None:
    target = root / path.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def sysctl_class() -> TargetClass:
    return TargetClass("test", ("/etc/test.d", "/usr/lib/test.d"), "last", ("/etc/test.conf",))


def test_observe_layers_captures_directory_membership_bytes_and_extra_absence(tmp_path):
    root = root_at(tmp_path)
    put(root, "/etc/test.d/20-a.conf", b"a=1\n")
    put(root, "/etc/test.conf", b"z=2\n")
    observed = observe_layers(root, sysctl_class())
    assert observed["directories"] == {"/etc/test.d": ["20-a.conf"], "/usr/lib/test.d": None}
    files, masks = effective_files(observed, sysctl_class())
    assert [(item.path, item.data, item.rank) for item in files] == [
        ("/etc/test.d/20-a.conf", b"a=1\n", 0),
        ("/etc/test.conf", b"z=2\n", 2),
    ]
    assert masks == ()


def test_effective_files_sorts_names_and_masks_lower_directory_same_basename(tmp_path):
    root = root_at(tmp_path)
    put(root, "/etc/test.d/80-z.conf", b"z=1\n")
    put(root, "/usr/lib/test.d/80-z.conf", b"z=2\n")
    put(root, "/usr/lib/test.d/10-a.conf", b"a=1\n")
    observed = observe_layers(root, sysctl_class())
    files, masks = effective_files(observed, sysctl_class())
    assert [item.path for item in files] == ["/usr/lib/test.d/10-a.conf", "/etc/test.d/80-z.conf"]
    assert masks == (("/usr/lib/test.d/80-z.conf", "/etc/test.d/80-z.conf"),)


def test_extra_configuration_files_follow_all_sorted_drop_ins(tmp_path):
    root = root_at(tmp_path)
    put(root, "/usr/lib/test.d/zz-last.conf", b"z=1\n")
    put(root, "/etc/test.conf", b"final=2\n")

    files, masks = effective_files(observe_layers(root, sysctl_class()), sysctl_class())

    assert [item.path for item in files] == ["/usr/lib/test.d/zz-last.conf", "/etc/test.conf"]
    assert masks == ()


def test_dev_null_masks_are_observed_and_fingerprinted_as_file_masks(tmp_path):
    root = root_at(tmp_path)
    put(root, "/usr/lib/test.d/80-z.conf", b"z=2\n")
    mask = root / "etc/test.d/80-z.conf"
    mask.parent.mkdir(parents=True)
    mask.symlink_to("/dev/null")

    observed = observe_layers(root, sysctl_class())
    files, masks = effective_files(observed, sysctl_class())

    assert files == ()
    assert masks == (("/usr/lib/test.d/80-z.conf", "/etc/test.d/80-z.conf"),)
    replaced, replaced_masks = effective_files(
        observed, sysctl_class(), overrides={"/etc/test.d/80-z.conf": b"z=3\n"}
    )
    assert replaced[0].data == b"z=3\n"
    assert replaced_masks == (("/usr/lib/test.d/80-z.conf", "/etc/test.d/80-z.conf"),)
    mask.unlink()
    mask.symlink_to("/etc/passwd")
    with pytest.raises(ValueError, match="unsupported target"):
        observe_layers(root, sysctl_class())


def test_effective_files_rejects_malformed_link_observations():
    with pytest.raises(ValueError, match="invalid link map"):
        effective_files({"files": {}, "links": []}, sysctl_class())
    with pytest.raises(ValueError, match="invalid symlink"):
        effective_files({"files": {}, "links": {"/etc/test.d/x.conf": "/etc/passwd"}}, sysctl_class())


def test_target_classes_include_local_vendor_layer():
    assert TARGET_CLASSES["sysusers"].directories == (
        "/etc/sysusers.d", "/run/sysusers.d", "/usr/local/lib/sysusers.d", "/usr/lib/sysusers.d"
    )
    assert TARGET_CLASSES["tmpfiles"].directories == (
        "/etc/tmpfiles.d", "/run/tmpfiles.d", "/usr/local/lib/tmpfiles.d", "/usr/lib/tmpfiles.d"
    )


def test_sysctl_target_class_pins_etc_before_run_and_accepts_only_the_exact_mask(tmp_path):
    target_class = TARGET_CLASSES["sysctl"]
    assert target_class.directories == (
        "/etc/sysctl.d", "/run/sysctl.d", "/usr/local/lib/sysctl.d", "/usr/lib/sysctl.d"
    )
    assert target_class.extra_files == ("/etc/sysctl.conf",)

    root = root_at(tmp_path)
    put(root, "/etc/sysctl.d/60-site.conf", b"kernel.test = 1\n")
    put(root, "/run/sysctl.d/60-site.conf", b"kernel.test = 0\n")
    observed = observe_layers(root, target_class)
    files, masks = effective_files(observed, target_class)
    assert [item.path for item in files] == ["/etc/sysctl.d/60-site.conf"]
    assert masks == (("/run/sysctl.d/60-site.conf", "/etc/sysctl.d/60-site.conf"),)
    accepted = analyze(
        target_file="/etc/sysctl.d/60-site.conf",
        assignments=(),
        target_keys=(),
        intent="authoritative",
        merge="last",
        accept_shadow=(("/run/sysctl.d/60-site.conf", "$file"),),
        masks=masks,
    )
    assert accepted[0].code == "shadow.mask" and accepted[0].severity == "note"


def test_layer_observation_refuses_non_directory_and_symlink(tmp_path):
    root = root_at(tmp_path)
    (root / "etc/test.d").parent.mkdir(parents=True)
    (root / "etc/test.d").write_text("not a directory", encoding="utf-8")
    with pytest.raises(ValueError, match="not a safe directory"):
        observe_layers(root, sysctl_class())

    (root / "etc/test.d").unlink()
    other = root / "other"
    other.mkdir()
    (root / "etc/test.d").symlink_to(other, target_is_directory=True)
    with pytest.raises(_fs.PathRefusal):
        observe_layers(root, sysctl_class())

    (root / "etc/test.d").unlink()
    directory_file = root / "usr/lib/test.d/20-not-file.conf"
    directory_file.mkdir(parents=True)
    with pytest.raises(ValueError, match="not a regular file"):
        observe_layers(root, sysctl_class())


def test_effective_files_overrides_add_replace_and_remove_and_rejects_bad_snapshot(tmp_path):
    root = root_at(tmp_path)
    put(root, "/usr/lib/test.d/20-a.conf", b"a=1\n")
    observed = observe_layers(root, sysctl_class())
    added, _ = effective_files(observed, sysctl_class(), overrides={"/etc/test.d/10-new.conf": b"n=2\n"})
    assert [item.path for item in added] == ["/etc/test.d/10-new.conf", "/usr/lib/test.d/20-a.conf"]
    replaced, _ = effective_files(observed, sysctl_class(), overrides={"/usr/lib/test.d/20-a.conf": b"a=3\n"})
    assert replaced[0].data == b"a=3\n"
    removed, _ = effective_files(observed, sysctl_class(), overrides={"/usr/lib/test.d/20-a.conf": None})
    assert removed == ()
    with pytest.raises(ValueError, match="no file map"):
        effective_files({}, sysctl_class())
    with pytest.raises(ValueError, match="invalid file"):
        effective_files({"files": {"/etc/test.d/x.conf": 7}}, sysctl_class())
    with pytest.raises(Exception):
        effective_files({"files": {"/etc/test.d/x.conf": "not base64"}}, sysctl_class())


def test_settings_assignments_keep_file_and_line_order():
    files, _ = effective_files(
        {
            "files": {
                "/etc/test.d/10-a.conf": "YT0xCg==",
                "/etc/test.d/20-b.conf": "YT0yCmI9Mw==",
            },
            "directories": {},
        },
        sysctl_class(),
    )
    parsed = settings(files, lambda data: [tuple(line.split("=", 1)) for line in data.decode().splitlines()])
    assert [(item.file, item.key, item.value, item.order) for item in parsed] == [
        ("/etc/test.d/10-a.conf", "a", "1", (0, 0)),
        ("/etc/test.d/20-b.conf", "a", "2", (1, 0)),
        ("/etc/test.d/20-b.conf", "b", "3", (1, 1)),
    ]


def test_authoritative_rejects_a_later_different_or_equal_setting():
    for value in ("old", "new"):
        findings = analyze(
            target_file="/etc/sysctl.d/60-site.conf",
            assignments=(
                Assignment("/etc/sysctl.d/60-site.conf", "kernel.test", "new", (0, 0)),
                Assignment("/usr/lib/sysctl.d/90-vendor.conf", "kernel.test", value, (1, 0)),
            ),
            target_keys=("kernel.test",),
            intent="authoritative",
            merge="last",
            accept_shadow=(),
        )
        assert findings[0].code == "shadow.later"
        assert findings[0].severity == "error"


def test_fallback_rejects_our_later_setting_but_accepts_a_later_winner():
    ours_late = analyze(
        target_file="/etc/sysctl.d/90-default.conf",
        assignments=(
            Assignment("/usr/lib/sysctl.d/60-vendor.conf", "vm.test", "1", (0, 0)),
            Assignment("/etc/sysctl.d/90-default.conf", "vm.test", "2", (1, 0)),
        ),
        target_keys=("vm.test",), intent="fallback", merge="last", accept_shadow=(),
    )
    assert ours_late[0].code == "shadow.earlier" and ours_late[0].severity == "error"
    loses = analyze(
        target_file="/etc/sysctl.d/20-default.conf",
        assignments=(
            Assignment("/etc/sysctl.d/20-default.conf", "vm.test", "1", (0, 0)),
            Assignment("/usr/lib/sysctl.d/90-site.conf", "vm.test", "2", (1, 0)),
        ),
        target_keys=("vm.test",), intent="fallback", merge="last", accept_shadow=(),
    )
    assert loses == ()


def test_first_definition_intent_and_equal_value_notes():
    earlier = Assignment("/usr/lib/sysusers.d/10-vendor.conf", "svc", "g svc 10 -", (0, 0))
    ours = Assignment("/etc/sysusers.d/90-site.conf", "svc", "g svc 20 -", (1, 0))
    finding = analyze(
        target_file=ours.file,
        assignments=(earlier, ours),
        target_keys=("svc",), intent="authoritative", merge="first", accept_shadow=(),
    )[0]
    assert finding.code == "shadow.earlier" and finding.severity == "error"
    equal = analyze(
        target_file=ours.file,
        assignments=(earlier, Assignment(ours.file, "svc", earlier.value, (1, 0))),
        target_keys=("svc",), intent="authoritative", merge="first", accept_shadow=(),
    )[0]
    assert equal.severity == "error"  # authoritative must be the first even for equal values
    note = analyze(
        target_file="/etc/sysusers.d/10-site.conf",
        assignments=(
            Assignment("/etc/sysusers.d/10-site.conf", "svc", "g svc 10 -", (0, 0)),
            Assignment("/usr/lib/sysusers.d/90-vendor.conf", "svc", "g svc 10 -", (1, 0)),
        ),
        target_keys=("svc",), intent="authoritative", merge="first", accept_shadow=(),
    )[0]
    assert note.severity == "note" and note.code == "shadow.later"


def test_equal_values_are_notes_and_exact_acceptance_converts_shadow_to_note():
    assignments = (
        Assignment("/usr/lib/sysctl.d/10-old.conf", "kernel.test", "1", (0, 0)),
        Assignment("/etc/sysctl.d/60-site.conf", "kernel.test", "1", (1, 0)),
        Assignment("/run/sysctl.d/90-new.conf", "kernel.test", "2", (2, 0)),
    )
    note = analyze(
        target_file="/etc/sysctl.d/60-site.conf", assignments=assignments, target_keys=("kernel.test",),
        intent="authoritative", merge="last", accept_shadow=(),
    )
    assert any(item.severity == "note" for item in note)
    accepted = analyze(
        target_file="/etc/sysctl.d/60-site.conf", assignments=assignments, target_keys=("kernel.test",),
        intent="authoritative", merge="last", accept_shadow=(("/run/sysctl.d/90-new.conf", "kernel.test"),),
    )
    assert next(item for item in accepted if item.file.endswith("90-new.conf")).severity == "note"


def test_equal_values_are_a_note_when_fallback_intent_would_otherwise_win():
    finding = analyze(
        target_file="/etc/sysctl.d/10-default.conf",
        assignments=(
            Assignment("/etc/sysctl.d/10-default.conf", "vm.test", "1", (0, 0)),
            Assignment("/usr/lib/sysctl.d/90-vendor.conf", "vm.test", "1", (1, 0)),
        ),
        target_keys=("vm.test",),
        intent="fallback",
        merge="last",
        accept_shadow=(),
    )[0]
    assert finding.severity == "note" and finding.code == "shadow.later"


def test_analyzer_skips_unrelated_settings_and_masks_and_acceptance_is_exact():
    assignments = (
        Assignment("/etc/sysctl.d/60-site.conf", "kernel.ours", "1", (0, 0)),
        Assignment("/usr/lib/sysctl.d/90-vendor.conf", "kernel.ours", "2", (1, 0)),
    )
    assert analyze(
        target_file="/etc/sysctl.d/60-site.conf",
        assignments=assignments,
        target_keys=("kernel.unrelated",),
        intent="authoritative",
        merge="last",
        accept_shadow=(),
    ) == ()
    assert analyze(
        target_file="/etc/sysctl.d/60-site.conf",
        assignments=(assignments[0],),
        target_keys=("kernel.ours",),
        intent="authoritative",
        merge="last",
        accept_shadow=(),
    ) == ()
    wrong_acceptance = analyze(
        target_file="/etc/sysctl.d/60-site.conf",
        assignments=assignments,
        target_keys=("kernel.ours",),
        intent="authoritative",
        merge="last",
        accept_shadow=(("/usr/lib/sysctl.d/90-vendor.conf", "kernel.other"),),
    )
    assert wrong_acceptance[0].severity == "error"
    key_only_acceptance = analyze(
        target_file="/etc/sysctl.d/60-site.conf",
        assignments=assignments,
        target_keys=("kernel.ours",),
        intent="authoritative",
        merge="last",
        accept_shadow=(("/usr/lib/sysctl.d/90-near-miss.conf", "kernel.ours"),),
    )
    assert key_only_acceptance[0].severity == "error"
    unrelated_mask = analyze(
        target_file="/etc/sysctl.d/60-site.conf",
        assignments=(),
        target_keys=(),
        intent="authoritative",
        merge="last",
        accept_shadow=(),
        masks=(("/usr/lib/sysctl.d/80-other.conf", "/etc/sysctl.d/80-other.conf"),),
    )
    assert unrelated_mask == ()
    tolerated_mask = analyze(
        target_file="/etc/sysctl.d/60-site.conf",
        assignments=(),
        target_keys=(),
        intent="authoritative",
        merge="last",
        accept_shadow=(),
        masks=(("/usr/lib/sysctl.d/60-site.conf", "/etc/sysctl.d/60-site.conf"),),
    )
    assert tolerated_mask[0].code == "shadow.mask" and tolerated_mask[0].severity == "error"

    accepted_mask = analyze(
        target_file="/etc/sysctl.d/60-site.conf",
        assignments=(),
        target_keys=(),
        intent="authoritative",
        merge="last",
        accept_shadow=(("/usr/lib/sysctl.d/60-site.conf", "$file"),),
        masks=(("/usr/lib/sysctl.d/60-site.conf", "/etc/sysctl.d/60-site.conf"),),
    )
    assert accepted_mask[0].severity == "note"


def test_analyzer_reports_when_the_target_loses_a_file_mask():
    target = "/etc/sysctl.d/60-site.conf"
    winner = "/run/sysctl.d/60-site.conf"

    findings = analyze(
        target_file=target,
        assignments=(),
        target_keys=(),
        intent="authoritative",
        merge="last",
        accept_shadow=(),
        masks=((target, winner),),
    )

    assert len(findings) == 1
    assert findings[0].file == winner
    assert findings[0].key == "$file"
    assert findings[0].severity == "error"
    assert "is masked by" in findings[0].message


def test_glob_overlap_and_exact_file_mask_acceptance():
    overlap = analyze(
        target_file="/etc/sysctl.d/60-site.conf",
        assignments=(
            Assignment("/etc/sysctl.d/60-site.conf", "net.ipv4.conf.*.rp_filter", "1", (0, 0)),
            Assignment("/usr/lib/sysctl.d/90-vendor.conf", "net.ipv4.conf.eth0.rp_filter", "0", (1, 0)),
        ),
        target_keys=("net.ipv4.conf.*.rp_filter",), intent="authoritative", merge="last", accept_shadow=(),
    )
    assert overlap and overlap[0].code == "shadow.later"
    mask = analyze(
        target_file="/etc/sysctl.d/60-site.conf", assignments=(), target_keys=(),
        intent="fallback", merge="last", accept_shadow=(),
        masks=(("/usr/lib/sysctl.d/60-site.conf", "/etc/sysctl.d/60-site.conf"),),
    )
    assert mask[0].code == "shadow.mask" and mask[0].severity == "error"
    accepted = analyze(
        target_file="/etc/sysctl.d/60-site.conf", assignments=(), target_keys=(),
        intent="fallback", merge="last", accept_shadow=(("/usr/lib/sysctl.d/60-site.conf", "$file"),),
        masks=(("/usr/lib/sysctl.d/60-site.conf", "/etc/sysctl.d/60-site.conf"),),
    )
    assert accepted[0].severity == "note"


def test_last_merge_uses_our_last_overlapping_assignment_even_after_a_foreign_row():
    findings = analyze(
        target_file="/etc/sysctl.d/60-site.conf",
        assignments=(
            Assignment("/etc/sysctl.d/60-site.conf", "net.ipv4.conf.*.rp_filter", "1", (0, 0)),
            Assignment("/usr/lib/sysctl.d/90-vendor.conf", "net.ipv4.conf.eth0.rp_filter", "0", (1, 0)),
            Assignment("/etc/sysctl.d/60-site.conf", "net.ipv4.conf.eth0.rp_filter", "1", (2, 0)),
        ),
        target_keys=("net.ipv4.conf.eth0.rp_filter",),
        intent="authoritative",
        merge="last",
        accept_shadow=(),
    )
    assert findings == ()


def test_analyze_refuses_invalid_intent_and_merge_and_handles_no_overlap():
    args = dict(target_file="/etc/x", assignments=(), target_keys=(), accept_shadow=())
    with pytest.raises(ValueError, match="intent"):
        analyze(**args, intent="implicit", merge="last")
    with pytest.raises(ValueError, match="merge"):
        analyze(**args, intent="fallback", merge="random")
    assert analyze(**args, intent="fallback", merge="last") == ()


def test_command_runner_is_argv_only_and_command_error_uses_first_diagnostic(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = run_command(("tool", "--flag"), timeout=4)
    assert result.stdout == "ok\n"
    assert calls[0][0] == ["tool", "--flag"]
    assert calls[0][1]["stdin"] is subprocess.DEVNULL and calls[0][1]["timeout"] == 4
    run_command(("tool",), input_text="input")
    assert calls[1][1]["input"] == "input" and calls[1][1]["stdin"] is None
    assert command_error(("tool",), SimpleNamespace(returncode=0, stdout="", stderr="")) is None
    message = command_error(("tool",), SimpleNamespace(returncode=2, stdout="fallback", stderr="bad\nsecond"))
    assert message == "tool exited 2: bad"
