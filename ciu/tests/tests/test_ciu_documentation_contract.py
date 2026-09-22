"""Documentation-contract test (P04, O3): the three user-facing documents
(README.md, docs/DESIGN-GUIDE.md, docs/CONSUMERS.md) stay in sync with the
shipped loader and the closed public vocabulary.

- Every TOML example in all three parses with the SHIPPED loader
  (config_model.parse_toml_string — tomllib), so a stale/undocumented config
  example cannot slip through.
- Every closed public value a consumer must type appears in at least one of
  the three, so a capability cannot ship undocumented.
- Every cross-document markdown anchor resolves, so a reference cannot dangle.
"""
from __future__ import annotations

import re
import runpy
import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from ciu import config_model, worktree  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

DOCS = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "docs" / "DESIGN-GUIDE.md",
    REPO_ROOT / "docs" / "CONSUMERS.md",
]

TOML_FENCE_RE = re.compile(r"```toml\n(.*?)```", re.DOTALL)
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")

# Every closed public value a consumer must type (D-009 / S16.4 / S16.5 /
# S16.7).
CLOSED_PUBLIC_VALUES = {
    # capability identifiers
    "worktree.identity.v1",
    "worktree.branches.v1",
    "worktree.inspect.v1",
    "worktree.lifecycle-json.v1",
    "worktree.up.v1",
    "worktree.exec-local.v1",
    "worktree.exec-target.v1",
    # structured-document operations
    "inspect",
    "list",
    "remove",
    "create",
    "ensure",
    "adopt",
    # exec-target config vocabulary (S16.7)
    "exec_targets",
    "requires_worktree_mount",
    "stack",
    "service",
    "workdir",
    # provenance closed vocabularies (S17.3/S17.5, CIU-39)
    "vendor-pinned",
    "vendor_images",
    "match",
    "mismatch",
    "unlabelled",
    # branch-hygiene categories + document statuses (S16.8, CIU-25)
    "prunable",
    "merged-dirty",
    "mainline",
    "unmerged",
    "current",
    "branches-prune",
    "survey",
    "partial",
    # cross-profile producer declaration (S13.6, CIU-42)
    "produced_by",
    # lifecycle states
    "allocating",
    "ready",
    "recovery-required",
    # terminal removal status
    "removed",
    # recovery statuses
    "checkout-incomplete",
    "env-generation-failed",
    "runtime-collision",
}


def _slugify(heading: str) -> str:
    """GitHub-style anchor slug for one markdown heading line."""
    text = re.sub(r"^#+\s*", "", heading.strip()).lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return text.replace(" ", "-")


def _headings(path: Path) -> set[str]:
    return {
        _slugify(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.lstrip().startswith("#")
    }


def _toml_blocks(path: Path) -> list[str]:
    return TOML_FENCE_RE.findall(path.read_text(encoding="utf-8"))


def test_every_toml_example_parses_with_the_shipped_loader():
    for doc in DOCS:
        for block in _toml_blocks(doc):
            config_model.parse_toml_string(block, str(doc))


WAVE_KEY_DOCS = [
    d
    for d in sorted((Path(__file__).resolve().parents[2] / "docs").glob("*.md"))
    if any(
        "produced_by" in b or "vendor_images" in b
        for b in TOML_FENCE_RE.findall(d.read_text(encoding="utf-8"))
    )
]


@pytest.mark.parametrize("doc", WAVE_KEY_DOCS)
def test_wave_config_examples_parse_wherever_they_appear(doc):
    if not _toml_blocks(doc):
        pytest.skip("no wave-key toml fences in this document")
    """A-270 corollary added by the CIU-42/39 review: a config example that a
    consumer must TYPE parses with the shipped loader WHEREVER it appears —
    including SPEC/CONFIG, whose other toml fences may legitimately be
    template placeholders."""
    for block in _toml_blocks(doc):
        if "produced_by" in block or "vendor_images" in block:
            config_model.parse_toml_string(block, str(doc))


def test_every_closed_public_value_appears_in_the_documents():
    corpus = "\n".join(doc.read_text(encoding="utf-8") for doc in DOCS)
    missing = sorted(v for v in CLOSED_PUBLIC_VALUES if v not in corpus)
    assert missing == []


def test_every_cross_document_anchor_resolves():
    heading_cache: dict[Path, set[str]] = {}

    def headings_of(path: Path) -> set[str]:
        if path not in heading_cache:
            heading_cache[path] = _headings(path) if path.suffix == ".md" else set()
        return heading_cache[path]

    failures: list[str] = []
    for doc in DOCS:
        for target in LINK_RE.findall(doc.read_text(encoding="utf-8")):
            if target.startswith("http") or target.startswith("#"):
                continue  # external URL or same-document anchor
            path_part, _, anchor = target.partition("#")
            if not path_part:
                continue  # same-document anchor (not cross-document)
            resolved = (doc.parent / path_part).resolve()
            if not resolved.exists():
                failures.append(
                    f"{doc.relative_to(REPO_ROOT)}: {target!r} -> missing target file"
                )
                continue
            if anchor and anchor not in headings_of(resolved):
                failures.append(
                    f"{doc.relative_to(REPO_ROOT)}: {target!r} -> no anchor #{anchor} "
                    f"in {path_part}"
                )
    assert failures == []


def test_workspace_identity_spec_keeps_export_and_authority_distinct():
    spec = (REPO_ROOT / "docs" / "SPEC.md").read_text(encoding="utf-8")
    assert "`ciu.env` is a legacy export, and never an internal source of facts." in spec
    assert "Root selection and generated identity facts are" in spec
    assert "Derived identity has one source." in spec
    assert "a pre-set environment value always wins" not in spec


def test_assay_lane_declares_the_complete_rigor_ladder():
    lane = tomllib.loads((REPO_ROOT / "assay.toml").read_text(encoding="utf-8"))["lanes"]["ciu"]
    assert lane["rigor"] == ["R0", "R1", "R2", "R3"]
    assert lane["isolation"]["snapshot_selection"] == "repository-minus-unsafe-symlinks"
    assert lane["judge"]["coverage"]["artifact"] == "coverage.json"
    assert lane["judge"]["mutation"]["jobs"] == 1
    assert lane["judge"]["mutation"]["liveness"] is True
    helper = runpy.run_path(str(REPO_ROOT / "run-ciu-tests.py"))
    assert lane["argv"][1:] == helper["pytest_args"]("0")
    assert "--maxfail=1" in lane["argv"]
    assert lane["judge"]["canary"]["mechanism"] == "import-break"
    spec = (REPO_ROOT / "docs" / "SPEC.md").read_text(encoding="utf-8")
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "--maxfail=1" in spec and "liveness monitoring" in spec
    assert "active liveness" in readme
