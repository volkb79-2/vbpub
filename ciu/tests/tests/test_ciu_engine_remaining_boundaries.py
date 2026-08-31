"""Public engine command boundaries that do not require a Docker daemon.

The assertions deliberately sit at CIU's command boundaries: callers see a
compose invocation, secret table, or generated environment result.  Docker and
configuration rendering are replaced only where those external systems begin.
"""

from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine  # noqa: E402


def _write_identity_facts(root, repo_name, instance_id):
    """CIU-75: the shipped identity project is derived from the checkout's own
    generated `[ciu.instance.generated]` overlay table, not its `ciu.env`."""
    from ciu.workspace_env import GENERATED_FACTS_KEYS, upsert_generated_facts

    facts = {key: "" for key in GENERATED_FACTS_KEYS}
    facts["repo_name"] = repo_name
    facts["instance_id"] = instance_id
    upsert_generated_facts(root, facts)



def test_shipped_execution_names_identity_project_without_deploy_tags(
    tmp_path, monkeypatch, capsys
):
    """S8.5/CIU-46 cutover: a minimal shipped file starts without native deploy
    metadata, under the WORKSPACE-IDENTITY compose project.

    Absent ``deploy.project_name/environment_tag`` the shipped fallback
    derives ``{repo_name}-{instance_id}-{stack}`` from THIS checkout's
    generated overlay facts (read by exact path) and passes it explicitly as
    ``-p`` — the
    same function clean's S6.4a enumeration calls, so up and clean name the
    project identically by construction. The withdrawn basename fallback let
    docker derive a name identical for every checkout of the repo: it both
    collided across instances and escaped every teardown pass.
    """
    stack = tmp_path / "vendor-stack"  # round-trips normalization (CIU-46 guard)
    stack.mkdir()
    (stack / "vendor.yml").write_text("services: {legacy: {image: alpine:3}}\n")
    _write_identity_facts(tmp_path, "dstdns", "abc123")
    calls: list[dict] = []
    guards: list[tuple[Path, str]] = []

    # S16.3/CIU-24: the budget-slot wiring reads DOCKER_NETWORK_INTERNAL
    # directly from os.environ at the (now-reached) real compose-up step;
    # a real ciu.env always carries it, but bootstrap_workspace_env is
    # stubbed to a no-op below, so this fixture must supply it itself.
    monkeypatch.setenv("DOCKER_NETWORK_INTERNAL", "remaining-boundaries-net")
    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **kwargs: None)
    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *args: {"ciu": {}})
    monkeypatch.setattr(engine, "configure_logging", lambda *args: None)
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda **kwargs: None)
    monkeypatch.setattr(engine, "_dood_preflight", lambda *args: None)
    monkeypatch.setattr(engine, "to_physical_path", lambda path, **kwargs: path)
    monkeypatch.setattr(engine.composefile, "compose_process_env", lambda *args, **kwargs: {"X": "1"})
    monkeypatch.setattr(engine, "compose_project_name", lambda *args: (_ for _ in ()).throw(ValueError("no deploy")))
    monkeypatch.setattr(
        engine, "guard_legacy_compose_project",
        lambda stack_dir, expected: guards.append((stack_dir, expected)),
    )

    def _compose(file_args, **kwargs):
        calls.append({"file_args": file_args, **kwargs})
        return {"status": "success", "stdout": "identity started\n"}

    monkeypatch.setattr(engine, "execute_docker_compose_with_logs", _compose)

    result = engine.run_shipped(stack, compose_file="vendor.yml", define_root=tmp_path)

    assert result == {
        "status": "success", "dry_run": False, "shipped": True,
        "stdout": "identity started\n",
    }
    assert calls == [{
        "file_args": ["-f", "vendor.yml"], "cwd": stack.resolve(),
        "env": {"X": "1"}, "project": "dstdns-abc123-vendor-stack",
        "repo_root": tmp_path.resolve(),
    }]
    assert guards == [(stack.resolve(), "dstdns-abc123-vendor-stack")]
    assert (
        "workspace-identity compose project 'dstdns-abc123-vendor-stack'"
        in capsys.readouterr().out
    )


def test_shipped_compose_error_is_mapped_by_public_cli_without_success_output(
    tmp_path, monkeypatch, capsys
):
    """S10.3: a failed shipped compose returns exit 1, not a green CLI result."""
    monkeypatch.setattr(
        engine, "run_shipped",
        lambda **kwargs: {"status": "interrupted", "stdout": "partial output\n"},
    )

    assert engine.main(["--shipped", "-d", str(tmp_path)]) == 1
    assert capsys.readouterr().out == ""


