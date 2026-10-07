"""CLI-18: every shipped config template loads through the real config loader.

The README calls ``templates/cmru.toml.tmpl`` "ready-to-copy"; it used to fail
with ``[runtime] is required``.  These tests render/copy each shipped template
exactly as an adopter (or ``cmru init``) would and run the production reader.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from cmru import scaffold
from cmru.config import load_forge_config

ROOT = Path(__file__).resolve().parents[1]
REPO_TEMPLATES = ROOT / "templates"
PACKAGE_TEMPLATES = ROOT / "src" / "cmru" / "templates"

# Every shipped template must appear here: adding one without coverage fails.
REPO_COVERED = {"cmru.toml.tmpl", "cmru.orchestration.toml.tmpl"}
PACKAGE_COVERED = {"get.py.tmpl", "orchestration.toml", "project-wheel.toml"}


def test_the_shipped_template_inventory_is_fully_covered():
    assert {p.name for p in REPO_TEMPLATES.iterdir()} == REPO_COVERED
    assert {p.name for p in PACKAGE_TEMPLATES.iterdir()} == PACKAGE_COVERED


def test_get_py_template_has_one_copy_the_package_resource():
    # CLI-18: a byte-identical duplicate used to live in templates/.
    assert not (REPO_TEMPLATES / "get.py.tmpl").exists()
    assert (PACKAGE_TEMPLATES / "get.py.tmpl").is_file()


def test_ready_to_copy_project_template_loads_standalone(tmp_path):
    (tmp_path / "cmru.toml").write_text(
        (REPO_TEMPLATES / "cmru.toml.tmpl").read_text(encoding="utf-8"), encoding="utf-8",
    )

    forge = load_forge_config(tmp_path / "cmru.toml")

    assert list(forge.projects) == ["example"]
    assert forge.projects["example"].runtime_kind == "none"


def test_ready_to_copy_project_and_orchestration_templates_load_as_a_pair(tmp_path):
    (tmp_path / "example").mkdir()
    project_text = (REPO_TEMPLATES / "cmru.toml.tmpl").read_text(encoding="utf-8")
    # The template's own header tells monorepo adopters to drop these tables.
    assert "delete the [github] and\n# [targets] tables" in project_text
    project_text = re.sub(r"\n\[github\].*?\n\[targets\].*?\n\n", "\n", project_text, flags=re.S)
    (tmp_path / "example" / "cmru.toml").write_text(project_text, encoding="utf-8")
    central = tmp_path / "cmru.orchestration.toml"
    central.write_text(
        (REPO_TEMPLATES / "cmru.orchestration.toml.tmpl").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    forge = load_forge_config(central, require_orchestration=True)

    assert forge.orchestration.project_order == ["example"]
    assert list(forge.projects) == ["example"]


@pytest.mark.parametrize("centralized", [False, True])
def test_cmru_init_project_template_loads(tmp_path, centralized):
    text = scaffold.render_project_toml(
        project_id="demo", description="Demo.", owner="o", repo="r", owner_type="user",
        generated_by="test", centralized=centralized,
    )
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo" / "cmru.toml").write_text(text, encoding="utf-8")
    if not centralized:
        forge = load_forge_config(tmp_path / "demo" / "cmru.toml")
    else:
        central = tmp_path / "cmru.orchestration.toml"
        central.write_text(
            scaffold.render_orchestration_toml(
                [{"id": "demo", "config": "demo/cmru.toml"}],
                owner="o", repo="r", generated_by="test",
            ),
            encoding="utf-8",
        )
        forge = load_forge_config(central, require_orchestration=True)

    assert list(forge.projects) == ["demo"]


def test_generated_orchestration_template_does_not_emit_the_deprecated_key(tmp_path, capsys):
    """CLI-04: neither the scaffold nor the ready-to-copy file sets default_projects."""
    rendered = scaffold.render_orchestration_toml(
        [{"id": "demo", "config": "demo/cmru.toml"}],
        owner="o", repo="r", generated_by="test",
    )
    assert "default_projects" not in rendered
    assert "default_projects" not in (
        REPO_TEMPLATES / "cmru.orchestration.toml.tmpl"
    ).read_text(encoding="utf-8")
