"""Deterministic page-content formatting against checked-in HTML, without a browser."""

from __future__ import annotations

import tomllib
from pathlib import Path
from html import escape

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
    source = "before<br><img src='x'><span/>after</unknown><div><span>nested</div>"

    assert _extract(source, fmt="text") == "before\nafter\nnested"


def test_markdown_separates_adjacent_blocks_nested_in_list_items_cells_and_links() -> None:
    cases = {
        "<ul><li><div>a</div><div>b</div></li></ul>": "- a b",
        "<ul><li><p>a</p><p>b</p></li></ul>": "- a b",
        "<ul><li><h3>h</h3>t</li></ul>": "- h t",
        "<ul><li>x<ul><li>y</li></ul>z</li></ul>": "- x z\n  - y",
        "<table><tr><td><div>a</div><div>b</div></td></tr></table>": "a b",
        "<table><tr><td><div>a</div><div>b</div></td><td>c</td></tr></table>": "a b | c",
        "<a href='/x'><div>Title</div><div>Desc</div></a>": "[Title Desc](https://example.test/x)",
        "<a href='/x'><div> Title </div> <span>and</span><div>Desc</div></a>": (
            "[Title and Desc](https://example.test/x)"
        ),
        "<p>one<div>two</div>three</p>": "one two three",
    }
    for source, expected in cases.items():
        assert _extract(source, fmt="markdown") == expected, source
    assert _extract("<ul><li><div>a</div><div>b</div></li></ul>", fmt="text") == "a\nb"


def test_details_summary_and_body_are_separate_blocks_in_text_and_markdown() -> None:
    source = "<details open><summary>Sum</summary>Body text</details>"

    assert _extract(source, fmt="text") == "Sum\nBody text"
    assert _extract(source, fmt="markdown") == "Sum\n\nBody text"


def test_markdown_pre_strips_only_leading_and_trailing_newlines() -> None:
    source = "<pre>\n\n  indented\n\nkept\n\n</pre>"

    assert _extract(source, fmt="markdown") == "```\n  indented\n\nkept\n```"


def test_table_row_extraction_does_not_duplicate_nested_table_rows() -> None:
    source = (
        "<table><tr><td>outer<table><tr><td>inner</td></tr></table></td></tr>"
        "<tr><td>last</td></tr></table>"
    )

    assert _extract(source, fmt="markdown") == "outer inner\nlast"
    nested_sibling = (
        "<table><tbody><table><tr><td>hidden</td></tr></table>"
        "<tr><td>shown</td></tr></tbody></table>"
    )
    assert _extract(nested_sibling, fmt="markdown") == "shown"
    spaced_table = "<table> \n <tr><td>cell</td></tr> </table>"
    assert _extract(spaced_table, fmt="markdown") == "cell"
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
    assert _extract("<span>top level inline</span>", fmt="markdown") == "top level inline"


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
            "computed",
        ):
            assert limit in document
    assert "collapses source whitespace" in consumers
    assert "preserves `<pre>`" in consumers
    assert "table cells are separated by ` | `" in consumers


def test_malformed_inline_css_policy_is_synchronized_across_user_docs() -> None:
    documents = (
        (PWMCP / "README.md").read_text(encoding="utf-8"),
        (PWMCP / "docs" / "DESIGN-GUIDE.md").read_text(encoding="utf-8"),
        (PWMCP / "docs" / "CONSUMERS.md").read_text(encoding="utf-8"),
    )
    policy_terms = (
        "complete declarations independently",
        "`display:none`",
        "`visibility:hidden`",
        "A malformed style with neither marker remains visible.",
        "Tokenization ignores string and nested function contents",
        "unclosed comment's remainder is checked as a malformed fragment",
        "An unescaped LF, CR, or FF inside a CSS string ends that malformed string",
        "CSS escapes in property names and hiding keyword values are decoded",
        "Outside strings, a backslash escape consumes the next code point",
    )

    for document in documents:
        normalized = " ".join(document.split()).casefold()
        for term in policy_terms:
            assert term.casefold() in normalized, term


# --- PWMCP-50: hidden page content and zero-width characters must not reach the caller ---

HIDDEN_FIXTURE = Path(__file__).parent / "fixtures" / "pages" / "hidden-content.html"
ZERO_WIDTH = ("\u200b", "\u200c", "\u200d", "\u2060", "\ufeff")


