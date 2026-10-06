"""CIU-117/104: worktree image tags and container-name ownership.

Oracles:
- project-built image references get the linked worktree id in Compose and
  every shared use of that reference; pulled images stay unchanged;
- bake output tags are rewritten from Buildx's resolved plan, preserving
  multiple tags and leaving untagged targets alone;
- an exact explicit container name is reusable only when Docker proves the
  same checkout, Compose project, and service; other/unknown owners and a
  failed Docker query refuse before Compose can recreate anything.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import cli, deploy, dev, engine, image_isolation, worktree, workspace  # noqa: E402


def test_compose_scopes_built_reference_and_shared_consumers_only():
    source = """# retained comment
services:
  app:
    image: registry:5000/team/app:latest
    build: .
  worker:
    image: registry:5000/team/app:latest
  db:
    image: postgres:16
"""

    rendered = image_isolation.scope_compose_images(source, "a3g64e")
    document = yaml.safe_load(rendered)

    assert document["services"]["app"]["image"] == "registry:5000/team/app:latest-a3g64e"
    assert document["services"]["worker"]["image"] == "registry:5000/team/app:latest-a3g64e"
    assert document["services"]["db"]["image"] == "postgres:16"
    assert "# retained comment" in rendered


def test_compose_without_built_or_imported_images_does_not_require_identity(monkeypatch, tmp_path):
    monkeypatch.setattr(
        worktree,
        "resolve_worktree_image_tag_suffix",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a pulled-only stack does not need a worktree image id")
        ),
    )
    source = "services:\n  db:\n    image: postgres:16\n"
    assert engine.scope_worktree_compose_images(tmp_path, source) == source


def test_engine_scope_uses_the_resolved_worktree_identity(monkeypatch, tmp_path):
    seen = []

    def resolve(root):
        seen.append(root)
        return "a3g64e"

    monkeypatch.setattr(worktree, "resolve_worktree_image_tag_suffix", resolve)
    monkeypatch.setattr(deploy, "resolve_primary_image_references", lambda _root: set())
    source = """services:
  app:
    image: example/app:latest
    build: .
  db:
    image: postgres:16
"""

    rendered = engine.scope_worktree_compose_images(tmp_path, source)

    assert seen == [tmp_path]
    assert yaml.safe_load(rendered)["services"]["app"]["image"] == (
        "example/app:latest-a3g64e"
    )
    assert yaml.safe_load(rendered)["services"]["db"]["image"] == "postgres:16"


def test_engine_scope_refuses_a_tag_named_by_the_primary_image_map(monkeypatch, tmp_path):
    monkeypatch.setattr(
        worktree, "resolve_worktree_image_tag_suffix", lambda _root: "a3g64e"
    )
    monkeypatch.setattr(
        deploy,
        "resolve_primary_image_references",
        lambda _root: {"example/app:latest-a3g64e"},
    )
    source = "services:\n  app:\n    image: example/app:latest\n    build: .\n"

    with pytest.raises(ValueError, match="refusing to overwrite example/app:latest-a3g64e"):
        engine.scope_worktree_compose_images(
            tmp_path, source, check_primary_image_map=True
        )


def test_primary_image_collision_check_normalizes_implicit_latest():
    with pytest.raises(image_isolation.ImageIsolationError, match="example/app:latest"):
        image_isolation.check_primary_image_collisions(
            {"example/app"}, {"example/app:latest"}
        )


def test_primary_image_map_uses_primary_checkout_resolved_identities(monkeypatch, tmp_path):
    primary = tmp_path / "primary" / "ciu"
    linked = tmp_path / "linked" / "ciu"
    seen = {}
    monkeypatch.setattr(worktree, "primary_ciu_root", lambda _root: primary)
    monkeypatch.setattr(
        deploy, "_render_environment_for_checkout",
        lambda root: {"REPO_ROOT": str(root), "INSTANCE_ID": "main-id"},
    )

    def resolve(root, **kwargs):
        seen["root"] = root
        seen.update(kwargs)
        return {
            "resolved": {
                "identities": {
                    "tools/runner": {
                        "runner": {"image": "example/runner"},
                        "postgres": {"image": "postgres:16"},
                    }
                }
            }
        }

    monkeypatch.setattr(deploy, "resolve_identities", resolve)

    assert deploy.resolve_primary_image_references(linked) == {
        "example/runner:latest",
        "postgres:16",
    }
    assert seen == {
        "root": primary,
        "render_environ": {"REPO_ROOT": str(primary), "INSTANCE_ID": "main-id"},
        "require_closed_image_map": True,
        "image_map_only": True,
    }


def test_primary_image_map_refuses_unresolved_compose_interpolation(monkeypatch, tmp_path):
    primary = tmp_path / "primary"
    monkeypatch.setattr(worktree, "primary_ciu_root", lambda _root: primary)
    monkeypatch.setattr(deploy, "_render_environment_for_checkout", lambda _root: {})
    monkeypatch.setattr(
        deploy,
        "resolve_identities",
        lambda *_args, **_kwargs: {
            "resolved": {
                "identities": {"tools/app": {"app": {"image": "team/app:${TAG}"}}}
            }
        },
    )

    with pytest.raises(ValueError, match="unresolved Compose interpolation"):
        deploy.resolve_primary_image_references(tmp_path / "linked")


def test_compose_scopes_build_and_image_inherited_through_yaml_merge():
    source = """x-built: &built
  image: example/app:dev
  build: .
