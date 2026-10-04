"""CIU-124/125: shared up actions and declared worktree startup profiles."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import cli, deploy, engine, worktree  # noqa: E402


def test_up_help_actions_match_both_actual_parsers():
    text = cli._VERB_HELP["up"]
    shared = {"--deploy", "--healthcheck"}
    profile_only = {"--check"}

    action_help = text.split("Actions (S10.2)", 1)[1].split(
        "Profile/multi-stack mode:", 1
    )[0]
    profile_help = text.split("Profile/multi-stack mode:", 1)[1].split(
        "Layout mode", 1
    )[0]
    single_help = text.split("Single-stack mode", 1)[1].split(
        "Remote (SPEC", 1
    )[0]

    profile_parser = deploy.build_argument_parser()
    single_parser = engine.build_argument_parser()
    profile_options = set(profile_parser._option_string_actions)
    single_options = set(single_parser._option_string_actions)

    assert all(flag in action_help for flag in shared)
    assert all(flag in single_help for flag in shared)
    assert all(flag in profile_help for flag in profile_only)
    assert shared <= profile_options
    assert shared <= single_options
    assert profile_only <= profile_options
    assert not (profile_only & single_options)


def test_single_stack_parser_accepts_deploy_and_healthcheck():
    args = engine.parse_arguments(["--deploy", "--healthcheck"])
    assert args.deploy is True
    assert args.healthcheck is True


def test_single_stack_dry_run_does_not_run_health_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(
        engine,
        "main_execution",
        lambda **_kwargs: {"status": "success", "dry_run": True, "config": {}},
    )
    monkeypatch.setattr(
        engine,
        "_run_single_stack_healthcheck",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("dry-run must not gate a stack that was not started")
        ),
    )

    assert engine.main([
        "--dir", str(tmp_path), "--deploy", "--healthcheck", "--dry-run", "-y",
    ]) == 0


def test_single_stack_healthcheck_is_scoped_to_one_stack_and_fails_closed(
    monkeypatch, tmp_path,
):
    stack = tmp_path / "tools" / "test-runner"
    stack.mkdir(parents=True)
    monkeypatch.setattr(engine, "resolve_env_root", lambda *_args: tmp_path)
    seen = {}

    def healthcheck(_root, _profile, selection):
        seen["selection"] = selection
        return 1

    monkeypatch.setattr(deploy, "action_healthcheck", healthcheck)
    monkeypatch.setattr(
        engine,
        "main_execution",
        lambda **_kwargs: {"status": "success", "config": {"deploy": {}}},
    )

    assert engine.main([
        "--dir", str(stack), "--deploy", "--healthcheck",
    ]) == 1
    assert [entry["path"] for entry in seen["selection"]] == [str(stack)]


def test_worktree_up_profile_declaration_is_validated_before_launch():
    try:
        worktree.resolve_worktree_up_profiles({
            "ciu": {"worktree": {"up": ["missing"]}},
            "deploy": {"profiles": {}},
        })
    except worktree.WorktreeError as exc:
        assert "Unknown profile 'missing'" in str(exc)
    else:
        raise AssertionError("unknown declared startup profile was accepted")


@pytest.mark.parametrize(
    "config",
    [
        {"ciu": None},
        {"ciu": {"worktree": {"up": []}}},
        {"ciu": {"worktree": {"up": ["core", " core "]}}},
    ],
)
def test_worktree_up_rejects_malformed_declaration(config):
    with pytest.raises(worktree.WorktreeError):
        worktree.resolve_worktree_up_profiles(config)


def test_worktree_up_returns_none_without_policy_or_up_key():
    assert worktree.resolve_worktree_up_profiles({}) is None
    assert worktree.resolve_worktree_up_profiles({"ciu": {"worktree": {}}}) is None


def test_profiles_listing_renders_without_persisting(monkeypatch, tmp_path):
    observed = {}
    monkeypatch.setattr(deploy, "bootstrap_workspace_env", lambda **_kwargs: None)
    monkeypatch.setattr(deploy, "enforce_standalone_root", lambda *_args: None)
    monkeypatch.setattr(deploy, "resolve_repo_root", lambda *_args: tmp_path)
    monkeypatch.setattr(deploy, "action_list_profiles", lambda _config: 0)

    def render_global_chain(*_args, **kwargs):
        observed["write_rendered"] = kwargs.get("write_rendered")
        return {"deploy": {"profiles": {}}}

    monkeypatch.setattr(deploy.config_model, "render_global_chain", render_global_chain)

    assert deploy.main(["--list-profiles"]) == 0
    assert observed["write_rendered"] is False


def test_create_up_failure_preserves_checkout_and_names_retry(monkeypatch, tmp_path, capsys):
    checkout = tmp_path / "created"
    checkout.mkdir()
    record = SimpleNamespace(git_worktree_path=checkout, ciu_root=checkout)
    monkeypatch.setattr(cli, "_resolve_worktree_git_root_cli", lambda *_args: tmp_path)
    monkeypatch.setattr(worktree, "create", lambda *_args, **_kwargs: record)
    monkeypatch.setattr(worktree, "up_instance", lambda *_args, **_kwargs: 19)

    assert cli._worktree(["create", "logical", "--up"]) == 19
    assert checkout.is_dir()
    assert "ciu worktree up logical" in capsys.readouterr().err


def test_create_up_success_returns_ready_after_start(monkeypatch, tmp_path):
    record = SimpleNamespace(git_worktree_path=tmp_path / "created", ciu_root=tmp_path / "created")
    started = []
    monkeypatch.setattr(cli, "_resolve_worktree_git_root_cli", lambda *_args: tmp_path)
    monkeypatch.setattr(worktree, "create", lambda *_args, **_kwargs: record)
    monkeypatch.setattr(worktree, "up_instance", lambda *_args, **_kwargs: started.append(True) or 0)

    assert cli._worktree(["create", "logical", "--up"]) == 0
    assert started == [True]


def test_worktree_up_all_forwards_explicit_default_selection(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(cli, "_resolve_worktree_git_root_cli", lambda *_args: tmp_path)
    monkeypatch.setattr(
        worktree, "up_instance",
        lambda root, name, **kwargs: calls.append((root, name, kwargs)) or 4,
    )

    assert cli._worktree(["up", "logical", "--all"]) == 4
    assert calls == [(tmp_path, "logical", {"all_profiles": True})]
