"""Executable module-entrypoint contract for ``python -m ciu.deploy``."""

from __future__ import annotations

import runpy
import sys

import pytest


def test_deploy_module_entrypoint_delegates_to_cli_help(monkeypatch, capsys):
    """The module guard invokes the real CLI and preserves argparse's help exit 0."""

    monkeypatch.setattr(sys, "argv", ["ciu.deploy", "--help"])

    # Other tests may already have imported this module.  Execute the module
    # guard from a fresh module-cache entry so the result is independent of
    # suite ordering (and runpy does not emit its re-import warning).
    monkeypatch.delitem(sys.modules, "ciu.deploy", raising=False)
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("ciu.deploy", run_name="__main__")

    assert exit_info.value.code == 0
    output = capsys.readouterr().out
    assert "usage:" in output
    assert "--deploy" in output
    assert "--healthcheck" in output
