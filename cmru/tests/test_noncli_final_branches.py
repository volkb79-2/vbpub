"""Behavioral witnesses for the last non-CLI branch alternatives."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from cmru import handlers, tester_gate, transaction


def test_handlers_omit_optional_urls_when_the_release_has_none(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_USERNAME", "alice")
    monkeypatch.setenv("GITHUB_REPO", "repo")
    monkeypatch.setenv("GITHUB_PUSH_PAT", "token")
    monkeypatch.setattr(handlers, "GitHubReleases", lambda *args: object())
    monkeypatch.setattr(
        handlers, "validate_latest_release",
        lambda *args, **kwargs: {
            "version": "1.0", "asset": "demo.whl", "url": "https://example/demo.whl",
        },
    )
    handlers.cmd_wheel_validate(SimpleNamespace(prefix="demo"))
    validation = capsys.readouterr().out
    assert "DEMO_WHEEL_LATEST_URL=https://example/demo.whl" in validation
    assert "DEMO_WHEEL_SHA256_URL" not in validation
    assert "Verify:" not in validation

    wheel = tmp_path / "dist" / "demo.whl"
    calls = []
    monkeypatch.setattr(handlers, "find_built_wheel", lambda *_: wheel)
    monkeypatch.setattr(handlers, "read_wheel_version", lambda _: "1.0")
    monkeypatch.setattr(
        handlers, "publish_versioned",
        lambda gh, **kwargs: calls.append((gh, kwargs)) or {"sha256": "abc"},
    )
    handlers.cmd_wheel_publish(SimpleNamespace(
        prefix="demo", cwd=str(tmp_path), glob="*.whl", notes_env=None, extra_asset=[],
    ))
    published = capsys.readouterr().out
    assert "[INFO] Published demo 1.0" in published
    assert "[INFO] DEMO_WHEEL_SHA256=abc" in published
    assert "DEMO_WHEEL_ASSET_URL" not in published
    assert calls[0][1]["asset_path"] == wheel


def test_tester_gate_cli_keeps_command_without_separator(monkeypatch, tmp_path):
    monkeypatch.setattr(tester_gate, "check_slice_unit", lambda *_: (True, "ok"))
    commands = []
    monkeypatch.setattr(
        tester_gate, "build_docker_command",
        lambda *args, **kwargs: commands.append(args[2]) or ["true"],
    )
    monkeypatch.setattr(tester_gate, "_resolve_worktree_context", lambda *_: (tmp_path, "."))
    monkeypatch.setattr(
        tester_gate.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )
    parsed = SimpleNamespace(
        cwd=".", image="img", cgroup_parent="slice", cgroup_probe_image="probe",
        memory="1G", memory_swap="2G", cpus="1", device_read_iops="",
        device_write_iops="", device_read_bps="", device_write_bps="",
        enable_docker=False, dind_image=None, command=["true"],
    )
    with patch.object(tester_gate.argparse.ArgumentParser, "parse_args", return_value=parsed):
        with pytest.raises(SystemExit) as raised:
            tester_gate.main([])
    assert raised.value.code == 0
    assert commands == [["true"]]


def test_retain_outputs_rolls_back_when_destination_setup_fails(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    child = tmp_path / "child"
    (child / "demo" / "dist").mkdir(parents=True)
    (child / "demo" / "dist" / "demo.whl").write_text("wheel", encoding="utf-8")
    workspace = transaction.ReleaseWorkspace(root, child, "cmru/release/x", "a" * 40)
    project = SimpleNamespace(project_root=root / "demo", artifact_dirs=["dist"])

    real_mkdir = Path.mkdir
    def fail_target_mkdir(path, *args, **kwargs):
        if path == root / "demo" / "artifacts" / "tag":
            raise OSError("destination setup failed")
        return real_mkdir(path, *args, **kwargs)

    with patch.object(Path, "mkdir", new=fail_target_mkdir):
        with pytest.raises(OSError, match="destination setup failed"):
            transaction.retain_success_outputs(
                root, workspace, {"demo": project}, {"demo": "tag"},
                retain_logs=False, retain_artifacts=True,
            )
    assert (child / "demo" / "dist" / "demo.whl").is_file()
    assert not (root / "demo" / "artifacts").exists()

    # An existing parent is also preserved when setup fails before the target
    # directory can be created; no rollback should remove an operator-owned
    # artifacts container.
    (root / "demo" / "artifacts").mkdir(parents=True)
    with patch.object(Path, "mkdir", new=fail_target_mkdir):
        with pytest.raises(OSError, match="destination setup failed"):
            transaction.retain_success_outputs(
                root, workspace, {"demo": project}, {"demo": "tag"},
                retain_logs=False, retain_artifacts=True,
            )
    assert (root / "demo" / "artifacts").is_dir()
    assert not (root / "demo" / "artifacts" / "tag").exists()


def test_promote_retries_a_non_fast_forward_then_succeeds(tmp_path):
    workspace = SimpleNamespace(repo_root=tmp_path, path=tmp_path)
    responses = iter([
        SimpleNamespace(returncode=1, stderr="[rejected] non-fast-forward", stdout=""),
        SimpleNamespace(returncode=0, stderr="", stdout=""),
        SimpleNamespace(returncode=0, stderr="", stdout=""),
        SimpleNamespace(returncode=0, stderr="", stdout=""),
    ])
    commands = []
    with patch.object(transaction, "read_release_progress", return_value=None), \
         patch.object(transaction.subprocess, "run", side_effect=lambda argv, **kwargs: commands.append(argv) or next(responses)):
        transaction.promote_workspace(workspace)
    assert commands == [
        ["git", "push", "origin", "HEAD:refs/heads/main"],
        ["git", "fetch", "--prune", "origin", "main"],
        ["git", "rebase", "origin/main"],
        ["git", "push", "origin", "HEAD:refs/heads/main"],
    ]


def test_sync_local_main_creates_missing_local_main_from_origin(tmp_path):
    commands = []
    def fake_git(_root, *args, **kwargs):
        if args[:2] == ("branch", "--show-current"):
            return "feature"
        if args[:2] == ("rev-parse", "main"):
            return ""
        raise AssertionError(args)

    with patch.object(transaction, "_git", side_effect=fake_git), \
         patch.object(transaction.subprocess, "run", side_effect=lambda argv, **kwargs: commands.append(argv) or SimpleNamespace(returncode=0)):
        assert transaction.sync_local_main(tmp_path) is True
    assert commands == [
        ["git", "fetch", "--prune", "origin", "main"],
        ["git", "branch", "-f", "main", "origin/main"],
    ]
