from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from project_fixture import copy_project_fixture  # noqa: E402


def test_disposable_project_fixture_copies_shared_libraries_and_estate_configs(tmp_path):
    repo_root = tmp_path / "repo"
    project_root = repo_root / "cmru"
    workspace = tmp_path / "scratch"
    workspace.mkdir()

    for filename in (
        "cmru.orchestration.sample.toml",
        "cmru.orchestration.toml",
        "cmru.project.sample.toml",
        "wheel-builder/Dockerfile",
        "docs/ciu-vs-cmru.md",
        "docs/RELEASE-TOOLING.md",
        "docs/plan-cmru-release-modes.md",
        "run-gate-project/CONSUMERS.md",
        "topos/cmru.toml",
        "nyxloom/cmru.toml",
        "tls-edge/cmru.toml",
        "tls-edge/scripts/build-artifact.sh",
    ):
        path = repo_root / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")

    (project_root / "src/cmru").mkdir(parents=True)
    (project_root / "src/cmru/__init__.py").write_text("", encoding="utf-8")
    for library, package in (
        ("cli-extended", "cli_extended"),
        ("worktree", "worktree"),
    ):
        package_file = repo_root / "libraries" / library / "src" / package / "__init__.py"
        package_file.parent.mkdir(parents=True, exist_ok=True)
        package_file.write_text("", encoding="utf-8")

    copied_project = copy_project_fixture(
        repo_root=repo_root,
        project_root=project_root,
        workspace=workspace,
    )

    assert (copied_project / "src/cmru/__init__.py").is_file()
    assert (workspace / "libraries/cli-extended/src/cli_extended/__init__.py").is_file()
    assert (workspace / "libraries/worktree/src/worktree/__init__.py").is_file()
    assert (workspace / "topos/cmru.toml").is_file()
    assert (workspace / "nyxloom/cmru.toml").is_file()
    assert (workspace / "tls-edge/cmru.toml").is_file()
    assert (workspace / "tls-edge/scripts/build-artifact.sh").is_file()
