"""Keep the shared run-gate admission label fixture aligned with CIU v8."""

from __future__ import annotations

import json
import re
from pathlib import Path


_MONOREPO_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE = _MONOREPO_ROOT / "tests/fixtures/admission-label-grammar.json"
_SPEC = Path(__file__).resolve().parents[2] / "docs/SPEC-V8.md"


def test_shared_admission_label_fixture_matches_v8_spec() -> None:
    fixture = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    assert fixture["schema_version"] == 1
    assert fixture["surface"] == "label_value_grammar"
    assert fixture["source"] == "ciu/docs/SPEC-V8.md Appendix E"

    spec = _SPEC.read_text(encoding="utf-8")
    blocks = re.findall(
        r"```surface:label_value_grammar\s*\n(.*?)\n```", spec, re.DOTALL
    )
    assert len(blocks) == 1
    specified = {
        key: value
        for line in blocks[0].splitlines()
        if line.strip()
        for key, value in [line.strip().split("=", 1)]
    }
    assert fixture["labels"] == specified
