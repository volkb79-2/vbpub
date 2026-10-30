from __future__ import annotations

import json

import pytest

from pwmcp_client import __version__
from pwmcp_client.contract import load_contract
from pwmcp_client.cli import main
from pwmcp_client.session import _major_minor


def test_load_contract(tmp_path):
    path = tmp_path / "contract.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "release": "1.61.0-r5",
        "playwright": {"python": "1.61.0", "protocol": "1.61"},
        "endpoints": {"playwright_ws": "ws://pwmcp:3000/", "health": "http://pwmcp:3000/health"},
        "run_server": {"limits": {"default_lease_seconds": 10, "max_lease_seconds": 20, "max_clients": 2, "idle_recycle_seconds": 30}},
    }))
    contract = load_contract(path)
    assert contract.release == "1.61.0-r5"
    assert contract.max_clients == 2


def test_major_minor_ignores_patch():
    assert _major_minor("1.61.1") == _major_minor("1.61.0")


def test_parser_errors_report_the_diagnostic_first_then_the_package_headline(monkeypatch, capsys):
    # Intentional change (library error layout, AC-03/AC-04): the failure diagnostic is
    # now the first block and the identity headline follows after a blank line, instead
    # of the headline being line 1 of the diagnostic.
    monkeypatch.setattr("sys.argv", ["pwmcp", "--bad"])
    assert main() == 2
    lines = capsys.readouterr().err.splitlines()
    assert lines[0].startswith("[ERROR] pwmcp:")
    assert lines[1] == ""
    assert lines[2] == f"PWMCP {__version__} — client utility"


def test_version_flag_prints_package_version(capsys):
    assert main(["--version"]) == 0
    captured = capsys.readouterr()
    assert captured.out == f"pwmcp {__version__}\n"
    assert captured.err == ""
