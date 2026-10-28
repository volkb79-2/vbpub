"""User-facing usage docs keep their linked contracts and closed choices."""

from __future__ import annotations

import re
import urllib.parse
from pathlib import Path

from nyxloom.session_extract.usage.anchors import COVERAGE_ASSUMPTIONS, KINDS, SOURCES

PROJECT = Path(__file__).resolve().parents[1]
DOCS = {
    PROJECT / "README.md",
    PROJECT / "docs" / "DESIGN-GUIDE.md",
    PROJECT / "docs" / "CONSUMERS.md",
}


def _slug(heading: str) -> str:
    text = re.sub(r"`([^`]*)`", r"\1", heading)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[^\w -]", "", text.lower())
    return re.sub(r"\s+", "-", text.strip())


def _heading_ids(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    headings = re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, flags=re.MULTILINE)
    return {_slug(heading) for heading in headings}


def test_usage_docs_resolve_every_cross_document_anchor():
    unresolved: list[str] = []
    for source in DOCS:
        text = source.read_text(encoding="utf-8")
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
            target = target.split()[0]
            file_part, separator, fragment = target.partition("#")
            destination = (source.parent / file_part).resolve() if file_part else source.resolve()
            if destination not in DOCS or not separator:
                continue
            anchor = urllib.parse.unquote(fragment)
            if anchor not in _heading_ids(destination):
                unresolved.append(f"{source.relative_to(PROJECT)} -> {target}")

    assert unresolved == []


def test_usage_anchor_choice_values_are_documented():
    human_docs = "\n".join(path.read_text(encoding="utf-8") for path in DOCS)
    for choice in KINDS | SOURCES | COVERAGE_ASSUMPTIONS:
        assert f"`{choice}`" in human_docs


def test_every_usage_report_format_is_documented():
    human_docs = "\n".join(path.read_text(encoding="utf-8") for path in DOCS)
    for value in ("table", "json", "csv", "html", "pdf"):
        assert f"`{value}`" in human_docs
