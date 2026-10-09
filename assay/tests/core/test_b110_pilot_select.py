from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from assay import cli as assay_cli


TOOL = Path(__file__).parents[2] / "tools" / "b110_pilot_select.py"
GO_PATH = "assay/src/assay/adapters/go.py"
BOOL_PATH = "assay/src/assay/adapters/pilot_bool_fixture.py"


def _id(number: int) -> str:
    return f"{number:064x}"


def _load_selector():
    spec = importlib.util.spec_from_file_location("b110_pilot_select", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _row(
    number: int,
    *,
    path: str,
    source: bytes,
    line: int,
    needle: bytes,
    operator: str,
    description: str,
) -> dict[str, object]:
    lines = source.splitlines(keepends=True)
    offset = sum(len(item) for item in lines[: line - 1])
    position = lines[line - 1].index(needle)
    start = offset + position
    return {
        "id": _id(number),
        "path": path,
        "operator": operator,
        "start_byte": start,
        "end_byte": start + len(needle),
        "lineno": line,
        "description": description,
        "source_sha256": hashlib.sha256(source).hexdigest(),
    }


def _valid_fixture(repo: Path) -> tuple[list[dict[str, object]], dict[str, str]]:
    go_source = (
        b"def _scan_raw_string():\n"
        b"    return None if end == -1 else end + 1\n"
        b"def _strip_comments_and_literals():\n"
        b'    if two == "//":\n'
        b"        return None\n"
        b"    if end == -1:\n"
        b"        return None\n"
        b"    if close == -1:\n"
        b"        return None\n"
        b"    return None\n"
    )
    bool_source = b"FLAG_A = True\nFLAG_B = False\nFLAG_C = True\n"
    files = {GO_PATH: go_source.decode(), BOOL_PATH: bool_source.decode()}
    for relative, content in files.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.encode())

    rows: list[dict[str, object]] = []
    scanner_specs = [(2, b"=="), (4, b"=="), (6, b"=="), (8, b"==")]
    for number, (line, needle) in enumerate(scanner_specs, 1):
        rows.append(
            _row(
                number,
                path=GO_PATH,
                source=go_source,
                line=line,
                needle=needle,
                operator="python:compare-swap",
                description="Eq->NotEq",
            )
        )
    for number, (line, needle) in enumerate(((1, b"True"), (2, b"False"), (3, b"True")), 5):
        rows.append(
            _row(
                number,
                path=BOOL_PATH,
                source=bool_source,
                line=line,
                needle=needle,
                operator="python:bool-const-flip",
                description="bool constant flip",
            )
        )
    for number in range(8, 15):
        rows.append(
            {
                "id": _id(number),
                "path": BOOL_PATH,
                "operator": "python:compare-swap",
                "start_byte": 0,
                "end_byte": 1,
                "lineno": 1,
                "description": f"filler {number}",
                "source_sha256": hashlib.sha256(bool_source).hexdigest(),
            }
        )
    return rows, files


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "git",
            "-c",
            "maintenance.auto=false",
            "-c",
            "maintenance.autoDetach=false",
            "-c",
            "gc.autoDetach=false",
            "-c",
            "core.hooksPath=/dev/null",
            "-C",
            str(repo),
            *args,
        ],
        check=check,
        capture_output=True,
        text=True,
    )


def _ensure_git_repo(repo: Path) -> tuple[str, str]:
    probe = _git(repo, "rev-parse", "--verify", "HEAD^{commit}", check=False)
    if probe.returncode != 0:
        _git(repo, "init", "-q")
        _git(repo, "config", "user.name", "Assay selector test")
        _git(repo, "config", "user.email", "assay-selector@example.invalid")
        _git(repo, "add", "-A")
        _git(repo, "commit", "--allow-empty", "-m", "selector fixture")
    commit = _git(repo, "rev-parse", "--verify", "HEAD^{commit}").stdout.strip()
    tree = _git(repo, "rev-parse", "--verify", "HEAD^{tree}").stdout.strip()
    return commit, tree


def _run_selector(
    tmp_path: Path,
    rows: list[dict[str, object]],
    repo: Path,
    *extra: str,
    raw_plan: bytes | None = None,
    out_path: Path | None = None,
    report_path: Path | None = None,
) -> SimpleNamespace:
    tmp_path.mkdir(parents=True, exist_ok=True)
    commit, tree = _ensure_git_repo(repo)
    plan = tmp_path / "plan.json"
    candidates = out_path or tmp_path / "candidates.txt"
    report = report_path or tmp_path / "selection.json"
    plan_bytes = (
        raw_plan
        if raw_plan is not None
        else json.dumps(
            {"status": "ok", "commit": commit, "tree": tree, "candidates": rows}
        ).encode()
    )
    plan.write_bytes(plan_bytes)
    argv = [
            sys.executable,
            "--plan",
            str(plan),
            "--repo-root",
            str(repo),
            "--out",
            str(candidates),
            "--report",
            str(report),
            *extra,
        ]
    del argv[0]  # the in-process CLI does not need a Python executable
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = _load_selector().main(argv)
    return SimpleNamespace(
        returncode=code,
        stdout=stdout.getvalue(),
        stderr=stderr.getvalue(),
        plan_path=plan,
        plan_bytes=plan_bytes,
    )


def _run_loaded_selector(module, argv: list[str]) -> SimpleNamespace:
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = module.main(argv)
    return SimpleNamespace(returncode=code, stdout=stdout.getvalue(), stderr=stderr.getvalue())


def _release_and_reap_selector_process(
    process: subprocess.Popen[str], release: Path
) -> tuple[str, str, Exception | None]:
    release_error: Exception | None = None
    if process.poll() is None:
        try:
            release.write_text("continue", encoding="utf-8")
        except Exception as exc:
            release_error = exc
            try:
                process.kill()
            except OSError:
                pass
    try:
        stdout, stderr = process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired as reap_error:
            raise AssertionError(
                "selector subprocess could not be terminated and reaped"
            ) from reap_error
    return stdout, stderr, release_error


