"""Deterministic text, Markdown, and link extraction from rendered page HTML."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import cast
from urllib.parse import urljoin


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    children: list[_Node | str] = field(default_factory=list)


@dataclass(frozen=True)
class Extraction:
    """Rendered text plus what was removed to keep it agent-safe (PWMCP-50)."""

    content: str
    hidden_removed: int = 0
    zero_width_stripped: int = 0


_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr",
})
_BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "details", "div", "dl", "dt", "dd", "fieldset", "figcaption",
    "figure", "summary",
    "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol",
    "option", "p", "pre", "section", "select", "table", "ul",
})
_IGNORED_TAGS = frozenset({"script", "style", "noscript", "template"})
_PAGE_CHROME_TAGS = frozenset({"header", "nav", "footer"})
_ZERO_WIDTH = re.compile("[\u200b-\u200d\u202a-\u202e\u2060\u2066-\u2069\ufeff\U000e0000-\U000e007f]")
_UNRESOLVED_CSS_VALUE = "<pwmcp-unresolved-css-value>"
_CSS_WHITESPACE = " \t\n\r\f"
_CSS_WHITESPACE_RUN = re.compile(r"[ \t\n\r\f]+")
# CSS keywords and property names fold ASCII case only. Unicode lowercasing
# maps U+212A KELVIN SIGN to ASCII "k".
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
_DISPLAY_OUTSIDE = frozenset({"block", "inline"})
_DISPLAY_INSIDE = frozenset({"flow", "flow-root", "table", "flex", "grid", "ruby"})
_DISPLAY_LIST_ITEM_INSIDE = frozenset({"flow", "flow-root"})
_DISPLAY_INTERNAL = frozenset({
    "table-row-group", "table-header-group", "table-footer-group", "table-row", "table-cell",
    "table-column-group", "table-column", "table-caption",
})
_DISPLAY_SINGLE = frozenset({
    "none", "contents", "list-item", "inline-block", "inline-table", "inline-flex", "inline-grid",
}) | _DISPLAY_OUTSIDE | _DISPLAY_INSIDE | _DISPLAY_INTERNAL
# Static hiding only: a CSS class that computes to display:none cannot be read from HTML.


class _TreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag.lower(), {name.lower(): value or "" for name, value in attrs})
        self.stack[-1].children.append(node)
        if node.tag not in _VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.stack[-1].children.append(
            _Node(tag.lower(), {name.lower(): value or "" for name, value in attrs})
        )

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == normalized:
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        self.stack[-1].children.append(data)


def _walk(node: _Node):
    for child in node.children:
        if isinstance(child, _Node):
            yield child
            yield from _walk(child)


def _strip_css_comments(style: str) -> str | None:
    """Remove CSS comments outside strings, returning ``None`` for malformed input."""

    result: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(style):
        char = style[index]
        if quote is not None:
            result.append(char)
            if escaped:
                escaped = False
                if char == "\r" and index + 1 < len(style) and style[index + 1] == "\n":
                    result.append("\n")
                    index += 1
            elif char == "\\":
                escaped = True
            elif char in "\r\n\f":
                return None
            elif char == quote:
                quote = None
            index += 1
            continue
        if style.startswith("/*", index):
            end = style.find("*/", index + 2)
            if end < 0:
                return None
            index = end + 2
            continue
        if char == "\\":
            result.append(char)
            if index + 1 < len(style):
                result.append(style[index + 1])
                index += 2
            else:
                index += 1
            continue
        if char in {'"', "'"}:
            quote = char
        result.append(char)
        index += 1
    if quote is not None or escaped:
        return None
    return "".join(result)


def _split_css(value: str, delimiter: str) -> list[str] | None:
    """Split CSS tokens only outside strings and parentheses; reject malformed input."""

    parts: list[str] = []
    quote: str | None = None
    escaped = False
    depth = 0
    start = 0
    index = 0
    while index < len(value):
        char = value[index]
        if quote is not None:
            if escaped:
                escaped = False
                if char == "\r" and index + 1 < len(value) and value[index + 1] == "\n":
                    index += 1
            elif char == "\\":
                escaped = True
            elif char in "\r\n\f":
                return None
            elif char == quote:
                quote = None
        elif char in {'"', "'"}:
            quote = char
        elif char == "\\":
            index += 1
        elif char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                return None
            depth -= 1
        elif depth == 0 and char == delimiter:
            parts.append(value[start:index])
            start = index + 1
        index += 1
    if quote is not None or escaped or depth:
        return None
    parts.append(value[start:])
    return parts


def _decode_css_escapes(value: str) -> str | None:
    """Decode CSS escapes outside strings, returning ``None`` for invalid escapes."""

    result: list[str] = []
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(value):
        char = value[index]
        if quote is not None:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in {'"', "'"}:
            quote = char
            result.append(char)
            index += 1
            continue
        if char != "\\":
            result.append(char)
            index += 1
            continue

        index += 1
        if index >= len(value) or value[index] in "\r\n\f":
            return None
        if value[index] in "0123456789abcdefABCDEF":
            hex_start = index
            while index < len(value) and index - hex_start < 6 and value[index] in "0123456789abcdefABCDEF":
                index += 1
            codepoint = int(value[hex_start:index], 16)
            if codepoint == 0 or codepoint > 0x10FFFF or 0xD800 <= codepoint <= 0xDFFF:
                codepoint = 0xFFFD
            result.append(chr(codepoint))
            if index < len(value) and value[index] in " \t\r\n\f":
                if value[index] == "\r" and index + 1 < len(value) and value[index + 1] == "\n":
                    index += 1
                index += 1
            continue
        result.append(value[index])
        index += 1
    if quote is not None or escaped:
        return None
    return "".join(result)


def _strip_css_whitespace(value: str) -> str:
    """Trim CSS Syntax whitespace without treating Unicode separators as whitespace."""

    return value.strip(_CSS_WHITESPACE)


def _split_css_whitespace(value: str) -> list[str]:
    """Split CSS keywords on CSS Syntax whitespace only."""

    stripped = _strip_css_whitespace(value)
    return _CSS_WHITESPACE_RUN.split(stripped) if stripped else []


def _priority_value(value: str) -> tuple[str, bool]:
    """Remove a trailing top-level ``!important`` from an already validated value."""

    quote: str | None = None
    escaped = False
    depth = 0
    bang = -1
    for index, char in enumerate(value):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif quote is not None:
            if char == quote:
                quote = None
        elif char in {'"', "'"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif depth == 0 and char == "!":
            bang = index
    if bang < 0:
        return _strip_css_whitespace(value), False
    priority = _decode_css_escapes(_strip_css_whitespace(value[bang + 1 :]))
    if priority is None or priority.translate(_ASCII_LOWER) != "important":
        return _strip_css_whitespace(value), False
    return _strip_css_whitespace(value[:bang]), True


def _display_value_is_valid(value: str) -> bool:
    """Accept the static single and multi-keyword values in CSS Display 3."""

    words = _split_css_whitespace(value)
    if len(words) == 1:
        return words[0] in _DISPLAY_SINGLE
    if len(set(words)) != len(words):
        return False
    if len(words) == 2:
        pair = set(words)
        return (
            (len(pair & _DISPLAY_OUTSIDE) == 1 and len(pair & _DISPLAY_INSIDE) == 1)
            or (
                "list-item" in pair
                and len(pair & (_DISPLAY_OUTSIDE | _DISPLAY_LIST_ITEM_INSIDE)) == 1
            )
        )
    if len(words) != 3:
        return False
    word_set = set(words)
    expected = (
        {"list-item"}
        | (word_set & _DISPLAY_OUTSIDE)
        | (word_set & _DISPLAY_LIST_ITEM_INSIDE)
    )
    return (
        "list-item" in word_set
        and word_set == expected
        and len(word_set & _DISPLAY_OUTSIDE) == 1
        and len(word_set & _DISPLAY_LIST_ITEM_INSIDE) == 1
    )


def _display_property_value(value: str) -> str | None:
    """Keep valid static display values; fail closed when computation is unknown."""

    if value in {"initial", "unset"}:
        return "inline"
    if value in {"inherit", "revert", "revert-layer"} or (
        value.startswith("var(") and value.endswith(")")
    ):
        return _UNRESOLVED_CSS_VALUE
    return value if _display_value_is_valid(value) else None


def _visibility_property_value(value: str) -> str | None:
    """Keep valid static visibility values; fail closed when computation is unknown."""

    if value == "initial":
        return "visible"
    if value in {"inherit", "unset", "revert", "revert-layer"} or (
        value.startswith("var(") and value.endswith(")")
    ):
        return _UNRESOLVED_CSS_VALUE
    if value == "collapse":
        return "hidden"
    return value if value in {"visible", "hidden"} else None


def _style_declarations(style: str) -> dict[str, str] | None:
    """Parse inline declarations, retaining the winning value for each property."""

    uncommented = _strip_css_comments(style)
    if uncommented is None:
        return None
    declarations = _split_css(uncommented, ";")
    if declarations is None:
        return None
    winners: dict[str, tuple[bool, str]] = {}
    for declaration in declarations:
        if not _strip_css_whitespace(declaration):
            continue
        property_name, separator, value = declaration.partition(":")
        if not separator:
            return None
        property_name = _decode_css_escapes(_strip_css_whitespace(property_name))
        if property_name is None:
            return None
        property_name = property_name.translate(_ASCII_LOWER)
        if not property_name:
            return None
        value, important = _priority_value(value)
        value = _decode_css_escapes(value)
        if value is None:
            return None
        value = value.translate(_ASCII_LOWER)
        if property_name == "display":
            value = _display_property_value(value)
            if value is None:
                continue
        elif property_name == "visibility":
            value = _visibility_property_value(value)
            if value is None:
                continue
        previous = winners.get(property_name)
        if previous is None or important or not previous[0]:
            winners[property_name] = (important, value)
    return {name: value for name, (_important, value) in winners.items()}


def _declarations_hide(declarations: dict[str, str]) -> bool:
    return (
        declarations.get("display") in {"none", _UNRESOLVED_CSS_VALUE}
        or declarations.get("visibility") in {"hidden", _UNRESOLVED_CSS_VALUE}
    )


def _contains_hiding_declaration(fragment: str) -> bool:
    """Find an existing hidden marker in a best-effort, independently parsed fragment."""

    uncommented = _strip_css_comments(fragment)
    if uncommented is None:
        return False
    declarations = _split_css(uncommented, ";")
    if declarations is None:
        return False
    for declaration in declarations:
        if not _strip_css_whitespace(declaration):
            continue
        parsed = _style_declarations(declaration)
        if parsed is not None and _declarations_hide(parsed):
            return True
    return False


def _append_recovered_declaration(segment: str, recovered: list[str]) -> bool:
    """Keep one complete valid declaration, reporting a hiding signal if malformed."""

    if not _strip_css_whitespace(segment):
        return False
    if _style_declarations(segment) is None:
        return _contains_hiding_declaration(segment)
    recovered.append(segment)
    return False


def _recover_malformed_style(style: str) -> tuple[dict[str, str], bool]:
    """Recover independent complete declarations and hiding signals from malformed CSS."""

    recovered: list[str] = []
    malformed_hiding_signal = False
    start = 0
    fragment_start = 0
    index = 0
    quote: str | None = None
    quote_start = -1
    escaped = False
    depth = 0
    paren_start = -1

    while index < len(style):
        char = style[index]
        if quote is not None:
            if escaped:
                escaped = False
                if char == "\r" and index + 1 < len(style) and style[index + 1] == "\n":
                    index += 1
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
                quote_start = -1
            elif char in "\r\n\f":
                if depth:
                    if paren_start >= fragment_start:
                        malformed_hiding_signal |= _contains_hiding_declaration(
                            style[fragment_start:paren_start]
                        )
                else:
                    malformed_hiding_signal |= _contains_hiding_declaration(
                        style[fragment_start:quote_start]
                    )
                quote = None
                quote_start = -1
                if char == "\r" and index + 1 < len(style) and style[index + 1] == "\n":
                    index += 1
                fragment_start = index + 1
            index += 1
            continue
        if style.startswith("/*", index):
            end = style.find("*/", index + 2)
            if end < 0:
                malformed_hiding_signal |= _contains_hiding_declaration(style[start:index])
                malformed_hiding_signal |= _contains_hiding_declaration(style[index + 2 :])
                break
            index = end + 2
            continue
        if char in {'"', "'"}:
            quote = char
            quote_start = index
        elif char == "(":
            if depth == 0:
                paren_start = index
            depth += 1
        elif char == ")":
            if depth:
                depth -= 1
                if depth == 0:
                    paren_start = -1
            else:
                malformed_hiding_signal |= _contains_hiding_declaration(style[start:index])
                start = index + 1
                fragment_start = start
        elif char == ";" and depth == 0:
            malformed_hiding_signal |= _append_recovered_declaration(style[start:index], recovered)
            start = index + 1
            fragment_start = start
        elif char == "\\":
            index += 1 if index + 1 >= len(style) else 2
            continue
        index += 1
    else:
        if quote is not None or escaped:
            if depth:
                if paren_start >= fragment_start:
                    malformed_hiding_signal |= _contains_hiding_declaration(
                        style[fragment_start:paren_start]
                    )
            else:
                malformed_hiding_signal |= _contains_hiding_declaration(
                    style[fragment_start:quote_start]
                )
        elif depth:
            if paren_start >= fragment_start:
                malformed_hiding_signal |= _contains_hiding_declaration(
                    style[fragment_start:paren_start]
                )
        else:
            malformed_hiding_signal |= _append_recovered_declaration(style[start:], recovered)

    # Every retained segment parsed strictly; joining at top-level boundaries preserves validity.
    declarations = cast(dict[str, str], _style_declarations(";".join(recovered)))
    return declarations, malformed_hiding_signal


def _style_is_hidden(style: str) -> bool:
    declarations = _style_declarations(style)
    if declarations is not None:
        return _declarations_hide(declarations)
    recovered, malformed_hiding_signal = _recover_malformed_style(style)
    return malformed_hiding_signal or _declarations_hide(recovered)


def _is_hidden(node: _Node) -> bool:
    """Static hiding as serialized: the attributes and inline style are what HTML carries.

    Computed hiding (a CSS class that renders to ``display:none``) cannot be read here and
    is a documented known limit (PWMCP-50): marking it needs a live browser to verify.
    """

    if "hidden" in node.attrs:
        return True
    if node.attrs.get("aria-hidden", "").strip().lower() == "true":
        return True
    if node.tag == "input" and node.attrs.get("type", "").lower() == "hidden":
        return True
    return _style_is_hidden(node.attrs.get("style", ""))


def _prune_hidden(node: _Node) -> tuple[_Node, int]:
    """Drop every hidden element with its whole subtree; return the kept tree and the count."""

    if node.tag == "details" and "open" not in node.attrs:
        kept: list[_Node | str] = []
        summary_found = False
        body_removed = False
        removed = 0
        for child in node.children:
            if isinstance(child, _Node) and child.tag == "summary" and not summary_found:
                summary_found = True
                if _is_hidden(child):
                    removed += 1
                else:
                    pruned, nested = _prune_hidden(child)
                    removed += nested
                    kept.append(pruned)
            elif isinstance(child, _Node):
                body_removed = True
            elif child.strip():
                body_removed = True
        if body_removed:
            removed += 1
        return _Node(node.tag, node.attrs, kept), removed

    kept: list[_Node | str] = []
    removed = 0
    for child in node.children:
        if isinstance(child, _Node):
            if _is_hidden(child):
                removed += 1
                continue
            pruned, nested = _prune_hidden(child)
            removed += nested
            kept.append(pruned)
        else:
            kept.append(child)
    return _Node(node.tag, node.attrs, kept), removed


def _strip_zero_width(node: _Node) -> tuple[_Node, int]:
    """Remove invisible U+200B..U+200D, U+2060 and U+FEFF from the kept text; return the count.

    Done on the tree, after hidden subtrees are gone, so the count is what the extractor
    actually dropped and a label made only of these characters still falls back to its href.
    """

    kept: list[_Node | str] = []
    removed = 0
    for child in node.children:
        if isinstance(child, _Node):
            cleaned, nested = _strip_zero_width(child)
            removed += nested
            kept.append(cleaned)
        else:
            cleaned, count = _ZERO_WIDTH.subn("", child)
            removed += count
            kept.append(cleaned)
    return _Node(node.tag, node.attrs, kept), removed


def _main_node(root: _Node) -> _Node:
    nodes = list(_walk(root))
    for tag, role in (("main", None), (None, "main"), ("article", None)):
        for node in nodes:
            if (tag is None and node.attrs.get("role", "").lower() == role) or node.tag == tag:
                return node
    return _without_page_chrome(root)


def _without_page_chrome(node: _Node) -> _Node:
    return _Node(
        node.tag,
        node.attrs,
        [
            _without_page_chrome(child) if isinstance(child, _Node) else child
            for child in node.children
            if not isinstance(child, _Node) or child.tag not in _PAGE_CHROME_TAGS
        ],
    )


def _selected(root: _Node, main: bool) -> _Node:
    return _main_node(root) if main else root


def _plain_parts(node: _Node) -> list[tuple[str, bool]]:
    """Return text chunks tagged with whether their whitespace is preformatted."""

    if node.tag in _IGNORED_TAGS:
        return []
    if node.tag == "br":
        return [("\n", False)]
    if node.tag == "pre":
        return [("\n", False), (_raw_text(node), True), ("\n", False)]
    if node.tag == "tr":
        cells = [
            child for child in node.children
            if isinstance(child, _Node) and child.tag in {"td", "th"}
        ]
        parts: list[tuple[str, bool]] = [("\n", False)]
        for index, cell in enumerate(cells):
            if index:
                parts.append((" ", False))
            parts.extend(_plain_parts(cell))
        parts.append(("\n", False))
        return parts

    is_block = node.tag in _BLOCK_TAGS
    parts = [("\n", False)] if is_block else []
    for child in node.children:
        if isinstance(child, _Node):
            parts.extend(_plain_parts(child))
        else:
            parts.append((re.sub(r"\s+", " ", child), False))
    if is_block:
        parts.append(("\n", False))
    return parts


def _plain(node: _Node) -> str:
    """Render text with one line boundary per block and verbatim ``pre`` chunks."""

    merged: list[tuple[str, bool]] = []
    for value, preformatted in _plain_parts(node):
        if merged and merged[-1][1] == preformatted:
            previous, _ = merged[-1]
            merged[-1] = (previous + value, preformatted)
        else:
            merged.append((value, preformatted))

    normalized: list[tuple[str, bool]] = []
    for value, preformatted in merged:
        if preformatted:
            normalized.append((value, True))
            continue
        value = re.sub(r"[\t\f\v ]+", " ", value)
        value = re.sub(r" *\n *", "\n", value)
        value = re.sub(r"\n+", "\n", value)
        normalized.append((value, False))
    # A pre chunk always has a normal-flow newline immediately before and after it.
    if normalized:
        value, _ = normalized[0]
        normalized[0] = (value.lstrip(), False)
        value, _ = normalized[-1]
        normalized[-1] = (value.rstrip(), False)
    return "".join(value for value, _ in normalized)


def _normalize_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[\t\f\v ]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _inline_text(children: list[_Node | str], base_url: str) -> str:
    parts: list[str] = []
    for child in children:
        if isinstance(child, str):
            parts.append(re.sub(r"\s+", " ", child))
            continue
        if child.tag in _IGNORED_TAGS:
            continue
        value = _inline_text(child.children, base_url)
        if child.tag == "br":
            value = "\n"
        elif child.tag == "a" and child.attrs.get("href"):
            href = urljoin(base_url, child.attrs["href"])
            safe_href = href.replace(")", "\\)")
            value = f"[{value or href}]({safe_href})"
        elif child.tag == "code":
            fence = "`" * (max((len(match.group(0)) for match in re.finditer(r"`+", value)), default=0) + 1)
            value = f"{fence}{value}{fence}"
        elif child.tag in {"strong", "b"}:
            value = f"**{value}**"
        elif child.tag in {"em", "i"}:
            value = f"*{value}*"
        elif child.tag in {"del", "s", "strike"}:
            value = f"~~{value}~~"
        if child.tag in _BLOCK_TAGS:
            # A block nested in an inline context (list item, cell, link) still ends the previous word.
            value = f" {value} "
        parts.append(value)
    return re.sub(r"[ \t]+", " ", "".join(parts)).strip()


def _raw_text(node: _Node) -> str:
    if node.tag in _IGNORED_TAGS:
        return ""
    return "".join(
        _raw_text(child) if isinstance(child, _Node) else child
        for child in node.children
    )


def _render_list(node: _Node, base_url: str, depth: int = 0) -> list[str]:
    ordered = node.tag == "ol"
    try:
        start = int(node.attrs.get("start", "1"))
    except ValueError:
        start = 1
    lines: list[str] = []
    item_number = start
    for item in (child for child in node.children if isinstance(child, _Node) and child.tag == "li"):
        lead_children: list[_Node | str] = []
        nested: list[_Node] = []
        for child in item.children:
            if isinstance(child, _Node) and child.tag in {"ul", "ol"}:
                nested.append(child)
                lead_children.append(" ")  # text around a nested list stays separate
            else:
                lead_children.append(child)
        lead = re.sub(r"\s+", " ", _inline_text(lead_children, base_url))
        marker = f"{item_number}. " if ordered else "- "
        prefix = "  " * depth
        lines.append(prefix + marker + lead)
        item_number += 1
        for child in nested:
            lines.extend(_render_list(child, base_url, depth + 1))
    return lines


def _table_rows(node: _Node):
    for child in node.children:
        if not isinstance(child, _Node):
            continue
        if child.tag == "tr":
            yield child
        elif child.tag != "table":
            yield from _table_rows(child)


def _render_table(node: _Node, base_url: str) -> list[str]:
    rows: list[str] = []
    for row in _table_rows(node):
        cells = [
            child for child in row.children
            if isinstance(child, _Node) and child.tag in {"td", "th"}
        ]
        rows.append(" | ".join(_inline_text(cell.children, base_url) for cell in cells))
    return rows


def _render_blocks(node: _Node, base_url: str) -> list[str]:
    blocks: list[str] = []
    inline: list[_Node | str] = []

    def flush_inline() -> None:
        value = _inline_text(inline, base_url)
        inline.clear()
        if value:
            blocks.append(value)

    for child in node.children:
        if isinstance(child, str):
            if child.strip():
                inline.append(child)
            continue
        if child.tag in _IGNORED_TAGS:
            continue
        if child.tag in _BLOCK_TAGS:
            flush_inline()
            if child.tag in {"ul", "ol"}:
                value = "\n".join(_render_list(child, base_url))
            elif child.tag == "table":
                value = "\n".join(_render_table(child, base_url))
            elif child.tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
                level = int(child.tag[1])
                value = "#" * level + " " + _inline_text(child.children, base_url)
            elif child.tag == "pre":
                code = _raw_text(child).strip("\n")
                longest = max((len(match.group(0)) for match in re.finditer(r"`+", code)), default=0)
                fence = "`" * max(3, longest + 1)
                value = f"{fence}\n{code}\n{fence}"
            elif child.tag == "blockquote":
                nested = "\n\n".join(_render_blocks(child, base_url))
                value = "\n".join("> " + line if line else ">" for line in nested.splitlines())
            elif child.tag == "hr":
                value = "---"
            elif child.tag in {"p", "dt", "dd", "figcaption"}:
                value = _inline_text(child.children, base_url)
            else:
                value = "\n\n".join(_render_blocks(child, base_url))
            if value:
                blocks.append(value)
        else:
            inline.append(child)
    flush_inline()
    return blocks


def _links(node: _Node, base_url: str) -> str:
    found: list[str] = []
    for child in _walk(node):
        if child.tag != "a" or not child.attrs.get("href"):
            continue
        href = urljoin(base_url, child.attrs["href"])
        label = _normalize_text(_plain(child)) or href
        found.append(f"- {label} ({href})")
    return "\n".join(found)


def extract_page(source: str, *, fmt: str, base_url: str, main: bool = False) -> Extraction:
    """Extract readable content plus the hidden and zero-width removals reported in diagnostics."""

    if fmt not in {"text", "markdown", "links"}:
        raise ValueError("format must be text, markdown, or links")
    parser = _TreeParser()
    parser.feed(source)
    parser.close()
    # Prune the whole document first, so a hidden ``main``/``article`` can never be selected
    # as the region, and the reported count does not depend on which format or region ran.
    root, hidden_removed = _prune_hidden(parser.root)
    root = _selected(root, main)
    root, zero_width_stripped = _strip_zero_width(root)
    if fmt == "text":
        rendered = _plain(root)
    elif fmt == "links":
        rendered = _links(root, base_url)
    else:
        rendered = "\n\n".join(block for block in _render_blocks(root, base_url) if block).strip()
    return Extraction(rendered, hidden_removed, zero_width_stripped)


def extract_content(source: str, *, fmt: str, base_url: str, main: bool = False) -> str:
    """Extract deterministic readable text, Markdown, or absolute links from HTML."""

    return extract_page(source, fmt=fmt, base_url=base_url, main=main).content