def _extraction(source: str, *, fmt: str, main: bool = False):
    from pwmcp_client.page_content import extract_page

    return extract_page(source, fmt=fmt, base_url=URL, main=main)


def test_hidden_fixture_is_compact_html_with_every_static_hidden_form() -> None:
    source = HIDDEN_FIXTURE.read_text(encoding="utf-8")

    assert "\n" not in source.rstrip("\n")  # compact: no whitespace between tags
    assert "<div hidden>" in source
    assert 'aria-hidden="true"' in source and 'aria-hidden="false"' in source
    assert 'style="color:red; DISPLAY : nOnE"' in source
    assert 'style="visibility: HIDDEN"' in source
    assert 'style="display: /* c */ none"' in source
    assert 'style="DISPLAY/**/:NONE"' in source
    assert 'style="display:none !important;display:block"' in source
    assert 'style="display:none;display:block"' in source
    assert 'type="HiDdEn"' in source
    assert 'hidden="until-found"' in source
    assert "<details><summary>Closed details summary</summary>" in source
    assert "<details open><summary>Open details summary</summary>" in source
    assert all(character in source for character in ZERO_WIDTH)


@pytest.mark.parametrize("main", [False, True])
@pytest.mark.parametrize("fmt", ["text", "markdown", "links"])
def test_hidden_elements_and_their_subtrees_are_omitted_from_every_parsed_format(fmt: str, main: bool) -> None:
    result = _extraction(HIDDEN_FIXTURE.read_text(encoding="utf-8"), fmt=fmt, main=main)

    assert "secret" not in result.content
    assert "hidden-link" not in result.content
    assert result.hidden_removed == 15
    assert result.zero_width_stripped == 5
    if fmt == "links":
        assert result.content.splitlines() == ["- kept link (https://example.test/kept)"]
        return
    # The visible content is unchanged by the filtering.
    assert "Visible heading" in result.content
    assert "visible outer" in result.content
    assert "visible tail" in result.content
    assert "aria false visible" in result.content
    assert "zerowidthtextplusbomend" in result.content


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [
        (
            "text",
            "Visible heading\nVisible kept link text.\nvisible outer\naria false visible\n"
            "zerowidthtextplusbomend\nvisible tail\ncustom property visible\nurl style visible\n"
            "content style visible\nlast declaration visible\nClosed details summary\n"
            "Open details summary\nopen details body",
        ),
        (
            "markdown",
            "# Visible heading\n\nVisible [kept link](https://example.test/kept) text.\n\nvisible outer\n\n"
            "aria false visible\n\nzerowidthtextplusbomend\n\nvisible tail\n\ncustom property visible\n\n"
            "url style visible\n\ncontent style visible\n\nlast declaration visible\n\n"
            "Closed details summary\n\nOpen details summary\n\nopen details body",
        ),
    ],
)
def test_hidden_fixture_text_is_byte_identical_to_the_visible_only_render(fmt: str, expected: str) -> None:
    filtered = _extraction(HIDDEN_FIXTURE.read_text(encoding="utf-8"), fmt=fmt, main=True)

    assert filtered.content == expected


def test_visible_inside_hidden_and_hidden_inside_visible_are_both_handled() -> None:
    hidden_outer = "<div hidden><p>outer secret</p><p>visible inside hidden secret</p></div><p>kept</p>"
    hidden_inner = "<div><p>kept outer</p><div hidden><p>inner secret</p></div></div>"

    outer = _extraction(hidden_outer, fmt="text")
    inner = _extraction(hidden_inner, fmt="text")

    assert outer.content == "kept"
    assert outer.hidden_removed == 1  # the whole subtree counted as one removed element
    assert inner.content == "kept outer"
    assert inner.hidden_removed == 1


def test_zero_width_characters_are_stripped_and_counted_in_every_format() -> None:
    source = "<p>zero\u200bwidth\u200ctext\u200dU+2060\ufeffhere</p><p><a href='/x'>\u200b</a></p>"

    for fmt in ("text", "markdown", "links"):
        result = _extraction(source, fmt=fmt)
        assert not any(character in result.content for character in ZERO_WIDTH), fmt
        assert result.zero_width_stripped == 5, fmt


