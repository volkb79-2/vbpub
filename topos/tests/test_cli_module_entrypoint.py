"""Executable-module boundary for the public Topos CLI."""

from __future__ import annotations

import runpy
import sys

import pytest


def test_cli_module_help_exits_successfully(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``python -m topos.cli --help`` dispatches through the module entrypoint."""
    monkeypatch.setattr(sys, "argv", ["topos.cli", "--help"])
    # Other test imports may have loaded the module; a fresh module execution is
    # what ``python -m`` performs and avoids testing runpy's warning path.
    monkeypatch.delitem(sys.modules, "topos.cli", raising=False)

    with pytest.raises(SystemExit) as raised:
        runpy.run_module("topos.cli", run_name="__main__")

    assert raised.value.code == 0
    assert "usage: topos" in capsys.readouterr().out
