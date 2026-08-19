"""Tests for [[project.tool_dependencies]] parsing (S2.6/S15.1).

Structural/local validation only -- config.py cannot know whether ``project``
names a real sibling in the estate (a project document stays portable to a
fresh repository root); that cross-project check lives in
``cmru.dependencies.build_report`` and is covered by test_dependencies.py.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cmru.config import ToolDependency, load_forge_config

VALID_SHA = "6224f784f96f5ad9d10264a69dd69594639959c5eda847dcede822a7adc515bf"

MINIMAL_GITHUB = """
schema_version = 1

[github]
owner = "octocat"
repo = "demo"
owner_type = "user"

[targets]
host = "github"
registry = []
"""


def _base_toml(extra_project: str = "") -> str:
    return (
        MINIMAL_GITHUB
        + """
[project]
id = "naf"
description = "test product"
template_revision = 4
prefix    = "naf-v"
artifacts = ["wheel"]
"""
        + extra_project
        + """
[project.version]
strategy = "scm"
bump = "conventional"

[project.release]
git_tag = true
build_step = "build"

[steps.run-tests]
quiet = true
commands = [{ label = "test", argv = ["true"], cwd = "." }]

[steps.build]
quiet = true
commands = [{ label = "build", argv = ["true"], cwd = "." }]

[steps.push]
quiet = true
commands = [{ label = "push", argv = ["true"], cwd = "." }]
"""
    )


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "cmru.toml"
    p.write_text(body)
    return p


def _valid_entry(**overrides) -> str:
    fields = {
        "project": "assay",
        "version": "1.0.0",
        "path": "tools/assay/assay-1.0.0.pyz",
        "sha256": VALID_SHA,
    }
    fields.update(overrides)
    lines = "\n".join(f'{key} = "{value}"' for key, value in fields.items())
    return f"[[project.tool_dependencies]]\n{lines}\n"


class TestToolDependencyParsing:
    def test_no_declaration_is_an_empty_list(self, tmp_path):
        cfg = _write(tmp_path, _base_toml())
        assert load_forge_config(cfg).projects["naf"].tool_dependencies == []

    def test_valid_entry_parses_into_a_tool_dependency(self, tmp_path):
        cfg = _write(tmp_path, _base_toml(_valid_entry()))
        deps = load_forge_config(cfg).projects["naf"].tool_dependencies
        assert deps == [
            ToolDependency(
                project="assay", version="1.0.0",
                path="tools/assay/assay-1.0.0.pyz", sha256=VALID_SHA,
            )
        ]

    def test_multiple_distinct_entries_parse_in_order(self, tmp_path):
        body = _valid_entry(project="assay") + _valid_entry(
            project="ciu", version="2.0.0", path="tools/ciu/ciu-2.0.0.pyz",
        )
        cfg = _write(tmp_path, _base_toml(body))
        deps = load_forge_config(cfg).projects["naf"].tool_dependencies
        assert [d.project for d in deps] == ["assay", "ciu"]

    def test_sha256_is_lowercased(self, tmp_path):
        cfg = _write(tmp_path, _base_toml(_valid_entry(sha256=VALID_SHA.upper())))
        deps = load_forge_config(cfg).projects["naf"].tool_dependencies
        assert deps[0].sha256 == VALID_SHA

    def test_tool_dependencies_must_be_an_array_of_tables(self, tmp_path):
        cfg = _write(tmp_path, _base_toml("tool_dependencies = 1\n"))
        with pytest.raises(SystemExit) as exc:
            load_forge_config(cfg)
        assert exc.value.code == 2

    def test_entry_must_be_a_table(self, tmp_path):
        cfg = _write(tmp_path, _base_toml("tool_dependencies = [1]\n"))
        with pytest.raises(SystemExit) as exc:
            load_forge_config(cfg)
        assert exc.value.code == 2

    def test_unknown_key_rejected(self, tmp_path):
        cfg = _write(tmp_path, _base_toml(_valid_entry(bogus="nope")))
        with pytest.raises(SystemExit) as exc:
            load_forge_config(cfg)
        assert exc.value.code == 2

    @pytest.mark.parametrize("missing", ["project", "version", "path", "sha256"])
    def test_missing_required_key_rejected(self, tmp_path, missing):
        fields = {
            "project": "assay", "version": "1.0.0",
            "path": "tools/assay/assay-1.0.0.pyz", "sha256": VALID_SHA,
        }
        del fields[missing]
        lines = "\n".join(f'{key} = "{value}"' for key, value in fields.items())
        cfg = _write(tmp_path, _base_toml(f"[[project.tool_dependencies]]\n{lines}\n"))
        with pytest.raises(SystemExit) as exc:
            load_forge_config(cfg)
        assert exc.value.code == 2

    def test_project_must_be_a_lowercase_identifier(self, tmp_path):
        cfg = _write(tmp_path, _base_toml(_valid_entry(project="Assay")))
        with pytest.raises(SystemExit) as exc:
            load_forge_config(cfg)
        assert exc.value.code == 2

    def test_project_may_not_declare_itself(self, tmp_path):
        cfg = _write(tmp_path, _base_toml(_valid_entry(project="naf")))
        with pytest.raises(SystemExit) as exc:
            load_forge_config(cfg)
        assert exc.value.code == 2

    def test_version_must_be_non_empty(self, tmp_path):
        cfg = _write(tmp_path, _base_toml(_valid_entry(version="")))
        with pytest.raises(SystemExit) as exc:
            load_forge_config(cfg)
        assert exc.value.code == 2

    def test_path_must_be_non_empty(self, tmp_path):
        cfg = _write(tmp_path, _base_toml(_valid_entry(path="")))
        with pytest.raises(SystemExit) as exc:
            load_forge_config(cfg)
        assert exc.value.code == 2

    @pytest.mark.parametrize("bad_path", ["/abs/path.pyz", "../escape.pyz", "."])
    def test_path_must_be_project_relative_and_not_escape_root(self, tmp_path, bad_path):
        cfg = _write(tmp_path, _base_toml(_valid_entry(path=bad_path)))
        with pytest.raises(SystemExit) as exc:
            load_forge_config(cfg)
        assert exc.value.code == 2

    @pytest.mark.parametrize("bad_sha", ["short", "z" * 64, "G" * 64, "6224f78" ])
    def test_sha256_must_be_64_hex_characters(self, tmp_path, bad_sha):
        cfg = _write(tmp_path, _base_toml(_valid_entry(sha256=bad_sha)))
        with pytest.raises(SystemExit) as exc:
            load_forge_config(cfg)
        assert exc.value.code == 2

    def test_duplicate_provider_declaration_rejected(self, tmp_path):
        body = _valid_entry(project="assay") + _valid_entry(project="assay", version="2.0.0")
        cfg = _write(tmp_path, _base_toml(body))
        with pytest.raises(SystemExit) as exc:
            load_forge_config(cfg)
        assert exc.value.code == 2