def test_hidden_guards_accept_case_and_space_variants_and_only_true_aria_hidden_is_hidden() -> None:
    cases = {
        "<p hidden>gone</p>": (True, "gone"),
        "<p aria-hidden=' true '>gone</p>": (True, "gone"),
        "<p aria-hidden='TRUE'>gone</p>": (True, "gone"),
        "<p aria-hidden='false'>kept</p>": (False, "kept"),
        "<p aria-hidden='1'>kept</p>": (False, "kept"),
        "<p style='display:none'>gone</p>": (True, "gone"),
        "<p style='display : none'>gone</p>": (True, "gone"),
        "<p style='DISPLAY:NONE'>gone</p>": (True, "gone"),
        "<p style='visibility:hidden'>gone</p>": (True, "gone"),
        "<p style='visibility: HIDDEN'>gone</p>": (True, "gone"),
        "<p style='display:block'>kept</p>": (False, "kept"),
        "<p style='visibility:visible'>kept</p>": (False, "kept"),
        "<p hidden='until-found'>gone</p>": (True, "gone"),
        "<p ARIA-HIDDEN='&#x74;RuE'>gone</p>": (True, "gone"),
    }
    for source, (hidden, text) in cases.items():
        result = _extraction(source, fmt="text")
        assert result.hidden_removed == (1 if hidden else 0), source
        assert result.content == ("" if hidden else text), source


def test_inline_style_parsing_uses_declarations_comments_and_priority() -> None:
    cases = {
        '<p style="display: /* c */ none">gone</p>': (True, "gone"),
        '<p style="DISPLAY/**/:NONE">gone</p>': (True, "gone"),
        '<p style="display:none !important">gone</p>': (True, "gone"),
        '<p style="display:none;">gone</p>': (True, "gone"),
        '<p style="display:none !invalid">kept</p>': (False, "kept"),
        '<p style="display:none !important;display:block">gone</p>': (True, "gone"),
        '<p style="display:block;display:none">gone</p>': (True, "gone"),
        '<p style="display:none;display:block">kept</p>': (False, "kept"),
        '<p style="display:none;display:block !important">kept</p>': (False, "kept"),
        '<p style="display:none !important;display:block !important">kept</p>': (False, "kept"),
        '<p style="--note: display:none">kept</p>': (False, "kept"),
        "<p style='background:url(\"display:none\")'>kept</p>": (False, "kept"),
        "<p style='content:\"visibility:hidden\"'>kept</p>": (False, "kept"),
        "<p style='content:\"display:none;visibility:hidden\";display:block'>kept</p>": (False, "kept"),
        r"""<p style='content:"escaped \";visibility:hidden";display:block'>kept</p>""": (False, "kept"),
        r'<p style="d\69splay:none">gone</p>': (True, "gone"),
        r'<p style="display:\6e one">gone</p>': (True, "gone"),
        r'<p style="display:\6e one !\69mportant;display:block">gone</p>': (True, "gone"),
        r'<p style="display:\62 lock">kept</p>': (False, "kept"),
        r"""<p style="content:'d\69splay:none'">kept</p>""": (False, "kept"),
        r'<p style="a:\(;display:none;b:\)">gone</p>': (True, "gone"),
        r'<p style="a:\(;display:none">gone</p>': (True, "gone"),
        r'''<p style='a:\";display:none;b:\"'>gone</p>''': (True, "gone"),
        r'''<p style="a:\';display:none;b:\'">gone</p>''': (True, "gone"),
        r'<p style="x\(:1;display:none">gone</p>': (True, "gone"),
        "<p style='display:none;content:\"unfinished'>gone</p>": (True, "gone"),
        '<p style="background:url(unclosed;display:none">kept</p>': (False, "kept"),
        '<p style="background:close);display:none">gone</p>': (True, "gone"),
        '<p style="display:none; /* unfinished">gone</p>': (True, "gone"),
        '<p style="; : none">kept</p>': (False, "kept"),
        '<p style="display:none;broken">gone</p>': (True, "gone"),
    }
    for source, (hidden, expected) in cases.items():
        result = _extraction(source, fmt="text")
        assert result.content == ("" if hidden else expected), source
        assert result.hidden_removed == (1 if hidden else 0), source


