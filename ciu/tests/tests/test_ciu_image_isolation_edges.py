"""Boundary tests for CIU-117 image and CIU-104 owner parsing helpers."""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import (  # noqa: E402
    cli,
    composefile,
    deploy,
    dev,
    engine,
    image_isolation as iso,
    worktree,
    workspace,
    workspace_env,
    workspace_env,
)
from ciu.config_constants import SHIPPED_COMPOSE  # noqa: E402


@pytest.mark.parametrize(
    ("source", "error"),
    [
        ("services: [", "invalid"),
        ("[x]", "mapping"),
        ("services: []", "services must be a YAML mapping"),
        ("services:\n  db: false\n", "services must be mappings"),
    ],
)
def test_compose_scoping_predicate_rejects_invalid_yaml_shapes(source, error):
    with pytest.raises(iso.ImageIsolationError, match=error):
        iso.compose_may_require_image_scoping(source)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("services: {}\n", False),
        ("include: [parts.yml]\nservices: {}\n", True),
        ("services:\n  app:\n    extends: base\n", True),
        ("services:\n  app:\n    build: .\n", True),
        ("services:\n  app:\n    build: false\n", False),
        ("services:\n  app:\n    build: null\n", False),
    ],
)
def test_compose_scoping_predicate_detects_only_build_or_import(source, expected):
    assert iso.compose_may_require_image_scoping(source) is expected


@pytest.mark.parametrize("reference", [None, "", "   "])
def test_image_name_and_normalizer_reject_empty_references(reference):
    with pytest.raises(iso.ImageIsolationError, match="non-empty string"):
        iso.append_instance_tag(reference, "id")
    with pytest.raises(iso.ImageIsolationError, match="non-empty string"):
        iso.normalize_image_reference(reference)


@pytest.mark.parametrize("instance_id", [None, ""])
def test_image_suffix_requires_an_instance_id(instance_id):
    with pytest.raises(iso.ImageIsolationError, match="non-empty instance id"):
        iso.append_instance_tag("team/app", instance_id)


def test_image_suffix_rejects_an_empty_tag_and_normalizer_preserves_digest():
    with pytest.raises(iso.ImageIsolationError, match="empty tag"):
        iso.append_instance_tag("team/app:", "id")
    digest = "team/app@sha256:abc"
    assert iso.normalize_image_reference(digest) == digest


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("services: [", "invalid"),
        ("[x]", "mapping"),
        ("include: [parts.yml]\nservices: {}\n", "include imports"),
        ("services: []\n", "services must be a YAML mapping"),
        ("services:\n  app: false\n", "must be a YAML mapping"),
        ("services:\n  app:\n    extends: base\n", "extends a definition"),
        ("services:\n  app:\n    build: .\n", "no explicit image"),
    ],
)
def test_project_built_image_map_refuses_unprovable_inputs(source, message):
    with pytest.raises(iso.ImageIsolationError, match=message):
        iso.project_built_image_references(source)


def test_project_built_image_map_skips_nonbuild_and_false_build_services():
    source = """services:
  pulled:
    image: postgres:16
  disabled:
    image: local/disabled
    build: false
  null_build:
    image: local/null
    build: null
  app:
    image: team/app
    build: .
"""
    assert iso.project_built_image_references(source) == {"team/app:latest"}


def test_primary_collision_check_rejects_unresolved_and_accepts_disjoint_maps():
    with pytest.raises(iso.ImageIsolationError, match="unresolved Compose"):
        iso.check_primary_image_collisions({"team/app:${TAG}"}, set())
    iso.check_primary_image_collisions({"team/app:worktree"}, {"team/app:main"})


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("services: [", "invalid"),
        ("[]", "mapping"),
        ("include: [parts.yml]\nservices: {}\n", "include imports"),
        ("services: []\n", "services must be a YAML mapping"),
        ("services:\n  app: false\n", "must be a YAML mapping"),
        ("services:\n  app:\n    extends: base\n", "extends a definition"),
        ("services:\n  app:\n    build: .\n", "no explicit image"),
    ],
)
def test_compose_image_scoper_refuses_unprovable_inputs(source, message):
    with pytest.raises(iso.ImageIsolationError, match=message):
        iso.scope_compose_images(source, "id")


def test_compose_image_scoper_is_noop_without_identity_or_built_images():
    malformed = "services: ["
    assert iso.scope_compose_images(malformed, None) == malformed
    empty = ""
    assert iso.scope_compose_images(empty, "id") == empty
    no_services = "volumes: {}\n"
    assert iso.scope_compose_images(no_services, "id") == no_services
    no_build = "services:\n  db:\n    image: postgres:16\n"
    assert iso.scope_compose_images(no_build, "id") == no_build


def test_compose_image_scoper_handles_false_builds_and_already_scoped_tags():
    source = """services:
  disabled:
    image: team/disabled
    build: false
  null_build:
    image: team/null
    build: null
  app:
    image: team/app:dev-id
    build: .
"""
    assert iso.scope_compose_images(source, "id") == source


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("services:\n  app:\n    image: |\n      team/app\n    build: .\n", "multiline"),
        ('services:\n  app:\n    image: "team/app:dev\\nother"\n    build: .\n', "line breaks"),
    ],
)
def test_compose_image_scoper_refuses_multiline_image_values(source, message):
    with pytest.raises(iso.ImageIsolationError, match=message):
        iso.scope_compose_images(source, "id")


@pytest.mark.parametrize(
    ("original", "scoped", "message"),
    [
        ("services: [", "services: {}", "invalid"),
        ("[x]", "services: {}", "documents must be YAML mappings"),
        ("services: {}", "services: []", "services must be YAML mappings"),
        ("services: {}", "services:\n  x: false\n", "named mappings"),
        ("services: {}", "services:\n  1: {image: app:new}\n", "named mappings"),
    ],
)
def test_compose_override_rejects_malformed_documents(original, scoped, message):
    with pytest.raises(iso.ImageIsolationError, match=message):
        iso.compose_image_override(original, scoped)


