from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import hashlib
from contextlib import contextmanager
from pathlib import Path
from typing import Mapping

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT_PATH = "gate/tests/test_changes_fold_preflight.py"
DOCS_TEST_PATH = "tests/core/test_docs_examples_and_vocabulary.py"
CURRENT_ONLY_NOTE = "run-local assay-injected `PYTHONPATH`"
FIRST_VERSION = "999.0.0"
HISTORY_MARKER = "<!-- cmru: release history -->"
GIT_HARDENING = (
    "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false",
    "-c", "gc.auto=0", "-c", "maintenance.auto=false",
)
GIT_INJECTION_KEYS = {
    "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CONFIG", "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS",
    "GIT_CONFIG_SYSTEM", "GIT_CONFIG_GLOBAL", "GIT_TEMPLATE_DIR",
    "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM",
}


def test_changes_fold_preflight_is_registered_in_tooling_tests():
    config = tomllib.loads((PROJECT_ROOT / "run-gate.toml").read_text(encoding="utf-8"))
    argv = config["lanes"]["tooling-tests"]["argv"]

    assert PREFLIGHT_PATH in "\n".join(argv)


@pytest.fixture
def current_sections_docs_check():
    """A fixture for the old docs-check shape that loses entries as history ages."""

    def check(changes: str) -> None:
        headings = list(re.finditer(
            r"^## \[([^\]]+)\](?: - \d{4}-\d{2}-\d{2})?$",
            changes,
            re.MULTILINE,
        ))
        sections = [
            (
                heading.group(1),
                changes[
                    heading.end():
                    headings[index + 1].start()
                    if index + 1 < len(headings)
                    else len(changes)
                ],
            )
            for index, heading in enumerate(headings)
        ]
        unreleased = next(
            (body for name, body in sections if name == "Unreleased"), ""
        )
        latest = next(
            (body for name, body in sections if name != "Unreleased"), ""
        )
        assert CURRENT_ONLY_NOTE in unreleased + latest, (
            f"{CURRENT_ONLY_NOTE!r} is not present in [Unreleased] or the newest release"
        )

    return check


def _cmru_release_functions():
    cmru_source = PROJECT_ROOT.parent / "cmru" / "src"
    sys.path.insert(0, str(cmru_source))
    try:
        from cmru.changelog import generate_release_changelog, pending_release_notes
        from cmru.cli import _fold_pending_release_notes
        from cmru.cli import load_config
        from cmru.changelog import _validate_history_structure
        from cmru.version import _project_scope_names
    except ImportError as exc:
        pytest.fail(
            "CMRU source cannot be imported in this test interpreter without "
            f"installing packages: {exc}"
        )
    return (
        generate_release_changelog,
        pending_release_notes,
        _fold_pending_release_notes,
        load_config,
        _validate_history_structure,
        _project_scope_names,
    )


def _minimal_git_environment(root: Path) -> dict[str, str]:
    home = root / "private-home"
    home.mkdir(parents=True, exist_ok=True)
    global_config = home / ".gitconfig"
    global_config.write_text("[core]\n\thooksPath = /dev/null\n", encoding="utf-8")
    env = {
        name: os.environ[name]
        for name in (
            "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TERM",
            "CMRU_TESTER_UNIFIED_IMAGE", "BUILDKIT_HOST", "BUILDX_BUILDER",
        )
        if os.environ.get(name)
    }
    env.update(
        HOME=str(home),
        XDG_CONFIG_HOME=str(home / ".config"),
        XDG_CACHE_HOME=str(home / ".cache"),
        XDG_STATE_HOME=str(home / ".state"),
        TMPDIR=os.environ.get("TMPDIR", tempfile.gettempdir()),
        PYTHONDONTWRITEBYTECODE="1",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=str(global_config),
        GIT_OPTIONAL_LOCKS="0",
        GIT_TERMINAL_PROMPT="0",
    )
    return env


@contextmanager
def _isolated_process_environment(env: Mapping[str, str]):
    previous = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