def test_secrets_list_cli_prints_metadata_without_secret_values(tmp_path, monkeypatch, capsys):
    """S4.25: public list emits lifecycle metadata but never a materialized value."""
    spec = SimpleNamespace(name="db_password")
    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **kwargs: None)
    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *args: {})
    monkeypatch.setattr(engine.config_model, "render_stack", lambda *args, **kwargs: {"demo": {}})
    monkeypatch.setattr(engine.config_model, "deep_merge", lambda *args: {"demo": {}})
    monkeypatch.setattr(engine.config_model, "validate_stack_shape", lambda *args: "demo")
    monkeypatch.setattr(engine.secret_directives, "discover", lambda *args: [spec])
    monkeypatch.setattr(
        engine.secret_materialize, "list_secrets",
        lambda *args: [{
            "name": "db_password", "kind": "ASK_EXTERNAL", "locator": "DB_PASSWORD",
            "store": "environment", "exists": True, "value": "never-print-this",
        }],
    )

    assert engine.main(["secrets", "list", "-d", str(tmp_path), "--define-root", str(tmp_path)]) == 0

    output = capsys.readouterr().out
    assert "NAME" in output and "db_password" in output and "ASK_EXTERNAL" in output
    assert "never-print-this" not in output


def test_unknown_secret_reset_fails_before_confirmation_or_deletion(tmp_path, monkeypatch, capsys):
    """S4.25: a typo cannot prompt for or delete an unrelated secret store."""
    spec = SimpleNamespace(name="real_secret")
    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **kwargs: None)
    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *args: {})
    monkeypatch.setattr(engine.config_model, "render_stack", lambda *args, **kwargs: {"demo": {}})
    monkeypatch.setattr(engine.config_model, "deep_merge", lambda *args: {"demo": {}})
    monkeypatch.setattr(engine.config_model, "validate_stack_shape", lambda *args: "demo")
    monkeypatch.setattr(engine.secret_directives, "discover", lambda *args: [spec])
    monkeypatch.setattr("builtins.input", lambda *args: pytest.fail("unknown secret must not prompt"))
    monkeypatch.setattr(
        engine.secret_materialize, "reset_secrets", lambda *args, **kwargs: pytest.fail("unknown secret must not delete")
    )

    assert engine.main(["secrets", "reset", "--name", "typo", "-d", str(tmp_path)]) == 2
    assert "no such secret 'typo'" in capsys.readouterr().out


def test_generate_env_fast_path_initializes_once_and_skips_stack_execution(
    tmp_path, monkeypatch, capsys
):
    """S2.8: ``--generate-env`` creates only ciu.env and does not deploy a stack."""
    generated = tmp_path / "ciu.env"
    calls: list[Path] = []
    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "resolve_env_root", lambda **kwargs: tmp_path)
    monkeypatch.setattr(engine, "bootstrap_env_init", lambda root: calls.append(root) or generated)
    monkeypatch.setattr(engine, "main_execution", lambda **kwargs: pytest.fail("fast path must not execute a stack"))

    assert engine.main(["--generate-env", "-d", str(tmp_path)]) == 0
    assert calls == [tmp_path]
    assert f"Generated {generated}" in capsys.readouterr().out


def test_generate_env_failure_has_environment_exit_code_and_skips_stack_execution(
    tmp_path, monkeypatch, capsys
):
    """S10.3: bootstrap errors are exit 3 and cannot fall through to deployment."""
    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(
        engine, "resolve_env_root", lambda **kwargs: (_ for _ in ()).throw(engine.WorkspaceEnvError("bad environment"))
    )
    monkeypatch.setattr(engine, "main_execution", lambda **kwargs: pytest.fail("failed bootstrap must not execute a stack"))

    assert engine.main(["--generate-env", "-d", str(tmp_path)]) == 3
    assert "bad environment" in capsys.readouterr().out


