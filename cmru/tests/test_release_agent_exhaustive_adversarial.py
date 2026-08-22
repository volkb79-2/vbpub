"""Exhaustive boundary witnesses for release, transaction, and agent contracts."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from cmru import release, resolve, tester_gate, transaction, version


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, text=True, capture_output=True)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "test")
    (root / "demo").mkdir()
    (root / "demo" / "x.py").write_text("x = 1\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "feat: initial")
    return root


class _Response:
    def __init__(self, payload: str, status: int = 200):
        self.payload, self.status = payload.encode(), status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.payload


def test_release_client_handles_not_found_pagination_and_optional_target():
    gh = release.GitHubReleases("o", "r", "")
    calls = []

    def request(method, url, data=None, content_type=None):
        calls.append((method, url, data, content_type))
        if "/tags/missing" in url:
            return 404, "{}"
        if "&page=1" in url:
            return 200, '[{"tag_name":"a"}]'
        if method == "POST":
            return 201, '{"id": 3, "upload_url": "https://upload/{?name}"}'
        return 200, "[]"

    gh._request = request
    assert gh.get_release_by_tag("missing") is None
    assert gh.list_releases(per_page=1) == [{"tag_name": "a"}]
    created = gh.create_release("demo-v1", "Demo", "notes", target_commitish="abc")
    assert calls[0][0] == "GET"
    assert created  # the request fixture returns a JSON object for POST below


def test_release_client_failures_are_reported_at_each_http_boundary(tmp_path, capsys):
    gh = release.GitHubReleases("o", "r", "token")
    gh._request = lambda *args, **kwargs: (503, "unavailable")
    asset = tmp_path / "asset"
    asset.write_bytes(b"asset")
    for operation in (
        lambda: gh.get_release_by_tag("v1"),
        lambda: gh.list_releases(),
        lambda: gh.create_release("v1", "t", "n"),
        lambda: gh.update_release(1, "t", "n"),
        lambda: gh.delete_release(1),
        lambda: gh.list_assets(1),
        lambda: gh.delete_asset(1),
        lambda: gh.upload_asset("https://upload/{?name}", asset, "x"),
    ):
        with pytest.raises(SystemExit) as error:
            operation()
        assert error.value.code == 1
    assert "HTTP 503" in capsys.readouterr().err


def test_publish_existing_release_updates_and_uploads_only_new_assets(tmp_path):
    asset = tmp_path / "artifact.whl"
    asset.write_bytes(b"wheel")
    gh = release.GitHubReleases("o", "r", "t")
    calls = []
    gh.get_release_by_tag = lambda tag: {"id": 4, "upload_url": "https://upload/{?name}"}
    gh.update_release = lambda *args: calls.append(("update", args)) or {"id": 4, "upload_url": "https://upload/{?name}"}
    gh.list_assets = lambda rid: []
    gh.upload_asset = lambda *args: calls.append(("upload", args[2]))
    result = gh.publish("demo-v1", "title", "notes", [asset])
    assert result["id"] == 4
    assert calls[0][0] == "update"
    assert {name for kind, name in calls[1:]} == {asset.name}


def test_validate_latest_release_retries_then_returns_integrity_coordinates(monkeypatch):
    gh = Mock()
    gh.resolve_latest.side_effect = [None, {
        "version": "1.2.3", "tag": "demo-v1.2.3", "assets": [
            {"name": "demo.whl", "url": "wheel"},
            {"name": "demo.whl.sha256", "url": "hash"},
        ],
    }]
    monkeypatch.setattr("time.sleep", lambda _: None)
    assert release.validate_latest_release(gh, "demo-v", retries=2, delay=0) == {
        "version": "1.2.3", "tag": "demo-v1.2.3", "asset": "demo.whl",
        "url": "wheel", "sha256_url": "hash",
    }


@pytest.mark.parametrize("messages, expected", [
    (["fix: patch", "feat: feature"], "minor"),
    (["feat!: breaking"], "major"),
    (["docs: text"], "patch"),
])
def test_conventional_commit_bump_precedence(messages, expected):
    assert version._bump_from_commits(messages) == expected


def test_version_strategies_dry_run_are_nonmutating_and_counter_is_numeric(tmp_path, capsys):
    root = _repo(tmp_path)
    _git(root, "tag", "demo-v1.0.0-r2")
    assert version._apply_strategy_scm(root, "demo-v", "2.0.0", dry_run=True) == "demo-v2.0.0"
    assert version._apply_strategy_counter(root, "demo-v", "1.0.0", dry_run=True) == "demo-v1.0.0-r3"
    assert _git(root, "tag", "--list") == "demo-v1.0.0-r2"
    assert "Would tag" in capsys.readouterr().out


def test_file_strategy_commits_changed_version_and_is_idempotent(tmp_path):
    root = _repo(tmp_path)
    version_file = root / "demo" / "VERSION"
    version_file.write_text("1.0.0\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "chore: version")
    tag = version._apply_strategy_file(root, "demo-v", "1.1.0", "VERSION", root / "demo")
    assert tag == "demo-v1.1.0"
    assert version_file.read_text() == "1.1.0\n"
    before = _git(root, "rev-parse", "HEAD")
    same = version._apply_strategy_file(root, "demo-v", "1.1.0", "VERSION", root / "demo", dry_run=True)
    assert same == tag and _git(root, "rev-parse", "HEAD") == before


def test_detect_changed_projects_ignores_release_control_only_changes(tmp_path):
    root = _repo(tmp_path)
    _git(root, "tag", "demo-v1.0.0")
    (root / "demo" / "cmru.toml").write_text("[project]\n")
    _git(root, "add", ".")
    _git(root, "commit", "-qm", "chore: adjust config")
    project = SimpleNamespace(prefix="demo-v", paths=["demo"], cwd="demo", version=SimpleNamespace(bump="conventional"))
    assert version.detect_changed_projects(root, {"demo": project}) == []


def test_transaction_secret_overlay_is_private_and_rejects_outside_config(tmp_path):
    root = _repo(tmp_path)
    (root / "cmru.secret.toml").write_text("token = 'secret'\n")
    child = tmp_path / "child"
    _git(root, "worktree", "add", "-q", "-b", "cmru/release/secret", str(child), "main")
    ws = transaction.ReleaseWorkspace(root, child, "cmru/release/secret", _git(root, "rev-parse", "HEAD"))
    config = root / "demo" / "cmru.toml"
    config.write_text("x")
    transaction.copy_secret_overlays(root, ws, [config])
    copied = child / "cmru.secret.toml"
    assert copied.read_text() == "token = 'secret'\n" and copied.stat().st_mode & 0o777 == 0o600
    with pytest.raises(RuntimeError, match="outside repository"):
        transaction.copy_secret_overlays(root, ws, [tmp_path / "outside.toml"])
    _git(root, "worktree", "remove", "--force", str(child)); _git(root, "branch", "-D", "cmru/release/secret")


def test_transaction_workspace_records_reject_corrupt_results_and_forget_markers(tmp_path):
    root = _repo(tmp_path)
    ws = transaction.ReleaseWorkspace(root, tmp_path / "ws", "cmru/release/token", "a" * 40)
    transaction.write_release_scope(root, ws, ["demo"])
    transaction.write_release_progress(root, ws, "b" * 40)
    transaction.write_release_result(root, ws, "demo", "demo-v1")
    assert transaction.read_release_results(root, ws) == {"demo": "demo-v1"}
    marker = root / ".git" / "cmru-release-scopes" / "token.results.json"
    marker.write_text("[]")
    with pytest.raises(RuntimeError, match="invalid release result"):
        transaction.read_release_results(root, ws)
    transaction.forget_release_scope(root, ws)
    assert transaction.read_release_scope(root, ws) is None
    assert transaction.read_release_progress(root, ws) is None


def test_transaction_delete_retained_output_requires_exact_verified_coordinate(tmp_path):
    root = _repo(tmp_path)
    project_root = root / "demo"
    project = SimpleNamespace(project_root=project_root)
    for output_id in ("bad", "20240101T000000Z_" + "a" * 39):
        with pytest.raises(RuntimeError, match="exact"):
            transaction.delete_retained_build_output(root, project, "demo", output_id, dry_run=False)
    output_id = "20240101T000000Z_" + "a" * 40
    target = project_root / "artifacts" / output_id
    target.mkdir(parents=True)
    (project_root / "logs" / output_id).mkdir(parents=True)
    (target / "build.json").write_text(json.dumps({
        "schema_version": 1, "kind": "cmru-local-build", "publication": "forbidden",
        "project": "demo", "build_id": output_id,
    }))
    assert transaction.delete_retained_build_output(root, project, "demo", output_id, dry_run=True) == [
        project_root / "logs" / output_id, target,
    ]
    assert target.exists()


def test_resolve_fast_path_falls_back_on_malformed_pointer(monkeypatch):
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: _Response("not-json"))
    assert resolve.resolve_via_latest_json("https://github/x/releases", "demo-v") is None
    host = SimpleNamespace(resolve_latest=lambda prefix: {"version": "1", "tag": "demo-v1", "url": "u"})
    assert resolve.resolve(host, "demo-v", use_latest_json=True, gh_releases_url="https://github/x/releases")["tag"] == "demo-v1"


def test_resolve_format_env_and_url_are_stable():
    result = {"version": "1.2.3", "tag": "demo-v1.2.3", "url": "https://x", "sha256": "abc"}
    rendered = resolve.format_result(result, "env")
    assert "DEMO_VERSION=1.2.3" in rendered and "DEMO_SHA256=abc" in rendered
    assert resolve.format_result(result, "url") == "https://x"


def test_selfupdate_marker_and_handoff_are_atomic_at_systemd_boundary(tmp_path, monkeypatch):
    from cmru.agent import selfupdate
    venv = tmp_path / "venv-2"; venv.mkdir()
    selfupdate.write_pending_marker(tmp_path, "2", venv)
    assert selfupdate.read_pending_marker(tmp_path) == {"version": "2", "venv": str(venv)}
    calls = []
    monkeypatch.setattr(selfupdate.subprocess, "run", lambda argv, **kwargs: calls.append(argv) or SimpleNamespace(returncode=0, stderr=""))
    selfupdate.handoff_via_systemd("2", venv, scope="user")
    assert (tmp_path / "venv-current").is_symlink()
    assert calls == [["systemctl", "--user", "restart", "cmru-agent"]]
    selfupdate.clear_pending_marker(tmp_path)
    assert selfupdate.read_pending_marker(tmp_path) is None


def test_controller_cli_reports_missing_plan_without_constructing_backend(tmp_path, capsys):
    from cmru.controller import cli
    args = SimpleNamespace(plan=str(tmp_path / "missing.toml"), landscape=None)
    assert cli.cmd_publish(args) == 2
    assert "Plan file not found" in capsys.readouterr().err


def test_tester_gate_resolvers_fail_closed_and_prefer_explicit(monkeypatch):
    for name in ("CMRU_TESTER_CGROUP_PARENT", "CGROUP_PARENT_DEV_BACKGROUND", "CMRU_TESTER_MEMORY", "CMRU_TESTER_MEMORY_SWAP", "CMRU_TESTER_CPUS", "CMRU_TESTER_CGROUP_PROBE_IMAGE", "CMRU_TESTER_DIND_IMAGE"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit, match="cgroup_parent"):
        tester_gate.resolve_cgroup_parent(None)
    assert tester_gate.resolve_cgroup_parent("slice-explicit") == "slice-explicit"


def test_tester_gate_cgroup_fallback_tier(monkeypatch, capsys):
    """CMRU_TESTER_CGROUP_PARENT_FALLBACK is the LAST tier: used only when no
    per-project override and no ambient devcontainer var exist, and whatever
    resolves is still verified against the host systemd by check_slice_unit
    (operator-declared default, never a code-level hardcoded one)."""
    for name in (
        "CMRU_TESTER_CGROUP_PARENT",
        "CGROUP_PARENT_DEV_BACKGROUND",
        "CMRU_TESTER_CGROUP_PARENT_FALLBACK",
    ):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit, match="cgroup_parent"):
        tester_gate.resolve_cgroup_parent(None)

    monkeypatch.setenv("CGROUP_PARENT_DEV_BACKGROUND", "ambient.slice")
    monkeypatch.setenv("CMRU_TESTER_CGROUP_PARENT_FALLBACK", "fallback.slice")
    # ambient outranks the declared fallback when present
    assert tester_gate.resolve_cgroup_parent(None) == "ambient.slice"

    monkeypatch.delenv("CGROUP_PARENT_DEV_BACKGROUND", raising=False)
    assert tester_gate.resolve_cgroup_parent(None) == "fallback.slice"
    err = capsys.readouterr().err
    assert "declared fallback" in err and "verified against the host systemd" in err

    monkeypatch.setenv("CMRU_TESTER_CGROUP_PARENT", "project-override.slice")
    assert tester_gate.resolve_cgroup_parent(None) == "project-override.slice"

    with pytest.raises(SystemExit, match="memory limit"):
        tester_gate.resolve_memory(None)
    monkeypatch.setenv("CMRU_TESTER_MEMORY", "1G")
    assert tester_gate.resolve_memory(None) == "1G"
    with pytest.raises(SystemExit, match="CPU limit"):
        tester_gate.resolve_cpus(None)
    monkeypatch.setenv("CMRU_TESTER_CPUS", "2")
    assert tester_gate.resolve_cpus(None) == "2"
