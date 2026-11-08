"""Deterministic page-content formatting against checked-in HTML, without a browser."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "pages" / "article.html"
COMPACT_FIXTURE = Path(__file__).parent / "fixtures" / "pages" / "compact-content.html"
URL = "https://example.test/news/release"
PWMCP = Path(__file__).resolve().parents[2]


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


def test_compact_html_separates_list_items_table_cells_rows_and_options() -> None:
    source = COMPACT_FIXTURE.read_text(encoding="utf-8")

    assert _extract(source, fmt="text") == (
        "before\none\ntwo\nA B\n1 2\nyes\nno\nafter"
    )
    assert _extract(source, fmt="markdown") == (
        "before\n\n- one\n- two\n\nA | B\n1 | 2\n\nyes\n\nno\n\nafter"
    )


def test_text_collapses_paragraph_source_whitespace_and_keeps_pre_verbatim() -> None:
    source = "<p>wrapped\n    line</p><pre>  first\n    second</pre><p>after\ncontinued</p>"

    assert _extract(source, fmt="text") == "wrapped line\n  first\n    second\nafter continued"
    nested_pre = "<pre><span>  first\n    second</span><script>hidden</script></pre>"
    assert _extract(nested_pre, fmt="text") == "  first\n    second"


def test_markdown_collapses_paragraph_source_whitespace_and_keeps_pre_indentation() -> None:
    source = "<p>wrapped\n    line</p><pre>  first\n    second</pre>"

    assert _extract(source, fmt="markdown") == "wrapped line\n\n```\n  first\n    second\n```"


def test_main_candidate_priority_is_main_then_role_then_article() -> None:
    source = (
        "<article>article first</article><div role='main'>role second</div>"
        "<main>main third</main>"
    )

    assert _extract(source, fmt="text", main=True) == "main third"
    assert _extract("<article>article</article><div role='main'>role</div>", fmt="text", main=True) == "role"
    assert _extract("<article>article</article>", fmt="text", main=True) == "article"


def test_script_style_noscript_and_template_content_are_skipped() -> None:
    source = (
        "<p>kept<script>inline secret</script></p><script>secret script</script><style>secret style</style>"
        "<noscript>secret noscript</noscript><template>secret template</template><p>end</p>"
    )

    for fmt in ("text", "markdown"):
        result = _extract(source, fmt=fmt)
        assert "kept" in result and "end" in result
        assert "secret" not in result


def test_nested_list_indentation_and_ordered_list_start_are_preserved() -> None:
    source = (
        "<ul><li>parent<ul><li>child</li></ul></li></ul>"
        "<ol start='4'><li>four</li><li>five</li></ol>"
    )

    assert _extract(source, fmt="markdown") == "- parent\n  - child\n\n4. four\n5. five"


def test_invalid_ordered_list_start_uses_the_first_item_number() -> None:
    assert _extract("<ol start='first'><li>one</li></ol>", fmt="markdown") == "1. one"


def test_pre_fence_is_longer_than_any_backtick_run() -> None:
    assert _extract("<pre><span>code with ``` inside</span><script>hidden</script></pre>", fmt="markdown") == (
        "````\ncode with ``` inside\n````"
    )


def test_empty_links_fall_back_to_the_resolved_href_and_empty_content_is_empty() -> None:
    assert _extract("<a href='../target'></a>", fmt="links") == (
        "- https://example.test/target (https://example.test/target)"
    )
    assert _extract("<div> \n </div>", fmt="text") == ""
    assert _extract("", fmt="text") == ""
    assert _extract("<a>no href</a>", fmt="links") == ""
    assert _extract("<table></table><ul></ul>", fmt="markdown") == ""


def test_parser_handles_void_self_closing_and_unmatched_end_tags() -> None:
    source = "before<br><img src='x'><span/>after</unknown>"

    assert _extract(source, fmt="text") == "before\nafter"


def test_table_row_extraction_does_not_duplicate_nested_table_rows() -> None:
    source = (
        "<table><tr><td>outer<table><tr><td>inner</td></tr></table></td></tr>"
        "<tr><td>last</td></tr></table>"
    )

    assert _extract(source, fmt="markdown") == "outerinner\nlast"
    nested_sibling = (
        "<table><tbody><table><tr><td>hidden</td></tr></table>"
        "<tr><td>shown</td></tr></tbody></table>"
    )
    assert _extract(nested_sibling, fmt="markdown") == "shown"
    whitespace_row = "<table><tr> \n <td>cell</td></tr><tr>no cells</tr></table>"
    assert _extract(whitespace_row, fmt="text") == "cell"


def test_markdown_keeps_inline_and_semantic_block_formatting() -> None:
    source = (
        "<p>before<br><em>italic</em><del>old</del><a href='/target'></a><a>plain link</a>"
        "<script>hidden</script></p><dl><dt>term</dt><dd>definition</dd></dl>"
        "<figure><figcaption>caption</figcaption></figure>"
        "<blockquote><p>quoted</p></blockquote><hr>"
    )

    result = _extract(source, fmt="markdown")

    assert result == (
        "before\n*italic*~~old~~[https://example.test/target](https://example.test/target)"
        "plain link\n\nterm\n\ndefinition\n\ncaption\n\n> quoted\n\n---"
    )


def test_unknown_content_format_is_rejected() -> None:
    from pwmcp_client.page_content import extract_content

    with pytest.raises(ValueError, match="format must be text, markdown, or links"):
        extract_content("<p>content</p>", fmt="html", base_url=URL)


def test_page_content_is_judged_for_line_and_branch_coverage_in_r1_and_r2() -> None:
    assay = tomllib.loads((PWMCP / "assay.toml").read_text(encoding="utf-8"))
    target = "client/src/pwmcp_client/page_content.py"

    for lane_name in ("r1", "r2"):
        judge = assay["lanes"][lane_name]["judge"]
        assert target in judge["targets"]
        assert judge["fail_under"] == 100.0
        assert judge["require_branch"] is True


def test_content_format_docs_cover_the_output_and_known_limits() -> None:
    readme = (PWMCP / "README.md").read_text(encoding="utf-8")
    design = (PWMCP / "docs" / "DESIGN-GUIDE.md").read_text(encoding="utf-8")
    consumers = (PWMCP / "docs" / "CONSUMERS.md").read_text(encoding="utf-8")

    for document in (readme, design):
        for limit in (
            "<base href>",
            "`javascript:`",
            "RecursionError",
            "--selector",
            "nested `header`, `nav`, and `footer`",
        ):
            assert limit in document
    assert "collapses source whitespace" in consumers
    assert "preserves `<pre>`" in consumers
    assert "table cells are separated by ` | `" in consumers