def _copy_docs_test_tree(repo_root: Path, destination: Path) -> Path:
    destination.mkdir()
    copied_assay = destination / "assay"
    shutil.copytree(
        repo_root / "assay",
        copied_assay,
        ignore=shutil.ignore_patterns(
            ".git",
            ".assay",
            ".pytest_cache",
            "__pycache__",
            "*.pyc",
            ".venv",
            "artifacts",
            "build",
            "dist",
            "evidence",
            "htmlcov",
            "logs",
            "vm-lab",
            "*.egg-info",
        ),
    )
    shutil.copytree(repo_root / "cmru" / "src", destination / "cmru" / "src")
    hostobs_changes = destination / "hostobs" / "CHANGES.md"
    hostobs_changes.parent.mkdir(parents=True)
    shutil.copy2(repo_root / "hostobs" / "CHANGES.md", hostobs_changes)
    return copied_assay


def _load_release_config_copy(
    repo_root: Path, destination: Path, load_config, project_scope_names,
    env: Mapping[str, str],
):
    source_config = repo_root / "cmru.orchestration.toml"
    orchestration = tomllib.loads(source_config.read_text(encoding="utf-8"))
    destination.mkdir()
    text = source_config.read_text(encoding="utf-8")
    (destination / "cmru.orchestration.toml").write_text(text, encoding="utf-8")
    for entry in orchestration["orchestration"]["project"].values():
        relative = Path(entry["config"])
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo_root / relative, target)
    private_env = dict(env)
    private_env["CGROUP_PARENT_DEV_GATES"] = "assay-preflight-fake.slice"
    with _isolated_process_environment(private_env):
        _, known_projects, *_ = load_config(destination / "cmru.orchestration.toml")
    known_project_names = frozenset(
        alias
        for name, project in known_projects.items()
        for alias in project_scope_names(name, project)
    )
    return known_projects, known_project_names


def _run_git(repo_root: Path, *args: str, env: Mapping[str, str] | None = None) -> str:
    isolated_env = dict(env) if env is not None else _minimal_git_environment(repo_root)
    result = subprocess.run(
        ["git", *GIT_HARDENING, "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
        env=isolated_env,
    )
    assert result.returncode == 0, (
        f"git {args!r} failed ({result.returncode}):\n"
        f"{result.stdout}{result.stderr}"
    )
    return result.stdout.strip()


def _initialize_fixture_git(repo_root: Path, assay_root: Path, seed_name: str) -> None:
    _run_git(repo_root, "init", "--quiet")
    _run_git(repo_root, "config", "user.name", "Assay changelog preflight")
    _run_git(repo_root, "config", "user.email", "assay-preflight@example.invalid")
    _run_git(repo_root, "config", "commit.gpgsign", "false")
    seed = assay_root / seed_name
    seed.write_text("source history for the synthetic release\n", encoding="utf-8")
    _run_git(repo_root, "add", str(seed.relative_to(repo_root)))
    _run_git(repo_root, "commit", "--quiet", "-m", "test: seed changelog preflight")


def _run_docs_tests(
    copied_assay: Path, state: str, *, expect_failure: bool = False
) -> str:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            DOCS_TEST_PATH,
        ],
        cwd=copied_assay,
        capture_output=True,
        text=True,
        check=False,
        env=dict(os.environ),
    )
    output = f"{result.stdout}{result.stderr}"
    if expect_failure:
        assert result.returncode == 1, (
            f"docs tests did not produce a test assertion failure against the {state} changelog; "
            f"exit status was {result.returncode}:\n{output}"
        )
        assert (
            f"FAILED {DOCS_TEST_PATH}::test_old_current_sections_shape" in output
            and f"missing B577 note: {CURRENT_ONLY_NOTE!r}" in output
        ), f"the expected B577 assertion did not fail against the {state} changelog:\n{output}"
    else:
        assert result.returncode == 0, (
            f"docs tests failed against the {state} CMRU-folded changelog; "
            f"inner pytest output follows:\n{output}"
        )
    return output


