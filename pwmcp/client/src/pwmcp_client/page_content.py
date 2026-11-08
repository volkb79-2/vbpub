"""Deterministic text, Markdown, and link extraction from rendered page HTML."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    children: list[_Node | str] = field(default_factory=list)


_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr",
})
_BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "div", "dl", "dt", "dd", "fieldset", "figcaption", "figure",
    "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "main", "nav", "ol", "p",
    "pre", "section", "table", "ul",
})
_IGNORED_TAGS = frozenset({"script", "style", "noscript", "template"})
_PAGE_CHROME_TAGS = frozenset({"header", "nav", "footer"})


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


def _plain(node: _Node, *, preformatted: bool = False) -> str:
    if node.tag in _IGNORED_TAGS:
        return ""
    if node.tag == "br":
        return "\n"
    if node.tag in _BLOCK_TAGS and node.tag != "pre":
        return "\n" + "".join(_plain(child, preformatted=preformatted) if isinstance(child, _Node) else child for child in node.children) + "\n"
    if node.tag == "pre":
        return "\n" + "".join(_plain(child, preformatted=True) if isinstance(child, _Node) else child for child in node.children) + "\n"
    return "".join(
        _plain(child, preformatted=preformatted) if isinstance(child, _Node) else child
        for child in node.children
    )


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
        lead_parts: list[str] = []
        nested: list[_Node] = []
        for child in item.children:
            if isinstance(child, _Node) and child.tag in {"ul", "ol"}:
                nested.append(child)
            elif isinstance(child, str):
                lead_parts.append(re.sub(r"\s+", " ", child))
            else:
                lead_parts.append(_inline_text([child], base_url))
        lead = re.sub(r"\s+", " ", "".join(lead_parts)).strip()
        marker = f"{item_number}. " if ordered else "- "
        prefix = "  " * depth
        lines.append(prefix + marker + lead)
        item_number += 1
        for child in nested:
            lines.extend(_render_list(child, base_url, depth + 1))
    return lines


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


def extract_content(source: str, *, fmt: str, base_url: str, main: bool = False) -> str:
    """Extract deterministic readable text, Markdown, or absolute links from HTML."""

    if fmt not in {"text", "markdown", "links"}:
        raise ValueError("format must be text, markdown, or links")
    parser = _TreeParser()
    parser.feed(source)
    parser.close()
    root = _selected(parser.root, main)
    if fmt == "text":
        return _normalize_text(_plain(root))
    if fmt == "links":
        return _links(root, base_url)
    return "\n\n".join(block for block in _render_blocks(root, base_url) if block).strip()
