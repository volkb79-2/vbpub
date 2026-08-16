"""Additional behavioural witnesses for runner/handler runtime contracts."""
from __future__ import annotations

import io
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest


class TestRunnerRuntimeContracts:
    def test_reproducible_env_is_derived_from_head_and_build_date(self, monkeypatch, tmp_path):
        import cmru.runner as r
        vals = {"log": "1700000000", "head": "abc123"}
        monkeypatch.setattr(r, "_git_out", lambda root, *args: vals["log"] if any("format" in item for item in args) else vals["head"])
        monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False); monkeypatch.delenv("BUILD_DATE", raising=False)
        r.compute_build_date({"build_metadata": {"date_env": "BUILD_DATE", "date_format": "%Y-%m-%d"}}, tmp_path)
        assert os.environ["SOURCE_DATE_EPOCH"] == "1700000000"
        assert os.environ["OCI_REVISION"] == "abc123"
        assert os.environ["BUILD_DATE"] == "2023-11-14"

    def test_missing_git_metadata_does_not_overwrite_inherited_values(self, monkeypatch, tmp_path):
        import cmru.runner as r
        monkeypatch.setattr(r, "_git_out", lambda *args: None)
        monkeypatch.setenv("SOURCE_DATE_EPOCH", "old")
        r.apply_reproducible_env(tmp_path)
        assert os.environ["SOURCE_DATE_EPOCH"] == "old"
        monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
        monkeypatch.delenv("BUILD_DATE", raising=False)
        with pytest.raises(RuntimeError, match="Cannot derive BUILD_DATE"):
            r.compute_build_date({"build_metadata": {"date_env": "BUILD_DATE"}}, tmp_path)

    def test_env_command_parses_comments_and_rejects_malformed_lines(self, monkeypatch, tmp_path):
        import cmru.runner as r
        result = SimpleNamespace(stdout="# comment\nA=one\n B = two \n", returncode=0)
        monkeypatch.setattr(r.subprocess, "run", lambda *a, **k: result)
        r.apply_env_command(["emit-env"], tmp_path)
        assert os.environ["A"] == "one" and os.environ["B"] == "two"
        monkeypatch.setattr(r.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="bad\n", returncode=0))
        with pytest.raises(ValueError, match="KEY=VALUE"):
            r.apply_env_command(["emit-env"], tmp_path)

    def test_login_is_optional_only_when_not_required_and_multi_login_is_explicit(self, monkeypatch):
        import cmru.runner as r
        monkeypatch.delenv("TOKEN", raising=False)
        r.maybe_login({"registry": "r", "username_env": "USER", "token_env": "TOKEN", "required": False})
        with pytest.raises(RuntimeError, match="TOKEN"):
            r.maybe_login({"registry": "r", "username_env": "USER", "token_env": "TOKEN", "required": True})
        monkeypatch.setenv("GITHUB_USERNAME", "u"); monkeypatch.setenv("GITHUB_PUSH_PAT", "p")
        calls = []
        monkeypatch.setattr(r, "_docker_login", lambda *a: calls.append(a))
        r.maybe_login_multi(None, ["one", "two", "three"])
        assert [c[0] for c in calls] == ["two", "three"]

    def test_success_evidence_requires_framework_fact(self):
        from cmru.runner import _success_evidence
        assert _success_evidence(["noise", "ERROR: no tests"]) is None
        assert "passed" in _success_evidence(["===== 2 passed in 0.1s ====="])
        assert "Ran 2 tests" in _success_evidence(["Ran 2 tests in 0.1s", "OK"])


