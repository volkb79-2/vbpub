"""Keep the user-facing boot-pool patch documents linked and adoptable."""

from __future__ import annotations

from html import unescape
from pathlib import Path
import re


PATCH_ROOT = Path(__file__).resolve().parents[1]
TRUENAS_ROOT = PATCH_ROOT.parent
USER_DOCS = {
    PATCH_ROOT / "README.md",
    PATCH_ROOT / "docs" / "DESIGN-GUIDE.md",
    PATCH_ROOT / "docs" / "CONSUMERS.md",
    PATCH_ROOT.parent / "docs" / "OVERVIEW.md",
}
UPDATE_AND_DRIFT = PATCH_ROOT.parent / "docs" / "UPDATE-AND-DRIFT.md"
LINK_DOCS = USER_DOCS | {UPDATE_AND_DRIFT}
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
    sources = {path: markdown_source(path) for path in LINK_DOCS}
    anchors = {path: heading_anchors(source) for path, source in sources.items()}
    for source_path, source in sources.items():
        for destination in LINK.findall(source):
            target_text, separator, fragment = destination.partition("#")
            if not separator or not fragment:
                continue
            target_path = (source_path.parent / target_text).resolve() if target_text else source_path
            matching_doc = next((path for path in LINK_DOCS if path.resolve() == target_path), None)
            if matching_doc is None or matching_doc.resolve() == source_path.resolve():
                continue
            assert fragment in anchors[matching_doc], (
                f"{source_path.relative_to(TRUENAS_ROOT)} links to missing anchor "
                f"{fragment!r} in {matching_doc.relative_to(TRUENAS_ROOT)}"
            )


def test_feature_docs_link_to_update_mechanics_and_survival_evidence() -> None:
    for path in USER_DOCS:
        source = markdown_source(path)
        assert "UPDATE-AND-DRIFT.md#update-mechanics-q1" in source, (
            f"{path.relative_to(TRUENAS_ROOT)} does not link to the update-mechanics record"
        )
        assert "UPDATE-AND-DRIFT.md#update-survival-acceptance-q4" in source, (
            f"{path.relative_to(TRUENAS_ROOT)} does not link to the update-survival record"
        )


def test_public_path_and_setup_values_are_documented() -> None:
    combined = "\n".join(markdown_source(path) for path in USER_DOCS)
    for value in (
        "path-a", "path-b", "ui-wizard", "preprovisioned", "install", "replace",
        "vanilla", "path-a+b", "none", "reapply", "install-after-update",
        "reapply-after-update", "plant", "update", "verify", "ui", "api",
    ):
        assert value in combined, f"consumer-facing closed value {value!r} is undocumented"


def test_registered_builds_and_update_policy_are_synced_across_user_docs() -> None:
    registry = PATCH_ROOT / "supported-builds.json"
    import json

    builds = json.loads(registry.read_text(encoding="utf-8"))["supported_builds"]
    for path in USER_DOCS:
        source = markdown_source(path)
        for version in builds:
            assert version in source, f"registered build {version} is missing from {path.name}"
        assert "one rolling" in re.sub(r"\s+", " ", source).lower(), (
            f"rolling-nightly policy is missing from {path.relative_to(TRUENAS_ROOT)}"
        )


def test_acceptance_entrypoint_values_and_result_index_are_documented() -> None:
    source = re.sub(r"\s+", " ", markdown_source(PATCH_ROOT / "docs" / "CONSUMERS.md"))
    assert "acceptance/accept.py" in source
    assert "ACCEPTANCE-RESULTS.md" in source
    assert "--start-state" in source and "--patched-target" in source and "--steps" in source
    assert "Path A root and eligible descendants mounted at boot" in markdown_source(
        PATCH_ROOT / "docs" / "DESIGN-GUIDE.md"
    )


def test_update_state_and_refusal_contract_are_documented() -> None:
    overview = markdown_source(PATCH_ROOT.parent / "docs" / "OVERVIEW.md")
    design = markdown_source(PATCH_ROOT / "docs" / "DESIGN-GUIDE.md")
    consumers = markdown_source(PATCH_ROOT / "docs" / "CONSUMERS.md")
    for source in (overview, design, consumers):
        assert "stock `/usr`" in source
        assert "boot-disk-data" in source
        assert "fresh" in source.lower()
        assert "rollback" in source.lower()
    assert "accept.py" in design and "install-after-update" in design
    assert "reapply-after-update" in consumers


def test_latest_v27_nightly_policy_and_previous_matrix_evidence_are_synced() -> None:
    nightly = "27.0.0-MASTER+20261029-173417"
    for path in USER_DOCS:
        source = re.sub(r"\s+", " ", markdown_source(path))
        assert nightly in source, f"latest v27 nightly compatibility fact is missing from {path.name}"
        assert "pre-RC.2" in source, f"v27 nightly scope is missing from {path.name}"
        assert "acceptance" in source.lower(), f"acceptance status is missing from {path.name}"
