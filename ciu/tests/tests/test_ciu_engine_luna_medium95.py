"""Top-level engine ``SystemExit`` propagation contracts."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import engine  # noqa: E402


def _args(**overrides: object) -> SimpleNamespace:
    values = {
        "dir": Path("/unused"),
        "file": engine.CIU_COMPOSE_TEMPLATE,
        "dry_run": False,
        "reset": False,
        "yes": False,
        "print_context": False,
        "render_toml": False,
        "define_root": None,
        "skip_hostdir_check": False,
        "skip_hooks": False,
        "skip_secrets": False,
        "secrets": False,
        "generate_env": False,
        "update_cert_permission": False,
        "auto_connect_network": None,
        "shipped": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _assert_exit_seven(callable_obj) -> None:
    with pytest.raises(SystemExit) as caught:
        callable_obj()
    assert caught.value.code == 7


def test_secrets_branch_reraises_system_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    parsed = argparse.Namespace(action="list")

    class Parser:
        def parse_args(self, argv: list[str]) -> argparse.Namespace:
            assert argv == ["list"]
            return parsed

    monkeypatch.setattr(engine, "_build_secrets_subparser", lambda: Parser())
    monkeypatch.setattr(engine, "secrets_command", lambda args: (_ for _ in ()).throw(SystemExit(7)))

    _assert_exit_seven(lambda: engine.main(["secrets", "list"]))


def test_generate_env_branch_reraises_system_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine, "parse_arguments", lambda argv: _args(generate_env=True))
    monkeypatch.setattr(
        engine,
        "check_runtime_dependencies",
        lambda: (_ for _ in ()).throw(SystemExit(7)),
    )

    _assert_exit_seven(lambda: engine.main(["--generate-env"]))


def test_shipped_branch_reraises_system_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine, "parse_arguments", lambda argv: _args(shipped=True))
    monkeypatch.setattr(
        engine,
        "run_shipped",
        lambda **kwargs: (_ for _ in ()).throw(SystemExit(7)),
    )

    _assert_exit_seven(lambda: engine.main(["--shipped"]))


def test_normal_native_branch_reraises_system_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine, "parse_arguments", lambda argv: _args())
    monkeypatch.setattr(
        engine,
        "main_execution",
        lambda **kwargs: (_ for _ in ()).throw(SystemExit(7)),
    )

    _assert_exit_seven(lambda: engine.main([]))