@pytest.mark.parametrize(
    ("style", "expected_parts", "expected_declarations"),
    [
        pytest.param(
            r"a:\(;display:none;b:\)",
            [r"a:\(", "display:none", r"b:\)"],
            {"a": "(", "display": "none", "b": ")"},
            id="escaped-parentheses",
        ),
        pytest.param(
            r'a:\";display:none;b:\"',
            [r'a:\"', "display:none", r'b:\"'],
            {"a": '"', "display": "none", "b": '"'},
            id="escaped-double-quotes",
        ),
        pytest.param(
            r"a:\';display:none;b:\'",
            [r"a:\'", "display:none", r"b:\'"],
            {"a": "'", "display": "none", "b": "'"},
            id="escaped-single-quotes",
        ),
    ],
)
def test_css_scanners_keep_escaped_structure_as_data(
    style: str,
    expected_parts: list[str],
    expected_declarations: dict[str, str],
) -> None:
    from pwmcp_client.page_content import _split_css, _style_declarations, _strip_css_comments

    assert _strip_css_comments(style) == style
    assert _split_css(style, ";") == expected_parts
    assert _style_declarations(style) == expected_declarations


@pytest.mark.parametrize(
    ("style", "hidden"),
    [
        pytest.param('display:none;content:"unfinished', True, id="reviewer-unclosed-string"),
        pytest.param("display:none;color:red;)", True, id="reviewer-stray-close-after-hide"),
        pytest.param("display:none;/* unfinished", True, id="reviewer-unclosed-comment-after-hide"),
        pytest.param("/* unfinished; display:none", True, id="unclosed-comment-before-hide"),
        pytest.param('content:"unfinished; display:none', False, id="semicolon-inside-unclosed-string"),
        pytest.param(r'content:"unfinished\"; display:none', False, id="escaped-quote-in-unclosed-string"),
        pytest.param('content:"a\n;display:none;color:red', True, id="bad-string-lf-then-hide"),
        pytest.param('content:"a\r\n;display:none', True, id="bad-string-crlf-then-hide"),
        pytest.param('content:"a\f;display:none', True, id="bad-string-ff-then-hide"),
        pytest.param("content:'a\n;visibility:hidden", True, id="single-quote-bad-string-lf-then-hide"),
        pytest.param("content:'a\r\n;display:none", True, id="single-quote-bad-string-crlf-then-hide"),
        pytest.param("content:'a\f;display:none", True, id="single-quote-bad-string-ff-then-hide"),
        pytest.param('display:none"a\n', True, id="bad-string-fragment-hide"),
        pytest.param('color:rgb("a\n;display:none', False, id="bad-string-inside-unclosed-function-visible"),
        pytest.param('display:none("a\n', True, id="bad-string-before-unclosed-function-hide"),
        pytest.param('color:rgb("a\n"b\n;display:none', False, id="later-bad-string-in-function-visible"),
        pytest.param('display:none("unfinished', True, id="eof-bad-string-after-hide-in-function"),
        pytest.param('color:rgb("a\n"unfinished', False, id="eof-bad-string-after-prior-function-fragment"),
        pytest.param('content:"a\\\n;display:none', False, id="escaped-lf-continues-string"),
        pytest.param('content:"a\\\r\n;display:none', False, id="escaped-crlf-continues-string"),
        pytest.param("color:red;);display:none", True, id="stray-close-before-independent-hide"),
        pytest.param("display/**/:none;broken", True, id="closed-comment-before-malformation"),
        pytest.param("/* unfinished; background:url(unclosed", False, id="comment-fragment-without-hide"),
        pytest.param("/* unfinished /* nested", False, id="nested-unclosed-comment-control"),
        pytest.param(
            'background:var(--note, url("data:text/plain;(x:y)"));display:none;content:"unfinished',
            True,
            id="nested-parentheses-and-quoted-semicolon",
        ),
        pytest.param(
            "display:none;background:var(--note, calc(1 + 2)",
            True,
            id="unclosed-nested-parenthesis-after-hide",
        ),
        pytest.param(
            "DISPLAY : NONE !ImPoRtAnT ; color:red;)",
            True,
            id="uppercase-important-whitespace",
        ),
        pytest.param("color:red;visibility:hidden)", True, id="hide-signal-in-malformed-fragment"),
        pytest.param(r"a:\(;display:none;b:\);broken", True, id="recovery-skips-escaped-parens"),
        pytest.param(r'a:\";display:none;b:\";broken', True, id="recovery-skips-escaped-double-quotes"),
        pytest.param(r"a:\';display:none;b:\';broken", True, id="recovery-skips-escaped-single-quotes"),
        pytest.param(r"x\(:1;display:none;broken", True, id="recovery-skips-escaped-property-paren"),
        pytest.param(r"a:\;;display:none;broken", True, id="recovery-skips-escaped-semicolon"),
        pytest.param("display:none" + "\\", False, id="recovery-trailing-escape-control"),
        pytest.param('display:none"x', True, id="unterminated-quote-fragment-hide"),
        pytest.param("display:none(x", True, id="unclosed-paren-fragment-hide"),
        pytest.param("display:none !important;display:block;broken", True, id="recovered-important-wins"),
        pytest.param("display:none;display:block !important;broken", False, id="recovered-later-important-wins"),
        pytest.param("display:none;display:block;broken", False, id="valid-cascade-before-malformation"),
        pytest.param("display:block;color:red;broken", False, id="malformed-visible-control"),
    ],
)
def test_malformed_inline_css_fails_closed_only_when_it_has_a_hiding_signal(
    style: str,
    hidden: bool,
) -> None:
    result = _extraction(f'<p style="{escape(style, quote=True)}">content</p>', fmt="text")

    assert result.content == ("" if hidden else "content"), style
    assert result.hidden_removed == (1 if hidden else 0), style


