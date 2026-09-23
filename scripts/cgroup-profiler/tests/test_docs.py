"""Mechanical checks for the daemon's three adopter-facing documents."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Iterable, List

import cgprofile as cg
from lib import serve, summary

ROOT = Path(__file__).resolve().parents[1]
DOCS = (ROOT / "README.md", ROOT / "docs" / "DESIGN-GUIDE.md", ROOT / "docs" / "CONSUMERS.md")


def _json_examples(path: Path) -> Iterable[Dict]:
    text = path.read_text(encoding="utf-8")
    for body in re.findall(r"```json\s*\n(.*?)```", text, flags=re.DOTALL):
        value = json.loads(body)
        assert isinstance(value, dict), f"{path}: JSON example must be an object"
        yield value


def _slug(heading: str) -> str:
    heading = re.sub(r"<[^>]+>", "", heading).lower()
    heading = re.sub(r"[^\w\s-]", "", heading)
    return re.sub(r"[-\s]+", "-", heading).strip("-")


def _anchors(path: Path) -> List[str]:
    return [_slug(match) for match in re.findall(r"^#{1,6}\s+(.+?)\s*$", path.read_text(), re.MULTILINE)]


def test_every_adopter_document_json_example_uses_the_shipped_contract_loader():
    for path in DOCS:
        examples = list(_json_examples(path))
        assert examples, f"{path}: expected a current-schema JSON example"
        for example in examples:
            assert example.get("contract") == serve.CONTRACT_VERSION
            cg._validate_ctl_response("version", example)
        assert summary.SCHEMA == 1


def test_every_local_document_link_and_anchor_resolves():
    link_re = re.compile(r"\[[^]]+\]\(([^)]+)\)")
    for source in DOCS:
        for raw in link_re.findall(source.read_text(encoding="utf-8")):
            if raw.startswith(("http://", "https://", "mailto:")):
                continue
            target_text, _, anchor = raw.partition("#")
            target = (source.parent / target_text).resolve() if target_text else source
            assert target.is_file(), f"{source}: missing linked file {raw}"
            if anchor:
                assert anchor in _anchors(target), f"{source}: missing anchor {raw}"


def test_every_closed_consumer_vocabulary_is_documented():
    text = "\n".join(path.read_text(encoding="utf-8") for path in DOCS)
    for value in ("container", "container-shared", "on", "off", "unavailable", "command", "assay"):
        assert re.search(rf"(?<![A-Za-z0-9_-]){re.escape(value)}(?![A-Za-z0-9_-])", text)
