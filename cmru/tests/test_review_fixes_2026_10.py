"""Regressions for the adversarial review of the 2026-10 cmru changes (H1-H2, M1-M4, L1-L5)."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import cli, config, credential_handoff, doctor, transaction
from cmru.errors import UnsafeRecord, UsageRefusal
from cmru.runner import StepConfig

from tests.test_cli_build_output_semantics import _materialized_output


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@x"},
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A clone whose origin/main holds commit A while the clone also has unpushed B."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    _git(work, "checkout", "-b", "main")
    (work / "a.txt").write_text("a")
    _git(work, "add", "a.txt")
    _git(work, "commit", "-m", "A")
    _git(work, "push", "origin", "main")
    published = _git(work, "rev-parse", "HEAD")
    _git(work, "checkout", "-b", "feature")
    (work / "b.txt").write_text("b")
    _git(work, "add", "b.txt")
    _git(work, "commit", "-m", "B")
    return SimpleNamespace(root=work, published=published, unpushed=_git(work, "rev-parse", "HEAD"))


# ─── H1: a HEAD build of an unpushed commit is not publishable ──────────────────

def test_head_build_of_an_unpushed_commit_is_refused_with_exit_4(repo):
    manifest = {"source_mode": "head", "source_commit": repo.unpushed}
    with pytest.raises(UnsafeRecord) as exc:
        transaction.assert_build_source_publishable(repo.root, "alpha", manifest)
    assert exc.value.exit_code == 4
    assert "--from-origin-main" in str(exc.value) and "origin/main" in str(exc.value)


def test_head_build_of_a_commit_contained_in_origin_main_is_publishable(repo):
    transaction.assert_build_source_publishable(
        repo.root, "alpha", {"source_mode": "head", "source_commit": repo.published},
    )


def test_origin_main_builds_and_genuine_old_records_keep_working(repo):
    transaction.assert_build_source_publishable(
        repo.root, "alpha", {"source_mode": "origin-main", "source_commit": repo.unpushed},
    )
    # no source_mode: checked like a HEAD build, so a pre-field origin/main build passes ...
    transaction.assert_build_source_publishable(
        repo.root, "alpha", {"source_commit": repo.published},
    )


def test_a_record_without_source_mode_is_not_trusted_as_origin_main(repo):
    """A dev cmru wrote HEAD builds without the field after KI-95 (review LOW a)."""
    with pytest.raises(UnsafeRecord):
        transaction.assert_build_source_publishable(
            repo.root, "alpha", {"source_commit": repo.unpushed},
        )


def test_a_failed_fetch_fails_closed_with_exit_4(repo, monkeypatch):
    def boom(*_a, **_k):
        raise subprocess.CalledProcessError(128, ["git", "fetch"])

    monkeypatch.setattr(transaction, "fetch_origin_main", boom)
    with pytest.raises(UnsafeRecord, match="cannot verify") as exc:
        transaction.assert_build_source_publishable(
            repo.root, "alpha", {"source_mode": "head", "source_commit": repo.published},
        )
    assert exc.value.exit_code == 4


def test_build_manifest_source_mode_is_optional_but_closed(tmp_path):
    for mode, ok in (("head", True), ("origin-main", True), ("bogus", False)):
        root = tmp_path / mode
        _project, artifact_root, _logs, _artifact, manifest, manifest_path = _materialized_output(root / "alpha")
        manifest["source_mode"] = mode
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        if ok:
            transaction.validate_build_output_tree(artifact_root, "alpha", artifact_root.name)
        else:
            with pytest.raises(RuntimeError, match="does not authorize publication"):
                transaction.validate_build_output_tree(artifact_root, "alpha", artifact_root.name)


# ─── H2: the gh token is resolved lazily ────────────────────────────────────────

def test_real_estate_config_loads_without_a_gh_binary(monkeypatch):
    config.reset_gh_token_cache()

    def no_gh(*_a, **_k):
        raise FileNotFoundError("gh")

    monkeypatch.setattr(config.subprocess, "run", no_gh)
    estate = Path(__file__).resolve().parents[2] / "cmru.orchestration.toml"
    forge = config.load_forge_config(estate)
    assert forge.github.token_source == "gh"
    with pytest.raises(SystemExit):  # only reading the credential needs gh
        forge.github.token


def test_load_config_and_git_auth_do_not_read_the_token(tmp_path, monkeypatch):
    from tests.test_token_source import _write

    config.reset_gh_token_cache()
    calls = []
    monkeypatch.setattr(config.subprocess, "run", lambda *a, **k: calls.append(a))
    _root, configs, *rest = cli.load_config(_write(tmp_path, '"gh"'), validate_dependencies=False)
    cli._git_auth_for_repository(rest[-2])
    assert calls == [] and configs["alpha"].token_source == "gh"
    assert "gh token" in repr(config.raw_field(configs["alpha"], "github_token"))


# ─── M1: delete:packages in every GHCR scope hint ───────────────────────────────

def test_gh_scope_hint_includes_delete_packages():
    assert "delete:packages" in config.GH_PACKAGES_HINT


def _step(name, **kw):
    base = dict(
        name=name, commands=[], bake_set_prefix=None, bake_set_vars=[], no_cache_env=None,
        clean_dirs=[], required_env=[], login=None, step_env={}, env_command=None,
    )
    return StepConfig(**{**base, **kw})


# ─── L3 / gh laziness in apply_release_env ──────────────────────────────────────

def test_gh_login_is_not_resolved_into_the_process_environment(monkeypatch):
    for key in ("GITHUB_PUSH_PAT", "GH_TOKEN", "GH_ENTERPRISE_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GH_TOKEN", "ambient")
    gh = cli.GitHubConfig("o", "r", "secret", "user", token_source="gh")
    cli.apply_release_env(gh, cli.ReleaseEnvConfig({}, None))
    assert "GITHUB_PUSH_PAT" not in os.environ  # injected per step instead (lazy)
    assert "GH_TOKEN" not in os.environ  # L3
    legacy = cli.GitHubConfig("o", "r", "secret", "user", token_source="cmru")
    cli.apply_release_env(legacy, cli.ReleaseEnvConfig({}, None))
    assert os.environ["GITHUB_PUSH_PAT"] == "secret"
    monkeypatch.delenv("GITHUB_PUSH_PAT", raising=False)


def test_git_hooks_never_see_gh_variables(monkeypatch):
    from cmru.git_auth import without_publisher_tokens

    monkeypatch.setenv("GH_TOKEN", "x")
    monkeypatch.setenv("GH_ENTERPRISE_TOKEN", "y")
    with without_publisher_tokens():
        assert "GH_TOKEN" not in os.environ and "GH_ENTERPRISE_TOKEN" not in os.environ
    assert os.environ["GH_TOKEN"] == "x"


# ─── M4: dirty tracked files outside the project dirs ───────────────────────────

def test_dirty_tracked_files_outside_the_selected_projects_are_listed(repo):
    root = repo.root
    (root / "alpha").mkdir()
    (root / "alpha" / "f.txt").write_text("1")
    (root / "libs").mkdir()
    (root / "libs" / "l.txt").write_text("1")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "dirs")
    (root / "alpha" / "f.txt").write_text("2")
    (root / "libs" / "l.txt").write_text("2")
    (root / "untracked.txt").write_text("u")
    projects = {"alpha": SimpleNamespace(project_root=root / "alpha")}
    assert cli._dirty_tracked_outside_projects(root, projects, ["alpha"]) == ["libs/l.txt"]


# ─── L1: no --secret-file under gh; gh-aware advice ─────────────────────────────

def test_secret_file_is_refused_under_gh():
    gh = cli.GitHubConfig("o", "r", "", "user", token_source="gh")
    with pytest.raises(UsageRefusal, match="token_source = 'gh'"):
        cli.refuse_secret_file_under_gh(Path("/x/cmru.secret.toml"), gh)
    cli.refuse_secret_file_under_gh(None, gh)
    cli.refuse_secret_file_under_gh(
        Path("/x"), cli.GitHubConfig("o", "r", "", "user", token_source="cmru"),
    )


def test_secret_advice_is_gh_aware(tmp_path):
    configs = {"alpha": SimpleNamespace(token_source="gh")}
    advice = cli._main_tree_secret_advice(tmp_path / "c.toml", tmp_path, configs, ["alpha"])
    assert "gh auth login" in advice and "--secret-file" not in advice


# ─── L2: doctor reports the declared source even if the config does not load ────

def test_doctor_reports_declared_gh_when_the_config_is_otherwise_invalid(tmp_path, monkeypatch):
    config.reset_gh_token_cache()
    (tmp_path / "cmru.toml").write_text('[github]\ntoken_source = "gh"\nbogus = 1\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        config.subprocess, "run",
        lambda argv, **k: subprocess.CompletedProcess(argv, 0, "zzsecret9\n", ""),
    )
    result = doctor.check_credentials()
    assert result.status == "ok" and result.details == {"token_source": "gh"}
    assert "scopes" in result.summary and "zzsecret9" not in repr(result)


def test_doctor_fails_under_gh_without_a_login(tmp_path, monkeypatch):
    config.reset_gh_token_cache()
    (tmp_path / "cmru.toml").write_text('[github]\ntoken_source = "gh"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        config.subprocess, "run",
        lambda argv, **k: subprocess.CompletedProcess(argv, 1, "", "not logged in"),
    )
    assert doctor.check_credentials().status == "fail"


# ─── L4: snapshot labels name the real source ───────────────────────────────────

def test_snapshot_label_names_origin_main_or_the_checked_out_commit(repo):
    _git(repo.root, "fetch", "origin")
    assert cli._snapshot_source_name(repo.root, repo.published) == "origin/main"
    assert cli._snapshot_source_name(repo.root, repo.unpushed) == "checked-out commit"


# ─── Round 2 ────────────────────────────────────────────────────────────────────
from tests.test_cli_build_output_semantics import (  # noqa: E402
    OUTPUT_ID, SOURCE_COMMIT, _patch_config, _project, _record,
)
from tests.test_token_source import _write as _write_gh_project  # noqa: E402


@pytest.fixture
def gh_tripwire(monkeypatch):
    """Any attempt to read the gh login fails the test."""
    config.reset_gh_token_cache()
    calls = []

    def tripwire():
        calls.append(1)
        raise AssertionError("gh was consulted")

    monkeypatch.setattr(config, "_gh_token", tripwire)
    for key in ("GITHUB_PUSH_PAT", "GITHUB_TOKEN", "GH_TOKEN", "CMRU_RELEASE_TRANSACTION_CHILD"):
        monkeypatch.delenv(key, raising=False)
    return calls


@pytest.fixture
def gh_project(tmp_path, monkeypatch):
    path = _write_gh_project(tmp_path, '"gh"')
    for args in (("init", "-q", "-b", "main"), ("config", "user.email", "a@b"),
                 ("config", "user.name", "a"), ("add", "."), ("commit", "-qm", "i")):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True, capture_output=True)
    monkeypatch.chdir(tmp_path)
    return path


def test_status_and_a_build_dry_run_need_no_gh_login(gh_project, gh_tripwire):
    assert cli.main(["status", "--config", str(gh_project)]) == 0
    assert cli.main(["build", "alpha", "--dry-run", "--config", str(gh_project)]) == 0
    assert gh_tripwire == []


def test_family_rebasing_and_hashing_keep_the_token_lazy(gh_project, gh_tripwire, tmp_path):
    _root, configs, *rest = cli.load_config(gh_project, validate_dependencies=False)
    cli._configs_for_git_family(configs, ["alpha"], tmp_path)
    hash(rest[-2])
    cli.github_for_project(rest[-2], configs["alpha"])
    assert gh_tripwire == []


def test_build_handoff_is_best_effort_without_a_gh_login(gh_project, monkeypatch):
    config.reset_gh_token_cache()
    monkeypatch.setattr(config, "_gh_token", lambda: (_ for _ in ()).throw(SystemExit(2)))
    _root, configs, *rest = cli.load_config(gh_project, validate_dependencies=False)
    handoff = cli._credential_handoff(rest[-2], configs, ["alpha"], best_effort=True)
    assert handoff.root == "" and handoff.projects == {"alpha": ""}
    with pytest.raises(SystemExit):  # a release still needs the credential
        cli._credential_handoff(rest[-2], configs, ["alpha"])


def test_the_handoff_payload_keeps_its_three_key_shape():
    payload = credential_handoff.encode(credential_handoff.CredentialHandoff(root="r"))
    assert set(json.loads(payload)) == {"schema_version", "root", "projects"}


# end-to-end (LOW b)
def test_publish_build_output_runs_the_source_gate_before_any_push(monkeypatch, tmp_path):
    project = _project("alpha", tmp_path)
    _patch_config(monkeypatch, tmp_path, {"alpha": project})
    record = _record(tmp_path)
    record["manifest"]["source_mode"] = "head"
    monkeypatch.setattr(transaction, "validate_retained_build_output", lambda *_a: record)
    monkeypatch.setattr(cli, "require_project_publish_credentials", lambda *_a: None)
    monkeypatch.setattr(
        transaction, "project_git_family_groups", lambda _r, sel: {tmp_path: list(sel)},
    )
    monkeypatch.setattr(cli, "_configs_for_git_family", lambda configs, _n, _r: configs)
    monkeypatch.setattr(cli, "apply_project_release_env", lambda *_a: None)
    gate = []

    def refuse(root, name, manifest, **_kw):
        gate.append((name, manifest["source_mode"]))
        raise UnsafeRecord("not on origin/main")

    monkeypatch.setattr(transaction, "assert_build_source_publishable", refuse)
    pushed = []
    monkeypatch.setattr(cli, "run_project_step", lambda *a, **k: pushed.append(a))
    assert cli.main(["publish", "alpha", "--build-output", OUTPUT_ID]) == 4
    assert gate == [("alpha", "head")] and pushed == []


def test_retention_writes_the_source_mode(tmp_path):
    from tests.test_runner_transaction_residuals import git, repo as make_repo

    root = make_repo(tmp_path)
    workspace = transaction.create_workspace(root, base=git(root, "rev-parse", "HEAD"), purpose="build")
    child = workspace.path / "demo"
    (child / "logs").mkdir(parents=True)
    (child / "dist").mkdir()
    (child / "dist" / "w.whl").write_bytes(b"x")
    project = SimpleNamespace(project_root=root / "demo", artifact_dirs=["dist"])
    out = transaction.retain_successful_build_outputs(
        root, workspace, {"demo": project}, ["demo"], source_mode="head",
    )
    manifest = json.loads((out[1] / "build.json").read_text(encoding="utf-8"))
    assert manifest["source_mode"] == "head"
    transaction.remove_workspace(workspace)


def test_run_project_step_injects_the_gh_credential_into_every_step_and_scrubs_ambient(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(
        cli, "execute_step", lambda step, *_a, **kw: captured.update({step.name: kw["protected_env"]}),
    )
    monkeypatch.setattr(cli, "_create_bound_cmru_launcher", lambda d: d / "cmru")
    monkeypatch.setattr(transaction, "is_transaction_child", lambda _r: False)
    steps = {n: _step(n) for n in ("build", "push", "prepare")}
    project = cli.ProjectConfig(
        name="a", template_revision=2, env={}, steps={}, prefix="a-v", scm_dist="a", cwd=".",
        version=None, paths=["."], project_root=tmp_path, runner_steps=steps,
        token_source="gh", github_token="SECRET-TOKEN",
    )
    for name in steps:
        cli.run_project_step(project, name, tmp_path, tmp_path / "logs")
    for name in steps:  # parity with the secret-file era: every step gets it
        assert captured[name]["GITHUB_PUSH_PAT"] == "SECRET-TOKEN"
        assert captured[name]["GH_TOKEN"] is None


def test_secret_file_is_refused_through_the_publish_verb(monkeypatch, tmp_path, capsys):
    project = _project("alpha", tmp_path)
    _patch_config(monkeypatch, tmp_path, {"alpha": project})
    gh = cli.GitHubConfig("o", "r", "", "user", token_source="gh")
    monkeypatch.setattr(
        cli, "load_config",
        lambda _p, **_k: (
            tmp_path, {"alpha": project}, ["alpha"], ["alpha"], ["build"], "project-first", {},
            cli.CleanupConfig([], [], [], []), gh, cli.ReleaseEnvConfig({}, None),
        ),
    )
    secret = tmp_path / "s.toml"
    secret.write_text("x", encoding="utf-8")
    rc = cli.main(["publish", "alpha", "--from-checkout", "--secret-file", str(secret)])
    captured = capsys.readouterr()
    assert rc == 2 and "--secret-file cannot be used" in captured.err + captured.out
