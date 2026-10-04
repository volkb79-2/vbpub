"""CLI and single-stack gates for CIU-124/118."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import cli, deploy, engine  # noqa: E402


def test_resolve_cli_requires_json_and_emits_document(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "_resolve_repo_root_deploy", lambda _root: tmp_path)
    seen = {}

    def resolve(root, **kwargs):
        seen.update(root=root, **kwargs)
        return {"schema_version": 1, "resolved": {"identities": {}}}

    monkeypatch.setattr(deploy, "resolve_identities", resolve)
    assert cli._resolve_identities_cli([]) == 2
    assert "requires --json" in capsys.readouterr().err
    assert cli._resolve_identities_cli([
        "--profile", "apps", "--profile", "test",
        "--stack", "tools/api", "--service", "api", "--live", "--json",
    ]) == 0
    assert seen == {
        "root": tmp_path, "stack": "tools/api", "service": "api",
        "profiles": ["apps", "test"], "live": True,
    }
    assert json.loads(capsys.readouterr().out)["schema_version"] == 1


@pytest.mark.parametrize(("error", "expected"), [(RuntimeError("docker"), 1), (ValueError("bad"), 2), (OSError("read"), 2)])
def test_resolve_cli_maps_expected_errors(monkeypatch, tmp_path, capsys, error, expected):
    monkeypatch.setattr(cli, "_resolve_repo_root_deploy", lambda _root: tmp_path)
    monkeypatch.setattr(deploy, "resolve_identities", lambda *_a, **_kw: (_ for _ in ()).throw(error))
    assert cli._resolve_identities_cli(["--json"]) == expected
    assert str(error) in capsys.readouterr().err


def test_exec_cli_requires_separator_selector_and_command(capsys):
    assert cli._exec_service_cli(["tools/api", "true"]) == 2
    assert cli._exec_service_cli(["--", "true"]) == 2
    assert cli._exec_service_cli(["tools/api", "--"]) == 2
    assert "requires" in capsys.readouterr().err


def test_exec_cli_preserves_argv_and_child_status(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_resolve_repo_root_deploy", lambda _root: tmp_path)
    seen = []
    monkeypatch.setattr(
        deploy, "exec_service",
        lambda root, selector, argv, **kwargs: seen.append((root, selector, argv, kwargs)) or 7,
    )
    assert cli._exec_service_cli([
        "--root-folder", str(tmp_path), "--profile", "apps",
        "tools/api:api", "--", "python", "--help",
    ]) == 7
    assert seen == [(tmp_path, "tools/api:api", ["python", "--help"], {"profiles": ["apps"]})]


@pytest.mark.parametrize(("error", "expected"), [(RuntimeError("docker"), 1), (ValueError("bad"), 2), (OSError("exec"), 2)])
def test_exec_cli_maps_expected_errors(monkeypatch, tmp_path, capsys, error, expected):
    monkeypatch.setattr(cli, "_resolve_repo_root_deploy", lambda _root: tmp_path)
    monkeypatch.setattr(
        deploy,
        "exec_service",
        lambda *_a, **_kw: (_ for _ in ()).throw(error),
    )
    assert cli._exec_service_cli(["tools/api", "--", "true"]) == expected
    assert str(error) in capsys.readouterr().err


@pytest.mark.parametrize(("verb", "rest"), [("resolve", ["--json"]), ("exec", ["tools/api", "--", "true"])])
def test_main_dispatches_resolve_and_exec(monkeypatch, verb, rest):
    called = []
    target = "_resolve_identities_cli" if verb == "resolve" else "_exec_service_cli"
    monkeypatch.setattr(cli, target, lambda argv: called.append(argv) or 6)
    monkeypatch.setattr(sys, "argv", ["ciu", verb, *rest])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 6
    assert called == [rest]


def test_down_dir_dispatches_exact_stack_and_profiles(monkeypatch, tmp_path):
    called = []
    monkeypatch.setattr(cli, "_resolve_repo_root_deploy", lambda _root: tmp_path)
    monkeypatch.setattr(
        deploy, "stop_stack",
        lambda root, selector, *, profiles: called.append((root, selector, profiles)) or 0,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["ciu", "down", "--dir", "tools/test-runner", "--profile", "test",
         "--root-folder", str(tmp_path)],
    )
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    assert called == [(tmp_path, "tools/test-runner", ["test"])]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ValueError("outside root"), 2),
        (OSError("docker unavailable"), 1),
        (RuntimeError("docker stop failed"), 1),
    ],
)
def test_down_dir_maps_stop_stack_errors(monkeypatch, tmp_path, capsys, error, expected):
    monkeypatch.setattr(cli, "_resolve_repo_root_deploy", lambda _root: tmp_path)
    monkeypatch.setattr(
        deploy,
        "stop_stack",
        lambda *_a, **_kw: (_ for _ in ()).throw(error),
    )
    monkeypatch.setattr(sys, "argv", ["ciu", "down", "--dir", "tools/test-runner"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == expected
    assert str(error) in capsys.readouterr().err


def test_clean_identity_cli_routes_and_rejects_mixed_options(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "_resolve_repo_root_deploy", lambda _root: tmp_path)
    calls = []
    monkeypatch.setattr(
        deploy, "action_clean_identity",
        lambda root, identity, *, yes: calls.append((root, identity, yes)) or 0,
    )
    monkeypatch.setattr(sys, "argv", ["ciu", "clean", "--identity", "ab12cd", "-y"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    assert calls == [(tmp_path, "ab12cd", True)]

    monkeypatch.setattr(sys, "argv", ["ciu", "clean", "--identity", "ab12cd", "--profile", "x"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
    assert "cannot be combined" in capsys.readouterr().err


def test_shipped_single_stack_healthcheck_success_and_failure_paths(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "run_shipped", lambda **_kw: {"status": "success"})
    monkeypatch.setattr(engine, "resolve_env_root", lambda *_a, **_kw: tmp_path)
    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *_a, **_kw: {"deploy": {}})
    seen = []
    monkeypatch.setattr(
        engine, "_run_single_stack_healthcheck",
        lambda root, define, config, *, shipped: seen.append((root, define, config, shipped)) or 9,
    )
    assert engine.main(["--dir", str(tmp_path), "--shipped", "--deploy", "--healthcheck"]) == 9
    assert seen == [(tmp_path, None, {"deploy": {}}, True)]

    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("bad config")))
    assert engine.main(["--dir", str(tmp_path), "--shipped", "--deploy", "--healthcheck"]) == 1

    monkeypatch.setattr(engine.config_model, "render_global_chain", lambda *_a, **_kw: (_ for _ in ()).throw(SystemExit(12)))
    with pytest.raises(SystemExit) as exc:
        engine.main(["--dir", str(tmp_path), "--shipped", "--deploy", "--healthcheck"])
    assert exc.value.code == 12


def test_regular_single_stack_healthcheck_system_exit_propagates(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "main_execution", lambda **_kw: {"status": "success", "config": {}})
    monkeypatch.setattr(engine, "_run_single_stack_healthcheck", lambda *_a, **_kw: (_ for _ in ()).throw(SystemExit(13)))
    with pytest.raises(SystemExit) as exc:
        engine.main(["--dir", str(tmp_path), "--deploy", "--healthcheck"])
    assert exc.value.code == 13


def test_regular_single_stack_healthcheck_runtime_error_maps_to_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(engine, "main_execution", lambda **_kw: {"status": "success", "config": {}})
    monkeypatch.setattr(engine, "_run_single_stack_healthcheck", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("probe failed")))
    assert engine.main(["--dir", str(tmp_path), "--deploy", "--healthcheck"]) == 1