class TestHandlerSafetyContracts:
    def test_prerequisites_and_cgroup_fail_closed(self, monkeypatch):
        import cmru.handlers as h
        monkeypatch.delenv("CMRU_WHEEL_BUILDER_IMAGE", raising=False)
        with pytest.raises(SystemExit): h._check_build_prerequisites()
        monkeypatch.setenv("CMRU_WHEEL_BUILDER_IMAGE", "builder")
        monkeypatch.setattr(h.shutil, "which", lambda _: None)
        with pytest.raises(SystemExit): h._check_build_prerequisites()
        monkeypatch.delenv("CMRU_DOCKER_CGROUP_PARENT", raising=False); monkeypatch.delenv("CGROUP_PARENT_DEV_BACKGROUND", raising=False)
        with pytest.raises(SystemExit): h._docker_cgroup_parent()

    def test_mount_mapping_uses_longest_mount_and_refuses_unknown(self, monkeypatch, tmp_path):
        import cmru.handlers as h
        info = "2 2 0:2 /deep /work rw - x x\n"
        monkeypatch.setattr(h.Path, "read_text", lambda *a, **k: info)
        assert h._host_bind_source(Path("/work/src")) == "/deep/src"
        with pytest.raises(RuntimeError, match="no matching"):
            h._host_bind_source(Path("/unknown"))

    def test_repack_rejection_precedes_any_external_prerequisite(self):
        import cmru.handlers as h
        with pytest.raises(SystemExit): h._reject_experimental_repack(True)
        h._reject_experimental_repack(False)

    def test_wheel_publish_rejects_missing_extra_and_wheel_build_cleans_stale(self, monkeypatch, tmp_path):
        import argparse
        import cmru.handlers as h
        args = argparse.Namespace(cwd=str(tmp_path), prefix="demo", glob=None, notes_env=None, extra_asset=[str(tmp_path / "missing")])
        (tmp_path / "dist").mkdir()
        import zipfile
        with zipfile.ZipFile(tmp_path / "dist/demo-1.whl", "w") as wheel:
            wheel.writestr("demo-1.dist-info/METADATA", "Metadata-Version: 2.1\nVersion: 1\n")
        for key, val in [("GITHUB_PUSH_PAT", "t"), ("GITHUB_USERNAME", "u"), ("GITHUB_REPO", "r")]: monkeypatch.setenv(key, val)
        with pytest.raises(SystemExit, match="matched no"):
            h.cmd_wheel_publish(args)
        monkeypatch.setenv("CMRU_WHEEL_BUILDER_IMAGE", "builder"); monkeypatch.setenv("CMRU_DOCKER_CGROUP_PARENT", "dev.slice")
        monkeypatch.setattr(h.shutil, "which", lambda _: "/docker")
        monkeypatch.setattr(h, "_git_common_dir", lambda p: Path("/repo/.git"))
        monkeypatch.setattr(h, "_host_bind_source", lambda p: "/host" + str(p))
        monkeypatch.setattr(h, "_wheel_builder_git_mount_args", lambda *a, **k: [])
        seen = []
        monkeypatch.setattr(h.subprocess, "run", lambda argv, **kw: seen.append(argv))
        h.cmd_wheel_build(argparse.Namespace(cwd=str(tmp_path)))
        assert not list((tmp_path / "dist").glob("*.whl"))
        assert "--cgroup-parent" in seen[0]

    def test_oci_build_repack_and_prerequisite_paths_are_observable(self, monkeypatch, tmp_path):
        import argparse
        import cmru.handlers as h
        args = argparse.Namespace(cwd=str(tmp_path), bake_file="b.hcl", target="img", repack=False)
        monkeypatch.setattr(h.shutil, "which", lambda _: "/docker")
        monkeypatch.setattr(h.subprocess, "run", lambda *a, **k: None)
        for key, val in [("REGISTRY", "ghcr.io"), ("GITHUB_USERNAME", "u"), ("GITHUB_PUSH_PAT", "p")]: monkeypatch.setenv(key, val)
        h.cmd_oci_image_build(args)
        h.cmd_oci_image_push(args)


class TestBundleAndChangelogBoundaries:
    def test_bundle_config_copy_and_archive_paths_are_real_filesystem_operations(self, tmp_path, monkeypatch):
        from cmru.bundle import parse_config, run_bundle
        project = tmp_path / "project"; project.mkdir()
        (project / "README.md").write_text("readme\n")
        (project / "src").mkdir(); (project / "src/app.py").write_text("x=1\n")
        cfg = project / "bundle.toml"
        cfg.write_text('''project_root = "."\ndist_dir = "dist"\nbundle_dir = "bundle"\n[archive]\nname_template = "demo-{version}.tar.gz"\nversion_env = "VERSION"\nformat = "gztar"\n[copy]\nfiles = ["README.md"]\ndirs = ["src"]\n''')
        parsed = parse_config(cfg)
        assert parsed.archive_format == "gztar"
        monkeypatch.setenv("VERSION", "1.0")
        out = run_bundle(cfg)
        assert out.exists() and out.name == "demo-1.0.tar.gz"

    @pytest.mark.parametrize("text,needle", [("", "project_root"), ("project_root='.'\n", "archive"),
                                               ("project_root='.'\n[archive]\nname_template='x'\nversion_env='V'\n[copy]\nfiles='bad'\n", "lists")])
    def test_bundle_config_rejects_incomplete_contract(self, tmp_path, text, needle):
        from cmru.bundle import parse_config
        path = tmp_path / "bundle.toml"; path.write_text(text)
        with pytest.raises((ValueError, FileNotFoundError), match=needle):
            parse_config(path)

    def test_changelog_path_validation_and_render_empty_metadata(self, tmp_path):
        from cmru.changelog import _render_section, _validate_changelog_path
        project = SimpleNamespace(name="demo", cwd="demo", changelog="CHANGES.md")
        assert _validate_changelog_path(project, tmp_path) == tmp_path / "demo/CHANGES.md"
        for bad in ("../CHANGES.md", "/tmp/CHANGES.md", ""):
            project.changelog = bad
            with pytest.raises(RuntimeError): _validate_changelog_path(project, tmp_path)
        rendered = _render_section("1.0.0", {}, source_end="abc", date="2024-01-01")
        assert "Release metadata prepared by CMRU" in rendered and "source-end=abc" in rendered