@pytest.mark.parametrize("fail_release_write", [False, True])
def test_selector_process_cleanup_runs_after_controller_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_release_write: bool
):
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    child = r"""
from pathlib import Path
import sys
import time

ready = Path(sys.argv[1])
release = Path(sys.argv[2])
ready.write_text("ready", encoding="utf-8")
deadline = time.monotonic() + 15
while not release.exists():
    if time.monotonic() >= deadline:
        raise TimeoutError("timed out waiting for controller release")
    time.sleep(0.01)
"""
    original_write_text = Path.write_text
    if fail_release_write:
        def fail_release(path: Path, *args, **kwargs):
            if path == release:
                raise OSError("injected release marker failure")
            return original_write_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "write_text", fail_release)

    release_error: Exception | None = None
    process = subprocess.Popen(
        [sys.executable, "-c", child, str(ready), str(release)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        with pytest.raises(RuntimeError, match="injected controller failure"):
            deadline = time.monotonic() + 10
            while not ready.exists():
                if process.poll() is not None:
                    pytest.fail("child exited before ready marker")
                if time.monotonic() >= deadline:
                    pytest.fail("child did not reach ready marker")
                time.sleep(0.01)
            raise RuntimeError("injected controller failure")
    finally:
        _stdout, _stderr, release_error = _release_and_reap_selector_process(
            process, release
        )

    assert process.returncode is not None
    if fail_release_write:
        assert isinstance(release_error, OSError)
        assert process.returncode != 0
    else:
        assert release_error is None
        assert process.returncode == 0


def test_S1_S7_selector_is_deterministic_and_hashes_plan_order(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    commit, tree = _ensure_git_repo(repo)
    first = _run_selector(tmp_path / "first", rows, repo, "--size", "8")
    second_dir = tmp_path / "second"
    second_dir.mkdir()
    second = _run_selector(second_dir, rows, repo, "--size", "8")
    assert first.returncode == second.returncode == 0, first.stderr + second.stderr
    first_candidates = (tmp_path / "first" / "candidates.txt").read_bytes()
    second_candidates = (second_dir / "candidates.txt").read_bytes()
    first_report = (tmp_path / "first" / "selection.json").read_bytes()
    second_report = (second_dir / "selection.json").read_bytes()
    assert first_candidates == second_candidates
    assert first_report == second_report
    lines = first_candidates.decode().splitlines()
    selected_ids = set(lines[1:])
    assert len(selected_ids) >= 8
    assert lines[0].startswith("# b110 pilot selection seed=b110-pilot-2026 size=8 plan_candidates=")
    report = json.loads((tmp_path / "first" / "selection.json").read_text())
    hard_ids = {item["id"] for item in report["known_hard"]}
    assert len(hard_ids) == 6
    assert hard_ids <= selected_ids
    assert report["plan_commit"] == commit
    assert report["plan_tree"] == tree
    assert report["selected_ids"] == lines[1:]
    assert report["plan_sha256"] == hashlib.sha256(
        (tmp_path / "first" / "plan.json").read_bytes()
    ).hexdigest()
    assert report["candidates_file_sha256"] == hashlib.sha256(first_candidates).hexdigest()
    plan_order = [row["id"] for row in rows]
    assert lines[1:] == [identity for identity in plan_order if identity in selected_ids]
    expected_digest = hashlib.sha256(
        "".join(f"{len(item)}:{item}," for item in lines[1:]).encode()
    ).hexdigest()
    assert report["selection_sha256"] == expected_digest

    third_dir = tmp_path / "third"
    third_dir.mkdir()
    third = _run_selector(third_dir, rows, repo, "--size", "8", "--seed", "another-seed")
    assert third.returncode == 0, third.stderr
    assert (third_dir / "candidates.txt").read_bytes() != first_candidates


def test_selector_rejects_control_characters_in_seed_before_writing(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    output_dir = tmp_path / "control-seed"
    result = _run_selector(
        output_dir, rows, repo, "--seed", "operator\n# injected candidate"
    )

    assert result.returncode == 2
    assert "printable ASCII" in result.stderr
    assert not (output_dir / "candidates.txt").exists()
    assert not (output_dir / "selection.json").exists()


@pytest.mark.parametrize(
    "destination", ["out", "report", "out_symlink", "report_symlink"]
)
def test_selector_refuses_output_path_that_aliases_plan(
    tmp_path: Path, destination: str
):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    output_dir = tmp_path / "alias"
    output_dir.mkdir()
    alias_path = output_dir / "plan-alias.json"
    if destination.endswith("_symlink"):
        alias_path.symlink_to(output_dir / "plan.json")
    destination_kind = destination.removesuffix("_symlink")
    target = alias_path if destination.endswith("_symlink") else output_dir / "plan.json"
    kwargs = {"out_path": target} if destination_kind == "out" else {"report_path": target}

    result = _run_selector(output_dir, rows, repo, **kwargs)

    assert result.returncode == 2
    if destination.endswith("_symlink"):
        assert "output destination is a symlink" in result.stderr
    else:
        assert "--plan, --out, and --report must name different files" in result.stderr
    assert result.plan_path.read_bytes() == result.plan_bytes


@pytest.mark.parametrize(
    "destination", ["out", "report", "out_symlink", "report_symlink"]
)
def test_selector_refuses_output_path_that_aliases_a_planned_source(
    tmp_path: Path, destination: str
):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    source = repo / BOOL_PATH
    original = source.read_bytes()
    output_dir = tmp_path / "source-output"
    output_dir.mkdir()
    is_symlink = destination.endswith("_symlink")
    field = destination.removesuffix("_symlink")
    output_path = output_dir / "source-alias"
    if is_symlink:
        output_path.symlink_to(source)
    else:
        output_path = source
    kwargs = {"out_path": output_path} if field == "out" else {"report_path": output_path}

    result = _run_selector(output_dir, rows, repo, **kwargs)

    assert result.returncode == 2
    if is_symlink:
        assert "output destination is a symlink" in result.stderr
    else:
        assert "must not overwrite a source file named by the plan" in result.stderr
    assert source.read_bytes() == original
    assert not (output_dir / "candidates.txt").exists()
    if is_symlink:
        assert output_path.is_symlink()


def test_repository_identity_ignores_ambient_git_dir_and_work_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    _rows, _files = _valid_fixture(repo)
    _ensure_git_repo(repo)
    clean_clone = tmp_path / "clean-clone"
    subprocess.run(
        ["git", "clone", "--quiet", str(repo), str(clean_clone)],
        check=True,
        capture_output=True,
        text=True,
    )
    (repo / "assay.toml").write_text("# dirty supplied checkout\n", encoding="utf-8")
    selector = _load_selector()
    monkeypatch.setenv("GIT_DIR", str(clean_clone / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(clean_clone))

    with pytest.raises(selector.SelectionError, match="must have a clean Git worktree"):
        selector._repository_identity(repo)


def test_selector_rejects_seed_over_bound_before_writing(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    output_dir = tmp_path / "long-seed"
    result = _run_selector(
        output_dir, rows, repo, "--seed", "s" * 129
    )

    assert result.returncode == 2
    assert "at most 128 characters" in result.stderr
    assert not (output_dir / "candidates.txt").exists()
    assert not (output_dir / "selection.json").exists()


def test_selector_refuses_output_larger_than_assay_candidate_input_limit(
    tmp_path: Path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    template = next(row for row in rows if row["operator"] == "python:bool-const-flip")
    rows.extend(
        {**template, "id": _id(10_000 + index)}
        for index in range(17_000)
    )
    output_dir = tmp_path / "oversized-output"
    result = _run_selector(
        output_dir, rows, repo, "--size", "17000"
    )

    assert result.returncode == 2
    assert "1 MiB input limit" in result.stderr
    assert not (output_dir / "candidates.txt").exists()
    assert not (output_dir / "selection.json").exists()


def test_selector_rejects_plan_from_another_source_checkout(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    source = repo / GO_PATH
    source.write_bytes(source.read_bytes() + b"# changed elsewhere in file\n")

    result = _run_selector(tmp_path / "changed-source", rows, repo)

    assert result.returncode == 2
    assert "source sha256 does not match plan" in result.stderr
    assert not (tmp_path / "changed-source" / "candidates.txt").exists()
    assert not (tmp_path / "changed-source" / "selection.json").exists()


def test_selector_rejects_stale_plan_when_unlisted_tree_content_changes(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    old_commit, old_tree = _ensure_git_repo(repo)
    original_go = (repo / GO_PATH).read_bytes()
    (repo / "assay.toml").write_text("# new target or lane configuration\n", encoding="utf-8")
    _git(repo, "add", "assay.toml")
    _git(repo, "commit", "-m", "change plan inputs")
    assert (repo / GO_PATH).read_bytes() == original_go
    stale_plan = json.dumps(
        {"status": "ok", "commit": old_commit, "tree": old_tree, "candidates": rows}
    ).encode()

    result = _run_selector(tmp_path / "stale-plan", rows, repo, raw_plan=stale_plan)

    assert result.returncode == 2
    assert "plan commit does not match repo-root HEAD" in result.stderr
    assert not (tmp_path / "stale-plan" / "candidates.txt").exists()
    assert not (tmp_path / "stale-plan" / "selection.json").exists()


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("commit", "plan commit does not match repo-root HEAD"),
        ("tree", "plan tree does not match repo-root HEAD tree"),
    ],
)
def test_selector_rejects_commit_or_tree_mismatch(
    tmp_path: Path, field: str, message: str
):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    commit, tree = _ensure_git_repo(repo)
    plan = {"status": "ok", "commit": commit, "tree": tree, "candidates": rows}
    plan[field] = "0" * 40

    result = _run_selector(
        tmp_path / f"wrong-{field}", rows, repo, raw_plan=json.dumps(plan).encode()
    )

    assert result.returncode == 2
    assert message in result.stderr
    assert not (tmp_path / f"wrong-{field}" / "candidates.txt").exists()
    assert not (tmp_path / f"wrong-{field}" / "selection.json").exists()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("commit", None, "plan commit must be a 40-character lowercase Git object id"),
        ("tree", "not-a-git-id", "plan tree must be a 40-character lowercase Git object id"),
    ],
)
def test_selector_rejects_missing_or_malformed_plan_identity(
    tmp_path: Path, field: str, value: str | None, message: str
):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    commit, tree = _ensure_git_repo(repo)
    plan: dict[str, object] = {
        "status": "ok",
        "commit": commit,
        "tree": tree,
        "candidates": rows,
    }
    if value is None:
        del plan[field]
    else:
        plan[field] = value

    result = _run_selector(
        tmp_path / f"invalid-{field}", rows, repo, raw_plan=json.dumps(plan).encode()
    )

    assert result.returncode == 2
    assert message in result.stderr
    assert not (tmp_path / f"invalid-{field}" / "candidates.txt").exists()
    assert not (tmp_path / f"invalid-{field}" / "selection.json").exists()


def test_selector_rejects_plan_when_worktree_is_dirty(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    commit, tree = _ensure_git_repo(repo)
    plan = json.dumps(
        {"status": "ok", "commit": commit, "tree": tree, "candidates": rows}
    ).encode()
    (repo / "assay.toml").write_text("# uncommitted plan input change\n", encoding="utf-8")

    result = _run_selector(tmp_path / "dirty-plan", rows, repo, raw_plan=plan)

    assert result.returncode == 2
    assert "must have a clean Git worktree" in result.stderr
    assert not (tmp_path / "dirty-plan" / "candidates.txt").exists()
    assert not (tmp_path / "dirty-plan" / "selection.json").exists()


def test_selector_ignores_global_core_worktree_and_checks_supplied_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    _valid_fixture(repo)
    _ensure_git_repo(repo)
    clean_clone = tmp_path / "clean-clone"
    subprocess.run(
        ["git", "clone", "--quiet", str(repo), str(clean_clone)],
        check=True,
        capture_output=True,
        text=True,
    )
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text(
        f"[core]\n\tworktree = {clean_clone}\n", encoding="utf-8"
    )
    (repo / "assay.toml").write_text("# supplied worktree is dirty\n", encoding="utf-8")
    selector = _load_selector()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / "xdg"))
    with pytest.raises(selector.SelectionError, match="must have a clean Git worktree"):
        selector._repository_identity(repo)


def test_selector_rechecks_unlisted_tree_inputs_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    (repo / "assay.toml").write_text("# committed plan input\n", encoding="utf-8")
    commit, tree = _ensure_git_repo(repo)
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"status": "ok", "commit": commit, "tree": tree, "candidates": rows}),
        encoding="utf-8",
    )
    output = tmp_path / "candidates.txt"
    report = tmp_path / "selection.json"
    selector = _load_selector()
    original_select = selector._select

    def edit_unlisted_input(*args, **kwargs):
        selected = original_select(*args, **kwargs)
        (repo / "assay.toml").write_text("# changed during selection\n", encoding="utf-8")
        return selected

    monkeypatch.setattr(selector, "_select", edit_unlisted_input)
    result = _run_loaded_selector(
        selector,
        [
            "--plan", str(plan), "--repo-root", str(repo),
            "--out", str(output), "--report", str(report),
        ],
    )
    assert result.returncode == 2
    assert "must have a clean Git worktree" in result.stderr
    assert not output.exists()
    assert not report.exists()


def test_selector_pinned_output_parent_cannot_be_redirected_to_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    commit, tree = _ensure_git_repo(repo)
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"status": "ok", "commit": commit, "tree": tree, "candidates": rows}),
        encoding="utf-8",
    )
    source = repo / GO_PATH
    original_source = source.read_bytes()
    original_parent = tmp_path / "initial-output"
    original_parent.mkdir()
    alias_parent = tmp_path / "output-alias"
    alias_parent.symlink_to(original_parent, target_is_directory=True)
    output = alias_parent / "candidates.txt"
    report = alias_parent / "selection.json"
    selector = _load_selector()
    original_select = selector._select

    def redirect_after_pinning(*args, **kwargs):
        result = original_select(*args, **kwargs)
        alias_parent.unlink()
        alias_parent.symlink_to(source.parent, target_is_directory=True)
        return result

    monkeypatch.setattr(selector, "_select", redirect_after_pinning)
    result = _run_loaded_selector(
        selector,
        [
            "--plan", str(plan), "--repo-root", str(repo),
            "--out", str(output), "--report", str(report),
        ],
    )
    assert result.returncode == 2
    assert "output path changed while selection was prepared" in result.stderr
    assert source.read_bytes() == original_source
    assert not (original_parent / "candidates.txt").exists()
    assert not (original_parent / "selection.json").exists()
    assert alias_parent.resolve() == source.parent