def test_compose_override_ignores_services_added_only_by_scoping():
    assert iso.compose_image_override(
        "services: {}\n", "services:\n  other:\n    image: team/app\n"
    ) is None


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("{", "invalid JSON"),
        ("[]", "omitted its target mapping"),
        ('{"target": []}', "omitted its target mapping"),
        ('{"target": {"app": []}}', "must be a mapping"),
        ('{"target": {"app": {"tags": "team/app"}}}', "malformed tags"),
        ('{"target": {"app": {"tags": [1]}}}', "malformed tags"),
        ('{"target": {"app*": {"tags": ["team/app"]}}}', "cannot be addressed safely"),
    ],
)
def test_bake_tag_overrides_rejects_invalid_plans(payload, message):
    with pytest.raises(iso.ImageIsolationError, match=message):
        iso.bake_tag_overrides(payload, "id")


def test_bake_tag_overrides_leaves_untagged_and_empty_tags_alone():
    payload = json.dumps({"target": {"app": {}, "empty": {"tags": []}}})
    assert iso.bake_tag_overrides(payload, "id") == []


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("{", "invalid JSON"),
        ("[]", "omitted its target mapping"),
        ('{"target": {"app": []}}', "must be a mapping"),
        ('{"target": {"app": {"tags": [1]}}}', "malformed tags"),
    ],
)
def test_bake_scoped_tag_reader_rejects_invalid_plans(payload, message):
    with pytest.raises(iso.ImageIsolationError, match=message):
        iso.bake_scoped_tags(payload, "id")


def test_bake_scoped_tag_reader_skips_targets_without_tags():
    assert iso.bake_scoped_tags('{"target": {"app": {}}}', "id") == set()


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("services: [", "invalid"),
        ("[x]", "mapping"),
        ("services: []", "services must be a YAML mapping"),
        ("services:\n  app: false\n", "must be a YAML mapping"),
        ("services:\n  app:\n    container_name: ''\n", "invalid container_name"),
        ("services:\n  app:\n    container_name: 3\n", "invalid container_name"),
    ],
)
def test_explicit_container_reader_rejects_malformed_services(source, message):
    with pytest.raises(iso.ContainerOwnershipError, match=message):
        iso.explicit_container_services(source)


def test_explicit_container_reader_skips_missing_names_and_keeps_unique_names():
    assert iso.explicit_container_services(
        "services:\n  app: {}\n  db:\n    container_name: db-one\n"
    ) == {"db-one": "db"}


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("services: [", "invalid"),
        ("[x]", "mapping"),
        ("services: []", "services must be a YAML mapping"),
    ],
)
def test_compose_import_detector_rejects_malformed_compose(source, message):
    with pytest.raises(iso.ContainerOwnershipError, match=message):
        iso.compose_may_import_service_definitions(source)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("services: {}\n", False),
        ("include: [parts.yml]\nservices: {}\n", True),
        ("services:\n  app: false\n", False),
        ("services:\n  app:\n    extends: base\n", True),
    ],
)
def test_compose_import_detector_reports_external_services(source, expected):
    assert iso.compose_may_import_service_definitions(source) is expected


@pytest.mark.parametrize(
    ("listing", "message"),
    [
        ("id-only\n", "malformed row"),
        ("id name\nid name\n", "multiple containers"),
    ],
)
def test_docker_container_listing_rejects_malformed_or_duplicate_names(listing, message):
    with pytest.raises(iso.ContainerOwnershipError, match=message):
        iso.docker_exact_container_ids(listing, {"name"})


def test_docker_container_listing_ignores_blank_and_unrequested_rows():
    assert iso.docker_exact_container_ids("\nabc other\n", {"name"}) == {}


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("{", "invalid JSON"),
        ("{}", "no unique container"),
        ("[]", "no unique container"),
        ('[{"Config": {}}]', "omitted the container name"),
        ('[{"Name": "container"}]', "omitted the container name"),
        ('[{"Name": "/app", "Config": {"Labels": []}}]', "malformed labels"),
        ('[{"Name": "/app", "Config": {"Labels": {"x": 3}}}]', "malformed labels"),
    ],
)
def test_inspect_labels_rejects_invalid_docker_responses(payload, message):
    with pytest.raises(iso.ContainerOwnershipError, match=message):
        iso.inspect_labels(payload, "id")


def test_inspect_labels_accepts_missing_labels_as_empty():
    assert iso.inspect_labels('[{"Name": "/app", "Config": {}}]', "id") == (
        "app", {}
    )


def test_same_compose_owner_requires_root_project_and_service_agreement():
    current = {"/repo", "/physical/repo"}
    assert iso.same_compose_owner(
        {}, current_roots=current, project="demo", service="db"
    ) == (False, None)
    assert iso.same_compose_owner(
        {
            "com.docker.compose.project.working_dir": "/repo",
            "com.docker.compose.project": "demo",
            "com.docker.compose.service": "db",
        },
        current_roots=current,
        project="demo",
        service="db",
    ) == (True, "/repo")
    same, owner = iso.same_compose_owner(
        {
            "ciu.repo-root": "/other",
            "com.docker.compose.project": "demo",
            "com.docker.compose.service": "db",
        },
        current_roots=current,
        project="demo",
        service="db",
    )
    assert (same, owner) == (False, "/other")


