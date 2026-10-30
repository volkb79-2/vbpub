"""D-001: a release retry keeps the exact per-project version plan of its first attempt.

The durable record, its validation, the plan decision (new, recorded, legacy,
override), the committed-candidate proof, and the release child's use of the
plan. The real-Git retry scenarios live in ``test_release_end_to_end_real_git``.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli, exit_codes, transaction, version
from cmru.version import ReleasePlanRefused

pytestmark = pytest.mark.real_release_version_plan

BASE = "a" * 40


def _workspace(tmp_path: Path, base: str = BASE) -> transaction.ReleaseWorkspace:
    return transaction.ReleaseWorkspace(
        tmp_path, tmp_path / "candidate", "cmru-release-20261018_120000-demo-abcdef", base,
    )


@pytest.fixture
def scope(tmp_path, monkeypatch):
    directory = tmp_path / "scope"
    directory.mkdir()
    monkeypatch.setattr(transaction, "_scope_dir", lambda _root: directory)
    monkeypatch.setattr(transaction, "_ensure_scope_dir", lambda _root: directory)
    return directory


def _record_path(scope_dir: Path, workspace) -> Path:
    return scope_dir / f"{transaction._release_token(workspace)}.version-plan.json"


# --- the durable record ------------------------------------------------------


def test_version_plan_round_trips_bound_to_its_transaction_and_is_written_once(tmp_path, scope):
    workspace = _workspace(tmp_path)
    assert transaction.read_release_version_plan(tmp_path, workspace) is None

    transaction.write_release_version_plan(tmp_path, workspace, {"demo": "6.2.0", "raw": None})

    assert transaction.read_release_version_plan(tmp_path, workspace) == {
        "demo": "6.2.0", "raw": None,
    }
    document = json.loads(_record_path(scope, workspace).read_text(encoding="utf-8"))
    assert document["transaction"] == workspace.branch and document["base"] == BASE
    with pytest.raises(RuntimeError, match="already exists"):
        transaction.write_release_version_plan(tmp_path, workspace, {"demo": "6.3.0"})
    assert transaction.read_release_version_plan(tmp_path, workspace) == {
        "demo": "6.2.0", "raw": None,
    }
    transaction.forget_release_scope(tmp_path, workspace)
    assert not _record_path(scope, workspace).exists()


@pytest.mark.parametrize("versions", [{}, {"": "1.0.0"}, {"demo": "-1"}, {"demo": "1 0"}])
def test_version_plan_writer_refuses_an_empty_or_invalid_plan(tmp_path, scope, versions):
    with pytest.raises(RuntimeError, match="empty or names an invalid version"):
        transaction.write_release_version_plan(tmp_path, _workspace(tmp_path), versions)
    assert list(scope.iterdir()) == []


def test_version_plan_writer_removes_a_partial_file_after_a_write_failure(
    tmp_path, scope, monkeypatch,
):
    workspace = _workspace(tmp_path)
    descriptor = {}

    def fail_after_open(fd, *_args, **_kwargs):
        descriptor["fd"] = fd
        raise OSError("disk full")

    monkeypatch.setattr(transaction.os, "fdopen", fail_after_open)
    with pytest.raises(OSError, match="disk full"):
        transaction.write_release_version_plan(tmp_path, workspace, {"demo": "1.0.0"})
    os.close(descriptor["fd"])
    assert not _record_path(scope, workspace).exists()


@pytest.mark.parametrize("document", [
    {"schema_version": 1},
    {"schema_version": 2, "transaction": "T", "base": BASE, "versions": {"demo": "1.0.0"}},
    {"schema_version": True, "transaction": "T", "base": BASE, "versions": {"demo": "1.0.0"}},
    {"schema_version": 1, "transaction": "other", "base": BASE, "versions": {"demo": "1.0.0"}},
    {"schema_version": 1, "transaction": "T", "base": "b" * 40, "versions": {"demo": "1.0.0"}},
    {"schema_version": 1, "transaction": "T", "base": BASE, "versions": {}},
    {"schema_version": 1, "transaction": "T", "base": BASE, "versions": ["demo"]},
    {"schema_version": 1, "transaction": "T", "base": BASE, "versions": {"demo": 1}},
    {"schema_version": 1, "transaction": "T", "base": BASE, "versions": {"demo": "../1"}},
    {"schema_version": 1, "transaction": "T", "base": BASE, "versions": {"": "1.0.0"}},
    ["not", "an", "object"],
])
def test_version_plan_reader_refuses_a_malformed_or_foreign_record(tmp_path, scope, document):
    workspace = _workspace(tmp_path)
    if isinstance(document, dict) and document.get("transaction") == "T":
        document = {**document, "transaction": workspace.branch}
    _record_path(scope, workspace).write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(RuntimeError, match="malformed or does not match"):
        transaction.read_release_version_plan(tmp_path, workspace)


def test_version_plan_reader_refuses_unreadable_nonregular_and_uninspectable_records(
    tmp_path, scope, monkeypatch,
):
    workspace = _workspace(tmp_path)
    record = _record_path(scope, workspace)
    record.mkdir()
    with pytest.raises(RuntimeError, match="not a regular file"):
        transaction.read_release_version_plan(tmp_path, workspace)
    record.rmdir()
    record.write_text("{not json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="cannot read release version plan"):
        transaction.read_release_version_plan(tmp_path, workspace)
    original_lstat = Path.lstat

    def denied(path):
        if path == record:
            raise PermissionError("denied")
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", denied)
    with pytest.raises(RuntimeError, match="cannot inspect release version plan.*denied"):
        transaction.read_release_version_plan(tmp_path, workspace)


def test_origin_preflight_keeps_git_diagnostics_as_text(tmp_path):
    repo = tmp_path / "without-origin"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    expected = subprocess.run(
        ["git", "remote", "get-url", "--all", "origin"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )

    with pytest.raises(ReleasePlanRefused) as raised:
        version._require_origin_remote(repo, purpose="build a release plan")

    assert expected.returncode != 0
    assert str(raised.value).endswith(f"({expected.stderr.strip()})")


def test_origin_preflight_refuses_a_failed_lookup_even_with_partial_output(
    monkeypatch, tmp_path,
):
    result = subprocess.CompletedProcess(
        ["git", "remote", "get-url", "--all", "origin"],
        1,
        stdout="partial URL\n",
        stderr="remote lookup failed\n",
    )
    monkeypatch.setattr(version, "run_local_git", lambda *_args, **_kwargs: result)

    with pytest.raises(ReleasePlanRefused, match="origin remote is not configured"):
        version._require_origin_remote(tmp_path, purpose="build a release plan")


def test_origin_preflight_prefers_stderr_when_git_returns_both_streams(
    monkeypatch, tmp_path,
):
    result = subprocess.CompletedProcess(
        ["git", "remote", "get-url", "--all", "origin"],
        1,
        stdout="stdout diagnostic\n",
        stderr="stderr diagnostic\n",
    )
    monkeypatch.setattr(version, "run_local_git", lambda *_args, **_kwargs: result)

    with pytest.raises(ReleasePlanRefused) as raised:
        version._require_origin_remote(tmp_path, purpose="build a release plan")

    assert "stderr diagnostic" in str(raised.value)
    assert "stdout diagnostic" not in str(raised.value)


def test_origin_preflight_uses_its_fallback_when_git_returns_no_diagnostics(
    monkeypatch, tmp_path,
):
    result = subprocess.CompletedProcess(
        ["git", "remote", "get-url", "--all", "origin"],
        1,
        stdout="",
        stderr="",
    )
    monkeypatch.setattr(version, "run_local_git", lambda *_args, **_kwargs: result)

    with pytest.raises(ReleasePlanRefused) as raised:
        version._require_origin_remote(tmp_path, purpose="build a release plan")

    assert "origin has no configured URL" in str(raised.value)


# --- the plan decision -------------------------------------------------------


CONFIGS = {
    "demo": SimpleNamespace(name="demo", git_tag=True, version=None),
    "raw": SimpleNamespace(name="raw", git_tag=False, version=None),
}


@pytest.fixture
def decision(tmp_path, scope, monkeypatch):
    """The plan decision with its Git reads controlled: HEAD, flag planning, evidence."""
    state = {"head": BASE, "evidence": {"demo": None}, "planned": {}}
    monkeypatch.setattr(cli, "_git", lambda _root, *args: state["head"])

    def planned(_root, configs, name, *, minor, major, set_version):
        if not cli._cmru_derives_version(configs[name]):
            return None
        return state["planned"][(minor, major, set_version)]

    monkeypatch.setattr(cli, "_planned_release_version", planned)
    monkeypatch.setattr(
        cli, "_committed_release_versions",
        lambda _root, _workspace, _configs, names: {name: state["evidence"][name] for name in names},
    )
    workspace = _workspace(tmp_path)

    def run(names=("demo", "raw"), *, minor=False, major=False, set_version=None, dry_run=False):
        return cli._release_version_plan(
            tmp_path, workspace, CONFIGS, list(names),
            minor=minor, major=major, set_version=set_version, dry_run=dry_run,
        )

    return SimpleNamespace(state=state, run=run, workspace=workspace, tmp_path=tmp_path, scope=scope)


def test_nothing_to_release_needs_no_plan(decision):
    assert decision.run(names=()) is None


def test_a_new_transaction_records_the_plan_its_flags_select_before_release(decision, capsys):
    decision.state["planned"][(True, False, None)] = "6.2.0"

    assert decision.run(minor=True) == {"demo": "6.2.0", "raw": None}

    assert transaction.read_release_version_plan(decision.tmp_path, decision.workspace) == {
        "demo": "6.2.0", "raw": None,
    }
    assert "Release version plan recorded: demo 6.2.0, raw (not CMRU-derived)" in capsys.readouterr().out


def test_a_new_dry_run_records_nothing_and_keeps_the_flags(decision):
    assert decision.run(minor=True, dry_run=True) is None
    assert transaction.read_release_version_plan(decision.tmp_path, decision.workspace) is None


def test_a_retry_reuses_the_recorded_plan_without_recomputing_it(decision, capsys):
    transaction.write_release_version_plan(
        decision.tmp_path, decision.workspace, {"demo": "6.2.0", "raw": None},
    )
    decision.state["head"] = "c" * 40  # the first attempt committed its candidate
    decision.state["evidence"] = {"demo": "6.2.0"}

    assert decision.run() == {"demo": "6.2.0", "raw": None}
    assert decision.run(dry_run=True) == {"demo": "6.2.0", "raw": None}
    assert "Resuming with the recorded version plan: demo 6.2.0" in capsys.readouterr().out


def test_a_retry_accepts_an_override_that_selects_the_same_version(decision):
    transaction.write_release_version_plan(decision.tmp_path, decision.workspace, {"demo": "6.2.0"})
    decision.state["planned"][(True, False, None)] = "6.2.0"

    assert decision.run(names=("demo",), minor=True) == {"demo": "6.2.0"}


def test_a_conflicting_override_refuses_and_leaves_the_record_unchanged(decision):
    transaction.write_release_version_plan(decision.tmp_path, decision.workspace, {"demo": "6.2.0"})
    before = _record_path(decision.scope, decision.workspace).read_bytes()
    decision.state["planned"][(False, False, "9.9.9")] = "9.9.9"

    with pytest.raises(ReleasePlanRefused, match=r"conflicts for: demo 6\.2\.0 \(override selects 9\.9\.9\)"):
        decision.run(names=("demo",), set_version="9.9.9")
    assert _record_path(decision.scope, decision.workspace).read_bytes() == before


@pytest.mark.parametrize("recorded,names", [
    ({"other": "1.0.0"}, ("demo",)),        # the project has no recorded version
    ({"demo": None}, ("demo",)),            # a CMRU-versioned project recorded as not derived
    ({"raw": "1.0.0"}, ("raw",)),           # a no-tag project recorded with a version
])
def test_a_retry_without_a_trustworthy_recorded_version_refuses(decision, recorded, names):
    transaction.write_release_version_plan(decision.tmp_path, decision.workspace, recorded)

    with pytest.raises(ReleasePlanRefused, match=f"no trustworthy recorded version for: {names[0]}"):
        decision.run(names=names)


def test_a_retry_whose_committed_candidate_names_another_version_refuses(decision):
    transaction.write_release_version_plan(decision.tmp_path, decision.workspace, {"demo": "6.2.0"})
    decision.state["evidence"] = {"demo": "6.1.1"}

    with pytest.raises(ReleasePlanRefused, match="no trustworthy recorded version for: demo"):
        decision.run(names=("demo",))


@pytest.mark.parametrize("legacy_state", ["scope", "results", "moved-head"])
def test_a_legacy_retry_resumes_with_the_version_its_candidate_proves(
    decision, capsys, legacy_state,
):
    if legacy_state == "scope":
        transaction.write_release_scope(decision.tmp_path, decision.workspace, ["demo"])
    elif legacy_state == "results":
        transaction.write_release_result(decision.tmp_path, decision.workspace, "raw", "source-abc")
    else:
        decision.state["head"] = "c" * 40
    decision.state["evidence"] = {"demo": "6.2.0"}

    assert decision.run() == {"demo": "6.2.0", "raw": None}
    assert "Resuming with committed candidate evidence: demo 6.2.0" in capsys.readouterr().out
    assert transaction.read_release_version_plan(decision.tmp_path, decision.workspace) is None


def test_a_legacy_retry_without_committed_proof_refuses_before_any_change(decision):
    transaction.write_release_scope(decision.tmp_path, decision.workspace, ["demo"])

    with pytest.raises(ReleasePlanRefused) as refused:
        decision.run()

    assert "has no recorded version plan" in str(refused.value)
    assert "does not prove the version for: demo" in str(refused.value)
    assert "abandon this candidate" in str(refused.value)


def test_a_legacy_retry_also_refuses_a_conflicting_override(decision):
    decision.state["head"] = "c" * 40
    decision.state["evidence"] = {"demo": "6.2.0"}
    decision.state["planned"][(False, True, None)] = "7.0.0"

    with pytest.raises(ReleasePlanRefused, match=r"demo 6\.2\.0 \(override selects 7\.0\.0\)"):
        decision.run(major=True)


# --- planning helpers --------------------------------------------------------


def test_only_tagged_cmru_versioned_projects_get_a_planned_version(monkeypatch, tmp_path):
    from cmru import changelog

    calls = []

    def plan(_root, project, **kwargs):
        calls.append((project.name, kwargs["minor"], kwargs["major"], kwargs["set_version"]))
        return "6.2.0", "demo-v6.1.0"

    monkeypatch.setattr(changelog, "_project_release_plan", plan)
    monkeypatch.setattr(cli, "_known_project_scope_names", lambda _configs: frozenset())
    configs = {
        "demo": SimpleNamespace(name="demo", git_tag=True, version=None),
        "ext": SimpleNamespace(
            name="ext", git_tag=True, version=SimpleNamespace(strategy="external:VERSION"),
        ),
        "raw": SimpleNamespace(name="raw", git_tag=False, version=None),
    }

    assert [
        cli._planned_release_version(
            tmp_path, configs, name, minor=True, major=False, set_version=None,
        )
        for name in ("demo", "ext", "raw")
    ] == ["6.2.0", None, None]
    assert calls == [("demo", True, False, None)]


def test_version_flags_pin_a_planned_version_and_otherwise_keep_the_invocation_flags():
    plan = {"demo": "6.2.0", "raw": None}
    assert cli._release_version_flags(plan, "demo", minor=True, major=False, set_version=None) == {
        "minor": False, "major": False, "set_version": "6.2.0",
    }
    assert cli._release_version_flags(plan, "raw", minor=False, major=False, set_version=None) == {
        "minor": False, "major": False, "set_version": None,
    }
    assert cli._release_version_flags(None, "demo", minor=True, major=False, set_version=None) == {
        "minor": True, "major": False, "set_version": None,
    }


def _git_repo(tmp_path: Path, changelog: str | None) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    (repo / "demo").mkdir(parents=True)
    run = lambda *args: subprocess.run(  # noqa: E731
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()
    run("init", "-q", "-b", "main")
    run("config", "user.name", "t")
    run("config", "user.email", "t@t")
    (repo / "demo" / "a.txt").write_text("a\n", encoding="utf-8")
    if changelog is not None:
        (repo / "demo" / "CHANGES.md").write_text(changelog, encoding="utf-8")
    run("add", "-A")
    run("commit", "-q", "-m", "base")
    return repo, run("rev-parse", "HEAD")


def _commit_changelog(repo: Path, text: str) -> None:
    (repo / "demo" / "CHANGES.md").write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "release inputs"], cwd=repo, check=True)


HISTORY = "# Changes\n\n## [Unreleased]\n\n## [1.0.0] - 2026-01-01\n- first\n"
PROJECT = SimpleNamespace(name="demo", cwd="demo", changelog="CHANGES.md")


@pytest.mark.parametrize("candidate,proven", [
    (HISTORY.replace("## [1.0.0]", "## [1.1.0] - 2026-10-18\n- next\n\n## [1.0.0]"), "1.1.0"),
    (HISTORY, None),
    (HISTORY.replace(
        "## [1.0.0]", "## [1.2.0] - 2026-10-18\n- b\n\n## [1.1.0] - 2026-10-17\n- a\n\n## [1.0.0]",
    ), None),
])
def test_committed_candidate_proves_exactly_one_new_dated_section(tmp_path, candidate, proven):
    repo, base = _git_repo(tmp_path, HISTORY)
    _commit_changelog(repo, candidate + "\n")

    assert cli._committed_release_versions(
        repo, _workspace(repo, base), {"demo": PROJECT}, ["demo"],
    ) == {"demo": proven}


def test_committed_candidate_proof_handles_a_new_changelog_and_a_project_without_one(tmp_path):
    repo, base = _git_repo(tmp_path, None)
    _commit_changelog(repo, "# Changes\n\n## [0.1.0] - 2026-10-18\n- first\n")
    bare = SimpleNamespace(name="bare", cwd="demo", changelog=None)

    assert cli._committed_release_versions(
        repo, _workspace(repo, base), {"demo": PROJECT, "bare": bare}, ["demo", "bare"],
    ) == {"demo": "0.1.0", "bare": None}


# --- the release child -------------------------------------------------------


def _child_config(tmp_path):
    project = cli.ProjectConfig("demo", {}, {}, prefix="demo-v", github_token="token")
    return (
        tmp_path, {"demo": project}, ["demo"], ["demo"], ["demo"], "project-first", {},
        cli.CleanupConfig([], [], [], []), cli.GitHubConfig("o", "r", "token", "user"),
        cli.ReleaseEnvConfig({}, None),
    )


@pytest.fixture
def child(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(transaction, "read_release_source", lambda *_args: None)
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: _child_config(tmp_path))
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    monkeypatch.setattr(version, "detect_changed_projects", lambda *a, **k: [("demo", "changed")])
    monkeypatch.setenv(transaction.BRANCH_ENV, "cmru/release/child")
    monkeypatch.setenv(transaction.BASE_ENV, BASE)
    monkeypatch.setattr(transaction, "write_release_scope", lambda *a: calls.append("scope"))
    monkeypatch.setattr(transaction, "push_backup_branch", lambda *a, **k: calls.append("backup"))
    monkeypatch.setattr(transaction, "mark_plan_refused", lambda *a: calls.append("refused"))
    monkeypatch.setattr(
        cli, "_release_projects_sequentially",
        lambda *a, **k: calls.append(("sequential", k["version_plan"])) or [],
    )
    monkeypatch.setattr(
        version, "release_cmd", lambda *a, **k: calls.append(("release_cmd", a[1], k)),
    )
    monkeypatch.setattr(cli.transaction, "is_transaction_child", lambda _root, **_kwargs: True)
    return SimpleNamespace(calls=calls, config=str(tmp_path / "cmru.toml"))


def test_the_child_refuses_a_conflicting_plan_before_scope_backup_or_release(
    child, monkeypatch, capsys,
):
    def refuse(*_a, **_k):
        raise ReleasePlanRefused("the version override conflicts for: demo")

    monkeypatch.setattr(cli, "_release_version_plan", refuse)

    assert cli.main(["release", "--config", child.config, "--minor"]) == exit_codes.REFUSED
    assert child.calls == ["refused"]
    assert "the version override conflicts for: demo" in capsys.readouterr().err


def test_the_child_hands_its_plan_to_the_sequential_release(child, monkeypatch):
    seen = {}

    def plan(_root, _workspace, _configs, names, **flags):
        seen.update(names=list(names), **flags)
        return {"demo": "6.2.0"}

    monkeypatch.setattr(cli, "_release_version_plan", plan)

    cli.main(["release", "--config", child.config, "--minor"])

    assert seen == {
        "names": ["demo"], "minor": True, "major": False, "set_version": None, "dry_run": False,
    }
    assert child.calls == ["scope", "backup", ("sequential", {"demo": "6.2.0"})]


def test_a_retry_dry_run_previews_each_project_at_its_recorded_version(child, monkeypatch):
    monkeypatch.setattr(cli, "_release_version_plan", lambda *a, **k: {"demo": "6.2.0"})

    cli.main(["release", "--config", child.config, "--dry-run"])

    ((name, projects, kwargs),) = [call for call in child.calls if call[0] == "release_cmd"]
    assert list(projects) == ["demo"]
    assert (kwargs["minor"], kwargs["major"], kwargs["set_version"], kwargs["dry_run"]) == (
        False, False, "6.2.0", True,
    )
    assert "scope" not in child.calls and "backup" not in child.calls