def test_selector_refuses_output_parent_retarget_with_stale_new_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    commit, tree = _ensure_git_repo(repo)
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"status": "ok", "commit": commit, "tree": tree, "candidates": rows}),
        encoding="utf-8",
    )
    original_parent = tmp_path / "initial-output"
    original_parent.mkdir()
    redirected_parent = tmp_path / "redirected-output"
    redirected_parent.mkdir()
    stale_candidates = redirected_parent / "candidates.txt"
    stale_report = redirected_parent / "selection.json"
    stale_candidates.write_text("stale candidates\n", encoding="utf-8")
    stale_report.write_text("stale report\n", encoding="utf-8")
    alias_parent = tmp_path / "output-alias"
    alias_parent.symlink_to(original_parent, target_is_directory=True)
    selector = _load_selector()
    original_select = selector._select

    def redirect_after_pinning(*args, **kwargs):
        selected = original_select(*args, **kwargs)
        alias_parent.unlink()
        alias_parent.symlink_to(redirected_parent, target_is_directory=True)
        return selected

    monkeypatch.setattr(selector, "_select", redirect_after_pinning)
    result = _run_loaded_selector(
        selector,
        [
            "--plan", str(plan), "--repo-root", str(repo),
            "--out", str(alias_parent / "candidates.txt"),
            "--report", str(alias_parent / "selection.json"),
        ],
    )
    assert result.returncode == 2
    assert "output path changed while selection was prepared" in result.stderr
    assert stale_candidates.read_text(encoding="utf-8") == "stale candidates\n"
    assert stale_report.read_text(encoding="utf-8") == "stale report\n"
    assert not (original_parent / "candidates.txt").exists()
    assert not (original_parent / "selection.json").exists()