def _release_candidate(
    repo_root: Path,
    project,
    *,
    generate_release_changelog,
    pending_release_notes,
    fold_pending_release_notes,
    known_projects,
    known_project_names,
    set_version: str | None = None,
) -> bool:
    scope = {
        "known_projects": known_projects,
        "known_project_names": known_project_names,
    }
    generate_release_changelog(
        repo_root, project, **({"set_version": set_version} if set_version else {}), **scope
    )
    plan = {"set_version": set_version} if set_version else {}
    notes = pending_release_notes(repo_root, project, **plan, **scope)
    folded = fold_pending_release_notes(repo_root, project, notes.version)
    pending_release_notes(repo_root, project, **plan, **scope)
    return folded


def _add_unreleased_note(changes_path: Path) -> None:
    changes = changes_path.read_text(encoding="utf-8")
    heading = re.search(r"^## \[Unreleased\]\s*$", changes, re.MULTILINE)
    note = "\n\n### Testing\n- synthetic B596 following-release note\n"
    if heading is not None:
        insert_at = heading.end()
        updated = changes[:insert_at] + note + changes[insert_at:]
    else:
        marker_at = changes.index(HISTORY_MARKER)
        updated = (
            changes[:marker_at].rstrip()
            + "\n\n## [Unreleased]"
            + note
            + "\n"
            + changes[marker_at:]
        )
    changes_path.write_text(updated, encoding="utf-8")


def _prove_current_sections_control_fails(
    tmp_path: Path,
    current_sections_docs_check,
    *,
    generate_release_changelog,
    pending_release_notes,
    fold_pending_release_notes,
    known_projects,
    known_project_names,
) -> None:
    repo_copy = tmp_path / "current-sections-control"
    assay_copy = repo_copy / "assay"
    assay_copy.mkdir(parents=True)
    changes_path = assay_copy / "CHANGES.md"
    changes_path.write_text(
        "# Changelog\n\n"
        "## [Unreleased]\n\n"
        f"### Testing\n- {CURRENT_ONLY_NOTE}\n\n"
        "<!-- cmru: release history -->\n",
        encoding="utf-8",
    )
    project = known_projects["assay"]
    _initialize_fixture_git(repo_copy, assay_copy, ".current-sections-seed")

    first_folded = _release_candidate(
        repo_copy,
        project,
        generate_release_changelog=generate_release_changelog,
        pending_release_notes=pending_release_notes,
        fold_pending_release_notes=fold_pending_release_notes,
        known_projects=known_projects,
        known_project_names=known_project_names,
        set_version=FIRST_VERSION,
    )
    assert first_folded, "the current-sections fixture must fold its initial note"
    first_changes = changes_path.read_text(encoding="utf-8")
    current_sections_docs_check(first_changes)

    _run_git(repo_copy, "add", "assay/CHANGES.md")
    _run_git(repo_copy, "commit", "--quiet", "-m", "chore: prepare fixture release")
    _run_git(repo_copy, "tag", f"assay-v{FIRST_VERSION}")
    _add_unreleased_note(changes_path)
    next_source = assay_copy / ".current-sections-following-source"
    next_source.write_text("following fixture source\n", encoding="utf-8")
    _run_git(
        repo_copy,
        "add",
        "assay/CHANGES.md",
        "assay/.current-sections-following-source",
    )
    _run_git(
        repo_copy,
        "commit",
        "--quiet",
        "-m",
        "fix: add synthetic following-release source",
    )
    second_folded = _release_candidate(
        repo_copy,
        project,
        generate_release_changelog=generate_release_changelog,
        pending_release_notes=pending_release_notes,
        fold_pending_release_notes=fold_pending_release_notes,
        known_projects=known_projects,
        known_project_names=known_project_names,
    )
    assert second_folded, "the second current-sections fixture fold must happen"
    control_test = assay_copy / DOCS_TEST_PATH
    control_test.parent.mkdir(parents=True, exist_ok=True)
    control_test.write_text(
        "from pathlib import Path\n"
        "import re\n"
        "def test_old_current_sections_shape():\n"
        "    changes = (Path(__file__).parents[2] / 'CHANGES.md').read_text()\n"
        "    heads = list(re.finditer(r'^## \\[([^\\]]+)\\](?: - \\d{4}-\\d{2}-\\d{2})?$', changes, re.M))\n"
        "    sections = [(m.group(1), changes[m.end(): heads[i+1].start() if i+1 < len(heads) else len(changes)]) for i,m in enumerate(heads)]\n"
        f"    note = {CURRENT_ONLY_NOTE!r}\n"
        "    assert note in next((body for name, body in sections if name == 'Unreleased'), '') + next((body for name, body in sections if name != 'Unreleased'), ''), f'missing B577 note: {note!r}'\n",
        encoding="utf-8",
    )
    _run_docs_tests(
        assay_copy,
        "following-release negative control",
        expect_failure=True,
    )


