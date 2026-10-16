"""Replay every reviewed pwmcp CLI case (docs/cli-review.toml) against the real registry.

Each active catalog row carries an invocation and its expected status/output; the
parametrized test below executes it in-process in a hermetic HOME and working
directory, so the catalog cannot drift from behavior. The ``cli_case`` marker links
each node to its row; the ``cli_extended.pytest_plugin`` (pytest.ini) fails the run
when an active case has no collected test.
"""

from __future__ import annotations

import json
import sys
import types
import tomllib
from pathlib import Path

import pytest

from pwmcp_client import cli as cli_module
from pwmcp_client import diagnostics

CATALOG = Path(__file__).resolve().parents[2] / "docs" / "cli-review.toml"
CONTRACT = {
    "schema_version": 1,
    "release": "1.63.0-r2",
    "playwright": {"python": "1.63.0", "protocol": "1.63"},
    "endpoints": {"playwright_ws": "ws://pwmcp:3000/", "health": "http://pwmcp:3000/health"},
    "run_server": {
        "limits": {
            "default_lease_seconds": 10,
            "max_lease_seconds": 20,
            "max_clients": 2,
            "idle_recycle_seconds": 30,
        }
    },
}


def _cases() -> list[object]:
    rows = tomllib.loads(CATALOG.read_text(encoding="utf-8"))["cases"]
    return [
        pytest.param(row, id="-".join(row["invocation"]), marks=pytest.mark.cli_case(row["id"]))
        for row in rows
        if row["state"] == "active"
    ]


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway HOME and cwd: skills verbs may write, never into the real home."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "contract.json").write_text(json.dumps(CONTRACT), encoding="utf-8")
    monkeypatch.setattr(diagnostics, "verify_installed_playwright", lambda contract: "1.63.4")
    return tmp_path


def _install_review_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakePlaywrightError(Exception):
        pass

    class FakeLocator:
        def inner_text(self, *, timeout: int) -> str:
            return "rendered page text"

        def aria_snapshot(self, *, timeout: int) -> str:
            return '- heading "Sample page" [level=1]'

        def wait_for(self, *, state: str, timeout: int) -> None:
            return None

    class FakePage:
        def goto(self, _url: str, *, wait_until: str, timeout: int) -> None:
            return None

        def locator(self, _selector: str) -> FakeLocator:
            return FakeLocator()

        def screenshot(self, **_kwargs: object) -> bytes:
            return b"reviewed screenshot"

    class FakeLease:
        browser = types.SimpleNamespace(new_page=lambda: FakePage())

        def __enter__(self) -> "FakeLease":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    class BrowserLeaseStub:
        @classmethod
        def connect(cls, **_kwargs: object) -> FakeLease:
            return FakeLease()

    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.Error = FakePlaywrightError
    playwright = types.ModuleType("playwright")
    playwright.sync_api = sync_api
    monkeypatch.setitem(sys.modules, "playwright", playwright)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    monkeypatch.setattr(cli_module, "BrowserLease", BrowserLeaseStub)


@pytest.mark.parametrize("row", _cases())
def test_reviewed_case(
    row: dict[str, object],
    sandbox: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if row["invocation"][0] == "inspect":
        _install_review_browser(monkeypatch)
    status = cli_module.main(list(row["invocation"]))  # type: ignore[call-overload]
    captured = capsys.readouterr()
    assert status == row["expected_exit_status"], captured
    assert row["expected_stdout_contains"] in captured.out, captured
    assert row["expected_stderr_contains"] in captured.err, captured
    assert "Traceback" not in captured.err
    invocation = list(row["invocation"])
    if "--output" in invocation:
        output = sandbox / invocation[invocation.index("--output") + 1]
        assert output.is_file()
        if output.suffix in {".png", ".jpg", ".jpeg"}:
            assert output.read_bytes() == b"reviewed screenshot"
        else:
            assert output.read_text(encoding="utf-8") == "rendered page text\n"


def test_catalog_invocations_are_unique_so_node_ids_are_stable() -> None:
    rows = tomllib.loads(CATALOG.read_text(encoding="utf-8"))["cases"]
    slugs = ["-".join(row["invocation"]) for row in rows if row["state"] == "active"]
    assert len(slugs) == len(set(slugs))
    for row in rows:
        if row["state"] == "active":
            node = f"client/tests/test_cli_review_cases.py::test_reviewed_case[{'-'.join(row['invocation'])}]"
            assert row["test_ids"] == [node]
