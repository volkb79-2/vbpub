from __future__ import annotations

import re
import tomllib
from pathlib import Path
from urllib.parse import unquote, urlsplit

from cli_extended import load_cli_review_catalog
from cli_extended.review import (
    REVIEW_SCHEMA_VERSION,
    _REVIEW_CASE_STATES,
    _REVIEW_DECISIONS,
)

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_DOCS = (
    PACKAGE_ROOT / "README.md",
    PACKAGE_ROOT / "docs" / "DESIGN-GUIDE.md",
    PACKAGE_ROOT / "docs" / "CONSUMERS.md",
)
LINK_CHECK_DOCS = (*CANONICAL_DOCS, PACKAGE_ROOT / "BACKLOG.md")
FENCED_BLOCK = re.compile(r"^```([\w-]*)[^\n]*\n(.*?)^```\s*$", re.MULTILINE | re.DOTALL)
INLINE_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")
HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)


def _fenced_blocks(document: str) -> list[tuple[str, str]]:
    return FENCED_BLOCK.findall(document)


def _without_fenced_blocks(document: str) -> str:
    return FENCED_BLOCK.sub("", document)


def _heading_ids(document: str) -> set[str]:
    counts: dict[str, int] = {}
    identifiers: set[str] = set()
    for heading in HEADING.findall(_without_fenced_blocks(document)):
        text = re.sub(r"`([^`]*)`", r"\1", heading).lower()
        slug = re.sub(r"[^\w -]", "", text)
        slug = re.sub(r"\s+", "-", slug.strip())
        occurrence = counts.get(slug, 0)
        counts[slug] = occurrence + 1
        identifiers.add(slug if occurrence == 0 else f"{slug}-{occurrence}")
    return identifiers


def test_canonical_document_toml_examples_parse_and_review_catalogs_load(tmp_path):
    review_examples = 0
    for document_path in CANONICAL_DOCS:
        document = document_path.read_text(encoding="utf-8")
        for block_index, (language, source) in enumerate(_fenced_blocks(document)):
            if language.lower() != "toml":
                continue
            parsed = tomllib.loads(source)
            if "cli_id" not in parsed:
                continue

            review_examples += 1
            assert parsed.get("schema_version") == REVIEW_SCHEMA_VERSION, (
                f"{document_path} TOML block {block_index} must declare the current "
                "CLI review catalog schema"
            )
            catalog_path = tmp_path / f"{document_path.stem}-{block_index}.toml"
            catalog_path.write_text(source, encoding="utf-8")
            load_cli_review_catalog(catalog_path)

    assert review_examples, "canonical docs must include a loader-valid review catalog example"


def test_review_catalog_closed_vocabularies_are_documented():
    corpus = "\n".join(path.read_text(encoding="utf-8") for path in CANONICAL_DOCS)
    for value in (*_REVIEW_CASE_STATES, *_REVIEW_DECISIONS):
        assert f"`{value}`" in corpus, f"review catalog value {value!r} is undocumented"


def test_library_document_markdown_links_and_anchors_resolve():
    for source_path in LINK_CHECK_DOCS:
        source = _without_fenced_blocks(source_path.read_text(encoding="utf-8"))
        for match in INLINE_LINK.finditer(source):
            destination = match.group(1).split(maxsplit=1)[0].strip("<>")
            parsed = urlsplit(destination)
            if parsed.scheme or parsed.netloc:
                continue

            target_path = (
                (source_path.parent / unquote(parsed.path)).resolve()
                if parsed.path
                else source_path.resolve()
            )
            if not parsed.path and not parsed.fragment:
                continue
            assert target_path.is_file(), (
                f"{source_path} links to missing document {parsed.path!r}"
            )
            if parsed.fragment:
                target = target_path.read_text(encoding="utf-8")
                fragment = unquote(parsed.fragment)
                assert fragment in _heading_ids(target), (
                    f"{source_path} links to missing anchor #{fragment} in {target_path}"
                )
