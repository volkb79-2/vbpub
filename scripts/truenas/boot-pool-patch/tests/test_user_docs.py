"""Keep the user-facing boot-pool patch documents linked and adoptable."""

from __future__ import annotations

from html import unescape
from pathlib import Path
import re


PATCH_ROOT = Path(__file__).resolve().parents[1]
USER_DOCS = {
    PATCH_ROOT / "README.md",
    PATCH_ROOT / "docs" / "DESIGN-GUIDE.md",
    PATCH_ROOT / "docs" / "CONSUMERS.md",
}
LINK = re.compile(r"\[[^\]]+\]\(([^)\s]+)(?:\s+[^)]*)?\)")
FENCE = re.compile(r"(?ms)^```[^\n]*\n.*?^```\s*$")


def markdown_source(path: Path) -> str:
    return FENCE.sub("", path.read_text(encoding="utf-8"))


def heading_anchors(source: str) -> set[str]:
    anchors: set[str] = set()
    for line in source.splitlines():
        match = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not match:
            continue
        heading = unescape(match.group(1))
        heading = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", heading)
        heading = heading.replace("`", "").lower()
        base = re.sub(r"[^\w -]", "", heading, flags=re.UNICODE)
        base = re.sub(r"-+", "-", re.sub(r"\s+", "-", base)).strip("-")
        anchor = base
        suffix = 1
        while anchor in anchors:
            anchor = f"{base}-{suffix}"
            suffix += 1
        anchors.add(anchor)
    return anchors


def test_cross_document_anchors_resolve() -> None:
    sources = {path: markdown_source(path) for path in USER_DOCS}
    anchors = {path: heading_anchors(source) for path, source in sources.items()}
    for source_path, source in sources.items():
        for destination in LINK.findall(source):
            target_text, separator, fragment = destination.partition("#")
            if not separator or not fragment:
                continue
            target_path = (source_path.parent / target_text).resolve() if target_text else source_path
            matching_doc = next((path for path in USER_DOCS if path.resolve() == target_path), None)
            if matching_doc is None or matching_doc.resolve() == source_path.resolve():
                continue
            assert fragment in anchors[matching_doc], (
                f"{source_path.relative_to(PATCH_ROOT)} links to missing anchor "
                f"{fragment!r} in {matching_doc.relative_to(PATCH_ROOT)}"
            )


def test_public_path_and_setup_values_are_documented() -> None:
    combined = "\n".join(markdown_source(path) for path in USER_DOCS)
    for value in ("path-a", "path-b", "ui-wizard", "preprovisioned", "install", "replace"):
        assert value in combined, f"consumer-facing closed value {value!r} is undocumented"


def test_current_v28_compatibility_build_is_synced_across_user_docs() -> None:
    version = "28.0.0-MASTER+20261027-175229"
    for path in USER_DOCS:
        assert version in markdown_source(path), (
            f"current v28 compatibility fact is stale in {path.relative_to(PATCH_ROOT)}"
        )


def test_v27_nightly_update_is_not_reported_as_matrix_acceptance() -> None:
    nightly = "27.0.0-MASTER+20261027-173422"
    for path in USER_DOCS:
        source = re.sub(r"\s+", " ", markdown_source(path))
        assert nightly in source, f"v27 update evidence is missing from {path.name}"
        assert "is update/reboot evidence only" in source
        assert "no separate full matrix was run on the nightly" in source

    combined = "\n".join(markdown_source(path) for path in USER_DOCS)
    assert "The v27 nightly Path A/B API/UI matrix passed" not in combined
