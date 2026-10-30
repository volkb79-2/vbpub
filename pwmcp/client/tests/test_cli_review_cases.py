"""Replay every reviewed pwmcp CLI case (docs/cli-review.toml) against the real registry.

Each active catalog row carries an invocation and its expected status/output; the
parametrized test below executes it in-process in a hermetic HOME and working
directory, so the catalog cannot drift from behavior. The ``cli_case`` marker links
each node to its row; the ``cli_extended.pytest_plugin`` (pytest.ini) fails the run
when an active case has no collected test.
"""

from __future__ import annotations

import http.server
import json
import sys
import threading
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


def _install_review_browser(monkeypatch: pytest.MonkeyPatch) -> tuple[list[bool], dict[str, list]]:
    context_options: list[bool] = []
    captures: dict[str, list] = {"waits": [], "clips": []}

    class FakePlaywrightError(Exception):
        pass

    class FakeLocator:
        def filter(self, *, visible: bool) -> "FakeLocator":
            return self

        @property
        def first(self) -> "FakeLocator":
            return self

        def inner_text(self, *, timeout: int) -> str:
            return "rendered page text"

        def aria_snapshot(self, *, timeout: int) -> str:
            return '- heading "Sample page" [level=1]'

        def wait_for(self, *, state: str, timeout: int) -> None:
            return None

        def screenshot(self, **_kwargs: object) -> bytes:
            return b"reviewed screenshot"

        def scroll_into_view_if_needed(self, **_kwargs: object) -> None:
            return None

        def bounding_box(self, **_kwargs: object) -> dict[str, float]:
            return {"x": 10.0, "y": 20.0, "width": 640.0, "height": 400.0}

    class FakePage:
        def goto(self, _url: str, *, wait_until: str, timeout: int) -> None:
            return None

        def wait_for_timeout(self, milliseconds: int) -> None:
            captures["waits"].append(milliseconds)

        def locator(self, _selector: str) -> FakeLocator:
            return FakeLocator()

        def screenshot(self, **kwargs: object) -> bytes:
            captures["clips"].append(kwargs.get("clip"))
            return b"reviewed screenshot"

    class FakeCdp:
        def send(self, _method: str, _params: object) -> None:
            return None

    class FakeContext:
        def new_page(self) -> FakePage:
            return FakePage()

        def new_cdp_session(self, _page: object) -> FakeCdp:
            return FakeCdp()

        def close(self) -> None:
            return None

    class FakeBrowser:
        def new_context(self, *, ignore_https_errors: bool, user_agent: str | None = None) -> FakeContext:
            context_options.append(ignore_https_errors)
            return FakeContext()

    class FakeLease:
        browser = FakeBrowser()

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
    return context_options, captures


OCR_CANNED = {
    "engine": "tesseract", "engine_version": "5.3.4", "lang": "eng", "psm": 6, "width": 640, "height": 400,
    "elapsed_ms": 12, "text": "Debian GNU/Linux installer",
    "lines": [{"text": "Debian GNU/Linux installer", "conf": 93.5, "box": [8, 8, 300, 16]}],
    "words": [{"text": "Debian", "conf": 95.0, "box": [8, 8, 60, 16], "line": 0}],
}
# The one URL a reviewed row may spell for --sidecar-url; the harness maps it to the fake sidecar.
SIDECAR_SPELLING = "http://pwmcp-ocr.test:8941"


class _FakeSidecar(http.server.BaseHTTPRequestHandler):
    seen: list[dict[str, object]] = []

    def do_POST(self) -> None:  # noqa: N802
        from urllib.parse import parse_qs, urlsplit

        body = self.rfile.read(int(self.headers["Content-Length"]))
        _FakeSidecar.seen.append({"query": parse_qs(urlsplit(self.path).query), "body": body})
        out = json.dumps(OCR_CANNED).encode()
        self.send_response(200)
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *_args: object) -> None:
        return None


@pytest.fixture
def fake_sidecar() -> "object":
    _FakeSidecar.seen = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FakeSidecar)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize("row", _cases())
