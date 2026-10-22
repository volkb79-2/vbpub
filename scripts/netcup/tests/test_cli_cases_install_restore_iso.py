"""Replay the restore workflow's reviewed CLI grammar cases."""
from __future__ import annotations

import io
import importlib.util
import tomllib
from pathlib import Path

import pytest
from cli_extended.parser import UsageError

NETCUP_DIR = Path(__file__).resolve().parents[1]
SCRIPT = NETCUP_DIR / "install-restore-iso.py"
CATALOG = NETCUP_DIR / "cli-review-install-restore-iso.toml"
ROWS = tomllib.loads(CATALOG.read_text(encoding="utf-8"))["cases"]
SPEC = importlib.util.spec_from_file_location("install_restore_iso_cli_cases", SCRIPT)
install_restore_iso = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(install_restore_iso)


def _case_params():
    return [
        pytest.param(row, id=row["id"], marks=pytest.mark.cli_case(row["id"]))
        for row in ROWS
    ]


@pytest.mark.parametrize("row", _case_params())
def test_replayed_case_matches_catalog(row):
    app = install_restore_iso.build_cli()
    expected = row["expected_exit_status"]
    if expected == 0:
        parsed = app.parse_args(row["invocation"])
        assert parsed.server_id == 804027
    else:
        try:
            app.parse_args(row["invocation"])
        except UsageError as exc:
            assert getattr(exc, "exit_code", expected) == expected
            assert row["expected_stderr_contains"] in str(exc)
        else:
            stdout = io.StringIO()
            stderr = io.StringIO()
            status = app.run(argv=row["invocation"], stdout=stdout, stderr=stderr)
            assert status == expected
            assert row["expected_stderr_contains"] in stderr.getvalue()