def _result(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


_SOURCE_CONTAINER = "services:\n  db:\n    container_name: app-db\n"
_RESOLVED_CONTAINER = json.dumps(
    {"services": {"db": {"container_name": "app-db"}}}
)


def _guard_container(monkeypatch, tmp_path, runner, source=_SOURCE_CONTAINER):
    root = tmp_path / "checkout"
    root.mkdir(exist_ok=True)
    monkeypatch.setattr(engine.procutil, "run_cmd", runner)
    return engine.guard_container_name_owners(
        source,
        expected_project="demo",
        repo_root=root,
        file_args=["-f", "compose.yml"],
        cwd=root,
        env={},
    )


def test_container_owner_guard_rejects_source_parse_error_and_skips_plain_services(
    monkeypatch, tmp_path
):
    with pytest.raises(engine.ComposeError, match=r"\[CIU-104\]"):
        _guard_container(monkeypatch, tmp_path, lambda *_a, **_kw: pytest.fail("no Docker"), "services: [")

    calls = []
    assert _guard_container(
        monkeypatch,
        tmp_path,
        lambda *args, **kwargs: calls.append((args, kwargs)),
        "services:\n  db:\n    image: postgres:16\n",
    ) is None
    assert calls == []


@pytest.mark.parametrize(
    ("runner", "message"),
    [
        (lambda *_a, **_kw: (_ for _ in ()).throw(OSError("docker missing")), "could not resolve Compose container names"),
        (lambda *_a, **_kw: _result(1, stderr="bad compose"), "bad compose"),
        (lambda *_a, **_kw: _result(1, stdout="compose stdout error"), "compose stdout error"),
        (lambda *_a, **_kw: _result(1), "unknown Compose error"),
    ],
)
def test_container_owner_guard_reports_compose_resolution_failures(
    monkeypatch, tmp_path, runner, message
):
    with pytest.raises(engine.ComposeError, match=message):
        _guard_container(monkeypatch, tmp_path, runner)


def test_container_owner_guard_rejects_bad_resolved_shape_and_skips_no_names(
    monkeypatch, tmp_path
):
    with pytest.raises(engine.ComposeError, match="could not inspect resolved"):
        _guard_container(monkeypatch, tmp_path, lambda *_a, **_kw: _result(stdout="[x]"))

    calls = []
    def no_names(argv, **_kwargs):
        calls.append(argv)
        return _result(stdout=json.dumps({"services": {"db": {"image": "postgres"}}}))

    _guard_container(monkeypatch, tmp_path, no_names, "services:\n  db:\n    extends: base\n")
    assert len(calls) == 1


def test_container_owner_guard_rejects_unreadable_current_identity(monkeypatch, tmp_path):
    def runner(argv, **_kwargs):
        if argv[0:2] == ["docker", "compose"]:
            return _result(stdout=_RESOLVED_CONTAINER)
        pytest.fail(f"unexpected Docker call: {argv}")

    monkeypatch.setattr(
        engine,
        "workspace_ownership_labels",
        lambda _root: (_ for _ in ()).throw(ValueError("bad identity")),
    )
    with pytest.raises(engine.ComposeError, match="cannot prove this checkout"):
        _guard_container(monkeypatch, tmp_path, runner)


@pytest.mark.parametrize(
    ("runner", "message"),
    [
        (lambda *_a, **_kw: (_ for _ in ()).throw(OSError("daemon missing")), "could not query Docker"),
        (lambda *_a, **_kw: _result(1, stdout="daemon says no"), "daemon says no"),
        (lambda *_a, **_kw: _result(1), "unknown Docker error"),
        (lambda *_a, **_kw: _result(stdout="broken-row\n"), "malformed row"),
    ],
)
def test_container_owner_guard_reports_container_list_failures(
    monkeypatch, tmp_path, runner, message
):
    def dispatch(argv, **_kwargs):
        if argv[0:2] == ["docker", "compose"]:
            return _result(stdout=_RESOLVED_CONTAINER)
        return runner(argv, **_kwargs)

    with pytest.raises(engine.ComposeError, match=message):
        _guard_container(monkeypatch, tmp_path, dispatch)


def test_container_owner_guard_accepts_absent_container_and_checks_inspect_name(
    monkeypatch, tmp_path
):
    calls = []
    def absent(argv, **_kwargs):
        calls.append(argv)
        if argv[0:2] == ["docker", "compose"]:
            return _result(stdout=_RESOLVED_CONTAINER)
        return _result(stdout="")

    assert _guard_container(monkeypatch, tmp_path, absent) is None
    assert len(calls) == 2

    def wrong_name(argv, **_kwargs):
        if argv[0:2] == ["docker", "compose"]:
            return _result(stdout=_RESOLVED_CONTAINER)
        if argv[0:2] == ["docker", "ps"]:
            return _result(stdout="abc app-db\n")
        return _result(stdout='[{"Name":"/other","Config":{"Labels":{}}}]')

    monkeypatch.setattr(engine, "workspace_ownership_labels", lambda _root: None)
    with pytest.raises(engine.ComposeError, match="could not prove who owns.*returned 'other'"):
        _guard_container(monkeypatch, tmp_path, wrong_name)


@pytest.mark.parametrize(
    ("inspected", "message"),
    [
        (lambda *_a, **_kw: (_ for _ in ()).throw(OSError("inspect unavailable")), "inspect unavailable"),
        (lambda *_a, **_kw: _result(1), "container changed during inspection"),
        (lambda *_a, **_kw: _result(stdout="{"), "invalid JSON"),
    ],
)
def test_container_owner_guard_refuses_indeterminate_inspection(
    monkeypatch, tmp_path, inspected, message
):
    def dispatch(argv, **kwargs):
        if argv[0:2] == ["docker", "compose"]:
            return _result(stdout=_RESOLVED_CONTAINER)
        if argv[0:2] == ["docker", "ps"]:
            return _result(stdout="abc app-db\n")
        return inspected(argv, **kwargs)

    monkeypatch.setattr(engine, "workspace_ownership_labels", lambda _root: None)
    with pytest.raises(engine.ComposeError, match=message):
        _guard_container(monkeypatch, tmp_path, dispatch)


def test_container_owner_guard_requires_matching_project_and_service_labels(
    monkeypatch, tmp_path
):
    def runner(argv, **_kwargs):
        if argv[0:2] == ["docker", "compose"]:
            return _result(stdout=_RESOLVED_CONTAINER)
        if argv[0:2] == ["docker", "ps"]:
            return _result(stdout="abc app-db\n")
        return _result(stdout=json.dumps([{
            "Name": "/app-db",
            "Config": {"Labels": {
                "com.docker.compose.project.working_dir": str(tmp_path / "checkout"),
                "com.docker.compose.project": "another-project",
                "com.docker.compose.service": "another-service",
            }},
        }]))

    monkeypatch.setattr(engine, "workspace_ownership_labels", lambda _root: None)
    with pytest.raises(engine.ComposeError, match="refusing to recreate.*another-project"):
        _guard_container(monkeypatch, tmp_path, runner)


def test_scope_worktree_image_check_accepts_explicit_primary_map_and_no_change(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(worktree, "resolve_worktree_image_tag_suffix", lambda _root: "id")
    monkeypatch.setattr(
        deploy,
        "resolve_primary_image_references",
        lambda _root: pytest.fail("explicit map avoids resolver"),
    )
    source = "services:\n  app:\n    image: team/app:dev\n    build: .\n"
    result = engine.scope_worktree_compose_images(
        tmp_path,
        source,
        primary_image_references={"team/app:main"},
        check_primary_image_map=True,
    )
    assert "team/app:dev-id" in result
    assert "suffix 'id'" in capsys.readouterr().out
    assert engine.scope_worktree_compose_images(tmp_path, result) == result


def test_scope_worktree_image_map_skips_primary_lookup_when_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(iso, "compose_may_require_image_scoping", lambda _yaml: True)
    monkeypatch.setattr(worktree, "resolve_worktree_image_tag_suffix", lambda _root: "id")
    monkeypatch.setattr(iso, "scope_compose_images", lambda source, _suffix: source)
    monkeypatch.setattr(iso, "project_built_image_references", lambda _source: set())
    monkeypatch.setattr(
        deploy,
        "resolve_primary_image_references",
        lambda _root: pytest.fail("empty build map must not read primary images"),
    )
    source = "services: {}\n"
    assert engine.scope_worktree_compose_images(
        tmp_path, source, check_primary_image_map=True
    ) == source


def test_render_environment_for_checkout_scrubs_legacy_and_reads_checkout_facts(
    monkeypatch, tmp_path
):
    facts_path = tmp_path / "facts.json"
    monkeypatch.setattr(workspace_env, "generated_facts_path", lambda _root: facts_path)
    monkeypatch.setattr(workspace_env, "LEGACY_IDENTITY_ENV_KEYS", ("REPO_ROOT", "INSTANCE_ID"))
    monkeypatch.setattr(workspace_env, "MACHINE_FACT_ENV_KEYS", {"physical_repo_root": "HOST_ROOT"})
    monkeypatch.setenv("REPO_ROOT", "/stale")
    monkeypatch.setenv("INSTANCE_ID", "stale-id")
    no_facts = deploy._render_environment_for_checkout(tmp_path)
    assert "REPO_ROOT" not in no_facts
    assert "INSTANCE_ID" not in no_facts

    facts_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        workspace_env,
        "read_generated_facts",
        lambda _root, *, allow_repair: {"instance_id": "fresh"},
    )
    monkeypatch.setattr(
        workspace_env, "identity_env_from_facts", lambda facts: {"INSTANCE_ID": facts["instance_id"]}
    )
    monkeypatch.setattr(
        workspace_env,
        "read_generated_machine_facts",
        lambda _root: {"physical_repo_root": "/host/repo"},
    )
    facts_env = deploy._render_environment_for_checkout(tmp_path)
    assert facts_env["INSTANCE_ID"] == "fresh"
    assert facts_env["HOST_ROOT"] == "/host/repo"


def _prepare_identity_resolver(monkeypatch, repo, *, profile_config=None, entries=()):
    profile = SimpleNamespace(config=profile_config or {}, name="test")
    selection = object()
    monkeypatch.setattr(deploy, "load_global_config", lambda *_a, **_kw: profile.config)
    monkeypatch.setattr(deploy.config_model, "render_global_chain", lambda *_a, **_kw: profile.config)
    monkeypatch.setattr(deploy.profiles_pkg, "reject_groups", lambda _config: None)
    monkeypatch.setattr(deploy, "resolve_profiles", lambda *_a, **_kw: profile)
    monkeypatch.setattr(deploy, "build_selection", lambda _profile: selection)
    monkeypatch.setattr(deploy, "_resolve_identity_stack_paths", lambda *_a, **_kw: list(entries))
    monkeypatch.setattr(deploy.profiles_pkg, "render_ciu_context", lambda *_a: {})
    monkeypatch.setattr(deploy, "profile_env", lambda *_a, **_kw: {})
    monkeypatch.setattr(deploy.engine, "scope_worktree_compose_images", lambda _root, source: source)
    monkeypatch.setattr(deploy.engine, "compose_project_name", lambda *_a: "project")
    monkeypatch.setattr(deploy, "_resolve_identity_live_state", lambda *_a: {"status": "running"})
    return profile


def test_resolve_identities_accepts_source_environment_for_image_map_without_network(
    monkeypatch, tmp_path
):
    _prepare_identity_resolver(monkeypatch, tmp_path)
    monkeypatch.setattr(deploy, "_resolve_identity_stack_paths", lambda *_a, **_kw: [])
    document = deploy.resolve_identities(
        tmp_path, render_environ={"REPO_ROOT": str(tmp_path)}, image_map_only=True
    )
    assert document == {"schema_version": 1, "resolved": {"identities": {}}}


def test_resolve_identities_scopes_rendered_stack_and_keeps_project_optional(
    monkeypatch, tmp_path
):
    repo = tmp_path / "repo"
    stack = repo / "tools" / "app"
    stack.mkdir(parents=True)
    (stack / "ciu.compose.yml.j2").write_text("template", encoding="utf-8")
    config = {
        "deploy": {"network_name": "repo-net"},
        "topology": {"services": {"app": {"internal_host": "app.local", "internal_port": 8080}}},
    }
    _prepare_identity_resolver(
        monkeypatch,
        repo,
        profile_config=config,
        entries=({"path": "tools/app", "service": "app"},),
    )
    monkeypatch.setattr(deploy.phases_pkg, "service_shipped", lambda _service: False)
    monkeypatch.setattr(deploy.config_model, "render_stack", lambda *_a, **_kw: {"stack": {}})
    monkeypatch.setattr(deploy.config_model, "deep_merge", lambda base, _stack: base)
    monkeypatch.setattr(deploy.config_model, "validate_stack_shape", lambda _cfg: "app")
    monkeypatch.setattr(deploy.engine, "auto_generate_values", lambda *_a, **_kw: None)
    monkeypatch.setattr(deploy, "_resolve_hostdirs_for_render", lambda *_a, **_kw: None)
    monkeypatch.setattr(deploy.secret_directives, "discover", lambda *_a, **_kw: [])
    monkeypatch.setattr(composefile, "render_compose", lambda *_a, **_kw: "services:\n  app:\n    image: team/app:latest\n    working_dir: /srv/app\n")
    monkeypatch.setattr(deploy.engine, "scope_worktree_compose_images", lambda _root, source: source.replace("latest", "latest-id"))
    monkeypatch.setattr(deploy.engine, "compose_project_name", lambda *_a: (_ for _ in ()).throw(ValueError("no project")))

    document = deploy.resolve_identities(
        repo,
        render_environ={"INSTANCE_ID": "id"},
        image_map_only=True,
        require_closed_image_map=True,
        service="app",
        live=True,
    )
    row = document["resolved"]["identities"]["tools/app"]["app"]
    assert row["image"] == "team/app:latest-id"
    assert row["compose_project"] is None
    assert row["container_name"] is None
    assert row["working_dir"] == "/srv/app"
    assert row["internal_host"] == "app.local"
    assert row["port"] == 8080
    assert row["live"] == {"status": "running"}


def test_shipped_deploy_passes_and_cleans_a_worktree_image_override(
    monkeypatch, tmp_path
):
    repo = tmp_path / "repo"
    stack = repo / "tools" / "app"
    stack.mkdir(parents=True)
    shipped = stack / SHIPPED_COMPOSE
    original = "services:\n  app:\n    image: team/app:latest\n    build: .\n"
    shipped.write_text(original, encoding="utf-8")
    monkeypatch.setenv("DOCKER_NETWORK_INTERNAL", "repo-net")
    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **_kwargs: None)
    monkeypatch.setattr(engine, "resolve_env_root", lambda *_args: repo)
    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *_args: {"ciu": {}, "deploy": {"project_name": "demo", "environment_tag": "test"}})
    monkeypatch.setattr(engine, "configure_logging", lambda _level: None)
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda **_kwargs: None)
    monkeypatch.setattr(engine, "to_physical_path", lambda path, **_kwargs: path)
    monkeypatch.setattr(engine, "_dood_preflight", lambda *_args: None)
    monkeypatch.setattr(
        engine,
        "scope_worktree_compose_images",
        lambda _root, source, **_kwargs: source.replace("latest", "latest-worktree"),
    )
    monkeypatch.setattr(engine, "compose_project_name", lambda *_args: "demo-test-app")
    monkeypatch.setattr(engine, "guard_container_name_owners", lambda *_a, **_kw: None)
    monkeypatch.setattr(engine, "guard_legacy_compose_project", lambda *_a: None)
    monkeypatch.setattr(worktree, "resolve_worktree_cap", lambda _root: None)
    monkeypatch.setattr(engine, "acquire_instance_lease", lambda _root: None)
    monkeypatch.setattr(worktree, "worktree_budget_slot", lambda *_a, **_kw: nullcontext())
    monkeypatch.setattr(engine.composefile, "compose_process_env", lambda *_a, **_kw: {})
    captured = {}

    def execute(file_args, **kwargs):
        captured["args"] = list(file_args)
        captured["kwargs"] = kwargs
        override = stack / file_args[-1]
        captured["path"] = override
        captured["contents"] = override.read_text(encoding="utf-8")
        return {"status": "success", "stdout": "started"}

    monkeypatch.setattr(engine, "execute_docker_compose_with_logs", execute)
    result = engine.run_shipped(stack, define_root=repo)

    assert result["status"] == "success"
    assert captured["args"] == ["-f", SHIPPED_COMPOSE, "-f", captured["args"][-1]]
    assert "team/app:latest-worktree" in captured["contents"]
    assert captured["kwargs"]["project"] == "demo-test-app"
    assert not captured["path"].exists()