def test_bad_string_fragment_checks_have_linear_work(monkeypatch) -> None:
    from pwmcp_client import page_content

    style = ('content:"x\n' * 512) + ";display:none"
    fragment_characters = 0

    def count_fragment(fragment: str) -> bool:
        nonlocal fragment_characters
        fragment_characters += len(fragment)
        return False

    monkeypatch.setattr(page_content, "_contains_hiding_declaration", count_fragment)
    recovered, malformed_hiding_signal = page_content._recover_malformed_style(style)

    assert page_content._declarations_hide(recovered)
    assert not malformed_hiding_signal
    assert fragment_characters <= 3 * len(style)


def test_malformed_hidden_style_on_parent_removes_its_child_subtree() -> None:
    source = (
        '<div style=\'visibility:hidden;content:"unfinished\'>'
        "<p>child hidden</p></div><p>outside</p>"
    )

    result = _extraction(source, fmt="text")

    assert result.content == "outside"
    assert result.hidden_removed == 1


@pytest.mark.parametrize(
    ("style", "hidden"),
    [
        pytest.param("dis/**/play:n/**/o/**/ne", True, id="comments-inside-property-and-value"),
        pytest.param(
            r'content:"\"escaped";display:none',
            True,
            id="escaped-quote-as-first-string-character",
        ),
        pytest.param(
            r'content:"escaped \"; still string";display:none',
            True,
            id="escaped-quote-before-semicolon",
        ),
        pytest.param(
            'background:url("data:text/plain; (x:y)");visibility:hidden',
            True,
            id="semicolon-and-nested-parentheses-in-string",
        ),
        pytest.param(
            'display:none;content:"x/* y */"',
            True,
            id="comment-markers-inside-string-after-hidden-declaration",
        ),
        pytest.param(
            'content:"display:none; visibility:hidden";display:block',
            False,
            id="hidden-declarations-inside-string",
        ),
        pytest.param(
            'background:url("data:text/plain; (display:none)")',
            False,
            id="hidden-token-inside-url-string",
        ),
    ],
)
def test_inline_style_lexing_preserves_hidden_declarations_across_strings_and_comments(
    style: str,
    hidden: bool,
) -> None:
    result = _extraction(f"<p style='{style}'>content</p>", fmt="text")

    assert result.content == ("" if hidden else "content"), style
    assert result.hidden_removed == (1 if hidden else 0), style


@pytest.mark.parametrize(
    ("style", "hidden"),
    [
        pytest.param("display:none!important", True, id="adjacent-important"),
        pytest.param("display:block;display:none !ImPoRtAnT", True, id="case-insensitive-important"),
        pytest.param("display:none ! important;display:block", True, id="important-whitespace-and-priority"),
        pytest.param("display:none !/**/important;display:block", True, id="important-comment"),
        pytest.param("display:none;display:block!important", False, id="later-important-visible"),
        pytest.param("display:none!important;display:block!important", False, id="later-equal-priority-wins"),
    ],
)
def test_inline_style_important_case_and_placement_follow_css_priority(
    style: str,
    hidden: bool,
) -> None:
    result = _extraction(f"<p style='{style}'>content</p>", fmt="text")

    assert result.content == ("" if hidden else "content"), style
    assert result.hidden_removed == (1 if hidden else 0), style