def test_selector_rechecks_output_parent_after_publishing_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    commit, tree = _ensure_git_repo(repo)
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"status": "ok", "commit": commit, "tree": tree, "candidates": rows}),
        encoding="utf-8",
    )
    pinned_parent = tmp_path / "pinned-output"
    pinned_parent.mkdir()
    replacement_parent = tmp_path / "replacement-output"
    replacement_parent.mkdir()
    alias_parent = tmp_path / "output-alias"
    alias_parent.symlink_to(pinned_parent, target_is_directory=True)
    selector = _load_selector()
    original_publish = selector._publish_pair

    def publish_then_redirect(*args, **kwargs):
        result = original_publish(*args, **kwargs)
        alias_parent.unlink()
        alias_parent.symlink_to(replacement_parent, target_is_directory=True)
        return result

    monkeypatch.setattr(selector, "_publish_pair", publish_then_redirect)
    result = _run_loaded_selector(
        selector,
        [
            "--plan", str(plan), "--repo-root", str(repo),
            "--out", str(alias_parent / "candidates.txt"),
            "--report", str(alias_parent / "selection.json"),
        ],
    )

    assert result.returncode == 2
    assert "output path changed while selection was prepared" in result.stderr
    assert alias_parent.resolve() == replacement_parent
    assert list(replacement_parent.iterdir()) == []
    final_candidates = (pinned_parent / "candidates.txt").read_bytes()
    final_report = json.loads(
        (pinned_parent / "selection.json").read_text(encoding="utf-8")
    )
    assert final_report["candidates_file_sha256"] == hashlib.sha256(
        final_candidates
    ).hexdigest()


def test_selector_accepts_parent_alias_restored_before_postcheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    commit, tree = _ensure_git_repo(repo)
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({"status": "ok", "commit": commit, "tree": tree, "candidates": rows}),
        encoding="utf-8",
    )
    pinned_parent = tmp_path / "pinned-output"
    pinned_parent.mkdir()
    transient_parent = tmp_path / "transient-output"
    transient_parent.mkdir()
    alias_parent = tmp_path / "output-alias"
    alias_parent.symlink_to(pinned_parent, target_is_directory=True)
    selector = _load_selector()
    original_publish = selector._publish_pair

    def publish_retarget_and_restore(*args, **kwargs):
        result = original_publish(*args, **kwargs)
        alias_parent.unlink()
        alias_parent.symlink_to(transient_parent, target_is_directory=True)
        alias_parent.unlink()
        alias_parent.symlink_to(pinned_parent, target_is_directory=True)
        return result

    monkeypatch.setattr(selector, "_publish_pair", publish_retarget_and_restore)
    result = _run_loaded_selector(
        selector,
        [
            "--plan", str(plan), "--repo-root", str(repo),
            "--out", str(alias_parent / "candidates.txt"),
            "--report", str(alias_parent / "selection.json"),
        ],
    )

    assert result.returncode == 0, result.stderr
    assert alias_parent.resolve() == pinned_parent
    assert list(transient_parent.iterdir()) == []
    candidate_bytes = (pinned_parent / "candidates.txt").read_bytes()
    report_document = json.loads(
        (pinned_parent / "selection.json").read_text(encoding="utf-8")
    )
    assert report_document["candidates_file_sha256"] == hashlib.sha256(
        candidate_bytes
    ).hexdigest()