def _checkout_snapshot(repo_root: Path, env: Mapping[str, str] | None = None) -> dict[str, str]:
    isolated_env = dict(env) if env is not None else _minimal_git_environment(repo_root.parent)
    git = lambda *args: _run_git(repo_root, *args, env=isolated_env)
    tracked = git("ls-files", "-z").split("\0")
    file_state = []
    for name in filter(None, tracked):
        path = repo_root / name
        if path.is_symlink():
            value = "symlink:" + os.readlink(path)
        elif path.is_file():
            value = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            value = "missing-or-nonregular"
        file_state.append((name, value))
    index_path = Path(git("rev-parse", "--git-path", "index"))
    if not index_path.is_absolute():
        index_path = repo_root / index_path
    symbolic_head = subprocess.run(
        ["git", *GIT_HARDENING, "-C", str(repo_root), "symbolic-ref", "-q", "HEAD"],
        capture_output=True, text=True, check=False, env=isolated_env,
    )
    assert symbolic_head.returncode in (0, 1), symbolic_head.stderr
    configured = subprocess.run(
        ["git", *GIT_HARDENING, "-C", str(repo_root), "config", "--bool", "--get", "extensions.worktreeConfig"],
        capture_output=True, text=True, check=False, env=isolated_env,
    )
    assert configured.returncode in (0, 1), configured.stderr
    worktree_config = "disabled"
    if configured.returncode == 0 and configured.stdout.strip() == "true":
        config_path = Path(git("rev-parse", "--git-path", "config.worktree"))
        if not config_path.is_absolute():
            config_path = repo_root / config_path
        worktree_config = (
            hashlib.sha256(config_path.read_bytes()).hexdigest()
            if config_path.exists() else "missing"
        )
    return {
        "head": git("rev-parse", "HEAD"),
        "head_ref": symbolic_head.stdout.strip() if symbolic_head.returncode == 0 else "detached",
        "index": hashlib.sha256(index_path.read_bytes()).hexdigest() if index_path.exists() else "missing",
        "worktree_config": worktree_config,
        "tracked": repr(file_state),
    }


def _assert_checkout_unchanged(before: Mapping[str, str], after: Mapping[str, str]) -> None:
    assert before == after, "the preflight changed tracked files or Git state in its source checkout"


@contextmanager
def _unchanged_checkout(repo_root: Path, env: Mapping[str, str]):
    before = _checkout_snapshot(repo_root, env)
    try:
        yield
    finally:
        _assert_checkout_unchanged(before, _checkout_snapshot(repo_root, env))


