import json
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from tests._cx_paths import CX_LIBRARY

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import coverage_canary  # noqa: E402
import mutation_campaign  # noqa: E402
import project_fixture  # noqa: E402
from project_fixture import copy_project_fixture  # noqa: E402

# A stand-in for the real coverage command: like pytest collecting the real
# suite, it fails with exit 2 when a repository-root file it reads is absent,
# and it refuses the canary's appended function as pytest-cov's fail-under does.
FAKE_COVERAGE_COMMAND = textwrap.dedent('''\
    import pathlib
    import sys

    project = pathlib.Path.cwd()
    for needed in sys.argv[2:]:
        if not (project.parent / needed).is_file():
            print(f"ERROR collecting: No such file or directory: {project.parent / needed}")
            sys.exit(2)
    if "_cmru_coverage_canary_unreached" in (project / "src/cmru/config.py").read_text():
        if sys.argv[1] == "coverage":
            print("FAIL Required test coverage of 100% not reached. Total coverage: 99.98%")
        else:
            print("1 failed")
        sys.exit(1)
    print("1 passed")
''')


#: Tracked files of each SUITE_COLLECTION_INPUTS pathspec in the estate below.
SUPPORT_FILES = (
    "tester-unified/Dockerfile",
    "nyxloom/pyproject.toml",
    "nyxloom/src/nyxloom/__init__.py",
    "nyxloom/src/nyxloom/templates/handoff.md",
    ".gitignore",
    "run-gate-project/cmru.toml",
    "assay/src/assay/__init__.py",
    "assay/src/assay/git.py",
    "docs/BOOTSTRAP-FROM-ZERO.md",
)


