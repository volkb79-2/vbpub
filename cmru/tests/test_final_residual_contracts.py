"""High-signal witnesses for final small operational residuals."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import bundle, changelog, resolve, tester_gate
from cmru.agent import cli as agent_cli
from cmru.agent.consul_backend import ConsulBackend


def test_changelog_subject_groups_classifies_conventional_types(monkeypatch, tmp_path):
    monkeypatch.setattr(changelog, "_git", lambda *args, **kwargs: "a1\x1ffeat: add\na2\x1ffix: fix\na3\x1fdocs: note\na4\x1frefactor: change")
    groups = changelog._subject_groups(tmp_path, "tag", ["demo"], exclude_paths=[])
    assert groups == {
        "Added": ["feat: add (a1)"], "Fixed": ["fix: fix (a2)"],
        "Documentation": ["docs: note (a3)"], "Changed": ["refactor: change (a4)"],
    }


def test_changelog_validate_path_rejects_symlink_escape(tmp_path):
    project_root = tmp_path / "demo"; project_root.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    (project_root / "link").symlink_to(outside, target_is_directory=True)
    project = SimpleNamespace(name="demo", cwd="demo", changelog="link/CHANGES.md")
    with pytest.raises(RuntimeError, match="escapes"):
        changelog._validate_changelog_path(project, tmp_path)


def test_bundle_parse_config_requires_archive_and_copy_tables(tmp_path):
    path = tmp_path / "cfg.toml"
    path.write_text("project_root='.'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="archive"):
        bundle.parse_config(path)
    path.write_text("project_root='.'\n[archive]\nname_template='x-{version}'\nversion_env='V'\n[copy]\nfiles='bad'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be lists"):
        bundle.parse_config(path)


def test_agent_cli_parser_requires_known_agent_verb_and_preserves_options():
    args = agent_cli._build_parser().parse_args(["--scope", "system", "once", "--release-root", "/srv/cmru"])
    assert args.verb == "once" and args.scope == "system" and args.release_root == "/srv/cmru"
    with pytest.raises(SystemExit):
        agent_cli._build_parser().parse_args(["unknown"])


def test_resolve_format_env_omits_absent_digest_and_url_is_empty():
    result = {"version": "1.0.0", "tag": "demo-v1.0.0", "url": None}
    formatted = resolve.format_result(result, "env")
    assert "DEMO_VERSION=1.0.0" in formatted and "SHA256" not in formatted
    assert resolve.format_result({}, "url") == ""


def test_consul_observed_and_signature_fail_closed_on_invalid_base64():
    backend = ConsulBackend()
    backend._get = lambda *args, **kwargs: (200, b'[{"Value":"!!!"}]', {})
    assert backend.read_observed("n", "l") is None
    assert backend.read_desired_sig("n", "l") is None


def test_tester_gate_required_resource_resolution_prefers_explicit(monkeypatch):
    monkeypatch.setenv("CMRU_TESTER_CPUS", "2")
    assert tester_gate.resolve_cpus("1") == "1"
    assert tester_gate.resolve_cpus(None) == "2"
    monkeypatch.delenv("CMRU_TESTER_CPUS")
    with pytest.raises(SystemExit, match="CPU limit"):
        tester_gate.resolve_cpus(None)
