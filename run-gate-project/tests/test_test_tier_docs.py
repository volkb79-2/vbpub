"""The canonical T0-T3 labels stay synchronized with the normative docs.

The methodology owns the semantic labels; CONSUMERS.md owns the adoption
contract and LANE-AUTHORING.md owns the lane mapping.  Derive the labels from
the canonical source instead of restating a second hard-coded vocabulary in
the test.
"""

from __future__ import annotations

import re
from pathlib import Path

import _testdeps

REPO_ROOT = Path(__file__).resolve().parents[2]
METHODOLOGY = REPO_ROOT / "nyxloom" / "reference" / "TESTING-METHODOLOGY.md"
CONSUMERS = REPO_ROOT / "run-gate-project" / "CONSUMERS.md"
LANE_AUTHORING = REPO_ROOT / "run-gate-project" / "LANE-AUTHORING.md"
README = REPO_ROOT / "run-gate-project" / "README.md"
DESIGN = REPO_ROOT / "run-gate-project" / "docs" / "DESIGN-lane-rework-2026-11.md"


def methodology() -> str:
    if not (REPO_ROOT / "run-gate.root.toml").is_file():
        _testdeps.missing(
            "the canonical testing methodology",
            "this is the standalone Run-Gate layout, not the vbpub monorepo",
        )
    assert METHODOLOGY.is_file(), f"missing canonical methodology: {METHODOLOGY}"
    return METHODOLOGY.read_text(encoding="utf-8")


def test_recommended_presets_are_documented_in_lane_authoring() -> None:
    text = methodology()
    section = text.split("### Recommended presets", 1)
    assert len(section) == 2, "methodology lost its recommended-presets section"
    presets = set(re.findall(
        r"^\| `([^`]+)` \| T[0-3][^|]* \|",
        section[1].split("\n### ", 1)[0],
        flags=re.MULTILINE,
    ))
    assert presets == {"focused", "r0-r1", "r3", "gate", "r2", "fuzz"}

    documented = LANE_AUTHORING.read_text(encoding="utf-8")
    section = documented.split("### Tier contract and preset names", 1)
    assert len(section) == 2, "LANE-AUTHORING.md lost its normative tier section"
    documented = section[1].split("\n### ", 1)[0]
    missing = sorted(
        preset for preset in presets
        if not re.search(
            rf"^\| `{re.escape(preset)}` \| T[0-3][^|]* \|",
            documented,
            flags=re.MULTILINE,
        )
    )
    assert not missing, f"LANE-AUTHORING.md lacks preset rows: {missing}"


def test_tier_labels_are_documented_in_consumers() -> None:
    text = methodology()
    section = text.split("### The four tiers", 1)
    assert len(section) == 2, "methodology lost its four-tiers table"
    labels = re.findall(
        r"^\| \*\*(T[0-3] [^|*]+)\*\* \|",
        section[1].split("\n### ", 1)[0],
        flags=re.MULTILINE,
    )
    assert labels == [
        "T0 focused",
        "T1 pre-merge",
        "T2 release",
        "T3 async",
    ]

    documented = CONSUMERS.read_text(encoding="utf-8")
    missing = [label for label in labels if f"**{label}**" not in documented]
    assert not missing, f"CONSUMERS.md lacks tier rows: {missing}"


def test_readme_points_to_the_tier_contract_and_design_rationale() -> None:
    readme = README.read_text(encoding="utf-8")
    links = {
        "CONSUMERS.md#normative-test-tiers-t0-t3":
            CONSUMERS,
        "docs/DESIGN-lane-rework-2026-11.md#2-features-needed":
            DESIGN,
        "docs/DESIGN-lane-rework-2026-11.md#3-t3-trigger-and-distribution":
            DESIGN,
    }
    missing = [link for link in links if f"]({link})" not in readme]
    assert not missing, f"README.md lacks tier documentation links: {missing}"

    assert "## Normative test tiers (T0-T3)" in CONSUMERS.read_text(
        encoding="utf-8"
    )
    headings = {
        "## 2. Features needed",
        "## 3. T3 trigger and distribution",
    }
    assert headings <= set(DESIGN.read_text(encoding="utf-8").splitlines())


def test_r2_worker_ceiling_identity_and_t3_choice_are_documented() -> None:
    design_guide = REPO_ROOT / "run-gate-project" / "docs" / "DESIGN-GUIDE.md"
    documents = {
        "README.md": README.read_text(encoding="utf-8"),
        "CONSUMERS.md": CONSUMERS.read_text(encoding="utf-8"),
        "LANE-AUTHORING.md": LANE_AUTHORING.read_text(encoding="utf-8"),
        "DESIGN-GUIDE.md": design_guide.read_text(encoding="utf-8"),
        "T3 design": DESIGN.read_text(encoding="utf-8"),
    }
    for name in ("ciu/assay.toml", "ciu/CHANGES.md"):
        path = REPO_ROOT / name
        if path.is_file():
            documents[name] = path.read_text(encoding="utf-8")
    for name, text in documents.items():
        normalized = " ".join(text.lower().replace("#", " ").split())
        assert "jobs_max" in normalized, f"{name} omits the R2 worker ceiling"
        assert "judge identity" in normalized, f"{name} omits the identity boundary"
        assert "invalidates" in normalized, f"{name} omits the resume consequence"
        assert "finished-candidate state" in normalized, f"{name} omits the affected state"
        assert "assay <= 12.1.1" in normalized, (
            f"{name} omits the Assay 12.1.1 identity boundary"
        )
        assert "assay >= 12.2.0" in normalized, (
            f"{name} omits the Assay 12.2.0 identity boundary"
        )
        assert all(token in normalized for token in ("tests", "sources", "argv", "env")), (
            f"{name} omits judged-input invalidation behavior"
        )
        if name != "CONSUMERS.md":
            assert "native-r2-worker-selection" in normalized, (
                f"{name} must point to the versioned rule in CONSUMERS.md"
            )
        assert "this ceiling is part of the judged-tree identity" not in normalized, (
            f"{name} repeats the obsolete unconditional identity rule"
        )
        assert "change it only in a new campaign tree, never during a resume" not in normalized, (
            f"{name} repeats the obsolete resume instruction"
        )