def test_inline_style_priority_marker_at_value_start_is_parsed_as_empty_important_value() -> None:
    from pwmcp_client.page_content import _priority_value

    assert _priority_value("!important") == ("", True)


def test_priority_value_preserves_escaped_quote_before_important_suffix() -> None:
    from pwmcp_client.page_content import _priority_value

    value = r'"\""!important'

    assert _priority_value(value) == (r'"\""', True)


@pytest.mark.parametrize(
    "style",
    [
        pytest.param('display:none;content:"unfinished', id="unclosed-quote"),
        pytest.param('content:"a\n;display:none', id="bad-string-lf"),
        pytest.param('content:"a\r\n;display:none', id="bad-string-crlf"),
        pytest.param('content:"a\f;display:none', id="bad-string-ff"),
        pytest.param("display\\:none", id="invalid-property-escape"),
        pytest.param("display:none" + "\\", id="invalid-value-escape"),
        pytest.param("background:url(unclosed;display:none", id="unclosed-parenthesis"),
        pytest.param("background:close);display:none", id="unmatched-parenthesis"),
        pytest.param("display:none;/* unfinished", id="unclosed-comment"),
    ],
)
def test_malformed_inline_css_is_rejected_as_a_whole(style: str) -> None:
    from pwmcp_client.page_content import _style_declarations

    assert _style_declarations(style) is None


def test_css_split_rejects_an_unclosed_quoted_string() -> None:
    from pwmcp_client.page_content import _split_css

    assert _split_css('content:"unfinished', ";") is None


@pytest.mark.parametrize(
    "style",
    [
        pytest.param('content:"a\n;display:none', id="bad-string-lf"),
        pytest.param('content:"a\r\n;display:none', id="bad-string-crlf"),
        pytest.param('content:"a\f;display:none', id="bad-string-ff"),
    ],
)
def test_css_split_rejects_unescaped_newline_in_string(style: str) -> None:
    from pwmcp_client.page_content import _split_css

    assert _split_css(style, ";") is None


def test_css_split_keeps_escaped_crlf_inside_a_string() -> None:
    from pwmcp_client.page_content import _split_css

    first = 'content:"a' + "\\" + "\r\n;b" + '"'

    assert _split_css(first + ";display:none", ";") == [first, "display:none"]


@pytest.mark.parametrize(
    "style",
    [
        pytest.param('content:"a\n;display:none', id="bad-string-lf"),
        pytest.param('content:"a\r\n;display:none', id="bad-string-crlf"),
        pytest.param('content:"a\f;display:none', id="bad-string-ff"),
    ],
)
def test_css_comment_stripper_rejects_unescaped_newline_in_string(style: str) -> None:
    from pwmcp_client.page_content import _strip_css_comments

    assert _strip_css_comments(style) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(r"d\69splay", "display", id="hex-escape"),
        pytest.param(r"d\isplay", "display", id="simple-escape"),
        pytest.param(r"\6e one", "none", id="hex-escape-consumes-space"),
        pytest.param("\\6e\r\none", "none", id="hex-escape-consumes-crlf"),
        pytest.param(r"\0000690", "i0", id="hex-escape-stops-after-six-digits"),
        pytest.param(r'"a\"b"', r'"a\"b"', id="string-escapes-are-preserved"),
        pytest.param(r"\0", "\ufffd", id="nul-becomes-replacement"),
        pytest.param(r"\d800", "\ufffd", id="surrogate-becomes-replacement"),
        pytest.param(r"\ffffff", "\ufffd", id="out-of-range-becomes-replacement"),
        pytest.param("a\\", None, id="trailing-backslash-is-invalid"),
        pytest.param('"unterminated', None, id="unclosed-string-is-invalid"),
        pytest.param('"a\\', None, id="trailing-backslash-in-string-is-invalid"),
        pytest.param("a\\\n", None, id="newline-escape-outside-string-is-invalid"),
        pytest.param("a\\\r\n", None, id="crlf-escape-outside-string-is-invalid"),
    ],
)
def test_css_escape_decoder(value: str, expected: str | None) -> None:
    from pwmcp_client.page_content import _decode_css_escapes

    assert _decode_css_escapes(value) == expected