def test_reviewed_case(
    row: dict[str, object],
    sandbox: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    fake_sidecar: str,
) -> None:
    context_options: list[bool] = []
    captures: dict[str, list] = {"waits": [], "clips": []}
    invocation = list(row["invocation"])
    is_ocr = "ocr" in invocation
    if "inspect" in invocation or is_ocr:
        context_options, captures = _install_review_browser(monkeypatch)
    if is_ocr:
        (sandbox / "screen.png").write_bytes(b"\x89PNG\r\n\x1a\nreviewed image")
        monkeypatch.delenv("PWMCP_OCR_URL", raising=False)
        if SIDECAR_SPELLING in invocation:
            invocation[invocation.index(SIDECAR_SPELLING)] = fake_sidecar
        else:
            monkeypatch.setenv("PWMCP_OCR_URL", fake_sidecar)
    status = cli_module.main(invocation)  # type: ignore[call-overload]
    captured = capsys.readouterr()
    assert status == row["expected_exit_status"], captured
    assert row["expected_stdout_contains"] in captured.out, captured
    assert row["expected_stderr_contains"] in captured.err, captured
    assert "Traceback" not in captured.err
    invocation = list(row["invocation"])
    if invocation[0] == "inspect":
        expected_context_options = (
            ["--ignore-https-errors" in invocation]
            if row["expected_exit_status"] == 0
            else []
        )
        assert context_options == expected_context_options
    if is_ocr:
        _assert_ocr_effects(invocation, sandbox, context_options, captures)
        return
    if "--output" in invocation:
        output = sandbox / invocation[invocation.index("--output") + 1]
        assert output.is_file()
        if output.suffix in {".png", ".jpg", ".jpeg"}:
            assert output.read_bytes() == b"reviewed screenshot"
        else:
            assert output.read_text(encoding="utf-8") == "rendered page text\n"


_OCR_QUERY_FLAGS = {"--engine": "engine", "--lang": "lang", "--psm": "psm", "--scale": "scale",
                    "--whitelist": "whitelist", "--min-conf": "min_conf"}


def _assert_ocr_effects(
    invocation: list[str], sandbox: Path, context_options: list[bool], captures: dict[str, list]
) -> None:
    """The reviewed ocr row really reached the sidecar with the spelled options, and nothing else."""
    (request,) = _FakeSidecar.seen
    query = request["query"]
    # Normalise `--flag=value` to `--flag value` for the lookup.
    flat = [part for token in invocation for part in (token.split("=", 1) if token.startswith("--") else [token])]
    for flag, name in _OCR_QUERY_FLAGS.items():
        if flag in flat:
            value = flat[flat.index(flag) + 1]
            assert query[name] == [f"{float(value):g}" if name == "min_conf" else value], (flag, query)
    url_mode = "https://example.test" in invocation
    assert request["body"] == (b"reviewed screenshot" if url_mode else b"\x89PNG\r\n\x1a\nreviewed image")
    if url_mode:
        assert context_options == ["--ignore-https-errors" in flat]
        if "--wait-ms" in flat:
            assert captures["waits"] == [int(flat[flat.index("--wait-ms") + 1])]
        if "--region" in flat:
            assert captures["clips"] == [{"x": 10.0, "y": 20.0, "width": 100, "height": 50}]
    if "--output" in flat:
        assert (sandbox / flat[flat.index("--output") + 1]).read_text(encoding="utf-8") == "Debian GNU/Linux installer\n"


def test_catalog_invocations_are_unique_so_node_ids_are_stable() -> None:
    rows = tomllib.loads(CATALOG.read_text(encoding="utf-8"))["cases"]
    slugs = ["-".join(row["invocation"]) for row in rows if row["state"] == "active"]
    assert len(slugs) == len(set(slugs))
    for row in rows:
        if row["state"] == "active":
            node = f"client/tests/test_cli_review_cases.py::test_reviewed_case[{'-'.join(row['invocation'])}]"
            assert row["test_ids"] == [node]
