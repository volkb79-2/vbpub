"""Parser-only safety contracts for command subcommands."""

from pathlib import Path

import pytest

from topos.cli import (
    parse_damon_args,
    parse_mcp_args,
    parse_snapshot_args,
)
from topos.daemon.deploy import DEFAULT_DAEMON_SOCKET
from topos.damon.control import APPROVAL_TEXT


def test_damon_stop_is_explicit_and_not_destructive_by_default() -> None:
    with pytest.raises(SystemExit):
        parse_damon_args([])

    args = parse_damon_args(["stop"])
    assert args.command == "stop"
    assert args.all_mine is False

    opted_in = parse_damon_args(["stop", "--all-mine"])
    assert opted_in.command == "stop"
    assert opted_in.all_mine is True


def test_damon_paddr_start_preserves_confirmation_config_and_fixture_flag(
    tmp_path: Path,
) -> None:
    config = tmp_path / "topos.toml"
    args = parse_damon_args(
        [
            "paddr",
            "start",
            "--confirm",
            APPROVAL_TEXT,
            "--config",
            str(config),
            "--allow-non-root-fixture",
        ]
    )

    assert args.command == "paddr"
    assert args.paddr_command == "start"
    assert args.confirm == APPROVAL_TEXT
    assert args.config == config
    assert args.allow_non_root_fixture is True


def test_snapshot_requires_inspect_and_preserves_bundle_path(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        parse_snapshot_args([])

    bundle = tmp_path / "incident.tar.zst"
    args = parse_snapshot_args(["inspect", str(bundle)])
    assert args.command == "inspect"
    assert args.file == bundle


def test_mcp_serve_defaults_socket_and_accepts_redaction_ceiling() -> None:
    args = parse_mcp_args(["serve", "--redact-above", "operational"])
    assert args.command == "serve"
    assert args.socket == DEFAULT_DAEMON_SOCKET
    assert args.redact_above == "operational"


def test_mcp_requires_serve_and_rejects_invalid_redaction_ceiling() -> None:
    with pytest.raises(SystemExit):
        parse_mcp_args([])
    with pytest.raises(SystemExit):
        parse_mcp_args(["serve", "--redact-above", "classified"])
