"""Regression oracles for CIU-117/104 R2 survivors."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import cli, deploy, engine, image_isolation, worktree, workspace, workspace_env  # noqa: E402


def _result(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.mark.parametrize("failure", ["rev-parse", "status"])
def test_git_revision_falls_back_only_when_checked_git_command_fails(
    monkeypatch, tmp_path, failure
):
    calls = []

    def run_cmd(argv, *, check, cwd):
        calls.append((argv, check, cwd))
        name = "rev-parse" if "rev-parse" in argv else "status"
        if name == failure:
            result = _result(1, stderr="git denied")
            if check:
                raise subprocess.CalledProcessError(
                    1, argv, stderr=result.stderr
                )
            return result
        return _result(stdout="12345678\n" if name == "rev-parse" else "")

    monkeypatch.setattr(engine.procutil, "run_cmd", run_cmd)

    assert engine._git_hash_at(tmp_path) == "dev"
    assert [check for _argv, check, _cwd in calls] == (
        [True] if failure == "rev-parse" else [True, True]
    )
    assert all(cwd == str(tmp_path.resolve()) for _argv, _check, cwd in calls)


def test_worktree_image_scope_flushes_its_progress_line(monkeypatch, tmp_path):
    monkeypatch.setattr(
        worktree, "resolve_worktree_image_tag_suffix", lambda _root: "worktree-id"
    )
    printed = []
    monkeypatch.setattr(
        "builtins.print", lambda *args, **kwargs: printed.append((args, kwargs))
    )
    source = "services:\n  app:\n    image: team/app:latest\n    build: .\n"

    assert "team/app:latest-worktree-id" in engine.scope_worktree_compose_images(
        tmp_path, source
    )
    assert len(printed) == 1
    assert printed[0][1]["flush"] is True


def test_linked_bake_preview_captures_text_and_checks_status_explicitly(
    monkeypatch, tmp_path
):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr("ciu.dev.resolve_repo_root", lambda *_a, **_kw: repo)
    monkeypatch.setattr(
        worktree, "resolve_worktree_image_tag_suffix", lambda *_a, **_kw: "worktree-id"
    )
    monkeypatch.setattr(deploy, "resolve_primary_image_references", lambda _root: set())
    monkeypatch.setattr(engine, "get_git_hash", lambda: "abcdef01")
    preview = json.dumps({"target": {"app": {"tags": ["team/app:latest"]}}})
    observed = {}

    def run(argv, **kwargs):
        observed["argv"] = argv
        observed["kwargs"] = kwargs
        return _result(stdout=preview)

    monkeypatch.setattr(cli.subprocess, "run", run)
    monkeypatch.setattr(cli.subprocess, "call", lambda _argv: 0)

    assert cli._bake(["app"]) == 0
    assert observed == {
        "argv": ["docker", "buildx", "bake", "app", "--print"],
        "kwargs": {"capture_output": True, "text": True, "check": False},
    }


@pytest.mark.parametrize("inventory", ["missing-current", "missing-primary"])
def test_rootless_bake_refuses_each_incomplete_git_inventory(
    monkeypatch, tmp_path, inventory
):
    current = tmp_path / "linked"
    primary = tmp_path / "primary"
    current.mkdir()
    primary.mkdir()
    if inventory == "missing-current":
        entries = [SimpleNamespace(path=primary, is_primary=True)]
    else:
        entries = [SimpleNamespace(path=current, is_primary=False)]
    monkeypatch.setattr(workspace, "resolve_worktree_git_root", lambda _root: current)
    monkeypatch.setattr(worktree, "list_worktrees", lambda _root: entries)

    with pytest.raises(ValueError, match="exactly one current and primary"):
        cli._bake_is_linked_checkout_without_ciu_root(current)


def test_resolve_identities_uses_source_environment_without_writing_config(
    monkeypatch, tmp_path
):
    profile = SimpleNamespace(config={}, name="test")
    render_calls = []
    environ = {"REPO_ROOT": str(tmp_path), "INSTANCE_ID": "source-id"}
    monkeypatch.setattr(
        deploy,
        "load_global_config",
        lambda *_a, **_kw: pytest.fail("source rendering must not use persisted config"),
    )
    monkeypatch.setattr(
        deploy.config_model,
        "render_global_chain",
        lambda *args, **kwargs: render_calls.append((args, kwargs)) or {},
    )
    monkeypatch.setattr(deploy.profiles_pkg, "reject_groups", lambda _config: None)
    monkeypatch.setattr(deploy, "resolve_profiles", lambda *_a, **_kw: profile)
    monkeypatch.setattr(deploy, "build_selection", lambda _profile: object())
    monkeypatch.setattr(deploy, "_resolve_identity_stack_paths", lambda *_a, **_kw: [])
    monkeypatch.setattr(deploy.profiles_pkg, "render_ciu_context", lambda *_a: {})
    monkeypatch.setattr(deploy, "profile_env", lambda *_a, **_kw: {})

    document = deploy.resolve_identities(
        tmp_path,
        render_environ=environ,
        image_map_only=True,
    )

    assert document == {"schema_version": 1, "resolved": {"identities": {}}}
    assert len(render_calls) == 1
    assert render_calls[0][1] == {
        "write_rendered": False,
        "environ": environ,
    }


def test_primary_image_map_does_not_repair_generated_identity_while_reading(
    monkeypatch, tmp_path
):
    facts_path = tmp_path / "facts.json"
    facts_path.write_text("{}\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(workspace_env, "generated_facts_path", lambda _root: facts_path)
    monkeypatch.setattr(workspace_env, "LEGACY_IDENTITY_ENV_KEYS", ())
    monkeypatch.setattr(workspace_env, "MACHINE_FACT_ENV_KEYS", {})
    monkeypatch.setattr(
        workspace_env,
        "read_generated_facts",
        lambda _root, *, allow_repair: calls.append(allow_repair) or {"instance_id": "main-id"},
    )
    monkeypatch.setattr(
        workspace_env, "identity_env_from_facts", lambda _facts: {"INSTANCE_ID": "main-id"}
    )
    monkeypatch.setattr(workspace_env, "read_generated_machine_facts", lambda _root: {})

    assert deploy._render_environment_for_checkout(tmp_path)["INSTANCE_ID"] == "main-id"
    assert calls == [False]


@pytest.mark.parametrize("inventory", ["missing-current", "missing-primary"])
def test_image_identity_refuses_each_incomplete_git_inventory(
    monkeypatch, tmp_path, inventory
):
    linked = tmp_path / "linked"
    linked_ciu = linked / "ciu"
    primary = tmp_path / "primary"
    linked_ciu.mkdir(parents=True)
    primary.mkdir()
    record_path = linked_ciu / worktree.WORKTREE_INSTANCE_RECORD
    record_path.write_text("{}\n", encoding="utf-8")
    if inventory == "missing-current":
        entries = [SimpleNamespace(path=primary, is_primary=True)]
    else:
        entries = [SimpleNamespace(path=linked, is_primary=False)]
    monkeypatch.setattr(workspace, "resolve_worktree_git_root", lambda _root: linked)
    monkeypatch.setattr(worktree, "list_worktrees", lambda _root: entries)
    monkeypatch.setattr(worktree, "read_instance_record", lambda _path: SimpleNamespace(
        state="ready", instance_id="linked-id"
    ))
    monkeypatch.setattr(worktree, "_primary_worktree_table", lambda _root: {})

    with pytest.raises(worktree.WorktreeError, match="exactly one current and primary"):
        worktree.resolve_worktree_image_tag_suffix(linked_ciu)


def test_image_identity_reads_generated_facts_without_repair(monkeypatch, tmp_path):
    linked = tmp_path / "linked"
    linked_ciu = linked / "ciu"
    primary = tmp_path / "primary"
    linked_ciu.mkdir(parents=True)
    primary.mkdir()
    (linked_ciu / worktree.WORKTREE_INSTANCE_RECORD).write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(workspace, "resolve_worktree_git_root", lambda _root: linked)
    monkeypatch.setattr(
        worktree,
        "list_worktrees",
        lambda _root: [
            SimpleNamespace(path=linked, is_primary=False),
            SimpleNamespace(path=primary, is_primary=True),
        ],
    )
    monkeypatch.setattr(
        worktree, "read_instance_record",
        lambda _path: SimpleNamespace(state="ready", instance_id="linked-id"),
    )
    monkeypatch.setattr(worktree, "_primary_worktree_table", lambda _root: {})
    reads = []
    monkeypatch.setattr(
        workspace_env,
        "read_generated_facts",
        lambda _root, *, allow_repair: reads.append(allow_repair)
        or {"instance_id": "linked-id"},
    )

    assert worktree.resolve_worktree_image_tag_suffix(linked_ciu) == "linked-id"
    assert reads == [False]


@pytest.mark.parametrize(
    ("reference", "message"),
    [
        ("${BUILT_IMAGE:-team/app}", "fully interpolated"),
        ("/${BUILT_IMAGE}", "empty image name before its path"),
        (":${TAG}", "empty image name"),
        (":latest", "empty image name"),
    ],
)
def test_image_tag_parser_refuses_interpolation_and_empty_name_boundaries(
    reference, message
):
    with pytest.raises(image_isolation.ImageIsolationError, match=message):
        image_isolation.append_instance_tag(reference, "worktree-id")


def test_compose_image_scoper_treats_quoted_false_as_a_build_context():
    source = "services:\n  app:\n    image: team/app:latest\n    build: 'false'\n"

    scoped = image_isolation.scope_compose_images(source, "worktree-id")

    assert "team/app:latest-worktree-id" in scoped


@pytest.mark.parametrize(
    ("build", "message"),
    [
        ("!!bool {unexpected: mapping}", "boolean must be a scalar"),
        ("true", "boolean may only disable a service with false"),
    ],
)
def test_compose_image_scoper_rejects_invalid_boolean_build_values(build, message):
    source = f"services:\n  app:\n    image: team/app:latest\n    build: {build}\n"

    with pytest.raises(image_isolation.ImageIsolationError, match=message):
        image_isolation.scope_compose_images(source, "worktree-id")


def test_compose_image_serializers_preserve_unicode_and_service_order():
    source = """services:
  z-app:
    image: team/café:latest
    build: .
  a-app:
    image: team/café:latest
