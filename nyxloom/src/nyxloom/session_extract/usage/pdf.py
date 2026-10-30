"""Optional PWMCP-backed PDF rendering for usage-report-v1."""

from __future__ import annotations

from typing import Any

from .html import render_html

_RENDER_TIMEOUT_MS = 60_000
_READY_SCRIPT = "document.getElementById('usage-report-ready')?.dataset.ready === '1'"


class UsagePDFError(RuntimeError):
    """A PDF could not be rendered by the configured PWMCP service."""


def render_pdf(report: dict[str, Any], *, weights_file: str = "usage-weights.toml") -> bytes:
    """Render a usage report through the managed PWMCP Chromium browser.

    The generated HTML is sent in memory with ``page.set_content``. No local
    file URL, local browser binary, or PWMCP server change is required.
    """
    if report.get("schema_version") != "usage-report-v1":
        raise ValueError("PDF renderer requires usage-report-v1")
    try:
        from pwmcp_client.session import BrowserLease
    except ImportError as exc:
        raise UsagePDFError(
            "PDF export needs the optional `nyxloom[report]` extra and the matching "
            "PWMCP client wheel; see the PDF export setup in docs/CONSUMERS.md."
        ) from exc

    html = render_html(report, weights_file=weights_file)
    try:
        with BrowserLease.connect(
            label="nyxloom-usage-pdf",
            timeout_ms=_RENDER_TIMEOUT_MS,
        ) as lease:
            context = lease.browser.new_context()
            try:
                page = context.new_page()
                page.set_content(
                    html,
                    wait_until="load",
                    timeout=_RENDER_TIMEOUT_MS,
                )
                page.wait_for_function(_READY_SCRIPT, timeout=_RENDER_TIMEOUT_MS)
                page.emulate_media(media="print")
                pdf = page.pdf(
                    format="A4",
                    landscape=True,
                    print_background=True,
                    prefer_css_page_size=True,
                )
            finally:
                context.close()
    except ImportError as exc:
        raise UsagePDFError(
            "PDF export needs the Playwright dependency from the matching PWMCP client "
            "bundle; see the PDF export setup in docs/CONSUMERS.md."
        ) from exc
    except Exception:  # noqa: BLE001 - normalize browser errors without exposing endpoints. # census: cleanup/containment
        raise UsagePDFError(
            "PWMCP PDF rendering failed. Check that the managed PWMCP service is running "
            "and that its client version matches the published browser contract; no PDF "
            "was written. See docs/CONSUMERS.md#pdf-export."
        ) from None

    if not isinstance(pdf, bytes) or not pdf.startswith(b"%PDF-"):
        raise UsagePDFError("PWMCP returned invalid PDF data; no PDF was written.")
    return pdf


__all__ = ["UsagePDFError", "render_pdf"]
