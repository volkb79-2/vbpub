"""Behavioral witnesses for residual bundle and changelog branches."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import bundle, changelog


def test_bundle_allowlist_skips_excluded_entry_and_keeps_extra_member(tmp_path):
    root = tmp_path / "project"; root.mkdir()
    (root / "minisign.key").write_text("secret")
    extra = bundle.BundleMember("generated.json", content=b"generated")
    members = bundle.collect_allowlist_members(root, ["minisign.key"], extra_members=[extra])
    assert [member.archive_path for member in members] == ["generated.json"]


def test_bundle_parse_config_rejects_non_table_wheel_and_defaults_none(tmp_path):
    path = tmp_path / "cfg.toml"
    base = "project_root='.'\n[archive]\nname_template='x-{version}'\nversion_env='V'\n[copy]\nfiles=[]\ndirs=[]\n"
    path.write_text(base, encoding="utf-8")
    assert bundle.parse_config(path).wheel_find_links is None
    path.write_text(
        "project_root='.'\nwheel='bad'\n[archive]\nname_template='x-{version}'\n"
        "version_env='V'\n[copy]\nfiles=[]\ndirs=[]\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match=r"\[wheel\] must be a table"):
        bundle.parse_config(path)


def test_bundle_copy_sources_ignores_excluded_file_and_requires_existing_source(tmp_path):
    root = tmp_path / "project"; root.mkdir()
    out = root / "bundle"; out.mkdir()
    (root / "secret.key").write_text("secret")
    cfg = bundle.BundleConfig(root, root, root / "dist", out, root / "client", False, "python", None, "x-{version}", "V", "gztar", ["secret.key"], [])
    bundle.copy_sources(cfg)
    assert not (out / "secret.key").exists()
    missing = bundle.BundleConfig(root, root, root / "dist", out, root / "client", False, "python", None, "x-{version}", "V", "gztar", ["missing.py"], [])
    with pytest.raises(FileNotFoundError, match="source file"):
        bundle.copy_sources(missing)


def test_changelog_rejects_hand_authored_duplicate_generated_heading(tmp_path, monkeypatch):
    project_root = tmp_path / "demo"; project_root.mkdir()
    path = project_root / "CHANGES.md"
    path.write_text("# Changelog\n\n<!-- cmru: release history -->\n## [1.0.1] - 2026-01-01\n<!-- hand-authored -->\n", encoding="utf-8")
    project = SimpleNamespace(name="demo", cwd="demo", paths=["demo"], prefix="demo-v", git_tag=True, changelog="CHANGES.md", version=SimpleNamespace(strategy="scm"))
    monkeypatch.setattr(changelog, "_project_release_plan", lambda *a, **k: ("1.0.1", "demo-v1.0.0"))
    monkeypatch.setattr(changelog, "_git", lambda *a, **k: "a" * 40)
    monkeypatch.setattr(changelog, "_subject_groups", lambda *a, **k: {"Fixed": ["fix: issue"]})
    with pytest.raises(RuntimeError, match="hand-authored"):
        changelog.generate_release_changelog(tmp_path, project)