services:
  app:
    <<: *built
  worker:
    image: example/app:dev
"""

    rendered = image_isolation.scope_compose_images(source, "a3g64e")
    document = yaml.safe_load(rendered)

    assert document["services"]["app"]["image"] == "example/app:dev-a3g64e"
    assert document["services"]["worker"]["image"] == "example/app:dev-a3g64e"
    assert "x-built: &built" in rendered


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("registry:5000/team/app:latest", "registry:5000/team/app:latest-a3g64e"),
        ("team/app", "team/app:latest-a3g64e"),
        ("team/app:${TAG:-latest}", "team/app:${TAG:-latest}-a3g64e"),
        ("team/app:latest-a3g64e", "team/app:latest-a3g64e"),
    ],
)
def test_image_tag_suffix_handles_registry_ports_and_compose_defaults(source, expected):
    assert image_isolation.append_instance_tag(source, "a3g64e") == expected


def test_digest_project_image_ref_fails_instead_of_claiming_it_is_scoped():
    with pytest.raises(image_isolation.ImageIsolationError, match="digest image"):
        image_isolation.append_instance_tag(
            "registry.example/team/app@sha256:abcdef", "a3g64e"
        )


def test_project_built_service_without_image_refuses_worktree_scope():
    with pytest.raises(image_isolation.ImageIsolationError, match="no explicit image"):
        image_isolation.scope_compose_images(
            "services:\n  app:\n    build: .\n", "a3g64e"
        )


@pytest.mark.parametrize(
    "source",
    [
        "include:\n  - ./parts/compose.yml\nservices: {}\n",
        "services:\n  app:\n    extends:\n      file: base.yml\n      service: app\n",
    ],
)
def test_compose_external_service_definitions_refuse_unproven_worktree_scope(source):
    with pytest.raises(image_isolation.ImageIsolationError, match="cannot be proven safe"):
        image_isolation.scope_compose_images(source, "a3g64e")


def test_fully_interpolated_image_reference_refuses_in_linked_worktree():
    with pytest.raises(image_isolation.ImageIsolationError, match="fully interpolated"):
        image_isolation.append_instance_tag("${BUILT_IMAGE}", "a3g64e")


def test_bake_plan_scopes_each_image_tag_and_preserves_untagged_targets():
    plan = {
        "target": {
            "app": {"tags": ["registry.example/team/app:latest", "team/app:dev"]},
            "artifact": {"context": "."},
        }
    }
    assert image_isolation.bake_tag_overrides(json.dumps(plan), "a3g64e") == [
        "--set", "app.tags=registry.example/team/app:latest-a3g64e",
        "--set", "app.tags=team/app:dev-a3g64e",
    ]
    assert image_isolation.bake_scoped_tags(json.dumps(plan), "a3g64e") == {
        "registry.example/team/app:latest-a3g64e",
        "team/app:dev-a3g64e",
    }


def test_shipped_image_override_changes_only_image_fields_and_keeps_paths_safe():
    original = """services:
  app:
    image: example/app:latest
    build: ./app
    volumes:
      - ./data:/data
    ports:
      - 8080:80
  worker:
    image: example/app:latest
