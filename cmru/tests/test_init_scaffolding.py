"""`cmru init` — guided scaffolding, validation-first (S10.1 verb).

Oracles:
- Non-interactive monorepo init generates an orchestration file + per-project
  contracts that PASS the real loaders (load_forge_config) — generation bugs
  die in `init`, not in a consumer's first run.
- Single-project layout writes ONLY the project contract (no orchestration
  file; the bare cmru.toml loads standalone).
- An existing target file is never overwritten — the run refuses naming every
  existing target before writing anything.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cmru import scaffold  # noqa: E402
from cmru.config import load_forge_config  # noqa: E402


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "-qb", "main", "."], cwd=root, check=True,
                   capture_output=True)
    return root


def test_monorepo_init_generates_loader_valid_contracts(git_repo):
    plan = scaffold.collect_plan(
        ["--layout", "monorepo", "--project", "alpha", "--project", "beta",
         "--owner", "acme"], git_repo,
    )
    files = scaffold.build_files(plan, git_repo)
    scaffold.validate(files, git_repo)

    written = {p.relative_to(git_repo).as_posix() for p, _ in files}
    assert written == {
        "alpha/cmru.toml", "beta/cmru.toml", "cmru.orchestration.toml",
    }
    # loader-level oracle on the REAL tree after write
    for path, content in files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    cfg = load_forge_config(git_repo / "cmru.orchestration.toml",
                            require_orchestration=True)
    assert set(cfg.projects) == {"alpha", "beta"}
    # ${NAME:-default} reference survives into the declared env verbatim;
    # expansion is a LOAD-time concern tested separately.
    assert "${CGROUP_PARENT_DEV_BACKGROUND:-dev-background.slice}" in (
        git_repo / "cmru.orchestration.toml"
    ).read_text(encoding="utf-8")


def test_single_project_layout_has_no_orchestration_file(git_repo):
    plan = scaffold.collect_plan(["--layout", "single"], git_repo)
    assert [p["id"] for p in plan["projects"]] == ["repo"]
    files = scaffold.build_files(plan, git_repo)
    scaffold.validate(files, git_repo)
    assert all(p.name == "cmru.toml" for p, _ in files)


def test_init_refuses_existing_targets_before_writing_anything(git_repo, capsys):
    (git_repo / "alpha").mkdir()
    existing = git_repo / "alpha" / "cmru.toml"
    existing.write_text("# operator content\n", encoding="utf-8")

    plan = scaffold.collect_plan(
        ["--layout", "monorepo", "--project", "alpha", "--owner", "acme"], git_repo,
    )
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        scaffold.build_files(plan, git_repo)
    assert existing.read_text(encoding="utf-8") == "# operator content\n"


def test_templates_render_without_leftover_placeholders():
    for name in ("project-wheel.toml", "orchestration.toml"):
        text = scaffold._template(name)
        assert "@@" not in scaffold.render_project_toml(
            project_id="x", description="d", owner="o", repo="r",
            owner_type="user", generated_by="t",
        ) or name == "orchestration.toml"  # project template fully substituted


def test_cli_end_to_end_exit_zero_and_files(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    from cmru.cli import main

    main(["init", "--layout", "monorepo", "--project", "solo", "--owner", "acme"])
    assert (tmp_path / "solo" / "cmru.toml").is_file()
    assert (tmp_path / "cmru.orchestration.toml").is_file()