def test_shipped_deploy_wraps_invalid_image_override_as_compose_error(
    monkeypatch, tmp_path
):
    repo = tmp_path / "repo"
    stack = repo / "tools" / "app"
    stack.mkdir(parents=True)
    (stack / SHIPPED_COMPOSE).write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **_kwargs: None)
    monkeypatch.setattr(engine, "resolve_env_root", lambda *_args: repo)
    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *_args: {"ciu": {}, "deploy": {}})
    monkeypatch.setattr(engine, "configure_logging", lambda _level: None)
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda **_kwargs: None)
    monkeypatch.setattr(engine, "to_physical_path", lambda path, **_kwargs: path)
    monkeypatch.setattr(engine, "_dood_preflight", lambda *_args: None)
    monkeypatch.setattr(engine, "scope_worktree_compose_images", lambda *_a, **_kw: "services: [")
    with pytest.raises(engine.ComposeError, match=r"\[CIU-117\].*invalid"):
        engine.run_shipped(stack, define_root=repo)


def test_shipped_deploy_cleans_image_override_when_budget_admission_fails(
    monkeypatch, tmp_path
):
    repo = tmp_path / "repo"
    stack = repo / "tools" / "app"
    stack.mkdir(parents=True)
    (stack / SHIPPED_COMPOSE).write_text(
        "services:\n  app:\n    image: team/app:latest\n    build: .\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCKER_NETWORK_INTERNAL", "repo-net")
    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **_kwargs: None)
    monkeypatch.setattr(engine, "resolve_env_root", lambda *_args: repo)
    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *_args: {"ciu": {}, "deploy": {"project_name": "demo", "environment_tag": "test"}})
    monkeypatch.setattr(engine, "configure_logging", lambda _level: None)
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda **_kwargs: None)
    monkeypatch.setattr(engine, "to_physical_path", lambda path, **_kwargs: path)
    monkeypatch.setattr(engine, "_dood_preflight", lambda *_args: None)
    monkeypatch.setattr(engine, "scope_worktree_compose_images", lambda _root, source, **_kw: source.replace("latest", "latest-id"))
    monkeypatch.setattr(engine, "compose_project_name", lambda *_args: "demo-test-app")
    monkeypatch.setattr(engine, "guard_container_name_owners", lambda *_a, **_kw: None)
    monkeypatch.setattr(engine, "guard_legacy_compose_project", lambda *_a: None)
    monkeypatch.setattr(worktree, "resolve_worktree_cap", lambda _root: None)
    monkeypatch.setattr(engine, "acquire_instance_lease", lambda _root: None)
    monkeypatch.setattr(engine.composefile, "compose_process_env", lambda *_a, **_kw: {})

    @contextmanager
    def unavailable_slot(*_args, **_kwargs):
        raise worktree.WorktreeError("admission refused")
        yield

    monkeypatch.setattr(worktree, "worktree_budget_slot", unavailable_slot)
    monkeypatch.setattr(
        engine,
        "execute_docker_compose_with_logs",
        lambda *_a, **_kw: pytest.fail("Compose must not run without admission"),
    )
    with pytest.raises(engine.ComposeError, match="admission refused"):
        engine.run_shipped(stack, define_root=repo)
    assert list(stack.rglob("ciu-compose-worktree-images-*.yml")) == []