def test_selector_refuses_output_parent_alias_of_planned_source_directory(
    tmp_path: Path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    source = repo / GO_PATH
    original_source = source.read_bytes()
    output_alias = tmp_path / "source-parent"
    output_alias.symlink_to(source.parent, target_is_directory=True)
    result = _run_selector(
        tmp_path / "output-parent-alias",
        rows,
        repo,
        out_path=output_alias / source.name,
    )
    assert result.returncode == 2
    assert "must not overwrite a source file named by the plan" in result.stderr
    assert source.read_bytes() == original_source
    assert not (tmp_path / "output-parent-alias" / "selection.json").exists()


def test_selector_publishes_report_last_and_removes_old_report_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    _ensure_git_repo(repo)
    output_dir = tmp_path / "pair"
    output_dir.mkdir()
    old_report = output_dir / "selection.json"
    old_report.write_text("old report", encoding="utf-8")
    selector = _load_selector()
    original_replace = selector.os.replace

    def fail_report_replace(src, dst, **kwargs):
        if dst == "selection.json":
            raise OSError("injected report publication failure")
        return original_replace(src, dst, **kwargs)

    monkeypatch.setattr(selector.os, "replace", fail_report_replace)
    result = _run_selector(output_dir, rows, repo)
    assert result.returncode == 2
    assert "injected report publication failure" in result.stderr
    assert (output_dir / "candidates.txt").is_file()
    assert not old_report.exists()


def test_concurrent_selector_publication_refuses_overlapping_outputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    commit, tree = _ensure_git_repo(repo)
    output_dir = tmp_path / "shared-output"
    output_dir.mkdir()
    candidates = output_dir / "candidates.txt"
    report = output_dir / "selection.json"

    first_seed = "concurrent-first"
    second_seed = "concurrent-second"
    expected_first = _run_selector(
        tmp_path / "expected-first",
        rows,
        repo,
        "--size", "8", "--seed", first_seed,
    )
    expected_second = _run_selector(
        tmp_path / "expected-second",
        rows,
        repo,
        "--size", "8", "--seed", second_seed,
    )
    assert expected_first.returncode == expected_second.returncode == 0
    expected_first_bytes = (
        tmp_path / "expected-first" / "candidates.txt"
    ).read_bytes()
    assert expected_first_bytes != (
        tmp_path / "expected-second" / "candidates.txt"
    ).read_bytes()

    def invocation(directory: Path, seed: str) -> list[str]:
        directory.mkdir()
        plan = directory / "plan.json"
        plan.write_text(
            json.dumps(
                {
                    "status": "ok",
                    "commit": commit,
                    "tree": tree,
                    "candidates": rows,
                }
            ),
            encoding="utf-8",
        )
        return [
            "--plan", str(plan),
            "--repo-root", str(repo),
            "--out", str(candidates),
            "--report", str(report),
            "--size", "8",
            "--seed", seed,
        ]

    first = _load_selector()
    second = _load_selector()
    first_published_candidates = threading.Event()
    publish_report = threading.Event()
    first_result: list[int] = []

    def pause_between_pair_replacements(
        candidate_output,
        candidate_temporary,
        report_output,
        report_temporary,
    ) -> None:
        try:
            os.unlink(report_output.name, dir_fd=report_output.parent_fd)
            os.fsync(report_output.parent_fd)
        except FileNotFoundError:
            pass
        os.replace(
            candidate_temporary,
            candidate_output.name,
            src_dir_fd=candidate_output.parent_fd,
            dst_dir_fd=candidate_output.parent_fd,
        )
        os.fsync(candidate_output.parent_fd)
        first_published_candidates.set()
        if not publish_report.wait(timeout=10):
            raise AssertionError("timed out waiting to publish the selection report")
        os.replace(
            report_temporary,
            report_output.name,
            src_dir_fd=report_output.parent_fd,
            dst_dir_fd=report_output.parent_fd,
        )
        os.fsync(report_output.parent_fd)

    first._publish_pair = pause_between_pair_replacements
    first_tempdir = tmp_path / "first-tempdir"
    second_tempdir = tmp_path / "second-tempdir"
    first_tempdir.mkdir()
    second_tempdir.mkdir()
    monkeypatch.setenv("TMPDIR", str(first_tempdir))
    monkeypatch.setattr(tempfile, "tempdir", None)
    worker = threading.Thread(
        target=lambda: first_result.append(
            first.main(invocation(tmp_path / "first-run", first_seed))
        ),
        daemon=True,
    )
    worker.start()
    try:
        assert first_published_candidates.wait(timeout=10)
        assert candidates.read_bytes() == expected_first_bytes
        monkeypatch.setenv("TMPDIR", str(second_tempdir))
        monkeypatch.setattr(tempfile, "tempdir", None)
        second_status = second.main(
            invocation(tmp_path / "second-run", second_seed)
        )
        assert second_status == 2
    finally:
        publish_report.set()
        worker.join(timeout=10)

    assert not worker.is_alive()
    assert first_result == [0]
    final_candidates = candidates.read_bytes()
    final_report = json.loads(report.read_text(encoding="utf-8"))
    assert final_candidates == expected_first_bytes
    assert final_report["candidates_file_sha256"] == hashlib.sha256(
        final_candidates
    ).hexdigest()


def test_selector_releases_parent_lock_when_a_later_lock_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    first_output_dir = tmp_path / "first-output"
    second_output_dir = tmp_path / "second-output"
    first_output_dir.mkdir()
    second_output_dir.mkdir()
    candidates = first_output_dir / "candidates.txt"
    report = second_output_dir / "selection.json"
    selector = _load_selector()
    original_flock = selector.fcntl.flock
    lock_calls = 0

    def fail_second_lock(file_descriptor, operation):
        nonlocal lock_calls
        if operation == selector.fcntl.LOCK_EX | selector.fcntl.LOCK_NB:
            lock_calls += 1
            if lock_calls == 2:
                raise OSError("injected second output-parent lock failure")
        return original_flock(file_descriptor, operation)

    monkeypatch.setattr(selector.fcntl, "flock", fail_second_lock)
    result = _run_selector(
        tmp_path / "failed-lock", rows, repo,
        out_path=candidates, report_path=report,
    )
    assert result.returncode == 2
    assert "injected second output-parent lock failure" in result.stderr
    assert not candidates.exists()
    assert not report.exists()
    assert not list(first_output_dir.glob("*.tmp"))
    assert not list(second_output_dir.glob("*.tmp"))

    monkeypatch.setattr(selector.fcntl, "flock", original_flock)
    retry = _run_selector(
        tmp_path / "retry-lock", rows, repo,
        out_path=candidates, report_path=report,
    )
    assert retry.returncode == 0, retry.stderr


def test_selector_closes_parent_descriptors_after_staged_cleanup_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    commit, tree = _ensure_git_repo(repo)
    first_output_dir = tmp_path / "first-output"
    second_output_dir = tmp_path / "second-output"
    first_output_dir.mkdir()
    second_output_dir.mkdir()
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {"status": "ok", "commit": commit, "tree": tree, "candidates": rows}
        ),
        encoding="utf-8",
    )
    candidates = first_output_dir / "candidates.txt"
    report = second_output_dir / "selection.json"
    selector = _load_selector()
    original_cleanup = selector._cleanup_staged
    original_publish = selector._publish_pair
    cleanup_injected = False

    def fail_publish(*_args, **_kwargs):
        raise OSError("injected publication failure")

    def fail_staged_cleanup(parent_fd, temporary):
        nonlocal cleanup_injected
        if temporary is not None and not cleanup_injected:
            cleanup_injected = True
            raise OSError("injected staged-cleanup failure")
        return original_cleanup(parent_fd, temporary)

    monkeypatch.setattr(selector, "_publish_pair", fail_publish)
    monkeypatch.setattr(selector, "_cleanup_staged", fail_staged_cleanup)
    result = _run_loaded_selector(
        selector,
        [
            "--plan", str(plan),
            "--repo-root", str(repo),
            "--out", str(candidates),
            "--report", str(report),
            "--size", "8",
        ],
    )
    assert result.returncode == 2
    assert "injected publication failure" in result.stderr
    assert "injected staged-cleanup failure" in result.stderr
    assert cleanup_injected

    monkeypatch.setattr(selector, "_publish_pair", original_publish)
    monkeypatch.setattr(selector, "_cleanup_staged", original_cleanup)
    retry = _run_selector(
        tmp_path / "retry-after-cleanup-error",
        rows,
        repo,
        out_path=candidates,
        report_path=report,
    )
    assert retry.returncode == 0, retry.stderr


def test_cleanup_error_after_publication_returns_two_with_complete_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    commit, tree = _ensure_git_repo(repo)
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {"status": "ok", "commit": commit, "tree": tree, "candidates": rows}
        ),
        encoding="utf-8",
    )
    candidates = output_dir / "candidates.txt"
    report = output_dir / "selection.json"
    selector = _load_selector()
    original_close = selector._PinnedOutput.close

    def close_then_report_error(output):
        original_close(output)
        if output.name == "candidates.txt":
            raise OSError("injected output-close failure")

    monkeypatch.setattr(
        selector._PinnedOutput, "close", close_then_report_error
    )
    result = _run_loaded_selector(
        selector,
        [
            "--plan", str(plan),
            "--repo-root", str(repo),
            "--out", str(candidates),
            "--report", str(report),
            "--size", "8",
        ],
    )

    assert result.returncode == 2
    assert "injected output-close failure" in result.stderr
    candidate_bytes = candidates.read_bytes()
    report_document = json.loads(report.read_text(encoding="utf-8"))
    assert report_document["candidates_file_sha256"] == hashlib.sha256(
        candidate_bytes
    ).hexdigest()
    retry_candidates = candidates.read_bytes()
    retry_report = json.loads(report.read_text(encoding="utf-8"))
    assert retry_report["candidates_file_sha256"] == hashlib.sha256(
        retry_candidates
    ).hexdigest()