def test_docs_tests_pass_before_and_after_two_real_cmru_folds(
    tmp_path: Path,
    current_sections_docs_check,
):
    (
        generate_release_changelog,
        pending_release_notes,
        fold_pending_release_notes,
        load_config,
        validate_history_structure,
        project_scope_names,
    ) = _cmru_release_functions()
    env = _minimal_git_environment(tmp_path / "environment")
    known_projects, known_project_names = _load_release_config_copy(
        PROJECT_ROOT.parent,
        tmp_path / "cmru-config",
        load_config,
        project_scope_names,
        env,
    )
    project = known_projects["assay"]
    copied_assay = _copy_docs_test_tree(PROJECT_ROOT.parent, tmp_path / "repo")
    repo_copy = copied_assay.parent
    changes_path = copied_assay / "CHANGES.md"
    with _unchanged_checkout(PROJECT_ROOT.parent, env):
        with _isolated_process_environment(env):
            _initialize_fixture_git(repo_copy, copied_assay, ".b596-preflight-seed")

            _release_candidate(
                repo_copy, project,
                generate_release_changelog=generate_release_changelog,
                pending_release_notes=pending_release_notes,
                fold_pending_release_notes=fold_pending_release_notes,
                known_projects=known_projects,
                known_project_names=known_project_names,
                set_version=FIRST_VERSION,
            )
            _run_docs_tests(copied_assay, "first-release")
            _run_git(repo_copy, "add", "assay/CHANGES.md")
            _run_git(repo_copy, "commit", "--quiet", "-m", "chore: prepare first release")
            _run_git(repo_copy, "tag", f"assay-v{FIRST_VERSION}")
            _add_unreleased_note(changes_path)
            next_source = copied_assay / ".b596-following-release-source"
            next_source.write_text("source history for the following release\n", encoding="utf-8")
            _run_git(repo_copy, "add", "assay/CHANGES.md", "assay/.b596-following-release-source")
            _run_git(repo_copy, "commit", "--quiet", "-m", "fix: add synthetic following-release source")
            second_folded = _release_candidate(
                repo_copy, project,
                generate_release_changelog=generate_release_changelog,
                pending_release_notes=pending_release_notes,
                fold_pending_release_notes=fold_pending_release_notes,
                known_projects=known_projects,
                known_project_names=known_project_names,
            )
            assert second_folded, "the synthetic following release must fold its [Unreleased] note"
            _prove_current_sections_control_fails(
                tmp_path, current_sections_docs_check,
                generate_release_changelog=generate_release_changelog,
                pending_release_notes=pending_release_notes,
                fold_pending_release_notes=fold_pending_release_notes,
                known_projects=known_projects,
                known_project_names=known_project_names,
            )
            _run_docs_tests(copied_assay, "following-release")


def test_unchanged_cmru_config_requires_gate_variable(tmp_path: Path, capsys):
    (_, _, _, load_config, _, _) = _cmru_release_functions()
    source_config = PROJECT_ROOT.parent / "cmru.orchestration.toml"
    orchestration = tomllib.loads(source_config.read_text(encoding="utf-8"))
    destination = tmp_path / "cmru-config"
    destination.mkdir()
    copied_config = destination / "cmru.orchestration.toml"
    shutil.copy2(source_config, copied_config)
    for entry in orchestration["orchestration"]["project"].values():
        relative = Path(entry["config"])
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PROJECT_ROOT.parent / relative, target)
    env = _minimal_git_environment(tmp_path / "environment")
    assert "CGROUP_PARENT_DEV_GATES" not in env
    with pytest.raises(SystemExit) as refused:
        with _isolated_process_environment(env):
            load_config(copied_config)
    assert refused.value.code == 2
    assert "CGROUP_PARENT_DEV_GATES" in capsys.readouterr().err


@pytest.mark.parametrize("heading", ["empty", "absent"])
def test_cmru_history_validation_accepts_empty_or_absent_unreleased(
    heading: str, tmp_path: Path
):
    (_, _, _, _, validate_history_structure, _) = _cmru_release_functions()
    section = "## [Unreleased]\n\n" if heading == "empty" else ""
    changes = f"# Changelog\n\n{section}{HISTORY_MARKER}\n"
    markers, span = validate_history_structure(
        type("Project", (), {"name": "assay"})(), Path("CHANGES.md"), changes
    )
    assert len(markers) == 1
    assert (span is not None) is (heading == "empty")
    changes_path = tmp_path / "CHANGES.md"
    changes_path.write_text(changes, encoding="utf-8")
    _add_unreleased_note(changes_path)
    updated = changes_path.read_text(encoding="utf-8")
    assert updated.index("## [Unreleased]") < updated.index(HISTORY_MARKER)
    assert "synthetic B596 following-release note" in updated


