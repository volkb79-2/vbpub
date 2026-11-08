"""Deterministic page-content formatting against checked-in HTML, without a browser."""

from __future__ import annotations

from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "pages" / "article.html"
URL = "https://example.test/news/release"


def _extract(source: str, *, fmt: str, main: bool = False) -> str:
    from pwmcp_client.page_content import extract_content

    return extract_content(source, fmt=fmt, base_url=URL, main=main)


def test_text_main_content_drops_navigation_header_and_footer() -> None:
    result = _extract(FIXTURE.read_text(encoding="utf-8"), fmt="text", main=True)

    assert "Release notes" in result
    assert "the guide" in result and "stay safe" in result
    assert "docs/index.md" in result and "pwmcp inspect --main" in result
    assert "Home" not in result and "Header chrome" not in result and "Footer chrome" not in result


def test_markdown_keeps_headings_lists_code_and_absolute_links() -> None:
    result = _extract(FIXTURE.read_text(encoding="utf-8"), fmt="markdown", main=True)

    assert result.startswith("# Release notes")
    assert "[the guide](https://example.test/guide/start?x=1&y=2)" in result
    assert "**stay safe**" in result
    assert "## Steps" in result
    assert "1. Open `docs/index.md`\n2. Run command" in result
    assert "```\npwmcp inspect --main\necho ok\n```" in result
    assert "- [Issue 7](https://example.test/issues/7)" in result
    assert "Header chrome" not in result and "Footer chrome" not in result


def test_links_format_resolves_relative_hrefs_and_main_excludes_chrome() -> None:
    source = FIXTURE.read_text(encoding="utf-8")

    all_links = _extract(source, fmt="links")
    main_links = _extract(source, fmt="links", main=True)

    assert all_links.splitlines() == [
        "- Home (https://example.test/home)",
        "- the guide (https://example.test/guide/start?x=1&y=2)",
        "- Issue 7 (https://example.test/issues/7)",
        "- Legal (https://example.test/legal)",
    ]
    assert main_links.splitlines() == [
        "- the guide (https://example.test/guide/start?x=1&y=2)",
        "- Issue 7 (https://example.test/issues/7)",
    ]


def test_main_fallback_removes_page_header_navigation_and_footer() -> None:
    source = (
        "<header>Site header</header><nav>Navigation</nav>"
        "<div><p>Rendered article body.</p></div><footer>Site footer</footer>"
    )

    result = _extract(source, fmt="text", main=True)

    assert result == "Rendered article body."