def test_selector_closes_all_output_descriptors_when_staged_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    commit, tree = _ensure_git_repo(repo)
    first_output_dir = tmp_path / "first-output"
    second_output_dir = tmp_path / "second-output"
    first_output_dir.mkdir()
    second_output_dir.mkdir()
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {"status": "ok", "commit": commit, "tree": tree, "candidates": rows}
        ),
        encoding="utf-8",
    )
    candidates = first_output_dir / "candidates.txt"
    report = second_output_dir / "selection.json"
    selector = _load_selector()
    original_cleanup = selector._cleanup_staged
    original_publish = selector._publish_pair
    cleanup_injected = False

    def fail_publication(*_args, **_kwargs):
        raise OSError("injected publication failure")

    def fail_first_staged_cleanup(parent_fd, temporary):
        nonlocal cleanup_injected
        if temporary is not None and not cleanup_injected:
            cleanup_injected = True
            raise OSError("injected staged-cleanup failure")
        return original_cleanup(parent_fd, temporary)

    monkeypatch.setattr(selector, "_publish_pair", fail_publication)
    monkeypatch.setattr(selector, "_cleanup_staged", fail_first_staged_cleanup)
    args = [
        "--plan", str(plan),
        "--repo-root", str(repo),
        "--out", str(candidates),
        "--report", str(report),
        "--size", "8",
    ]
    result = _run_loaded_selector(selector, args)
    assert result.returncode == 2
    assert "injected publication failure" in result.stderr
    assert "injected staged-cleanup failure" in result.stderr
    assert cleanup_injected

    monkeypatch.setattr(selector, "_publish_pair", original_publish)
    monkeypatch.setattr(selector, "_cleanup_staged", original_cleanup)
    retry = _run_selector(
        tmp_path / "retry-after-cleanup-error",
        rows,
        repo,
        out_path=candidates,
        report_path=report,
    )
    assert retry.returncode == 0, retry.stderr