def test_git_preflight_environment_drops_hostile_repository_redirects(tmp_path: Path, monkeypatch):
    hostile = tmp_path / "hostile"
    hostile.mkdir()
    _initialize_fixture_git(hostile, hostile, "seed.txt")
    original_head = _run_git(hostile, "rev-parse", "HEAD")
    hostile_values = {
        "GIT_DIR": str(hostile / ".git"),
        "GIT_WORK_TREE": str(hostile),
        "GIT_INDEX_FILE": str(tmp_path / "hostile-index"),
        "GIT_OBJECT_DIRECTORY": str(tmp_path / "hostile-objects"),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(hostile / ".git/objects"),
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.hooksPath",
        "GIT_CONFIG_VALUE_0": str(tmp_path / "hostile-hooks"),
        "GIT_TEMPLATE_DIR": str(tmp_path / "hostile-template"),
    }
    for name, value in hostile_values.items():
        monkeypatch.setenv(name, value)
    env = _minimal_git_environment(tmp_path / "environment")
    assert not (GIT_INJECTION_KEYS - {"GIT_CONFIG_GLOBAL"}).intersection(env)
    assert not any(name.startswith("GIT_") for name in env if name not in {
        "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL", "GIT_OPTIONAL_LOCKS", "GIT_TERMINAL_PROMPT"
    })
    target = tmp_path / "target"
    target.mkdir()
    with _isolated_process_environment(env):
        _initialize_fixture_git(target, target, "target.txt")
    assert _run_git(hostile, "rev-parse", "HEAD") == original_head


@pytest.mark.parametrize("mutation", ["tracked-file", "head"])
def test_checkout_immutability_guard_detects_file_and_ref_changes(tmp_path: Path, mutation: str):
    repo = tmp_path / "repo"
    repo.mkdir()
    _initialize_fixture_git(repo, repo, "tracked.txt")
    before = _checkout_snapshot(repo)
    if mutation == "tracked-file":
        (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
    else:
        _run_git(repo, "commit", "--quiet", "--allow-empty", "-m", "advance own HEAD")
    with pytest.raises(AssertionError, match="changed tracked files or Git state"):
        _assert_checkout_unchanged(before, _checkout_snapshot(repo))


def test_checkout_immutability_guard_ignores_sibling_worktree_refs(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _initialize_fixture_git(repo, repo, "tracked.txt")
    env = _minimal_git_environment(tmp_path / "environment")
    sibling = tmp_path / "sibling"
    _run_git(repo, "worktree", "add", "--quiet", "-b", "sibling-branch", str(sibling), "HEAD", env=env)
    with _unchanged_checkout(repo, env):
        (sibling / "sibling.txt").write_text("sibling worktree update\n", encoding="utf-8")
        _run_git(sibling, "add", "sibling.txt", env=env)
        _run_git(sibling, "commit", "--quiet", "-m", "advance sibling branch", env=env)
        _run_git(sibling, "tag", "sibling-tag", env=env)


def test_run_docs_tests_rejects_collection_error_as_negative_control(tmp_path: Path, monkeypatch):
    assay_copy = tmp_path / "assay"
    assay_copy.mkdir()

    def collection_error(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 2, "", "ERROR collecting test file\n")

    monkeypatch.setattr(subprocess, "run", collection_error)
    with pytest.raises(AssertionError, match="did not produce a test assertion failure"):
        _run_docs_tests(assay_copy, "collection-error negative control", expect_failure=True)


def test_checkout_immutability_guard_runs_when_an_early_step_raises(
    tmp_path: Path, monkeypatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    _initialize_fixture_git(repo, repo, "tracked.txt")
    env = _minimal_git_environment(tmp_path / "environment")
    calls = []
    original_assert = _assert_checkout_unchanged

    def record_assert(before, after):
        calls.append(True)
        return original_assert(before, after)

    def fail_early(*args, **kwargs):
        raise RuntimeError("injected early preflight failure")

    monkeypatch.setattr(sys.modules[__name__], "_assert_checkout_unchanged", record_assert)
    monkeypatch.setattr(sys.modules[__name__], "_initialize_fixture_git", fail_early)
    with pytest.raises(RuntimeError, match="injected early preflight failure"):
        with _unchanged_checkout(repo, env):
            _initialize_fixture_git(repo, repo, "unused.txt")
    assert calls == [True]