"""
    scoped = image_isolation.scope_compose_images(original, "a3g64e")
    override = image_isolation.compose_image_override(original, scoped)

    assert override is not None
    assert yaml.safe_load(override) == {
        "services": {
            "app": {"image": "example/app:latest-a3g64e"},
            "worker": {"image": "example/app:latest-a3g64e"},
        }
    }
    assert image_isolation.compose_image_override(original, original) is None


def test_container_name_matching_is_exact_not_substring():
    assert image_isolation.docker_exact_container_ids(
        "a1 unrelated-app-db\nb2 app-db\n", {"app-db"}
    ) == {"app-db": "b2"}


def test_compose_container_names_must_be_unique_within_one_render():
    with pytest.raises(image_isolation.ContainerOwnershipError, match="both declare"):
        image_isolation.explicit_container_services(
            "services:\n  one:\n    container_name: shared\n"
            "  two:\n    container_name: shared\n"
        )


def test_container_name_parser_preserves_compose_interpolation_for_resolution():
    assert image_isolation.explicit_container_services(
        "services:\n  db:\n    container_name: ${DB_CONTAINER}\n"
    ) == {"${DB_CONTAINER}": "db"}


def _docker_result(returncode: int, stdout: str = "", stderr: str = ""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_same_checkout_project_and_service_can_redeploy(monkeypatch, tmp_path):
    repo_root = tmp_path / "checkout"
    physical_root = tmp_path / "host-checkout"
    repo_root.mkdir()
    physical_root.mkdir()
    calls = []
    inspect = [{
        "Name": "/app-db",
        "Config": {"Labels": {
            "com.docker.compose.project.working_dir": str(repo_root),
            "ciu.repo-root": str(physical_root),
            "com.docker.compose.project": "demo-dev-db",
            "com.docker.compose.service": "db",
        }},
    }]

    def fake_run(argv, *, check=False, **kwargs):
        calls.append(argv)
        if argv[0:3] == ["docker", "compose", "-p"]:
            assert kwargs["cwd"] == str(repo_root)
            assert kwargs["env"] == {}
            return _docker_result(
                0,
                json.dumps({"services": {"db": {"container_name": "app-db"}}}),
            )
        if argv[1:3] == ["ps", "-a"]:
            return _docker_result(0, "abc123 app-db\n")
        return _docker_result(0, json.dumps(inspect))

    monkeypatch.setattr(engine.procutil, "run_cmd", fake_run)
    monkeypatch.setattr(
        engine, "workspace_ownership_labels",
        lambda _root: {
            engine.OWNERSHIP_LABEL_INSTANCE: "a3g64e",
            engine.OWNERSHIP_LABEL_REPO_ROOT: str(physical_root),
        },
    )

    engine.guard_container_name_owners(
        "services:\n  db:\n    container_name: app-db\n",
        expected_project="demo-dev-db",
        repo_root=repo_root,
        file_args=["-f", "compose.yml"],
        cwd=repo_root,
        env={},
    )

    assert len(calls) == 3
    assert calls[0][0:3] == ["docker", "compose", "-p"]
    assert calls[1][0:3] == ["docker", "ps", "-a"]


def test_other_checkout_exact_name_refuses_before_compose(monkeypatch, tmp_path):
    repo_root = tmp_path / "worktree"
    other_root = tmp_path / "primary"
    repo_root.mkdir()
    other_root.mkdir()
    inspect = [{
        "Name": "/app-db",
        "Config": {"Labels": {
            "com.docker.compose.project.working_dir": str(other_root),
            "com.docker.compose.project": "demo-prod-db",
            "com.docker.compose.service": "db",
        }},
    }]

    def fake_run(argv, *, check=False, **_kwargs):
        if argv[0:3] == ["docker", "compose", "-p"]:
            return _docker_result(
                0,
                json.dumps({"services": {"db": {"container_name": "app-db"}}}),
            )
        if argv[1:3] == ["ps", "-a"]:
            return _docker_result(0, "abc123 app-db\n")
        return _docker_result(0, json.dumps(inspect))

    monkeypatch.setattr(engine.procutil, "run_cmd", fake_run)
    monkeypatch.setattr(engine, "workspace_ownership_labels", lambda _root: None)

    with pytest.raises(engine.ComposeError, match="refusing to recreate.*primary"):
        engine.guard_container_name_owners(
            "services:\n  db:\n    container_name: app-db\n",
            expected_project="demo-dev-db",
            repo_root=repo_root,
            file_args=["-f", "compose.yml"],
            cwd=repo_root,
            env={},
        )


def test_unreadable_compose_config_refuses_closed(monkeypatch, tmp_path):
    repo_root = tmp_path / "checkout"
    repo_root.mkdir()
    monkeypatch.setattr(
        engine.procutil, "run_cmd",
        lambda *_args, **_kwargs: _docker_result(1, stderr="daemon unavailable"),
    )

    with pytest.raises(engine.ComposeError, match="could not resolve Compose container names"):
        engine.guard_container_name_owners(
            "services:\n  db:\n    container_name: app-db\n",
            expected_project="demo-dev-db",
            repo_root=repo_root,
            file_args=["-f", "compose.yml"],
            cwd=repo_root,
            env={},
        )


def test_interpolated_container_name_is_checked_after_compose_resolution(
    monkeypatch, tmp_path
):
    repo_root = tmp_path / "checkout"
    other_root = tmp_path / "primary"
    repo_root.mkdir()
    other_root.mkdir()
    inspect = [{
        "Name": "/app-db",
        "Config": {"Labels": {
            "com.docker.compose.project.working_dir": str(other_root),
            "com.docker.compose.project": "demo-prod-db",
            "com.docker.compose.service": "db",
        }},
    }]
    calls = []

    def fake_run(argv, *, check=False, **kwargs):
        calls.append(argv)
        if argv[0:3] == ["docker", "compose", "-p"]:
            assert kwargs["env"] == {"DB_CONTAINER": "app-db"}
            return _docker_result(
                0,
                json.dumps({"services": {"db": {"container_name": "app-db"}}}),
            )
        if argv[1:3] == ["ps", "-a"]:
            return _docker_result(0, "abc123 app-db\n")
        return _docker_result(0, json.dumps(inspect))

    monkeypatch.setattr(engine.procutil, "run_cmd", fake_run)
    monkeypatch.setattr(engine, "workspace_ownership_labels", lambda _root: None)

    with pytest.raises(engine.ComposeError, match="refusing to recreate.*primary"):
        engine.guard_container_name_owners(
            "services:\n  db:\n    container_name: ${DB_CONTAINER}\n",
            expected_project="demo-dev-db",
            repo_root=repo_root,
            file_args=["-f", "compose.yml"],
            cwd=repo_root,
            env={"DB_CONTAINER": "app-db"},
        )
    assert calls[0][-3:] == ["config", "--format", "json"]


def test_resolved_compose_name_inherited_from_another_file_is_checked(
    monkeypatch, tmp_path
):
    repo_root = tmp_path / "checkout"
    other_root = tmp_path / "primary"
    repo_root.mkdir()
    other_root.mkdir()
    inspect = [{
        "Name": "/app-db",
        "Config": {"Labels": {
            "com.docker.compose.project.working_dir": str(other_root),
            "com.docker.compose.project": "demo-prod-db",
            "com.docker.compose.service": "db",
        }},
    }]

    def fake_run(argv, *, check=False, **_kwargs):
        if argv[0:3] == ["docker", "compose", "-p"]:
            return _docker_result(
                0,
                json.dumps({"services": {"db": {"container_name": "app-db"}}}),
            )
        if argv[1:3] == ["ps", "-a"]:
            return _docker_result(0, "abc123 app-db\n")
        return _docker_result(0, json.dumps(inspect))

    monkeypatch.setattr(engine.procutil, "run_cmd", fake_run)
    monkeypatch.setattr(engine, "workspace_ownership_labels", lambda _root: None)

    with pytest.raises(engine.ComposeError, match="refusing to recreate.*primary"):
        engine.guard_container_name_owners(
            "services:\n  db:\n    extends:\n      file: base.yml\n      service: db\n",
            expected_project="demo-dev-db",
            repo_root=repo_root,
            file_args=["-f", "compose.yml", "-f", "ownership-overlay.yml"],
            cwd=repo_root,
            env={},
        )


def test_unreadable_docker_query_refuses_closed(monkeypatch, tmp_path):
    repo_root = tmp_path / "checkout"
    repo_root.mkdir()

    def fake_run(argv, *, check=False, **_kwargs):
        if argv[0:3] == ["docker", "compose", "-p"]:
            return _docker_result(
                0,
                json.dumps({"services": {"db": {"container_name": "app-db"}}}),
            )
        return _docker_result(1, stderr="daemon unavailable")

    monkeypatch.setattr(engine.procutil, "run_cmd", fake_run)

    with pytest.raises(engine.ComposeError, match="could not verify explicit container-name ownership"):
        engine.guard_container_name_owners(
            "services:\n  db:\n    container_name: app-db\n",
            expected_project="demo-dev-db",
            repo_root=repo_root,
            file_args=["-f", "compose.yml"],
            cwd=repo_root,
            env={},
        )


def test_unreadable_docker_inspection_refuses_closed(monkeypatch, tmp_path):
    repo_root = tmp_path / "checkout"
    repo_root.mkdir()

    def fake_run(argv, *, check=False, **_kwargs):
        if argv[0:3] == ["docker", "compose", "-p"]:
            return _docker_result(
                0,
                json.dumps({"services": {"db": {"container_name": "app-db"}}}),
            )
        if argv[1:3] == ["ps", "-a"]:
            return _docker_result(0, "abc123 app-db\n")
        return _docker_result(1, stderr="container disappeared")

    monkeypatch.setattr(engine.procutil, "run_cmd", fake_run)

    with pytest.raises(engine.ComposeError, match="could not prove who owns"):
        engine.guard_container_name_owners(
            "services:\n  db:\n    container_name: app-db\n",
            expected_project="demo-dev-db",
            repo_root=repo_root,
            file_args=["-f", "compose.yml"],
            cwd=repo_root,
            env={},
        )


def test_unreadable_git_checkout_is_not_mistaken_for_primary(monkeypatch, tmp_path):
    repo_root = tmp_path / "checkout" / "ciu"
    repo_root.mkdir(parents=True)
    (repo_root.parent / ".git").write_text("gitdir: missing\n")

    from ciu import workspace

    monkeypatch.setattr(
        workspace, "resolve_worktree_git_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            workspace.CiuWorkspaceError("[no-git-root] broken checkout")
        ),
    )
    with pytest.raises(worktree.WorktreeError, match="could not resolve the checkout's Git identity"):
        worktree.resolve_worktree_image_tag_suffix(repo_root)


@pytest.mark.parametrize("value", [True, False, 1, None, "false"])
def test_shared_image_tags_is_a_boolean_closed_config_value(value):
    if isinstance(value, bool):
        worktree._validate_worktree_table({"shared_image_tags": value})
    else:
        with pytest.raises(worktree.WorktreeError, match="must be a boolean"):
            worktree._validate_worktree_table({"shared_image_tags": value})


def test_linked_checkout_uses_recorded_generated_identity_unless_opted_out(
    monkeypatch, tmp_path
):
    from ciu import workspace

    linked_root = tmp_path / "linked" / "ciu"
    primary_root = tmp_path / "primary"
    linked_root.mkdir(parents=True)
    primary_root.mkdir()
    record_path = linked_root / worktree.WORKTREE_INSTANCE_RECORD
    record_path.write_text("{}\n")
    entries = [
        worktree.WorktreeInfo(linked_root.parent, "linked", "abc", False),
        worktree.WorktreeInfo(primary_root, "main", "def", True),
    ]
    monkeypatch.setattr(
        workspace, "resolve_worktree_git_root", lambda _root: linked_root.parent
    )
    monkeypatch.setattr(worktree, "list_worktrees", lambda _root: entries)
    monkeypatch.setattr(worktree, "_primary_worktree_table", lambda _root: {})
    monkeypatch.setattr(
        worktree, "read_instance_record",
        lambda _path: SimpleNamespace(state="ready", instance_id="a3g64e"),
    )
    monkeypatch.setattr(
        "ciu.workspace_env.read_generated_facts",
        lambda _root, *, allow_repair: {"instance_id": "a3g64e"},
    )

    assert worktree.resolve_worktree_image_tag_suffix(linked_root) == "a3g64e"
    record_path.unlink()
    assert worktree.resolve_worktree_image_tag_suffix(linked_root) == "a3g64e"
    record_path.write_text("{}\n")
    monkeypatch.setattr(
        worktree, "_primary_worktree_table",
        lambda _root: {"shared_image_tags": True},
    )
    assert worktree.resolve_worktree_image_tag_suffix(
        linked_root
    ) is None


def test_linked_worktree_bake_scopes_resolved_tags(monkeypatch, tmp_path):
    repo_root = tmp_path / "checkout"
    repo_root.mkdir()
    (repo_root / worktree.WORKTREE_INSTANCE_RECORD).write_text("{}\n")
    monkeypatch.setattr(dev, "resolve_repo_root", lambda *_args, **_kwargs: repo_root)
    monkeypatch.setattr(deploy, "load_global_config", lambda _root: {})
    monkeypatch.setattr(deploy, "resolve_primary_image_references", lambda _root: set())
    monkeypatch.setattr(
        worktree, "resolve_worktree_image_tag_suffix",
        lambda *_args, **_kwargs: "a3g64e",
    )
    preview = json.dumps({"target": {"app": {"tags": ["team/app:latest"]}}})
    commands = []
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda argv, **_kwargs: commands.append(("print", argv))
        or _docker_result(0, preview),
    )
    monkeypatch.setattr(
        cli.subprocess, "call",
        lambda argv: commands.append(("build", argv)) or 0,
    )
    monkeypatch.setattr(engine, "get_git_hash", lambda: "abcdef01")

    assert cli._bake(["app"]) == 0
    assert commands == [
        ("print", ["docker", "buildx", "bake", "app", "--print"]),
        ("build", [
            "docker", "buildx", "bake", "app", "--load",
            "--set", "*.labels.org.opencontainers.image.revision=abcdef01",
            "--set", "app.tags=team/app:latest-a3g64e",
        ]),
    ]


def test_linked_worktree_bake_refuses_primary_image_tag_collision(
    monkeypatch, tmp_path, capsys
):
    repo_root = tmp_path / "checkout"
    repo_root.mkdir()
    (repo_root / worktree.WORKTREE_INSTANCE_RECORD).write_text("{}\n")
    monkeypatch.setattr(dev, "resolve_repo_root", lambda *_args, **_kwargs: repo_root)
    monkeypatch.setattr(deploy, "load_global_config", lambda _root: {})
    monkeypatch.setattr(
        worktree, "resolve_worktree_image_tag_suffix",
        lambda *_args, **_kwargs: "a3g64e",
    )
    monkeypatch.setattr(
        deploy,
        "resolve_primary_image_references",
        lambda _root: {"team/app:latest-a3g64e"},
    )
    preview = json.dumps({"target": {"app": {"tags": ["team/app:latest"]}}})
    calls = []
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda argv, **_kwargs: calls.append(("print", argv))
        or _docker_result(0, preview),
    )
    monkeypatch.setattr(
        cli.subprocess, "call",
        lambda argv: calls.append(("build", argv)) or 0,
    )

    assert cli._bake(["app"]) == 2
    assert calls == [("print", ["docker", "buildx", "bake", "app", "--print"])]
    assert "[CIU-117] refusing to overwrite team/app:latest-a3g64e" in (
        capsys.readouterr().err
    )


def test_linked_worktree_bake_reports_missing_buildx(monkeypatch, tmp_path, capsys):
    repo_root = tmp_path / "checkout"
    repo_root.mkdir()
    monkeypatch.setattr(dev, "resolve_repo_root", lambda *_args, **_kwargs: repo_root)
    monkeypatch.setattr(
        worktree, "resolve_worktree_image_tag_suffix",
        lambda *_args, **_kwargs: "a3g64e",
    )
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("docker")),
    )

    assert cli._bake(["app"]) == 2
    assert "could not resolve Buildx image tags" in capsys.readouterr().err


def test_linked_worktree_bake_shared_tag_flag_keeps_declared_tags(monkeypatch, tmp_path, capsys):
    repo_root = tmp_path / "checkout"
    repo_root.mkdir()
    (repo_root / worktree.WORKTREE_INSTANCE_RECORD).write_text("{}\n")
    monkeypatch.setattr(dev, "resolve_repo_root", lambda *_args, **_kwargs: repo_root)
    monkeypatch.setattr(deploy, "load_global_config", lambda _root: {})
    monkeypatch.setattr(
        worktree, "resolve_worktree_image_tag_suffix",
        lambda *_args, **kwargs: None if kwargs["allow_shared_tag"] else "a3g64e",
    )
    monkeypatch.setattr(engine, "get_git_hash", lambda: "abcdef01")
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("shared-tag override must not need a Bake preview")
        ),
    )
    seen = []
    monkeypatch.setattr(cli.subprocess, "call", lambda argv: seen.append(argv) or 0)

    assert cli._bake(["app", "--allow-shared-tag"]) == 0
    assert seen == [[
        "docker", "buildx", "bake", "app", "--load",
        "--set", "*.labels.org.opencontainers.image.revision=abcdef01",
    ]]
    assert "declared tags without worktree scoping" in capsys.readouterr().err


def test_bake_refuses_linked_git_checkout_without_ciu_root(
    monkeypatch, tmp_path, capsys
):
    primary = tmp_path / "primary"
    linked = tmp_path / "linked"
    primary.mkdir()
    linked.mkdir()
    monkeypatch.chdir(linked)
    monkeypatch.setattr(
        dev, "resolve_repo_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("[no-ciu-root] missing CIU marker")
        ),
    )
    monkeypatch.setattr(workspace, "resolve_worktree_git_root", lambda _start: linked)
    monkeypatch.setattr(
        worktree,
        "list_worktrees",
        lambda _root: [
            SimpleNamespace(path=primary, is_primary=True),
            SimpleNamespace(path=linked, is_primary=False),
        ],
    )
    monkeypatch.setattr(
        cli.subprocess, "call",
        lambda _argv: (_ for _ in ()).throw(
            AssertionError("rootless linked checkout must not run an unscoped Bake")
        ),
    )

    assert cli._bake(["app"]) == 2
    assert "linked Git worktree without a CIU root" in capsys.readouterr().err


def test_bake_allows_explicit_shared_tag_opt_out_without_ciu_root(
    monkeypatch, tmp_path, capsys
):
    linked = tmp_path / "linked"
    linked.mkdir()
    monkeypatch.chdir(linked)
    monkeypatch.setattr(
        dev, "resolve_repo_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("[no-ciu-root] missing CIU marker")
        ),
    )
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("an explicit shared-tag Bake needs no scoped-tag preview")
        ),
    )
    monkeypatch.setattr(engine, "get_git_hash", lambda: "abcdef01")
    calls = []
    monkeypatch.setattr(cli.subprocess, "call", lambda argv: calls.append(argv) or 0)

    assert cli._bake(["app", "--allow-shared-tag"]) == 0
    assert calls == [[
        "docker", "buildx", "bake", "app", "--load",
        "--set", "*.labels.org.opencontainers.image.revision=abcdef01",
    ]]
    assert "declared tags without worktree scoping" in capsys.readouterr().err


def test_bake_keeps_primary_git_checkout_without_ciu_root_compatible(
    monkeypatch, tmp_path
):
    primary = tmp_path / "primary"
    primary.mkdir()
    monkeypatch.chdir(primary)
    monkeypatch.setattr(
        dev, "resolve_repo_root",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("[no-ciu-root] missing CIU marker")
        ),
    )
    monkeypatch.setattr(workspace, "resolve_worktree_git_root", lambda _start: primary)
    monkeypatch.setattr(
        worktree, "list_worktrees",
        lambda _root: [SimpleNamespace(path=primary, is_primary=True)],
    )
    calls = []
    monkeypatch.setattr(cli.subprocess, "call", lambda argv: calls.append(argv) or 0)

    assert cli._bake(["app"]) == 0
    assert calls == [[
        "docker", "buildx", "bake", "app", "--load", *engine.bake_revision_args()
    ]]