def _write(path: Path, text: str = "fixture\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _estate(repo_root: Path) -> Path:
    """A Git worktree shaped like vbpub around the CMRU project."""
    project_root = repo_root / "cmru"
    for filename in (
        *project_fixture.ROOT_CMRU_ARTIFACTS,
        "wheel-builder/Dockerfile",
        "docs/ciu-vs-cmru.md",
        "docs/RELEASE-TOOLING.md",
        "docs/plan-cmru-release-modes.md",
        "run-gate-project/CONSUMERS.md",
        "topos/cmru.toml",
        "nyxloom/cmru.toml",
        # Suite collection inputs, and their tracked siblings that must stay out:
        # gen-requirements.py and build-image.sh are the tester modules' skip keys.
        "tester-unified/Dockerfile",
        "tester-unified/gen-requirements.py",
        "tester-unified/build-image.sh",
        "nyxloom/pyproject.toml",
        "nyxloom/src/nyxloom/__init__.py",
        "nyxloom/src/nyxloom/templates/handoff.md",
        "nyxloom/tests/test_nyxloom.py",
        "run-gate-project/cmru.toml",
        "run-gate-project/tools/release_candidate.py",
        "assay/src/assay/__init__.py",
        "assay/src/assay/git.py",
        "assay/tests/test_assay.py",
        "docs/BOOTSTRAP-FROM-ZERO.md",
        # Registered estate configs beyond the declared two stay out: the full set
        # (incl. cli-extended's and tls-edge's) switches estate tests out of their skip.
        "libraries/cli-extended/cmru.toml",
        "tls-edge/cmru.toml",
        "ciu/cmru.toml",
    ):
        _write(repo_root / filename)
    _write(project_root / "pyproject.toml", "[tool.pytest.ini_options]\n")
    _write(project_root / "src/cmru/__init__.py", "")
    _write(project_root / "src/cmru/config.py", "VALUE = 1\n")
    _write(project_root / "check_coverage.py", FAKE_COVERAGE_COMMAND)
    (project_root / "config-link.py").symlink_to("src/cmru/config.py")
    for library, package in (
        ("cli-extended", "cli_extended"),  # present in the repo, must NOT be copied
        ("worktree", "worktree"),
    ):
        _write(repo_root / "libraries" / library / "src" / package / "__init__.py", "")
    _write(repo_root / ".gitignore", "*.egg-info/\n")
    # The fixture copies only TRACKED inputs, resolved through a real Git worktree.
    subprocess.run(["git", "init", "-q", str(repo_root)], check=True)
    subprocess.run(["git", "-C", str(repo_root), "add", "-A"], check=True)
    _write(repo_root / "nyxloom/src/nyxloom/scratch.py", "UNTRACKED = True\n")
    _write(repo_root / "nyxloom/src/nyxloom.egg-info/PKG-INFO", "Name: nyxloom\n")
    _write(repo_root / "tester-unified/local-wheel/cli_extended-1-py3-none-any.whl", "wheel\n")
    return project_root


def test_disposable_project_fixture_copies_worktree_library_never_cli_extended_and_estate_configs(tmp_path):
    project_root = _estate(tmp_path / "repo")
    workspace = tmp_path / "scratch"
    workspace.mkdir()

    copied_project = copy_project_fixture(
        repo_root=tmp_path / "repo",
        project_root=project_root,
        workspace=workspace,
    )

    assert (copied_project / "src/cmru/__init__.py").is_file()
    # CX-D1: cli-extended is a wheel dependency of the gate environment; a fixture
    # (and so every mutated candidate) never carries a vendored copy of its source.
    assert not (workspace / CX_LIBRARY).exists()
    assert (workspace / "libraries/worktree/src/worktree/__init__.py").is_file()
    assert (workspace / "topos/cmru.toml").is_file()
    assert (workspace / "nyxloom/cmru.toml").is_file()


def test_fixture_carries_the_suite_collection_inputs_and_keeps_skip_keys_and_untracked_state_out(tmp_path):
    repo_root = tmp_path / "repo"
    project_root = _estate(repo_root)
    workspace = tmp_path / "scratch"

    copied_project = copy_project_fixture(
        repo_root=repo_root, project_root=project_root, workspace=workspace,
    )

    # Read by the full suite in the fixture (see SUITE_COLLECTION_INPUTS).
    for relative in SUPPORT_FILES:
        assert (workspace / relative).read_bytes() == (repo_root / relative).read_bytes(), relative
    # Their presence would switch suite modules out of their isolated-tree skip.
    assert not (workspace / "tester-unified/gen-requirements.py").exists()
    assert not (workspace / "tester-unified/build-image.sh").exists()
    assert not (workspace / "run-gate-project/tools").exists()
    assert not (workspace / "tls-edge").exists()
    assert not (workspace / "ciu").exists()
    # Nothing beyond the declared pathspecs, nothing untracked or ignored.
    assert not (workspace / "assay/tests").exists()
    assert not (workspace / "nyxloom/tests").exists()
    assert not (workspace / "nyxloom/src/nyxloom/scratch.py").exists()
    assert not (workspace / "nyxloom/src/nyxloom.egg-info").exists()
    assert not (workspace / "tester-unified/local-wheel").exists()
    assert not (workspace / CX_LIBRARY).exists()
    # A tracked symlink keeps its link text rather than becoming a copy.
    assert (copied_project / "config-link.py").is_symlink()
    assert (copied_project / "config-link.py").readlink() == Path("src/cmru/config.py")


@pytest.mark.parametrize("relative", [path.as_posix() for path in project_fixture.SUITE_COLLECTION_INPUTS])
def test_fixture_refuses_when_a_suite_collection_input_is_not_tracked(tmp_path, relative):
    repo_root = tmp_path / "repo"
    project_root = _estate(repo_root)
    subprocess.run(["git", "-C", str(repo_root), "rm", "-rq", "--cached", relative], check=True)

    with pytest.raises(ValueError, match=f"required tracked CMRU fixture inputs are missing: {relative}$"):
        copy_project_fixture(repo_root=repo_root, project_root=project_root, workspace=tmp_path / "scratch")


def test_mutation_manifest_pins_the_suite_collection_inputs_it_copies(tmp_path):
    repo_root = tmp_path / "repo"
    project_root = _estate(repo_root)
    workspace = tmp_path / "scratch"
    copy_project_fixture(repo_root=repo_root, project_root=project_root, workspace=workspace)

    manifest = mutation_campaign._test_manifest(repo_root, Path("cmru"))

    assert set(SUPPORT_FILES) <= set(manifest["fixture_files"])
    assert mutation_campaign._fixture_matches_manifest(
        template_root=workspace, project_prefix=Path("cmru"), manifest=manifest,
    )
    (workspace / "nyxloom/src/nyxloom/__init__.py").write_text("drift\n", encoding="utf-8")
    assert not mutation_campaign._fixture_matches_manifest(
        template_root=workspace, project_prefix=Path("cmru"), manifest=manifest,
    )


def _canary(project_root: Path, evidence: Path, mode: str, *needed: str) -> int:
    return coverage_canary.run([
        "--repo-root", str(project_root.parent),
        "--project-root", str(project_root),
        "--target", "src/cmru/config.py",
        "--evidence", str(evidence),
        "--", sys.executable, "check_coverage.py", mode, *needed,
    ])


def test_canary_control_passes_and_the_uncovered_candidate_fails_for_the_coverage_reason(tmp_path):
    project_root = _estate(tmp_path / "repo")
    evidence = tmp_path / "canary.json"
    needed = [
        "tester-unified/Dockerfile", "nyxloom/pyproject.toml", "nyxloom/src/nyxloom/__init__.py",
    ]

    assert _canary(project_root, evidence, "coverage", *needed) == 0

    document = json.loads(evidence.read_text(encoding="utf-8"))
    assert document["control_exit_code"] == 0
    assert document["transformed_exit_code"] == 1
    assert document["expected_reason"] == "Required test coverage of 100% not reached"
    assert document["observed_expected_reason"] is True
    assert "_cmru_coverage_canary_unreached" not in (project_root / "src/cmru/config.py").read_text()


def test_canary_reports_a_missing_support_input_as_a_control_failure_never_a_coverage_refusal(tmp_path):
    project_root = _estate(tmp_path / "repo")
    evidence = tmp_path / "canary.json"

    # Tracked in the repository but not a fixture input: the suite cannot collect.
    with pytest.raises(RuntimeError, match="known-good canary control failed with exit 2") as raised:
        _canary(project_root, evidence, "coverage", "tester-unified/gen-requirements.py")

    assert "No such file or directory" in str(raised.value)
    assert not evidence.exists()


def test_canary_refuses_an_uncovered_candidate_that_fails_for_another_reason(tmp_path):
    project_root = _estate(tmp_path / "repo")
    evidence = tmp_path / "canary.json"

    with pytest.raises(RuntimeError, match="did not fail for the expected coverage reason"):
        _canary(project_root, evidence, "other")

    document = json.loads(evidence.read_text(encoding="utf-8"))
    assert document["control_exit_code"] == 0
    assert document["transformed_exit_code"] == 1
    assert document["observed_expected_reason"] is False
