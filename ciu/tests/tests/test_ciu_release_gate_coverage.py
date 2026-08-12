"""Release-gate contracts for the CIU-16..24 public safety surfaces.

These are intentionally boundary tests, not line probes: each case exercises a
failure mode that must remain explicit (no guessed physical path, no malformed
provenance enumeration, and no swallowed worktree/KSM dispatcher error).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import cli, composefile, deploy, governance, ksm  # noqa: E402
from ciu.composefile import ConfigFileMount  # noqa: E402


def test_ksm_verb_builds_against_the_resolved_physical_root(tmp_path, monkeypatch, capsys):
    physical = tmp_path / "physical"
    built = tmp_path / ".ciu" / "ksm" / "shim.so"
    built.parent.mkdir(parents=True)
    built.write_bytes(b"shim")
    from ciu import dev, workspace_env

    monkeypatch.setattr(dev, "resolve_repo_root", lambda *_args: tmp_path)
    monkeypatch.setattr(workspace_env, "_detect_physical_repo_root", lambda _root: physical)
    seen: dict[str, object] = {}

    def fake_build(repo_root, physical_root, *, force):
        seen.update(repo_root=repo_root, physical_root=physical_root, force=force)
        return built

    monkeypatch.setattr(ksm, "build", fake_build)

    assert cli._ksm(["build", "--force"]) == 0
    assert seen == {"repo_root": tmp_path, "physical_root": physical, "force": True}
    assert capsys.readouterr().out.strip() == str(built)


def test_ksm_verb_turns_build_refusal_into_a_cli_error(tmp_path, monkeypatch, capsys):
    from ciu import dev, workspace_env

    monkeypatch.setattr(dev, "resolve_repo_root", lambda *_args: tmp_path)
    monkeypatch.setattr(workspace_env, "_detect_physical_repo_root", lambda _root: tmp_path)
    monkeypatch.setattr(ksm, "build", lambda *_args, **_kwargs: (_ for _ in ()).throw(ksm.KsmBuildError("bad shim")))

    assert cli._ksm(["build"]) == 2
    assert "bad shim" in capsys.readouterr().err


def test_main_rejects_conflicting_ksm_overrides(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["ciu", "ksm", "--ksm", "--no-ksm"])

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 2
    assert "mutually exclusive" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("flag", "expected"),
    [("--ksm", governance.BUILTIN_KSM), ("--no-ksm", "off")],
)
def test_main_passes_one_ksm_override_to_the_chosen_verb(monkeypatch, flag, expected):
    seen: list[list[str]] = []
    monkeypatch.setattr(sys, "argv", ["ciu", "ksm", flag])
    monkeypatch.setattr(cli, "_ksm", lambda rest: seen.append(rest) or 0)

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 0
    assert seen == [[]]
    assert cli.os.environ[governance.KSM_ENV_VAR] == expected


@pytest.mark.parametrize(
    ("verb", "handler"),
    [("ksm", "_ksm"), ("worktree", "_worktree"), ("provenance", "_provenance")],
)
def test_main_dispatches_each_new_public_verb(monkeypatch, verb, handler):
    monkeypatch.setattr(sys, "argv", ["ciu", verb])
    monkeypatch.setattr(cli, handler, lambda rest: 7)

    with pytest.raises(SystemExit) as exc:
        cli.main()

    assert exc.value.code == 7


def test_builtin_ksm_overlay_builds_once_then_uses_a_daemon_visible_path(tmp_path, monkeypatch):
    stack = tmp_path / "stack"
    stack.mkdir()
    built = stack / ".ciu" / "ksm" / "shim.so"
    built.parent.mkdir(parents=True)
    built.write_bytes(b"shim")
    monkeypatch.delenv(governance.KSM_ENV_VAR, raising=False)
    seen: dict[str, Path] = {}

    def fake_build(repo_root, physical_root):
        seen.update(repo_root=repo_root, physical_root=physical_root)
        return built

    monkeypatch.setattr(ksm, "build", fake_build)
    overlay = composefile.generate_overlay(
        stack, {}, [], compose_yaml_text="services:\n  app:\n    image: example/app\n",
        governance={"enabled": True, "ksm_optin": "builtin"},
        repo_root=tmp_path, physical_root=tmp_path,
    )

    assert overlay is not None
    assert seen == {"repo_root": tmp_path, "physical_root": tmp_path}
    assert str(built) in overlay.read_text(encoding="utf-8")


def test_builtin_ksm_refuses_without_a_physical_root(tmp_path, monkeypatch):
    stack = tmp_path / "stack"
    stack.mkdir()
    monkeypatch.delenv("PHYSICAL_REPO_ROOT", raising=False)
    monkeypatch.delenv(governance.KSM_ENV_VAR, raising=False)

    with pytest.raises(ValueError, match="PHYSICAL_REPO_ROOT is not set"):
        composefile.generate_overlay(
            stack, {}, [], compose_yaml_text="services:\n  app:\n    image: example/app\n",
            governance={"enabled": True, "ksm_optin": "builtin"}, repo_root=tmp_path,
        )


def test_builtin_ksm_derives_its_physical_root_from_workspace_env(tmp_path, monkeypatch):
    stack = tmp_path / "stack"
    stack.mkdir()
    physical = tmp_path / "host-visible"
    built = stack / ".ciu" / "ksm" / "shim.so"
    built.parent.mkdir(parents=True)
    built.write_bytes(b"shim")
    monkeypatch.setenv("PHYSICAL_REPO_ROOT", str(physical))
    monkeypatch.delenv(governance.KSM_ENV_VAR, raising=False)
    seen: dict[str, Path] = {}
    monkeypatch.setattr(
        ksm, "build",
        lambda repo_root, physical_root: seen.update(repo_root=repo_root, physical_root=physical_root) or built,
    )

    composefile.generate_overlay(
        stack, {}, [], compose_yaml_text="services:\n  app:\n    image: example/app\n",
        governance={"enabled": True, "ksm_optin": "builtin"}, repo_root=tmp_path,
    )

    assert seen == {"repo_root": tmp_path, "physical_root": physical}


def test_builtin_ksm_build_error_becomes_a_render_refusal(tmp_path, monkeypatch):
    stack = tmp_path / "stack"
    stack.mkdir()
    monkeypatch.delenv(governance.KSM_ENV_VAR, raising=False)
    monkeypatch.setattr(ksm, "build", lambda *_args: (_ for _ in ()).throw(ksm.KsmBuildError("compiler failed")))

    with pytest.raises(ValueError, match="compiler failed"):
        composefile.generate_overlay(
            stack, {}, [], compose_yaml_text="services:\n  app:\n    image: example/app\n",
            governance={"enabled": True, "ksm_optin": "builtin"},
            repo_root=tmp_path, physical_root=tmp_path,
        )


def test_configfile_overlay_refuses_a_missing_staging_directory(tmp_path):
    stack = tmp_path / "stack"
    stack.mkdir()
    mount = ConfigFileMount(
        service="app", name="settings", rendered_path=stack / ".ciu" / "rendered" / "app" / "settings",
        target="/etc/app/settings.toml",
    )

    with pytest.raises(ValueError, match="staging directory does not exist"):
        composefile.generate_overlay(
            stack, {}, [mount], compose_yaml_text="services:\n  app:\n    image: example/app\n",
        )


def test_provenance_enumeration_ignores_malformed_docker_rows(monkeypatch):
    monkeypatch.setattr(
        deploy.procutil, "docker",
        lambda cmd, **_kwargs: subprocess.CompletedProcess(
            cmd, 0, "malformed\napp\texample/app\napp\texample/app\n", ""
        ),
    )

    assert deploy._running_containers("app-") == []


def test_provenance_label_lookup_treats_docker_oserror_as_unknown(monkeypatch):
    monkeypatch.setattr(
        deploy.procutil, "docker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("socket unavailable")),
    )

    assert deploy._image_revision_label("example/app") == ""


def test_memory_profile_requires_a_table_and_ignores_non_table_services():
    with pytest.raises(ValueError, match="must be a table"):
        governance.resolve_service_ksm({"memory_profile": "invalid"}, "app")

    assert governance.resolve_service_ksm(
        {"memory_profile": {"services": "invalid", "default": {"ksm": "off"}}}, "app"
    ) == "off"
