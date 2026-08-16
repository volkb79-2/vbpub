"""High-signal offline witnesses for operational CMRU branches."""
from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import bundle, changelog, handlers, release, tester_gate
from cmru.agent import protocol, reconciler


def _bundle_config(root: Path, **overrides):
    values = dict(
        project_root=root,
        wheel_project_root=root,
        dist_dir=root / "dist",
        bundle_dir=root / "dist" / "bundle",
        client_dir=root / "dist" / "client",
        wheel_enabled=False,
        wheel_python_bin="python3",
        wheel_find_links=None,
        archive_template="demo-{version}.tar.xz",
        archive_version_env="VERSION",
        archive_format="xztar",
        copy_files=[],
        copy_dirs=[],
    )
    values.update(overrides)
    return bundle.BundleConfig(**values)


def test_bundle_config_accepts_explicitly_disabled_wheel_table_and_excludes_metadata(tmp_path):
    cfg = tmp_path / "bundle.toml"
    cfg.write_text(
        "project_root='.'\n"
        "[wheel]\nenabled=false\nproject_root='client'\nfind_links='links'\n"
        "[archive]\nname_template='x-{version}'\nversion_env='V'\nformat='xztar'\n"
        "[copy]\nfiles=[]\ndirs=[]\n",
        encoding="utf-8",
    )
    parsed = bundle.parse_config(cfg)
    assert parsed.wheel_enabled is False
    assert parsed.wheel_project_root == (tmp_path / "client").resolve()
    assert bundle._is_excluded("src/pyproject.toml") is False
    assert bundle._is_excluded("src/minisign.key") is True


def test_bundle_copy_sources_excludes_outside_mount_candidates_and_missing_dirs(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("x=1\n", encoding="utf-8")
    cfg = _bundle_config(root, bundle_dir=root / "bundle", copy_files=[str(outside)])
    cfg.bundle_dir.mkdir()
    # A source outside project_root is still copied by an explicit caller; the
    # ignore callback must classify its basename without raising ValueError.
    bundle.copy_sources(cfg)
    assert (cfg.bundle_dir / outside.name).read_text() == "x=1\n"
    missing = _bundle_config(root, copy_dirs=["not-there"])
    with pytest.raises(FileNotFoundError, match="source dir"):
        bundle.copy_sources(missing)


def test_bundle_deterministic_archive_reads_epoch_from_environment(tmp_path, monkeypatch):
    root = tmp_path / "project"
    root.mkdir()
    cfg = _bundle_config(root, dist_dir=root / "dist", bundle_dir=root / "dist" / "bundle")
    cfg.bundle_dir.mkdir(parents=True)
    (cfg.bundle_dir / "README").write_text("safe\n", encoding="utf-8")
    monkeypatch.setenv("VERSION", "1.2.3")
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "17")
    out = bundle.create_archive(cfg)
    assert out == root / "dist" / "demo-1.2.3.tar.xz"
    assert out.is_file()


def test_changelog_git_failure_preserves_diagnostic(monkeypatch, tmp_path):
    monkeypatch.setattr(
        changelog.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=2, stderr="bad ref\n", stdout=""),
    )
    with pytest.raises(RuntimeError, match=r"git rev-parse HEAD failed \(2\): bad ref"):
        changelog._git(tmp_path, "rev-parse", "HEAD")


def test_changelog_rejects_external_strategy_without_variable(tmp_path, monkeypatch):
    project = SimpleNamespace(
        name="demo", cwd="demo", prefix="demo-v", git_tag=True,
        version=SimpleNamespace(strategy="external:"),
    )
    monkeypatch.setattr(changelog, "detect_changed_projects", lambda *a: [("demo", None, "demo-v1.0.0", "patch")])
    with pytest.raises(RuntimeError, match="no variable name"):
        changelog._project_release_plan(tmp_path, project)


def test_changelog_backfill_requires_history_marker_when_file_exists(tmp_path, monkeypatch):
    project = SimpleNamespace(name="demo", cwd="demo", changelog="CHANGES.md", prefix="demo-v")
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo" / "CHANGES.md").write_text("# hand written\n", encoding="utf-8")
    monkeypatch.setattr(changelog, "_git", lambda *a: "deadbeef")
    monkeypatch.setattr(changelog, "_previous_project_tag", lambda *a: None)
    monkeypatch.setattr(changelog, "_subject_groups", lambda *a, **k: {})
    with pytest.raises(RuntimeError, match="lacks"):
        changelog.backfill_release_changelog(tmp_path, project, "demo-v1.0.0")


