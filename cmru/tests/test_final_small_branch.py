"""A final no-op standards update witness after current coverage."""
from pathlib import Path
from types import SimpleNamespace

from cmru import standards


def test_standards_update_reloads_when_revision_was_already_current(monkeypatch, tmp_path, capsys):
    project_root = tmp_path / "demo"
    project_root.mkdir()
    (project_root / "cmru.toml").write_text(
        "[project]\ntemplate_revision = 4\n", encoding="utf-8"
    )
    project = SimpleNamespace(
        project_root=project_root,
        template_revision=4,
        changelog="CHANGES.md",
        steps={},
        runner_steps={},
        env={},
    )
    loaded = (tmp_path, {"demo": project}, [])
    monkeypatch.setattr("cmru.cli._resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr("cmru.cli.load_config", lambda _: loaded)
    standards.standards_main(["--project", "demo", "--update"])
    assert "CMRU standards: 1 project(s) conform" in capsys.readouterr().out
