"""KI-14: ``--dry-run`` must not be the only way to learn something about a
real run. Both paths compute the release plan through the exact same call
(``detect_changed_projects`` with ``check_tag_at_head=True``,
``require_pushed_baseline=True``) BEFORE branching on ``vargs.dry_run`` --
this module is the durable guard that a future change cannot silently
reintroduce a dry-run-only (or real-only) decision-level line without
breaking a test, the way KI-14 originally happened (KI-13 fixed on the
unchanged path was, by construction, unconditional -- but nothing previously
proved that in a way that would fail if it stopped being true).

Uses ``cli.main([..., "--_transaction-child", ...])`` directly against a
real git repo (with a real ``origin``) -- the CLI's release-plan section
(``detect_changed_projects`` -> ``changed_names``/``release_names`` ->
the dry-run/real branch) runs unmocked, exactly as it does in production;
only ``_resolve_config``/``load_config``/``apply_release_env`` are stubbed,
matching the existing KI-12 CLI-wiring harness
(``tests/test_ki12_cli_wiring.py``).
"""
from __future__ import annotations

import re
import subprocess
import tempfile
import shutil
from pathlib import Path

import pytest

from cmru import cli, output, version


@pytest.fixture(autouse=True)
def _no_ambient_time_short_prefix(monkeypatch):
    """``consume_cli_flags`` intentionally leaks ``CMRU_LOG_PREFIX_TIME_SHORT``
    into ``os.environ`` once any earlier call passes ``--log-prefix-time-short``
    -- deliberate, so a release child process inherits the caller's explicit
    presentation choice (see ``output.consume_cli_flags``'s docstring), but it
    means this module's exact-text assertions must not depend on suite
    ordering. Other test modules already guard the same way (e.g.
    ``tests/test_core_residuals.py``)."""
    monkeypatch.delenv(output._TIME_ENV, raising=False)


# ---------------------------------------------------------------------------
# Real-repo harness
# ---------------------------------------------------------------------------

def _git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if check and result.returncode:
        raise AssertionError(f"git {args} failed:\n{result.stderr}")
    return result.stdout.strip()


class _Repo:
    def __enter__(self) -> "_Repo":
        self.tmp = Path(tempfile.mkdtemp(prefix="cmru_ki14_test_"))
        self.root = self.tmp / "repo"
        self.root.mkdir()
        _git(self.root, "init", "-q", "-b", "main")
        _git(self.root, "config", "user.email", "test@example.invalid")
        _git(self.root, "config", "user.name", "test")
        (self.root / "README.md").write_text("init\n")
        for name in ("alpha", "beta"):
            (self.root / name).mkdir()
            (self.root / name / "x.py").write_text("x = 1\n")
        _git(self.root, "add", ".")
        _git(self.root, "commit", "-q", "-m", "chore: initial")
        self.origin = self.tmp / "origin.git"
        _git(self.tmp, "clone", "-q", "--bare", str(self.root), str(self.origin))
        _git(self.root, "remote", "add", "origin", str(self.origin))
        _git(self.root, "push", "-q", "origin", "main")
        return self

    def commit(self, path: str, msg: str) -> None:
        target = self.root / path
        existing = target.read_text() if target.exists() else ""
        target.write_text(existing + f"# {msg}\n")
        _git(self.root, "add", path)
        _git(self.root, "commit", "-q", "-m", msg)

    def tag(self, name: str, *, ref: str = "HEAD") -> None:
        _git(self.root, "tag", "-a", name, "-m", f"release {name}", ref)
        _git(self.root, "push", "-q", "origin", name)

    def __exit__(self, *_exc) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)


def _config(repo_root: Path):
    alpha = cli.ProjectConfig("alpha", {}, {}, prefix="alpha-v", github_token="token")
    beta = cli.ProjectConfig("beta", {}, {}, prefix="beta-v", github_token="token")
    return (
        repo_root, {"alpha": alpha, "beta": beta}, ["alpha", "beta"], ["alpha", "beta"], [],
        "project-first", {}, cli.CleanupConfig([], [], [], []),
        cli.GitHubConfig("owner", "repo", "token", "user"), cli.ReleaseEnvConfig({}, None),
    )