def test_handlers_require_pinned_builder_and_governed_cgroup(monkeypatch):
    monkeypatch.delenv(handlers._WHEEL_BUILDER_IMAGE_ENV, raising=False)
    with pytest.raises(SystemExit) as missing_image:
        handlers._check_build_prerequisites()
    assert missing_image.value.code == 3
    monkeypatch.setenv(handlers._WHEEL_BUILDER_IMAGE_ENV, "builder@sha256:abc")
    monkeypatch.setattr(handlers.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.delenv(handlers._DOCKER_CGROUP_PARENT_ENV, raising=False)
    monkeypatch.delenv("CGROUP_PARENT_DEV_BACKGROUND", raising=False)
    with pytest.raises(SystemExit) as missing_slice:
        handlers._docker_cgroup_parent()
    assert missing_slice.value.code == 3


def test_handlers_host_bind_resolution_uses_longest_mount_and_refuses_unknown(monkeypatch, tmp_path):
    path = Path("/workspaces/vbpub/cmru")
    mountinfo = "10 1 0:1 /host/repo /workspaces/vbpub rw - bind x y\n"
    monkeypatch.setattr(handlers.Path, "read_text", lambda *a, **k: mountinfo)
    assert handlers._host_bind_source(path) == "/host/repo/cmru"
    monkeypatch.setattr(handlers.Path, "read_text", lambda *a, **k: "")
    with pytest.raises(RuntimeError, match="no matching mount"):
        handlers._host_bind_source(path)


def test_tester_gate_mountinfo_decoding_and_resource_flags(monkeypatch, tmp_path):
    assert tester_gate._unescape_mountinfo(r"/host/a\040b\011c\012d\134e") == "/host/a b\tc\nd\\e"
    mountinfo = "10 1 0:1 /host/repo /cockpit rw - bind x y\n"
    assert tester_gate._physical_path(Path("/cockpit/cmru"), mountinfo) == Path("/host/repo/cmru")
    monkeypatch.setattr(tester_gate, "_physical_path", lambda p: p)
    monkeypatch.setattr(tester_gate, "_git_common_dir", lambda p: None)
    argv = tester_gate.build_docker_command(
        tmp_path, ".", ["true"], image="tester", cgroup_parent="dev.slice",
        memory="1g", memory_swap="2g", cpus="1", device_read_iops="/dev/vda:10",
        device_write_iops="/dev/vda:20", device_read_bps="/dev/vda:30",
        device_write_bps="/dev/vda:40",
    )
    assert ["--device-read-iops", "/dev/vda:10"] == argv[argv.index("--device-read-iops"):argv.index("--device-read-iops") + 2]
    assert argv[-2:] == ["tester", "true"]


def test_release_artifact_discovery_and_wheel_metadata_fail_closed(tmp_path):
    with pytest.raises(SystemExit) as missing:
        release.find_artifact(tmp_path, "*.whl")
    assert missing.value.code == 1
    (tmp_path / "a.whl").write_bytes(b"x")
    (tmp_path / "b.whl").write_bytes(b"x")
    with pytest.raises(SystemExit) as multiple:
        release.find_artifact(tmp_path, "*.whl")
    assert multiple.value.code == 1
    with pytest.raises(zipfile.BadZipFile):
        release.read_wheel_version(tmp_path / "a.whl")


def test_release_validation_rejects_missing_primary_asset():
    gh = SimpleNamespace(resolve_latest=lambda prefix: {
        "version": "1.0.0", "tag": "demo-v1.0.0", "assets": [],
    })
    with pytest.raises(SystemExit) as missing:
        release.validate_latest_release(gh, "demo", retries=1, delay=0)
    assert missing.value.code == 1


def test_reconciler_signature_and_install_failures_are_explicit(monkeypatch, tmp_path):
    with pytest.raises(protocol.DesiredStateError, match="no minisign public key"):
        reconciler._verify_sig_if_present(b"{}", b"sig", "")
    backend = SimpleNamespace()
    rec = reconciler.Reconciler(backend, "node", "land", "user", tmp_path)
    desired = protocol.DesiredState(
        schema_version=1, generation=1, action="apply",
        release=protocol.ReleaseRef("tag", "url", "a" * 64), profiles=[],
        config_hash="h", plan_id="p", step_id="s",
    )
    monkeypatch.setattr(reconciler.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError()))
    assert rec._ensure_release(desired) is None
    (tmp_path / "releases" / "tag").mkdir(parents=True)
    assert rec._ensure_release(desired) == tmp_path / "releases" / "tag"


def test_reconciler_noop_rejects_older_and_accepts_matching_generation(tmp_path):
    rec = reconciler.Reconciler(SimpleNamespace(), "n", "l", release_root=tmp_path)
    desired = protocol.DesiredState(
        schema_version=1, generation=2, action="apply",
        release=protocol.ReleaseRef("tag", "url", "digest"), profiles=[],
        config_hash="h", plan_id="p", step_id="s",
    )
    assert rec._is_noop(desired, protocol.ObservedState(applied_generation=3)) is True
    assert rec._is_noop(desired, protocol.ObservedState(applied_generation=1)) is False
    assert rec._is_noop(desired, protocol.ObservedState(
        applied_generation=2, release_digest="digest", adapter_phase="s")) is True
