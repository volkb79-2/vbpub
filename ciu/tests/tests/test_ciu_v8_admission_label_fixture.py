"""Keep the shared run-gate admission label fixture aligned with CIU v8."""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path


_MONOREPO_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE = _MONOREPO_ROOT / "tests/fixtures/admission-label-grammar.json"
_SPEC = Path(__file__).resolve().parents[2] / "docs/SPEC-V8.md"
_ADMISSION_MODULE = _MONOREPO_ROOT / "run-gate-project/run_gate_admission.py"


def test_shared_admission_label_fixture_matches_v8_spec_and_gate_module() -> None:
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

    assert _ADMISSION_MODULE.is_file()
    module_spec = importlib.util.spec_from_file_location(
        "ciu_contract_run_gate_admission", _ADMISSION_MODULE
    )
    assert module_spec is not None and module_spec.loader is not None
    module = importlib.util.module_from_spec(module_spec)
    sys.modules[module_spec.name] = module
    module_spec.loader.exec_module(module)
    assert fixture["labels"] == module.LABEL_VALUE_GRAMMAR
