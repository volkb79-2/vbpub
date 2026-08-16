"""Deep behavioural coverage for strict config and controller CLI contracts."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmru import config
from cmru.controller import cli as controller_cli


def test_installer_config_validates_and_preserves_explicit_values():
    parsed = config._parse_installer(
        "demo",
        {
            "install_dir_system": "/opt/demo",
            "install_dir_user": "~/.local/demo",
            "asset_suffix": ".tar.zst",
            "entrypoint": "demo",
            "required_commands": ["tar"],
            "preserve": ["config.toml"],
            "manifest_name": "release.json",
            "signature_name": "release.minisig",
            "wheels": [{"path": "dist/demo.whl", "distribution": "demo"}],
        },
    )
    assert parsed.install_dir_system == "/opt/demo"
    assert parsed.asset_suffix == ".tar.zst"
    assert parsed.wheels[0].distribution == "demo"


@pytest.mark.parametrize(
    "raw, message",
    [
        ({"install_dir_user": "/x"}, "install_dir_system"),
        ({"install_dir_system": "/x"}, "install_dir_user"),
        ({"install_dir_system": "/x", "install_dir_user": "/y", "required_commands": "tar"}, "required_commands"),
        ({"install_dir_system": "/x", "install_dir_user": "/y", "preserve": "config"}, "preserve"),
        ({"install_dir_system": "/x", "install_dir_user": "/y", "wheels": ["bad"]}, "wheels[0]"),
        ({"install_dir_system": "/x", "install_dir_user": "/y", "wheels": [{}]}, "path"),
    ],
)
def test_installer_config_rejects_missing_or_ambiguous_fields(raw, message, capsys):
    with pytest.raises(SystemExit) as exc:
        config._parse_installer("demo", raw)
    assert exc.value.code == 2
    assert message in capsys.readouterr().out


def test_installer_config_rejects_unknown_wheel_and_installer_keys(capsys):
    base = {"install_dir_system": "/x", "install_dir_user": "/y"}
    with pytest.raises(SystemExit):
        config._parse_installer("demo", {**base, "unknown": True})
    assert "unknown keys" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        config._parse_installer("demo", {**base, "wheels": [{"path": "x", "distribution": "d", "extra": 1}]})
    assert "unknown keys" in capsys.readouterr().out


def test_variants_parse_filename_safe_unique_names():
    variants = config._parse_variants(
        "demo", {"variants": [{"name": "amd64-debug", "build_arg": "linux/amd64", "label": "Debug"}, {"name": "arm64"}]}
    )
    assert [(item.name, item.build_arg, item.label) for item in variants] == [
        ("amd64-debug", "linux/amd64", "Debug"), ("arm64", None, None)
    ]


@pytest.mark.parametrize(
    "items, expected",
    [(["bad"], "must be a table"), ([{"name": "bad/name"}], "invalid"),
     ([{"name": "same"}, {"name": "same"}], "duplicate"), ([{"name": "x", "extra": 1}], "unknown keys")],
)
def test_variants_reject_non_contract_entries(items, expected, capsys):
    with pytest.raises(SystemExit) as exc:
        config._parse_variants("demo", {"variants": items})
    assert exc.value.code == 2
    assert expected in capsys.readouterr().out


def test_project_document_helpers_fail_closed_for_paths_and_targets(tmp_path, capsys):
    with pytest.raises(SystemExit):
        config._read_toml(tmp_path / "wrong-name", "cmru.toml")
    assert "expected cmru.toml" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        config._read_toml(tmp_path / "cmru.toml", "cmru.toml")
    assert "not found" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        config._targets({"host": "github", "registry": [""]})
    assert "non-empty strings" in capsys.readouterr().out


def test_secret_overlay_and_github_validation_refuse_wrong_shapes(capsys):
    with pytest.raises(SystemExit):
        config._github({"owner": "o", "repo": "r", "owner_type": "team"})
    assert "owner_type" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        config._secret_token({"token": ""}, "secret.github")
    assert "non-empty" in capsys.readouterr().out


def test_validate_runner_steps_checks_login_and_command_contract(capsys):
    valid = {"run-tests": {"commands": [{"label": "gate", "argv": ["pytest"], "cwd": "."}], "quiet": True,
                            "login": {"registry": "ghcr.io", "username_env": "USER", "token_env": "TOKEN", "required": False}}}
    assert config._validate_runner_steps(valid)["run-tests"]["quiet"] is True
    broken = {"run-tests": {"commands": [{"label": "gate", "argv": ["pytest"], "cwd": "."}], "quiet": True,
                             "login": {"registry": "ghcr.io", "username_env": "USER", "token_env": "TOKEN", "required": "yes"}}}
    with pytest.raises(SystemExit):
        config._validate_runner_steps(broken)
    assert "required" in capsys.readouterr().out


class _Engine:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def _call(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        if self.error:
            raise RuntimeError(self.error)
        return self.result

    def publish(self, plan): return self._call("publish", plan)
    def approve(self, plan): return self._call("approve", plan)
    def hold(self, plan): return self._call("hold", plan)
    def status(self, plan): return self._call("status", plan)
    def rollback(self, plan, **kwargs): return self._call("rollback", plan, **kwargs)


def _args(**overrides):
    values = {"plan": "plan.toml", "landscape": "prod", "consul_addr": None,
              "token": None, "generation_base": 3, "dry_run": True,
              "to_tag": "demo-v1", "generation": 9}
    values.update(overrides)
    return SimpleNamespace(**values)


def test_controller_command_families_dispatch_success_and_report_engine_failures(monkeypatch, capsys, tmp_path):
    plan_path = tmp_path / "plan.toml"
    plan_path.write_text("landscape = 'prod'\n", encoding="utf-8")
    plan = SimpleNamespace(landscape="prod")
    monkeypatch.setattr("cmru.controller.planner.load_plan", lambda _path: plan)
    engine = _Engine(result={"nodes": []})
    monkeypatch.setattr(controller_cli, "_build_engine", lambda *_args: engine)
    assert controller_cli.cmd_publish(_args(plan=str(plan_path))) == 0
    assert controller_cli.cmd_rollback(_args(plan=str(plan_path))) == 0
    assert controller_cli.cmd_status(_args(plan=str(plan_path))) == 0
    assert [call[0] for call in engine.calls] == ["publish", "rollback", "status"]
    failing = _Engine(error="backend down")
    monkeypatch.setattr(controller_cli, "_build_engine", lambda *_args: failing)
    assert controller_cli.cmd_approve(_args()) == 1
    assert controller_cli.cmd_hold(_args()) == 1
    assert controller_cli.cmd_rollback(_args(plan=str(plan_path))) == 1
    assert "backend down" in capsys.readouterr().err


def test_controller_plan_and_required_argument_refusals(capsys, tmp_path):
    missing = _args(plan=str(tmp_path / "missing.toml"))
    assert controller_cli.cmd_publish(missing) == 2
    assert controller_cli.cmd_rollback(missing) == 2
    assert controller_cli.cmd_approve(_args(plan="")) == 2
    assert controller_cli.cmd_hold(_args(plan="")) == 2
    assert controller_cli.cmd_status(_args(plan=None, landscape="")) == 2
    assert "required" in capsys.readouterr().err


def test_controller_status_catalog_is_a_real_json_boundary(monkeypatch, capsys):
    class Backend:
        def _get(self, _path):
            return 200, json.dumps([{"Node": "n1", "ServiceTags": ["canary"]}]), {}
    monkeypatch.setattr(controller_cli, "_build_backend", lambda _args: Backend())
    assert controller_cli.cmd_status(_args(plan=None, landscape="prod")) == 0
    output = capsys.readouterr().out
    assert "n1" in output and "canary" in output


def test_controller_status_catalog_malformed_and_http_error_are_nonfatal_warnings(monkeypatch, capsys):
    class Backend:
        def __init__(self, status, body): self.status, self.body = status, body
        def _get(self, _path): return self.status, self.body, {}
    monkeypatch.setattr(controller_cli, "_build_backend", lambda _args: Backend(200, "not-json"))
    assert controller_cli.cmd_status(_args(plan=None, landscape="prod")) == 0
    assert "Could not parse" in capsys.readouterr().out
    monkeypatch.setattr(controller_cli, "_build_backend", lambda _args: Backend(503, "busy"))
    assert controller_cli.cmd_status(_args(plan=None, landscape="prod")) == 0
    assert "HTTP 503" in capsys.readouterr().out


def test_controller_parser_exposes_all_global_and_subcommand_contracts():
    parser = controller_cli._build_parser()
    args = parser.parse_args(["--landscape", "prod", "--dry-run", "publish", "--plan", "p.toml", "--generation-base", "7"])
    assert args.verb == "publish" and args.generation_base == 7 and args.dry_run is True
    args = parser.parse_args(["rollback", "--plan", "p.toml", "--to", "v1", "--generation", "4"])
    assert args.to_tag == "v1" and args.generation == 4