def test_resolve_identities_requires_a_closed_primary_compose_image_map(
    monkeypatch, tmp_path
):
    repo = tmp_path / "repo"
    stack = repo / "tools" / "app"
    stack.mkdir(parents=True)
    (stack / SHIPPED_COMPOSE).write_text("include: [parts.yml]\nservices: {}\n", encoding="utf-8")
    _prepare_identity_resolver(
        monkeypatch,
        repo,
        profile_config={"deploy": {"network_name": "net"}},
        entries=({"path": "tools/app", "service": "app"},),
    )
    monkeypatch.setattr(deploy.phases_pkg, "service_shipped", lambda _service: True)
    monkeypatch.setattr(
        iso,
        "compose_may_import_service_definitions",
        lambda _source: (_ for _ in ()).throw(iso.ContainerOwnershipError("bad yaml")),
    )
    with pytest.raises(ValueError, match="cannot read the primary image map"):
        deploy.resolve_identities(repo, require_closed_image_map=True)


def test_resolve_identities_rejects_an_open_primary_image_map(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    stack = repo / "tools" / "app"
    stack.mkdir(parents=True)
    (stack / SHIPPED_COMPOSE).write_text("include: [parts.yml]\nservices: {}\n", encoding="utf-8")
    _prepare_identity_resolver(
        monkeypatch,
        repo,
        profile_config={"deploy": {"network_name": "net"}},
        entries=({"path": "tools/app", "service": "app"},),
    )
    monkeypatch.setattr(deploy.phases_pkg, "service_shipped", lambda _service: True)
    with pytest.raises(ValueError, match="imports Compose service definitions"):
        deploy.resolve_identities(repo, require_closed_image_map=True)


@pytest.mark.parametrize(
    ("scoped", "message"),
    [
        ("services: [", "scoped compose.*invalid"),
        ("services: 3\n", "scoped compose.*no services table"),
        ("services:\n  app: false\n", "service tools/app:app must be a table"),
    ],
)
def test_resolve_identities_revalidates_scoped_compose(
    monkeypatch, tmp_path, scoped, message
):
    repo = tmp_path / "repo"
    stack = repo / "tools" / "app"
    stack.mkdir(parents=True)
    (stack / SHIPPED_COMPOSE).write_text("services:\n  app: {}\n", encoding="utf-8")
    _prepare_identity_resolver(
        monkeypatch,
        repo,
        profile_config={"deploy": {"network_name": "net"}},
        entries=({"path": "tools/app", "service": "app"},),
    )
    monkeypatch.setattr(deploy.phases_pkg, "service_shipped", lambda _service: True)
    monkeypatch.setattr(deploy.engine, "scope_worktree_compose_images", lambda *_a, **_kw: scoped)
    with pytest.raises(ValueError, match=message):
        deploy.resolve_identities(repo)


@pytest.mark.parametrize(
    "document",
    [
        {"resolved": {"identities": []}},
        {"resolved": {"identities": {"stack": []}}},
        {"resolved": {"identities": {"stack": {"app": []}}}},
        {"resolved": {"identities": {"stack": {"app": {"image": 3}}}}},
        {"resolved": {"identities": {"stack": {"app": {"image": " "}}}}},
    ],
)
def test_primary_image_map_rejects_malformed_identity_shapes(monkeypatch, tmp_path, document):
    monkeypatch.setattr(deploy.worktree_pkg, "primary_ciu_root", lambda _root: tmp_path)
    monkeypatch.setattr(deploy, "_render_environment_for_checkout", lambda _root: {})
    monkeypatch.setattr(deploy, "resolve_identities", lambda *_a, **_kw: document)
    with pytest.raises(ValueError, match="primary image map"):
        deploy.resolve_primary_image_references(tmp_path)


def test_primary_image_map_skips_services_without_images(monkeypatch, tmp_path):
    monkeypatch.setattr(deploy.worktree_pkg, "primary_ciu_root", lambda _root: tmp_path)
    monkeypatch.setattr(deploy, "_render_environment_for_checkout", lambda _root: {})
    monkeypatch.setattr(
        deploy,
        "resolve_identities",
        lambda *_a, **_kw: {"resolved": {"identities": {"stack": {"app": {"image": None}}}}},
    )
    assert deploy.resolve_primary_image_references(tmp_path) == set()


def _linked_identity_tree(monkeypatch, tmp_path, *, shared_table=None, record=None, facts=None):
    linked = tmp_path / "linked" / "ciu"
    primary = tmp_path / "primary"
    linked.mkdir(parents=True)
    primary.mkdir()
    entries = [
        worktree.WorktreeInfo(linked.parent, "linked", "a", False),
        worktree.WorktreeInfo(primary, "main", "b", True),
    ]
    monkeypatch.setattr(workspace, "resolve_worktree_git_root", lambda _root: linked.parent)
    monkeypatch.setattr(worktree, "list_worktrees", lambda _root: entries)
    monkeypatch.setattr(worktree, "_primary_worktree_table", lambda _root: shared_table)
    if record is None:
        monkeypatch.setattr(worktree, "read_instance_record", lambda _path: pytest.fail("no record"))
    else:
        (linked / worktree.WORKTREE_INSTANCE_RECORD).write_text("{}\n", encoding="utf-8")
        monkeypatch.setattr(worktree, "read_instance_record", lambda _path: record)
    if isinstance(facts, Exception):
        monkeypatch.setattr(
            workspace_env,
            "read_generated_facts",
            lambda *_a, **_kw: (_ for _ in ()).throw(facts),
        )
    else:
        monkeypatch.setattr(workspace_env, "read_generated_facts", lambda *_a, **_kw: facts or {})
    return linked, primary


def test_linked_identity_resolver_classifies_primary_and_non_git_paths(monkeypatch, tmp_path):
    primary = tmp_path / "primary"
    primary.mkdir()
    monkeypatch.setattr(workspace, "resolve_worktree_git_root", lambda _root: primary)
    monkeypatch.setattr(
        worktree,
        "list_worktrees",
        lambda _root: [worktree.WorktreeInfo(primary, "main", "abc", True)],
    )
    assert worktree.resolve_worktree_image_tag_suffix(primary) is None

    monkeypatch.setattr(
        workspace,
        "resolve_worktree_git_root",
        lambda _root: (_ for _ in ()).throw(workspace.CiuWorkspaceError("no git")),
    )
    non_git = tmp_path / "loose"
    non_git.mkdir()
    original_exists = Path.exists
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(Path, "exists", lambda path: False if path.name == ".git" else original_exists(path))
    monkeypatch.setattr(Path, "is_symlink", lambda path: False if path.name == ".git" else original_is_symlink(path))
    assert worktree.resolve_worktree_image_tag_suffix(non_git) is None


def test_linked_identity_resolver_refuses_uncertain_git_inventory(monkeypatch, tmp_path):
    linked, _primary = _linked_identity_tree(monkeypatch, tmp_path)
    monkeypatch.setattr(worktree, "list_worktrees", lambda _root: (_ for _ in ()).throw(RuntimeError("git unavailable")))
    with pytest.raises(worktree.WorktreeError, match="cannot inspect Git worktree ownership"):
        worktree.resolve_worktree_image_tag_suffix(linked)

    monkeypatch.setattr(worktree, "list_worktrees", lambda _root: [])
    with pytest.raises(worktree.WorktreeError, match="exactly one current and primary"):
        worktree.resolve_worktree_image_tag_suffix(linked)


@pytest.mark.parametrize(
    ("record", "facts", "message"),
    [
        (SimpleNamespace(state="preparing", instance_id="id"), {"instance_id": "id"}, "no ready CIU image identity"),
        (None, workspace_env.WorkspaceEnvError("bad facts"), "could not read worktree image identity"),
        (None, {}, "no generated instance_id"),
        (SimpleNamespace(state="ready", instance_id="other"), {"instance_id": "id"}, "identity disagrees"),
    ],
)
def test_linked_identity_resolver_refuses_invalid_record_or_facts(
    monkeypatch, tmp_path, record, facts, message
):
    linked, _primary = _linked_identity_tree(
        monkeypatch, tmp_path, record=record, facts=facts
    )
    with pytest.raises(worktree.WorktreeError, match=message):
        worktree.resolve_worktree_image_tag_suffix(linked)


def test_linked_identity_policy_allows_explicit_shared_tags_and_rejects_bad_policy(
    monkeypatch, tmp_path
):
    linked, _primary = _linked_identity_tree(monkeypatch, tmp_path, shared_table={"shared_image_tags": True})
    assert worktree.resolve_worktree_image_tag_suffix(linked) is None

    linked2, _primary2 = _linked_identity_tree(monkeypatch, tmp_path / "again", shared_table={})
    assert worktree.resolve_worktree_image_tag_suffix(linked2, allow_shared_tag=True) is None

    linked3, _primary3 = _linked_identity_tree(monkeypatch, tmp_path / "third", shared_table={"shared_image_tags": "yes"})
    with pytest.raises(worktree.WorktreeError, match="must be a boolean"):
        worktree.resolve_worktree_image_tag_suffix(linked3)


def test_linked_identity_detects_git_marker_stat_failure(monkeypatch, tmp_path):
    linked = tmp_path / "linked"
    linked.mkdir()
    monkeypatch.setattr(
        workspace,
        "resolve_worktree_git_root",
        lambda _root: (_ for _ in ()).throw(workspace.CiuWorkspaceError("bad git")),
    )
    original_exists = Path.exists
    original_is_symlink = Path.is_symlink

    def broken_exists(path):
        if path.name == ".git":
            raise OSError("stat denied")
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", broken_exists)
    monkeypatch.setattr(Path, "is_symlink", lambda _path: False)
    with pytest.raises(worktree.WorktreeError, match="could not determine whether"):
        worktree.resolve_worktree_image_tag_suffix(linked)


def test_bake_cli_reports_preview_failures_and_prefixed_errors(monkeypatch, tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(dev, "resolve_repo_root", lambda *_a, **_kw: repo)
    monkeypatch.setattr(worktree, "resolve_worktree_image_tag_suffix", lambda *_a, **_kw: "id")
    monkeypatch.setattr(deploy, "load_global_config", lambda *_a, **_kw: {})
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *_a, **_kw: _result(7, stderr="buildx rejected"),
    )
    assert cli._bake(["app"]) == 7
    assert "buildx rejected" in capsys.readouterr().err

    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *_a, **_kw: _result(stdout="{}"),
    )
    monkeypatch.setattr(
        iso,
        "bake_scoped_tags",
        lambda *_a, **_kw: (_ for _ in ()).throw(iso.ImageIsolationError("[CIU-117] already prefixed")),
    )
    assert cli._bake(["app"]) == 2
    assert capsys.readouterr().err.count("[CIU-117]") == 1