def test_closed_details_keep_summary_and_remove_body_but_open_details_keep_body() -> None:
    closed = _extraction(
        "<details><summary>Summary</summary><p>closed secret</p></details>", fmt="text"
    )
    opened = _extraction(
        "<details open><summary>Summary</summary><p>open body</p></details>", fmt="text"
    )

    assert closed.content == "Summary"
    assert closed.hidden_removed == 1
    assert opened.content == "Summary\nopen body"
    assert opened.hidden_removed == 0


def test_closed_details_without_a_summary_remove_the_entire_body() -> None:
    result = _extraction("<details><p>closed secret</p>direct secret</details>", fmt="text")

    assert result.content == ""
    assert result.hidden_removed == 1


def test_closed_details_keep_only_the_first_summary() -> None:
    result = _extraction(
        "<details><summary>visible summary</summary><summary>closed body summary</summary>"
        "<p>closed body</p></details>",
        fmt="text",
    )

    assert result.content == "visible summary"
    assert result.hidden_removed == 1


def test_closed_details_count_hidden_summary_and_direct_text_body() -> None:
    result = _extraction(
        "<details><summary hidden>hidden summary</summary>direct hidden body</details>", fmt="text"
    )

    assert result.content == ""
    assert result.hidden_removed == 2


def test_closed_details_with_no_body_does_not_report_a_removal() -> None:
    result = _extraction("<details><summary>Summary only</summary></details>", fmt="text")

    assert result.content == "Summary only"
    assert result.hidden_removed == 0


def test_closed_details_whitespace_only_body_does_not_report_a_removal() -> None:
    result = _extraction("<details><summary>Summary</summary>  \n \t</details>", fmt="text")

    assert result.content == "Summary"
    assert result.hidden_removed == 0


def test_closed_details_remove_each_top_level_body_element_as_one_region() -> None:
    result = _extraction(
        "<details><summary>Summary</summary><p>first body</p><p>second body</p></details>", fmt="text"
    )

    assert result.content == "Summary"
    assert result.hidden_removed == 1


def test_hidden_input_type_is_case_insensitive_and_counted() -> None:
    hidden = _extraction('<input type="HiDdEn" value="input secret"><p>kept</p>', fmt="text")
    visible = _extraction('<input type="text" value="not rendered"><p>kept</p>', fmt="text")

    assert hidden.content == "kept"
    assert hidden.hidden_removed == 1
    assert visible.content == "kept"
    assert visible.hidden_removed == 0


def test_invisible_bidi_and_tag_characters_are_stripped_and_counted() -> None:
    invisible = "".join(chr(value) for value in (*range(0x202A, 0x202F), *range(0x2066, 0x206A), *range(0xE0000, 0xE0080)))
    result = _extraction(f"<p>safe{invisible}text</p>", fmt="text")

    assert result.content == "safetext"
    assert result.zero_width_stripped == len(invisible)


def test_stripping_zwj_can_separate_a_joined_emoji() -> None:
    result = _extraction("<p>👩‍💻</p>", fmt="text")

    assert result.content == "👩💻"
    assert result.zero_width_stripped == 1


def test_hidden_filtering_does_not_disturb_structures_it_does_not_touch() -> None:
    source = (
        "<ul><li>one</li><li hidden>gone</li><li>two</li></ul>"
        "<table><tr><td hidden>gone</td><td>cell</td></tr></table>"
        "<pre>kept\n  indented</pre>"
    )

    assert _extraction(source, fmt="text").content == "one\ntwo\ncell\nkept\n  indented"
    assert _extraction(source, fmt="markdown").content == "- one\n- two\n\ncell\n\n```\nkept\n  indented\n```"



def test_a_hidden_main_region_is_never_selected_and_zero_width_labels_keep_their_href() -> None:
    source = "<main hidden><p>secret region</p></main><main><p>kept region</p></main>"
    assert _extraction(source, fmt="text", main=True).content == "kept region"

    label_only_invisible = "<a href='/target'>\u200b\u200c</a>"
    result = _extraction(label_only_invisible, fmt="links")
    assert result.content == "- https://example.test/target (https://example.test/target)"
    assert result.zero_width_stripped == 2


def test_hidden_pruning_counts_one_per_hidden_element_not_per_descendant() -> None:
    source = "<div hidden><p>a secret</p><ul><li>b secret</li></ul></div><span>kept</span>"

    result = _extraction(source, fmt="text")

    assert result.content == "kept"
    assert result.hidden_removed == 1
