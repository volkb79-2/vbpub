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