def test_bake_cli_rootless_detection_and_identity_resolution_errors(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(
        dev,
        "resolve_repo_root",
        lambda *_a, **_kw: (_ for _ in ()).throw(ValueError("no CIU root")),
    )
    monkeypatch.setattr(
        cli,
        "_bake_is_linked_checkout_without_ciu_root",
        lambda _path: (_ for _ in ()).throw(ValueError("Git inventory unreadable")),
    )
    assert cli._bake(["app"]) == 2
    assert "Git inventory unreadable" in capsys.readouterr().err

    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(dev, "resolve_repo_root", lambda *_a, **_kw: repo)
    monkeypatch.setattr(
        worktree,
        "resolve_worktree_image_tag_suffix",
        lambda *_a, **_kw: (_ for _ in ()).throw(ValueError("missing image identity")),
    )
    assert cli._bake(["app"]) == 2
    assert "missing image identity" in capsys.readouterr().err


def test_bake_cli_preview_failure_without_stderr_returns_docker_status(
    monkeypatch, tmp_path, capsys
):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(dev, "resolve_repo_root", lambda *_a, **_kw: repo)
    monkeypatch.setattr(worktree, "resolve_worktree_image_tag_suffix", lambda *_a, **_kw: "id")
    monkeypatch.setattr(deploy, "load_global_config", lambda *_a, **_kw: {})
    monkeypatch.setattr(cli.subprocess, "run", lambda *_a, **_kw: _result(9))
    monkeypatch.setattr(cli.subprocess, "call", lambda _argv: pytest.fail("must not build"))
    assert cli._bake(["app"]) == 9
    assert capsys.readouterr().err == ""


def test_bake_cli_skips_primary_map_when_build_plan_has_no_tags(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(dev, "resolve_repo_root", lambda *_a, **_kw: repo)
    monkeypatch.setattr(worktree, "resolve_worktree_image_tag_suffix", lambda *_a, **_kw: "id")
    monkeypatch.setattr(deploy, "load_global_config", lambda *_a, **_kw: {})
    monkeypatch.setattr(cli.subprocess, "run", lambda *_a, **_kw: _result(stdout='{"target":{"meta":{}}}'))
    calls = []
    monkeypatch.setattr(cli.subprocess, "call", lambda argv: calls.append(argv) or 0)
    assert cli._bake(["meta"]) == 0
    assert len(calls) == 1


def test_rootless_bake_helper_reports_git_inventory_failures(monkeypatch, tmp_path):
    start = tmp_path / "loose"
    start.mkdir()
    monkeypatch.setattr(
        workspace,
        "resolve_worktree_git_root",
        lambda _root: (_ for _ in ()).throw(workspace.CiuWorkspaceError("not git")),
    )
    original_exists = Path.exists
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(Path, "exists", lambda path: False if path.name == ".git" else original_exists(path))
    monkeypatch.setattr(Path, "is_symlink", lambda path: False if path.name == ".git" else original_is_symlink(path))
    assert cli._bake_is_linked_checkout_without_ciu_root(start) is False

    monkeypatch.setattr(Path, "exists", original_exists)
    monkeypatch.setattr(Path, "is_symlink", original_is_symlink)
    (start / ".git").write_text("gitdir: missing\n", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot resolve Git ownership"):
        cli._bake_is_linked_checkout_without_ciu_root(start)

    (start / ".git").unlink()
    monkeypatch.setattr(workspace, "resolve_worktree_git_root", lambda _root: start)
    monkeypatch.setattr(worktree, "list_worktrees", lambda _root: (_ for _ in ()).throw(RuntimeError("git inventory")))
    with pytest.raises(ValueError, match="cannot inspect Git worktree ownership"):
        cli._bake_is_linked_checkout_without_ciu_root(start)

    monkeypatch.setattr(worktree, "list_worktrees", lambda _root: [])
    with pytest.raises(ValueError, match="exactly one current and primary"):
        cli._bake_is_linked_checkout_without_ciu_root(start)


def test_rootless_bake_helper_rejects_git_marker_stat_errors(monkeypatch, tmp_path):
    start = tmp_path / "loose"
    start.mkdir()
    monkeypatch.setattr(
        workspace,
        "resolve_worktree_git_root",
        lambda _root: (_ for _ in ()).throw(workspace.CiuWorkspaceError("not git")),
    )
    original_exists = Path.exists

    def broken_exists(path):
        if path.name == ".git":
            raise OSError("stat denied")
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", broken_exists)
    with pytest.raises(ValueError, match="cannot determine Git ownership"):
        cli._bake_is_linked_checkout_without_ciu_root(start)
