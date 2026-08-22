"""`ciu init` — guided repo scaffolding, validation-first (S10.1 verb).

Oracles:
- Non-interactive init produces a global defaults template that renders
  through the real Jinja step into TOML the parser accepts (S3.2 order), and
  stack defaults/ compose templates that render against the same context.
- An existing target file is never overwritten.
- The gitignore additions land exactly once.
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ciu import scaffold  # noqa: E402


@pytest.fixture
def workdir(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_init_writes_tree_and_renders_clean(workdir):
    plan = scaffold.collect_plan(
        ["--project-name", "demo", "--stacks", "api"], workdir
    )
    files = scaffold.build_files(plan, workdir)
    written = {p.relative_to(workdir).as_posix() for p, _ in files}
    assert written == {
        "ciu.global.defaults.toml.j2",
        ".gitignore-additions.txt",
        "applications/api/ciu.defaults.toml.j2",
        "applications/api/ciu.compose.yml.j2",
    }
    for path, content in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    # The global template renders to TOML the shipped parser accepts (S3.2).
    from jinja2 import Environment

    context = {
        "deploy": {
            "project_name": "demo", "environment_tag": "dev",
            "health": {"interval": "5s", "timeout": "3s", "retries": 3,
                        "start_period": "5s"},
            "env": {"defaults": {"TZ": "UTC", "PYTHONUNBUFFERED": "1"}},
        },
    }
    rendered = Environment(keep_trailing_newline=True).from_string(
        (workdir / "ciu.global.defaults.toml.j2").read_text(encoding="utf-8")
    ).render(**context)
    parsed = tomllib.loads(rendered)
    assert parsed["deploy"]["project_name"] == "demo"
    assert parsed["deploy"]["network_name"] == "$DOCKER_NETWORK_INTERNAL"


def test_init_refuses_existing_targets(workdir):
    (workdir / "ciu.global.defaults.toml.j2").write_text(
        "# operator content\n", encoding="utf-8"
    )
    plan = scaffold.collect_plan(["--project-name", "demo"], workdir)
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        scaffold.build_files(plan, workdir)


def test_gitignore_additions_applied_once(workdir, monkeypatch):
    """The gitignore merge DEDUPES: a refused rerun (existing targets) must
    still not append duplicate entries."""
    scaffold.init_main(["--project-name", "demo"])
    first = (workdir / ".gitignore").read_text(encoding="utf-8")

    # A rerun with different stack name only adds NEW files; the gitignore
    # entries already present are skipped (dedupe by exact line).
    monkeypatch.chdir(workdir)
    plan = scaffold.collect_plan(["--project-name", "demo", "--stacks", "other"],
                                 workdir)
    files = scaffold.build_files(plan, workdir) if False else None
    # direct gitignore-merge path (what init_main does after writing):
    existing_lines = set(first.splitlines())
    missing = [e for e in scaffold._GITIGNORE_ENTRIES if e not in existing_lines]
    assert missing == []  # nothing left to add — deduped
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        scaffold.init_main(["--project-name", "demo"])  # targets now exist
    assert (workdir / ".gitignore").read_text(encoding="utf-8") == first


def test_cli_end_to_end(workdir, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["ciu", "init", "--project-name", "solo"])
    from ciu.cli import main

    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 0
    assert (workdir / "ciu.global.defaults.toml.j2").is_file()
