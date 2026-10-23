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


def test_origin_main_builds_and_old_records_keep_working(repo, capsys):
    transaction.assert_build_source_publishable(
        repo.root, "alpha", {"source_mode": "origin-main", "source_commit": repo.unpushed},
    )
    transaction.assert_build_source_publishable(
        repo.root, "alpha", {"source_commit": repo.unpushed},
    )
    assert "no source_mode" in capsys.readouterr().out


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


# ─── M2: a child adopts the parent's token_source for an older snapshot ─────────

def test_handoff_carries_token_source_and_old_payloads_still_decode():
    payload = credential_handoff.encode(
        credential_handoff.CredentialHandoff(root="r", token_source="gh"),
    )
    assert credential_handoff.decode(payload).token_source == "gh"
    legacy = json.dumps({"schema_version": 1, "root": "r", "projects": {}}).encode()
    assert credential_handoff.decode(legacy).token_source == ""


def test_child_loading_a_snapshot_without_the_key_takes_the_parents_source(monkeypatch):
    handoff = credential_handoff.CredentialHandoff(root="t", token_source="gh")
    monkeypatch.setattr(credential_handoff, "child_handoff", lambda: handoff)
    assert config._github({"owner": "o", "repo": "r", "owner_type": "user"}).token_source == "gh"
    monkeypatch.setattr(credential_handoff, "child_handoff", lambda: None)
    with pytest.raises(SystemExit):  # no handoff: the key stays mandatory
        config._github({"owner": "o", "repo": "r", "owner_type": "user"})


# ─── M3: the gh credential reaches publishing steps only ────────────────────────

def _step(name, **kw):
    base = dict(
        name=name, commands=[], bake_set_prefix=None, bake_set_vars=[], no_cache_env=None,
        clean_dirs=[], required_env=[], login=None, step_env={}, env_command=None,
    )
    return StepConfig(**{**base, **kw})


def test_only_publishing_steps_receive_the_gh_credential():
    assert cli.step_receives_publisher_credential(_step("push"))
    assert cli.step_receives_publisher_credential(_step("x", login={"registry": "ghcr.io"}))
    assert cli.step_receives_publisher_credential(_step("x", required_env=["GITHUB_PUSH_PAT"]))
    for name in ("build", "run-tests", "prepare", "clean"):
        assert not cli.step_receives_publisher_credential(_step(name))


def test_gh_login_is_not_placed_in_the_process_environment(monkeypatch):
    for key in ("GITHUB_PUSH_PAT", "GH_TOKEN", "GH_ENTERPRISE_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GH_TOKEN", "ambient")
    gh = cli.GitHubConfig("o", "r", "secret", "user", token_source="gh")
    cli.apply_release_env(gh, cli.ReleaseEnvConfig({}, None))
    assert "GITHUB_PUSH_PAT" not in os.environ
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
