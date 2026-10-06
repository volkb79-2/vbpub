"""Deep behavioural coverage for strict config contracts."""
from __future__ import annotations

import pytest

from cmru import config


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
    assert message in capsys.readouterr().err


def test_installer_config_rejects_unknown_wheel_and_installer_keys(capsys):
    base = {"install_dir_system": "/x", "install_dir_user": "/y"}
    with pytest.raises(SystemExit):
        config._parse_installer("demo", {**base, "unknown": True})
    assert "unknown keys" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        config._parse_installer("demo", {**base, "wheels": [{"path": "x", "distribution": "d", "extra": 1}]})
    assert "unknown keys" in capsys.readouterr().err


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
    assert expected in capsys.readouterr().err


def test_project_document_helpers_fail_closed_for_paths_and_targets(tmp_path, capsys):
    with pytest.raises(SystemExit):
        config._read_toml(tmp_path / "wrong-name", "cmru.toml")
    assert "expected cmru.toml" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        config._read_toml(tmp_path / "cmru.toml", "cmru.toml")
    assert "not found" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        config._targets({"host": "github", "registry": [""]})
    assert "non-empty strings" in capsys.readouterr().err


def test_secret_overlay_and_github_validation_refuse_wrong_shapes(capsys):
    with pytest.raises(SystemExit):
        config._github({"owner": "o", "repo": "r", "owner_type": "team"})
    assert "owner_type" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        config._secret_token({"token": ""}, "secret.github")
    assert "non-empty" in capsys.readouterr().err


def test_validate_runner_steps_checks_login_and_command_contract(capsys):
    valid = {"run-tests": {"commands": [{"label": "gate", "argv": ["pytest"], "cwd": "."}], "quiet": True,
                            "login": {"registry": "ghcr.io", "username_env": "USER", "token_env": "TOKEN", "required": False}}}
    assert config._validate_runner_steps(valid)["run-tests"]["quiet"] is True
    broken = {"run-tests": {"commands": [{"label": "gate", "argv": ["pytest"], "cwd": "."}], "quiet": True,
                             "login": {"registry": "ghcr.io", "username_env": "USER", "token_env": "TOKEN", "required": "yes"}}}
    with pytest.raises(SystemExit):
        config._validate_runner_steps(broken)
    assert "required" in capsys.readouterr().err