def test_selector_refuses_partially_overlapping_output_directories(
    tmp_path: Path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    commit, tree = _ensure_git_repo(repo)
    first_candidates_dir = tmp_path / "first-candidates"
    shared_dir = tmp_path / "shared"
    second_report_dir = tmp_path / "second-report"
    first_candidates_dir.mkdir()
    shared_dir.mkdir()
    second_report_dir.mkdir()

    def invocation(
        directory: Path, seed: str, out_path: Path, report_path: Path
    ) -> list[str]:
        directory.mkdir()
        plan = directory / "plan.json"
        plan.write_text(
            json.dumps(
                {
                    "status": "ok",
                    "commit": commit,
                    "tree": tree,
                    "candidates": rows,
                }
            ),
            encoding="utf-8",
        )
        return [
            "--plan", str(plan),
            "--repo-root", str(repo),
            "--out", str(out_path),
            "--report", str(report_path),
            "--size", "8",
            "--seed", seed,
        ]

    first_candidates = first_candidates_dir / "candidates-a.txt"
    first_report = shared_dir / "selection-a.json"
    second_candidates = shared_dir / "candidates-b.txt"
    second_report = second_report_dir / "selection-b.json"
    first = _load_selector()
    second = _load_selector()
    first_published_candidates = threading.Event()
    publish_report = threading.Event()
    first_result: list[int] = []

    def pause_between_pair_replacements(
        candidate_output,
        candidate_temporary,
        report_output,
        report_temporary,
    ) -> None:
        try:
            os.unlink(report_output.name, dir_fd=report_output.parent_fd)
            os.fsync(report_output.parent_fd)
        except FileNotFoundError:
            pass
        os.replace(
            candidate_temporary,
            candidate_output.name,
            src_dir_fd=candidate_output.parent_fd,
            dst_dir_fd=candidate_output.parent_fd,
        )
        os.fsync(candidate_output.parent_fd)
        first_published_candidates.set()
        if not publish_report.wait(timeout=10):
            raise AssertionError("timed out waiting to publish the selection report")
        os.replace(
            report_temporary,
            report_output.name,
            src_dir_fd=report_output.parent_fd,
            dst_dir_fd=report_output.parent_fd,
        )
        os.fsync(report_output.parent_fd)

    first._publish_pair = pause_between_pair_replacements
    worker = threading.Thread(
        target=lambda: first_result.append(
            first.main(
                invocation(
                    tmp_path / "first-run",
                    "partial-overlap-first",
                    first_candidates,
                    first_report,
                )
            )
        ),
        daemon=True,
    )
    worker.start()
    try:
        assert first_published_candidates.wait(timeout=10)
        second_result = _run_loaded_selector(
            second,
            invocation(
                tmp_path / "second-run",
                "partial-overlap-second",
                second_candidates,
                second_report,
            ),
        )
        assert second_result.returncode == 2
        assert "another selector is publishing" in second_result.stderr
        assert not second_candidates.exists()
        assert not second_report.exists()
    finally:
        publish_report.set()
        worker.join(timeout=10)

    assert not worker.is_alive()
    assert first_result == [0]
    final_candidates = first_candidates.read_bytes()
    final_report = json.loads(first_report.read_text(encoding="utf-8"))
    assert final_report["candidates_file_sha256"] == hashlib.sha256(
        final_candidates
    ).hexdigest()


def test_independent_selector_process_refuses_symlink_alias_during_publication(
    tmp_path: Path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    commit, tree = _ensure_git_repo(repo)
    output_dir = tmp_path / "shared-output"
    output_dir.mkdir()
    output_alias = tmp_path / "output-alias"
    output_alias.symlink_to(output_dir, target_is_directory=True)
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {"status": "ok", "commit": commit, "tree": tree, "candidates": rows}
        ),
        encoding="utf-8",
    )
    first_tempdir = tmp_path / "process-one-temp"
    second_tempdir = tmp_path / "process-two-temp"
    first_tempdir.mkdir()
    second_tempdir.mkdir()
    marker = tmp_path / "candidate-published"
    release = tmp_path / "publish-report"
    child_source = r"""
import importlib.util
import os
from pathlib import Path
import sys
import time

tool = Path(sys.argv[1])
marker = Path(sys.argv[2])
release = Path(sys.argv[3])
spec = importlib.util.spec_from_file_location("b110_selector_process", tool)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

def pause_between_pair_replacements(candidates, candidates_temporary, report, report_temporary):
    try:
        os.unlink(report.name, dir_fd=report.parent_fd)
        os.fsync(report.parent_fd)
    except FileNotFoundError:
        pass
    os.replace(candidates_temporary, candidates.name, src_dir_fd=candidates.parent_fd, dst_dir_fd=candidates.parent_fd)
    os.fsync(candidates.parent_fd)
    marker.write_text("ready", encoding="utf-8")
    deadline = time.monotonic() + 15
    while not release.exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("timed out waiting to publish the report")
        time.sleep(0.01)
    os.replace(report_temporary, report.name, src_dir_fd=report.parent_fd, dst_dir_fd=report.parent_fd)
    os.fsync(report.parent_fd)

module._publish_pair = pause_between_pair_replacements
raise SystemExit(module.main(sys.argv[4:]))
"""
    args = [
        "--plan", str(plan),
        "--repo-root", str(repo),
        "--out", str(output_dir / "candidates.txt"),
        "--report", str(output_dir / "selection.json"),
        "--size", "8",
        "--seed", "concurrent-first",
    ]
    first_env = os.environ.copy()
    first_env["TMPDIR"] = str(first_tempdir)
    second_result = None
    second_error: Exception | None = None
    marker_timed_out = False
    release_error: Exception | None = None
    first_process = subprocess.Popen(
        [sys.executable, "-c", child_source, str(TOOL), str(marker), str(release), *args],
        cwd=tmp_path,
        env=first_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not marker.exists():
            if first_process.poll() is not None:
                break
            if time.monotonic() >= deadline:
                marker_timed_out = True
                break
            time.sleep(0.01)

        if marker.exists():
            candidates = output_dir / "candidates.txt"
            report = output_dir / "selection.json"
            first_candidates = candidates.read_bytes()
            second_env = os.environ.copy()
            second_env["TMPDIR"] = str(second_tempdir)
            second_args = [
                "--plan", str(plan),
                "--repo-root", str(repo),
                "--out", str(output_alias / "candidates.txt"),
                "--report", str(output_alias / "selection.json"),
                "--size", "8",
                "--seed", "concurrent-second",
            ]
            try:
                second_result = subprocess.run(
                    [sys.executable, str(TOOL), *second_args],
                    cwd=tmp_path,
                    env=second_env,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
            except Exception as exc:
                second_error = exc
    finally:
        first_stdout, first_stderr, release_error = _release_and_reap_selector_process(
            first_process, release
        )

    assert not marker_timed_out, (
        f"first selector did not reach publication marker: {first_stdout}\n{first_stderr}"
    )
    assert marker.exists(), (
        f"first selector exited before candidate publication: {first_stdout}\n{first_stderr}"
    )
    assert release_error is None, f"could not release first selector: {release_error}"
    if second_error is not None:
        pytest.fail(f"contending selector failed to complete: {second_error}")
    assert second_result is not None
    assert second_result.returncode == 2, second_result.stdout + second_result.stderr
    assert "another selector is publishing in an output directory" in second_result.stderr
    assert first_process.returncode == 0, first_stdout + first_stderr
    final_candidates = candidates.read_bytes()
    final_report = json.loads(report.read_text(encoding="utf-8"))
    assert final_candidates == first_candidates
    assert final_report["candidates_file_sha256"] == hashlib.sha256(
        final_candidates
    ).hexdigest()


def test_S8_selector_refuses_size_below_per_file_plus_missing_operator(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    result = _run_selector(tmp_path, rows, repo, "--size", "2")
    assert result.returncode == 2
    assert "size 2 is smaller than per-file (2) plus missing-operator (1) coverage" in result.stderr


def test_S3_selector_fills_exact_size_and_reports_all_rows_for_smaller_plans(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    exact = _run_selector(tmp_path / "exact", rows, repo, "--size", "8")
    assert exact.returncode == 0, exact.stderr
    exact_report = json.loads((tmp_path / "exact" / "selection.json").read_text())
    assert len(exact_report["stratified"]) == 8
    smaller = _run_selector(tmp_path / "smaller", rows, repo, "--size", "64")
    assert smaller.returncode == 0, smaller.stderr
    smaller_report = json.loads((tmp_path / "smaller" / "selection.json").read_text())
    assert len(smaller_report["stratified"]) == len(rows)


def test_S2_selector_adds_an_operator_that_loses_the_per_file_pick(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    relative_a, relative_b = "pkg/a.py", "pkg/b.py"
    source_a = b"A = 1\nB = 2\n"
    source_b = b"C = 3\nD = 4\n"
    for relative, source in ((relative_a, source_a), (relative_b, source_b)):
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source)
    # Use the fixed seed's rank to make the per-file winner on b.py use the
    # common operator, leaving the rare operator for step 2.
    seed = "operator-coverage"
    module = _load_selector()
    ids = [_id(number) for number in range(101, 105)]
    common_b, rare_b = sorted(ids[2:], key=lambda identity: module._rank(seed, identity))
    rows.extend([
        _row(101, path=relative_a, source=source_a, line=1, needle=b"1", operator="op:common", description="common"),
        _row(102, path=relative_a, source=source_a, line=2, needle=b"2", operator="op:common", description="common"),
        _row(int(common_b, 16), path=relative_b, source=source_b, line=1, needle=b"3", operator="op:common", description="common"),
        _row(int(rare_b, 16), path=relative_b, source=source_b, line=2, needle=b"4", operator="op:rare", description="rare"),
    ])
    # The known-hard set is specified for the production plan; this focused
    # strata fixture supplies its own exact hard sites so it can exercise the
    # stratifier without inventing a production adapter catalogue.
    # Exercise the pure selection algorithm directly with the same row schema.
    selected, hard, files, operators, _count = module._select(
        rows,
        seed=seed,
        size=10,
        repo_root=repo,
    )
    assert relative_a in files and relative_b in files
    assert "op:common" in operators and "op:rare" in operators
    assert any(row["id"] == rare_b and row["reason"] == "operator" for row in selected)
    assert hard


def test_S4_selector_refuses_scanner_span_that_does_not_point_at_equality(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    rows[0]["start_byte"] = rows[0]["start_byte"] + 1
    rows[0]["end_byte"] = rows[0]["end_byte"] + 1
    result = _run_selector(tmp_path, rows, repo)
    assert result.returncode == 2
    assert "source does not match plan" in result.stderr


def test_S4_selector_refuses_scanner_span_on_a_different_source_line(
    tmp_path: Path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    rows[0]["start_byte"] = rows[1]["start_byte"]
    rows[0]["end_byte"] = rows[1]["end_byte"]

    result = _run_selector(tmp_path, rows, repo)

    assert result.returncode == 2
    assert "source does not match plan" in result.stderr


def test_S4_selector_refuses_duplicate_scanner_site_matches(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    duplicate = dict(rows[0])
    duplicate["id"] = _id(99)
    rows.append(duplicate)
    result = _run_selector(tmp_path, rows, repo)
    assert result.returncode == 2
    assert "matches 2 plan rows" in result.stderr


def test_S4_selector_refuses_hard_site_inside_differently_named_nested_function(
    tmp_path: Path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    source = (
        b"def _scan_raw_string():\n"
        b"    def nested():\n"
        b"        return None if end == -1 else end + 1\n"
        b"def _strip_comments_and_literals():\n"
        b'    if two == "//":\n'
        b"        return None\n"
        b"    if end == -1:\n"
        b"        return None\n"
        b"    if close == -1:\n"
        b"        return None\n"
        b"    return None\n"
    )
    (repo / GO_PATH).write_bytes(source)
    source_digest = hashlib.sha256(source).hexdigest()
    for row in rows:
        if row["path"] == GO_PATH:
            row["source_sha256"] = source_digest
    rows[0] = _row(
        1,
        path=GO_PATH,
        source=source,
        line=3,
        needle=b"==",
        operator="python:compare-swap",
        description="Eq->NotEq",
    )
    result = _run_selector(tmp_path, rows, repo)
    assert result.returncode == 2
    assert "_scan_raw_string" in result.stderr
    assert "matches 0 plan rows" in result.stderr


def test_S5_selector_uses_utf8_byte_spans_and_excludes_function_bodies(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    source = "# café\n@flag(True)\ndef f(default=True):\n    body = False\n    return body\nclass C:\n    member = True\nTOP = False\n".encode()
    relative = "pkg/nonascii.py"
    (repo / relative).parent.mkdir(parents=True, exist_ok=True)
    (repo / relative).write_bytes(source)
    bool_rows = [row for row in rows if row["operator"] == "python:bool-const-flip"]
    rows = [row for row in rows if row["operator"] != "python:bool-const-flip"]
    # The low-ranked body row must be excluded even though a character-column
    # implementation would misplace the following AST ranges after "café".
    body_line = 4
    body = _row(15, path=relative, source=source, line=body_line, needle=b"False", operator="python:bool-const-flip", description="body")
    default = _row(16, path=relative, source=source, line=3, needle=b"True", operator="python:bool-const-flip", description="default")
    top = _row(17, path=relative, source=source, line=8, needle=b"False", operator="python:bool-const-flip", description="top")
    decorator = _row(18, path=relative, source=source, line=2, needle=b"True", operator="python:bool-const-flip", description="decorator")
    class_body = _row(19, path=relative, source=source, line=7, needle=b"True", operator="python:bool-const-flip", description="class body")
    # Force the body row to rank ahead of every legitimate import-time row.
    seed = "test-nonascii"
    valid_bool_rows = (default, top, decorator, class_body, *bool_rows)
    valid_rank = min(
        hashlib.blake2b((seed + row["id"]).encode("ascii"), digest_size=16).hexdigest()
        for row in valid_bool_rows
    )
    body_id = 1000
    while hashlib.blake2b(
        (seed + _id(body_id)).encode("ascii"), digest_size=16
    ).hexdigest() >= valid_rank:
        body_id += 1
    body["id"] = _id(body_id)
    rows.extend(bool_rows)
    rows.extend((body, default, top, decorator, class_body))
    result = _run_selector(tmp_path, rows, repo, "--seed", seed)
    assert result.returncode == 0, result.stderr
    report = json.loads((tmp_path / "selection.json").read_text())
    known = {item["id"] for item in report["known_hard"] if item["label"].startswith("import-time")}
    assert report["import_time_total"] == 7
    assert body["id"] not in known
    assert known <= {default["id"], top["id"], decorator["id"], class_body["id"], *[row["id"] for row in bool_rows]}


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b'{"status":"ok","status":"ok","candidates":[]}', "duplicate JSON key"),
        (b'{"status":"ok","candidates":[]} trailing', "valid plan JSON"),
        (b'{"status":"fail","candidates":[]}', "status must be 'ok'"),
        (b'{"status":"ok"}', "candidates must be a list"),
    ],
)
def test_S6_selector_rejects_noncanonical_plan_documents(
    tmp_path: Path, raw: bytes, message: str
):
    repo = tmp_path / "repo"
    repo.mkdir()
    result = _run_selector(tmp_path, [], repo, raw_plan=raw)
    assert result.returncode == 2
    assert message in result.stderr


def test_S4_S5_selector_refuses_missing_known_hard_import_time_sites(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    rows, _files = _valid_fixture(repo)
    rows[:] = [row for row in rows if row["operator"] != "python:bool-const-flip"]
    result = _run_selector(tmp_path, rows, repo)
    assert result.returncode == 2
    assert "expected at least two import-time bool-const-flip rows" in result.stderr


def test_selector_accepts_real_assay_plan_and_loader_accepts_its_output(
    tmp_path: Path,
):
    project_root = Path(__file__).resolve().parents[2]
    plan_stdout, plan_stderr = io.StringIO(), io.StringIO()
    plan_code = assay_cli.main(
        [
            "plan",
            "self-qualification",
            "--file",
            str(project_root / "assay.toml"),
            "--allow-dirty",
        ],
        stdout=plan_stdout,
        stderr=plan_stderr,
    )
    assert plan_code == 0, plan_stderr.getvalue()
    plan_document = json.loads(plan_stdout.getvalue())
    rows = plan_document["candidates"]
    assert rows
    assert set(rows[0]) == {
        "id",
        "path",
        "operator",
        "start_byte",
        "end_byte",
        "lineno",
        "description",
        "source_sha256",
        "mutated_file_sha256",
    }
    plan_path = tmp_path / "actual-plan.json"
    plan_path.write_text(plan_stdout.getvalue(), encoding="utf-8")
    subprocess.run(
        ["git", "-c", "maintenance.auto=false", "-c", "maintenance.autoDetach=false",
         "-c", "gc.autoDetach=false", "clone", "--quiet", "--shared", "--no-checkout",
         str(project_root.parent), str(tmp_path / "clean-repo")],
        check=True,
        capture_output=True,
    )
    clean_repo_root = tmp_path / "clean-repo"
    _git(clean_repo_root, "checkout", "--quiet", "--detach", plan_document["commit"])
    candidates = tmp_path / "actual-candidates.txt"
    report = tmp_path / "actual-selection.json"
    selector = _load_selector()
    selection_stdout, selection_stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(selection_stdout), contextlib.redirect_stderr(
        selection_stderr
    ):
        selection_code = selector.main(
            [
                "--plan",
                str(plan_path),
                "--repo-root",
                str(clean_repo_root),
                "--out",
                str(candidates),
                "--report",
                str(report),
            ]
        )
    assert selection_code == 0, selection_stderr.getvalue() + selection_stdout.getvalue()

    lane = assay_cli._resolve_lane_file(project_root / "assay.toml").lane(
        "self-qualification"
    )
    assert lane.judge is not None and lane.judge.mutation is not None
    selected_ids, _file_digest = assay_cli._parse_candidates_file(
        candidates, max_mutants=lane.judge.mutation.max_mutants
    )
    assert selected_ids
    selection_document = json.loads(report.read_text(encoding="utf-8"))
    expected_ids = [row["id"] for row in rows if row["id"] in selected_ids]
    assert selection_document["selection_sha256"] == assay_cli.mutation.plan_sha256(
        expected_ids
    )
    assert candidates.stat().st_size <= assay_cli._PILOT_CANDIDATE_FILE_LIMIT


def test_selector_rejects_empty_plan(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    empty = _run_selector(
        tmp_path / "empty",
        [],
        repo,
        raw_plan=b'{"status":"ok","candidates":[]}',
    )
    assert empty.returncode == 2
    assert "no candidates" in empty.stderr