def _run(monkeypatch, repo_root: Path, extra_args: list[str]) -> str:
    monkeypatch.setattr(cli, "_resolve_config", lambda _: repo_root / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: _config(repo_root))
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)
    cli.main(
        ["release", "--_transaction-child", "--config", str(repo_root / "cmru.toml")] + extra_args
    )


_SKIP_LINE = re.compile(r"^\[INFO\] Unchanged, skipping: ")
_PLAN_LINE = re.compile(r"^\[INFO\] Release plan: ")


def _decision_lines(output: str) -> list[str]:
    """Just the lines KI-14 requires to be identical on both paths: the plan
    summary and every per-project unchanged reason -- not the effect-only
    bookend lines ("[DRY RUN] No tags pushed..." / "Nothing to release...")
    that legitimately differ because one path has no effects at all."""
    return [
        line for line in output.splitlines()
        if _SKIP_LINE.match(line) or _PLAN_LINE.match(line)
    ]


@pytest.fixture()
def _both_unchanged_repo():
    with _Repo() as repo:
        repo.tag("alpha-v1.0.0")  # "equal": tagged exactly at HEAD
        repo.commit("beta/x.py", "feat: beta gets its own tag first")
        repo.tag("beta-v1.0.0")
        repo.commit("README.md", "chore: unrelated repo-wide change")  # moves HEAD past beta's tag ("behind")
        yield repo


def test_dry_run_and_real_run_print_byte_identical_decision_lines(monkeypatch, capsys, _both_unchanged_repo):
    """THE regression this module exists to catch: every plan/baseline/reason
    line a dry run shows must also appear, verbatim, on a real run -- nothing
    decision-level may be dry-run-exclusive (or real-exclusive)."""
    repo = _both_unchanged_repo

    _run(monkeypatch, repo.root, ["--dry-run"])
    dry_lines = _decision_lines(capsys.readouterr().out)

    _run(monkeypatch, repo.root, [])
    real_lines = _decision_lines(capsys.readouterr().out)

    assert dry_lines  # the probe must actually exercise real content, not compare two empties
    assert dry_lines == real_lines


def test_each_unchanged_project_gets_its_own_line_not_a_bare_joined_list(monkeypatch, capsys, _both_unchanged_repo):
    """KI-13's shape, re-verified through the full CLI wiring (not just
    ``detect_changed_projects`` directly): the old ``Unchanged, skipping:
    alpha, beta`` bare list must not reappear on EITHER path."""
    repo = _both_unchanged_repo

    _run(monkeypatch, repo.root, ["--dry-run"])
    dry_out = capsys.readouterr().out
    _run(monkeypatch, repo.root, [])
    real_out = capsys.readouterr().out

    for out in (dry_out, real_out):
        skip_lines = [line for line in out.splitlines() if _SKIP_LINE.match(line)]
        assert len(skip_lines) == 2  # one line per project
        assert not any("alpha" in line and "beta" in line for line in skip_lines)


def test_detect_changed_projects_is_called_once_with_identical_strict_kwargs_on_both_paths(monkeypatch, tmp_path):
    """Structural guard, independent of message content: the release plan
    MUST be computed by exactly one call, before the dry-run/real branch --
    never a second, differently-flagged call inside either branch (which is
    the shape a future regression would most likely take)."""
    calls = []

    def fake_detect(repo_root, projects, **kwargs):
        calls.append(dict(kwargs))
        return []

    monkeypatch.setattr(version, "detect_changed_projects", fake_detect)
    monkeypatch.setattr(version, "release_cmd", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_resolve_config", lambda _: tmp_path / "cmru.toml")
    monkeypatch.setattr(cli, "load_config", lambda _: _config(tmp_path))
    monkeypatch.setattr(cli, "apply_release_env", lambda *_: None)

    cli.main(["release", "--_transaction-child", "--dry-run", "--config", str(tmp_path / "cmru.toml")])
    dry_calls = list(calls)
    calls.clear()

    cli.main(["release", "--_transaction-child", "--config", str(tmp_path / "cmru.toml")])
    real_calls = list(calls)

    assert len(dry_calls) == 1
    assert len(real_calls) == 1
    assert dry_calls == real_calls
    assert dry_calls[0]["require_pushed_baseline"] is True
    assert dry_calls[0]["check_tag_at_head"] is True