"""
    scoped = image_isolation.scope_compose_images(source, "worktree-id")
    assert "team/café:latest-worktree-id" in scoped

    original = "services:\n  z-app:\n    image: team/café:old\n  a-app:\n    image: team/café:old\n"
    changed = "services:\n  z-app:\n    image: team/café:new\n  a-app:\n    image: team/café:new\n"
    override = image_isolation.compose_image_override(original, changed)

    assert override is not None
    assert "team/café:new" in override
    assert override.index("z-app:") < override.index("a-app:")


@pytest.mark.parametrize("stage", ["compose", "list", "inspect"])
def test_container_owner_queries_use_return_codes_for_closed_errors(
    monkeypatch, tmp_path, stage
):
    root = tmp_path / "checkout"
    root.mkdir()
    source = "services:\n  db:\n    container_name: app-db\n"
    resolved = json.dumps({"services": {"db": {"container_name": "app-db"}}})
    seen_checks = {}

    def run_cmd(argv, *, check, **_kwargs):
        if argv[:2] == ["docker", "compose"]:
            key = "compose"
            if stage == key:
                return fail(argv, key, check)
            seen_checks[key] = check
            return _result(stdout=resolved)
        if argv[:4] == ["docker", "ps", "-a", "--no-trunc"]:
            key = "list"
            if stage == key:
                return fail(argv, key, check)
            seen_checks[key] = check
            return _result(stdout="container-id app-db\n")
        key = "inspect"
        if stage == key:
            return fail(argv, key, check)
        seen_checks[key] = check
        return _result(stdout=json.dumps([{
            "Name": "/app-db",
            "Config": {"Labels": {
                "com.docker.compose.project.working_dir": str(root),
                "com.docker.compose.project": "demo",
                "com.docker.compose.service": "db",
            }},
        }]))

    def fail(argv, key, check):
        seen_checks[key] = check
        if check:
            raise subprocess.CalledProcessError(1, argv, stderr="daemon says denied")
        return _result(1, stderr="daemon says denied")

    monkeypatch.setattr(engine.procutil, "run_cmd", run_cmd)
    with pytest.raises(engine.ComposeError, match="daemon says denied"):
        engine.guard_container_name_owners(
            source,
            expected_project="demo",
            repo_root=root,
            file_args=["-f", "compose.yml"],
            cwd=root,
            env={},
        )

    assert seen_checks[stage] is False
