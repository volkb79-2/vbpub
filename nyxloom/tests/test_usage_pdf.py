"""Synthetic PDF and optional PWMCP renderer contracts."""

from __future__ import annotations

import sys
import types
from io import BytesIO
from typing import ClassVar, Self

import pytest
from pypdf import PdfReader
from test_usage_html import _report
from test_usage_report import (
    MESSAGE_SENTINEL,
    TOOL_SENTINEL,
    _build,
    _write_tree_fixture,
)

from nyxloom import cli
from nyxloom.session_extract.usage import ChartOptions, load_corpus
from nyxloom.session_extract.usage.pdf import UsagePDFError, render_pdf
from nyxloom.session_extract.usage.pricing import apply_valuation, load_weights


def _text_pdf(pages: list[list[str]]) -> bytes:
    """Build a tiny deterministic, searchable PDF fixture without a writer dependency."""
    page_ids = [3 + index * 2 for index in range(len(pages))]
    content_ids = [page_id + 1 for page_id in page_ids]
    font_id = 3 + len(pages) * 2
    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: (
            f"<< /Type /Pages /Kids [{ ' '.join(f'{page_id} 0 R' for page_id in page_ids) }] "
            f"/Count {len(pages)} >>"
        ).encode("ascii"),
        font_id: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for page_id, content_id, lines in zip(page_ids, content_ids, pages, strict=True):
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 842 595] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode("ascii")
        commands = ["BT", "/F1 9 Tf", "40 565 Td"]
        for index, line in enumerate(lines):
            if index:
                commands.append("0 -9 Td")
            escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            commands.append(f"({escaped}) Tj")
        commands.append("ET")
        stream = ("\n".join(commands) + "\n").encode("ascii")
        objects[content_id] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
            + stream + b"endstream"
        )

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0] * (font_id + 1)
    for object_id in sorted(objects):
        offsets[object_id] = len(output)
        output.extend(f"{object_id} 0 obj\n".encode("ascii"))
        output.extend(objects[object_id])
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {font_id + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {font_id + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(output)


def _pdf_fixture() -> bytes:
    first_page = [
        "nyxloom usage report",
        "Session / agent / topic | Calls | Tokens",
        *(f"agent-{index:03d} | 1 | {index}" for index in range(1, 61)),
    ]
    second_page = [
        "Session / agent / topic | Calls | Tokens",
        *(f"agent-{index:03d} | 1 | {index}" for index in range(61, 121)),
        "Swim lanes - context over time",
        "Joined context over time",
        "Self cost by time bucket",
        "Cost composition",
        "Grand total: 120 calls, 7260 tokens",
    ]
    return _text_pdf([first_page, second_page])


class _FakePage:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.html = ""
        self.ready_script = ""
        self.media: str | None = None
        self.pdf_options: dict[str, object] = {}
        self.events: list[str] = []

    def set_content(self, html: str, **kwargs: object) -> None:
        self.events.append("set_content")
        assert kwargs == {"wait_until": "load", "timeout": 60_000}
        self.html = html

    def wait_for_function(self, script: str, **kwargs: object) -> None:
        self.events.append("wait_for_function")
        assert kwargs == {"timeout": 60_000}
        self.ready_script = script

    def emulate_media(self, *, media: str) -> None:
        self.events.append("emulate_media")
        self.media = media

    def pdf(self, **kwargs: object) -> bytes:
        self.events.append("pdf")
        self.pdf_options = kwargs
        return self.payload


class _FakeContext:
    def __init__(self, page: _FakePage):
        self.page = page
        self.closed = False

    def new_page(self) -> _FakePage:
        return self.page

    def close(self) -> None:
        self.closed = True


class _FakeBrowser:
    def __init__(self, context: _FakeContext):
        self.context = context

    def new_context(self) -> _FakeContext:
        return self.context


class _FakeLease:
    connect_args: ClassVar[dict[str, object]] = {}

    def __init__(self, browser: _FakeBrowser):
        self.browser = browser

    @classmethod
    def connect(cls, **kwargs: object) -> _FakeLease:
        cls.connect_args = kwargs
        return cls(_FakeBrowser(_FAKE_CONTEXT))

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


_FAKE_PAGE = _FakePage(_pdf_fixture())
_FAKE_CONTEXT = _FakeContext(_FAKE_PAGE)


def _install_fake_pwmcp(monkeypatch: pytest.MonkeyPatch) -> None:
    package = types.ModuleType("pwmcp_client")
    package.__path__ = []  # type: ignore[attr-defined]
    session = types.ModuleType("pwmcp_client.session")
    session.BrowserLease = _FakeLease  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pwmcp_client", package)
    monkeypatch.setitem(sys.modules, "pwmcp_client.session", session)


def test_pdf_renderer_waits_for_html_then_uses_print_media_and_preserves_pages(monkeypatch, tmp_path):
    _install_fake_pwmcp(monkeypatch)
    _write_tree_fixture(tmp_path)
    weights = load_weights()
    corpus = load_corpus(tmp_path)
    apply_valuation(corpus, weights)
    report = _build(corpus, chart_options=ChartOptions(weights=weights))

    rendered = render_pdf(report, weights_file="usage-weights.toml")

    assert rendered.startswith(b"%PDF-")
    assert _FakeLease.connect_args == {
        "label": "nyxloom-usage-pdf", "timeout_ms": 60_000,
    }
    assert _FAKE_PAGE.ready_script == (
        "document.getElementById('usage-report-ready')?.dataset.ready === '1'"
    )
    assert _FAKE_PAGE.media == "print"
    assert _FAKE_PAGE.pdf_options == {
        "format": "A4", "landscape": True, "print_background": True,
        "prefer_css_page_size": True,
    }
    assert _FAKE_PAGE.events == ["set_content", "wait_for_function", "emulate_media", "pdf"]
    assert _FAKE_CONTEXT.closed
    assert 'id="usage-report-ready"' in _FAKE_PAGE.html
    assert 'id="usage-table"' in _FAKE_PAGE.html
    assert "Swim lanes · context over time" in _FAKE_PAGE.html
    assert "outer topic" in _FAKE_PAGE.html
    assert MESSAGE_SENTINEL not in _FAKE_PAGE.html
    assert TOOL_SENTINEL not in _FAKE_PAGE.html
    assert "display:table-header-group" in _FAKE_PAGE.html
    assert "break-inside:avoid" in _FAKE_PAGE.html

    reader = PdfReader(BytesIO(rendered))
    assert len(reader.pages) == 2
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "Session / agent / topic" in text
    assert all(f"agent-{index:03d}" in text for index in range(1, 121))
    for title in (
        "Swim lanes - context over time", "Joined context over time",
        "Self cost by time bucket", "Cost composition", "Grand total: 120 calls, 7260 tokens",
    ):
        assert title in text
    assert MESSAGE_SENTINEL not in text


def test_pdf_renderer_gives_optional_dependency_remedy_when_client_is_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "pwmcp_client", None)
    monkeypatch.setitem(sys.modules, "pwmcp_client.session", None)

    with pytest.raises(UsagePDFError, match=r"nyxloom\[report\].*PWMCP client wheel"):
        render_pdf(_report())


def test_pdf_renderer_hides_service_endpoint_details_and_explains_remedy(monkeypatch):
    class UnavailableLease:
        @classmethod
        def connect(cls, **_kwargs: object) -> UnavailableLease:
            raise OSError("private-service-host:3000 unreachable")

    package = types.ModuleType("pwmcp_client")
    package.__path__ = []  # type: ignore[attr-defined]
    session = types.ModuleType("pwmcp_client.session")
    session.BrowserLease = UnavailableLease  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pwmcp_client", package)
    monkeypatch.setitem(sys.modules, "pwmcp_client.session", session)

    with pytest.raises(UsagePDFError) as raised:
        render_pdf(_report())

    message = str(raised.value)
    assert "managed PWMCP service" in message
    assert "client version matches" in message
    assert "private-service-host" not in message
    assert "no PDF was written" in message


def test_private_output_writer_preserves_pdf_bytes_and_mode(tmp_path):
    target = tmp_path / "usage.pdf"
    payload = _pdf_fixture()

    cli._write_private_usage_output(target, payload)

    assert target.read_bytes() == payload
    assert target.stat().st_mode & 0o777 == 0o600