def test_shipped_prefers_checkout_own_env_root_over_ambient_repo_root(
    tmp_path, monkeypatch, capsys
):
    """CIU-46 review fix: a shell carrying ANOTHER checkout's REPO_ROOT must
    not make a config-less shipped stack name its compose project with that
    checkout's identity record. The walk-up finds THIS stack's env root, an
    INFO line names the winner."""
    nested = tmp_path / "main-checkout" / ".worktrees" / "wt2"
    stack = nested / "vendor" / "vault"
    stack.mkdir(parents=True)
    (stack / "docker-compose.yml").write_text("services: {}\n")
    # The linked worktree is its OWN checkout: nearest marker + own record.
    (nested / "ciu.global.defaults.toml.j2").write_text("[ciu]\n", encoding="utf-8")
    _write_identity_facts(nested, "wt2repo", "beef42")
    # The MAIN checkout carries a different identity that must NOT be used.
    (tmp_path / "main-checkout" / "ciu.global.defaults.toml.j2").write_text(
        "[ciu]\n", encoding="utf-8"
    )
    _write_identity_facts(tmp_path / "main-checkout", "mainrepo", "98535c")

    monkeypatch.delenv("REPO_ROOT", raising=False)
    monkeypatch.setenv("REPO_ROOT", str(tmp_path / "main-checkout"))  # the contamination
    monkeypatch.setenv("DOCKER_NETWORK_INTERNAL", "net")
    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **kwargs: None)
    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *args: {"ciu": {}})
    monkeypatch.setattr(engine, "configure_logging", lambda *args: None)
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda **kwargs: None)
    monkeypatch.setattr(engine, "_dood_preflight", lambda *args: None)
    monkeypatch.setattr(engine, "to_physical_path", lambda path, **kwargs: path)
    monkeypatch.setattr(
        engine, "compose_project_name", lambda *a: (_ for _ in ()).throw(ValueError("no deploy"))
    )
    monkeypatch.setattr(engine, "guard_legacy_compose_project", lambda *a: None)

    seen = {}

    def _compose(file_args, **kwargs):
        seen["project"] = kwargs.get("project")
        return {"status": "success", "stdout": ""}

    monkeypatch.setattr(engine, "execute_docker_compose_with_logs", _compose)

    result = engine.run_shipped(stack, define_root=None)

    assert result["status"] == "success"
    assert seen["project"] == "wt2repo-beef42-vault"
    out = capsys.readouterr().out
    assert "ambient REPO_ROOT points at" in out
    assert "using the checkout's own record" in out
    import os

    assert os.environ["REPO_ROOT"] == str(nested.resolve())
    assert "98535c" not in seen["project"]


def test_shipped_marker_found_with_matching_ambient_is_silent(tmp_path, monkeypatch, capsys):
    """Marker found and ambient REPO_ROOT already agrees → no INFO line, same
    identity project (the guard's quiet equality arc)."""
    nested = tmp_path / "checkout"
    stack = nested / "vendor" / "vault"
    stack.mkdir(parents=True)
    (stack / "docker-compose.yml").write_text("services: {}\n")
    (nested / "ciu.global.defaults.toml.j2").write_text("[ciu]\n", encoding="utf-8")
    _write_identity_facts(nested, "wt2repo", "beef42")
    monkeypatch.setenv("REPO_ROOT", str(nested))
    monkeypatch.setenv("DOCKER_NETWORK_INTERNAL", "net")
    monkeypatch.setattr(engine, "check_runtime_dependencies", lambda: None)
    monkeypatch.setattr(engine, "bootstrap_workspace_env", lambda **kwargs: None)
    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *args: {"ciu": {}})
    monkeypatch.setattr(engine, "configure_logging", lambda *args: None)
    monkeypatch.setattr(engine, "ensure_workspace_network", lambda **kwargs: None)
    monkeypatch.setattr(engine, "_dood_preflight", lambda *args: None)
    monkeypatch.setattr(engine, "to_physical_path", lambda path, **kwargs: path)
    monkeypatch.setattr(
        engine, "compose_project_name", lambda *a: (_ for _ in ()).throw(ValueError("no deploy"))
    )
    monkeypatch.setattr(engine, "guard_legacy_compose_project", lambda *a: None)
    seen = {}

    def _compose(file_args, **kwargs):
        seen["project"] = kwargs.get("project")
        return {"status": "success", "stdout": ""}

    monkeypatch.setattr(engine, "execute_docker_compose_with_logs", _compose)

    assert engine.run_shipped(stack).get("status") == "success"
    assert seen["project"] == "wt2repo-beef42-vault"
    assert "ambient REPO_ROOT points at" not in capsys.readouterr().out
